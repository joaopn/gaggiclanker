"""A seeded archive and a tool context over it.

The fixture is the analyzer's — one Set, six shots, five judgements, the whole
knowledge base — because the tools are asked the same questions an analysis is
and building a second archive would mean two places to keep the coffee facts
consistent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext
from gaggiclanker.tools.scope import ToolScope
from tests.analyzer.conftest import Fixture, build_fixture


@pytest.fixture
async def tool_db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "tools.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def archive(tool_db: Database) -> Fixture:
    return await build_fixture(tool_db)


@pytest.fixture
def settings(tool_db: Database) -> SettingsService:
    return SettingsService(SettingsRepository(tool_db))


def _context(archive: Fixture, settings: SettingsService, scope: ToolScope) -> ToolContext:
    return ToolContext(
        db=archive.db,
        settings=settings,
        knowledge=KnowledgeService(archive.db),
        scope=scope,
        caller="test",
        permissions=CHAT_PERMISSIONS,
    )


@pytest.fixture
def ctx(archive: Fixture, settings: SettingsService) -> ToolContext:
    """A general conversation: the whole archive, read-only, no Set."""
    return _context(archive, settings, ToolScope())


@pytest.fixture
def set_ctx(archive: Fixture, settings: SettingsService) -> ToolContext:
    """A conversation about the fixture Set: its tools, and nothing else's."""
    return _context(archive, settings, ToolScope.for_thread(archive.set_id))


@dataclass(frozen=True, slots=True)
class OwnRegistry(ToolScope):
    """A scope for a registry a test built for itself.

    The two real scopes are allow-lists of the application's own tools, which is
    exactly what they should be — so a test that registers `double` in a
    registry of its own would have it refused for not being a chat tool, which
    is true and has nothing to do with what that test is about (the audit row,
    the timeout, the error value). This says "whatever this registry holds";
    nothing in `gaggiclanker/` constructs one.
    """

    def allows(self, name: str) -> bool:
        return True


@pytest.fixture
def own_ctx(ctx: ToolContext) -> ToolContext:
    """The general context, with a test's own registry's tools allowed."""
    ctx.scope = OwnRegistry()
    return ctx
