"""The chat's tools, as an MCP server.

The tool *definitions* live in :mod:`gaggiclanker.tools`; this module is the
adapter that turns them into an MCP server, and it is deliberately thin. The
chat's API providers call the registry directly; the ``claude_code`` provider
reaches the same tools through this server, so one definition feeds both and a
tool added to the registry appears in both without a second registration —
read and propose, never a write to the machine.

Two mechanical things are worth knowing before editing.

**The signature is synthesised.** The SDK derives a tool's JSON Schema by
inspecting the function's parameters, and our tools are
``(ctx, input_model)``. So each registration gets a wrapper whose
``__signature__`` is rebuilt from the input model's fields, with each field's
``FieldInfo`` carried through as ``Annotated`` metadata — otherwise the bounds
that make ``limit`` safe (``le=500``) would be documented in the chat and absent
over MCP.

**Every call still goes through the dispatcher.** The wrapper does not call the
tool function; it calls :meth:`ToolRegistry.dispatch`, so the permission check,
the per-tool timeout and the audit row happen over MCP exactly as they do for
the chat's own dispatch. A wrapper that called the function directly would be a second,
unaudited path to the same code, which is how the two drift.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import structlog
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo

from gaggiclanker import __version__
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.tools.registry import (
    CHAT_PERMISSIONS,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)
from gaggiclanker.tools.scope import ToolScope

__all__ = [
    "MCP_INSTRUCTIONS",
    "SERVER_NAME",
    "SET_INSTRUCTIONS",
    "build_mcp_server",
    "instructions_for",
]

log = structlog.get_logger(__name__)

#: The server's name, and therefore the ``mcp__<name>__<tool>`` prefix every
#: client namespaces our tools under. It is duplicated as a constant in the
#: ``claude_code`` provider, where it is half of the ``--allowedTools`` glob;
#: the two must agree, and a test asserts they do.
SERVER_NAME = "gaggiclanker"

#: Shown to the client on connect. Short: the CLI prepends it to the system
#: prompt it already has, and the place for the rules of engagement is our own
#: chat prompt, not a second copy of them here.
MCP_INSTRUCTIONS = (
    "gaggiclanker is an espresso shot archive for a GaggiMate machine. It holds every shot "
    "the machine has pulled with full telemetry, the user's verdict on each cup, the Sets "
    "they are dialling in, and a knowledge base of espresso heuristics.\n\n"
    "Start with describe_schema, then query_shots for anything about many shots at once. "
    "Cite shots by id and knowledge passages by their heading_path. Tools whose names begin "
    "with propose_, draft_ or record_ create something the user must confirm; nothing here "
    "writes to the espresso machine."
)

#: The same, for a server serving one Set's conversation. It names the tools
#: that exist *here* rather than the two that do not: a first line telling a
#: model to start with `describe_schema` inside a Set's chat is a first tool
#: call that comes back refused.
SET_INSTRUCTIONS = (
    "gaggiclanker is an espresso shot archive for a GaggiMate machine. This connection is "
    "about one Set — one coffee being dialled in — and it can see that Set only: its "
    "versions with their predictions and outcomes, its shots, and the knowledge base.\n\n"
    "Start with get_set for the experiment so far, then list_set_shots for the shots behind "
    "it. Cite shots by id and knowledge passages by their heading_path. Tools whose names "
    "begin with propose_, draft_ or record_ create something the user must confirm; nothing "
    "here writes to the espresso machine."
)


def instructions_for(scope: ToolScope | None) -> str:
    """What the client is told this connection is, from the same scope."""
    return SET_INSTRUCTIONS if scope is not None and scope.kind == "set" else MCP_INSTRUCTIONS


#: Produces the context one call runs with. Async so building one may read the
#: archive; the stdio entry point's is a plain constructor today.
type ContextFactory = Callable[[], Awaitable[ToolContext]]


def _annotation(field: FieldInfo) -> Any:
    """The field's type with its constraints and description, but no default.

    The default travels as the parameter's own default, and a ``FieldInfo``
    carrying one as well is a ``TypeError`` from pydantic the moment the SDK
    rebuilds the argument model ("cannot specify both default and
    default_factory"). So the *constraints* are forwarded — ``ge``, ``le``,
    ``min_length`` and the rest, which are plain metadata objects — plus a
    description-only ``FieldInfo``, and nothing else.
    """
    extras: list[Any] = list(field.metadata)
    if field.description:
        extras.append(Field(description=field.description))
    if not extras:
        return field.annotation
    # `Annotated[X, *extras]` is not valid syntax in a subscript, so the tuple
    # form is used deliberately here rather than as a comprehension.
    return Annotated[(field.annotation, *extras)]


def _parameters(spec: ToolSpec) -> list[inspect.Parameter]:
    """The input model's fields as keyword parameters, constraints intact."""
    parameters: list[inspect.Parameter] = []
    for name, field in spec.input_model.model_fields.items():
        annotation = _annotation(field)
        default = (
            inspect.Parameter.empty
            if field.is_required()
            else field.get_default(call_default_factory=True)
        )
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
    return parameters


def _wrapper(
    spec: ToolSpec, registry: ToolRegistry, context: ContextFactory
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """One MCP-callable function for one registered tool."""

    async def call(**kwargs: Any) -> dict[str, Any]:
        ctx = await context()
        outcome = await registry.dispatch(ctx, spec.name, kwargs)
        if not outcome.ok:
            # `ToolError`, not a bare exception: an anticipated failure comes
            # back as `is_error=True` *with the message*, where anything else is
            # treated as a crash and the model is shown only "Error executing
            # tool <name>". A refused permission and a rejected SQL statement
            # are exactly the failures the model is supposed to read and fix.
            raise ToolError(str(outcome.data.get("detail") or outcome.error or "tool failed"))
        return outcome.data

    call.__name__ = spec.name
    call.__doc__ = spec.description
    call.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        _parameters(spec), return_annotation=dict[str, Any]
    )
    return call


def build_mcp_server(
    context: ContextFactory,
    *,
    registry: ToolRegistry,
    permissions: frozenset[str] = CHAT_PERMISSIONS,
    scope: ToolScope | None = None,
    db_for_resources: Any = None,
) -> MCPServer[Any]:
    """An ``MCPServer`` carrying every tool this conversation is allowed to see.

    ``permissions`` defaults to the chat's own set, and both entry points leave
    it there: the MCP server is read-only by design (``propose`` writes to this
    archive, never to the machine), and no setting widens it. ``scope`` is the
    other narrowing, and it is the one that differs per conversation — a Set's
    chat gets the Set's tools and a general one gets the archive's, from the
    same function the dispatcher and the provider schemas use.

    A tool outside either set is not merely refused at call time, it is not
    advertised. A client that cannot see a tool does not plan around it.
    """

    server: MCPServer[Any] = MCPServer(
        name=SERVER_NAME,
        title="gaggiclanker",
        version=__version__,
        instructions=instructions_for(scope),
        # The tools are ours and the clients are the user's own agents; a
        # duplicate-name warning would only fire on a programming error, which
        # `ToolRegistry.register` already refuses.
        warn_on_duplicate_tools=False,
    )
    for spec in registry.specs(permissions, scope):
        server.add_tool(
            _wrapper(spec, registry, context),
            name=spec.name,
            description=spec.description,
        )
    _add_resources(server, context, db_for_resources, scope)
    return server


def _add_resources(
    server: MCPServer[Any],
    context: ContextFactory,
    db: Any = None,
    scope: ToolScope | None = None,
) -> None:
    """Three resources: the rules, one knowledge document, one Set.

    Resources rather than tools because that is what they are — addressable
    documents a client can attach to a conversation without the model deciding
    to call something. The rules in particular are the tier a client should be
    able to pin.
    """

    async def _db() -> Any:
        return db if db is not None else (await context()).db

    @server.resource(
        f"{SERVER_NAME}://knowledge/rules",
        name="Espresso rules",
        description="The authoritative dial-in heuristics tier, as JSON.",
        mime_type="application/json",
    )
    async def rules() -> str:
        rows = await RulesRepository(await _db()).list_rules(enabled=True)
        return json.dumps(
            [
                {
                    "id": rule.id,
                    "category": rule.category,
                    "key": rule.key,
                    "text": rule.text,
                    "unit": rule.unit,
                    "confidence": rule.confidence,
                    "source": rule.source,
                }
                for rule in rows
            ],
            indent=2,
        )

    @server.resource(
        f"{SERVER_NAME}://knowledge/docs/{{slug}}",
        name="Knowledge document",
        description="One knowledge document's markdown, by slug.",
        mime_type="text/markdown",
    )
    async def document(slug: str) -> str:
        row = await KnowledgeService(await _db()).docs.get_doc(slug)
        if row is None:
            raise ResourceError(f"No knowledge document {slug!r}")
        return row.body

    @server.resource(
        f"{SERVER_NAME}://sets/{{set_id}}",
        name="Set summary",
        description="One Set: what it is, and every version with its intent.",
        mime_type="application/json",
    )
    async def one_set(set_id: str) -> str:
        try:
            identifier = int(set_id)
        except ValueError:
            raise ResourceError(f"{set_id!r} is not a Set id") from None
        # The scope is a limit here as well as on the tools. A resource is
        # fetched by URI rather than called, so a client that can read any Set
        # by address would be the one way around a Set conversation's whole
        # point — and the refusal says nothing about the Set it refuses.
        if scope is not None and scope.kind == "set" and identifier != scope.set_id:
            raise ResourceError(
                f"This conversation is about Set {scope.set_id} and can see no other"
            )
        repo = SetsRepository(await _db())
        row = await repo.get(identifier)
        if row is None:
            raise ResourceError(f"No Set {identifier}")
        versions = await repo.versions(identifier)
        return json.dumps(
            {
                "set": row.model_dump(mode="json"),
                "versions": [version.model_dump(mode="json") for version in versions],
            },
            indent=2,
            default=str,
        )


def tool_output_schema(spec: ToolSpec) -> dict[str, Any]:
    """The output model's schema. Exported for the docs and the tests."""
    model: type[BaseModel] = spec.output_model
    return model.model_json_schema()
