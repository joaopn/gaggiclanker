"""Answering a reading's claims: one at a time, or all at once, by a person.

The rules under test: a claim starts `proposed` and only a person moves it; an answer can be
changed; "confirm all" leaves what was already answered alone; only the newest finished reading
of a shot can be answered (409 for any other); the check and the write share one transaction, so
two answers racing leave one consistent state and each is logged; and a reading and its claims
are written together or not at all.
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


async def test_a_person_confirms_a_claim_and_the_review_comes_back_updated(api: Api) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    review = await _read(client, data.shots[-1])

    response = await client.patch(_url(review), json={"status": "confirmed"})

    assert response.status_code == 200
    updated = response.json()["data"]
    assert updated["id"] == review["id"]
    first, second = updated["claims"]
    assert (first["status"], first["reason"]) == ("confirmed", "")
    assert first["answered_at"]
    assert (second["status"], second["answered_at"]) == ("proposed", None)
    detail = (await client.get(f"/api/shots/{data.shots[-1]}")).json()["data"]
    assert detail["reading"]["unanswered"] == 1


async def test_a_claim_is_rejected_with_a_reason_and_the_answer_can_be_changed(api: Api) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    review = await _read(client, data.shots[-1])

    rejected = await client.patch(
        _url(review), json={"status": "rejected", "reason": "  not what I saw  "}
    )
    assert rejected.json()["data"]["claims"][0]["status"] == "rejected"
    assert rejected.json()["data"]["claims"][0]["reason"] == "not what I saw"

    changed = await client.patch(_url(review), json={"status": "confirmed"})
    claim = changed.json()["data"]["claims"][0]
    assert (claim["status"], claim["reason"]) == ("confirmed", "")
    again = await client.patch(_url(review), json={"status": "rejected"})
    assert again.json()["data"]["claims"][0]["status"] == "rejected"


async def test_confirm_all_confirms_what_is_waiting_and_leaves_a_rejection_alone(api: Api) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    review = await _read(client, data.shots[-1])
    await client.patch(_url(review, 1), json={"status": "rejected", "reason": "no"})

    response = await client.post(f"/api/reviews/{review['id']}/claims/confirm-all")

    assert response.status_code == 200
    statuses = [claim["status"] for claim in response.json()["data"]["claims"]]
    assert statuses == ["confirmed", "rejected"]
    detail = (await client.get(f"/api/shots/{data.shots[-1]}")).json()["data"]
    assert detail["reading"]["unanswered"] == 0
    # Confirming all again changes nothing and is not an error.
    again = await client.post(f"/api/reviews/{review['id']}/claims/confirm-all")
    assert again.status_code == 200


async def _with_a_stance(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider
) -> dict[str, object]:
    """A reading with two claims and a stance on the Set version's prediction."""
    data = await build_app_fixture(app)
    await predict(data)
    stance = {"stance": "partly", "text": "The shot was faster.", "evidence": []}
    provider.script = [json.dumps(reading(prediction=stance))]
    return await _read(client, data.shots[-1])


async def test_confirm_all_can_leave_a_kind_of_claim_waiting(api: Api) -> None:
    """The stance is held back until the shot has a decision: Confirm all must not confirm it."""
    app, client, provider = api
    review = await _with_a_stance(app, client, provider)
    shot_id = review["shot_id"]

    response = await client.post(
        f"/api/reviews/{review['id']}/claims/confirm-all", json={"except_kinds": ["prediction"]}
    )

    assert response.status_code == 200
    claims = response.json()["data"]["claims"]
    assert [(c["kind"], c["status"]) for c in claims] == [
        ("claim", "confirmed"),
        ("claim", "confirmed"),
        ("prediction", "proposed"),
    ]
    assert claims[2]["answered_at"] is None
    detail = (await client.get(f"/api/shots/{shot_id}")).json()["data"]
    assert detail["reading"]["unanswered"] == 1, "the held-back stance still counts"
    # Nothing unconfirmed teaches: the stance is not served to the SQL view.
    kinds = await app.state.db.fetch_all("SELECT kind FROM v_review_claims")
    assert {row["kind"] for row in kinds} == {"claim"}
    # A later plain Confirm all (the person has now seen it) confirms the rest.
    later = await client.post(f"/api/reviews/{review['id']}/claims/confirm-all")
    assert [c["status"] for c in later.json()["data"]["claims"]] == ["confirmed"] * 3


async def test_confirm_all_with_no_body_or_an_empty_list_confirms_everything(api: Api) -> None:
    app, client, provider = api
    review = await _with_a_stance(app, client, provider)

    response = await client.post(
        f"/api/reviews/{review['id']}/claims/confirm-all", json={"except_kinds": []}
    )

    assert {c["status"] for c in response.json()["data"]["claims"]} == {"confirmed"}


async def test_an_unknown_kind_is_a_422_that_does_not_echo_it(api: Api) -> None:
    app, client, provider = api
    review = await _with_a_stance(app, client, provider)

    response = await client.post(
        f"/api/reviews/{review['id']}/claims/confirm-all",
        json={"except_kinds": ["prediction", "secret-kind"]},
    )

    assert response.status_code == 422
    assert "secret-kind" not in response.text
    assert response.json()["error"]["details"]["field"] == "except_kinds"
    stored = (await client.get(f"/api/reviews/{review['id']}")).json()["data"]
    assert {c["status"] for c in stored["claims"]} == {"proposed"}, "nothing was confirmed"


async def test_confirm_all_refuses_an_unknown_body_key(api: Api) -> None:
    app, client, provider = api
    review = await _with_a_stance(app, client, provider)
    response = await client.post(
        f"/api/reviews/{review['id']}/claims/confirm-all", json={"except": ["prediction"]}
    )
    assert response.status_code == 400


async def test_only_the_newest_finished_reading_can_be_answered(api: Api) -> None:
    app, client, provider = api
    data = await build_app_fixture(app)
    old = await _read(client, data.shots[-1])
    provider.script = [json.dumps(reading(summary="The second reading."))]
    new = await _read(client, data.shots[-1])

    answered = await client.patch(_url(old), json={"status": "confirmed"})
    assert answered.status_code == 409
    assert answered.json()["error"]["code"] == "REVIEW_SUPERSEDED"
    assert (await client.post(f"/api/reviews/{old['id']}/claims/confirm-all")).status_code == 409
    # Nothing of the old reading moved, and the new one still answers.
    stored = (await client.get(f"/api/reviews/{old['id']}")).json()["data"]
    assert {claim["status"] for claim in stored["claims"]} == {"proposed"}
    assert (await client.patch(_url(new), json={"status": "confirmed"})).status_code == 200


async def test_a_reading_that_is_running_or_failed_cannot_be_answered(api: Api) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    repo = ShotReviewsRepository(app.state.db)
    running = await repo.start(ReviewStart(shot_id=data.shots[-1]))

    response = await client.post(f"/api/reviews/{running}/claims/confirm-all")
    assert response.status_code == 409

    await repo.finish(running, ReviewOutcome(status="failed", error="auth: bad key"))
    assert (await client.post(f"/api/reviews/{running}/claims/confirm-all")).status_code == 409


async def test_an_answer_names_a_review_and_a_claim_that_exist(api: Api) -> None:
    app, client, _ = api
    data = await build_app_fixture(app)
    review = await _read(client, data.shots[-1])

    assert (
        await client.patch("/api/reviews/999999/claims/1", json={"status": "confirmed"})
    ).status_code == 404
    assert (await client.post("/api/reviews/999999/claims/confirm-all")).status_code == 404
    missing = await client.patch(
        f"/api/reviews/{review['id']}/claims/999999", json={"status": "confirmed"}
    )
    assert missing.status_code == 404


async def test_a_claim_of_another_review_is_not_answered_through_this_one(api: Api) -> None:
    app, client, provider = api
    data = await build_app_fixture(app)
    first = await _read(client, data.shots[-1])
    second = await _read(client, data.shots[-1])
    stray = first["claims"][0]["id"]  # type: ignore[index]

    response = await client.patch(
        f"/api/reviews/{second['id']}/claims/{stray}", json={"status": "confirmed"}
    )

    assert response.status_code == 404
    stored = (await client.get(f"/api/reviews/{first['id']}")).json()["data"]
    assert stored["claims"][0]["status"] == "proposed"
    assert provider.calls


@pytest.mark.parametrize(
    "body",
    [
        {"status": "proposed"},
        {"status": "maybe"},
        {},
        {"status": "confirmed", "extra": 1},
        {"status": "rejected", "reason": "x" * 301},
    ],
)
async def test_an_answer_is_confirmed_or_rejected_and_nothing_else(
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
        client.patch(_url(review), json={"status": "rejected", "reason": "no"}),
    )

    assert first.status_code == second.status_code == 200
    final = (await client.get(f"/api/reviews/{review['id']}")).json()["data"]["claims"][0]
    # One of them won, wholly: the status and the reason belong to the same answer.
    assert (final["status"], final["reason"]) in {("confirmed", ""), ("rejected", "no")}
    answered = [e for e in logged if e["event"] == "review_claim_answered"]
    assert sorted(str(e["status"]) for e in answered) == ["confirmed", "rejected"]


async def test_confirm_all_is_one_statement_so_a_failure_in_the_middle_confirms_nothing(
    fixture: Fixture,
) -> None:
    repo = ShotReviewsRepository(fixture.db)
    review = await repo.start(ReviewStart(shot_id=fixture.shots[-1]))
    claims = [ClaimWrite(kind="claim", text=f"claim {n}") for n in range(3)]
    await repo.finish(review, ReviewOutcome(status="ok", summary="s", claims=claims))
    # The third claim cannot be confirmed: whatever the first two did is undone with it.
    await fixture.db.execute(
        "CREATE TRIGGER c36_refuse BEFORE UPDATE ON review_claims WHEN NEW.position = 2 "
        "BEGIN SELECT RAISE(ABORT, 'refused'); END"
    )

    with pytest.raises(Exception, match="refused"):
        await repo.confirm_all(review)

    stored = await repo.get(review)
    assert stored is not None
    assert [claim.status for claim in stored.claims] == ["proposed", "proposed", "proposed"]


async def test_a_reading_and_its_claims_are_written_in_one_transaction(fixture: Fixture) -> None:
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
