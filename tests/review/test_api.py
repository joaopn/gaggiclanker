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

from gaggiclanker.db.repos.reviews import (
    ClaimWrite,
    ReviewOutcome,
    ReviewStart,
    ShotReviewsRepository,
)
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
    # The claims, each as the page reads it, every one kept until a person rejects it.
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
        "answered_at",
    } <= set(first)
    assert "reason" not in first
    assert (first["kind"], first["status"], first["fault"]) == ("claim", "confirmed", "fast flow")
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

    # And the shot detail carries it, so the box needs no second request, with the two blocks the
    # shots list serves on every row.
    detail = (await client.get(f"/api/shots/{shot_id}")).json()["data"]
    assert [row["id"] for row in detail["reviews"]] == [body["id"]]
    assert detail["reviews"][0]["claims"][0]["id"] == first["id"]
    assert detail["review"]["state"] == "reviewed"
    assert detail["review"]["review_id"] == body["id"]
    assert detail["review"]["summary"] == GOOD_REVIEW["summary"]
    assert set(detail["checks"]) == {"badge", "entries"}
    assert "reading" not in detail and "warnings" not in detail and "badge" not in detail
    assert "analyses" not in detail


async def test_only_what_a_person_did_not_reject_reaches_the_chat(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    """End to end: the button's route, a mocked provider, a person's answer, the chat's shot tool.

    Every claim of the review in force teaches the chat unless a person rejected it, and the
    summary never does.
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

    kept = await read_back()
    assert "[Review]" in kept
    assert "2 claims kept, 0 rejected" in kept
    assert first["text"] in kept and second["text"] in kept
    assert GOOD_REVIEW["summary"] not in kept

    rejected = await client.patch(
        f"/api/reviews/{reviewed['data']['id']}/claims/{second['id']}",
        json={"status": "rejected"},
    )
    assert rejected.status_code == 200

    text = await read_back()
    assert "1 claim kept, 1 rejected" in text
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


async def test_the_shots_list_carries_the_checks_and_review_blocks_on_every_row(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    shot_id = data.shots[-1]

    def row_of(listing: httpx.Response) -> dict[str, object]:
        return next(item for item in listing.json()["data"]["items"] if item["id"] == shot_id)

    before = row_of(await client.get("/api/shots"))
    assert before["checks"] == {"badge": None, "entries": []}
    assert before["review"] == {
        "state": "unreviewed",
        "badge": None,
        "entries": [],
        "verdict": None,
        "summary": None,
        "reason": None,
        "review_id": None,
        "in_force_id": None,
    }
    assert not {"badge", "warnings", "reading"} & set(before)

    await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})
    after = row_of(await client.get("/api/shots"))
    # The review's fault is the model's: its claim carries a fault word. The Curve check is
    # exactly what it was.
    assert after["checks"] == before["checks"]
    assert after["review"]["state"] == "reviewed"  # type: ignore[index]
    assert after["review"]["badge"] == "Pressurise: fast flow"  # type: ignore[index]
    assert after["review"]["verdict"] == "entries"  # type: ignore[index]
    assert [e["status"] for e in after["review"]["entries"]] == ["claim"]  # type: ignore[index]


@pytest.mark.parametrize("status", ["running", "failed", "interrupted"])
async def test_a_newer_review_that_did_not_finish_changes_only_the_review_badge_words(
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
    words = {"running": "Reviewing…", "failed": "Failed to run", "interrupted": "Failed to run"}

    row = next(
        item
        for item in (await client.get("/api/shots?sort=review")).json()["data"]["items"]
        if item["id"] == shot_id
    )
    fields = (await client.get(f"/api/shots/{shot_id}/fields")).json()["data"]
    detail = (await client.get(f"/api/shots/{shot_id}")).json()["data"]
    for served in (row, fields, detail):
        review = served["review"]
        assert review["state"] == ("running" if status == "running" else "failed")
        assert review["review_id"] == newer
        assert review["in_force_id"] == in_force["id"]
    for served in (row, fields):
        assert served["review"]["badge"] == words[status]
        # What the review in force said is still there: the free-text failure (critical) and the
        # claim with a fault word.
        assert [e["fault"] for e in served["review"]["entries"]] == ["unstable", "fast flow"]
    assert fields["review"]["summary"] == GOOD_REVIEW["summary"]
    # The Curve check is the deterministic list and holds no free-text answer.
    assert [c["status"] for c in fields["checks"]["items"] if c["kind"] == "free_text"] == [
        "unchecked"
    ]
    # The in-force reading's claims are the ones the detail lists beside the newer attempt.
    assert [r["id"] for r in detail["reviews"]] == [newer, in_force["id"]]
    # Its claims can still be answered; the attempt that has not finished has none to answer.
    claim = in_force["claims"][0]
    answered = await app.state.reviews.answer(in_force["id"], claim["id"], keep=False)
    assert answered.refused is None


async def test_an_unsupported_claim_is_listed_in_the_review_but_not_named_by_its_badge(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    shot_id = data.shots[-1]
    repo = ShotReviewsRepository(app.state.db)
    review_id = await repo.start(ReviewStart(shot_id=shot_id))
    claims = [
        ClaimWrite(kind="claim", text="Backed.", fault="fast flow", phase="Pressurise"),
        ClaimWrite(
            kind="claim", text="Not backed.", fault="slow flow", phase="Pressurise", supported=False
        ),
    ]
    await repo.finish(review_id, ReviewOutcome(status="ok", summary="s", claims=claims))

    detail = (await client.get(f"/api/shots/{shot_id}")).json()["data"]
    assert [c["supported"] for c in detail["reviews"][0]["claims"]] == [True, False]
    assert [e["fault"] for e in detail["review"]["entries"]] == ["fast flow"]
    assert detail["review"]["badge"] == "Pressurise: fast flow"
    row = next(
        item
        for item in (await client.get("/api/shots?sort=review")).json()["data"]["items"]
        if item["id"] == shot_id
    )
    assert row["review"]["badge"] == "Pressurise: fast flow"


async def test_the_fields_of_a_discarded_shot_say_it_is_not_reviewable(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    shot_id = data.shots[-1]
    await app.state.db.execute(
        "UPDATE shot_judgements SET decision = 'discard' WHERE shot_id = ?", (shot_id,)
    )

    fields = (await client.get(f"/api/shots/{shot_id}/fields")).json()["data"]

    assert fields["review"]["state"] == "not_reviewable"
    assert fields["review"]["verdict"] is None
    assert fields["review"]["badge"] is None


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
