"""`POST /api/shots/{id}/evaluate`: the metric language asked about a stored shot.

The shot is the constructed lever shot, filed under a version with an 18 g dose
and a 36 g target, so every expected number is one of the constants of
``tests/lever_shot.py`` or worked out from them.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.models import Profile
from gaggiclanker.sync.derive import derive_shot
from tests.lever_shot import (
    LEVER_PROFILE,
    RAMP_END_G,
    SOAK_END_G,
    TARGET_YIELD_G,
    lever_shot,
    without_scale,
)
from tests.sets.test_api import data

RAMP_CUP_SHARE = {
    "channel": "cup_weight",
    "window": {"phase": "ramp"},
    "op": "at_end",
    "relative_to": "target_yield",
    "compare": {"op": "<=", "value": 0.15},
}


async def file_lever(
    app: FastAPI, *, device_id: str = "000900", scale: bool = True, has_pressure: bool | None = None
) -> tuple[int, int, int]:
    """The lever shot filed under a version: (shot id, set id, version id)."""
    db: Database = app.state.db
    profile, _ = await ProfilesRepository(db).ensure_version(Profile.model_validate(LEVER_PROFILE))
    bean = await BeansRepository(db).create(
        BeanWrite(name="Constructed", roast_level="medium", process="washed")
    )
    grinder = await GrindersRepository(db).create(
        GrinderWrite(name="Constructed", step_unit="numbers")
    )
    sets = SetsRepository(db)
    row = await sets.create(
        SetWrite(name="Constructed lever", bean_id=bean.id, grinder_id=grinder.id),
        SetVersionWrite(profile_version_id=profile.id, dose_g=18.0, target_yield_g=TARGET_YIELD_G),
    )
    slog = lever_shot() if scale else without_scale(lever_shot())
    derived = derive_shot(
        slog,
        slog_to_raw(slog),
        device_id=device_id,
        source="import",
        profile=LEVER_PROFILE,
        has_pressure=has_pressure,
    )
    derived.shot.profile_version_id = profile.id
    shot = await ShotsRepository(db).insert(derived.shot, derived.samples)
    assert row.current_version_id is not None
    assert await sets.assign_shot(shot, row.current_version_id)
    return shot, row.id, row.current_version_id


async def evaluate(
    client: httpx.AsyncClient, shot: int, expressions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    response = await client.post(f"/api/shots/{shot}/evaluate", json={"expressions": expressions})
    assert response.status_code == 200, response.text
    results: list[dict[str, Any]] = data(response)["results"]
    return results


async def test_results_come_back_one_per_expression_in_order(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    shot, _, _ = await file_lever(app)
    results = await evaluate(
        client,
        shot,
        [
            RAMP_CUP_SHARE,
            {"channel": "cup_weight", "op": "at_end", "window": {"phase": "soak"}},
            {"channel": "cup_weight", "op": "at_end", "window": {"phase": "decline"}},
            {"channel": "pressure", "op": "max"},
        ],
    )
    assert [r["unit"] for r in results] == ["share", "g", "g", "bar"]
    assert results[0]["value"] == pytest.approx(round(RAMP_END_G / TARGET_YIELD_G, 3))
    assert results[0]["held"] is False
    assert results[0]["sentence"] == (
        "cup weight at the end of the ramp, as a share of the target yield, at most 0.15"
    )
    assert results[1]["value"] == SOAK_END_G and results[1]["kind"] == "measured"
    assert results[2]["value"] is None and results[2]["absent"] == "phase_not_reached"
    assert results[2]["why"]
    assert results[3]["value"] is not None
    assert json.loads(results[0]["method"])["channel"] == "cup_weight"
    assert (await evaluate(client, shot, [])) == []


async def test_a_value_is_the_one_stored_for_the_phase(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    shot, _, _ = await file_lever(app)
    row = await ShotsRepository(app.state.db).get(shot)
    assert row is not None and row.phases
    phases = row.phases
    for phase in phases:
        numbers = phase["metrics"]
        [cup, gained, peak] = await evaluate(
            client,
            shot,
            [
                {
                    "channel": "cup_weight",
                    "op": "at_end",
                    "window": {"phase_number": phase["phase_number"]},
                },
                {
                    "channel": "cup_weight",
                    "op": "gained",
                    "window": {"phase_number": phase["phase_number"]},
                },
                {
                    "channel": "scale_flow",
                    "op": "max",
                    "window": {"phase_number": phase["phase_number"]},
                },
            ],
        )
        assert cup["value"] == numbers["cup_weight_end_g"]
        assert gained["value"] == numbers["cup_weight_gained_g"]
        assert peak["value"] == numbers["scale_flow_peak_g_s"]


async def test_relative_to_follows_the_filing_and_nothing_stored_moves(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    shot, set_id, _ = await file_lever(app)
    sets = SetsRepository(app.state.db)
    before = await ShotsRepository(app.state.db).get(shot)
    assert before is not None
    [first] = await evaluate(client, shot, [RAMP_CUP_SHARE])
    other = await sets.add_version(
        set_id, SetVersionPatch(target_yield_g=60.0, intent="a longer shot")
    )
    assert other is not None
    assert await sets.assign_shot(shot, other.id)
    [second] = await evaluate(client, shot, [RAMP_CUP_SHARE])
    assert first["value"] == round(RAMP_END_G / 36.0, 3)
    assert second["value"] == round(RAMP_END_G / 60.0, 3)
    assert second["held"] is False
    again = await ShotsRepository(app.state.db).get(shot)
    # Only the filing moved; every derived number is where it was.
    assert again is not None
    assert (again.phases, again.diagnostics) == (before.phases, before.diagnostics)
    [dose, own, unfiled] = await evaluate(
        client,
        shot,
        [
            {**RAMP_CUP_SHARE, "relative_to": "dose"},
            {**RAMP_CUP_SHARE, "relative_to": "final_weight"},
            {**RAMP_CUP_SHARE, "relative_to": "dose", "window": {"phase": "nothing"}},
        ],
    )
    assert dose["value"] == round(RAMP_END_G / 18.0, 3)
    assert own["value"] == 1.0
    assert unfiled["absent"] == "no_such_phase"


async def test_a_shot_with_no_scale_answers_not_recorded_never_zero(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    shot, _, _ = await file_lever(app, scale=False)
    [cup, flow, pressure] = await evaluate(
        client,
        shot,
        [
            RAMP_CUP_SHARE,
            {"channel": "scale_flow", "op": "max"},
            {"channel": "pressure", "op": "max"},
        ],
    )
    assert (cup["value"], cup["absent"], cup["held"]) == (None, "not_recorded", None)
    assert (flow["value"], flow["absent"]) == (None, "not_recorded")
    assert pressure["value"] is not None


async def test_a_shot_that_is_not_in_the_filing_has_no_target_to_divide_by(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    db: Database = app.state.db
    slog = lever_shot()
    derived = derive_shot(slog, slog_to_raw(slog), device_id="000901", source="import")
    shot = await ShotsRepository(db).insert(derived.shot, derived.samples)
    [result] = await evaluate(client, shot, [RAMP_CUP_SHARE])
    # No profile, so no named phases: the filing is not what is missing first.
    assert result["absent"] in ("no_target", "no_such_phase")
    [result] = await evaluate(
        client,
        shot,
        [{"channel": "cup_weight", "op": "at_end", "relative_to": "target_yield"}],
    )
    assert result["absent"] == "no_target"


async def test_a_malformed_expression_is_a_422_that_names_the_field_and_never_echoes_it(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    shot, _, _ = await file_lever(app)
    secret = "SECRET-VALUE-7731"
    response = await client.post(
        f"/api/shots/{shot}/evaluate",
        json={
            "expressions": [
                RAMP_CUP_SHARE,
                {"channel": secret, "op": "mean"},
                {
                    "channel": "pressure",
                    "op": "mean",
                    "window": {"phase": secret, "phase_number": 1},
                },
                {"channel": "pressure", "op": "time_above", "threshold": secret},
                {"channel": "pressure", "op": "mean", secret: 1},
            ]
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert secret not in response.text
    details = body["error"]["details"]
    fields = {d["field"] for d in details}
    assert "expressions.1.channel" in fields
    assert any(f.startswith("expressions.2") for f in fields)
    assert "expressions.3.threshold" in fields
    assert not any(f.startswith("expressions.0") for f in fields)
    assert "expressions.4" in fields  # an unknown key is reported on the object holding it
    # The values sent are never repeated.
    for detail in details:
        assert set(detail) == {"field", "message", "type"}
        assert "7731" not in detail["message"]
    assert secret not in json.dumps([d["message"] for d in details])
    assert "request_id" in body["meta"]


async def test_at_most_fifty_expressions_are_answered(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    shot, _, _ = await file_lever(app)
    one = {"channel": "pressure", "op": "max"}
    assert len(await evaluate(client, shot, [one] * 50)) == 50
    response = await client.post(f"/api/shots/{shot}/evaluate", json={"expressions": [one] * 51})
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "expressions"


async def test_an_unknown_shot_is_a_404_and_a_body_that_is_not_an_object_list_a_400(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    response = await client.post("/api/shots/9999/evaluate", json={"expressions": []})
    assert response.status_code == 404
    shot, _, _ = await file_lever(app)
    assert (await client.post(f"/api/shots/{shot}/evaluate", json={})).status_code == 400
    assert (
        await client.post(f"/api/shots/{shot}/evaluate", json={"expressions": ["mean"]})
    ).status_code == 400


async def test_the_route_reads_and_writes_nothing(app: FastAPI, client: httpx.AsyncClient) -> None:
    shot, _, _ = await file_lever(app)
    db: Database = app.state.db
    tables = ("shots", "shot_samples", "set_versions", "shot_judgements")
    counts = [await db.fetch_one(f"SELECT COUNT(*) AS n FROM {t}") for t in tables]  # noqa: S608
    await evaluate(client, shot, [RAMP_CUP_SHARE])
    after = [await db.fetch_one(f"SELECT COUNT(*) AS n FROM {t}") for t in tables]  # noqa: S608
    assert [dict(c or {}) for c in counts] == [dict(a or {}) for a in after]
