"""One definition per tool, three consumers: chat, MCP, and the audit ledger.

A tool here is an ``async def f(ctx, input) -> output`` with pydantic models on
both sides. That single declaration produces the function-calling schema the
OpenAI and Anthropic APIs want, the registration FastMCP wants, and the row the
``tool_calls`` table wants — which is the whole reason this module exists rather
than three lists of tools that drift apart.

Three rules are encoded here rather than left to the call sites.

**Permission is a property of the tool, not of the caller's intent.** Every tool
declares ``read`` or ``propose`` and the dispatcher checks it before the
function runs. ``read`` touches nothing; ``propose`` writes to gaggiclanker only,
and everything it creates is something a person still has to confirm in the UI.
There is no third class. A tool that could change the machine cannot be
registered at all, so the chat gets the same set whichever provider runs it —
the API providers through this dispatcher, ``claude_code`` through the MCP server
built from it:
the machine is written by this application's own HTTP routes, behind buttons a
person presses, and never by a sentence somebody types to a model.

**Every dispatch is audited, including the refusals.** A tool that was refused
for lack of a permission and a tool that was never called look identical from
the transcript, and the difference is exactly what somebody debugging "why did
it not do the thing" needs. The input is stored as a hash: an argument can be a
whole SQL statement, the transcript already holds the readable copy, and this
table is for "which tool, how long, did it work".

**What exists is decided by the conversation, not by the caller.**
:mod:`gaggiclanker.tools.scope` maps a conversation's kind — one Set, or the
archive — to the tools it has, and the dispatcher refuses anything outside it
with a message the model can read. The same scope produces the schemas the
provider is sent and the registrations the MCP server makes, so a tool a Set
conversation cannot call is also a tool it was never told about.

**A tool cannot hang the run.** Each one has its own timeout and a tool that
blows it comes back as an error *value* the model can read and react to, not as
an exception that ends the turn. A model that is told "that query took too long,
try a narrower one" usually does.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, get_type_hints

import structlog
from pydantic import BaseModel, ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.infra.tasks import TaskSpawner
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.scope import ToolScope

if TYPE_CHECKING:
    from gaggiclanker.drafts.proposals import DraftProposals

__all__ = [
    "DEFAULT_TOOL_TIMEOUT_S",
    "Permission",
    "ToolContext",
    "ToolOutcome",
    "ToolRegistry",
    "ToolSpec",
    "registry",
    "tool",
]

log = structlog.get_logger(__name__)

#: What a tool is allowed to touch. Deliberately two values: nothing a language
#: model drives may write to the machine, whichever client it arrives through.
type Permission = Literal["read", "propose"]

#: Longest one tool may run. Generous next to a SELECT and short next to the
#: provider call wrapping it, so a wedged tool never becomes a wedged run.
DEFAULT_TOOL_TIMEOUT_S = 20.0

#: The order permissions are widened in, so a caller can say "read and propose"
#: as a set without anyone writing the same tuple twice.
READ_ONLY: frozenset[str] = frozenset({"read"})
#: Every class a tool may declare, and what the chat and MCP are both handed.
#: One constant for both callers rather than a function of the settings: there
#: is no switch that widens it, so there is nothing to resolve.
CHAT_PERMISSIONS: frozenset[str] = frozenset({"read", "propose"})


@dataclass(slots=True)
class ToolContext:
    """Everything a tool is allowed to reach, and who is asking.

    Services are optional because the two callers differ: the app has an
    analyzer on ``app.state``, the stdio MCP entry
    point opens a database and nothing else. A tool that needs one it was not
    given says so as an error value rather than raising ``AttributeError`` at
    the bottom of a stack the model cannot read.

    **Nothing here reaches the machine.** Not the device client, not the
    connection that owns it, and not a service holding either: a tool that can
    only read and propose is handed only things that read and propose. The
    draft entry is :class:`~gaggiclanker.drafts.proposals.DraftProposals`, never
    the service that pushes. ``tests/tools/test_no_machine_reachable.py`` walks
    the contexts the chat and the stdio server build and fails if a client or a
    connection is anywhere in the graph.

    **The guarantee comes from what is put in, not from the types here.**
    ``tasks`` is typed :class:`~gaggiclanker.infra.tasks.TaskSpawner` because
    the two tools that queue work only ever call ``spawn`` — but a narrower type
    stops nobody: whatever object is passed still answers ``get()`` and
    ``cancel()`` if it has them, and a task hands out its own coroutine frame,
    where the ``self`` it was called on is sitting. What makes this safe is that
    the registry wired in holds no task that talks to the machine; the sync
    engine's loops, a cleanup run and a notes send live on the registry
    :class:`~gaggiclanker.device.connection.DeviceConnection` keeps to itself.
    """

    db: Database
    settings: SettingsService
    #: :class:`~gaggiclanker.knowledge.service.KnowledgeService`. Untyped to
    #: keep this module free of an import cycle through the analyzer.
    knowledge: Any = None
    analyzer: Any = None
    #: Creates drafts and nothing else. Typed, and only under ``TYPE_CHECKING``,
    #: so the wiring cannot hand a tool the draft service that pushes.
    drafts: DraftProposals | None = None
    #: Queues one background task by name. Never the full registry's type: see
    #: the class docstring for why that is a statement of intent and not a
    #: barrier. ``None`` over stdio MCP, where there is no application to queue
    #: work on.
    tasks: TaskSpawner | None = None
    #: :class:`~gaggiclanker.infra.ratelimit.RateLimiter`. The one tool that
    #: spends provider tokens checks it, so a model in a loop cannot do what the
    #: route it shortcuts is already stopped from doing.
    rate_limits: Any = None
    #: What this conversation is: the whole archive read-only, or one Set. It
    #: decides which tools exist (the schemas, the dispatcher and the MCP
    #: server all read it) and it is what tools taking a ``set_id`` resolve
    #: against — so "how is it going?" has an answer, and "how is Set 7 going?"
    #: asked inside Set 3's conversation has a refusal.
    scope: ToolScope = field(default_factory=ToolScope)
    #: Which conversation this is, when there is one. Not a limit — the scope
    #: above is — but a proposed change records the room it was argued in, so
    #: the experiment log can offer a way back to the reasoning. ``None`` is an
    #: ordinary answer: a proposal outlives its chat and works without one.
    thread_id: int | None = None
    user: str = ""
    #: Bookkeeping for the audit row: which run, and whether this came from the
    #: chat's own dispatch or through its stdio MCP server.
    run_id: int | None = None
    caller: str = "chat"
    #: What this caller may invoke. Narrowed by the dispatcher, never widened.
    permissions: frozenset[str] = field(default_factory=lambda: CHAT_PERMISSIONS)

    @property
    def set_id(self) -> int | None:
        """The Set this conversation is about, if it is about one.

        A property over the scope rather than a field beside it: two places to
        read the Set from is two answers the day one of them is set and the
        other is not.
        """
        return self.scope.set_id


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """What one dispatch produced. A failure is a value, as in the LLM layer."""

    name: str
    ok: bool
    #: JSON-ready output on success; ``{"error": ...}`` on failure.
    data: dict[str, Any]
    status: Literal["ok", "error", "refused", "timeout"] = "ok"
    error: str = ""
    duration_ms: int = 0

    def as_content(self) -> str:
        """What the model is shown. JSON either way, so parsing never branches."""
        return json.dumps(self.data, default=str)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One registered tool."""

    name: str
    permission: Permission
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: Callable[[ToolContext, Any], Awaitable[BaseModel]]
    timeout_s: float = DEFAULT_TOOL_TIMEOUT_S

    def input_schema(self) -> dict[str, Any]:
        """The JSON Schema for the arguments, with ``$defs`` left in place.

        Both APIs accept ``$defs``/``$ref``, and inlining them would mean
        re-implementing pydantic's reference resolution for no gain.
        """
        return self.input_model.model_json_schema()

    def openai_schema(self) -> dict[str, Any]:
        """``{"type": "function", "function": {...}}`` — chat completions."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema(),
            },
        }

    def anthropic_schema(self) -> dict[str, Any]:
        """``{"name", "description", "input_schema"}`` — the Messages API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema(),
        }


class ToolRegistry:
    """The set of tools, and the one place a call is checked and audited."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def register(self, spec: ToolSpec) -> None:
        # Checked at runtime as well as by the type: a `Literal` is a hint, and
        # a tool declared with a permission outside the two classes is exactly
        # the tool that would reach the machine from a model's say-so.
        if spec.permission not in CHAT_PERMISSIONS:
            raise ValueError(
                f"tool {spec.name!r} declares permission {spec.permission!r}; tools may only "
                "read or propose — the machine is written by the app's own routes, never by a tool"
            )
        if spec.name in self._tools:
            raise RuntimeError(f"tool {spec.name!r} is already registered")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self, scope: ToolScope | None = None) -> list[str]:
        """Every registered tool, or only the ones this conversation has."""
        return [name for name in sorted(self._tools) if scope is None or scope.allows(name)]

    def specs(
        self, permissions: Iterable[str] = CHAT_PERMISSIONS, scope: ToolScope | None = None
    ) -> list[ToolSpec]:
        """Every tool this caller may see, in a stable order.

        Two narrowings, and they answer different questions. ``permissions`` is
        what the *caller* may do — read, or read and propose. ``scope`` is what
        this *conversation* has at all, which is the Set-or-archive rule in
        :mod:`gaggiclanker.tools.scope`; left ``None`` it is the whole registry,
        which is what the docs and a test of the registry itself want.

        Sorted by name rather than registration order because the list becomes
        a prompt: a tool list that reorders itself between calls busts the
        provider's prompt cache for nothing.
        """
        allowed = frozenset(permissions)
        return [
            self._tools[name]
            for name in self.names(scope)
            if self._tools[name].permission in allowed
        ]

    def openai_schemas(
        self, permissions: Iterable[str] = CHAT_PERMISSIONS, scope: ToolScope | None = None
    ) -> list[dict[str, Any]]:
        return [spec.openai_schema() for spec in self.specs(permissions, scope)]

    def anthropic_schemas(
        self, permissions: Iterable[str] = CHAT_PERMISSIONS, scope: ToolScope | None = None
    ) -> list[dict[str, Any]]:
        return [spec.anthropic_schema() for spec in self.specs(permissions, scope)]

    # -- the dispatcher ---------------------------------------------------

    async def dispatch(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """Run one tool. Never raises for anything the tool or the model did.

        The order is deliberate: exists, in scope, permitted, arguments valid,
        then run. The three refusals are different messages because they are
        different mistakes — a hallucinated name, a tool this conversation does
        not have, and a caller that may only read — and a model that is told
        which one it made stops making it.

        A tool outside the scope is refused here as well as left out of the
        schemas, because "not offered" is not a guarantee: a provider replaying
        an older turn, or a model that has read the name somewhere, will call
        it. The refusal is an error *value* the model reads, never an exception
        that ends the run.
        """
        started = time.monotonic()
        spec = self._tools.get(name)
        if spec is None:
            outcome = _refused(
                name,
                f"No tool named {name!r}. Call one of: {', '.join(self.names(ctx.scope))}.",
            )
            await self._audit(ctx, outcome, permission="read")
            return outcome

        if not ctx.scope.allows(name):
            outcome = _refused(name, ctx.scope.refusal(name))
            await self._audit(ctx, outcome, permission=spec.permission)
            return outcome

        if spec.permission not in ctx.permissions:
            outcome = _refused(
                name,
                f"The tool {name!r} is not available to this caller "
                f"(it needs the {spec.permission!r} permission class).",
            )
            await self._audit(ctx, outcome, permission=spec.permission)
            return outcome

        try:
            parsed = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            outcome = ToolOutcome(
                name=name,
                ok=False,
                data={"error": "invalid_arguments", "detail": _render_errors(exc)},
                status="error",
                error="invalid arguments",
                duration_ms=_elapsed(started),
            )
            await self._audit(ctx, outcome, permission=spec.permission, arguments=arguments)
            return outcome

        try:
            async with asyncio.timeout(spec.timeout_s):
                result = await spec.fn(ctx, parsed)
        except TimeoutError:
            outcome = ToolOutcome(
                name=name,
                ok=False,
                data={
                    "error": "timeout",
                    "detail": f"{name} did not finish within {spec.timeout_s:g}s. Ask for less.",
                },
                status="timeout",
                error="timeout",
                duration_ms=_elapsed(started),
            )
        except asyncio.CancelledError:
            # The run was cancelled. Not a tool failure: record nothing and let
            # the cancellation reach the runner, which owns the run's status.
            raise
        except Exception as exc:
            log.warning("tool_failed", tool=name, exc_info=True)
            outcome = ToolOutcome(
                name=name,
                ok=False,
                data={"error": "failed", "detail": str(exc)[:500]},
                status="error",
                error=str(exc)[:500],
                duration_ms=_elapsed(started),
            )
        else:
            outcome = ToolOutcome(
                name=name,
                ok=True,
                data=result.model_dump(mode="json"),
                status="ok",
                duration_ms=_elapsed(started),
            )

        await self._audit(ctx, outcome, permission=spec.permission, arguments=arguments)
        return outcome

    async def _audit(
        self,
        ctx: ToolContext,
        outcome: ToolOutcome,
        *,
        permission: str,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        """Write the ledger row. Best effort: auditing must not fail the call.

        Imported here rather than at module scope because the repository
        imports the database layer, and this module is imported by the tools
        themselves — the cycle is real and the import is cheap.
        """
        from gaggiclanker.db.repos.chat import ToolCallRow, ToolCallsRepository

        try:
            await ToolCallsRepository(ctx.db).record(
                ToolCallRow(
                    run_id=ctx.run_id,
                    caller=ctx.caller,
                    tool=outcome.name,
                    permission=permission,
                    input_hash=input_hash(arguments or {}),
                    duration_ms=outcome.duration_ms,
                    status=outcome.status,
                    error=outcome.error or None,
                )
            )
        except Exception:
            log.warning("tool_audit_failed", tool=outcome.name, exc_info=True)

    # -- registration -----------------------------------------------------

    def tool(
        self,
        name: str,
        *,
        permission: Permission = "read",
        description: str = "",
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> Callable[[Callable[[ToolContext, Any], Awaitable[Any]]], Callable[..., Any]]:
        """Decorate ``async def f(ctx, input) -> output`` into a registered tool.

        The models come from the annotations rather than from arguments here,
        so there is exactly one place each is written down and a rename cannot
        leave the schema pointing at the old one.
        """

        def decorate(fn: Callable[[ToolContext, Any], Awaitable[Any]]) -> Callable[..., Any]:
            hints = get_type_hints(fn)
            parameters = list(inspect.signature(fn).parameters)
            if len(parameters) != 2:
                raise TypeError(f"tool {name!r} must take exactly (ctx, input)")
            input_model = hints.get(parameters[1])
            output_model = hints.get("return")
            if not (isinstance(input_model, type) and issubclass(input_model, BaseModel)):
                raise TypeError(f"tool {name!r} must annotate its input as a pydantic model")
            if not (isinstance(output_model, type) and issubclass(output_model, BaseModel)):
                raise TypeError(f"tool {name!r} must annotate its return as a pydantic model")
            self.register(
                ToolSpec(
                    name=name,
                    permission=permission,
                    description=description or inspect.getdoc(fn) or name,
                    input_model=input_model,
                    output_model=output_model,
                    fn=fn,
                    timeout_s=timeout_s,
                )
            )
            return fn

        return decorate


def _refused(name: str, detail: str) -> ToolOutcome:
    return ToolOutcome(
        name=name,
        ok=False,
        data={"error": "refused", "detail": detail},
        status="refused",
        error=detail,
    )


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def input_hash(arguments: dict[str, Any]) -> str:
    """A stable sha256 over the arguments, short enough to read in a log line."""
    payload = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _render_errors(exc: ValidationError) -> str:
    """The pydantic errors as one line, naming fields and never values."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()
    )[:500]


#: The process-wide registry. One per process because the tools are module-level
#: declarations; a test that wants an empty one builds its own ``ToolRegistry``.
registry = ToolRegistry()

#: The module-level decorator the tool modules use: ``@tool("get_shot", ...)``.
tool = registry.tool
