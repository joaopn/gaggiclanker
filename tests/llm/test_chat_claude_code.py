"""The CLI chat turn: argv, environment, and translating ``stream-json``.

This provider is the odd one out — the tool loop runs *inside* Claude Code
against our own MCP server — so the two things worth testing are the command
line that arranges that and the translation of what comes back. Both run
against an injected stream, so the whole file works with no binary and no
subscription.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import pytest

from gaggiclanker.llm.chat_types import ChatEvent, ChatMessage, ChatRequest, ChatToolResult
from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.providers.claude_code import (
    MCP_SERVER_NAME,
    MCP_TOOL_GLOB,
    ClaudeCodeProvider,
    build_mcp_config,
    format_chat_prompt,
)


class RecordedStream:
    """Stands in for the child, and remembers how it was launched."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.argv: list[str] = []
        self.env: dict[str, str] = {}
        self.cwd = ""
        self.stdin: str | None = None
        self.closed = False

    async def __call__(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        cwd: str,
        stdin: str | None,
        timeout_s: float,
    ) -> AsyncIterator[str]:
        self.argv = list(argv)
        self.env = dict(env)
        self.cwd = cwd
        self.stdin = stdin
        try:
            for line in self.lines:
                yield line
        finally:
            # The real generator kills the child here; a test asserting cancel
            # needs to see that this ran.
            self.closed = True


def line(payload: dict[str, Any]) -> str:
    return json.dumps(payload) + "\n"


def stream_event(text: str) -> str:
    return line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        }
    )


def assistant(*blocks: dict[str, Any], usage: dict[str, int] | None = None) -> str:
    return line(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": list(blocks),
                "usage": usage or {"input_tokens": 100, "output_tokens": 20},
            },
        }
    )


def tool_result(call_id: str, content: str, *, is_error: bool = False) -> str:
    return line(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": [{"type": "text", "text": content}],
                        "is_error": is_error,
                    }
                ],
            },
        }
    )


def result(text: str = "Grind two clicks finer.", **overrides: Any) -> str:
    payload: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "usage": {"input_tokens": 1200, "cache_read_input_tokens": 30_000, "output_tokens": 80},
    }
    payload.update(overrides)
    return line(payload)


TRANSCRIPT = [
    line({"type": "system", "subtype": "init", "tools": ["mcp__gaggiclanker__get_shot"]}),
    "a warning the CLI printed before the stream\n",
    stream_event("Let me "),
    stream_event("look."),
    assistant(
        {"type": "text", "text": "Let me look."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "mcp__gaggiclanker__get_shot",
            "input": {"shot_id": 129},
        },
    ),
    tool_result("toolu_1", '{"shot": {"shot_id": 129}}'),
    stream_event("Grind two clicks finer."),
    result(),
]


def provider(stream: RecordedStream, **kwargs: Any) -> ClaudeCodeProvider:
    return ClaudeCodeProvider(
        binary="claude",
        oauth_token="sk-ant-oat-test",
        stream_spawn=stream,
        data_dir=kwargs.pop("data_dir", "/data"),
        **kwargs,
    )


def request(**kwargs: Any) -> ChatRequest:
    base = {
        "messages": [ChatMessage(role="user", content="why is this sour?")],
        "system": "you are a barista",
        "model": "haiku",
        "timeout_s": 30.0,
    }
    return ChatRequest(**{**base, **kwargs})


# -- the command line ------------------------------------------------------


async def test_the_argv_points_the_cli_at_our_mcp_server_and_nothing_else() -> None:
    stream = RecordedStream(TRANSCRIPT)

    await provider(stream).chat(request(), lambda _event: None)

    argv = stream.argv
    assert argv[0] == "claude"
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "stream-json"
    # stream-json in print mode is refused without it.
    assert "--verbose" in argv
    # Without this the answer arrives in one lump at the end.
    assert "--include-partial-messages" in argv
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--allowedTools") + 1] == MCP_TOOL_GLOB
    # No built-in tools, and no ambient settings or CLAUDE.md.
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert argv[argv.index("--system-prompt") + 1] == "you are a barista"
    assert argv[argv.index("--model") + 1] == "haiku"

    config = json.loads(argv[argv.index("--mcp-config") + 1])
    server = config["mcpServers"][MCP_SERVER_NAME]
    assert server["args"] == ["-m", "gaggiclanker", "mcp"]
    assert server["env"]["DATA_DIR"] == "/data"


async def test_every_variadic_flag_is_followed_by_another_flag() -> None:
    """`--tools ""` swallows the next bare word, so nothing positional may follow."""
    stream = RecordedStream(TRANSCRIPT)

    await provider(stream).chat(request(), lambda _event: None)

    argv = stream.argv
    for flag in ("--tools", "--setting-sources", "--allowedTools"):
        after = argv[argv.index(flag) + 2 :]
        assert not after or after[0].startswith("-"), flag


async def test_the_prompt_goes_on_stdin_and_never_in_argv() -> None:
    stream = RecordedStream(TRANSCRIPT)

    await provider(stream).chat(request(), lambda _event: None)

    assert "why is this sour?" in (stream.stdin or "")
    assert not any("why is this sour?" in argument for argument in stream.argv)


async def test_the_environment_is_an_allow_list_with_a_scratch_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "nope")
    stream = RecordedStream(TRANSCRIPT)

    await provider(stream).chat(request(), lambda _event: None)

    assert "ANTHROPIC_API_KEY" not in stream.env
    assert "AWS_SECRET_ACCESS_KEY" not in stream.env
    assert stream.env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-test"
    assert stream.env["HOME"] == stream.cwd
    assert stream.env["HOME"] != os.environ.get("HOME")


async def test_no_token_is_refused_before_a_process_is_started() -> None:
    stream = RecordedStream(TRANSCRIPT)
    bare = ClaudeCodeProvider(oauth_token="", stream_spawn=stream, data_dir="/data")

    with pytest.raises(LlmApiError) as caught:
        await bare.chat(request(), lambda _event: None)

    assert caught.value.status == 401
    assert stream.argv == [], "the credential check has to come first"


async def test_a_provider_with_no_data_dir_gets_no_mcp_config() -> None:
    """One built outside the app cannot point the CLI at an archive."""
    stream = RecordedStream(TRANSCRIPT)

    await provider(stream, data_dir="").chat(request(), lambda _event: None)

    assert "--mcp-config" not in stream.argv
    assert "--allowedTools" not in stream.argv


# -- translating the stream ------------------------------------------------


async def test_the_transcript_becomes_deltas_calls_results_and_a_turn() -> None:
    stream = RecordedStream(TRANSCRIPT)
    events: list[ChatEvent] = []

    turn = await provider(stream).chat(request(), events.append)

    kinds = [event.kind for event in events]
    assert kinds.count("delta") == 3
    assert kinds.count("tool_call") == 1
    assert kinds.count("tool_result") == 1

    # The loop already happened inside the CLI: nothing is left for the runner
    # to dispatch, which is what ends its loop after one round.
    assert turn.tool_calls == []
    assert [call.name for call in turn.executed_tool_calls] == ["get_shot"]
    assert turn.executed_tool_results == [
        ChatToolResult(id="toolu_1", name="get_shot", content='{"shot": {"shot_id": 129}}')
    ]
    assert turn.text == "Grind two clicks finer."
    # The envelope's totals supersede the per-message sums; cache traffic is billed.
    assert turn.usage.prompt_tokens == 31_200
    assert turn.usage.completion_tokens == 80


async def test_the_mcp_namespace_is_stripped_from_tool_names() -> None:
    """The transcript, the audit table and the UI all name tools as the registry does."""
    stream = RecordedStream(TRANSCRIPT)
    events: list[ChatEvent] = []

    await provider(stream).chat(request(), events.append)

    names = [event.data["name"] for event in events if event.kind == "tool_call"]
    assert names == ["get_shot"]


async def test_unknown_and_unparseable_lines_are_ignored() -> None:
    """A CLI that prints something new must not break a chat."""
    stream = RecordedStream(
        [
            "not json at all\n",
            line({"type": "hook_event", "name": "PreToolUse"}),
            "\n",
            result("fine"),
        ]
    )

    turn = await provider(stream).chat(request(), lambda _event: None)

    assert turn.text == "fine"


async def test_an_answer_that_never_streamed_is_still_emitted_as_an_event() -> None:
    """Otherwise the bubble stays empty until the page is reloaded."""
    stream = RecordedStream([result("short answer")])
    events: list[ChatEvent] = []

    turn = await provider(stream).chat(request(), events.append)

    assert turn.text == "short answer"
    assert [event.data["text"] for event in events if event.kind == "delta"] == ["short answer"]


async def test_an_error_envelope_raises_with_its_status_not_the_exit_code() -> None:
    stream = RecordedStream([result("Invalid API key", is_error=True, api_error_status=401)])

    with pytest.raises(LlmApiError) as caught:
        await provider(stream).chat(request(), lambda _event: None)

    assert caught.value.status == 401
    assert "Invalid API key" in str(caught.value)


async def test_cancelling_stops_reading_and_closes_the_child() -> None:
    import asyncio

    cancel = asyncio.Event()
    cancel.set()
    stream = RecordedStream(TRANSCRIPT)

    turn = await provider(stream).chat(request(cancel=cancel), lambda _event: None)

    assert turn.stop_reason == "cancelled"
    assert stream.closed, "the child has to be killed, or it keeps spending the subscription"


# -- the prompt ------------------------------------------------------------


def test_the_flattened_prompt_keeps_tool_results_visible() -> None:
    """A model shown its own earlier answer with the evidence removed contradicts itself."""
    from gaggiclanker.llm.chat_types import ChatToolCall

    text = format_chat_prompt(
        [
            ChatMessage(role="user", content="why sour?"),
            ChatMessage(
                role="assistant",
                content="looking",
                tool_calls=[ChatToolCall(id="a", name="get_shot", arguments={})],
            ),
            ChatMessage(
                role="tool",
                tool_results=[ChatToolResult(id="a", name="get_shot", content='{"x": 1}')],
            ),
        ]
    )

    assert "why sour?" in text
    assert "(called: get_shot)" in text
    assert '{"x": 1}' in text


def test_the_mcp_config_carries_the_scope_when_there_is_one() -> None:
    config = json.loads(build_mcp_config(data_dir="/data", executable="/py", set_id=3))

    env = config["mcpServers"][MCP_SERVER_NAME]["env"]
    assert env["DATA_DIR"] == "/data"
    assert env["GAGGICLANKER_MCP_SET_ID"] == "3"


async def test_a_scoped_request_puts_the_set_in_the_generated_config() -> None:
    """The scope has to survive the whole way to the child's environment.

    Every other provider gets it through the tool context, because the loop
    runs here. This one's loop runs inside the CLI against our MCP server, so
    the config is the only channel there is — and without it a scoped thread's
    `get_set` with no argument answers "not scoped" on the default provider and
    correctly everywhere else.
    """
    stream = RecordedStream(TRANSCRIPT)

    await provider(stream).chat(request(set_id=7), lambda _event: None)

    config = json.loads(stream.argv[stream.argv.index("--mcp-config") + 1])
    assert config["mcpServers"][MCP_SERVER_NAME]["env"]["GAGGICLANKER_MCP_SET_ID"] == "7"


async def test_an_unscoped_request_leaves_the_scope_out() -> None:
    stream = RecordedStream(TRANSCRIPT)

    await provider(stream).chat(request(), lambda _event: None)

    config = json.loads(stream.argv[stream.argv.index("--mcp-config") + 1])
    assert "GAGGICLANKER_MCP_SET_ID" not in config["mcpServers"][MCP_SERVER_NAME]["env"]
