"""The chat-completions family: request shape per preset and mode, and errors.

Mocked at the transport the SDK actually uses. ``respx`` cannot do it: both
SDKs are built on ``httpx2`` (the httpx 2.x distribution) while respx patches
``httpx`` 0.28, so its patches never fire and every request would go to the real
internet. ``httpx2.MockTransport`` injected through the SDK's own
``http_client`` seam is the equivalent, and it lets a test assert on the parsed
request object rather than on a URL pattern.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.providers.base import ProviderCall
from gaggiclanker.llm.providers.openai_compatible import (
    OpenAiCompatibleProvider,
    normalize_base_url,
)
from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import Err, LlmMessage, LlmRequest
from gaggiclanker.settings_service import SettingsService
from tests.llm.conftest import Answer


def completion(content: str, usage: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage or {"prompt_tokens": 42, "completion_tokens": 8},
    }


class Recorder:
    """Captures every request and replies with a scripted response."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self.requests: list[httpx2.Request] = []
        self.responses = list(responses)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return self.responses[index]

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))


def call(mode: str = "json_schema") -> ProviderCall:
    return ProviderCall(
        mode=mode,  # type: ignore[arg-type]
        model="test-model",
        messages=[LlmMessage(role="user", content="hello")],
        output_model=Answer,
        timeout_s=5.0,
    )


# -- presets --------------------------------------------------------------


@pytest.mark.parametrize(
    ("preset", "url", "auth"),
    [
        ("openrouter", "https://openrouter.ai/api/v1/chat/completions", True),
        ("openai", "https://api.openai.com/v1/chat/completions", True),
        ("ollama", "http://localhost:11434/v1/chat/completions", False),
        ("lmstudio", "http://localhost:1234/v1/chat/completions", False),
    ],
)
async def test_each_preset_posts_where_it_should(preset: str, url: str, auth: bool) -> None:
    recorder = Recorder(httpx2.Response(200, json=completion('{"verdict": "ok", "score": 1}')))
    provider = OpenAiCompatibleProvider(
        preset=preset, api_key="sk-test" if auth else "", http_client=recorder.client()
    )

    await provider.complete(call())

    request = recorder.requests[0]
    assert str(request.url) == url
    # A keyless server still gets an Authorization header - the SDK refuses to
    # construct without a key at all - but it carries the placeholder, never a
    # credential borrowed from another provider.
    bearer = request.headers.get("authorization", "")
    assert ("sk-test" in bearer) is auth


async def test_openrouter_sends_its_attribution_headers() -> None:
    recorder = Recorder(httpx2.Response(200, json=completion('{"verdict": "ok", "score": 1}')))
    provider = OpenAiCompatibleProvider(
        preset="openrouter", api_key="sk-test", http_client=recorder.client()
    )

    await provider.complete(call())

    assert recorder.requests[0].headers["x-title"] == "gaggiclanker"


async def test_a_hosted_preset_ignores_a_supplied_base_url() -> None:
    """Redirecting the endpoint while still sending the key exfiltrates it."""
    provider = OpenAiCompatibleProvider(
        preset="openrouter", base_url="http://attacker.test/v1", api_key="sk-test"
    )

    assert provider.base_url == "https://openrouter.ai/api/v1"
    await provider.aclose()


async def test_a_self_hosted_preset_honours_one() -> None:
    provider = OpenAiCompatibleProvider(preset="ollama", base_url="http://nas.local:11434")

    assert provider.base_url == "http://nas.local:11434/v1"
    await provider.aclose()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://host:1234", "http://host:1234/v1"),
        ("http://host:1234/", "http://host:1234/v1"),
        ("http://host:1234/v1", "http://host:1234/v1"),
        # People paste the endpoint out of a curl example as often as the base.
        ("http://host:1234/v1/chat/completions", "http://host:1234/v1"),
        ("https://gateway.test/openai/v1", "https://gateway.test/openai/v1"),
        ("", ""),
    ],
)
def test_base_url_normalisation(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


# -- modes ----------------------------------------------------------------


async def test_json_schema_mode_sends_a_strict_closed_schema() -> None:
    recorder = Recorder(httpx2.Response(200, json=completion('{"verdict": "ok", "score": 1}')))
    provider = OpenAiCompatibleProvider(
        preset="openai", api_key="sk-test", http_client=recorder.client()
    )

    await provider.complete(call("json_schema"))

    fmt = recorder.bodies[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "Answer"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False
    assert set(fmt["json_schema"]["schema"]["required"]) == {"verdict", "score"}


async def test_json_object_mode_sends_only_the_type() -> None:
    recorder = Recorder(httpx2.Response(200, json=completion('{"verdict": "ok", "score": 1}')))
    provider = OpenAiCompatibleProvider(
        preset="openai", api_key="sk-test", http_client=recorder.client()
    )

    await provider.complete(call("json_object"))

    assert recorder.bodies[0]["response_format"] == {"type": "json_object"}


async def test_text_mode_sends_no_response_format_at_all() -> None:
    """It is the fallback, so it must be the plainest request possible."""
    recorder = Recorder(httpx2.Response(200, json=completion('{"verdict": "ok", "score": 1}')))
    provider = OpenAiCompatibleProvider(
        preset="openai", api_key="sk-test", http_client=recorder.client()
    )

    await provider.complete(call("text"))

    assert "response_format" not in recorder.bodies[0]


# -- failures and usage ---------------------------------------------------


async def test_a_status_error_carries_the_status_and_the_body() -> None:
    body = {"error": {"message": "response_format is not supported", "type": "invalid_request"}}
    recorder = Recorder(httpx2.Response(400, json=body))
    provider = OpenAiCompatibleProvider(
        preset="openai", api_key="sk-test", http_client=recorder.client()
    )

    with pytest.raises(LlmApiError) as caught:
        await provider.complete(call())

    assert caught.value.status == 400
    assert "response_format" in caught.value.message
    assert "response_format" in caught.value.body


async def test_a_two_hundred_with_no_content_asks_to_be_retried() -> None:
    recorder = Recorder(httpx2.Response(200, json=completion("")))
    provider = OpenAiCompatibleProvider(
        preset="openai", api_key="sk-test", http_client=recorder.client()
    )

    with pytest.raises(LlmApiError, match="parse"):
        await provider.complete(call())


async def test_usage_is_read_under_either_naming() -> None:
    recorder = Recorder(
        httpx2.Response(
            200,
            json=completion(
                '{"verdict": "ok", "score": 1}',
                usage={"input_tokens": 90, "output_tokens": 5},
            ),
        )
    )
    provider = OpenAiCompatibleProvider(
        preset="openai", api_key="sk-test", http_client=recorder.client()
    )

    reply = await provider.complete(call())

    assert reply.usage.prompt_tokens == 90
    assert reply.usage.completion_tokens == 5


async def test_listing_models_validates_the_credentials() -> None:
    recorder = Recorder(
        httpx2.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "b-model", "object": "model", "created": 0, "owned_by": "x"},
                    {"id": "a-model", "object": "model", "created": 0, "owned_by": "x"},
                ],
            },
        )
    )
    provider = OpenAiCompatibleProvider(
        preset="openai", api_key="sk-test", http_client=recorder.client()
    )

    check = await provider.validate_credentials()

    assert check.ok
    assert check.models == ["a-model", "b-model"]


async def test_a_missing_key_is_refused_without_a_request() -> None:
    recorder = Recorder(httpx2.Response(500, json={}))
    provider = OpenAiCompatibleProvider(preset="openai", http_client=recorder.client())

    check = await provider.validate_credentials()

    assert check.ok is False
    assert recorder.requests == []


# -- the credential gate, end to end --------------------------------------


async def test_a_keyless_openrouter_call_never_leaves_the_box(
    settings: SettingsService, budget: RateLimitBudget
) -> None:
    """Without the gate this posts `Bearer not-required` and reports back a 401.

    The SDK refuses to construct without a key, so the placeholder is real; the
    check is what stops it being sent, and what turns "401" into a message
    naming the setting to fill in.
    """
    recorder = Recorder(httpx2.Response(200, json=completion("{}")))
    provider = OpenAiCompatibleProvider(
        preset="openrouter", api_key="", http_client=recorder.client()
    )
    service = LlmService(
        settings, budget=budget, mode_memory=ModeMemory(), provider_factory=lambda *_: provider
    )

    result = await service.call_json(
        LlmRequest(
            messages=[LlmMessage(role="user", content="hi")],
            output_model=Answer,
            model="anything",
        )
    )

    assert isinstance(result, Err)
    assert result.code == "auth"
    assert recorder.requests == []


def test_an_ambient_key_is_never_what_the_client_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK reads OPENAI_API_KEY when it is given no key; this provider always gives one."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient")
    monkeypatch.setenv("GAGGICLANKER_LLM_API_KEY", "sk-ambient-too")

    provider = OpenAiCompatibleProvider(preset="openrouter", api_key="")

    assert provider.missing_credential() is not None
    assert provider.client.api_key not in ("sk-ambient", "sk-ambient-too")
