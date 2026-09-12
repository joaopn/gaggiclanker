"""A seeded archive and a tool context over it.

The fixture is the analyzer's — one Set, six shots, five judgements, the whole
knowledge base — because the tools are asked the same questions an analysis is
and building a second archive would mean two places to keep the coffee facts
consistent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext
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
    return SettingsService(SettingsRepository(tool_db), dotenv={})


@pytest.fixture
def ctx(archive: Fixture, settings: SettingsService) -> ToolContext:
    """A read+propose context, which is what the chat gets."""
    return ToolContext(
        db=archive.db,
        settings=settings,
        knowledge=KnowledgeService(archive.db),
        set_id=archive.set_id,
        caller="test",
        permissions=CHAT_PERMISSIONS,
    )
