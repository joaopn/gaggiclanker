"""The Messages API: system hoisted and cached, structured output, usage."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.providers.anthropic import AnthropicProvider
from gaggiclanker.llm.providers.base import ProviderCall
from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import Err, LlmMessage, LlmRequest
from gaggiclanker.settings_service import SettingsService
from tests.llm.conftest import Answer


def message(text: str, usage: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 30, "output_tokens": 12},
    }


class Recorder:
    def __init__(self, *responses: httpx2.Response) -> None:
        self.requests: list[httpx2.Request] = []
        self.responses = list(responses)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))


def call(mode: str = "json_schema") -> ProviderCall:
    return ProviderCall(
        mode=mode,  # type: ignore[arg-type]
        model="claude-haiku-4-5",
        messages=[
            LlmMessage(role="system", content="you judge espresso"),
            LlmMessage(role="user", content="how was it?"),
        ],
        output_model=Answer,
        timeout_s=5.0,
    )


async def test_the_system_message_is_hoisted_and_marked_for_caching() -> None:
    recorder = Recorder(httpx2.Response(200, json=message('{"verdict": "ok", "score": 1}')))
    provider = AnthropicProvider(api_key="sk-ant-test", http_client=recorder.client())

    await provider.complete(call())

    body = recorder.bodies[0]
    # A system message left in the list is a 400.
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert body["system"][0]["text"] == "you judge espresso"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


async def test_structured_output_sends_the_schema_and_parses_the_reply() -> None:
    recorder = Recorder(httpx2.Response(200, json=message('{"verdict": "sweet", "score": 8}')))
    provider = AnthropicProvider(api_key="sk-ant-test", http_client=recorder.client())

    reply = await provider.complete(call("json_schema"))

    body = recorder.bodies[0]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "verdict" in body["output_config"]["format"]["schema"]["properties"]
    # The SDK parsed it; the service still validates it against the model.
    assert reply.data == Answer(verdict="sweet", score=8)


async def test_text_mode_asks_for_the_schema_in_words() -> None:
    """There is no json_object equivalent, so the instruction carries it all."""
    recorder = Recorder(httpx2.Response(200, json=message('{"verdict": "ok", "score": 1}')))
    provider = AnthropicProvider(api_key="sk-ant-test", http_client=recorder.client())

    reply = await provider.complete(call("text"))

    system = recorder.bodies[0]["system"][0]["text"]
    assert "JSON Schema" in system
    assert "output_config" not in recorder.bodies[0]
    assert reply.data is None
    assert reply.text == '{"verdict": "ok", "score": 1}'


async def test_cache_traffic_counts_as_input() -> None:
    """Reporting only input_tokens makes a 30k cached prompt look like nothing."""
    recorder = Recorder(
        httpx2.Response(
            200,
            json=message(
                '{"verdict": "ok", "score": 1}',
                usage={
                    "input_tokens": 20,
                    "cache_creation_input_tokens": 26700,
                    "cache_read_input_tokens": 25900,
                    "output_tokens": 140,
                },
            ),
        )
    )
    provider = AnthropicProvider(api_key="sk-ant-test", http_client=recorder.client())

    reply = await provider.complete(call())

    assert reply.usage.prompt_tokens == 20 + 26700 + 25900
    assert reply.usage.completion_tokens == 140


async def test_a_status_error_keeps_its_status() -> None:
    recorder = Recorder(
        httpx2.Response(
            429,
            json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
        )
    )
    provider = AnthropicProvider(api_key="sk-ant-test", http_client=recorder.client())

    with pytest.raises(LlmApiError) as caught:
        await provider.complete(call())

    assert caught.value.status == 429


async def test_no_key_is_refused_without_a_request() -> None:
    recorder = Recorder(httpx2.Response(500, json={}))
    provider = AnthropicProvider(http_client=recorder.client())

    check = await provider.validate_credentials()

    assert check.ok is False
    assert recorder.requests == []


async def test_a_keyless_anthropic_call_never_leaves_the_box(
    settings: SettingsService, budget: RateLimitBudget
) -> None:
    """And never reaches the SDK either, which raises a TypeError with no key."""
    recorder = Recorder(httpx2.Response(200, json=message("{}")))
    provider = AnthropicProvider(api_key="", http_client=recorder.client())
    service = LlmService(
        settings, budget=budget, mode_memory=ModeMemory(), provider_factory=lambda *_: provider
    )

    result = await service.call_json(
        LlmRequest(
            messages=[LlmMessage(role="user", content="hi")],
            output_model=Answer,
            model="claude-haiku-4-5",
        )
    )

    assert isinstance(result, Err)
    assert result.code == "auth"
    assert recorder.requests == []


async def test_an_ambient_key_is_never_what_the_client_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK reads the environment when it is given no key; this provider always gives one."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-bearer")

    unconfigured = AnthropicProvider(api_key="")
    assert unconfigured.missing_credential() is not None
    assert unconfigured.client.api_key == ""
    assert unconfigured.client.auth_token is None

    recorder = Recorder(httpx2.Response(200, json=message('{"verdict": "fine", "score": 7}')))
    configured = AnthropicProvider(api_key="sk-ant-stored", http_client=recorder.client())
    await configured.complete(call())
    sent = recorder.requests[0].headers
    assert sent["x-api-key"] == "sk-ant-stored"
    assert "authorization" not in sent
