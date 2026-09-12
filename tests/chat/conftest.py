"""A chat runner over the seeded archive, wired to a scripted provider.

The provider is the LLM suite's :class:`FakeProvider` with its ``chat_script``
filled in: one :class:`ChatTurn` per round, in order. A turn carrying tool calls
makes the runner dispatch them and come back for the next entry, so a loop of a
known length is a list of a known length — which is what makes the bounds, the
cancel and the reconnect tests assertions about data rather than about timing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.chat.runner import ChatRunner
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.infra.sse import EventBus, SseEvent
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import DEFAULT_PROMPTS_DIR, PromptService, seed_prompts
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import registry
from tests.analyzer.conftest import Fixture, build_fixture
from tests.llm.conftest import FakeProvider


@pytest.fixture
async def chat_db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "chat.db")
    await database.connect()
    await run_migrations(database)
    await seed_prompts(PromptsRepository(database), DEFAULT_PROMPTS_DIR)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def archive(chat_db: Database) -> Fixture:
    return await build_fixture(chat_db)


@pytest.fixture
def chat_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def bus() -> EventBus[SseEvent]:
    return EventBus[SseEvent]()


@pytest.fixture
def tasks() -> TaskRegistry:
    return TaskRegistry()


@pytest.fixture
def runner(
    archive: Fixture,
    chat_provider: FakeProvider,
    bus: EventBus[SseEvent],
    tasks: TaskRegistry,
) -> ChatRunner:
    settings = SettingsService(SettingsRepository(archive.db), dotenv={})
    llm = LlmService(
        settings,
        budget=RateLimitBudget(retries=0),
        mode_memory=ModeMemory(),
        provider_factory=lambda _config, _name: chat_provider,
    )
    return ChatRunner(
        archive.db,
        llm,
        PromptService(PromptsRepository(archive.db)),
        tools=registry,
        bus=bus,
        knowledge=KnowledgeService(archive.db),
        tasks=tasks,
    )


@pytest.fixture
async def thread(archive: Fixture) -> int:
    row = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(title="", set_id=archive.set_id)
    )
    return row.id
