"""A machine with no pressure sensor has no puck flow, and no number built on it.

A Standard board writes `pf`, `fl`, `tf` and `wp` as zeros on every sample, and the
log's field mask still lists the columns, so a zero there is never a reading. The
same recording with a pressure sensor keeps every one of these numbers.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.shotinfo import default_tiers, load_shots, render_shot, shot_lines
from gaggiclanker.shotinfo.catalogue import CATALOGUE
from gaggiclanker.shotinfo.fields import shot_fields_of
from gaggiclanker.sync.derive import derive_shot
from tests.lever_shot import lever_shot, without_pressure
from tests.shotinfo.conftest import Archive
from tests.shotinfo.test_evaluate_route import file_lever

FLOW_ITEMS = frozenset(
    {
        "brew_flow",
        "average_flow",
        "peak_flow",
        "total_volume",
        "phase_volume",
        "phase_flow",
        "phase_flow_peak",
        "curve_puck_flow",
        "curve_target_flow",
        "curve_pump_flow",
        "curve_water_pumped",
    }
)
CURVE_COLUMNS = ("puck flow (ml/s)", "target flow (ml/s)", "pump flow (ml/s)", "water pumped (ml)")


def stored_phase_metrics(has_pressure: bool | None) -> list[dict[str, float]]:
    slog = without_pressure(lever_shot()) if has_pressure is False else lever_shot()
    derived = derive_shot(slog, slog_to_raw(slog), device_id="000001", has_pressure=has_pressure)
    return [p["metrics"] for p in json.loads(derived.shot.phases_json or "[]")]


def test_no_puck_flow_is_stored_for_a_shot_with_no_pressure_sensor() -> None:
    for metrics in stored_phase_metrics(False):
        assert not {k for k in metrics if k.startswith("puck_flow")}
        assert "scale_flow_mean_g_s" in metrics  # what the shot did measure stays


def test_a_shot_with_a_pressure_sensor_keeps_its_puck_flow() -> None:
    for metrics in stored_phase_metrics(None):
        assert {"puck_flow_mean_ml_s", "puck_flow_peak_ml_s"} <= set(metrics)


@pytest.mark.parametrize("tier", ["base", "extended", "full"])
async def test_no_tier_renders_a_flow_number_for_a_shot_with_no_pressure_sensor(
    archive: Archive, tier: str
) -> None:
    [facts] = await load_shots(archive.db, [archive.no_pressure], samples=True)
    layout = {item.key: "base" for item in CATALOGUE}
    for tiers in (default_tiers(), layout):
        text = render_shot(facts, tier, tiers, curve_points=20)  # type: ignore[arg-type]
        assert not [c for c in CURVE_COLUMNS if c in text]
    assert shot_lines(facts, FLOW_ITEMS) == []
    document = shot_fields_of(facts)
    served = {f.key for f in document.shot} | {f.key for p in document.phases for f in p.fields}
    assert not served & FLOW_ITEMS


@pytest.mark.parametrize("tier", ["extended", "full"])
async def test_the_same_recording_with_a_pressure_sensor_still_shows_them(
    archive: Archive, tier: str
) -> None:
    [facts] = await load_shots(archive.db, [archive.shot], samples=True)
    text = render_shot(facts, tier, default_tiers(), curve_points=20)  # type: ignore[arg-type]
    assert "puck flow (ml/s)" in text
    assert {line.key for line in shot_lines(facts, FLOW_ITEMS)} >= {
        "brew_flow",
        "average_flow",
        "total_volume",
        "phase_flow",
    }


async def test_the_water_counters_zeros_are_not_a_column_either(archive: Archive) -> None:
    # A version 7 log carries the pumped-water counter; a Standard board writes it as zeros.
    slog = without_pressure(lever_shot())
    derived = derive_shot(
        slog, slog_to_raw(slog), device_id="000777", source="import", has_pressure=False
    )
    shot = await ShotsRepository(archive.db).insert(derived.shot, derived.samples)
    [facts] = await load_shots(archive.db, [shot], samples=True)
    layout = {item.key: "base" for item in CATALOGUE}
    text = render_shot(facts, "base", layout, curve_points=20)  # type: ignore[arg-type]
    assert "water pumped (ml)" not in text
    assert "cup flow (g/s)" in text  # the scale's own column is still there


async def test_a_shot_flagged_with_no_pressure_sensor_has_no_first_drip_though_its_log_has_flow(
    app: FastAPI,
) -> None:
    # The lever shot's log carries a real puck flow from its fast part on. Flagged as a
    # board with no pressure sensor, that flow is not read: no drip, stored, rendered, served
    # or summed into a Set's trends.
    shot, set_id, _ = await file_lever(app, device_id="000907", has_pressure=False, scale=False)
    db = app.state.db
    row = await ShotsRepository(db).get(shot)
    assert row is not None and row.phases
    assert not [p for p in row.phases if "first_drip_s" in p["metrics"]]
    [facts] = await load_shots(db, [shot], samples=True)
    layout = {item.key: "base" for item in CATALOGUE}
    assert shot_lines(facts, frozenset({"first_drip", "phase_first_drip", "brew_flow"})) == []
    assert "first drip" not in render_shot(facts, "full", layout, curve_points=20).lower()  # type: ignore[arg-type]
    counted = await SetsRepository(db).counted_shots(set_id)
    assert [c.first_drip_s for c in counted] == [None]
    assert [c.brew_flow_ml_s for c in counted] == [None]

    # The same bytes read as a machine that has a pressure sensor keep all of them.
    sensor, other_set, _ = await file_lever(app, device_id="000908", scale=False)
    [facts] = await load_shots(db, [sensor], samples=True)
    assert {
        line.key for line in shot_lines(facts, frozenset({"first_drip", "phase_first_drip"}))
    } == {
        "first_drip",
        "phase_first_drip",
    }
    assert [c.first_drip_s for c in await SetsRepository(db).counted_shots(other_set)] == [
        pytest.approx(23.8, abs=0.1)
    ]


async def test_a_standard_board_with_a_scale_has_the_cups_first_drip_and_no_puck_one(
    app: FastAPI,
) -> None:
    # No pressure sensor means no puck flow, so the estimate is absent; the scale still saw
    # the cup fill, so the first drip and the cup flow exist there for the first time.
    shot, set_id, _ = await file_lever(app, device_id="000911", has_pressure=False)
    db = app.state.db
    [facts] = await load_shots(db, [shot], samples=True)
    assert facts.has_scale
    keys = frozenset({"cup_first_drip", "brew_cup_flow", "first_drip", "brew_flow"})
    assert {line.key for line in shot_lines(facts, keys)} == {"cup_first_drip", "brew_cup_flow"}
    [counted] = await SetsRepository(db).counted_shots(set_id)
    assert counted.cup_first_drip_s is not None
    assert counted.cup_flow_g_s is not None
    assert counted.first_drip_s is None
    assert counted.brew_flow_ml_s is None


async def test_a_first_drip_stored_before_the_gate_is_not_read_for_a_shot_flagged_without_sensor(
    app: FastAPI,
) -> None:
    # A shot derived at an earlier version still holds its stored per-phase first drip until
    # the boot re-derive reaches it; what is read of it must already leave it out.
    shot, _, _ = await file_lever(app, device_id="000910", scale=False)
    db = app.state.db
    [before] = await load_shots(db, [shot])
    assert shot_lines(before, frozenset({"phase_first_drip"}))
    await db.execute(
        "UPDATE shots SET diagnostics_json = "
        "json_set(diagnostics_json, '$.has_pressure', json('false')) WHERE id = ?",
        (shot,),
    )
    [after] = await load_shots(db, [shot])
    assert after.has_pressure is False
    assert shot_lines(after, frozenset({"phase_first_drip", "first_drip"})) == []
