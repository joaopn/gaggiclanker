"""The routes, over the real app, with the LLM service wired to the scripted fake.

The app is built the way the rest of the suite builds it — a real file, real
migrations, `httpx.ASGITransport` in process — with one substitution: the
provider factory hands back the fake, so a route test spends nothing and the
thing under test is the route.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings import EnvSettings
from gaggiclanker.starting.service import StartingPointService
from tests.conftest import running_app
from tests.llm.conftest import FakeProvider, api_error
from tests.starting.conftest import GOOD_PROFILE, Fixture, build_fixture, option_with


@pytest.fixture
async def api(
    env: EnvSettings, provider: FakeProvider
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider]]:
    """A real app whose LLM service — and starting-point service — use the fake."""
    async with running_app(env) as (app, client):
        # Knowledge seeding is the slow part and these tests are about routes,
        # not about retrieval; the lifespan has already seeded the docs anyway.
        fixture = await build_fixture(app.state.db, seed_knowledge=False)
        _rewire(app, provider)
        yield app, client, fixture, provider


def _rewire(app: FastAPI, provider: FakeProvider) -> None:
    """Point the app's LLM service — and the services holding it — at the fake.

    The starting-point service is app-scoped (it owns the "being opened right
    now" map), so replacing `app.state.llm` alone would leave it talking to the
    real provider factory. The budget and the mode memory are process-wide
    singletons by design, and one test scripting a 429 would otherwise latch
    every test after it in the file, so this app gets its own.
    """
    app.state.llm = LlmService(
        app.state.settings_service,
        observer=app.state.llm.observer,
        budget=RateLimitBudget(retries=0),
        mode_memory=ModeMemory(),
        provider_factory=lambda _config, _name: provider,
    )
    service = StartingPointService(
        app.state.db,
        app.state.llm,
        PromptService(PromptsRepository(app.state.db)),
        drafts=app.state.draft_proposals,
        bus=app.state.events,
    )
    service.retry_delay_s = 0.0
    app.state.starting = service


def _body(fixture: Fixture, **overrides: object) -> dict[str, object]:
    return {
        "bean_id": fixture.new_bean_id,
        "grinder_id": fixture.grinder_id,
        "usual_grind": "22",
        **overrides,
    }


# ── similar sets ─────────────────────────────────────────────────────


async def test_similar_sets_is_readable_without_spending_anything(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    """The wizard's first step shows these before anybody presses a button."""
    _app, client, fixture, provider = api
    response = await client.get(
        f"/api/beans/{fixture.new_bean_id}/similar-sets",
        params={"grinder_id": fixture.grinder_id},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["bean_id"] == fixture.new_bean_id
    assert data["items"][0]["set_version_id"] == fixture.versions["kenya"]
    assert data["items"][0]["outcome"]["shots"] == 5
    assert provider.calls == [], "reading the archive costs no tokens"


async def test_similar_sets_404s_for_a_bean_that_does_not_exist(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    _app, client, _fixture, _provider = api
    response = await client.get("/api/beans/9999/similar-sets")
    assert response.status_code == 404
    assert response.json()["ok"] is False


# ── proposing ────────────────────────────────────────────────────────


async def test_posting_answers_202_with_the_row_and_wait_finishes_it(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    _app, client, fixture, _provider = api
    response = await client.post("/api/starting-points", json=_body(fixture), params={"wait": 1})
    assert response.status_code == 202
    row = response.json()["data"]
    assert row["status"] == "ok"
    assert len(row["output"]["options"]) == 3

    read = await client.get(f"/api/starting-points/{row['id']}")
    assert read.status_code == 200
    assert read.json()["data"]["id"] == row["id"]


async def test_a_provider_failure_still_answers_2xx_with_the_row(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    """A 502 would leave the client an error and no id to look the failure up by."""
    _app, client, fixture, provider = api
    provider.script = [api_error(401, "bad key")]
    response = await client.post("/api/starting-points", json=_body(fixture), params={"wait": 1})
    assert response.status_code == 202
    row = response.json()["data"]
    assert row["status"] == "failed"
    assert row["error"].startswith("auth:")


async def test_a_bean_that_does_not_exist_is_a_404(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    _app, client, fixture, _provider = api
    response = await client.post("/api/starting-points", json=_body(fixture, bean_id=9999))
    assert response.status_code == 404


async def test_an_unknown_body_key_is_refused(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    _app, client, fixture, _provider = api
    response = await client.post(
        "/api/starting-points", json={**_body(fixture), "roast_level": "light"}
    )
    # 400, not 422: a body FastAPI itself cannot parse is an invalid *request*,
    # and `infra/envelope.py` maps it before the route is ever entered. 422 is
    # reserved for a body that parsed and asked for something impossible.
    assert response.status_code == 400


async def test_reading_a_run_that_does_not_exist_is_a_404(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    _app, client, _fixture, _provider = api
    assert (await client.get("/api/starting-points/9999")).status_code == 404


# ── accepting ────────────────────────────────────────────────────────


async def _run(client: httpx.AsyncClient, fixture: Fixture, **overrides: object) -> int:
    response = await client.post(
        "/api/starting-points", json=_body(fixture, **overrides), params={"wait": 1}
    )
    assert response.status_code == 202
    run_id: int = response.json()["data"]["id"]
    return run_id


async def test_accepting_answers_201_with_the_set_and_its_version(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    _app, client, fixture, _provider = api
    run_id = await _run(client, fixture)

    response = await client.post(
        f"/api/starting-points/{run_id}/accept", json={"option": "recommended"}
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["set"]["bean_id"] == fixture.new_bean_id
    assert data["version"]["origin"] == "starting_point"
    assert data["version"]["version_no"] == 1
    assert data["draft"] is None
    assert data["run"]["accepted_option"] == "recommended"

    # ... and the Set is where the Sets page will find it.
    listed = await client.get("/api/sets")
    assert any(row["id"] == data["set"]["id"] for row in listed.json()["data"]["items"])


async def test_accepting_an_option_with_a_profile_returns_the_draft_to_open(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    """The wizard's next step is to open it; a second round-trip to find out
    whether there even is one would make the button feel like it did nothing."""
    _app, client, fixture, provider = api
    provider.script = [json.dumps(option_with(profile=GOOD_PROFILE))]
    run_id = await _run(client, fixture)

    response = await client.post(
        f"/api/starting-points/{run_id}/accept", json={"option": "recommended"}
    )
    assert response.status_code == 201
    draft = response.json()["data"]["draft"]
    assert draft is not None
    assert draft["status"] == "draft"

    queue = await client.get("/api/profile-drafts")
    assert queue.status_code == 200, queue.text
    assert any(row["id"] == draft["id"] for row in queue.json()["data"]["items"])


async def test_accepting_twice_is_a_409(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    _app, client, fixture, _provider = api
    run_id = await _run(client, fixture)
    assert (
        await client.post(f"/api/starting-points/{run_id}/accept", json={"option": "recommended"})
    ).status_code == 201

    second = await client.post(
        f"/api/starting-points/{run_id}/accept", json={"option": "conservative"}
    )
    assert second.status_code == 409
    # The refusal carries what the first accept made, so the UI can take the
    # person there rather than showing them an error about a Set that exists.
    assert second.json()["error"]["details"]["set_id"]


async def test_an_option_key_that_is_not_one_of_the_three_is_refused(
    api: tuple[FastAPI, httpx.AsyncClient, Fixture, FakeProvider],
) -> None:
    _app, client, fixture, _provider = api
    run_id = await _run(client, fixture)
    response = await client.post(
        f"/api/starting-points/{run_id}/accept", json={"option": "balanced"}
    )
    # The literal is on the request model, so this never reaches the service:
    # it is a malformed request rather than an impossible one.
    assert response.status_code == 400
