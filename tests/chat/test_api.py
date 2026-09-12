"""``/api/chat`` over the real app: threads, a queued turn, and the stream.

The provider is replaced on ``app.state`` rather than mocked at the HTTP layer,
so everything from the route down — the rate limit, the background task, the
persisted stream, the envelope — is the code that ships.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.chat.runner import run_task_name
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.chat_types import ChatToolCall, ChatTurn
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings import EnvSettings
from tests.analyzer.conftest import Fixture, build_fixture
from tests.llm.conftest import FakeProvider


def data(response: httpx.Response) -> Any:
    body = response.json()
    assert body["ok"], body
    return body["data"]


@pytest.fixture
async def chat_app(
    env: EnvSettings,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture]]:
    from gaggiclanker.main import create_app
    from tests.conftest import NO_WEB_DIST

    app = create_app(env, web_dist=NO_WEB_DIST, dotenv={})
    provider = FakeProvider(chat_script=[ChatTurn(text="Pull two more.")])
    async with app.router.lifespan_context(app):
        fixture = await build_fixture(app.state.db)
        # A service of this test's own, wired to the scripted provider, handed
        # to the runner the lifespan already built.
        app.state.chat.llm = LlmService(
            app.state.settings_service,
            budget=RateLimitBudget(retries=0),
            mode_memory=ModeMemory(),
            provider_factory=lambda _config, _name: provider,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client, provider, fixture


async def wait_for(app: FastAPI, run_id: int) -> None:
    task = app.state.tasks.get(run_task_name(run_id))
    if task is not None:
        await asyncio.wait([task], timeout=10)


async def test_a_thread_can_be_created_listed_renamed_and_deleted(
    chat_app: tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture],
) -> None:
    _app, client, _provider, fixture = chat_app

    created = data(
        await client.post("/api/chat/threads", json={"title": "", "set_id": fixture.set_id})
    )
    assert created["set_id"] == fixture.set_id
    assert created["set_name"]

    assert [row["id"] for row in data(await client.get("/api/chat/threads"))] == [created["id"]]

    renamed = data(
        await client.patch(f"/api/chat/threads/{created['id']}", json={"title": "Guji v3"})
    )
    assert renamed["title"] == "Guji v3"

    assert data(await client.delete(f"/api/chat/threads/{created['id']}"))["deleted"] is True
    assert data(await client.get("/api/chat/threads")) == []


async def test_sending_answers_202_with_the_run_to_follow(
    chat_app: tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture],
) -> None:
    app, client, _provider, _fixture = chat_app
    thread = data(await client.post("/api/chat/threads", json={}))

    response = await client.post(
        f"/api/chat/threads/{thread['id']}/messages", json={"message": "how is it going?"}
    )

    assert response.status_code == 202
    body = data(response)
    assert body["run"]["status"] == "running"
    assert body["message"]["role"] == "user"

    await wait_for(app, body["run"]["id"])
    assert data(await client.get(f"/api/chat/runs/{body['run']['id']}"))["status"] == "ok"


async def test_the_transcript_comes_back_with_the_thread(
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
        ChatTurn(text="It looks fine."),
    ]
    thread = data(await client.post("/api/chat/threads", json={}))
    run = data(
        await client.post(f"/api/chat/threads/{thread['id']}/messages", json={"message": "look"})
    )["run"]
    await wait_for(app, run["id"])

    detail = data(await client.get(f"/api/chat/threads/{thread['id']}"))

    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert detail["messages"][1]["tool_calls"][0]["name"] == "get_shot"
    assert detail["runs"][0]["tool_calls"] == 1


async def test_the_stream_replays_a_finished_run_and_then_ends(
    chat_app: tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture],
) -> None:
    """A tab that opens after the answer landed still sees the whole answer."""
    app, client, _provider, _fixture = chat_app
    thread = data(await client.post("/api/chat/threads", json={}))
    run = data(
        await client.post(f"/api/chat/threads/{thread['id']}/messages", json={"message": "hi"})
    )["run"]
    await wait_for(app, run["id"])

    async with client.stream("GET", f"/api/chat/runs/{run['id']}/stream") as response:
        assert response.status_code == 200
        payloads = [
            json.loads(line[len("data: ") :])
            for line in [raw.strip() for raw in await _lines(response)]
            if line.startswith("data: ")
        ]

    kinds = [payload["kind"] for payload in payloads]
    assert "delta" in kinds
    assert kinds[-1] == "completed"


async def _lines(response: httpx.Response) -> list[str]:
    """Read the stream to its end.

    Bounded, because the interesting failure is a stream that never closes and a
    test that hangs reports nothing. A timeout here leaves whatever arrived, and
    the caller's assertions then say what was missing.
    """
    collected: list[str] = []

    async def read() -> None:
        async for line in response.aiter_lines():
            collected.append(line)

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(read(), timeout=5)
    return collected


async def test_the_stream_releases_its_subscriber_when_it_ends(
    chat_app: tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture],
) -> None:
    """A stream that returns early must not leave a queue on the bus.

    It used to: the route iterated `bus.stream()` and returned out of the
    `async for` on the run's terminal event, which leaves the inner generator
    suspended for the garbage collector. The subscriber leaked, every later
    event was published into a queue nobody read, and the process hung in
    `gc.collect()` at exit finalising it.
    """
    app, client, _provider, _fixture = chat_app
    thread = data(await client.post("/api/chat/threads", json={}))
    run = data(
        await client.post(f"/api/chat/threads/{thread['id']}/messages", json={"message": "hi"})
    )["run"]
    await wait_for(app, run["id"])

    async with client.stream("GET", f"/api/chat/runs/{run['id']}/stream") as response:
        await _lines(response)

    assert app.state.events.subscriber_count == 0


async def test_resuming_after_a_sequence_number_skips_what_was_seen(
    chat_app: tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture],
) -> None:
    app, client, _provider, _fixture = chat_app
    thread = data(await client.post("/api/chat/threads", json={}))
    run = data(
        await client.post(f"/api/chat/threads/{thread['id']}/messages", json={"message": "hi"})
    )["run"]
    await wait_for(app, run["id"])

    async with client.stream(
        "GET", f"/api/chat/runs/{run['id']}/stream", params={"after": 1}
    ) as response:
        payloads = [
            json.loads(line[len("data: ") :])
            for line in [raw.strip() for raw in await _lines(response)]
            if line.startswith("data: ")
        ]

    assert payloads
    assert min(payload["seq"] for payload in payloads) == 2


async def test_cancelling_a_run_is_a_route(
    chat_app: tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture],
) -> None:
    app, client, provider, _fixture = chat_app
    provider.chat_delay = 0.3
    thread = data(await client.post("/api/chat/threads", json={}))
    run = data(
        await client.post(f"/api/chat/threads/{thread['id']}/messages", json={"message": "hi"})
    )["run"]

    await client.post(f"/api/chat/runs/{run['id']}/cancel")
    await wait_for(app, run["id"])

    assert data(await client.get(f"/api/chat/runs/{run['id']}"))["status"] == "cancelled"


async def test_the_tool_list_names_each_permission_class(
    chat_app: tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture],
) -> None:
    """The UI labels a trace with it: "propose" is the thing a reader must see."""
    _app, client, _provider, _fixture = chat_app

    tools = data(await client.get("/api/chat/tools"))["tools"]

    by_name = {tool["name"]: tool["permission"] for tool in tools}
    assert by_name["query_shots"] == "read"
    assert by_name["propose_set_version"] == "propose"
    assert set(by_name.values()) == {"read", "propose"}


async def test_unknown_threads_and_runs_are_404s(
    chat_app: tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture],
) -> None:
    _app, client, _provider, _fixture = chat_app

    assert (await client.get("/api/chat/threads/999")).status_code == 404
    assert (await client.get("/api/chat/runs/999")).status_code == 404
    assert (
        await client.post("/api/chat/threads/999/messages", json={"message": "hi"})
    ).status_code == 404


async def test_an_empty_message_is_a_400(
    chat_app: tuple[FastAPI, httpx.AsyncClient, FakeProvider, Fixture],
) -> None:
    _app, client, _provider, _fixture = chat_app
    thread = data(await client.post("/api/chat/threads", json={}))

    response = await client.post(
        f"/api/chat/threads/{thread['id']}/messages", json={"message": "   "}
    )

    assert response.status_code == 400
