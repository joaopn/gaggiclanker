"""The firmware analyzer's own numbers: machine puck resistance, liquid resistance, water pumped.

The definitions are tests: the golden is what firmware v1.9.0's own analyzer code
computes over the real fixture shots (see ``tests/fixtures/firmware_values``);
the cases below pin each rule of that code on small hand-made streams.
"""

from __future__ import annotations

import json
import math

import pytest

from gaggiclanker.domain.diagnostics import as_sample_dicts
from gaggiclanker.domain.firmware_values import (
    ResistanceStats,
    compute_firmware_values,
    resistance_stats,
    water_pumped_ml,
)
from gaggiclanker.domain.slog import Slog, parse_slog
from tests.domain.helpers import FIXTURES, SLOG_FIXTURES, make_slog, slog_from_export

GOLDEN = json.loads((FIXTURES / "firmware_values" / "golden.json").read_text())["shots"]

#: Values are kept to three decimals: the golden is full precision, so a match is
#: within half a unit of the last kept place.
TOLERANCE = 0.0005 + 1e-9


def _shots() -> dict[str, Slog]:
    shots = {
        path.stem: parse_slog(path.read_bytes(), "000001")
        for path in sorted(SLOG_FIXTURES.glob("*.slog"))
    }
    shots["shot-129"] = slog_from_export("shot-129.json")
    shots["shot-v7-synthetic"] = slog_from_export("shot-v7-synthetic.json")
    return shots


SHOTS = _shots()


def _assert_stats(ours: ResistanceStats | None, theirs: dict[str, float] | None) -> None:
    if theirs is None or theirs["avg"] is None:
        assert ours is None
        return
    assert ours is not None
    assert set(ours) == {"start", "end", "min", "max", "avg"}
    for key in ("start", "end", "min", "max", "avg"):
        assert abs(ours[key] - theirs[key]) <= TOLERANCE, key


def test_the_golden_covers_every_fixture_shot() -> None:
    assert sorted(GOLDEN) == sorted(SHOTS)


@pytest.mark.parametrize("name", sorted(SHOTS))
def test_every_stat_matches_the_firmware_analyzer(name: str) -> None:
    slog = SHOTS[name]
    ours = compute_firmware_values(as_sample_dicts(slog), slog.volume_g)
    theirs = GOLDEN[name]

    _assert_stats(ours["pr"], theirs["whole"]["pr"])
    _assert_stats(ours["lr"], theirs["whole"]["lr"])
    assert [p["phase_number"] for p in ours["phases"]] == sorted(int(n) for n in theirs["phases"])
    for phase in ours["phases"]:
        expected = theirs["phases"][str(phase["phase_number"])]
        _assert_stats(phase["pr"], expected["pr"])
        _assert_stats(phase["lr"], expected["lr"])
    if theirs["water_pumped_ml"] is None:
        assert ours["water_pumped_ml"] is None
    else:
        assert ours["water_pumped_ml"] is not None
        assert abs(ours["water_pumped_ml"] - theirs["water_pumped_ml"]) <= TOLERANCE


def test_the_real_shots_have_a_resistance_and_only_the_v7_one_has_water() -> None:
    for name, slog in SHOTS.items():
        values = compute_firmware_values(as_sample_dicts(slog), slog.volume_g)
        assert values["pr"] is not None, name
        assert values["lr"] is not None, name
        assert (values["water_pumped_ml"] is not None) == (name == "shot-v7-synthetic"), name


def _s(t: int, pr: float | None, cp: float | None = 4.0, **more: float) -> dict[str, float]:
    row: dict[str, float] = {"t": float(t), **more}
    if pr is not None:
        row["pr"] = pr
    if cp is not None:
        row["cp"] = cp
    return row


def test_the_average_is_weighted_by_the_time_since_the_previous_sample() -> None:
    # 1 for 1 s, then 4 for 3 s: the plain mean is 3.0, the weighted 3.25 (the
    # first sample has no predecessor, so it carries no weight itself).
    stats = resistance_stats([_s(0, 1.0), _s(1000, 1.0), _s(4000, 4.0)])

    assert stats is not None
    assert stats["avg"] == pytest.approx((1.0 * 1 + 4.0 * 3) / 4)
    assert (stats["start"], stats["end"], stats["min"], stats["max"]) == (1.0, 4.0, 1.0, 4.0)


def test_a_gap_in_validity_breaks_the_weighting() -> None:
    # The invalid sample in the middle means the next valid one has no valid
    # predecessor: its 2 s gap is not spanned by either side.
    stats = resistance_stats([_s(0, 1.0), _s(1000, 2.0), _s(3000, None), _s(4000, 8.0)])

    assert stats is not None
    assert stats["avg"] == pytest.approx(2.0)  # only 2.0 over the 1 s before it
    assert stats["end"] == 8.0
    assert stats["max"] == 8.0


def test_no_positive_weight_gives_the_first_value() -> None:
    assert resistance_stats([_s(0, 2.5)]) == {
        "start": 2.5,
        "end": 2.5,
        "min": 2.5,
        "max": 2.5,
        "avg": 2.5,
    }
    # Two valid samples at the same instant: still no time, so the first value.
    stats = resistance_stats([_s(0, 2.5), _s(0, 3.5)])
    assert stats is not None and stats["avg"] == 2.5 and stats["end"] == 3.5


@pytest.mark.parametrize("value", [0.0, -1.0, 100.0, 655.35, math.nan, math.inf, None])
def test_a_reading_outside_zero_to_one_hundred_is_not_a_reading(value: float | None) -> None:
    assert resistance_stats([_s(0, value), _s(1000, value)]) is None


def test_just_inside_the_bounds_counts() -> None:
    stats = resistance_stats([_s(0, 0.01), _s(1000, 99.99)])

    assert stats is not None and stats["min"] == 0.01 and stats["max"] == 99.99


def test_all_zeros_is_a_standard_board_and_gives_nothing() -> None:
    samples = [_s(i * 250, 0.0, cp=0.0) for i in range(20)]

    values = compute_firmware_values(samples, None)

    assert values["pr"] is None and values["lr"] is None
    assert values["phases"][0]["pr"] is None


def test_liquid_resistance_is_pr_times_the_square_root_of_pressure() -> None:
    from gaggiclanker.domain.firmware_values import _lr

    assert _lr(_s(0, 2.0, cp=9.0)) == pytest.approx(6.0)
    # Not pr * cp: that would be 18.
    stats = resistance_stats([_s(0, 2.0, cp=9.0), _s(1000, 2.0, cp=9.0)], _lr)
    assert stats is not None and stats["avg"] == pytest.approx(6.0)


def test_liquid_resistance_needs_a_pressure_above_zero_and_a_valid_pr() -> None:
    from gaggiclanker.domain.firmware_values import _lr

    assert _lr(_s(0, 2.0, cp=0.0)) is None
    assert _lr(_s(0, 2.0, cp=None)) is None
    assert _lr(_s(0, 100.0, cp=9.0)) is None
    # A sample without pressure breaks lr's weighting but not pr's.
    samples = [_s(0, 2.0), _s(1000, 2.0, cp=0.0), _s(2000, 2.0)]
    pr = resistance_stats(samples)
    lr = resistance_stats(samples, _lr)
    assert pr is not None and pr["avg"] == 2.0
    assert lr is not None and lr["avg"] == pytest.approx(4.0)  # 2 * sqrt(4), no spanning


def test_the_whole_shot_is_every_sample_and_a_phase_is_its_own() -> None:
    rows = [
        _s(0, 1.0, phase=0.0),
        _s(1000, 1.0, phase=0.0),
        _s(2000, 3.0, phase=1.0),
        _s(3000, 3.0, phase=1.0),
    ]

    values = compute_firmware_values(rows, None)

    assert values["pr"] is not None and values["pr"]["max"] == 3.0
    assert [p["phase_number"] for p in values["phases"]] == [0, 1]
    assert values["phases"][0]["pr"] == {
        "start": 1.0,
        "end": 1.0,
        "min": 1.0,
        "max": 1.0,
        "avg": 1.0,
    }
    assert values["phases"][1]["pr"] == {
        "start": 3.0,
        "end": 3.0,
        "min": 3.0,
        "max": 3.0,
        "avg": 3.0,
    }
    # Across the boundary the whole shot spans 1 -> 3 weighted by the gaps: 1 s of
    # 1.0, 1 s of 3.0, 1 s of 3.0.
    assert values["pr"]["avg"] == pytest.approx((1 + 3 + 3) / 3, abs=TOLERANCE)


def test_water_pumped_is_the_last_reading_less_the_first() -> None:
    rows = [{"t": float(i), "wp": w} for i, w in enumerate([0.0, 2.0, 5.5, 9.4, 9.4])]

    assert water_pumped_ml(rows) == 9.4
    # Not the first, not the maximum of a different sample: the running total's end.
    assert water_pumped_ml([{"wp": 1.0}, {"wp": 4.0}]) == 3.0


def test_water_pumped_is_absent_without_a_complete_increasing_counter() -> None:
    assert water_pumped_ml([]) is None
    assert water_pumped_ml([{"t": 0.0}, {"t": 1.0}]) is None  # a v5 shot: no wp
    assert water_pumped_ml([{"wp": 1.0}, {"t": 1.0}]) is None  # partly logged
    assert water_pumped_ml([{"wp": 5.0}, {"wp": 4.0}]) is None  # went down


def test_water_minus_weight_needs_both() -> None:
    rows = [{"t": 0.0, "wp": 0.0}, {"t": 1.0, "wp": 40.0}]

    assert compute_firmware_values(rows, 36.5)["water_minus_weight_g"] == 3.5
    assert compute_firmware_values(rows, None)["water_minus_weight_g"] is None
    assert compute_firmware_values([{"t": 0.0}], 36.5)["water_minus_weight_g"] is None
    assert compute_firmware_values(rows, None)["water_pumped_ml"] == 40.0


def test_the_synthetic_v7_shot_reports_its_water_and_its_weight_difference() -> None:
    slog = SHOTS["shot-v7-synthetic"]

    values = compute_firmware_values(as_sample_dicts(slog), slog.volume_g)

    assert values["water_pumped_ml"] == 9.4
    if slog.volume_g is not None:
        assert values["water_minus_weight_g"] == pytest.approx(9.4 - slog.volume_g, abs=TOLERANCE)


def test_a_shot_without_a_scale_has_no_weight_difference() -> None:
    slog = make_slog(
        [
            {"t": 0, "cp": 0.0, "pr": 1.0, "wp": 0.0},
            {"t": 250, "cp": 5.0, "pr": 1.0, "wp": 1.0},
        ],
        phases=[(0, 0, "brew")],
    )

    values = compute_firmware_values(as_sample_dicts(slog), slog.volume_g)

    assert values["water_pumped_ml"] == 1.0
    assert values["water_minus_weight_g"] is None


def test_no_pressure_sensor_means_no_resistance() -> None:
    rows = [
        {"t": 0.0, "pr": 1.0, "cp": 4.0, "wp": 0.0},
        {"t": 1000.0, "pr": 1.0, "cp": 4.0, "wp": 5.0},
    ]

    values = compute_firmware_values(rows, 4.0, has_pressure=False)

    assert values["pr"] is None and values["lr"] is None
    # The water is judged by its own counter, not by the sensor gate (the
    # controller's board rules for either are not something a file records).
    assert values["water_pumped_ml"] == 5.0 and values["water_minus_weight_g"] == 1.0
    assert compute_firmware_values(rows, 4.0)["pr"] is not None


def test_a_counter_that_never_rose_is_no_water_at_all() -> None:
    # A board without a dimmed pump sends 0 in every sample: complete and
    # non-decreasing, and still no measurement.
    rows = [{"t": float(i * 250), "wp": 0.0} for i in range(10)]

    values = compute_firmware_values(rows, 36.0, has_pressure=False)

    assert water_pumped_ml(rows) is None
    assert values["water_pumped_ml"] is None
    assert values["water_minus_weight_g"] is None
    assert compute_firmware_values(rows, 36.0)["water_pumped_ml"] is None


def test_a_sample_without_a_time_cannot_anchor_the_next_interval() -> None:
    # As the analyzer: the sample is remembered even though its time is not a
    # number, so the next valid sample spans no interval from it (mutant: keep
    # the earlier anchor, which would weight 4.0 for the whole 2 s).
    stats = resistance_stats([_s(0, 1.0), {"pr": 2.0, "cp": 4.0}, _s(2000, 4.0)])

    assert stats is not None
    assert stats["avg"] == pytest.approx(1.0)
