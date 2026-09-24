"""``gaggiclanker mcp`` — the chat's tool server, over stdio.

Its caller is the ``claude_code`` chat provider, which points the Claude Code CLI
at this entry point through a generated ``--mcp-config``; the CLI spawns it as a
child process for the length of one chat turn.

**The conversation's scope arrives in the environment**, because the CLI runs
the tool loop itself: `GAGGICLANKER_MCP_SET_ID` (and the version beside it)
makes this server a Set conversation, offering the Set's tools and refusing any
other Set, and its absence makes it a general one. Whether that Set is being
designed is not in the environment: the server opens the archive anyway, and
reads the flag from the Set itself with the same rule the runner uses. `GAGGICLANKER_MCP_THREAD_ID`
is not a scope: it is which conversation this is, so a change proposed here
records the room it was argued in. The mapping from those two
ids to a surface is :mod:`gaggiclanker.tools.scope`, the same one the dispatcher
and the provider schemas use — the CLI's loop is out of the dispatcher's reach,
so the surface is narrowed where it is built instead.

It opens the archive directly from ``DATA_DIR`` and wires nothing else: no
machine connection, and nothing that could reach one. Proposing a profile draft
needs only the database and the safety bounds, so ``draft_profile`` works here
exactly as it does in the app. ``run_analysis`` and ``starting_point`` queue a
provider call on the running application's task registry, and over stdio they
return an error naming that rather than half-working.

**No migrations are run here.** A second process migrating a database the
application is also using is a race with a schema at the end of it; instead the
schema is probed and a missing one is an error telling the user to start
gaggiclanker once. The server is a client of the archive, not an owner of it.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.drafts.proposals import DraftProposals
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools import registry as tool_registry
from gaggiclanker.tools.mcp.server import build_mcp_server
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext
from gaggiclanker.tools.scope import ToolScope

__all__ = [
    "add_mcp_parser",
    "mcp_command",
    "scope_from",
    "serve_stdio",
    "stdio_tool_context",
    "thread_from",
]

log = structlog.get_logger(__name__)


def add_mcp_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``gaggiclanker mcp`` — serve the tool surface on stdin/stdout."""
    parser = subparsers.add_parser(
        "mcp",
        help="the chat's database tools over MCP on stdio (spawned by the claude_code provider)",
        description=(
            "Speaks the Model Context Protocol on stdin/stdout. The claude_code chat provider "
            "starts this for its tool loop. The archive is read from DATA_DIR, the same "
            "directory the server uses; start the server once first so the schema exists. "
            "Nothing is printed on stdout except protocol messages."
        ),
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="Where the archive lives. Defaults to $DATA_DIR, then ./data.",
    )
    parser.add_argument(
        "--set-id",
        type=int,
        default=None,
        help=(
            "The Set this conversation is about. It is a limit, not a default: with it "
            "the server offers the Set conversation's tools and refuses any other Set. "
            "Without it the server is a general conversation — the whole archive, "
            "read-only. Defaults to $GAGGICLANKER_MCP_SET_ID."
        ),
    )
    parser.add_argument(
        "--set-version-id",
        type=int,
        default=None,
        help=(
            "Which version of that Set is being argued. Defaults to "
            "$GAGGICLANKER_MCP_SET_VERSION_ID. Ignored without --set-id."
        ),
    )
    parser.add_argument(
        "--thread-id",
        type=int,
        default=None,
        help=(
            "Which conversation this is. Not a limit — it narrows nothing — but a "
            "change proposed here records the room it was argued in, so the "
            "experiment log can offer a way back to the reasoning. Defaults to "
            "$GAGGICLANKER_MCP_THREAD_ID."
        ),
    )


def _data_dir(given: str) -> Path:
    return Path(given or os.environ.get("DATA_DIR") or "data").expanduser().resolve()


def _identifier(given: int | None, variable: str) -> int | None:
    """One scope id, from the flag or the environment, or a refusal to start.

    **A scope that is present must parse.** The whole point of the scope is that
    it narrows what this server offers, so anything it cannot read is a server
    that would serve the *wider* surface — a Set's conversation with the whole
    archive in it. `abc`, `-1`, `1e0`, `²` and an empty string are all "somebody
    meant to scope this and it did not arrive", and every one of them exits
    rather than falling back to a general conversation. Absent is the only way
    to ask for a general one.

    The flag and the variable are both read and must agree, because they are two
    ways of saying the same thing and a disagreement means one of them is stale.
    """
    from_flag: int | None = None
    if given is not None:
        if given <= 0:
            raise SystemExit(f"{variable}: a scope id must be a positive integer, got {given}")
        from_flag = given

    from_env: int | None = None
    raw = os.environ.get(variable)
    if raw is not None:
        stripped = raw.strip()
        if not stripped.isascii() or not stripped.isdigit() or int(stripped) <= 0:
            raise SystemExit(
                f"{variable}: a scope id must be a positive integer, got {raw!r}. "
                "Unset it for a conversation about the whole archive."
            )
        from_env = int(stripped)

    if from_flag is not None and from_env is not None and from_flag != from_env:
        raise SystemExit(
            f"{variable} is {from_env} and the flag says {from_flag}; they must agree."
        )
    return from_flag if from_flag is not None else from_env


def scope_from(args: argparse.Namespace) -> ToolScope:
    """The conversation this server is serving, from the flags or the environment.

    The kind follows the Set: a server told which Set it is about is a Set
    conversation and a server told nothing is a general one. One rule, and it
    is :meth:`ToolScope.for_thread` — the same one the runner applies to a
    thread's two columns, so the CLI's own tool loop sees exactly the surface
    the other providers are sent schemas for.

    Everything it cannot make sense of is an exit, never a fallback: see
    :func:`_identifier`. A version without a Set is one of those — it names a
    row this server could not check against anything.
    """
    set_id = _identifier(getattr(args, "set_id", None), "GAGGICLANKER_MCP_SET_ID")
    version_id = _identifier(
        getattr(args, "set_version_id", None), "GAGGICLANKER_MCP_SET_VERSION_ID"
    )
    if set_id is None and version_id is not None:
        raise SystemExit(
            "GAGGICLANKER_MCP_SET_VERSION_ID is set without GAGGICLANKER_MCP_SET_ID: "
            "a version belongs to a Set, and without it there is nothing to check it against."
        )
    return ToolScope.for_thread(set_id, version_id)


def thread_from(args: argparse.Namespace) -> int | None:
    """Which conversation this server is serving, when it was told.

    Read with the same rule as the scope ids — anything present must parse, and
    :func:`_check_scope_exists` then requires it to be a conversation of the
    scope's Set. It narrows nothing, so its *absence* is ordinary rather than a
    fallback to something wider: a proposal made without it simply names no
    chat. What is not ordinary is a thread that is not this Set's, because the
    column is the record of where a change was argued.
    """
    return _identifier(getattr(args, "thread_id", None), "GAGGICLANKER_MCP_THREAD_ID")


def stdio_tool_context(
    db: Database,
    settings: SettingsService,
    *,
    scope: ToolScope | None = None,
    thread_id: int | None = None,
) -> ToolContext:
    """What one tool call over stdio is handed: the archive, and no machine.

    The chat's permission set, unconditionally: tools read and propose, and the
    machine is written only by the application's own routes.
    """
    return ToolContext(
        db=db,
        settings=settings,
        knowledge=KnowledgeService(db),
        drafts=DraftProposals(db, settings),
        scope=scope or ToolScope(),
        thread_id=thread_id,
        caller="mcp-stdio",
        permissions=CHAT_PERMISSIONS,
    )


async def serve_stdio(
    data_dir: Path, *, scope: ToolScope | None = None, thread_id: int | None = None
) -> int:
    """Open the archive and run the protocol on stdio until the client hangs up."""
    path = data_dir / "gaggiclanker.db"
    if not path.exists():
        raise SystemExit(
            f"No archive at {path}. Start gaggiclanker once (or point --data-dir at the "
            "directory the server uses) so the database exists."
        )
    db = Database(path)
    await db.connect()
    try:
        applied = await db.fetch_value(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'view' AND name = 'v_shots'"
        )
        if not applied:
            raise SystemExit(
                f"The archive at {path} predates the chat tools. Start gaggiclanker once to "
                "apply its migrations, then try again."
            )
        settings = SettingsService(SettingsRepository(db))
        requested = scope or ToolScope()
        await _check_scope_exists(db, requested, thread_id)
        # Whether the Set is being designed is the archive's to say, read here
        # as the runner reads it for every other provider: the same rule, so
        # the CLI's own tool loop sees the surface the dispatcher would allow.
        # This process lives for one turn, so reading it once is reading it
        # every turn.
        conversation = await ToolScope.resolve(db, requested.set_id, requested.set_version_id)

        async def context() -> ToolContext:
            return stdio_tool_context(db, settings, scope=conversation, thread_id=thread_id)

        server = build_mcp_server(
            context,
            registry=tool_registry,
            permissions=CHAT_PERMISSIONS,
            scope=conversation,
            db_for_resources=db,
        )
        await server.run_stdio_async()
    finally:
        await db.close()
    return 0


async def _check_scope_exists(db: Database, scope: ToolScope, thread_id: int | None = None) -> None:
    """Every id this server was handed has to be real, or it does not start.

    Checked here rather than in :func:`scope_from` because it needs the archive.
    A Set id nobody recognises would serve a conversation about nothing with a
    Set's tools, and a version of *another* Set would put this conversation's
    opening context and its tools on two different experiments.

    The thread is checked the same way and for a sharper reason: it is written
    onto every change proposed here as the room the change was argued in, and
    one belonging to another Set would file this Set's reasoning under somebody
    else's transcript. A server that cannot record that truthfully serves
    nothing at all — the same answer the scope variables get, rather than
    starting and refusing every proposal later.
    """
    if scope.kind != "set" or scope.set_id is None:
        if thread_id is not None:
            raise SystemExit(
                "GAGGICLANKER_MCP_THREAD_ID is set without GAGGICLANKER_MCP_SET_ID: a "
                "conversation about the whole archive changes no Set, so there is nothing "
                "for it to be the room of."
            )
        return
    sets = SetsRepository(db)
    if await sets.get(scope.set_id) is None:
        raise SystemExit(f"No Set {scope.set_id} in this archive.")
    if scope.set_version_id is not None:
        if await sets.version_of_set(scope.set_id, scope.set_version_id) is None:
            raise SystemExit(
                f"Version {scope.set_version_id} is not a version of Set {scope.set_id}."
            )
    if thread_id is not None:
        found = await db.fetch_value(
            "SELECT 1 FROM chat_threads WHERE id = ? AND set_id = ?", (thread_id, scope.set_id)
        )
        if found is None:
            raise SystemExit(
                f"Conversation {thread_id} is not a conversation of Set {scope.set_id}."
            )


def mcp_command(args: argparse.Namespace) -> int:
    """The argparse entry point. Synchronous, because ``main`` is."""
    import asyncio

    return asyncio.run(
        serve_stdio(_data_dir(args.data_dir), scope=scope_from(args), thread_id=thread_from(args))
    )
