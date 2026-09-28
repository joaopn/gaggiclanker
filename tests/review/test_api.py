"""The review routes, over the real app.

The app is built the way the rest of the suite builds it — a real file, real
migrations, `httpx.ASGITransport` in process — with one substitution: the LLM
service's provider factory hands back the scripted fake, so a route test spends
no tokens and the thing under test is the route rather than the model.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.review.service import ReviewService, review_task_name
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app
from tests.llm.conftest import FakeProvider, api_error
from tests.review.conftest import GOOD_REVIEW, Fixture, build_fixture


@pytest.fixture
async def api(
    env: EnvSettings, provider: FakeProvider
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, FakeProvider]]:
    """A real app whose LLM service is wired to the scripted fake."""
    async with running_app(env) as (app, client):
        _rewire(app, provider)
        yield app, client, provider


def _rewire(app: FastAPI, provider: FakeProvider) -> None:
    """Point the app's LLM service — and the review service holding it — at the fake.

    The review service is app-scoped (it owns the "being opened right now" map), so
    replacing `app.state.llm` alone would leave it talking to the real provider
    factory. The budget and the mode memory are process-wide singletons by
    design, and one test scripting a 429 would otherwise latch every test after
    it in the file, so this app gets its own.
    """
    app.state.llm = LlmService(
        app.state.settings_service,
        observer=app.state.llm.observer,
        budget=RateLimitBudget(retries=0),
        mode_memory=ModeMemory(),
        provider_factory=lambda _config, _name: provider,
    )
    app.state.reviews = ReviewService(
        app.state.db,
        app.state.llm,
        PromptService(PromptsRepository(app.state.db)),
        bus=app.state.events,
    )
    # No real backoff: the failure paths retry, and half a second each is most
    # of this file's wall clock for nothing.
    app.state.reviews.retry_delay_s = 0.0


# ── the review routes ────────────────────────────────────────────────
#
# These need a shot, which needs a machine and a Set. The fixture Set lives in
# conftest and is built over a bare `Database`; here the same builder is run
# against the app's own handle so the routes see it.


async def _settle(app: FastAPI, task_name: str) -> None:
    """Wait for a queued task, if it has not already finished.

    A finished task has released its name, so a missing one is the success
    case rather than something to assert on.
    """
    task = app.state.tasks.get(task_name)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 10)


async def _build_fixture(app: FastAPI) -> Fixture:
    """The conftest Set, built against the app's own handle."""
    return await build_fixture(app.state.db)


async def test_reviewing_a_shot_over_http(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]

    response = await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})
    assert response.status_code == 202
    body = response.json()["data"]
    assert body["status"] == "ok"
    assert body["summary"] == GOOD_REVIEW["summary"]
    assert body["description"] == GOOD_REVIEW["description"]
    assert body["taste_balance"] == "sour"
    assert "input" not in body, "a row as a page reads it carries no input"

    listed = await client.get(f"/api/shots/{shot_id}/reviews")
    assert [row["id"] for row in listed.json()["data"]["items"]] == [body["id"]]

    one = (await client.get(f"/api/reviews/{body['id']}")).json()["data"]
    assert one["id"] == body["id"]
    assert one["input"]["shot_id"] == shot_id
    assert one["input"]["shot"].startswith(f"shot {shot_id}")

    # And the shot detail carries it, so the card needs no second request.
    detail = await client.get(f"/api/shots/{shot_id}")
    assert [row["id"] for row in detail.json()["data"]["reviews"]] == [body["id"]]
    assert "analyses" not in detail.json()["data"]


async def test_a_second_press_after_one_finished_is_a_fresh_review(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """Review again means another reading; the earlier one stays stored."""
    app, client, provider = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]

    first = (await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})).json()["data"]
    calls = len(provider.calls)
    again = await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})

    assert again.status_code == 202
    assert again.json()["data"]["id"] != first["id"]
    assert len(provider.calls) > calls
    rows = (await client.get(f"/api/shots/{shot_id}/reviews")).json()["data"]["items"]
    assert [row["id"] for row in rows] == [again.json()["data"]["id"], first["id"]]


async def test_a_provider_failure_is_a_2xx_carrying_the_error(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """A 502 would leave the client with an error and no row to look at."""
    app, client, provider = api
    data = await _build_fixture(app)
    provider.script = [api_error(429, "slow down")]

    response = await client.post(f"/api/shots/{data.shots[-1]}/reviews?wait=1", json={})

    assert response.status_code == 202
    body = response.json()["data"]
    assert body["status"] == "failed"
    assert body["error"].startswith("rate_limited:")


async def test_a_quarantined_shot_cannot_be_reviewed(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await _build_fixture(app)
    await app.state.db.execute(
        "UPDATE shots SET quarantined = 1 WHERE id = ?",
        (data.shots[-1],),
    )

    response = await client.post(f"/api/shots/{data.shots[-1]}/reviews", json={})
    assert response.status_code == 422
    assert "quarantined" in response.json()["error"]["message"]


async def test_reviewing_a_missing_shot_is_a_404(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    _, client, _ = api
    assert (await client.post("/api/shots/999999/reviews", json={})).status_code == 404
    assert (await client.get("/api/reviews/999999")).status_code == 404


async def test_a_queued_review_answers_before_the_provider_does(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """202 with a `running` row, and the work carries on in the registry."""
    app, client, provider = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]
    provider.delay = 0.3

    response = await client.post(f"/api/shots/{shot_id}/reviews", json={})

    assert response.status_code == 202
    assert response.json()["data"]["status"] == "running"
    assert review_task_name(shot_id) in app.state.tasks.names

    # And it finishes on its own.
    await _settle(app, review_task_name(shot_id))
    listed = (await client.get(f"/api/shots/{shot_id}/reviews")).json()["data"]["items"]
    assert listed[0]["status"] == "ok"


async def test_a_second_press_while_one_runs_gets_the_same_row(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """The registry name is the idempotency rule, and it is claimed synchronously."""
    app, client, provider = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]
    provider.delay = 0.3

    first, second = await asyncio.gather(
        client.post(f"/api/shots/{shot_id}/reviews", json={}),
        client.post(f"/api/shots/{shot_id}/reviews", json={}),
    )

    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    await _settle(app, review_task_name(shot_id))
    rows = (await client.get(f"/api/shots/{shot_id}/reviews")).json()["data"]["items"]
    assert len(rows) == 1, "two presses at once must not become two reviews"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/shots/{shot}/analyses"),
        ("get", "/api/shots/{shot}/analyses"),
        ("get", "/api/analyses/1"),
        ("post", "/api/suggestions/1/accept"),
        ("post", "/api/suggestions/1/reject"),
        ("get", "/api/sets/{set}/suggestions"),
        ("post", "/api/sets/{set}/analyse"),
    ],
)
async def test_the_analysis_routes_are_gone(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider], method: str, path: str
) -> None:
    app, client, provider = api
    data = await _build_fixture(app)

    response = await client.request(
        method.upper(), path.format(shot=data.shots[-1], set=data.set_id), json={}
    )

    assert response.status_code == 404
    assert provider.calls == []


async def test_the_shots_list_carries_no_review_state(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """A review lives on the shot page only, not in the shots table."""
    app, client, _ = api
    data = await _build_fixture(app)
    await client.post(f"/api/shots/{data.shots[-1]}/reviews?wait=1", json={})

    row = next(
        item
        for item in (await client.get("/api/shots")).json()["data"]["items"]
        if item["id"] == data.shots[-1]
    )
    assert not [key for key in row if "review" in key or "analysis" in key]


async def test_the_vocabulary_serves_no_suggestion_words(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    _, client, _ = api
    vocab = (await client.get("/api/vocab")).json()["data"]
    assert next(term["value"] for term in vocab["shot_styles"]) == "classic"
    assert not [key for key in vocab if "suggestion" in key or "actionable" in key]


async def test_shutdown_cancels_a_running_review_and_boot_reconciles_it(
    env: EnvSettings, provider: FakeProvider
) -> None:
    """A lifespan teardown cancels the registered task; the next boot marks it `interrupted`."""
    provider.delay = 30.0
    async with running_app(env) as (app, client):
        _rewire(app, provider)
        data = await _build_fixture(app)
        response = await client.post(f"/api/shots/{data.shots[-1]}/reviews", json={})
        assert response.json()["data"]["status"] == "running"
    # The lifespan has exited: the task was cancelled mid-call.

    async with running_app(env) as (_app, client):
        rows = (await client.get(f"/api/shots/{data.shots[-1]}/reviews")).json()["data"]["items"]
        assert rows[0]["status"] == "interrupted"
        assert "stopped before this review finished" in rows[0]["error"]
