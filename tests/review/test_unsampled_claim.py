"""A reading may name a phase that ended before the machine logged a sample of it.

The real shot: the profile's Fill (exit on 2.8 bar) was over before the first sample, and the log
opens in the Ramp. The fault of that shot is the Fill's, so a claim "Fill: skipped" has to be
allowed, placed at the moment the Fill ended, and backed by numbers taken where there are some.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.models import Profile
from gaggiclanker.knowledge.service import FAULT_QUERIES
from gaggiclanker.review.context import build_review_input
from gaggiclanker.sync.derive import derive_shot
from tests.domain.helpers import fill_ended_shot
from tests.llm.conftest import FakeProvider
from tests.review.conftest import reading

CLAIM = "The fill was already over when the first sample was logged."


async def _shot(app: FastAPI) -> int:
    slog, _, profile = fill_ended_shot()
    version, _ = await ProfilesRepository(app.state.db).ensure_version(
        Profile.model_validate(profile)
    )
    derived = derive_shot(slog, slog_to_raw(slog), device_id="000225", profile=profile)
    shot_id = await ShotsRepository(app.state.db).insert(derived.shot, derived.samples)
    await app.state.db.execute(
        "UPDATE shots SET profile_version_id = ? WHERE id = ?", (version.id, shot_id)
    )
    return shot_id


def _answer(evidence: list[dict[str, Any]]) -> str:
    claim = {
        "window": {"phase": "Fill"},
        "fault": "skipped",
        "text": CLAIM,
        "evidence": evidence,
    }
    return json.dumps(reading(claims=[claim], summary="The fill never ran."))


async def test_the_phases_a_reading_may_name_hold_the_skipped_fill_in_profile_order(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, _, _ = api
    shot_id = await _shot(app)

    review = await build_review_input(app.state.db, shot_id)

    assert review.phases == ["Fill", "Ramp", "Decline"]


async def test_a_claim_on_the_skipped_fill_with_evidence_elsewhere_is_stored_supported_and_badged(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, provider = api
    shot_id = await _shot(app)
    provider.script = [
        _answer(
            [
                # Where there are numbers: the pressure the Ramp started from, and the whole shot.
                {"channel": "pressure", "op": "at_start", "window": {"phase": "Ramp"}},
                {"channel": "pressure", "op": "max"},
            ]
        )
    ]

    response = await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})
    assert response.json()["data"]["status"] == "ok"
    detail = (await client.get(f"/api/shots/{shot_id}")).json()["data"]

    [claim] = detail["reviews"][0]["claims"]
    assert (claim["phase"], claim["fault"], claim["supported"]) == ("Fill", "skipped", True)
    # No samples to span: the claim sits at the moment the fill ended.
    assert claim["start_s"] == claim["end_s"] == 0.0
    assert detail["review"]["badge"] == "Fill: skipped"
    assert [e["fault"] for e in detail["review"]["entries"]] == ["skipped"]


async def test_evidence_taken_over_the_skipped_fill_itself_is_absent_and_the_claim_unsupported(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, provider = api
    shot_id = await _shot(app)
    provider.script = [_answer([{"channel": "pressure", "op": "max", "window": {"phase": "Fill"}}])]

    await client.post(f"/api/shots/{shot_id}/reviews?wait=1", json={})
    detail = (await client.get(f"/api/shots/{shot_id}")).json()["data"]

    [claim] = detail["reviews"][0]["claims"]
    assert claim["supported"] is False
    assert claim["evidence"][0]["absent"] == (
        "the Fill ended on its pressure target before the first sample"
    )
    # The page names only what the numbers bear out.
    assert detail["review"]["entries"] == []


async def test_a_shot_that_both_skipped_a_fill_and_stopped_early_gets_both_excerpt_searches(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, _, _ = api
    slog, _, profile = fill_ended_shot()
    # A fourth phase the shot never began, and it ended on its volumetric target: both kinds
    # of skipped warning.
    profile = {**profile, "phases": [*profile["phases"], {**profile["phases"][2], "name": "Tail"}]}
    version, _ = await ProfilesRepository(app.state.db).ensure_version(
        Profile.model_validate(profile)
    )
    derived = derive_shot(slog, slog_to_raw(slog), device_id="000225", profile=profile)
    shot_id = await ShotsRepository(app.state.db).insert(derived.shot, derived.samples)
    await app.state.db.execute(
        "UPDATE shots SET profile_version_id = ? WHERE id = ?", (version.id, shot_id)
    )

    review = await build_review_input(app.state.db, shot_id)

    assert {"fault:skipped", "fault:skipped_at_start"} <= set(review.signals)
    queries = [excerpt["query"] for excerpt in review.excerpts]
    assert FAULT_QUERIES["skipped_at_start"] in queries
    assert FAULT_QUERIES["skipped"] in queries
    assert "STOP_CONDITIONS#multiple-stop-conditions" in {
        excerpt["heading_path"] for excerpt in review.excerpts
    }
