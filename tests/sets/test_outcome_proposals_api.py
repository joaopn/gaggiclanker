"""The routes a person presses to answer a proposed grade, and what the Set page reads.

Against the real app, like the rest of the Set routes: the envelope, the codes,
`details` that echo nothing, and the one-click case where accepting the next
version records the waiting grade too.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.outcome_proposals import (
    OutcomeProposalsRepository,
    OutcomeProposalWrite,
)
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch
from tests.sets.conftest import make_shot
from tests.sets.test_api import _make_set, data, error


@pytest.fixture
async def bean_id(client: httpx.AsyncClient) -> int:
    response = await client.post(
        "/api/beans",
        json={"name": "Ethiopia Guji", "roast_level": "light", "process": "natural"},
    )
    assert response.status_code == 201
    return int(data(response)["id"])


NOTE = "Time held at 31 s against 28 s; the sourness did not move; the yield held at 36 g."
SECRET = "the maintainer's private reasoning about the grinder"


async def _graded_set(
    client: httpx.AsyncClient, app: FastAPI, bean: int, *, shots: int = 1, name: str = "Guji"
) -> tuple[int, int]:
    """A Set whose v1 has a prediction (written before any shot) and ``shots`` Keep shots."""
    created = await _make_set(client, bean, name=name)
    set_id = int(created["id"])
    version_id = int(created["current_version_id"])
    response = await client.patch(
        f"/api/sets/{set_id}/versions/{version_id}/prediction",
        json={"prediction": "Expect about 30 s.", "compares_to_version_id": None},
    )
    assert response.status_code == 200, response.text
    sets = SetsRepository(app.state.db)
    for number in range(shots):
        shot_id = await make_shot(app.state.db, f"{set_id}00{number:03d}")
        assert await sets.assign_shot(shot_id, version_id)
        await JudgementsRepository(app.state.db).upsert(
            shot_id, JudgementWrite.model_validate({"decision": "keep"})
        )
    return set_id, version_id


async def _propose(
    app: FastAPI, set_id: int, version_id: int, outcome: str = "partly_held", note: str = NOTE
) -> int:
    result = await OutcomeProposalsRepository(app.state.db).create(
        set_id,
        version_id,
        OutcomeProposalWrite.model_validate({"outcome": outcome, "note": note}),
    )
    assert result.proposal is not None
    return result.proposal.id


def _url(set_id: int, proposal_id: int, verb: str) -> str:
    return f"/api/sets/{set_id}/outcome-proposals/{proposal_id}/{verb}"


class TestWhatThePageReads:
    async def test_a_set_with_nothing_waiting_says_so(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, _ = await _graded_set(client, app, bean_id)
        detail = data(await client.get(f"/api/sets/{set_id}"))
        assert detail["outcome_proposal"] is None
        assert detail["versions"][0]["outcome_proposal"] is None

    async def test_a_waiting_grade_rides_with_the_current_version_and_the_set(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id, shots=2)
        proposal_id = await _propose(app, set_id, version_id)

        detail = data(await client.get(f"/api/sets/{set_id}"))

        waiting = detail["outcome_proposal"]
        assert waiting["id"] == proposal_id
        assert (waiting["outcome"], waiting["status"], waiting["counted_shots"]) == (
            "partly_held",
            "proposed",
            2,
        )
        assert waiting["version_label"] == "v1"
        assert detail["versions"][0]["outcome_proposal"]["id"] == proposal_id
        # Words only: the recorded outcome and the track record did not move.
        assert detail["versions"][0]["version"]["outcome"] is None
        assert detail["track_record"]["graded"] == 0

    async def test_the_waiting_grade_is_listed_with_the_answered_ones(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        first = await _propose(app, set_id, version_id, "held")
        await client.post(_url(set_id, first, "dismiss"), json={})
        second = await _propose(app, set_id, version_id, "failed")

        items = data(await client.get(f"/api/sets/{set_id}/outcome-proposals"))["items"]
        assert [(item["id"], item["status"]) for item in items] == [
            (second, "proposed"),
            (first, "dismissed"),
        ]

    async def test_an_unknown_set_is_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/api/sets/999/outcome-proposals")).status_code == 404


class TestAnswering:
    async def test_accept_records_the_outcome_on_the_version(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        proposal_id = await _propose(app, set_id, version_id, "held")

        body = data(await client.post(_url(set_id, proposal_id, "accept")))

        assert body["proposal"]["status"] == "accepted"
        assert body["version"]["outcome"] == "held"
        assert body["version"]["outcome_note"] == NOTE
        detail = data(await client.get(f"/api/sets/{set_id}"))
        assert detail["outcome_proposal"] is None
        assert detail["versions"][0]["version"]["outcome_state"] == "held"
        assert detail["track_record"]["graded"] == 1

    async def test_change_records_the_persons_outcome(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        proposal_id = await _propose(app, set_id, version_id, "held")

        body = data(
            await client.post(_url(set_id, proposal_id, "change"), json={"outcome": "failed"})
        )

        assert body["proposal"]["status"] == "changed"
        assert body["proposal"]["recorded_outcome"] == "failed"
        assert body["proposal"]["outcome"] == "held"
        assert body["version"]["outcome"] == "failed"

    async def test_change_refuses_an_outcome_outside_the_vocabulary(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        proposal_id = await _propose(app, set_id, version_id)
        for bad in ("open", "no_prediction", "great"):
            response = await client.post(_url(set_id, proposal_id, "change"), json={"outcome": bad})
            assert response.status_code == 400

    async def test_dismiss_records_nothing(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        proposal_id = await _propose(app, set_id, version_id)

        body = data(
            await client.post(_url(set_id, proposal_id, "dismiss"), json={"note": "one shot"})
        )

        assert body["proposal"]["status"] == "dismissed"
        assert body["proposal"]["decision_note"] == "one shot"
        assert body["version"] is None
        detail = data(await client.get(f"/api/sets/{set_id}"))
        assert detail["versions"][0]["version"]["outcome"] is None

    @pytest.mark.parametrize(
        ("verb", "payload"),
        [("accept", None), ("change", {"outcome": "failed"}), ("dismiss", {})],
    )
    async def test_an_answered_proposal_is_409_with_its_own_code(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
        verb: str,
        payload: dict[str, Any] | None,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        proposal_id = await _propose(app, set_id, version_id)
        await client.post(_url(set_id, proposal_id, "dismiss"), json={})

        response = await client.post(_url(set_id, proposal_id, verb), json=payload)

        assert response.status_code == 409
        assert error(response)["code"] == "OUTCOME_PROPOSAL_DECIDED"

    async def test_a_superseded_proposal_is_409_too(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        first = await _propose(app, set_id, version_id, "held")
        await _propose(app, set_id, version_id, "failed")
        response = await client.post(_url(set_id, first, "accept"))
        assert response.status_code == 409
        assert error(response)["code"] == "OUTCOME_PROPOSAL_DECIDED"

    async def test_an_unknown_proposal_and_another_sets_are_404(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        other_id, _ = await _graded_set(client, app, bean_id, name="Other")
        proposal_id = await _propose(app, set_id, version_id)
        assert (await client.post(_url(set_id, 9999, "accept"))).status_code == 404
        assert (await client.post(_url(other_id, proposal_id, "accept"))).status_code == 404

    async def test_a_grade_that_can_no_longer_be_recorded_is_409_and_still_waiting(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        proposal_id = await _propose(app, set_id, version_id)
        await app.state.db.execute("UPDATE shot_judgements SET decision = 'discard'")

        response = await client.post(_url(set_id, proposal_id, "accept"))

        assert response.status_code == 409
        assert error(response)["code"] == "NOTHING_TO_GRADE"
        # Names the version it is about, never "Version None".
        assert "v1 has no shot" in error(response)["message"]
        assert "None" not in error(response)["message"]
        waiting = data(await client.get(f"/api/sets/{set_id}"))["outcome_proposal"]
        assert waiting is not None and waiting["id"] == proposal_id

    async def test_details_never_echo_what_was_sent_or_written(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        proposal_id = await _propose(app, set_id, version_id, note=SECRET)
        await client.post(_url(set_id, proposal_id, "dismiss"), json={"note": SECRET})

        for verb, payload in (
            ("accept", None),
            ("change", {"outcome": "failed", "note": SECRET}),
            ("dismiss", {"note": SECRET}),
        ):
            response = await client.post(_url(set_id, proposal_id, verb), json=payload)
            assert response.status_code == 409
            assert SECRET not in response.text
        bad = await client.post(
            _url(set_id, proposal_id, "change"), json={"outcome": SECRET, "note": SECRET}
        )
        assert bad.status_code == 400
        assert SECRET not in bad.text

    async def test_the_answer_is_the_envelope(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        proposal_id = await _propose(app, set_id, version_id)
        body = (await client.post(_url(set_id, proposal_id, "accept"))).json()
        assert set(body) == {"ok", "data", "meta"}
        assert "request_id" in body["meta"]
        refused = (await client.post(_url(set_id, proposal_id, "accept"))).json()
        assert set(refused["error"]) >= {"code", "message", "details"}


class TestTheNextVersionCardSaysItRecordsTheGrade:
    async def _next_version(self, app: FastAPI, set_id: int) -> int:
        result = await SetProposalsRepository(app.state.db).create(
            set_id,
            ProposalWrite(
                patch=SetVersionPatch(grind_setting="21"),
                reason="one click finer, chasing the sourness out",
                prediction="Compared to v1: two to four seconds longer and less sour.",
            ),
        )
        assert result.proposal is not None, result.refused
        return result.proposal.id

    async def test_the_card_carries_the_waiting_grade_of_its_base_version(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        grade_id = await _propose(app, set_id, version_id, "partly_held")
        await self._next_version(app, set_id)

        proposal = data(await client.get(f"/api/sets/{set_id}"))["proposal"]

        assert proposal["records_outcome"]["id"] == grade_id
        assert proposal["records_outcome"]["outcome"] == "partly_held"
        assert proposal["records_outcome"]["version_label"] == "v1"

    async def test_a_recorded_outcome_means_no_line_and_the_grade_stays_waiting(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        grade_id = await _propose(app, set_id, version_id, "partly_held")
        proposal_id = await self._next_version(app, set_id)
        await client.put(
            f"/api/sets/{set_id}/versions/{version_id}/outcome",
            json={"outcome": "failed", "note": "my own words"},
        )

        detail = data(await client.get(f"/api/sets/{set_id}"))
        assert detail["proposal"]["records_outcome"] is None

        await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept", json={})
        after = data(await client.get(f"/api/sets/{set_id}"))
        recorded = next(v for v in after["versions"] if v["version"]["id"] == version_id)
        assert recorded["version"]["outcome"] == "failed"
        assert recorded["version"]["outcome_note"] == "my own words"
        assert after["outcome_proposal"] is None  # the card is for v1, no longer current
        assert recorded["outcome_proposal"]["id"] == grade_id

    async def test_no_waiting_grade_means_no_line(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        created = await _make_set(client, bean_id)
        await self._next_version(app, int(created["id"]))
        proposal = data(await client.get(f"/api/sets/{created['id']}"))["proposal"]
        assert proposal["records_outcome"] is None

    async def test_accepting_it_records_the_grade_and_appends_the_version(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        grade_id = await _propose(app, set_id, version_id, "held")
        proposal_id = await self._next_version(app, set_id)

        body = data(
            await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept", json={})
        )

        assert body["version"]["version_label"] == "v1.1"
        assert body["proposal"]["records_outcome"] is None
        detail = data(await client.get(f"/api/sets/{set_id}"))
        recorded = next(v for v in detail["versions"] if v["version"]["id"] == version_id)
        assert recorded["version"]["outcome"] == "held"
        listed = data(await client.get(f"/api/sets/{set_id}/outcome-proposals"))["items"]
        assert [(i["id"], i["status"]) for i in listed] == [(grade_id, "accepted")]

    async def test_a_dismissed_grade_is_refused_as_an_open_outcome(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        grade_id = await _propose(app, set_id, version_id, "held")
        proposal_id = await self._next_version(app, set_id)
        await client.post(_url(set_id, grade_id, "dismiss"), json={})

        response = await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept", json={})

        assert response.status_code == 409
        assert error(response)["code"] == "PROPOSAL_OUTCOME_OPEN"

    async def test_a_grade_that_cannot_be_recorded_refuses_the_version_with_its_own_code(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        bean_id: int,
    ) -> None:
        set_id, version_id = await _graded_set(client, app, bean_id)
        await _propose(app, set_id, version_id, "held")
        proposal_id = await self._next_version(app, set_id)
        await app.state.db.execute("UPDATE shot_judgements SET decision = 'discard'")

        response = await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept", json={})

        assert response.status_code == 409
        assert error(response)["code"] == "PROPOSAL_GRADE_UNRECORDABLE"
