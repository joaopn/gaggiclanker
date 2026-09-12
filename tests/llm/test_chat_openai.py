"""The chat-completions streaming turn: text deltas, and tool calls in pieces.

The awkward part of this API is that a tool call does not arrive whole. The
first delta carries an index, an id and a name; every later delta carries the
same index and another fragment of `arguments` as a string. These tests script
exactly that shape, because a provider that accumulates by id or parses early
looks fine against a single-chunk fixture and breaks against a real one.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from gaggiclanker.llm.chat_types import ChatEvent, ChatMessage, ChatRequest, ChatToolCall
from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.providers.openai_compatible import OpenAiCompatibleProvider


def sse(*chunks: dict[str, Any]) -> bytes:
    """The wire format the SDK's streaming client reads."""
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return (body + "data: [DONE]\n\n").encode()


def chunk(**delta: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }


def final(reason: str = "stop", usage: dict[str, int] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


class Recorder:
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


def provider(recorder: Recorder) -> OpenAiCompatibleProvider:
    return OpenAiCompatibleProvider(
        preset="openai", api_key="sk-test", http_client=recorder.client()
    )


def request(**kwargs: Any) -> ChatRequest:
    base = {
        "messages": [ChatMessage(role="user", content="hello")],
        "system": "you are a barista",
        "model": "test-model",
        "timeout_s": 5.0,
    }
    return ChatRequest(**{**base, **kwargs})


async def test_text_arrives_as_deltas_and_is_joined_into_the_turn() -> None:
    recorder = Recorder(
        sse(
            chunk(content="Pull "),
            chunk(content="two "),
            chunk(content="more."),
            final(usage={"prompt_tokens": 30, "completion_tokens": 6}),
        )
    )
    events: list[ChatEvent] = []

    turn = await provider(recorder).chat(request(), events.append)

    assert [event.data["text"] for event in events] == ["Pull ", "two ", "more."]
    assert turn.text == "Pull two more."
    assert turn.usage.prompt_tokens == 30
    assert turn.stop_reason == "stop"


async def test_a_tool_call_split_across_deltas_is_reassembled() -> None:
    recorder = Recorder(
        sse(
            chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "query_shots", "arguments": ""},
                    }
                ]
            ),
            chunk(tool_calls=[{"index": 0, "function": {"arguments": '{"sql": "SELECT '}}]),
            chunk(tool_calls=[{"index": 0, "function": {"arguments": '1", "limit": 5}'}}]),
            final("tool_calls"),
        )
    )

    turn = await provider(recorder).chat(request(), lambda _event: None)

    assert turn.tool_calls == [
        ChatToolCall(id="call_abc", name="query_shots", arguments={"sql": "SELECT 1", "limit": 5})
    ]


async def test_two_tool_calls_are_kept_apart_by_index() -> None:
    """The later deltas carry no id at all, so index is the only key there is."""
    recorder = Recorder(
        sse(
            chunk(
                tool_calls=[
                    {"index": 0, "id": "a", "function": {"name": "get_shot", "arguments": "{"}},
                    {"index": 1, "id": "b", "function": {"name": "get_set", "arguments": "{"}},
                ]
            ),
            chunk(tool_calls=[{"index": 1, "function": {"arguments": '"set_id": 2}'}}]),
            chunk(tool_calls=[{"index": 0, "function": {"arguments": '"shot_id": 1}'}}]),
            final("tool_calls"),
        )
    )

    turn = await provider(recorder).chat(request(), lambda _event: None)

    assert [(call.name, call.arguments) for call in turn.tool_calls] == [
        ("get_shot", {"shot_id": 1}),
        ("get_set", {"set_id": 2}),
    ]


async def test_malformed_arguments_become_an_empty_object_not_an_exception() -> None:
    """The dispatcher's own validation error is what the model can act on."""
    recorder = Recorder(
        sse(
            chunk(
                tool_calls=[
                    {"index": 0, "id": "a", "function": {"name": "get_shot", "arguments": "{oops"}}
                ]
            ),
            final("tool_calls"),
        )
    )

    turn = await provider(recorder).chat(request(), lambda _event: None)

    assert turn.tool_calls[0].arguments == {}


async def test_the_system_prompt_is_a_message_and_tools_are_sent() -> None:
    recorder = Recorder(sse(chunk(content="hi"), final()))
    tools = [{"type": "function", "function": {"name": "get_shot", "parameters": {}}}]

    await provider(recorder).chat(request(tools=tools), lambda _event: None)

    body = recorder.body_json
    assert body["messages"][0] == {"role": "system", "content": "you are a barista"}
    assert body["stream"] is True
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"


async def test_a_tool_result_is_one_message_per_call_id() -> None:
    """One message carrying several results is a 400, and so is a mismatched id."""
    from gaggiclanker.llm.chat_types import ChatToolResult

    recorder = Recorder(sse(chunk(content="hi"), final()))
    history = [
        ChatMessage(role="user", content="q"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ChatToolCall(id="a", name="get_shot", arguments={"shot_id": 1})],
        ),
        ChatMessage(
            role="tool",
            tool_results=[
                ChatToolResult(id="a", name="get_shot", content="{}"),
                ChatToolResult(id="b", name="get_set", content="{}"),
            ],
        ),
    ]

    await provider(recorder).chat(request(messages=history), lambda _event: None)

    rendered = recorder.body_json["messages"]
    assert [message["role"] for message in rendered] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    assert rendered[3]["tool_call_id"] == "a"
    assert rendered[2]["tool_calls"][0]["function"]["arguments"] == '{"shot_id": 1}'


async def test_a_provider_refusal_becomes_an_LlmApiError_with_its_status() -> None:
    class Failing:
        def __call__(self, _request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(429, json={"error": {"message": "slow down"}})

        def client(self) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(transport=httpx2.MockTransport(self))

    failing = Failing()
    service = OpenAiCompatibleProvider(
        preset="openai", api_key="sk-test", http_client=failing.client()
    )

    with pytest.raises(LlmApiError) as caught:
        await service.chat(request(), lambda _event: None)

    assert caught.value.status == 429
    assert "slow down" in str(caught.value)
