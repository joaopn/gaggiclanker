"""Fixtures for the LLM suite: a fake provider, and a service wired to it.

The service is the thing under test in most of these files, and what it does is
decide *how many times* and *in which mode* to ask a provider. So the provider
is a scripted stub: a list of outcomes it returns in order, and a record of the
calls it received. That makes "fell back from json_schema to json_object", "gave
up after three attempts" and "asked again with the validation error appended"
assertions about a list rather than about a mock library.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.chat_types import ChatEvent, ChatRequest, ChatTurn, OnChatEvent
from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.providers.base import ProviderCall, ProviderReply
from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import CredentialCheck, ProviderId, ResponseMode, Usage
from gaggiclanker.settings_service import SettingsService


class Answer(BaseModel):
    """The output model the service tests validate against."""

    verdict: str
    score: int


@dataclass
class FakeProvider:
    """A provider that returns a scripted list of outcomes, in order.

    An entry is either a :class:`ProviderReply` (or a string, shorthand for one)
    or an exception to raise. Running past the end repeats the last entry, so a
    test that only cares about "it kept failing" writes one entry.
    """

    script: list[Any] = field(default_factory=list)
    id: ProviderId = "openai_compatible"
    base_url: str = "https://example.test/v1"
    modes: tuple[ResponseMode, ...] = ("json_schema", "json_object", "text")
    calls: list[ProviderCall] = field(default_factory=list)
    closed: bool = False
    credentials: CredentialCheck | None = None
    models: Sequence[str] = ()
    #: Set to a message to make the service refuse before any call, the way a
    #: provider with no API key does.
    missing: str | None = None
    #: Seconds to stall before answering. A real stall rather than a patched
    #: method, so the deadline and cancellation tests exercise the same code
    #: path as every other test here.
    delay: float = 0.0
    #: The chat half of the interface: one :class:`ChatTurn` per
    #: round, in order, with the last one repeating. A turn carrying tool calls
    #: makes the runner dispatch and come back for the next entry, which is how
    #: a loop of a known length is scripted.
    chat_script: list[Any] = field(default_factory=list)
    chat_calls: list[ChatRequest] = field(default_factory=list)
    #: Stall inside ``chat`` before answering, for the cancel tests. Checked
    #: against ``request.cancel`` as a real provider does.
    chat_delay: float = 0.0

    async def complete(self, call: ProviderCall) -> ProviderReply:
        self.calls.append(call)
        if self.delay:
            await asyncio.sleep(self.delay)
        index = min(len(self.calls) - 1, len(self.script) - 1)
        outcome = self.script[index] if self.script else '{"verdict": "ok", "score": 1}'
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, str):
            return ProviderReply(text=outcome, usage=Usage(prompt_tokens=11, completion_tokens=7))
        reply: ProviderReply = outcome
        return reply

    async def chat(self, request: ChatRequest, on_event: OnChatEvent) -> ChatTurn:
        """Emit the turn's text as deltas, then hand the turn over.

        The deltas are emitted rather than skipped because the runner persists
        each one, and "did the browser get tokens" is a property the streaming
        tests assert on.
        """
        self.chat_calls.append(request)
        if self.chat_delay:
            await asyncio.sleep(self.chat_delay)
        index = min(len(self.chat_calls) - 1, len(self.chat_script) - 1)
        outcome = self.chat_script[index] if self.chat_script else ChatTurn(text="ok")
        if isinstance(outcome, BaseException):
            raise outcome
        turn: ChatTurn = outcome
        if request.cancel is not None and request.cancel.is_set():
            return ChatTurn(text=turn.text, stop_reason="cancelled")
        for piece in turn.text.split(" "):
            on_event(ChatEvent(kind="delta", data={"text": piece + " "}))
        return turn

    def missing_credential(self) -> str | None:
        return self.missing

    async def validate_credentials(self) -> CredentialCheck:
        return self.credentials or CredentialCheck(provider=self.id, ok=True, detail="fine")

    async def list_models(self) -> list[str]:
        return list(self.models)

    async def aclose(self) -> None:
        self.closed = True

    @property
    def modes_used(self) -> list[str]:
        return [call.mode for call in self.calls]


@pytest.fixture
async def db(tmp_path: Any) -> AsyncIterator[Database]:
    """A migrated database on a real file, as the house rules require."""
    database = Database(tmp_path / "llm.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def settings(db: Database) -> SettingsService:
    """A settings service with no environment and no dotenv behind it."""
    return SettingsService(SettingsRepository(db), dotenv={})


@pytest.fixture
def budget() -> RateLimitBudget:
    """A budget of this test's own. The real one is process-wide on purpose."""
    return RateLimitBudget(retries=2)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def service(
    settings: SettingsService, provider: FakeProvider, budget: RateLimitBudget
) -> LlmService:
    """A service whose provider factory always hands back the fake."""
    return LlmService(
        settings,
        budget=budget,
        mode_memory=ModeMemory(),
        provider_factory=lambda _config, _name: provider,
    )


@pytest.fixture
def prompts_repo(db: Database) -> PromptsRepository:
    return PromptsRepository(db)


def api_error(status: int | None, message: str = "no", body: str = "") -> LlmApiError:
    """Shorthand for a provider refusal in a script."""
    return LlmApiError(message, status=status, body=body or message)


async def set_settings(app: FastAPI, **values: Any) -> None:
    """Write registry overrides through the real service, as a PATCH would."""
    await app.state.settings_service.apply(values)
