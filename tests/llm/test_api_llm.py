"""``/api/llm`` over HTTP, against the real app."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.api.llm import _call_stream
from gaggiclanker.db.repos.llm import LlmCallRow, LlmCallsRepository
from gaggiclanker.infra.sse import SseEvent
from gaggiclanker.llm.types import CredentialCheck, LlmMessage, LlmRequest, Usage
from gaggiclanker.settings import EnvSettings

# `serving` runs the app on a real loopback port. httpx.ASGITransport awaits the
# application to completion before returning a response, so a stream that never
# completes deadlocks it; the helper lives next to the device tests because they
# hit the same wall first.
from tests.device.conftest import read_events, serving
from tests.llm.conftest import Answer, FakeProvider


def use_fake(app: FastAPI, provider: FakeProvider) -> FakeProvider:
    """Point the app's service at a scripted provider instead of a real one."""
    app.state.llm._build_provider = lambda _config, _name: provider
    app.state.llm._provider = None
    app.state.llm._provider_key = None
    return provider


async def body(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    assert payload["ok"] is True, payload
    payload_data: dict[str, Any] = payload["data"]
    return payload_data


# -- validate and models --------------------------------------------------


async def test_validate_uses_the_configured_provider(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    provider = use_fake(
        app, FakeProvider(credentials=CredentialCheck(provider="ollama", ok=True, detail="fine"))
    )

    data = await body(await client.post("/api/llm/validate", json={}))

    assert data["ok"] is True
    assert provider.closed is True


async def test_validate_accepts_a_named_provider(app: FastAPI, client: httpx.AsyncClient) -> None:
    use_fake(app, FakeProvider(credentials=CredentialCheck(provider="anthropic", ok=False)))

    data = await body(await client.post("/api/llm/validate", json={"provider": "anthropic"}))

    assert data["ok"] is False


async def test_an_unknown_provider_is_a_400_naming_the_valid_ones(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/llm/validate", json={"provider": "skynet"})

    assert response.status_code == 400
    payload = response.json()
    assert "claude_code" in payload["error"]["details"]["known_providers"]


async def test_models_lists_what_the_provider_offers(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    use_fake(app, FakeProvider(models=["a", "b"]))

    data = await body(await client.get("/api/llm/models"))

    assert data["models"] == ["a", "b"]


# -- the rate-limit latch -------------------------------------------------


async def test_the_latch_is_visible_and_clearable(app: FastAPI, client: httpx.AsyncClient) -> None:
    app.state.llm.budget.latch()

    assert (await body(await client.get("/api/llm/rate-limit")))["stopped"] is True

    data = await body(await client.post("/api/llm/rate-limit/reset"))

    assert data["stopped"] is False
    assert data["remaining"] == data["retries"]


# -- the live call list ---------------------------------------------------


async def test_calls_are_listed_newest_first(app: FastAPI, client: httpx.AsyncClient) -> None:
    observer = app.state.llm.observer
    observer.register(label="first").succeed(Usage(prompt_tokens=1, completion_tokens=2))
    observer.register(label="second")

    data = await body(await client.get("/api/llm/calls"))

    assert [call["label"] for call in data["calls"]] == ["second", "first"]
    assert data["running"] == 1


async def test_the_stream_opens_with_a_snapshot_then_carries_updates(app: FastAPI) -> None:
    """A tab that joins mid-analysis has to be immediately correct.

    Driven as a generator rather than over HTTP: the ordering being asserted is
    the generator's, and racing it against a real SSE connection would test the
    test harness's buffering instead.
    """
    observer = app.state.llm.observer
    observer.register(label="already running")
    stream = _call_stream(observer, app.state.events)

    snapshot = await anext(stream)
    # Subscribed only once the generator has yielded the snapshot, so the
    # publish below cannot be missed.
    pending: asyncio.Task[SseEvent] = asyncio.create_task(anext(stream))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    observer.register(label="just started")
    update = await asyncio.wait_for(pending, timeout=2)
    await stream.aclose()  # type: ignore[attr-defined]

    assert snapshot.event == "llm.snapshot"
    assert snapshot.data["calls"][0]["label"] == "already running"
    assert update.event == "llm.call"
    assert update.data["call"]["label"] == "just started"
    assert update.data["running"] == 2


async def test_the_stream_is_a_real_event_stream_over_http(env: EnvSettings) -> None:
    """The wire format, including the CRLF separator sse-starlette writes."""
    async with serving(env) as (app, base_url):
        app.state.llm.observer.register(label="analyse shot", subject="#129")
        async with httpx.AsyncClient(base_url=base_url) as http:
            async with http.stream("GET", "/api/llm/calls/stream") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                events = await read_events(response, 1)

    assert events[0]["event"] == "llm.snapshot"
    assert events[0]["data"]["calls"][0]["subject"] == "#129"


async def test_a_call_publishes_onto_the_stream(app: FastAPI) -> None:
    """The header indicator's running count comes from these events."""
    use_fake(app, FakeProvider())
    bus = app.state.events
    seen: list[str] = []

    async def collect() -> None:
        async for event in bus.stream():
            seen.append(event.event)
            if len(seen) >= 2:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await app.state.llm.call_json(
        LlmRequest(
            messages=[LlmMessage(role="user", content="hi")],
            output_model=Answer,
            model="m",
            label="analyse",
        )
    )
    await asyncio.wait_for(task, timeout=2)

    assert seen == ["llm.call", "llm.call"]


# -- the ledger -----------------------------------------------------------


async def test_a_call_writes_a_usage_row(app: FastAPI, client: httpx.AsyncClient) -> None:
    use_fake(app, FakeProvider())

    await app.state.llm.call_json(
        LlmRequest(
            messages=[LlmMessage(role="user", content="hi")],
            output_model=Answer,
            model="test-model",
            purpose="analysis",
            label="analyse shot",
            subject="#129",
            prompt_name="ping",
        )
    )

    data = await body(await client.get("/api/llm/usage"))

    assert data["calls"] == 1
    assert data["succeeded"] == 1
    assert data["input_tokens"] == 11
    assert data["output_tokens"] == 7
    assert data["by_model"][0]["model"] == "test-model"


async def test_usage_can_be_scoped_to_a_date(app: FastAPI, client: httpx.AsyncClient) -> None:
    repo = LlmCallsRepository(app.state.db)
    await repo.record(
        LlmCallRow(
            call_id="old",
            purpose="analysis",
            provider="anthropic",
            model="opus",
            status="succeeded",
            input_tokens=5,
        )
    )
    await app.state.db.execute(
        "UPDATE llm_calls SET created_at = '2020-01-01T00:00:00.000Z' WHERE call_id = 'old'"
    )
    await repo.record(
        LlmCallRow(
            call_id="new",
            purpose="analysis",
            provider="anthropic",
            model="opus",
            status="failed",
            input_tokens=50,
        )
    )

    data = await body(await client.get("/api/llm/usage", params={"since": "2024-01-01T00:00:00Z"}))

    assert data["calls"] == 1
    assert data["failed"] == 1
    assert data["input_tokens"] == 50


async def test_an_unreported_token_count_is_not_counted_as_zero(app: FastAPI) -> None:
    repo = LlmCallsRepository(app.state.db)
    await repo.record(
        LlmCallRow(call_id="x", purpose="chat", provider="ollama", status="succeeded")
    )

    row = (await repo.recent())[0]

    assert row.input_tokens is None


# -- the status panel -----------------------------------------------------


async def test_status_reports_the_provider_and_the_models(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    await app.state.settings_service.apply(
        {"llmProvider": "openrouter", "modelDefault": "base", "modelChat": "chatty"}
    )

    data = await body(await client.get("/api/llm/status"))

    assert data["provider"] == "openrouter"
    assert data["models"]["chat"] == "chatty"
    assert data["models"]["analysis"] == "base"
    assert "max" in data["effort_levels"]
    # The CLI is not probed unless it is the provider in use.
    assert "version" not in data["claude_code"]
