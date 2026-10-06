"""The reading routes, over the real app.

The app is built the way the rest of the suite builds it — a real file, real
migrations, `httpx.ASGITransport` in process — with one substitution: the LLM
service's provider factory hands back the scripted fake, so a route test spends
no tokens and the thing under test is the route rather than the model.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.reviews import ReviewOutcome, ReviewStart, ShotReviewsRepository
from gaggiclanker.review.service import review_task_name
from gaggiclanker.settings import EnvSettings
from gaggiclanker.tools.registry import registry
from gaggiclanker.tools.scope import ToolScope
from tests.conftest import running_app
from tests.llm.conftest import FakeProvider, api_error
from tests.review.conftest import (
    GOOD_REVIEW,
    build_app_fixture,
    confirm_free_text,
    free_text_result,
    reading,
    rewire,
)

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


async def test_reading_a_shot_over_http(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    shot_id = data.shots[-1]

    response = await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})
    assert response.status_code == 202
    body = response.json()["data"]
    assert body["status"] == "ok"
    assert body["summary"] == GOOD_REVIEW["summary"]
    assert body["prediction_given"] == ""
    assert not {"description", "taste_balance", "taste_body", "taste_confidence"} & set(body)
    assert "input" not in body, "a row as a page reads it carries no input"
    # The claims, each as the page reads it, every one waiting for a person.
    first = body["claims"][0]
    assert {
        "id",
        "position",
        "kind",
        "phase",
        "window_text",
        "start_s",
        "end_s",
        "fault",
        "text",
        "evidence",
        "supported",
        "expectation_id",
        "held",
        "stance",
        "status",
        "reason",
        "answered_at",
    } <= set(first)
    assert (first["kind"], first["status"], first["fault"]) == ("claim", "proposed", "fast flow")
    assert (first["phase"], first["window_text"]) == ("Pressurise", "the Pressurise")
    assert (first["start_s"], first["end_s"]) == (10.0, 27.75)
    assert set(first["evidence"][0]) == {
        "sentence",
        "value",
        "unit",
        "kind",
        "absent",
        "held",
        "limit_text",
    }
    assert first["evidence"][0]["unit"] == "ml/s"
    assert first["evidence"][0]["held"] is True

    listed = await client.get(f"/api/shots/{shot_id}/reviews")
    assert [row["id"] for row in listed.json()["data"]["items"]] == [body["id"]]
    assert len(listed.json()["data"]["items"][0]["claims"]) == 2

    one = (await client.get(f"/api/reviews/{body['id']}")).json()["data"]
    assert one["id"] == body["id"]
    assert one["input"]["shot_id"] == shot_id
    assert one["input"]["shot"].startswith(f"shot {shot_id}")
    assert len(one["claims"]) == 2

    # And the shot detail carries it, so the card needs no second request, with the reading
    # block the shots list serves on every row.
    detail = (await client.get(f"/api/shots/{shot_id}")).json()["data"]
    assert [row["id"] for row in detail["reviews"]] == [body["id"]]
    assert detail["reviews"][0]["claims"][0]["id"] == first["id"]
    assert detail["reading"]["state"] == "read"
    assert detail["reading"]["review_id"] == body["id"]
    assert detail["reading"]["unanswered"] == 2
    assert detail["reading"]["summary"] == GOOD_REVIEW["summary"]
    assert "analyses" not in detail


async def test_only_what_a_person_confirmed_reaches_the_chat(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """End to end: the button's route, a mocked provider, a person's answer, the chat's shot tool.

    Nothing proposed or rejected teaches the chat, the summary never does, and only the count of
    what is still unverified is said.
    """
    app, client, _ = api
    data = await build_app_fixture(app)
    shot_id = data.shots[-1]
    judged = (await client.get(f"/api/shots/{shot_id}")).json()["data"]["judgement"]
    ctx = app.state.chat.tool_context(scope=ToolScope.for_thread(data.set_id), run_id=None)

    async def read_back() -> str:
        outcome = await registry.dispatch(ctx, "get_shot_full", {"shot_id": shot_id})
        assert outcome.ok, outcome.data
        return str(outcome.data["text"])

    reviewed = (await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})).json()
    assert reviewed["data"]["status"] == "ok"
    first, second = reviewed["data"]["claims"]

    unanswered = await read_back()
    assert "[Reading]" in unanswered
    assert "0 claims confirmed, 2 unverified, 0 rejected" in unanswered
    assert first["text"] not in unanswered and second["text"] not in unanswered
    assert GOOD_REVIEW["summary"] not in unanswered

    review_id = reviewed["data"]["id"]
    answered = await app.state.reviews.answer(review_id, first["id"], confirm=True)
    assert answered.refused is None
    await app.state.reviews.answer(review_id, second["id"], confirm=False, reason="not what I saw")

    text = await read_back()
    assert "1 claim confirmed, 0 unverified, 1 rejected" in text
    assert first["text"] in text
    assert "fast flow" in text and "the Pressurise (10-27.75 s)" in text
    assert "mean of the machine's estimate of puck flow over the Pressurise" in text
    assert second["text"] not in text
    assert GOOD_REVIEW["summary"] not in text
    # The person's own judgement is still theirs, untouched by the reading.
    assert (await client.get(f"/api/shots/{shot_id}")).json()["data"]["judgement"] == judged


async def test_a_second_press_after_one_finished_is_a_fresh_review(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """Review again means another reading; the earlier one stays stored."""
    app, client, provider = api
    data = await build_app_fixture(app)
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
    data = await build_app_fixture(app)
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
    data = await build_app_fixture(app)
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
    data = await build_app_fixture(app)
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
    data = await build_app_fixture(app)
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
    data = await build_app_fixture(app)

    response = await client.request(
        method.upper(), path.format(shot=data.shots[-1], set=data.set_id), json={}
    )

    assert response.status_code == 404
    assert provider.calls == []


async def test_the_shots_list_carries_the_reading_block_on_every_row(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    shot_id = data.shots[-1]

    def row_of(listing: httpx.Response) -> dict[str, object]:
        return next(item for item in listing.json()["data"]["items"] if item["id"] == shot_id)

    before = row_of(await client.get("/api/shots"))
    assert before["badge"] == "Review"
    assert before["reading"] == {
        "state": "unread",
        "review_id": None,
        "in_force_id": None,
        "verdict": None,
        "unanswered": 0,
        "reason": None,
        "summary": None,
        "finished_at": None,
    }

    await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})
    after = row_of(await client.get("/api/shots"))
    assert after["badge"] == "No signature"
    assert after["reading"]["state"] == "read"  # type: ignore[index]
    assert after["reading"]["verdict"] == "no_signature"  # type: ignore[index]
    assert after["reading"]["unanswered"] == 2  # type: ignore[index]


@pytest.mark.parametrize("status", ["running", "failed", "interrupted"])
async def test_a_newer_reading_that_did_not_finish_changes_only_the_badge_words(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider], status: str
) -> None:
    """The reading in force is the newest finished one, in the list, the detail and the fields."""
    app, client, provider = api
    data = await build_app_fixture(app)
    shot_id = data.shots[-1]
    expectation = await confirm_free_text(data)
    answer = free_text_result(expectation)
    provider.script = [json.dumps(reading(free_text_results=[answer]))]
    in_force = (await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})).json()["data"]
    assert in_force["status"] == "ok"

    repo = ShotReviewsRepository(app.state.db)
    newer = await repo.start(ReviewStart(shot_id=shot_id))
    if status == "failed":
        await repo.finish(newer, ReviewOutcome(status="failed", error="auth: bad key"))
    elif status == "interrupted":
        assert await repo.reconcile_running() == 1
    words = {"running": "Reading…", "failed": "Failed to run", "interrupted": "Failed to run"}

    row = next(
        item
        for item in (await client.get("/api/shots?sort=review")).json()["data"]["items"]
        if item["id"] == shot_id
    )
    fields = (await client.get(f"/api/shots/{shot_id}/fields")).json()["data"]
    detail = (await client.get(f"/api/shots/{shot_id}")).json()["data"]
    for served in (row, fields):
        assert served["badge"] == words[status]
        assert served["reading"]["state"] == ("running" if status == "running" else "failed")
        assert served["reading"]["review_id"] == newer
        assert served["reading"]["in_force_id"] == in_force["id"]
        # What the reading in force said is still there, unverified, and still waiting.
        assert [(w["fault"], w["unverified"]) for w in served["warnings"]] == [("unstable", True)]
        assert served["reading"]["unanswered"] == 3
    assert fields["reading"]["summary"] == GOOD_REVIEW["summary"]
    assert [c["status"] for c in fields["checks"] if c["kind"] == "free_text"] == ["failed"]
    assert detail["reading"]["unanswered"] == 3
    assert detail["reading"]["in_force_id"] == in_force["id"]
    assert detail["reading"]["review_id"] == newer
    # The in-force reading's claims are the ones the detail lists beside the newer attempt.
    assert [r["id"] for r in detail["reviews"]] == [newer, in_force["id"]]
    # Its claims can still be answered; the attempt that has not finished has none to answer.
    claim = in_force["claims"][0]
    answered = await app.state.reviews.answer(in_force["id"], claim["id"], confirm=True)
    assert answered.refused is None


async def test_the_fields_of_a_discarded_shot_say_it_is_not_readable(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    shot_id = data.shots[-1]
    await app.state.db.execute(
        "UPDATE shot_judgements SET decision = 'discard' WHERE shot_id = ?", (shot_id,)
    )

    fields = (await client.get(f"/api/shots/{shot_id}/fields")).json()["data"]

    assert fields["reading"]["state"] == "not_readable"
    assert fields["reading"]["verdict"] is None


async def test_a_discarded_shot_cannot_be_read(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, provider = api
    data = await build_app_fixture(app)
    await app.state.db.execute(
        "UPDATE shot_judgements SET decision = 'discard' WHERE shot_id = ?", (data.shots[-1],)
    )

    response = await client.post(f"/api/shots/{data.shots[-1]}/reviews", json={})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SHOT_DISCARDED"
    assert provider.calls == []
    assert await app.state.db.fetch_value("SELECT COUNT(*) FROM shot_reviews") == 0


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
        rewire(app, provider)
        data = await build_app_fixture(app)
        response = await client.post(f"/api/shots/{data.shots[-1]}/reviews", json={})
        assert response.json()["data"]["status"] == "running"
    # The lifespan has exited: the task was cancelled mid-call.

    async with running_app(env) as (_app, client):
        rows = (await client.get(f"/api/shots/{data.shots[-1]}/reviews")).json()["data"]["items"]
        assert rows[0]["status"] == "interrupted"
        assert "stopped before this review finished" in rows[0]["error"]
