"""The tool loop: a chat turn from "Send" to "completed", and everything between.

Read it as four concentric things.

**The loop.** System prompt plus Set scope, then the thread's history, then a
provider call with the tool schemas attached. If the answer contains tool calls,
run them through the dispatcher, append the results as a ``tool`` message, and
ask again. Bounded twice — by rounds and by total calls — because a model that
keeps re-querying is the failure mode that spends an afternoon's tokens on one
question, and a bound the model is *told about* is one it can plan around.

**The stream.** Every event is written to ``chat_events`` and then published on
the app's SSE bus, in that order. A browser that reconnects replays from the
table and picks the live stream up where the replay stopped; if the two were the
other way round, an event published and not yet stored would be missed by both.

**Cancellation.** An ``asyncio.Event``, not task cancellation. The provider
checks it between chunks and the CLI provider kills its child on it, and the run
still reaches its ``finally`` — so a cancelled run writes a ``cancelled`` row and
a ``cancelled`` event, rather than leaving a spinner and a half-written bubble.

**The claude_code special case.** That provider runs the tool loop inside Claude
Code against our own MCP server, so its turn comes back with the
calls *already executed*. The runner records them for the transcript and does
not loop: there is nothing left to dispatch.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from gaggiclanker.chat.context import opening_context, thread_title_from
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.chat import (
    ChatEventsRepository,
    ChatMessageRow,
    ChatMessageWrite,
    ChatRepository,
    ChatRunRow,
)
from gaggiclanker.db.repos.llm import LlmCallRow
from gaggiclanker.infra.errors import BadRequest, NotFound
from gaggiclanker.infra.sse import SseEvent, SseEventBus
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.llm.chat_types import (
    ChatEvent,
    ChatMessage,
    ChatRequest,
    ChatToolCall,
    ChatToolResult,
    ChatTurn,
)
from gaggiclanker.llm.errors import LlmApiError, classify_llm_error
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import Usage
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext, ToolRegistry
from gaggiclanker.tools.scope import ToolScope

if TYPE_CHECKING:
    from gaggiclanker.drafts.proposals import DraftProposals
    from gaggiclanker.starting.service import StartingPointService

__all__ = [
    "CHAT_EVENT",
    "GENERAL_CHAT_PROMPT",
    "SET_CHAT_PROMPT",
    "ChatRunner",
    "prompt_for",
    "run_task_name",
]

log = structlog.get_logger(__name__)

#: The SSE event name every chat event travels under on the app bus. One name,
#: with the run id inside the payload, because the bus is a single fan-out and a
#: per-run event name would mean a subscriber that cannot filter until it has
#: parsed the name.
CHAT_EVENT = "chat.event"

#: The two prompts, one per kind of conversation. Two files rather than one
#: with a conditional paragraph: they now say different things about what the
#: agent is for — grading an experiment, against answering about the archive —
#: and a single prompt would have to be read with half of it crossed out.
SET_CHAT_PROMPT = "chat-set"
GENERAL_CHAT_PROMPT = "chat-general"


def prompt_for(scope: ToolScope) -> str:
    """Which prompt this conversation is answered with. From the same scope."""
    return SET_CHAT_PROMPT if scope.kind == "set" else GENERAL_CHAT_PROMPT


#: Roughly four characters to a token. Deliberately crude: the budget exists to
#: stop a year-long thread from being re-sent in full, and a tokeniser per
#: provider would be three dependencies for a bound whose exact value nobody
#: can justify anyway.
CHARS_PER_TOKEN = 4

#: Cap on one tool result as the model is shown it. A `query_shots` returning
#: 500 rows is legitimate and enormous; truncating with a marker lets the model
#: notice and ask for an aggregate instead.
TOOL_RESULT_CHARS = 24_000


def run_task_name(run_id: int) -> str:
    """The registry name for one run's background task."""
    return f"chat:{run_id}"


@dataclass(slots=True)
class _Budget:
    """What one answer may spend, read from the settings registry per run."""

    max_rounds: int = 8
    max_calls: int = 20
    history_tokens: int = 12_000


@dataclass(slots=True)
class _RunState:
    """The mutable half of one run, kept off the service so runs cannot collide."""

    run_id: int
    thread_id: int
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    seq: int = 0
    tool_rounds: int = 0
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)


class ChatRunner:
    """Owns the chat: threads, runs, the loop, and the stream.

    App-scoped, because it holds the cancel events. A per-request instance would
    make every cancel button press a no-op against an empty map, which is the
    same class of bug as a per-request rate limiter.
    """

    def __init__(
        self,
        db: Database,
        llm: LlmService,
        prompts: PromptService,
        *,
        tools: ToolRegistry,
        bus: SseEventBus | None = None,
        analyzer: Any = None,
        drafts: DraftProposals | None = None,
        starting: StartingPointService | None = None,
        knowledge: Any = None,
        tasks: TaskRegistry | None = None,
        rate_limits: Any = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.prompts = prompts
        self.tools = tools
        self.bus = bus
        self.analyzer = analyzer
        #: The proposal half of drafts, and a starting-point service built over
        #: it. Neither holds the machine connection: this runner hands them to
        #: tools a model drives, and pushing stays with the routes.
        self.drafts = drafts
        self.starting = starting
        self.knowledge = knowledge
        self.tasks = tasks
        self.rate_limits = rate_limits
        self.repo = ChatRepository(db)
        self.events = ChatEventsRepository(db)
        self._running: dict[int, _RunState] = {}

    # -- starting and stopping --------------------------------------------

    async def send(
        self,
        thread_id: int,
        message: str,
        *,
        tasks: TaskRegistry | None = None,
    ) -> tuple[ChatRunRow, ChatMessageRow]:
        """Store the question, open a run, and hand the work to a background task.

        202-shaped on purpose, exactly like an analysis: the answer takes tens of
        seconds and a request holding it open is a request `docker stop` kills
        mid-flight. The row is the handle and the stream is how it moves.
        """
        text = message.strip()
        if not text:
            raise BadRequest("A message needs some text.")
        thread = await self.repo.get_thread(thread_id)
        if thread is None:
            raise NotFound(f"No chat thread {thread_id}")
        registry = tasks or self.tasks
        if registry is None:  # pragma: no cover - the app always wires one
            raise RuntimeError("the chat runner has no task registry")

        run = await self.repo.start_run(thread_id)
        stored = await self.repo.add_message(
            ChatMessageWrite(thread_id=thread_id, role="user", content=text, run_id=run.id)
        )
        if not thread.title:
            await self.repo.rename_thread(thread_id, thread_title_from(text))

        state = _RunState(run_id=run.id, thread_id=thread_id)
        self._running[run.id] = state
        # The thread's two columns and its Set's design flag decide what this
        # turn is: one experiment, a Set being designed, or the archive. Read
        # once per turn, here, and carried through the run — every later
        # decision about tools is this one value — so the turn after an
        # initial recipe is accepted is already an ordinary Set conversation.
        scope = await ToolScope.resolve(self.db, thread.set_id, thread.set_version_id)
        registry.spawn(run_task_name(run.id), self._background(state, scope))
        return run, stored

    async def cancel(self, run_id: int) -> ChatRunRow:
        """Ask a run to stop. Idempotent, and safe on a run that already ended."""
        state = self._running.get(run_id)
        row = await self.repo.get_run(run_id)
        if row is None:
            raise NotFound(f"No chat run {run_id}")
        if state is not None:
            state.cancel.set()
        elif row.status == "running":
            # Running in the table but not in this process: the row survived a
            # restart that boot reconciliation has not reached, or another
            # process owned it. Closing it is better than a button that lies.
            finished = await self.repo.finish_run(run_id, status="cancelled", error="Cancelled.")
            return finished or row
        return row

    async def reconcile(self) -> int:
        """Close runs left ``running`` by a restart. Called from the lifespan."""
        return await self.repo.reconcile_running()

    # -- the loop ----------------------------------------------------------

    async def _background(self, state: _RunState, scope: ToolScope) -> None:
        started = time.monotonic()
        config = await self.llm.config()
        model = config.resolve_model("chat")
        handle = self.llm.observer.register(
            label="chat turn",
            subject=f"thread {state.thread_id}",
            provider=config.provider,
            model=model or "(provider default)",
            purpose="chat",
        )
        status = "ok"
        error: str | None = None
        try:
            await self._loop(state, scope, model=model)
        except asyncio.CancelledError:
            status = "cancelled"
            error = "The run was cancelled."
            await self._emit(state, "cancelled", {})
            raise
        except LlmApiError as exc:
            code = classify_llm_error(status=exc.status, message=str(exc))
            status = "failed"
            error = f"{code}: {exc}"
            await self._emit(state, "error", {"code": code, "message": str(exc)[:500]})
        except Exception as exc:
            log.warning("chat_run_failed", run_id=state.run_id, exc_info=True)
            status = "failed"
            error = str(exc)[:500]
            await self._emit(state, "error", {"code": "unknown", "message": error})
        else:
            if state.cancel.is_set():
                status = "cancelled"
                error = "The run was cancelled."
                await self._emit(state, "cancelled", {})
            else:
                await self._emit(state, "completed", {"usage": _usage_json(state.usage)})
        finally:
            self._running.pop(state.run_id, None)
            if status == "ok":
                handle.succeed(state.usage)
            else:
                handle.fail(error or status, usage=state.usage)
            await self.repo.finish_run(
                state.run_id,
                status=status,  # type: ignore[arg-type]
                error=error,
                usage=_usage_json(state.usage),
                tool_rounds=state.tool_rounds,
                tool_calls=state.tool_calls,
                provider=config.provider,
                model=model,
            )
            await self._record_call(
                state,
                config.provider,
                model,
                prompt=prompt_for(scope),
                status=status,
                error=error,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    async def _loop(self, state: _RunState, scope: ToolScope, *, model: str) -> None:
        budget = await self._budget()
        # The Set prompt is handed the experiment; the general one has no
        # variables at all, and passing it one it does not use would be
        # harmless but misleading to read.
        prompt = prompt_for(scope)
        variables = (
            {"scope": await opening_context(self.db, scope)} if prompt == SET_CHAT_PROMPT else {}
        )
        rendered = await self.prompts.load(prompt, variables)
        history = await self._history(state.thread_id, budget.history_tokens)
        provider = await self.llm.provider_for(await self.llm.config())
        schemas = self._schemas(provider, scope)

        for round_number in range(budget.max_rounds):
            turn = await self._turn(
                state,
                provider,
                ChatRequest(
                    messages=history,
                    tools=schemas,
                    system=rendered.system,
                    model=model,
                    timeout_s=(await self.llm.config()).timeout_s,
                    set_id=scope.set_id,
                    set_version_id=scope.set_version_id,
                    thread_id=state.thread_id,
                    cancel=state.cancel,
                ),
            )
            state.usage = state.usage + turn.usage

            # The CLI provider already ran the loop. Persist what it did and stop.
            if turn.executed_tool_calls or turn.executed_tool_results:
                await self._persist_executed(state, turn)
                await self._say(state, turn)
                return

            if not turn.tool_calls or state.cancel.is_set():
                await self._say(state, turn)
                return

            calls = turn.tool_calls[: max(0, budget.max_calls - state.tool_calls)]
            if not calls:
                # The call budget is spent and this turn asked for more. Storing
                # the round anyway would write an assistant message with empty
                # content and a `tool` message with no results, and the
                # Anthropic provider rejects both on every *later* turn of the
                # thread — one exhausted budget would poison the conversation
                # for good. Leave the loop with the history untouched and let
                # the final no-tools turn produce the answer.
                break
            await self.repo.add_message(
                ChatMessageWrite(
                    thread_id=state.thread_id,
                    role="assistant",
                    content=turn.text,
                    run_id=state.run_id,
                    tool_calls=[_call_json(call) for call in calls],
                    usage=_usage_json(turn.usage),
                )
            )
            history.append(ChatMessage(role="assistant", content=turn.text, tool_calls=list(calls)))
            results = await self._dispatch(state, calls, scope)
            history.append(ChatMessage(role="tool", tool_results=results))
            await self.repo.add_message(
                ChatMessageWrite(
                    thread_id=state.thread_id,
                    role="tool",
                    run_id=state.run_id,
                    tool_results=[_result_json(result) for result in results],
                )
            )
            state.tool_rounds = round_number + 1
            if state.tool_calls >= budget.max_calls:
                # Said once, on the round that spent the budget: repeating it
                # every round would push the question itself out of the history
                # budget with a sentence the model has already read.
                break

        # Out of budget — rounds or calls. One last turn without tools, so the
        # person gets an answer rather than a silent run that ends on a bound.
        final = await self._turn(
            state,
            provider,
            ChatRequest(
                messages=[
                    *history,
                    ChatMessage(
                        role="user",
                        content=(
                            "That is the tool budget for this question. Answer now with what "
                            "you have, and say plainly what you could not check."
                        ),
                    ),
                ],
                tools=[],
                system=rendered.system,
                model=model,
                timeout_s=(await self.llm.config()).timeout_s,
                set_id=scope.set_id,
                set_version_id=scope.set_version_id,
                thread_id=state.thread_id,
                cancel=state.cancel,
            ),
        )
        state.usage = state.usage + final.usage
        await self._say(state, final)

    async def _turn(self, state: _RunState, provider: Any, request: ChatRequest) -> ChatTurn:
        """One provider call, with its events forwarded as they arrive.

        The forwarding is fire-and-forget on purpose: a provider that had to
        await the database on every token would stream at the speed of SQLite.
        Each event is persisted by a task of its own and the tasks are awaited
        before the turn returns, so ordering within a run is preserved by the
        sequence number rather than by scheduling.
        """
        pending: list[asyncio.Task[None]] = []

        def forward(event: ChatEvent) -> None:
            pending.append(asyncio.create_task(self._emit(state, event.kind, event.data)))

        try:
            turn: ChatTurn = await provider.chat(request, forward)
            return turn
        finally:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    def tool_context(
        self, *, scope: ToolScope, run_id: int | None, thread_id: int | None = None
    ) -> ToolContext:
        """What one run's tools are handed. Nothing in it reaches the machine."""
        return ToolContext(
            db=self.db,
            settings=self.llm.settings,
            knowledge=self.knowledge,
            analyzer=self.analyzer,
            drafts=self.drafts,
            starting=self.starting,
            tasks=self.tasks,
            rate_limits=self.rate_limits,
            scope=scope,
            run_id=run_id,
            thread_id=thread_id,
            caller="chat",
            # The one permission set every model-driven caller gets; MCP is handed
            # the same one. No tool can write to the machine.
            permissions=CHAT_PERMISSIONS,
        )

    async def _dispatch(
        self, state: _RunState, calls: list[ChatToolCall], scope: ToolScope
    ) -> list[ChatToolResult]:
        ctx = self.tool_context(scope=scope, run_id=state.run_id, thread_id=state.thread_id)
        results: list[ChatToolResult] = []
        for call in calls:
            if state.cancel.is_set():
                # Checked between calls as well as between chunks: a round of
                # four tools is four provider-free seconds in which Stop has to
                # mean something. The calls already made keep their results, so
                # the transcript still says what was read.
                break
            await self._emit(
                state,
                "tool_call",
                {"id": call.id, "name": call.name, "arguments": call.arguments},
            )
            outcome = await self.tools.dispatch(ctx, call.name, call.arguments)
            state.tool_calls += 1
            content = outcome.as_content()
            if len(content) > TOOL_RESULT_CHARS:
                content = (
                    content[:TOOL_RESULT_CHARS]
                    + f"\n… truncated at {TOOL_RESULT_CHARS} characters. Ask for an aggregate "
                    "or a smaller limit."
                )
            result = ChatToolResult(id=call.id, name=call.name, content=content, ok=outcome.ok)
            results.append(result)
            await self._emit(
                state,
                "tool_result",
                {
                    "id": call.id,
                    "name": call.name,
                    "ok": outcome.ok,
                    "status": outcome.status,
                    "duration_ms": outcome.duration_ms,
                    "content": content[:4000],
                },
            )
        return results

    async def _persist_executed(self, state: _RunState, turn: ChatTurn) -> None:
        """Record a loop that ran inside the CLI, so the transcript still shows it."""
        if turn.executed_tool_calls:
            await self.repo.add_message(
                ChatMessageWrite(
                    thread_id=state.thread_id,
                    role="assistant",
                    run_id=state.run_id,
                    tool_calls=[_call_json(call) for call in turn.executed_tool_calls],
                )
            )
            state.tool_calls += len(turn.executed_tool_calls)
            state.tool_rounds = 1
        if turn.executed_tool_results:
            await self.repo.add_message(
                ChatMessageWrite(
                    thread_id=state.thread_id,
                    role="tool",
                    run_id=state.run_id,
                    tool_results=[_result_json(result) for result in turn.executed_tool_results],
                )
            )

    async def _say(self, state: _RunState, turn: ChatTurn) -> None:
        """Store the assistant's answer and announce it as one message."""
        if not turn.text.strip():
            return
        stored = await self.repo.add_message(
            ChatMessageWrite(
                thread_id=state.thread_id,
                role="assistant",
                content=turn.text,
                run_id=state.run_id,
                usage=_usage_json(turn.usage),
            )
        )
        await self._emit(
            state,
            "message",
            {"message_id": stored.id, "role": "assistant", "content": turn.text},
        )

    # -- the stream --------------------------------------------------------

    async def _emit(self, state: _RunState, kind: str, data: dict[str, Any]) -> None:
        """Persist, then publish. That order is what makes a replay complete."""
        state.seq += 1
        payload = {"run_id": state.run_id, "thread_id": state.thread_id, "seq": state.seq, **data}
        try:
            await self.events.append(state.run_id, state.seq, kind, payload)
        except Exception:
            log.warning("chat_event_not_stored", run_id=state.run_id, kind=kind, exc_info=True)
        if self.bus is not None:
            self.bus.publish(SseEvent(event=CHAT_EVENT, data={"kind": kind, **payload}))

    async def replay(self, run_id: int, after_seq: int = 0) -> list[SseEvent]:
        """Everything this run has already emitted, as stream events."""
        rows = await self.events.since(run_id, after_seq)
        return [
            SseEvent(event=CHAT_EVENT, data={"kind": row.kind, **row.data}, id=str(row.seq))
            for row in rows
        ]

    def is_running(self, run_id: int) -> bool:
        return run_id in self._running

    # -- assembly ----------------------------------------------------------

    def _schemas(self, provider: Any, scope: ToolScope) -> list[dict[str, Any]]:
        """Tool schemas in this provider's dialect, for this conversation only.

        The scope is applied here rather than left to the dispatcher because a
        tool a conversation does not have should not be *described* to it: a
        model told about `query_shots` inside a Set's chat will plan around it
        and then be refused, which reads as a broken tool rather than as a
        limit.

        ``claude_code`` gets none: its tools come from the MCP server named on
        its command line — narrowed by the same scope, through the environment
        the child is spawned with — and sending schemas as well would describe
        the same tools twice in two namespaces.
        """
        if getattr(provider, "id", "") == "claude_code":
            return []
        if getattr(provider, "id", "") == "anthropic":
            return self.tools.anthropic_schemas(CHAT_PERMISSIONS, scope)
        return self.tools.openai_schemas(CHAT_PERMISSIONS, scope)

    async def _budget(self) -> _Budget:
        settings = self.llm.settings
        return _Budget(
            max_rounds=int(await settings.get("chatMaxToolRounds")),
            max_calls=int(await settings.get("chatMaxToolCalls")),
            history_tokens=int(await settings.get("chatHistoryTokenBudget")),
        )

    async def _history(self, thread_id: int, token_budget: int) -> list[ChatMessage]:
        """The thread as provider messages, oldest dropped to fit the budget.

        Dropped from the front and in whole messages, and a ``tool`` message is
        never kept without the assistant turn that asked for it — an orphaned
        ``tool_result`` is a 400 on both APIs, not merely confusing.
        """
        rows = await self.repo.messages(thread_id)
        messages = [_to_chat_message(row) for row in rows]
        budget_chars = max(1, token_budget) * CHARS_PER_TOKEN

        kept: list[ChatMessage] = []
        used = 0
        for message in reversed(messages):
            size = _size(message)
            if kept and used + size > budget_chars:
                break
            kept.append(message)
            used += size
        kept.reverse()
        while kept and kept[0].role == "tool":
            kept.pop(0)
        return kept

    async def _record_call(
        self,
        state: _RunState,
        provider: str,
        model: str,
        *,
        prompt: str,
        status: str,
        error: str | None,
        duration_ms: int,
    ) -> None:
        """One ledger row per run, so a chat costs something visible in Usage."""
        if self.llm.calls_repo is None:
            return
        try:
            await self.llm.calls_repo.record(
                LlmCallRow(
                    call_id=f"chat-{state.run_id}",
                    purpose="chat",
                    label="chat turn",
                    subject=f"thread {state.thread_id}",
                    provider=provider,
                    model=model or None,
                    prompt_name=prompt,
                    input_tokens=state.usage.prompt_tokens,
                    output_tokens=state.usage.completion_tokens,
                    duration_ms=duration_ms,
                    # The ledger's CHECK allows two values; a cancelled run is a
                    # failed one as far as "did this produce an answer" goes.
                    status="succeeded" if status == "ok" else "failed",
                    error=error,
                )
            )
        except Exception:
            log.warning("chat_usage_not_recorded", run_id=state.run_id, exc_info=True)


def _to_chat_message(row: ChatMessageRow) -> ChatMessage:
    return ChatMessage(
        role=row.role,  # type: ignore[arg-type]
        content=row.content,
        tool_calls=[
            ChatToolCall(
                id=str(call.get("id", "")),
                name=str(call.get("name", "")),
                arguments=_arguments(call.get("arguments")),
            )
            for call in row.tool_calls
        ],
        tool_results=[
            ChatToolResult(
                id=str(result.get("id", "")),
                name=str(result.get("name", "")),
                content=str(result.get("content", "")),
                ok=bool(result.get("ok", True)),
            )
            for result in row.tool_results
        ],
    )


def _arguments(raw: Any) -> dict[str, Any]:
    """A stored tool call's arguments, defensively.

    Written by us, read back through JSON; a row from an older shape is worth
    tolerating rather than crashing a whole transcript over.
    """
    return raw if isinstance(raw, dict) else {}


def _size(message: ChatMessage) -> int:
    return (
        len(message.content)
        + sum(len(json.dumps(call.arguments, default=str)) for call in message.tool_calls)
        + sum(len(result.content) for result in message.tool_results)
    )


def _call_json(call: ChatToolCall) -> dict[str, Any]:
    return {"id": call.id, "name": call.name, "arguments": call.arguments}


def _result_json(result: ChatToolResult) -> dict[str, Any]:
    return {"id": result.id, "name": result.name, "content": result.content, "ok": result.ok}


def _usage_json(usage: Usage) -> dict[str, Any] | None:
    if usage.total_tokens is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }
