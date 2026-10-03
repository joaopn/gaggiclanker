"""A database with three Sets and their insights, for the pattern tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import DEFAULT_PROMPTS_DIR, PromptService, seed_prompts
from gaggiclanker.llm.service import LlmService
from gaggiclanker.patterns.service import PatternsService
from gaggiclanker.settings_service import SettingsService
from tests.llm.conftest import FakeProvider
from tests.patterns.world import PatternWorld, Talking, build_pattern_world, build_talking


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "patterns.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def world(db: Database) -> PatternWorld:
    return await build_pattern_world(db)


@pytest.fixture
def provider() -> FakeProvider:
    """A provider that answers with no proposals unless a test scripts it."""
    return FakeProvider(script=[json.dumps({"proposals": []})])


@pytest.fixture
def budget() -> RateLimitBudget:
    return RateLimitBudget(retries=0)


@pytest.fixture
def llm(db: Database, provider: FakeProvider, budget: RateLimitBudget) -> LlmService:
    return LlmService(
        SettingsService(SettingsRepository(db)),
        budget=budget,
        mode_memory=ModeMemory(),
        provider_factory=lambda _config, _name: provider,
    )


@pytest.fixture
async def talking(world: PatternWorld) -> Talking:
    """The world with its Sets' insights said: three Sets, two on light roasts."""
    return await build_talking(world)


@pytest.fixture
async def service(talking: Talking, llm: LlmService) -> PatternsService:
    await seed_prompts(PromptsRepository(talking.world.db), DEFAULT_PROMPTS_DIR)
    service = PatternsService(
        talking.world.db, llm, PromptService(PromptsRepository(talking.world.db))
    )
    service.retry_delay_s = 0.0
    return service
