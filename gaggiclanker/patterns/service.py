"""Running Find patterns across Sets: a row, a call, proposals, and nothing else.

The shape of :meth:`PatternsService.run` is the contract, and it is short on purpose:

    open a `running` row  ->  build the input  ->  call_json  ->  filter  ->  close the row

**A run writes only its own rows**: the run and its proposals (and retires the previous
runs' proposals, see :meth:`PatternRunsRepository.finish_done`). It never writes an insight;
only a person's Approve does, through a route.

**Only a person starts one.** The one caller is the route behind the Knowledge page's button.
No chat tool, MCP tool, timer, sync hook, boot step or background task holds this service or
calls it, and the tests walk the registry and the package to keep it that way.

**It never raises for a provider failure.** A 429, a bad key, a timeout or an answer that does
not fit :class:`~gaggiclanker.patterns.models.PatternsResult` all come back from the LLM layer
as an ``Err`` and become a stored `failed` run carrying the error.

**The row is opened before the call.** A process that dies mid-call leaves a `running` row,
which the next boot marks `interrupted`.

**One run at a time.** The task name ``patterns`` is claimed synchronously inside ``spawn``, so
a second press while one runs gets the running row back instead of a second call; the partial
unique index on `pattern_runs` is the guard behind it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.patterns import (
    PatternRunOutcome,
    PatternRunRow,
    PatternRunsRepository,
    PatternRunStart,
)
from gaggiclanker.infra.sse import SseEvent, SseEventBus
from gaggiclanker.infra.tasks import TaskSpawner
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import LlmMessage, LlmRequest, Ok
from gaggiclanker.patterns.context import PatternsInput, build_patterns_input
from gaggiclanker.patterns.models import PatternsResult, filter_proposals

__all__ = [
    "MIN_SETS",
    "PATTERNS_EVENTS",
    "PATTERNS_PROMPT",
    "PATTERNS_TASK",
    "PATTERNS_USER_PROMPT",
    "NotEnoughSets",
    "PatternsService",
]

log = structlog.get_logger(__name__)

PATTERNS_PROMPT = "patterns"
PATTERNS_USER_PROMPT = "patterns-user"

#: The one registry name: one run at a time, however many tabs press the button.
PATTERNS_TASK = "patterns"

#: The SSE events a run publishes, on the LLM bus. The Knowledge page refreshes from them.
PATTERNS_EVENTS = ("patterns.started", "patterns.finished", "patterns.failed")

#: A pattern needs two Sets, so a run needs two Sets with a confirmed insight.
MIN_SETS = 2

#: Linear backoff between this call's own retries; the suite sets it to zero.
RETRY_DELAY_S = 0.5


class NotEnoughSets(Exception):
    """Fewer than two Sets have a confirmed insight, so there is nothing to find a pattern in."""

    def __init__(self, found: int) -> None:
        super().__init__(f"{found} Set(s) have confirmed insights; a pattern needs {MIN_SETS}")
        self.found = found


@dataclass(frozen=True, slots=True)
class _Prepared:
    row: PatternRunRow
    given: PatternsInput
    system: str
    user: str
    version: str
    model: str


class PatternsService:
    """The one entry point. Held on ``app.state.patterns``; the route is its only caller."""

    def __init__(
        self,
        db: Database,
        llm: LlmService,
        prompts: PromptService,
        *,
        bus: SseEventBus | None = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.prompts = prompts
        self.bus = bus
        self.retry_delay_s = RETRY_DELAY_S
        self.runs = PatternRunsRepository(db)
        # The run whose row is being opened right now, so a second request waits for the
        # first one's row instead of reading a database it has not written to yet.
        self._opening: asyncio.Future[PatternRunRow] | None = None

    async def start(
        self, *, tasks: TaskSpawner, model: str | None = None
    ) -> tuple[PatternRunRow, bool]:
        """Queue a run. Returns the row and whether this call started it.

        Raises :class:`NotEnoughSets` when fewer than two Sets have a confirmed insight: the
        caller's situation rather than the provider's, which the route answers with a 409
        and not a stored `failed` row.
        """
        # No await before the spawn below, so this check and the registry's own name guard
        # together leave no window: either somebody is mid-open and we wait for their row,
        # or the name is free and we claim it.
        pending = self._in_flight()
        if pending is not None:
            return await asyncio.shield(pending), False
        running = await self.runs.latest_running()
        if running is not None:
            return running, False
        found = await self.runs.sets_with_insights()
        if found < MIN_SETS:
            raise NotEnoughSets(found)
        pending = self._in_flight()  # another request may have got in while we read
        if pending is not None:
            return await asyncio.shield(pending), False

        opened: asyncio.Future[PatternRunRow] = asyncio.get_running_loop().create_future()
        self._opening = opened
        try:
            tasks.spawn(PATTERNS_TASK, self._background(model, opened))
        except RuntimeError:
            self._opening = None
            opened.cancel()
            running = await self.runs.latest_running()
            if running is not None:
                return running, False
            raise
        row = await opened
        return row, row.status == "running"

    def _in_flight(self) -> asyncio.Future[PatternRunRow] | None:
        return self._opening

    async def _background(self, model: str | None, opened: asyncio.Future[PatternRunRow]) -> None:
        """The registered task: open the row, hand it back, then do the work."""
        try:
            prepared = await self._prepare(model=model)
        except BaseException as exc:
            if not opened.done():
                opened.set_exception(exc)
            raise
        finally:
            self._opening = None
        opened.set_result(prepared.row)
        await self._complete(prepared)

    async def run(self, *, model: str | None = None) -> PatternRunRow:
        """Find patterns and wait for it. Returns the row, whatever happened.

        The direct form, for tests and for anything that genuinely wants to block. The route
        calls :meth:`start` instead.
        """
        return await self._complete(await self._prepare(model=model))

    async def _prepare(self, *, model: str | None = None) -> _Prepared:
        config = await self.llm.config()
        resolved = (model or "").strip() or config.resolve_model("patterns")
        given = await build_patterns_input(self.db)
        system, user, version = await self._render(given)
        run_id = await self.runs.start(
            PatternRunStart(
                provider=config.provider,
                model=resolved,
                prompt_name=PATTERNS_PROMPT,
                prompt_version=version,
                input=given.model_dump(mode="json"),
                insights_read=given.insight_count,
                sets_read=len(given.sets),
            )
        )
        self._publish("patterns.started", run_id, status="running")
        row = _require(await self.runs.get(run_id), run_id)
        return _Prepared(
            row=row, given=given, system=system, user=user, version=version, model=resolved
        )

    async def _complete(self, prepared: _Prepared) -> PatternRunRow:
        """The provider call and the bookkeeping. Never raises for a failure."""
        run_id = prepared.row.id
        try:
            result = await self.llm.call_json(
                LlmRequest(
                    messages=[
                        LlmMessage(role="system", content=prepared.system),
                        LlmMessage(role="user", content=prepared.user),
                    ],
                    output_model=PatternsResult,
                    model=prepared.model,
                    purpose="patterns",
                    label="find patterns",
                    subject=f"{prepared.row.sets_read} Sets",
                    prompt_name=PATTERNS_PROMPT,
                    prompt_version=prepared.version,
                    retry_delay_s=self.retry_delay_s,
                )
            )
        except asyncio.CancelledError:
            # Not a provider failure, so not a `failed` run: the row stays `running` and boot
            # marks it `interrupted`, which is what happened.
            self._publish("patterns.failed", run_id, status="running")
            raise
        except Exception as exc:
            await self.runs.finish_failed(
                run_id,
                PatternRunOutcome(status="failed", error=f"unknown: {type(exc).__name__}: {exc}"),
            )
            log.exception("patterns_crashed", run_id=run_id)
            self._publish("patterns.failed", run_id, status="failed")
            raise

        if not isinstance(result, Ok):
            row = await self.runs.finish_failed(
                run_id,
                PatternRunOutcome(
                    status="failed",
                    error=f"{result.code}: {result.message}",
                    provider=result.provider,
                    model=result.model,
                    llm_call_id=result.call_id or None,
                    usage=_usage(result.usage.prompt_tokens, result.usage.completion_tokens),
                ),
            )
            log.info("patterns_failed", run_id=run_id, code=result.code, provider=result.provider)
            self._publish("patterns.failed", run_id, status="failed")
            return _require(row, run_id)

        try:
            outcome = filter_proposals(result.data, prepared.given)
            row = await self.runs.finish_done(
                run_id,
                PatternRunOutcome(
                    status="done",
                    usage=_usage(result.usage.prompt_tokens, result.usage.completion_tokens),
                    provider=result.provider,
                    model=result.model,
                    llm_call_id=result.call_id or None,
                    dropped=outcome.dropped,
                ),
                outcome.kept,
            )
        except Exception as exc:
            # Whatever the model said, a run never stays `running` because filtering or
            # storing its answer raised: the row says `failed` with the error, and the
            # exception is still raised, as for any other crash in here.
            await self.runs.finish_failed(
                run_id,
                PatternRunOutcome(status="failed", error=f"unknown: {type(exc).__name__}: {exc}"),
            )
            log.exception("patterns_crashed", run_id=run_id)
            self._publish("patterns.failed", run_id, status="failed")
            raise
        log.info(
            "patterns_finished",
            run_id=run_id,
            kept=len(outcome.kept),
            dropped=sum(outcome.dropped.values()),
            model=result.model,
        )
        self._publish("patterns.finished", run_id, status="done")
        return _require(row, run_id)

    async def _render(self, given: PatternsInput) -> tuple[str, str, str]:
        """The two prompts, rendered, plus both versions joined for the ledger."""
        system = await self.prompts.load(PATTERNS_PROMPT)
        user = await self.prompts.load(PATTERNS_USER_PROMPT, given.render())
        return system.system, user.user, f"{system.version}+{user.version}"

    def _publish(self, event: str, run_id: int, *, status: str) -> None:
        """Best-effort fan-out. Observability must never fail what it observes."""
        if self.bus is None:
            return
        self.bus.publish(SseEvent(event=event, data={"run_id": run_id, "status": status}))


def _require(row: PatternRunRow | None, run_id: int) -> PatternRunRow:
    if row is None:  # pragma: no cover - the insert above guarantees it
        raise RuntimeError(f"pattern run {run_id} vanished between write and read")
    return row


def _usage(prompt_tokens: int | None, completion_tokens: int | None) -> dict[str, Any] | None:
    if prompt_tokens is None and completion_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": (prompt_tokens or 0) + (completion_tokens or 0),
    }
