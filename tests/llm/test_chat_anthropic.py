"""The Messages-API streaming turn: content blocks, and the tool_result shape.

Two things differ from chat completions and both are pinned here. Tool
arguments arrive as `input_json_delta` fragments that only parse once the block
closes, and a tool *result* is a **user** turn whose blocks carry
`tool_use_id` — which reads oddly and is not negotiable.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from gaggiclanker.llm.chat_types import (
    ChatEvent,
    ChatMessage,
    ChatRequest,
    ChatToolCall,
    ChatToolResult,
)
from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.providers.anthropic import AnthropicProvider


def sse(*events: tuple[str, dict[str, Any]]) -> bytes:
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    ).encode()


def message_start(input_tokens: int = 25) -> tuple[str, dict[str, Any]]:
    return (
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
                "usage": {"input_tokens": input_tokens, "output_tokens": 0},
            },
        },
    )


def block_start(block: dict[str, Any], index: int = 0) -> tuple[str, dict[str, Any]]:
    return (
        "content_block_start",
        {"type": "content_block_start", "index": index, "content_block": block},
    )


def block_stop(index: int = 0) -> tuple[str, dict[str, Any]]:
    return ("content_block_stop", {"type": "content_block_stop", "index": index})


def text_delta(text: str, index: int = 0) -> tuple[str, dict[str, Any]]:
    return (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        },
    )


def json_delta(fragment: str, index: int = 0) -> tuple[str, dict[str, Any]]:
    """A tool argument arriving as a string that only parses once the block closes."""
    return (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": fragment},
        },
    )


def message_delta(stop: str, output_tokens: int = 9) -> tuple[str, dict[str, Any]]:
    return (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
    )


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


def provider(recorder: Recorder) -> AnthropicProvider:
    return AnthropicProvider(api_key="sk-ant-test", http_client=recorder.client())


def request(**kwargs: Any) -> ChatRequest:
    base = {
        "messages": [ChatMessage(role="user", content="hello")],
        "system": "you are a barista",
        "model": "claude-test",
        "timeout_s": 5.0,
    }
    return ChatRequest(**{**base, **kwargs})


async def test_text_blocks_stream_as_deltas() -> None:
    recorder = Recorder(
        sse(
            message_start(),
            block_start({"type": "text", "text": ""}),
            text_delta("Grind "),
            text_delta("finer."),
            block_stop(),
            message_delta("end_turn"),
            ("message_stop", {"type": "message_stop"}),
        )
    )
    events: list[ChatEvent] = []

    turn = await provider(recorder).chat(request(), events.append)

    assert [event.data["text"] for event in events] == ["Grind ", "finer."]
    assert turn.text == "Grind finer."
    assert turn.stop_reason == "end_turn"
    assert turn.usage.prompt_tokens == 25
    assert turn.usage.completion_tokens == 9


async def test_a_tool_use_block_is_assembled_from_input_json_deltas() -> None:
    recorder = Recorder(
        sse(
            message_start(),
            block_start({"type": "tool_use", "id": "toolu_1", "name": "get_shot", "input": {}}),
            json_delta('{"shot_'),
            json_delta('id": 129}'),
            block_stop(),
            message_delta("tool_use"),
            ("message_stop", {"type": "message_stop"}),
        )
    )

    turn = await provider(recorder).chat(request(), lambda _event: None)

    assert turn.tool_calls == [
        ChatToolCall(id="toolu_1", name="get_shot", arguments={"shot_id": 129})
    ]
    assert turn.stop_reason == "tool_use"


async def test_the_system_prompt_is_a_cached_block_not_a_message() -> None:
    recorder = Recorder(sse(message_start(), message_delta("end_turn")))

    await provider(recorder).chat(request(), lambda _event: None)

    body = recorder.body_json
    assert body["system"][0]["text"] == "you are a barista"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert all(message["role"] != "system" for message in body["messages"])
    assert body["stream"] is True


async def test_a_tool_result_is_a_user_turn_carrying_tool_use_ids() -> None:
    recorder = Recorder(sse(message_start(), message_delta("end_turn")))
    history = [
        ChatMessage(role="user", content="q"),
        ChatMessage(
            role="assistant",
            content="looking",
            tool_calls=[ChatToolCall(id="toolu_1", name="get_shot", arguments={"shot_id": 1})],
        ),
        ChatMessage(
            role="tool",
            tool_results=[ChatToolResult(id="toolu_1", name="get_shot", content="{}", ok=False)],
        ),
    ]

    await provider(recorder).chat(request(messages=history), lambda _event: None)

    messages = recorder.body_json["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"][0] == {"type": "text", "text": "looking"}
    assert messages[1]["content"][1]["type"] == "tool_use"
    assert messages[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "{}",
        "is_error": True,
    }


async def test_tools_are_forwarded_in_the_messages_api_shape() -> None:
    recorder = Recorder(sse(message_start(), message_delta("end_turn")))
    tools = [{"name": "get_shot", "description": "one shot", "input_schema": {"type": "object"}}]

    await provider(recorder).chat(request(tools=tools), lambda _event: None)

    assert recorder.body_json["tools"] == tools


async def test_a_refusal_carries_its_status() -> None:
    class Failing:
        def __call__(self, _request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(401, json={"error": {"message": "bad key"}})

        def client(self) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(transport=httpx2.MockTransport(self))

    failing = Failing()
    service = AnthropicProvider(api_key="sk-ant-test", http_client=failing.client())

    with pytest.raises(LlmApiError) as caught:
        await service.chat(request(), lambda _event: None)

    assert caught.value.status == 401
