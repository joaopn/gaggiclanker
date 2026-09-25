"""The analysis, knowledge and suggestion routes, over the real app.

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

from gaggiclanker.analyzer.service import (
    BATCH_ACKNOWLEDGE_ABOVE,
    AnalyzerService,
    analysis_task_name,
)
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings import EnvSettings
from tests.analyzer.conftest import Fixture, build_fixture
from tests.conftest import running_app
from tests.llm.conftest import FakeProvider, api_error


@pytest.fixture
async def api(
    env: EnvSettings, provider: FakeProvider
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, FakeProvider]]:
    """A real app whose LLM service is wired to the scripted fake."""
    async with running_app(env) as (app, client):
        _rewire(app, provider)
        yield app, client, provider


def _rewire(app: FastAPI, provider: FakeProvider) -> None:
    """Point the app's LLM service — and the analyzer holding it — at the fake.

    The analyzer is app-scoped (it owns the "being opened right now" map), so
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
    app.state.analyzer = AnalyzerService(
        app.state.db,
        app.state.llm,
        PromptService(PromptsRepository(app.state.db)),
        bus=app.state.events,
    )
    # No real backoff: the failure paths retry, and half a second each is most
    # of this file's wall clock for nothing.
    app.state.analyzer.retry_delay_s = 0.0


async def test_the_knowledge_rules_are_seeded_and_listed(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    _, client, _ = api
    response = await client.get("/api/knowledge/rules")
    assert response.status_code == 200
    body = response.json()["data"]

    assert len(body["items"]) > 100
    assert body["categories"][0] == "dial_in_order", "the order the prompt lists them in"
    first = body["items"][0]
    assert first["category"] == "dial_in_order"
    assert first["enabled"] is True
    assert first["edited"] is False
    assert first["source"]


async def test_rules_can_be_filtered(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    _, client, _ = api
    response = await client.get("/api/knowledge/rules", params={"category": "pressure_matrix"})
    items = response.json()["data"]["items"]
    assert items
    assert {item["category"] for item in items} == {"pressure_matrix"}

    bad = await client.get("/api/knowledge/rules", params={"category": "nonsense"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_REQUEST"


async def test_rules_can_be_narrowed_to_what_a_situation_would_select(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """`?applies=` answers "what would this bean be told"."""
    _, client, _ = api

    response = await client.get(
        "/api/knowledge/rules",
        params={"applies": "roast_level:light,process:natural,style:bloom,signal:balance:sour"},
    )
    keys = {item["key"] for item in response.json()["data"]["items"]}

    assert "natural.light" in keys
    assert "washed.light" not in keys, "a washed cell is not what this bean would be told"
    assert "sour" in keys
    assert "bitter" not in keys
    # Style-keyed rules follow the style, and the procedure rules always apply.
    assert "hierarchy" in keys
    bloom_ratio = {
        item["key"]
        for item in response.json()["data"]["items"]
        if item["category"] == "ratio_by_style"
    }
    assert "bloom" in bloom_ratio
    assert "turbo" not in bloom_ratio


async def test_a_malformed_applies_token_is_a_400(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    _, client, _ = api

    response = await client.get("/api/knowledge/rules", params={"applies": "light"})

    assert response.status_code == 400
    assert response.json()["error"]["details"]["field"] == "applies"


async def test_a_rule_can_be_disabled_and_edited(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    _, client, _ = api
    rule = (await client.get("/api/knowledge/rules", params={"category": "increments"})).json()[
        "data"
    ]["items"][0]

    disabled = await client.patch(f"/api/knowledge/rules/{rule['id']}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False

    edited = await client.patch(
        f"/api/knowledge/rules/{rule['id']}", json={"value": {"text": "mine", "min": 3}}
    )
    assert edited.json()["data"]["value"] == {"text": "mine", "min": 3}
    assert edited.json()["data"]["edited"] is True

    # A re-seed leaves the edit alone.
    reload = await client.post("/api/knowledge/rules/reload")
    assert reload.status_code == 200
    after = await client.get(f"/api/knowledge/rules?category={rule['category']}")
    mine = next(item for item in after.json()["data"]["items"] if item["id"] == rule["id"])
    assert mine["value"]["text"] == "mine"
    assert mine["enabled"] is False


async def test_patching_a_rule_that_does_not_exist_is_a_404(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    _, client, _ = api
    response = await client.patch("/api/knowledge/rules/999999", json={"enabled": False})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ── the analysis routes ──────────────────────────────────────────────
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


async def test_running_an_analysis_over_http(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]

    response = await client.post(f"/api/shots/{shot_id}/analyses?wait=1", json={})
    assert response.status_code == 202
    body = response.json()["data"]
    assert body["status"] == "ok"
    assert body["output"]["diagnosis"]
    assert len(body["suggestions"]) == 3

    listed = await client.get(f"/api/shots/{shot_id}/analyses")
    assert [row["id"] for row in listed.json()["data"]["items"]] == [body["id"]]

    one = await client.get(f"/api/analyses/{body['id']}")
    assert one.json()["data"]["id"] == body["id"]

    # And the shot detail carries it, so the panel needs no second request.
    detail = await client.get(f"/api/shots/{shot_id}")
    assert [row["id"] for row in detail.json()["data"]["analyses"]] == [body["id"]]


async def test_a_second_press_returns_the_existing_analysis(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """The accidental double-click must not spend a second call."""
    app, client, provider = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]

    first = await client.post(f"/api/shots/{shot_id}/analyses?wait=1", json={})
    calls = len(provider.calls)

    again = await client.post(f"/api/shots/{shot_id}/analyses", json={})
    # Nothing was accepted, so it is a 200 rather than a 202.
    assert again.status_code == 200
    assert again.json()["data"]["id"] == first.json()["data"]["id"]
    assert len(provider.calls) == calls

    forced = await client.post(f"/api/shots/{shot_id}/analyses?wait=1", json={"force": True})
    assert forced.status_code == 202
    assert forced.json()["data"]["id"] != first.json()["data"]["id"]
    assert len(provider.calls) > calls


async def test_a_provider_failure_is_a_201_carrying_the_error(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """A 502 would leave the client with an error and no row to look at."""
    app, client, provider = api
    data = await _build_fixture(app)
    provider.script = [api_error(429, "slow down")]

    response = await client.post(f"/api/shots/{data.shots[-1]}/analyses?wait=1", json={})

    assert response.status_code == 202
    body = response.json()["data"]
    assert body["status"] == "failed"
    assert body["error"].startswith("rate_limited:")


async def test_a_quarantined_shot_cannot_be_analysed(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await _build_fixture(app)
    await app.state.db.execute(
        "UPDATE shots SET quarantined = 1 WHERE id = ?",
        (data.shots[-1],),
    )

    response = await client.post(f"/api/shots/{data.shots[-1]}/analyses", json={})
    assert response.status_code == 422
    assert "quarantined" in response.json()["error"]["message"]


async def test_analysing_a_missing_shot_is_a_404(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    _, client, _ = api
    response = await client.post("/api/shots/999999/analyses", json={})
    assert response.status_code == 404


async def test_the_set_batch_and_its_suggestions(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await _build_fixture(app)
    set_id = data.set_id

    batch = await client.post(f"/api/sets/{set_id}/analyse?wait=1", json={})
    assert batch.status_code == 202
    assert batch.json()["data"]["succeeded"] == 2
    assert batch.json()["data"]["task"] == f"analyse_set:{set_id}"

    listed = await client.get(f"/api/sets/{set_id}/suggestions")
    items = listed.json()["data"]["items"]
    assert items
    assert {item["variable"] for item in items} >= {"grind", "yield", "pressure"}
    # Every row names the shot it is about, so the page can group them.
    assert all(item["shot_id"] for item in items)


async def test_accept_and_reject_over_http(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]
    analysis = (await client.post(f"/api/shots/{shot_id}/analyses?wait=1", json={})).json()["data"]
    grind, yield_, pressure = analysis["suggestions"]

    accepted = await client.post(f"/api/suggestions/{grind['id']}/accept")
    assert accepted.status_code == 201
    body = accepted.json()["data"]
    assert body["suggestion"]["status"] == "accepted"
    assert body["version"]["grind_value"] == 20.0
    assert body["version"]["version_no"] == 2
    assert body["version"]["origin"] == "analysis"

    rejected = await client.post(f"/api/suggestions/{yield_['id']}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["data"]["status"] == "rejected"

    refused = await client.post(f"/api/suggestions/{pressure['id']}/accept")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "CONFLICT"


async def test_the_shots_list_carries_the_analysis_state(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]

    # The fixture's earlier shots already carry an analysis each; the subject of
    # this one does not.
    before = {
        row["id"]: row["analysis_state"]
        for row in (await client.get("/api/shots")).json()["data"]["items"]
    }
    assert before[shot_id] == "none"

    await client.post(f"/api/shots/{shot_id}/analyses?wait=1", json={})

    after = (await client.get("/api/shots")).json()["data"]["items"]
    states = {row["id"]: row["analysis_state"] for row in after}
    assert states[shot_id] == "ok"


async def test_the_shots_list_carries_the_newest_failure_and_only_that(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """The list's Retry button says what went wrong without a request per row."""
    app, client, provider = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]

    async def listed() -> dict[str, object]:
        items = (await client.get("/api/shots")).json()["data"]["items"]
        return next(row for row in items if row["id"] == shot_id)

    assert (await listed())["analysis_error"] is None

    good = list(provider.script)
    provider.script = [api_error(429, "slow down")]
    await client.post(f"/api/shots/{shot_id}/analyses?wait=1", json={})
    row = await listed()
    assert row["analysis_state"] == "failed"
    assert str(row["analysis_error"]).startswith("rate_limited:")

    # A later run that worked supersedes the failure: the error goes with it.
    # The 429 latched the process-wide budget, which is what Settings clears.
    await client.post("/api/llm/rate-limit/reset")
    provider.script = good
    await client.post(f"/api/shots/{shot_id}/analyses?wait=1", json={"force": True})
    row = await listed()
    assert row["analysis_state"] == "ok", row["analysis_error"]
    assert row["analysis_error"] is None


async def test_the_vocabulary_serves_the_analysis_words(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """Nothing in the front end types a coffee word (web/README.md)."""
    _, client, _ = api
    vocab = (await client.get("/api/vocab")).json()["data"]
    assert next(term["value"] for term in vocab["shot_styles"]) == "classic"
    assert "grinder_steps" in [term["value"] for term in vocab["suggestion_units"]]
    assert "puck_prep" in [term["value"] for term in vocab["suggestion_variables"]]
    assert "superseded" in [term["value"] for term in vocab["suggestion_statuses"]]


async def test_a_queued_analysis_answers_before_the_provider_does(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """202 with a `running` row, and the work carries on in the registry.

    The whole point of the move off the request: `docker stop` allows ten
    seconds, and a request holding a two-minute call is killed mid-flight with
    the browser still waiting.
    """
    app, client, provider = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]
    provider.delay = 0.3

    response = await client.post(f"/api/shots/{shot_id}/analyses", json={})

    assert response.status_code == 202
    assert response.json()["data"]["status"] == "running"
    assert analysis_task_name(shot_id) in app.state.tasks.names

    # And it finishes on its own.
    await _settle(app, analysis_task_name(shot_id))
    listed = (await client.get(f"/api/shots/{shot_id}/analyses")).json()["data"]["items"]
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
        client.post(f"/api/shots/{shot_id}/analyses", json={}),
        client.post(f"/api/shots/{shot_id}/analyses", json={}),
    )

    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    await _settle(app, analysis_task_name(shot_id))
    rows = (await client.get(f"/api/shots/{shot_id}/analyses")).json()["data"]["items"]
    assert len(rows) == 1, "two presses must not become two analyses"


async def test_a_batch_skips_a_shot_that_is_already_being_analysed(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """A batch cannot see the registry's per-shot names, so the row is the check."""
    app, client, provider = api
    data = await _build_fixture(app)
    shot_id = data.shots[-1]
    provider.delay = 0.3

    await client.post(f"/api/shots/{shot_id}/analyses", json={})
    batch = await client.post(f"/api/sets/{data.set_id}/analyse?wait=1", json={})

    body = batch.json()["data"]
    assert body["skipped"] == 1
    assert body["requested"] == 1, "the other un-analysed shot, and not this one"
    await _settle(app, analysis_task_name(shot_id))


async def test_a_second_batch_while_one_runs_is_a_409(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, provider = api
    data = await _build_fixture(app)
    provider.delay = 0.3

    first = await client.post(f"/api/sets/{data.set_id}/analyse", json={})
    second = await client.post(f"/api/sets/{data.set_id}/analyse", json={})

    assert first.status_code == 202
    assert second.status_code == 409
    await _settle(app, first.json()["data"]["task"])


async def _add_unanalysed(app: FastAPI, data: Fixture, count: int) -> None:
    """File `count` more shots on the fixture Set, none of them analysed."""
    shots = ShotsRepository(app.state.db)
    sets = SetsRepository(app.state.db)
    for n in range(count):
        shot_id = await shots.insert(
            ShotInsert(
                device_id=f"0009{n:02d}",
                raw_slog=b"fixture",
                started_at=f"2026-03-04T08:{n:02d}:00.000Z",
                duration_ms=28_000,
                profile_version_id=data.profile_version_id,
                final_weight_g=36.0,
            )
        )
        await sets.assign_shot(shot_id, data.version_id)


async def _analysis_count(app: FastAPI) -> int:
    row = await app.state.db.fetch_one("SELECT COUNT(*) AS n FROM shot_analyses")
    return int(row["n"])


async def test_a_batch_over_the_limit_is_refused_until_acknowledged(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """Eleven shots is eleven provider calls: the person sees the number first."""
    app, client, provider = api
    data = await _build_fixture(app)
    # The fixture leaves two shots un-analysed; nine more make eleven.
    await _add_unanalysed(app, data, BATCH_ACKNOWLEDGE_ABOVE - 1)
    before = await _analysis_count(app)

    refused = await client.post(f"/api/sets/{data.set_id}/analyse", json={})

    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "LARGE_BATCH"
    assert error["details"]["field"] == "acknowledge_large_batch"
    assert error["details"]["count"] == BATCH_ACKNOWLEDGE_ABOVE + 1
    assert error["details"]["limit"] == BATCH_ACKNOWLEDGE_ABOVE
    assert app.state.tasks.get(f"analyse_set:{data.set_id}") is None, "nothing queued"
    assert await _analysis_count(app) == before
    assert provider.calls == []

    accepted = await client.post(
        f"/api/sets/{data.set_id}/analyse?wait=1", json={"acknowledge_large_batch": True}
    )

    assert accepted.status_code == 202
    body = accepted.json()["data"]
    assert body["requested"] == BATCH_ACKNOWLEDGE_ABOVE + 1
    assert body["succeeded"] == BATCH_ACKNOWLEDGE_ABOVE + 1


async def test_a_batch_at_the_limit_needs_no_acknowledgement(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await _build_fixture(app)
    await _add_unanalysed(app, data, BATCH_ACKNOWLEDGE_ABOVE - 2)

    batch = await client.post(f"/api/sets/{data.set_id}/analyse?wait=1", json={})

    assert batch.status_code == 202
    assert batch.json()["data"]["requested"] == BATCH_ACKNOWLEDGE_ABOVE


async def test_the_limit_counts_only_what_would_be_queued(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """A shot already being analysed is not a call this batch would make."""
    app, client, provider = api
    data = await _build_fixture(app)
    await _add_unanalysed(app, data, BATCH_ACKNOWLEDGE_ABOVE - 1)
    shot_id = data.shots[-1]
    provider.delay = 0.3

    await client.post(f"/api/shots/{shot_id}/analyses", json={})
    batch = await client.post(f"/api/sets/{data.set_id}/analyse?wait=1", json={})

    assert batch.status_code == 202
    body = batch.json()["data"]
    assert body["skipped"] == 1
    assert body["requested"] == BATCH_ACKNOWLEDGE_ABOVE
    await _settle(app, analysis_task_name(shot_id))


async def test_the_refusal_reports_the_queued_count_however_large(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """The count is what would be queued: not the limit, not the running shot too."""
    app, client, provider = api
    data = await _build_fixture(app)
    # Two un-analysed in the fixture, twenty more; one of them already running.
    await _add_unanalysed(app, data, 2 * BATCH_ACKNOWLEDGE_ABOVE)
    shot_id = data.shots[-1]
    provider.delay = 0.3
    await client.post(f"/api/shots/{shot_id}/analyses", json={})
    queued = 2 * BATCH_ACKNOWLEDGE_ABOVE + 1

    refused = await client.post(f"/api/sets/{data.set_id}/analyse", json={})

    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["count"] == queued

    provider.delay = 0.0
    accepted = await client.post(
        f"/api/sets/{data.set_id}/analyse", json={"acknowledge_large_batch": True}
    )

    assert accepted.status_code == 202
    body = accepted.json()["data"]
    assert (body["requested"], body["skipped"]) == (queued, 1)
    await _settle(app, body["task"])
    await _settle(app, analysis_task_name(shot_id))


async def test_re_running_a_whole_set_is_held_to_the_same_limit(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """Re-analysing every shot is the bigger batch, and is counted the same way."""
    app, client, _ = api
    data = await _build_fixture(app)
    # Six shots in the fixture, two of them un-analysed; five more makes eleven
    # in the Set but only seven un-analysed.
    await _add_unanalysed(app, data, 5)

    refused = await client.post(f"/api/sets/{data.set_id}/analyse", json={"only_unanalysed": False})

    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["count"] == len(data.shots) + 5

    unanalysed = await client.post(f"/api/sets/{data.set_id}/analyse?wait=1", json={})
    assert unanalysed.status_code == 202
    assert unanalysed.json()["data"]["requested"] == 7


async def test_shutdown_cancels_a_running_analysis_and_boot_reconciles_it(
    env: EnvSettings, provider: FakeProvider
) -> None:
    """The property the move exists for, end to end.

    A lifespan teardown cancels the registered task; the `CancelledError` path
    leaves the row `running`; the next boot marks it `interrupted`. Held to a
    real app rather than a unit test because the thing under test is the
    lifespan.
    """
    provider.delay = 30.0
    async with running_app(env) as (app, client):
        _rewire(app, provider)
        data = await _build_fixture(app)
        response = await client.post(f"/api/shots/{data.shots[-1]}/analyses", json={})
        assert response.json()["data"]["status"] == "running"
    # The lifespan has exited: the task was cancelled mid-call.

    async with running_app(env) as (_app, client):
        rows = (await client.get(f"/api/shots/{data.shots[-1]}/analyses")).json()["data"]["items"]
        assert rows[0]["status"] == "interrupted"
        assert "stopped before this analysis finished" in rows[0]["error"]
