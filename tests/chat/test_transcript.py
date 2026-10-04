"""The downloadable message transcript: what is in it, and what must never be.

The exclusions are the point. The system prompt and the context injected into
it each turn are never stored, so the transcript (built from stored rows only)
cannot carry them; the first test proves it against the real opening context of
a real run, through the route, rather than against a list of phrases. The rest
pin the tool-result rule, the JSON shape, the file name and the route's path.
"""

from __future__ import annotations

import asyncio
import json
import types
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.chat.context import opening_context
from gaggiclanker.chat.runner import run_task_name
from gaggiclanker.chat.transcript import (
    SHOT_RENDERING_TOOLS,
    build_transcript,
    render_transcript,
    transcript_filename,
)
from gaggiclanker.db.repos.chat import (
    ChatMessageRow,
    ChatMessageWrite,
    ChatRepository,
    ChatRunRow,
    ChatThreadRow,
    ChatThreadWrite,
)
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.chat_types import ChatToolCall, ChatTurn
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings import EnvSettings
from gaggiclanker.tools.registry import registry
from gaggiclanker.tools.scope import ToolScope
from tests.conftest import running_app
from tests.llm.conftest import FakeProvider
from tests.review.conftest import Fixture, build_fixture

GOLDEN = Path(__file__).resolve().parent / "golden" / "chat-transcript.json"
EXPORTED = "2026-09-29T10:00:00Z"

#: A shot rendering as a tool result: long, several lines, easy to spot.
SHOT_BODY = "SHOT-BODY-MARKER\n" + "\n".join(f"pressure {n} bar at {n}s" for n in range(200))
REFUSAL = "That shot is not in this Set. Ask about one of the Set's own shots."
KNOWLEDGE = "KNOWLEDGE-MARKER: Sour and fast usually means grind finer."
T = "2026-09-29T02:{minute:02d}:{second:02d}.658Z"


def at(minute: int, second: int = 36) -> str:
    return T.format(minute=minute, second=second)


def _thread(title: str = "Dialling in", set_name: str | None = "Kenya", label: str = "v1.1") -> Any:
    return ChatThreadRow(
        id=7,
        title=title,
        set_id=1 if set_name else None,
        set_name=set_name,
        set_version_id=2 if set_name else None,
        set_version_label=label if set_name else None,
        created_at=at(30, 1),
    )


def _messages() -> list[ChatMessageRow]:
    def row(id_: int, role: str, when: str, **fields: Any) -> ChatMessageRow:
        return ChatMessageRow(id=id_, thread_id=7, role=role, created_at=when, **fields)

    return [
        row(1, "user", at(31), run_id=1, content="How was the last shot?"),
        row(
            2,
            "assistant",
            at(32),
            run_id=1,
            content="Let me look.",
            tool_calls=[
                {"id": "c1", "name": "get_shot", "arguments": {"shot_id": 41}},
                {"id": "c2", "name": "get_shot", "arguments": {"shot_id": 99}},
                {
                    "id": "c3",
                    "name": "search_knowledge",
                    "arguments": {"query": "use ```code``` fences"},
                },
            ],
        ),
        row(
            3,
            "tool",
            at(32, 40),
            run_id=1,
            tool_results=[
                {"id": "c1", "name": "get_shot", "content": SHOT_BODY, "ok": True},
                {"id": "c2", "name": "get_shot", "content": REFUSAL, "ok": False},
                {"id": "c3", "name": "search_knowledge", "content": KNOWLEDGE, "ok": True},
            ],
        ),
        row(
            4,
            "assistant",
            at(33),
            run_id=1,
            content="## Summary\n\nIt ran fast; grind finer.\n\n```\nnot closed",
        ),
        row(5, "user", at(40), run_id=2, content="And the next?"),
    ]


def _runs() -> list[ChatRunRow]:
    return [
        ChatRunRow(
            id=1,
            thread_id=7,
            status="ok",
            provider="anthropic",
            model="claude-x",
            usage={
                "prompt_tokens": 1200,
                "completion_tokens": 85,
                "total_tokens": 1285,
                "context_tokens": 700,
                "cache_read": 900,
                "cache_write": 100,
                "fresh": 200,
                "requests": 2,
                "per_request": [{"context": 500}, {"context": 700}],
                "cost_usd": 0.01,
            },
            started_at=at(31),
        ),
        ChatRunRow(
            id=2,
            thread_id=7,
            status="failed",
            provider="anthropic",
            model="claude-x",
            error="provider said 529 overloaded",
            started_at=at(40),
        ),
    ]


@pytest.fixture
async def chat_app(
    env: EnvSettings,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture]]:
    """The real app with the runner's provider replaced by a scripted one."""
    async with running_app(env) as (app, client):
        provider = FakeProvider(chat_script=[ChatTurn(text="Pull two more.")])
        fixture = await build_fixture(app.state.db)
        app.state.chat.llm = LlmService(
            app.state.settings_service,
            budget=RateLimitBudget(retries=0),
            mode_memory=ModeMemory(),
            provider_factory=lambda _config, _name: provider,
        )
        yield app, client, provider, fixture


async def test_the_transcript_carries_none_of_the_injected_context_or_the_system_prompt(
    chat_app: tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture],
) -> None:
    app, client, provider, fixture = chat_app
    provider.chat_script = [
        ChatTurn(
            tool_calls=[
                ChatToolCall(id="c1", name="get_shot", arguments={"shot_id": fixture.shots[0]})
            ],
            stop_reason="tool_use",
        ),
        ChatTurn(text="Looks fine."),
    ]
    created = await client.post("/api/chat/threads", json={"set_id": fixture.set_id})
    thread_id = created.json()["data"]["id"]
    sent = await client.post(f"/api/chat/threads/{thread_id}/messages", json={"message": "how?"})
    task = app.state.tasks.get(run_task_name(sent.json()["data"]["run"]["id"]))
    assert task is not None
    await asyncio.wait([task], timeout=10)

    system = provider.chat_calls[0].system
    context = await opening_context(fixture.db, await ToolScope.resolve(fixture.db, fixture.set_id))
    # Guard the test itself: the context really has content, and the system
    # prompt really contains it, so "absent from the transcript" means something.
    interesting = [line.strip() for line in context.splitlines() if len(line.strip()) > 25]
    assert len(interesting) > 5
    assert all(line in system for line in interesting)
    forbidden = interesting + [
        line.strip() for line in system.splitlines() if len(line.strip()) > 25
    ]

    repo = ChatRepository(fixture.db)
    row = await repo.get_thread(thread_id)
    assert row is not None
    built = render_transcript(
        build_transcript(
            row, await repo.messages(thread_id), await repo.runs(thread_id), exported_at=EXPORTED
        )
    )
    # And through the route, the code that ships.
    response = await client.get(f"/api/chat/threads/{thread_id}/transcript")
    assert response.status_code == 200
    for text in (built, response.text):
        for line in forbidden:
            assert line not in text, line
        # The user's own words are in it, though: it is a transcript, not an empty file.
        assert "how?" in text


# -- tool results and calls ------------------------------------------------


def _messages_with_results(results: list[dict[str, Any]]) -> list[ChatMessageRow]:
    messages = _messages()
    messages[2].tool_results = results
    return messages


def _built(messages: list[ChatMessageRow] | None = None) -> dict[str, Any]:
    transcript = build_transcript(_thread(), messages or _messages(), _runs(), exported_at=EXPORTED)
    return json.loads(render_transcript(transcript))  # type: ignore[no-any-return]


def _results(messages: list[ChatMessageRow] | None = None) -> list[dict[str, Any]]:
    return _built(messages)["messages"][2]["tool_results"]  # type: ignore[no-any-return]


@pytest.mark.parametrize("tool", sorted(SHOT_RENDERING_TOOLS))
def test_a_shot_rendering_result_is_left_out_and_its_size_is_kept(tool: str) -> None:
    messages = _messages_with_results(
        [{"id": "c1", "name": tool, "content": SHOT_BODY, "ok": True}]
    )

    text = json.dumps(_built(messages))
    assert "SHOT-BODY-MARKER" not in text
    assert "pressure 100 bar" not in text
    assert _results(messages) == [
        {"id": "c1", "name": tool, "ok": True, "characters": len(SHOT_BODY), "content": None}
    ]


@pytest.mark.parametrize("name", ["", "not_a_tool"])
def test_a_success_that_names_no_registered_tool_is_left_out_like_a_shot(name: str) -> None:
    # The claude_code provider stores "" when it cannot pair a result with its
    # call, so the body could be a shot rendering from any tool.
    messages = _messages_with_results(
        [{"id": "c9", "name": name, "content": SHOT_BODY, "ok": True}]
    )

    assert "SHOT-BODY-MARKER" not in json.dumps(_built(messages))
    assert _results(messages)[0]["content"] is None
    assert _results(messages)[0]["characters"] == len(SHOT_BODY)


def test_a_failed_result_with_no_registered_name_is_still_kept() -> None:
    messages = _messages_with_results([{"id": "c9", "name": "", "content": REFUSAL, "ok": False}])

    assert _results(messages)[0]["content"] == REFUSAL


def test_every_other_tool_s_successful_result_is_kept_whole() -> None:
    kept = _results()[2]

    assert kept["content"] == KNOWLEDGE
    assert kept["ok"] is True


def test_a_result_with_no_ok_key_reads_as_ok_like_the_runner_reads_it() -> None:
    messages = _messages_with_results(
        [
            {"id": "x", "name": "get_shot", "content": SHOT_BODY},
            {"id": "y", "name": "search_knowledge", "content": KNOWLEDGE},
        ]
    )

    hidden, kept = _results(messages)
    assert hidden["ok"] is True
    assert hidden["content"] is None
    assert kept["ok"] is True
    assert kept["content"] == KNOWLEDGE


def test_a_failed_result_is_kept_in_full() -> None:
    refused = _results()[1]

    assert refused["content"] == REFUSAL
    assert refused["ok"] is False


def test_a_failed_shot_tool_result_is_kept_even_though_its_success_is_not() -> None:
    messages = _messages_with_results(
        [{"id": "c", "name": "compare_shots", "content": "Two to four shots.", "ok": False}]
    )

    assert _results(messages)[0]["content"] == "Two to four shots."


def test_tool_calls_are_kept_whole_with_name_id_and_arguments() -> None:
    calls = _built()["messages"][1]["tool_calls"]

    assert calls[0] == {"id": "c1", "name": "get_shot", "arguments": {"shot_id": 41}}
    assert calls[2]["arguments"] == {"query": "use ```code``` fences"}


def test_results_are_paired_with_their_calls_by_id() -> None:
    built = _built()

    call_ids = [call["id"] for call in built["messages"][1]["tool_calls"]]
    assert call_ids == [result["id"] for result in built["messages"][2]["tool_results"]]


# -- the tool list -----------------------------------------------------------


def _renders_shots(fn: Any) -> bool:
    """Whether a tool's code reaches ``render_shot``, following its own helpers.

    Walks the function's code objects (nested ones included) for the name, and
    follows every global that is a function defined under ``gaggiclanker.tools``.
    A call through an attribute of an object it is handed is not followed, which
    is why the control below names the five tools known to render shots.
    """
    seen: set[Any] = set()

    def walk(code: types.CodeType, globals_: dict[str, Any]) -> bool:
        if code in seen:
            return False
        seen.add(code)
        if "render_shot" in code.co_names:
            return True
        for name in code.co_names:
            target = globals_.get(name)
            if (
                isinstance(target, types.FunctionType)
                and target.__module__.startswith("gaggiclanker.tools")
                and walk(target.__code__, target.__globals__)
            ):
                return True
        return any(
            walk(const, globals_) for const in code.co_consts if isinstance(const, types.CodeType)
        )

    return walk(fn.__code__, fn.__globals__)


def _tools_rendering_shots() -> set[str]:
    found = set()
    for name in registry.names():
        spec = registry.get(name)
        assert spec is not None
        if _renders_shots(spec.fn):
            found.add(name)
    return found


def test_the_detector_finds_the_tools_known_to_render_shots() -> None:
    found = _tools_rendering_shots()

    assert {
        "get_shot",
        "get_shot_extended",
        "get_shot_full",
        "compare_shots",
        "list_set_shots",
    } <= found
    assert "search_knowledge" not in found
    assert "list_sets" not in found


def test_every_registered_tool_that_renders_shots_is_in_the_hidden_set() -> None:
    found = _tools_rendering_shots()

    assert found == SHOT_RENDERING_TOOLS


# -- order, content, runs --------------------------------------------------


def test_messages_keep_their_stored_order_roles_and_content() -> None:
    stored = _messages()

    got = _built(stored)["messages"]

    assert [(m["id"], m["role"], m["content"], m["run_id"]) for m in got] == [
        (m.id, m.role, m.content, m.run_id) for m in stored
    ]
    assert got[3]["content"].startswith("## Summary")  # verbatim, unclosed fence and all


def test_a_running_run_exports_without_error() -> None:
    running = ChatRunRow(id=2, thread_id=7, status="running", started_at="09:10")
    transcript = build_transcript(
        _thread(), _messages(), [_runs()[0], running], exported_at=EXPORTED
    )

    assert json.loads(render_transcript(transcript))["runs"][1]["status"] == "running"


def test_runs_carry_provider_model_tokens_and_the_whole_error() -> None:
    error = "claude exited 1\n## boom\n```\n  File x"
    failing = ChatRunRow(id=3, thread_id=7, status="failed", error=error, started_at=at(50))
    transcript = build_transcript(_thread(), _messages(), [*_runs(), failing], exported_at=EXPORTED)

    runs = json.loads(render_transcript(transcript))["runs"]

    assert runs[0]["provider"] == "anthropic"
    assert runs[0]["model"] == "claude-x"
    assert (runs[0]["tokens_in"], runs[0]["tokens_out"]) == (1200, 85)
    # The conversation's size and the cache split ride along, named in tokens;
    # a run that stored none exports them as null, not zero.
    assert (
        runs[0]["context_tokens"],
        runs[0]["cache_read_tokens"],
        runs[0]["cache_write_tokens"],
        runs[0]["requests"],
    ) == (700, 900, 100, 2)
    assert runs[1]["context_tokens"] is None
    assert runs[1]["cache_read_tokens"] is None
    assert runs[1]["error"] == "provider said 529 overloaded"
    assert runs[2]["error"] == error  # a run that stored no message still appears


def test_the_transcript_matches_the_golden_file(update_golden: bool) -> None:
    rendered = render_transcript(
        build_transcript(_thread(), _messages(), _runs(), exported_at=EXPORTED)
    )

    if update_golden:
        GOLDEN.write_text(rendered, encoding="utf-8")
        pytest.skip("golden file rewritten")
    assert rendered == GOLDEN.read_text(encoding="utf-8")


def test_the_json_is_pretty_printed_and_keeps_non_ascii_text() -> None:
    thread = _thread(title="Café ☕")
    rendered = render_transcript(
        build_transcript(thread, _messages(), _runs(), exported_at=EXPORTED)
    )

    assert '\n  "title": "Café ☕",' in rendered
    assert rendered.endswith("}\n")


def test_a_general_conversation_says_so() -> None:
    transcript = build_transcript(
        _thread(set_name=None), _messages(), _runs(), exported_at=EXPORTED
    )

    assert json.loads(render_transcript(transcript))["about"] == "General"


# -- the file name ---------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Dialling in the Kenya!", "chat-7-dialling-in-the-kenya.json"),
        ("", "chat-7.json"),
        ("???", "chat-7.json"),
        ("Café Ñandú 珈琲", "chat-7-cafe-nandu.json"),
        ("日本語だけ", "chat-7.json"),
        ("a" * 200, f"chat-7-{'a' * 60}.json"),
        ("a" * 59 + " b", f"chat-7-{'a' * 59}.json"),
    ],
)
def test_the_file_name_is_an_ascii_slug_of_the_title(title: str, expected: str) -> None:
    assert transcript_filename(7, title) == expected


# -- the route -------------------------------------------------------------


@pytest.fixture
async def app_client(env: EnvSettings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, int]]:
    async with running_app(env) as (app, client):
        fixture = await build_fixture(app.state.db)
        repo = ChatRepository(app.state.db)
        created = await repo.create_thread(
            ChatThreadWrite(title="Café notes", set_id=fixture.set_id)
        )
        assert created.thread is not None
        run = await repo.start_run(created.thread.id, provider="p", model="m")
        await repo.add_message(
            ChatMessageWrite(
                thread_id=created.thread.id, role="user", content="hello there", run_id=run.id
            )
        )
        await repo.add_message(
            ChatMessageWrite(
                thread_id=created.thread.id,
                role="tool",
                run_id=run.id,
                tool_results=[{"id": "a", "name": "get_shot", "content": SHOT_BODY, "ok": True}],
            )
        )
        yield app, client, created.thread.id


async def test_the_route_serves_json_as_an_attachment_with_the_slug_name(
    app_client: tuple[FastAPI, httpx.AsyncClient, int],
) -> None:
    _app, client, thread_id = app_client

    response = await client.get(f"/api/chat/threads/{thread_id}/transcript")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="chat-{thread_id}-cafe-notes.json"'
    )
    body = json.loads(response.text)  # the file itself, not the envelope
    assert "ok" not in body
    assert body["thread_id"] == thread_id
    assert body["messages"][0]["content"] == "hello there"
    assert "SHOT-BODY-MARKER" not in response.text
    assert body["messages"][1]["tool_results"][0]["characters"] == len(SHOT_BODY)
    assert body["runs"][0]["status"] == "running"  # exported as far as it is stored
    assert response.text.startswith("{\n  ")  # pretty-printed


async def test_an_unknown_thread_is_a_404_envelope(
    app_client: tuple[FastAPI, httpx.AsyncClient, int],
) -> None:
    _app, client, _thread_id = app_client

    response = await client.get("/api/chat/threads/9999/transcript")

    assert response.status_code == 404
    assert response.json()["ok"] is False


async def test_the_route_takes_no_query_parameter(
    app_client: tuple[FastAPI, httpx.AsyncClient, int],
) -> None:
    app, _client, _thread_id = app_client

    operation = app.openapi()["paths"]["/api/chat/threads/{thread_id}/transcript"]["get"]

    assert [parameter["name"] for parameter in operation["parameters"]] == ["thread_id"]


async def test_the_download_path_is_not_one_a_content_blocker_drops(
    app_client: tuple[FastAPI, httpx.AsyncClient, int],
) -> None:
    """EasyPrivacy (on by default in uBlock Origin and most blockers) has the
    rule ``/log?format=``: a request to it never leaves the browser and
    ``fetch`` throws "Failed to fetch" while the server sees nothing. So the
    route is not called ``/log`` and carries no query string; a rename back
    fails here.
    """
    app, _client, _thread_id = app_client

    paths = app.openapi()["paths"]
    gets = [
        path
        for path, item in paths.items()
        if path.startswith("/api/chat/threads/") and "get" in item
    ]

    assert "/api/chat/threads/{thread_id}/transcript" in gets
    for path in gets:
        assert "?" not in path
        assert "log" not in path.split("/"), path


async def test_a_conversation_longer_than_the_page_limit_is_exported_whole(
    app_client: tuple[FastAPI, httpx.AsyncClient, int],
) -> None:
    app, client, thread_id = app_client
    repo = ChatRepository(app.state.db)
    for number in range(520):
        await repo.add_message(
            ChatMessageWrite(thread_id=thread_id, role="user", content=f"turn {number}")
        )

    body = json.loads((await client.get(f"/api/chat/threads/{thread_id}/transcript")).text)

    assert len(body["messages"]) == 522
    assert body["messages"][-1]["content"] == "turn 519"
