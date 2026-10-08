"""Per-phase metrics: each row of the table is a definition, and a test.

The expected values are worked out here from the parsed samples, each with a
loop of its own, never read back from the output. Two kinds of shot:

* the real fixtures (three `.slog` files and the exported shot), which are
  version 5 logs and so say "Unknown" for how each phase ended;
* the constructed lever shot, a version 7 log built out of a real one whose
  numbers are the constants of ``tests/lever_shot.py``: its cup passes the target
  during the ramp, the shot stops on its weight before the decline begins, and
  the pumped-water counter is reset after the stop.

A machine with no scale or no pressure sensor is the same shot with those
columns zeroed and the flag cleared, as the firmware writes it, never a trace
with nothing in it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

import pytest

from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog, slog_to_raw
from gaggiclanker.domain.models import PHASE_EXIT_REASONS, Sample
from gaggiclanker.domain.slog import Slog, parse_slog
from gaggiclanker.sync.derive import DERIVATION_VERSION, derive_shot
from tests.domain.helpers import EXPORT_FIXTURES, SLOG_FIXTURES
from tests.lever_shot import (
    LEVER_PROFILE,
    RAMP_END_G,
    SOAK_END_G,
    lever_shot,
    without_pressure,
    without_scale,
)

TOLERANCE = 0.006


def _derive(slog: Slog, *, profile: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    return derive_shot(slog, slog_to_raw(slog), device_id="000001", profile=profile, **kwargs)


def phases_of(derived: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = json.loads(derived.shot.phases_json)
    return found


def blob_of(derived: Any) -> dict[str, Any]:
    found: dict[str, Any] = json.loads(derived.shot.diagnostics_json)
    return found


def metrics_by_phase(derived: Any) -> dict[int, dict[str, Any]]:
    return {p["phase_number"]: p.get("metrics", {}) for p in phases_of(derived)}


def real_shots() -> Iterator[tuple[str, Slog]]:
    for path in sorted(SLOG_FIXTURES.glob("*.slog")):
        yield path.stem, parse_slog(path.read_bytes())
    export = json.loads((EXPORT_FIXTURES / "shot-129.json").read_text())
    yield "shot-129", shot_export_to_slog(ShotExport.model_validate(export))
    synthetic = json.loads((EXPORT_FIXTURES / "shot-v7-synthetic.json").read_text())
    yield "shot-v7-synthetic", shot_export_to_slog(ShotExport.model_validate(synthetic))


SHOTS = [*real_shots(), ("lever", lever_shot())]


# ── an independent reading of the samples ───────────────────────────


def groups(slog: Slog) -> dict[int, list[Sample]]:
    found: dict[int, list[Sample]] = defaultdict(list)
    for sample in slog.samples:
        found[sample.phase or 0].append(sample)
    return dict(sorted(found.items()))


def water_rises(slog: Slog) -> dict[int, float] | None:
    """The counter's rise in each phase, reading nothing at or after the first fall."""
    rises: dict[int, float] = {}
    previous_end = 0.0
    last: float | None = None
    stopped = False
    peak = 0.0
    for number, rows in groups(slog).items():
        valid: list[float] = []
        for sample in rows:
            if sample.wp is None:
                continue
            if stopped or (last is not None and sample.wp < last):
                stopped = True
                continue
            valid.append(sample.wp)
            last = sample.wp
            peak = max(peak, sample.wp)
        if valid:
            rises[number] = valid[-1] - previous_end
            previous_end = valid[-1]
    return rises if peak > 0 else None


@pytest.mark.parametrize(("name", "slog"), SHOTS, ids=[n for n, _ in SHOTS])
def test_every_phase_has_its_numbers_as_the_samples_give_them(name: str, slog: Slog) -> None:
    derived = _derive(slog)
    scale = derived.shot.scale_connected
    metrics = metrics_by_phase(derived)
    rises = water_rises(slog)
    previous_weight = 0.0
    saw_drip = False
    drip = next((s for s in slog.samples if (s.pf or 0) > 0), None)
    assert set(metrics) == set(groups(slog)), name
    for number, rows in groups(slog).items():
        got = metrics[number]
        flows = [s.pf for s in rows if s.pf is not None]
        assert got["puck_flow_mean_ml_s"] == pytest.approx(sum(flows) / len(flows), abs=TOLERANCE)
        assert got["puck_flow_peak_ml_s"] == pytest.approx(max(flows), abs=TOLERANCE)
        assert got["pressure_peak_bar"] == pytest.approx(max(s.cp or 0 for s in rows), abs=0.06)
        assert got["pressure_end_bar"] == pytest.approx(rows[-1].cp, abs=0.06)
        assert got["temperature_min_c"] == pytest.approx(min(s.ct or 0 for s in rows), abs=0.06)
        wanted = [s.tt for s in rows if s.tt is not None and s.tt > 0]
        assert got["temperature_target_c"] == pytest.approx(sum(wanted) / len(wanted), abs=0.06)
        if scale:
            end = rows[-1].v or 0.0
            assert got["cup_weight_end_g"] == pytest.approx(end, abs=0.06)
            assert got["cup_weight_gained_g"] == pytest.approx(end - previous_weight, abs=0.06)
            previous_weight = end
            vf = [max(s.vf or 0.0, 0.0) for s in rows]
            assert got["scale_flow_mean_g_s"] == pytest.approx(sum(vf) / len(vf), abs=TOLERANCE)
            assert got["scale_flow_peak_g_s"] == pytest.approx(max(vf), abs=TOLERANCE)
        in_phase = drip is not None and any(s is drip for s in rows)
        saw_drip = saw_drip or in_phase
        if in_phase:
            assert drip is not None
            assert got["first_drip_s"] == pytest.approx((drip.t or 0) / 1000, abs=0.06)
        else:
            assert "first_drip_s" not in got
        if rises is not None and number in rises:
            assert got["water_pumped_ml"] == pytest.approx(rises[number], abs=0.06)
        else:
            assert "water_pumped_ml" not in got
    assert saw_drip == (drip is not None)


# ── how a phase ended: the three shapes of log ──────────────────────


def test_version_7_says_why_each_phase_ended() -> None:
    derived = _derive(lever_shot(), profile=LEVER_PROFILE)
    ended = {n: m["ended_by"] for n, m in metrics_by_phase(derived).items()}
    assert [PHASE_EXIT_REASONS[ended[n]] for n in sorted(ended)] == [
        "Duration",
        "Duration",
        "Volumetric target",
    ]
    shot = blob_of(derived)["metrics"]
    assert shot["per_phase"] is True
    assert shot["exit_reasons"] is True


def test_version_5_reads_unknown_for_every_phase_and_says_so() -> None:
    for name, slog in real_shots():
        if slog.version != 5:
            continue
        derived = _derive(slog)
        metrics = metrics_by_phase(derived)
        assert metrics, name
        assert {m["ended_by"] for m in metrics.values()} == {0}, name
        assert PHASE_EXIT_REASONS[0] == "Unknown"
        assert blob_of(derived)["metrics"]["exit_reasons"] is False


def test_a_log_with_no_phase_table_has_shot_wide_numbers_only() -> None:
    slog = lever_shot()
    slog.header = slog.header.model_copy(update={"version": 4, "transitions": []})
    derived = _derive(slog)
    assert derived.diagnostics_error is None
    assert all("metrics" not in phase for phase in phases_of(derived))
    shot = blob_of(derived)["metrics"]
    assert shot["per_phase"] is False
    assert shot["exit_reasons"] is False
    assert "fast_flow" in shot


# ── the pumped-water counter and its reset ──────────────────────────


def test_water_is_the_counter_rise_over_the_phase_and_stops_at_the_reset() -> None:
    derived = _derive(lever_shot(), profile=LEVER_PROFILE)
    metrics = metrics_by_phase(derived)
    # Half a millilitre a sample from the first soak sample (index 29) to index 127.
    assert metrics[0]["water_pumped_ml"] == 0.0
    assert metrics[1]["water_pumped_ml"] == pytest.approx(0.5 * 40)
    # Nothing after the reset is read: with the tail's 0.6 read as a reading
    # this phase would have gained 0.6 - 20.0.
    assert metrics[2]["water_pumped_ml"] == pytest.approx(0.5 * (127 - 68))


def test_a_counter_that_rises_again_after_its_reset_is_not_read() -> None:
    slog = lever_shot()
    for index in range(130, len(slog.samples)):
        slog.samples[index] = slog.samples[index].model_copy(update={"wp": 90.0 + index})
    assert metrics_by_phase(_derive(slog))[2]["water_pumped_ml"] == pytest.approx(29.5)


def test_water_is_absent_when_the_counter_never_rose() -> None:
    derived = _derive(without_pressure(lever_shot()), has_pressure=False)
    assert all("water_pumped_ml" not in m for m in metrics_by_phase(derived).values())


# ── the lever shot: the numbers it was constructed with ─────────────


def test_the_lever_shots_cup_at_each_phase_end_is_the_constructed_value() -> None:
    metrics = metrics_by_phase(_derive(lever_shot(), profile=LEVER_PROFILE))
    assert [metrics[n]["cup_weight_end_g"] for n in (0, 1, 2)] == [0.0, SOAK_END_G, RAMP_END_G]
    assert [metrics[n]["cup_weight_gained_g"] for n in (0, 1, 2)] == [
        0.0,
        SOAK_END_G,
        pytest.approx(RAMP_END_G - SOAK_END_G),
    ]
    assert metrics[2]["scale_flow_peak_g_s"] == 4.0


def test_the_lever_shots_profile_phases_it_never_began() -> None:
    shot = blob_of(_derive(lever_shot(), profile=LEVER_PROFILE))["metrics"]
    assert shot["profile_phases"] == ["preinfusion", "soak", "ramp", "decline"]
    assert shot["phases_not_reached"] == [{"phase_number": 3, "name": "decline"}]


def test_no_phases_are_claimed_skipped_without_a_profile_or_with_a_shorter_one() -> None:
    bare = blob_of(_derive(lever_shot()))["metrics"]
    assert bare["profile_phases"] is None
    assert bare["phases_not_reached"] == []
    short = {**LEVER_PROFILE, "phases": LEVER_PROFILE["phases"][:2]}
    assert blob_of(_derive(lever_shot(), profile=short))["metrics"]["phases_not_reached"] == []


# ── absent sensors are zeros, never nulls ───────────────────────────


def test_a_machine_with_no_scale_has_no_weight_numbers() -> None:
    slog = without_scale(lever_shot())
    assert all(s.v == 0.0 and s.vf == 0.0 for s in slog.samples)
    derived = _derive(slog, profile=LEVER_PROFILE)
    assert derived.shot.scale_connected is False
    for metrics in metrics_by_phase(derived).values():
        assert not {k for k in metrics if k.startswith(("cup_", "scale_"))}
        assert "puck_flow_mean_ml_s" in metrics  # the rest of the phase is still measured
    assert blob_of(derived)["metrics"]["fast_flow"] is None


def test_a_machine_with_no_pressure_sensor_has_no_pressure_numbers() -> None:
    slog = without_pressure(lever_shot())
    derived = _derive(slog, profile=LEVER_PROFILE, has_pressure=False)
    for metrics in metrics_by_phase(derived).values():
        assert not {k for k in metrics if k.startswith("pressure_")}
        assert "cup_weight_end_g" in metrics
    assert blob_of(derived)["metrics"]["fast_flow"] is None


def test_the_other_sensors_numbers_are_unchanged_by_the_missing_one() -> None:
    full = metrics_by_phase(_derive(lever_shot(), profile=LEVER_PROFILE))
    no_scale = metrics_by_phase(_derive(without_scale(lever_shot()), profile=LEVER_PROFILE))
    for number, metrics in no_scale.items():
        assert metrics["pressure_peak_bar"] == full[number]["pressure_peak_bar"]
        assert metrics["temperature_min_c"] == full[number]["temperature_min_c"]


# ── the derivation version ──────────────────────────────────────────


def test_the_version_is_bumped_for_a_change_to_what_derive_produces() -> None:
    assert DERIVATION_VERSION >= 7
    assert _derive(lever_shot()).shot.derivation_version == DERIVATION_VERSION


def test_a_scale_that_drops_to_zero_at_the_end_leaves_the_last_phase_at_the_final_weight() -> None:
    slog = lever_shot()
    slog.header.final_weight_g = None
    slog.samples = [
        s.model_copy(update={"v": 0.0}) if i >= len(slog.samples) - 3 else s
        for i, s in enumerate(slog.samples)
    ]
    assert slog.volume_g == RAMP_END_G
    metrics = metrics_by_phase(_derive(slog, profile=LEVER_PROFILE))
    assert metrics[2]["cup_weight_end_g"] == RAMP_END_G
    assert metrics[2]["cup_weight_gained_g"] == pytest.approx(RAMP_END_G - SOAK_END_G)
