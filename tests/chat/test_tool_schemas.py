"""What the provider is actually sent, read off the wire.

The runner narrows the tool schemas to the conversation's scope, and the two
API providers put them in the body in two different shapes — `tools[].function`
for chat completions, `tools[].name` for the Messages API. A test that only
drives the fake provider checks the first shape and the code path that builds
it, and would not notice the Anthropic branch being built without the scope at
all; so this drives the **real** providers with their SDK transport mocked and
reads the tool names out of the JSON that was going to be sent.

Nothing here calls a provider: `httpx2.MockTransport` answers every request
with a canned stream, which is how the rest of the suite mocks these SDKs.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest

from gaggiclanker.chat.runner import ChatRunner
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import DEFAULT_PROMPTS_DIR, PromptService, seed_prompts
from gaggiclanker.llm.providers.anthropic import AnthropicProvider
from gaggiclanker.llm.providers.openai_compatible import OpenAiCompatibleProvider
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import registry
from gaggiclanker.tools.scope import GENERAL_TOOLS, SET_TOOLS
from tests.analyzer.conftest import Fixture, build_fixture


class Recorder:
    """A transport that answers with one canned stream and keeps the request."""

    def __init__(self, body: bytes) -> None:
        self.requests: list[httpx2.Request] = []
        self.body = body

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(
            200, content=self.body, headers={"content-type": "text/event-stream"}
        )

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))

    @property
    def body_json(self) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.requests[0].content)
        return parsed


#: One Messages-API turn that says a sentence and stops.
ANTHROPIC_STREAM = (
    "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n"
        for name, payload in (
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-test",
                        "content": [],
                        "stop_reason": None,
                        "usage": {"input_tokens": 10, "output_tokens": 0},
                    },
                },
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "ok"},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
    )
).encode()

#: The same, in chat-completions shape.
OPENAI_STREAM = (
    "data: "
    + json.dumps(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}],
        }
    )
    + "\n\ndata: "
    + json.dumps(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )
    + "\n\ndata: [DONE]\n\n"
).encode()


@pytest.fixture
async def archive(tmp_path: Path) -> AsyncIterator[Fixture]:
    db = Database(tmp_path / "schemas.db")
    await db.connect()
    await run_migrations(db)
    await seed_prompts(PromptsRepository(db), DEFAULT_PROMPTS_DIR)
    try:
        yield await build_fixture(db)
    finally:
        await db.close()


async def one_turn(archive: Fixture, provider: Any, *, set_id: int | None) -> None:
    """Run one turn on a thread of that kind. What was sent is on the recorder."""
    settings = SettingsService(SettingsRepository(archive.db))
    tasks = TaskRegistry()
    runner = ChatRunner(
        archive.db,
        LlmService(
            settings,
            budget=RateLimitBudget(retries=0),
            mode_memory=ModeMemory(),
            provider_factory=lambda _config, _name: provider,
        ),
        PromptService(PromptsRepository(archive.db)),
        tools=registry,
        knowledge=KnowledgeService(archive.db),
        tasks=tasks,
    )
    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite(set_id=set_id))
    assert created.thread is not None
    run, _message = await runner.send(created.thread.id, "how is it going?")
    task = tasks.get(f"chat:{run.id}")
    assert task is not None
    await task


async def test_the_anthropic_body_carries_exactly_this_conversation_s_tools(
    archive: Fixture,
) -> None:
    """The Messages API shape: `tools[].name`, and only the scope's."""
    scoped = Recorder(ANTHROPIC_STREAM)
    general = Recorder(ANTHROPIC_STREAM)

    await one_turn(
        archive,
        AnthropicProvider(api_key="sk-ant-test", http_client=scoped.client()),
        set_id=archive.set_id,
    )
    await one_turn(
        archive,
        AnthropicProvider(api_key="sk-ant-test", http_client=general.client()),
        set_id=None,
    )

    assert {tool["name"] for tool in scoped.body_json["tools"]} == SET_TOOLS
    assert {tool["name"] for tool in general.body_json["tools"]} == GENERAL_TOOLS


async def test_the_openai_body_carries_exactly_this_conversation_s_tools(
    archive: Fixture,
) -> None:
    """The chat-completions shape: `tools[].function.name`, and only the scope's."""
    scoped = Recorder(OPENAI_STREAM)
    general = Recorder(OPENAI_STREAM)

    await one_turn(
        archive,
        OpenAiCompatibleProvider(preset="openai", api_key="sk-test", http_client=scoped.client()),
        set_id=archive.set_id,
    )
    await one_turn(
        archive,
        OpenAiCompatibleProvider(preset="openai", api_key="sk-test", http_client=general.client()),
        set_id=None,
    )

    assert {tool["function"]["name"] for tool in scoped.body_json["tools"]} == SET_TOOLS
    assert {tool["function"]["name"] for tool in general.body_json["tools"]} == GENERAL_TOOLS
