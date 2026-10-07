"""Rejecting and restoring a review's claims, by a person.

The rules under test: a claim is kept (`confirmed`) from the moment it is written and only a
person rejects it; they may restore it; there is no confirm-all and no waiting state; only the
newest finished review of a shot can be answered (409 for any other); the check and the write
share one transaction, so two answers racing leave one consistent state and each is logged; and a
review and its claims are written together or not at all.
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
from tests.llm.conftest import FakeProvider
from tests.review.conftest import GOOD_REVIEW, Fixture, build_app_fixture, predict, reading

type Api = tuple[FastAPI, httpx.AsyncClient, FakeProvider]


async def _read(client: httpx.AsyncClient, shot_id: int) -> dict[str, object]:
    response = await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})
    assert response.status_code == 202
    body: dict[str, object] = response.json()["data"]
    assert body["status"] == "ok"
    return body


def _url(review: dict[str, object], claim: int = 0) -> str:
    claims = review["claims"]
    assert isinstance(claims, list)
    return f"/api/reviews/{review['id']}/claims/{claims[claim]['id']}"


async def test_a_claim_is_kept_from_the_start_and_a_person_rejects_and_restores_it(
    api: Api,
) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    review = await _read(client, data.shots[-1])
    assert [claim["status"] for claim in review["claims"]] == ["confirmed", "confirmed"]  # type: ignore[attr-defined]

    rejected = await client.patch(_url(review), json={"status": "rejected"})

    assert rejected.status_code == 200
    updated = rejected.json()["data"]
    assert updated["id"] == review["id"]
    first, second = updated["claims"]
    assert (first["status"], second["status"]) == ("rejected", "confirmed")
    assert first["answered_at"] and second["answered_at"] is None
    assert "reason" not in first

    restored = await client.patch(_url(review), json={"status": "confirmed"})
    assert restored.json()["data"]["claims"][0]["status"] == "confirmed"
    again = await client.patch(_url(review), json={"status": "rejected"})
    assert again.json()["data"]["claims"][0]["status"] == "rejected"


async def test_there_is_no_confirm_all_and_no_waiting_state(api: Api) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    review = await _read(client, data.shots[-1])

    gone = await client.post(f"/api/reviews/{review['id']}/claims/confirm-all")
    assert gone.status_code in (404, 405)
    waiting = await client.patch(_url(review), json={"status": "proposed"})
    assert waiting.status_code == 400
    detail = (await client.get(f"/api/shots/{data.shots[-1]}")).json()["data"]
    assert "unanswered" not in detail["reading"]


async def _with_a_stance(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider
) -> dict[str, object]:
    """A review with two claims and a stance on the Set version's prediction."""
    data = await build_app_fixture(app)
    await predict(data)
    stance = {"stance": "partly", "text": "The shot was faster.", "evidence": []}
    provider.script = [json.dumps(reading(prediction=stance))]
    return await _read(client, data.shots[-1])


async def test_a_stance_is_kept_like_any_claim_and_a_person_may_reject_it(api: Api) -> None:
    app, client, provider = api
    review = await _with_a_stance(app, client, provider)

    claims = review["claims"]
    assert [(c["kind"], c["status"]) for c in claims] == [  # type: ignore[attr-defined]
        ("claim", "confirmed"),
        ("claim", "confirmed"),
        ("prediction", "confirmed"),
    ]
    kinds = await app.state.db.fetch_all("SELECT kind FROM v_review_claims")
    assert {row["kind"] for row in kinds} == {"claim", "prediction"}

    await client.patch(_url(review, 2), json={"status": "rejected"})
    kinds = await app.state.db.fetch_all("SELECT kind FROM v_review_claims")
    assert {row["kind"] for row in kinds} == {"claim"}


async def test_only_the_newest_finished_review_can_be_answered(api: Api) -> None:
    app, client, provider = api
    data = await build_app_fixture(app)
    old = await _read(client, data.shots[-1])
    provider.script = [json.dumps(reading(summary="The second reading."))]
    new = await _read(client, data.shots[-1])

    answered = await client.patch(_url(old), json={"status": "rejected"})
    assert answered.status_code == 409
    assert answered.json()["error"]["code"] == "REVIEW_SUPERSEDED"
    # Nothing of the old review moved, and the new one still answers.
    stored = (await client.get(f"/api/reviews/{old['id']}")).json()["data"]
    assert {claim["status"] for claim in stored["claims"]} == {"confirmed"}
    assert (await client.patch(_url(new), json={"status": "rejected"})).status_code == 200


async def test_a_review_that_is_running_or_failed_cannot_be_answered(api: Api) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    repo = ShotReviewsRepository(app.state.db)
    running = await repo.start(ReviewStart(shot_id=data.shots[-1]))
    url = f"/api/reviews/{running}/claims/1"

    assert (await client.patch(url, json={"status": "rejected"})).status_code == 409

    await repo.finish(running, ReviewOutcome(status="failed", error="auth: bad key"))
    assert (await client.patch(url, json={"status": "rejected"})).status_code == 409


async def test_an_answer_names_a_review_and_a_claim_that_exist(api: Api) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    review = await _read(client, data.shots[-1])

    assert (
        await client.patch("/api/reviews/999999/claims/1", json={"status": "rejected"})
    ).status_code == 404
    missing = await client.patch(
        f"/api/reviews/{review['id']}/claims/999999", json={"status": "rejected"}
    )
    assert missing.status_code == 404


async def test_a_claim_of_another_review_is_not_answered_through_this_one(api: Api) -> None:
    app, client, provider = api
    data = await build_app_fixture(app)
    first = await _read(client, data.shots[-1])
    second = await _read(client, data.shots[-1])
    stray = first["claims"][0]["id"]  # type: ignore[index]

    response = await client.patch(
        f"/api/reviews/{second['id']}/claims/{stray}", json={"status": "rejected"}
    )

    assert response.status_code == 404
    stored = (await client.get(f"/api/reviews/{first['id']}")).json()["data"]
    assert stored["claims"][0]["status"] == "confirmed"
    assert provider.calls


@pytest.mark.parametrize(
    "body",
    [
        {"status": "proposed"},
        {"status": "maybe"},
        {},
        {"status": "confirmed", "extra": 1},
        {"status": "rejected", "reason": "no longer a field"},
    ],
)
async def test_an_answer_is_kept_or_rejected_and_nothing_else(
    api: Api, body: dict[str, object]
) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    review = await _read(client, data.shots[-1])

    response = await client.patch(_url(review), json=body)

    assert response.status_code == 400, "the app's validation refusal"


async def test_two_answers_arriving_together_leave_one_consistent_state_and_are_both_logged(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    review = await _read(client, data.shots[-1])

    logged: list[dict[str, object]] = []

    class Recorder:
        def info(self, event: str, **fields: object) -> None:
            logged.append({"event": event, **fields})

    monkeypatch.setattr("gaggiclanker.db.repos.reviews.log", Recorder())
    first, second = await asyncio.gather(
        client.patch(_url(review), json={"status": "confirmed"}),
        client.patch(_url(review), json={"status": "rejected"}),
    )

    assert first.status_code == second.status_code == 200
    final = (await client.get(f"/api/reviews/{review['id']}")).json()["data"]["claims"][0]
    # One of them won, wholly, and the stored state is the last write's.
    assert final["status"] in {"confirmed", "rejected"}
    answered = [e for e in logged if e["event"] == "review_claim_answered"]
    assert sorted(str(e["status"]) for e in answered) == ["confirmed", "rejected"]


async def test_an_answer_is_one_transaction_so_a_failure_after_the_write_undoes_it(
    fixture: Fixture,
) -> None:
    repo = ShotReviewsRepository(fixture.db)
    review = await repo.start(ReviewStart(shot_id=fixture.shots[-1]))
    claims = [ClaimWrite(kind="claim", text=f"claim {n}") for n in range(2)]
    await repo.finish(review, ReviewOutcome(status="ok", summary="s", claims=claims))
    stored = await repo.get(review)
    assert stored is not None
    # The update itself raises: nothing may stay written.
    await fixture.db.execute(
        "CREATE TRIGGER c38_refuse AFTER UPDATE ON review_claims "
        "BEGIN SELECT RAISE(ABORT, 'refused'); END"
    )

    with pytest.raises(Exception, match="refused"):
        await repo.answer(review, stored.claims[0].id, keep=False)

    after = await repo.get(review)
    assert after is not None
    assert [claim.status for claim in after.claims] == ["confirmed", "confirmed"]


async def test_a_review_and_its_claims_are_written_in_one_transaction(fixture: Fixture) -> None:
    """The last claim's insert fails: the review is still `running` and no claim is stored."""
    repo = ShotReviewsRepository(fixture.db)
    review = await repo.start(ReviewStart(shot_id=fixture.shots[-1]))
    await fixture.db.execute(
        "CREATE TRIGGER c36_refuse BEFORE INSERT ON review_claims WHEN NEW.position = 1 "
        "BEGIN SELECT RAISE(ABORT, 'refused'); END"
    )
    claims = [ClaimWrite(kind="claim", text="a"), ClaimWrite(kind="claim", text="b")]

    with pytest.raises(Exception, match="refused"):
        await repo.finish(review, ReviewOutcome(status="ok", summary="s", claims=claims))

    stored = await repo.get(review)
    assert stored is not None
    assert (stored.status, stored.summary, stored.finished_at) == ("running", None, None)
    assert stored.claims == []
    assert await fixture.db.fetch_value("SELECT COUNT(*) FROM review_claims") == 0


async def test_only_a_finished_reading_has_claims(fixture: Fixture) -> None:
    with pytest.raises(ValueError, match="only a finished reading has claims"):
        ReviewOutcome(status="failed", claims=[ClaimWrite(kind="claim", text="a")])


async def test_the_claim_shapes_are_checked_before_they_are_stored() -> None:
    with pytest.raises(ValueError, match="names an expectation"):
        ClaimWrite(kind="free_text", text="a", held=True)
    with pytest.raises(ValueError, match="whether it held"):
        ClaimWrite(kind="free_text", text="a", expectation_id=1)
    with pytest.raises(ValueError, match="stance"):
        ClaimWrite(kind="prediction", text="a")
    with pytest.raises(ValueError, match="names an expectation"):
        ClaimWrite(kind="claim", text="a", expectation_id=1, held=True)


def test_the_good_reading_the_suite_uses_is_two_claims() -> None:
    assert len(GOOD_REVIEW["claims"]) == 2
