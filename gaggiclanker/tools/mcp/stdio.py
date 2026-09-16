"""``gaggiclanker mcp`` — the chat's tool server, over stdio.

Its caller is the ``claude_code`` chat provider, which points the Claude Code CLI
at this entry point through a generated ``--mcp-config``; the CLI spawns it as a
child process for the length of one chat turn.

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
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.drafts.proposals import DraftProposals
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools import registry as tool_registry
from gaggiclanker.tools.mcp.server import build_mcp_server
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext

__all__ = ["add_mcp_parser", "mcp_command", "serve_stdio", "stdio_tool_context"]

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
            "Scope tools that take a Set to this one, so the chat can ask 'how is it "
            "going' without naming it. Defaults to $GAGGICLANKER_MCP_SET_ID."
        ),
    )


def _data_dir(given: str) -> Path:
    return Path(given or os.environ.get("DATA_DIR") or "data").expanduser().resolve()


def _set_id(given: int | None) -> int | None:
    if given is not None:
        return given
    raw = os.environ.get("GAGGICLANKER_MCP_SET_ID", "").strip()
    return int(raw) if raw.isdigit() else None


def stdio_tool_context(
    db: Database, settings: SettingsService, *, set_id: int | None = None
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
        set_id=set_id,
        caller="mcp-stdio",
        permissions=CHAT_PERMISSIONS,
    )


async def serve_stdio(data_dir: Path, *, set_id: int | None = None) -> int:
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

        async def context() -> ToolContext:
            return stdio_tool_context(db, settings, set_id=set_id)

        server = build_mcp_server(
            context, registry=tool_registry, permissions=CHAT_PERMISSIONS, db_for_resources=db
        )
        await server.run_stdio_async()
    finally:
        await db.close()
    return 0


def mcp_command(args: argparse.Namespace) -> int:
    """The argparse entry point. Synchronous, because ``main`` is."""
    import asyncio

    return asyncio.run(
        serve_stdio(_data_dir(args.data_dir), set_id=_set_id(getattr(args, "set_id", None)))
    )
