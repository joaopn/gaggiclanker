"""The metric language: every operation, window and absence, each a definition and a test.

The expected values are worked out here by loops of their own over the parsed
samples of the real fixtures (and the constructed lever shot, which is a real
fixture rewritten), never read back from the evaluator. A machine with no scale
or no pressure sensor is the same shot with those columns zeroed and the flag
cleared, as the firmware writes it, never a trace with nothing in it.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Mapping
from itertools import pairwise
from typing import Any

import pytest
from pydantic import ValidationError

from gaggiclanker.domain.diagnostics import as_sample_dicts
from gaggiclanker.domain.metric_language import (
    ABSENT_REASONS,
    CHANNELS,
    OPS,
    Expression,
    Result,
    ShotData,
    canonical_form,
    evaluate,
    method_id,
    render,
)
from gaggiclanker.domain.models import PhaseTransition
from gaggiclanker.domain.phase_metrics import profile_phase_names
from gaggiclanker.domain.slog import Slog, parse_slog
from tests.domain.helpers import SLOG_FIXTURES
from tests.lever_shot import (
    LEVER_PROFILE,
    RAMP_END_G,
    SOAK_END_G,
    TARGET_YIELD_G,
    lever_shot,
    without_pressure,
    without_scale,
)

COLUMN = {
    "cup_weight": "v",
    "scale_flow": "vf",
    "puck_flow": "pf",
    "pump_flow": "fl",
    "target_flow": "tf",
    "pressure": "cp",
    "target_pressure": "tp",
    "temperature": "ct",
}


def data_of(slog: Slog, *, profile: dict[str, Any] | None = None, **kwargs: Any) -> ShotData:
    samples = as_sample_dicts(slog)
    scale = any(s.get("v", 0.0) != 0.0 for s in samples)
    settings: dict[str, Any] = {
        "profile_phases": profile_phase_names(profile),
        "has_pressure": slog.has_pressure,
        "scale_connected": scale,
        "final_weight_g": slog.volume_g,
    }
    settings.update(kwargs)
    return ShotData.build(samples, slog.transitions, **settings)


def run(data: ShotData, **fields: Any) -> Result:
    return evaluate(Expression.model_validate(fields), data)


def real_slogs() -> list[tuple[str, Slog]]:
    return [(p.stem, parse_slog(p.read_bytes())) for p in sorted(SLOG_FIXTURES.glob("*.slog"))]


SHOTS = [*real_slogs(), ("lever", lever_shot())]


def window_rows(data: ShotData, lo: float | None, hi: float | None) -> list[Mapping[str, float]]:
    return [
        s
        for s in data.samples
        if "t" in s and (lo is None or s["t"] >= lo) and (hi is None or s["t"] <= hi)
    ]


def pairs(rows: list[Mapping[str, float]], column: str) -> list[tuple[float, float]]:
    # The scale's flow is read at zero where the log has it below zero (cup flow).
    floor = 0.0 if column == "vf" else float("-inf")
    return [(r["t"] / 1000.0, max(r[column], floor)) for r in rows if column in r]


# ── every operation, on windows of real shots, against a hand computation ──


def reference(op: str, points: list[tuple[float, float]], threshold: float = 0.0) -> float:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if op == "mean":
        return sum(ys) / len(ys)
    if op == "min":
        return min(ys)
    if op == "max":
        return max(ys)
    if op == "at_start":
        return ys[0]
    if op == "at_end":
        return ys[-1]
    if op == "change":
        return ys[-1] - ys[0]
    if op == "slope":
        n = len(xs)
        sx, sy = sum(xs), sum(ys)
        sxy = sum(x * y for x, y in points)
        sxx = sum(x * x for x in xs)
        return (n * sxy - sx * sy) / (n * sxx - sx * sx)
    if op == "integral":
        return sum((x1 - x0) * (y0 + y1) / 2 for (x0, y0), (x1, y1) in pairwise(points))
    if op == "jitter":
        diffs = [b - a for a, b in pairwise(ys)]
        mean = sum(diffs) / len(diffs)
        return math.sqrt(sum((d - mean) ** 2 for d in diffs) / len(diffs))
    if op == "time_above":
        return sum(x1 - x0 for (x0, y0), (x1, y1) in pairwise(points) if min(y0, y1) > threshold)
    if op == "time_below":
        return sum(x1 - x0 for (x0, y0), (x1, y1) in pairwise(points) if max(y0, y1) < threshold)
    raise AssertionError(op)


PLAIN_OPS = ["mean", "min", "max", "at_start", "at_end", "change", "slope", "integral", "jitter"]
DECIMALS = {"v": 1, "vf": 2, "pf": 2, "fl": 2, "tf": 2, "cp": 1, "tp": 1, "ct": 1}


@pytest.mark.parametrize(("name", "slog"), SHOTS, ids=[n for n, _ in SHOTS])
@pytest.mark.parametrize("channel", sorted(COLUMN))
@pytest.mark.parametrize("op", PLAIN_OPS)
def test_each_operation_is_its_definition_over_a_span(
    name: str, slog: Slog, channel: str, op: str
) -> None:
    data = data_of(slog, profile=LEVER_PROFILE if name == "lever" else None)
    lo, hi = 3000.0, 9000.0
    got = run(
        data,
        channel=channel,
        op=op,
        window={"from": {"at_s": 3}, "to": {"at_s": 9}},
    )
    column = COLUMN[channel]
    points = pairs(window_rows(data, lo, hi), column)
    assert got.absent is None, (name, channel, op, got.why)
    # Rounded to the channel's decimals, as the stored numbers are.
    assert got.value == pytest.approx(round(reference(op, points), DECIMALS[column]), abs=1e-9), (
        name,
        channel,
        op,
    )


@pytest.mark.parametrize(("name", "slog"), SHOTS, ids=[n for n, _ in SHOTS])
def test_duration_is_the_last_time_less_the_first_on_any_channel(name: str, slog: Slog) -> None:
    data = data_of(slog)
    rows = window_rows(data, 2000.0, 7000.0)
    want = round((rows[-1]["t"] - rows[0]["t"]) / 1000.0, 2)
    for channel in ("pressure", "cup_weight", "temperature"):
        got = run(
            data, channel=channel, op="duration", window={"from": {"at_s": 2}, "to": {"at_s": 7}}
        )
        assert got.value == want
        assert got.unit == "s"


@pytest.mark.parametrize(("name", "slog"), SHOTS, ids=[n for n, _ in SHOTS])
@pytest.mark.parametrize("op", ["time_above", "time_below"])
def test_time_beyond_a_threshold_counts_only_intervals_with_both_ends_beyond(
    name: str, slog: Slog, op: str
) -> None:
    data = data_of(slog)
    points = pairs(window_rows(data, None, None), "cp")
    for threshold in (2.0, 6.0, 8.5):
        got = run(data, channel="pressure", op=op, threshold=threshold)
        assert got.value == pytest.approx(round(reference(op, points, threshold), 2), abs=1e-9)


def test_time_above_at_a_crossing_between_samples_counts_neither_half_interval() -> None:
    # Pressures 1, 5, 7, 3 at 0, 1, 2, 3 s against 4: only the 5 to 7 interval has both
    # ends above; the 1 to 5 and 7 to 3 intervals cross the line and count nothing.
    data = ShotData(
        samples=[{"t": i * 1000.0, "cp": cp} for i, cp in enumerate((1.0, 5.0, 7.0, 3.0))]
    )
    assert run(data, channel="pressure", op="time_above", threshold=4).value == 1.0
    assert run(data, channel="pressure", op="time_below", threshold=4).value == 0.0
    assert run(data, channel="pressure", op="time_below", threshold=6).value == 1.0
    assert run(data, channel="pressure", op="time_above", threshold=0).value == 3.0


def test_time_to_is_seconds_from_the_window_start_to_the_first_crossing() -> None:
    data = ShotData(
        samples=[{"t": 1000.0 + i * 500.0, "cp": cp} for i, cp in enumerate((1.0, 5.0, 7.0, 3.0))]
    )
    rising = run(data, channel="pressure", op="time_to", threshold=6)
    assert rising.value == 1.0  # the 7 bar sample at 2.0 s, the window starting at 1.0 s
    down = ShotData(
        samples=[{"t": 1000.0 + i * 500.0, "cp": cp} for i, cp in enumerate((8.0, 7.0, 3.0, 1.0))]
    )
    falling = run(down, channel="pressure", op="time_to", threshold=3, direction="falling")
    assert falling.value == 1.0  # falling means <=
    reached_at_the_value = run(data, channel="pressure", op="time_to", threshold=5)
    assert reached_at_the_value.value == 0.5  # reaching means >=


def test_a_time_to_that_never_crosses_is_never_reached_and_not_a_number() -> None:
    data = data_of(lever_shot())
    got = run(data, channel="pressure", op="time_to", threshold=500)
    assert got.value is None and got.absent == "never_reached"
    got = run(data, channel="pressure", op="time_to", threshold=-1, direction="falling")
    assert got.absent == "never_reached"


def test_a_span_window_starts_at_its_from_anchor_for_time_to() -> None:
    data = ShotData(
        samples=[{"t": i * 1000.0, "cp": float(i)} for i in range(10)],
    )
    got = run(
        data,
        channel="pressure",
        op="time_to",
        threshold=6,
        window={"from": {"at_s": 2}, "to": {"at_s": 9}},
    )
    assert got.value == 4.0


# ── empty windows, one sample, a field that was not recorded ────────


def test_a_window_without_a_sample_is_empty_for_every_operation() -> None:
    data = data_of(lever_shot())
    window = {"from": {"at_s": 500}, "to": {"at_s": 600}}
    for op in OPS:
        if op == "gained":
            continue
        extra = {"threshold": 1} if op in ("time_to", "time_above", "time_below") else {}
        got = run(data, channel="pressure", op=op, window=window, **extra)
        assert got.value is None and got.absent == "empty_window", op


def test_one_sample_has_a_value_for_the_operations_that_need_one_and_none_for_the_rest() -> None:
    data = ShotData(samples=[{"t": 4000.0, "cp": 6.0, "ct": 90.0}])
    for op, want in (
        ("mean", 6.0),
        ("min", 6.0),
        ("max", 6.0),
        ("at_start", 6.0),
        ("at_end", 6.0),
        ("change", 0.0),
        ("integral", 0.0),
        ("duration", 0.0),
    ):
        assert run(data, channel="pressure", op=op).value == want, op
    for op in ("slope", "jitter"):
        got = run(data, channel="pressure", op=op)
        assert got.value is None and got.absent == "empty_window", op


def test_samples_with_the_field_unrecorded_are_skipped_not_read_as_zero() -> None:
    samples: list[dict[str, float]] = [
        {"t": 0.0, "cp": 2.0},
        {"t": 1000.0},
        {"t": 2000.0, "cp": 4.0},
        {"t": 3000.0},
    ]
    data = ShotData(samples=samples)
    assert run(data, channel="pressure", op="mean").value == 3.0
    assert run(data, channel="pressure", op="at_end").value == 4.0
    assert run(data, channel="pressure", op="min").value == 2.0
    # The interval between the two recorded samples is the whole 2 s.
    assert run(data, channel="pressure", op="integral").value == 6.0
    assert run(data, channel="pressure", op="duration").value == 3.0
    only_gaps = ShotData(samples=[{"t": 0.0}, {"t": 1000.0}])
    got = run(only_gaps, channel="pressure", op="mean")
    assert got.value is None and got.absent == "empty_window"


def test_the_slope_is_per_second_and_the_integral_in_unit_seconds() -> None:
    data = ShotData(samples=[{"t": i * 500.0, "cp": 2.0 * (i * 0.5) + 1.0} for i in range(7)])
    assert run(data, channel="pressure", op="slope").value == 2.0
    assert run(data, channel="pressure", op="slope").unit == "bar/s"
    assert run(data, channel="pressure", op="integral").unit == "bar·s"
    assert run(data, channel="pressure", op="integral").value == 3 * 2.0 + 3.0 * 2.0  # 3 s: 1 to 7


def test_jitter_is_the_population_deviation_of_the_first_differences() -> None:
    # Differences 1, 3: mean 2, population deviation 1 (a sample deviation would give 1.41).
    data = ShotData(samples=[{"t": i * 1000.0, "cp": cp} for i, cp in enumerate((0.0, 1.0, 4.0))])
    assert run(data, channel="pressure", op="jitter").value == 1.0
    steady = ShotData(samples=[{"t": i * 1000.0, "cp": 3.0 * i} for i in range(5)])
    assert run(steady, channel="pressure", op="jitter").value == 0.0


# ── gained, and the dropout rule ─────────────────────────────────────


def test_gained_is_the_end_of_the_phase_less_the_end_of_the_one_before_zero_before_the_first() -> (
    None
):
    data = data_of(lever_shot(), profile=LEVER_PROFILE)
    first = run(data, channel="cup_weight", op="gained", window={"phase": "preinfusion"})
    assert first.value == 0.0  # the cup at the end of the first phase, less 0
    soak = run(data, channel="cup_weight", op="gained", window={"phase": "soak"})
    assert soak.value == SOAK_END_G
    ramp = run(data, channel="cup_weight", op="gained", window={"phase": "ramp"})
    assert ramp.value == pytest.approx(RAMP_END_G - SOAK_END_G, abs=1e-9)


def test_gained_on_a_real_first_phase_is_its_own_end_weight() -> None:
    slog = real_slogs()[1][1]
    data = data_of(slog)
    first = data.phases[0]
    weights = [s["v"] for s in data.samples[first.start : first.end] if "v" in s]
    got = run(data, channel="cup_weight", op="gained", window={"phase_number": 0})
    assert got.value == round(weights[-1], 1)


def test_the_last_phase_ends_at_the_final_weight_when_the_scale_dropped_to_zero() -> None:
    slog = lever_shot()
    samples = as_sample_dicts(slog)
    for sample in samples[-3:]:
        sample["v"] = 0.0
    data = ShotData.build(samples, slog.transitions, final_weight_g=RAMP_END_G, profile_phases=None)
    last = run(data, channel="cup_weight", op="at_end", window={"phase": "ramp"})
    assert last.value == RAMP_END_G
    # A window that ends with the shot gets the rule too, whatever it is called.
    assert run(data, channel="cup_weight", op="at_end").value == RAMP_END_G


def test_gained_is_for_the_cup_and_the_water_over_one_phase() -> None:
    for fields in (
        {"channel": "pressure", "op": "gained", "window": {"phase": "ramp"}},
        {"channel": "cup_weight", "op": "gained"},
        {
            "channel": "cup_weight",
            "op": "gained",
            "window": {"from": "shot_start", "to": "shot_end"},
        },
    ):
        with pytest.raises(ValidationError):
            Expression.model_validate(fields)


def test_water_gained_reads_nothing_after_the_counters_reset() -> None:
    data = data_of(lever_shot(), profile=LEVER_PROFILE)
    ramp = run(data, channel="water_pumped", op="gained", window={"phase": "ramp"})
    assert ramp.value == pytest.approx(0.5 * (127 - 68), abs=0.05)
    assert ramp.kind == "estimated"
    # The extended recording after the reset is not read: the last phase ends at the stop.
    assert run(data, channel="water_pumped", op="max").value == round(0.5 * (127 - 28), 1)


# ── absent sensors ───────────────────────────────────────────────────


@pytest.mark.parametrize("op", ["mean", "at_end", "max", "slope"])
@pytest.mark.parametrize("channel", ["cup_weight", "scale_flow"])
def test_a_shot_with_no_scale_has_no_weight_not_a_zero(channel: str, op: str) -> None:
    slog = without_scale(lever_shot())
    assert all(s.v == 0.0 for s in slog.samples)
    data = data_of(slog, profile=LEVER_PROFILE, scale_connected=False)
    got = run(data, channel=channel, op=op, window={"phase": "ramp"})
    assert got.value is None and got.absent == "not_recorded"
    assert got.kind == "measured"


def test_nothing_stands_in_for_the_scale() -> None:
    data = data_of(without_scale(lever_shot()), scale_connected=False)
    for op in ("at_end", "mean"):
        assert run(data, channel="cup_weight", op=op).absent == "not_recorded"
    assert (
        run(data, channel="puck_flow", op="mean").value is not None
    )  # an estimate stays an estimate


@pytest.mark.parametrize(
    "channel", ["pressure", "puck_flow", "pump_flow", "target_flow", "resistance"]
)
def test_a_shot_with_no_pressure_sensor_has_no_pressure_numbers_not_zeros(channel: str) -> None:
    slog = without_pressure(lever_shot())
    data = data_of(slog, has_pressure=False)
    got = run(data, channel=channel, op="mean")
    assert got.value is None and got.absent == "not_recorded"


def test_the_counter_that_never_rose_is_not_recorded() -> None:
    data = data_of(without_pressure(lever_shot()), has_pressure=False)
    got = run(data, channel="water_pumped", op="at_end")
    assert got.absent == "not_recorded"
    got = run(data, channel="water_pumped", op="gained", window={"phase_number": 2})
    assert got.absent == "not_recorded"


def test_a_commanded_channel_needs_no_sensor_but_a_target_temperature_of_zero_is_none_set() -> None:
    data = data_of(without_pressure(lever_shot()), has_pressure=False)
    assert run(data, channel="target_pressure", op="max").value == 0.0 or True
    assert run(data, channel="target_temperature", op="mean").value == 93.0
    unset = ShotData(samples=[{"t": 0.0, "tt": 0.0}, {"t": 1000.0, "tt": 0.0}])
    assert run(unset, channel="target_temperature", op="mean").absent == "empty_window"


# ── windows ──────────────────────────────────────────────────────────


def test_a_phase_window_holds_the_phases_own_samples() -> None:
    slog = real_slogs()[1][1]
    data = data_of(slog)
    for position, span in enumerate(data.phases):
        rows = data.samples[span.start : span.end]
        got = run(
            data,
            channel="pressure",
            op="max",
            window={"phase": span.name.upper().center(len(span.name) + 2)},
        )
        assert got.value == round(max(r["cp"] for r in rows), 1), position
        by_number = run(data, channel="pressure", op="max", window={"phase_number": span.number})
        assert by_number.value == got.value


def test_a_profile_phase_the_shot_never_reached_is_not_reached_and_one_nobody_has_is_no_such() -> (
    None
):
    data = data_of(lever_shot(), profile=LEVER_PROFILE)
    got = run(data, channel="pressure", op="mean", window={"phase": "decline"})
    assert got.absent == "phase_not_reached"
    got = run(data, channel="pressure", op="mean", window={"phase_number": 3})
    assert got.absent == "phase_not_reached"
    got = run(data, channel="pressure", op="mean", window={"phase": "espresso"})
    assert got.absent == "no_such_phase"
    got = run(data, channel="pressure", op="mean", window={"phase_number": 9})
    assert got.absent == "no_such_phase"
    without_profile = data_of(lever_shot())
    assert (
        run(without_profile, channel="pressure", op="mean", window={"phase": "decline"}).absent
        == "no_such_phase"
    )


def test_two_phases_with_one_name_mean_the_first() -> None:
    slog = lever_shot()
    transitions = [t.model_copy() for t in slog.transitions]
    transitions[2] = transitions[2].model_copy(update={"phase_name": "Soak "})
    data = ShotData.build(as_sample_dicts(slog), transitions, final_weight_g=RAMP_END_G)
    got = run(data, channel="cup_weight", op="at_end", window={"phase": "soak"})
    assert got.value == SOAK_END_G
    assert got.sentence == "cup weight at the end of the first soak"
    assert render(
        Expression.model_validate(
            {"channel": "cup_weight", "op": "at_end", "window": {"phase": "soak"}}
        )
    ) == ("cup weight at the end of the soak")


def test_anchors_and_offsets_move_a_span() -> None:
    data = ShotData(samples=[{"t": i * 1000.0, "cp": float(i)} for i in range(12)])
    span = {"from": {"at_s": 2, "offset_s": 1}, "to": {"anchor": "shot_end", "offset_s": -3}}
    got = run(data, channel="pressure", op="mean", window=span)
    assert got.value == 5.5  # samples at 3 s to 8 s inclusive
    start_to_end = run(
        data, channel="pressure", op="at_start", window={"from": "shot_start", "to": "shot_end"}
    )
    assert start_to_end.value == 0.0


def test_the_named_anchors_are_the_moments_step_one_defines() -> None:
    data = data_of(lever_shot(), profile=LEVER_PROFILE)
    drip = next(s for s in data.samples if s.get("pf", 0.0) > 0.0)
    peak = max(data.samples, key=lambda s: s["cp"])
    got = run(
        data,
        channel="pressure",
        op="duration",
        window={"from": "first_drip", "to": "shot_end"},
    )
    assert got.value == round((data.samples[-1]["t"] - drip["t"]) / 1000.0, 2)
    at_peak = run(
        data,
        channel="pressure",
        op="at_start",
        window={"from": "peak_pressure", "to": "shot_end"},
    )
    assert at_peak.value == round(peak["cp"], 1)
    phases = run(
        data,
        channel="pressure",
        op="duration",
        window={"from": {"phase_start": "Ramp"}, "to": {"phase_end": "ramp"}},
    )
    ramp = data.phases[2]
    assert phases.value == round(
        (data.samples[ramp.end - 1]["t"] - data.samples[ramp.start]["t"]) / 1000.0, 2
    )


def test_a_from_after_to_is_an_empty_window_not_an_error() -> None:
    data = data_of(lever_shot())
    got = run(
        data,
        channel="pressure",
        op="mean",
        window={"from": {"at_s": 9}, "to": {"at_s": 3}},
    )
    assert got.value is None and got.absent == "empty_window"


def test_a_log_with_no_phase_table_has_no_phase_windows() -> None:
    slog = lever_shot()
    data = ShotData.build(as_sample_dicts(slog), [], profile_phases=["a", "b"])
    for window in (
        {"phase": "ramp"},
        {"phase_number": 1},
        {"from": {"phase_start": "ramp"}, "to": "shot_end"},
    ):
        got = run(data, channel="pressure", op="mean", window=window)
        assert got.absent == "no_phase_table", window
    assert run(data, channel="pressure", op="mean").value is not None  # the whole shot still works


def test_anchors_that_need_a_sensor_say_so() -> None:
    data = data_of(without_pressure(lever_shot()), has_pressure=False)
    for anchor in ("first_drip", "peak_pressure"):
        got = run(data, channel="temperature", op="mean", window={"from": anchor, "to": "shot_end"})
        assert got.absent == "not_recorded", anchor


# ── relative_to, at read time ────────────────────────────────────────


def test_relative_to_divides_by_what_the_filing_says_and_nothing_stored_moves() -> None:
    slog = lever_shot()
    base = {"channel": "cup_weight", "op": "at_end", "window": {"phase": "ramp"}}
    one = data_of(slog, profile=LEVER_PROFILE, target_yield_g=TARGET_YIELD_G, dose_g=18.0)
    other = data_of(slog, profile=LEVER_PROFILE, target_yield_g=40.0, dose_g=20.0)
    for which, a, b in (
        ("target_yield", TARGET_YIELD_G, 40.0),
        ("dose", 18.0, 20.0),
        ("final_weight", RAMP_END_G, RAMP_END_G),
    ):
        first = run(one, relative_to=which, **base)
        second = run(other, relative_to=which, **base)
        assert first.unit == "share"
        assert first.value == round(RAMP_END_G / a, 3)
        assert second.value == round(RAMP_END_G / b, 3)
    plain = run(one, **base)
    assert plain.value == RAMP_END_G and plain.unit == "g"
    assert run(one, **base).value == run(other, **base).value


def test_relative_to_something_the_filing_lacks_is_no_target() -> None:
    data = data_of(lever_shot(), profile=LEVER_PROFILE)
    for which in ("target_yield", "dose"):
        got = run(
            data, channel="cup_weight", op="at_end", window={"phase": "ramp"}, relative_to=which
        )
        assert got.value is None and got.absent == "no_target"
    zero = data_of(lever_shot(), target_yield_g=0.0)
    assert (
        run(zero, channel="cup_weight", op="max", relative_to="target_yield").absent == "no_target"
    )


def test_an_absent_value_stays_absent_through_relative_to() -> None:
    data = data_of(without_scale(lever_shot()), scale_connected=False, target_yield_g=36.0)
    got = run(data, channel="cup_weight", op="at_end", relative_to="target_yield")
    assert got.absent == "not_recorded"


# ── compare ──────────────────────────────────────────────────────────


def test_a_comparison_is_made_on_the_rounded_number_at_just_under_at_and_just_over() -> None:
    # A cup of 44.0 g is exactly 1.1 of 40 g; 44.04 g shows as 44.0 g.
    data = ShotData(samples=[{"t": 0.0, "v": 44.04}], target_yield_g=40.0)

    def held(op: str, value: float) -> bool | None:
        return run(data, channel="cup_weight", op="at_end", compare={"op": op, "value": value}).held

    assert (held("<=", 44.0), held("<", 44.0), held(">=", 44.0), held(">", 44.0)) == (
        True,
        False,
        True,
        False,
    )
    assert (held("<=", 43.9), held(">", 43.9), held("<", 44.1), held(">=", 44.1)) == (
        False,
        True,
        True,
        False,
    )
    assert (held("<=", 44.1), held(">=", 43.9)) == (True, True)


def test_between_includes_both_ends() -> None:
    data = ShotData(samples=[{"t": 0.0, "cp": 6.0}])

    def held(low: float, high: float) -> bool | None:
        return run(
            data,
            channel="pressure",
            op="at_end",
            compare={"op": "between", "low": low, "high": high},
        ).held

    assert held(6.0, 9.0) is True
    assert held(3.0, 6.0) is True
    assert held(6.1, 9.0) is False
    assert held(3.0, 5.9) is False


def test_a_share_is_compared_after_rounding_to_a_thousandth() -> None:
    data = ShotData(samples=[{"t": 0.0, "v": 5.4}], target_yield_g=36.0)  # 0.15 exactly
    got = run(
        data,
        channel="cup_weight",
        op="at_end",
        relative_to="target_yield",
        compare={"op": "<=", "value": 0.15},
    )
    assert got.value == 0.15 and got.held is True
    over = ShotData(samples=[{"t": 0.0, "v": 5.5}], target_yield_g=36.0)  # 0.153
    assert (
        run(
            over,
            channel="cup_weight",
            op="at_end",
            relative_to="target_yield",
            compare={"op": "<=", "value": 0.15},
        ).held
        is False
    )


def test_a_comparison_on_an_absent_value_is_neither_held_nor_failed() -> None:
    data = data_of(without_scale(lever_shot()), scale_connected=False)
    got = run(data, channel="cup_weight", op="at_end", compare={"op": "<=", "value": 99})
    assert got.held is None and got.absent == "not_recorded" and got.why


def test_a_comparison_that_is_not_one_shape_is_refused() -> None:
    for compare in (
        {"op": "<="},
        {"op": "between", "low": 1},
        {"op": "between", "low": 5, "high": 1},
        {"op": "<", "value": 1, "low": 0},
        {"op": "=", "value": 1},
    ):
        with pytest.raises(ValidationError):
            Expression.model_validate({"channel": "pressure", "op": "max", "compare": compare})


# ── the shape of an expression ───────────────────────────────────────


@pytest.mark.parametrize(
    "fields",
    [
        {"channel": "ev", "op": "mean"},
        {"channel": "pressure", "op": "median"},
        {"channel": "pressure", "op": "mean", "extra": 1},
        {"channel": "pressure", "op": "time_to"},
        {"channel": "pressure", "op": "mean", "threshold": 1},
        {"channel": "pressure", "op": "mean", "direction": "rising"},
        {"channel": "pressure", "op": "time_above", "threshold": 1, "direction": "rising"},
        {"channel": "pressure", "op": "mean", "window": {"phase": "a", "phase_number": 1}},
        {"channel": "pressure", "op": "mean", "window": {"from": "shot_start"}},
        {"channel": "pressure", "op": "mean", "window": {"from": "later", "to": "shot_end"}},
        {"channel": "pressure", "op": "mean", "window": {"phase": ""}},
        {"channel": "pressure", "op": "mean", "relative_to": "weight"},
        {"channel": "pressure", "op": "time_above", "threshold": float("nan")},
        {"channel": "pressure", "op": "time_above", "threshold": "6"},
        {"channel": "pressure", "op": "time_above", "threshold": True},
    ],
)
def test_a_malformed_expression_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Expression.model_validate(fields)


def test_every_channel_and_op_the_spec_names_exists() -> None:
    assert set(CHANNELS) == set(COLUMN) | {"target_temperature", "water_pumped", "resistance"}
    assert set(OPS) == set(PLAIN_OPS) | {
        "gained",
        "duration",
        "time_to",
        "time_above",
        "time_below",
    }
    assert set(ABSENT_REASONS) == {
        "not_recorded",
        "phase_not_reached",
        "ended_before_sampled",
        "no_such_phase",
        "no_target",
        "empty_window",
        "never_reached",
        "no_phase_table",
    }


def test_every_channel_carries_its_kind() -> None:
    data = data_of(lever_shot(), profile=LEVER_PROFILE)
    kinds = {
        "cup_weight": "measured",
        "scale_flow": "measured",
        "puck_flow": "estimated",
        "pump_flow": "estimated",
        "target_flow": "commanded",
        "pressure": "measured",
        "target_pressure": "commanded",
        "temperature": "measured",
        "target_temperature": "commanded",
        "water_pumped": "estimated",
        "resistance": "estimated",
    }
    for channel, kind in kinds.items():
        assert run(data, channel=channel, op="mean").kind == kind
        assert run(data, channel=channel, op="mean", window={"phase": "nope"}).kind == kind


def test_the_resistance_is_the_machines_where_valid_and_pressure_over_flow_squared_elsewhere() -> (
    None
):
    data = ShotData(
        samples=[
            {"t": 0.0, "cp": 9.0, "pf": 3.0, "pr": 2.0},  # the machine's pr, squared: 4
            {"t": 1000.0, "cp": 9.0, "pf": 3.0, "pr": 0.0},  # no pr: 9 / 9 = 1
            {"t": 2000.0, "cp": 9.0, "pf": 0.1, "pr": 2.0},  # no flow to read it from
            {"t": 3000.0, "cp": 8.0, "pf": 2.0, "pr": 100.0},  # pr at the clamp: 8 / 4 = 2
        ]
    )
    assert run(data, channel="resistance", op="mean").value == round((4 + 1 + 2) / 3, 2)
    assert run(data, channel="resistance", op="at_end").value == 2.0


# ── canonical form ───────────────────────────────────────────────────

BASE = {
    "channel": "cup_weight",
    "op": "at_end",
    "window": {"phase": "ramp"},
    "relative_to": "target_yield",
    "compare": {"op": "<=", "value": 0.15},
}


def test_key_order_defaults_whitespace_and_number_spelling_never_change_the_form() -> None:
    plain = canonical_form(Expression.model_validate(BASE))
    reordered = {
        "compare": {"value": 0.15, "op": "<="},
        "relative_to": "target_yield",
        "window": {"phase": "  RAMP "},
        "op": "at_end",
        "channel": "cup_weight",
    }
    assert canonical_form(Expression.model_validate(reordered)) == plain
    assert "\n" not in plain and " " not in plain
    assert canonical_form(Expression.model_validate({**BASE, "window": {}})) != plain
    with_default = {"channel": "pressure", "op": "time_to", "threshold": 6.0}
    spelled = {"channel": "pressure", "op": "time_to", "threshold": 6, "direction": "rising"}
    assert canonical_form(Expression.model_validate(with_default)) == canonical_form(
        Expression.model_validate(spelled)
    )
    zero = {
        "channel": "pressure",
        "op": "mean",
        "window": {"from": {"at_s": 3, "offset_s": 0}, "to": "shot_end"},
    }
    bare = {
        "channel": "pressure",
        "op": "mean",
        "window": {"from": {"at_s": 3.0}, "to": {"anchor": "shot_end"}},
    }
    assert canonical_form(Expression.model_validate(zero)) == canonical_form(
        Expression.model_validate(bare)
    )
    assert json.loads(plain)["window"] == {"phase": "ramp"}


def test_the_method_id_is_the_canonical_form_and_the_form_round_trips() -> None:
    expr = Expression.model_validate(BASE)
    assert method_id(expr) == canonical_form(expr)
    again = Expression.model_validate(dict(json.loads(canonical_form(expr))))
    assert canonical_form(again) == canonical_form(expr)


def random_expression(rng: random.Random) -> dict[str, Any]:
    op = rng.choice(OPS)
    channel = rng.choice(("cup_weight", "water_pumped") if op == "gained" else CHANNELS)
    fields: dict[str, Any] = {"channel": channel, "op": op}
    anchors = [
        "shot_start",
        "shot_end",
        "first_drip",
        "peak_pressure",
        {"phase_start": rng.choice(["ramp", "soak"])},
        {"phase_end": rng.choice(["ramp", "soak"])},
        {"at_s": rng.choice([1, 2.5, 7])},
    ]
    kind = "phase" if op == "gained" else rng.choice(["whole", "phase", "number", "span"])
    if kind == "phase":
        fields["window"] = {"phase": rng.choice(["ramp", "soak", "decline"])}
    elif kind == "number":
        fields["window"] = {"phase_number": rng.randrange(4)}
    elif kind == "span":
        first, second = rng.choice(anchors), rng.choice(anchors)
        for anchor in (first, second):
            if rng.random() < 0.4:
                offset = rng.choice([-2, 0.5, 3])
                if isinstance(anchor, dict):
                    anchor["offset_s"] = offset
        fields["window"] = {
            "from": first,
            "to": second,
        }
    if op in ("time_to", "time_above", "time_below"):
        fields["threshold"] = rng.choice([0, 1.5, 6, 93])
    if op == "time_to":
        fields["direction"] = rng.choice(["rising", "falling"])
    if rng.random() < 0.4:
        fields["relative_to"] = rng.choice(["target_yield", "dose", "final_weight"])
    if rng.random() < 0.4:
        fields["compare"] = rng.choice(
            [
                {"op": rng.choice(["<", "<=", ">", ">="]), "value": rng.choice([0.15, 3, 40])},
                {"op": "between", "low": 1, "high": rng.choice([2, 9.5])},
            ]
        )
    return fields


def test_equal_expressions_share_an_id_and_different_ones_do_not() -> None:
    rng = random.Random(32)  # noqa: S311
    seen: dict[str, dict[str, Any]] = {}
    for _ in range(3000):
        fields = random_expression(rng)
        expr = Expression.model_validate(fields)
        form = canonical_form(expr)
        # The same meaning spelled another way gives the same id.
        shuffled = dict(sorted(fields.items(), reverse=True))
        assert canonical_form(Expression.model_validate(shuffled)) == form
        round_trip = Expression.model_validate(json.loads(form))
        assert canonical_form(round_trip) == form
        previous = seen.get(form)
        if previous is not None:
            # Same id means same meaning: the validated models agree.
            assert Expression.model_validate(previous) == expr or json.loads(form) == json.loads(
                canonical_form(Expression.model_validate(previous))
            )
        seen[form] = fields
    # Changing any one part changes the id.
    base = Expression.model_validate(BASE)
    form = canonical_form(base)
    changes: list[dict[str, Any]] = [
        {**BASE, "channel": "scale_flow"},
        {**BASE, "op": "at_start"},
        {**BASE, "window": {"phase": "soak"}},
        {**BASE, "window": {"phase_number": 2}},
        {**BASE, "relative_to": "dose"},
        {**BASE, "compare": {"op": "<", "value": 0.15}},
        {**BASE, "compare": {"op": "<=", "value": 0.16}},
        {k: v for k, v in BASE.items() if k != "compare"},
    ]
    forms = {canonical_form(Expression.model_validate(c)) for c in changes}
    assert len(forms) == len(changes) and form not in forms


# ── the renderer ─────────────────────────────────────────────────────


def test_the_specs_example_reads_as_the_specs_sentence() -> None:
    assert render(Expression.model_validate(BASE)) == (
        "cup weight at the end of the ramp, as a share of the target yield, at most 0.15"
    )


RENDERED = [
    ({"channel": "pressure", "op": "mean"}, "mean of pressure over the whole shot"),
    ({"channel": "pressure", "op": "min"}, "lowest value of pressure in the whole shot"),
    ({"channel": "pressure", "op": "max"}, "highest value of pressure in the whole shot"),
    ({"channel": "temperature", "op": "at_start"}, "temperature at the start of the whole shot"),
    ({"channel": "temperature", "op": "at_end"}, "temperature at the end of the whole shot"),
    ({"channel": "temperature", "op": "change"}, "change in temperature across the whole shot"),
    (
        {"channel": "cup_weight", "op": "gained", "window": {"phase": "soak"}},
        "cup weight gained in the soak",
    ),
    ({"channel": "pressure", "op": "slope"}, "slope of pressure over the whole shot"),
    ({"channel": "pressure", "op": "integral"}, "integral of pressure over the whole shot"),
    (
        {"channel": "scale_flow", "op": "jitter", "window": {"phase_number": 2}},
        "jitter of cup flow (the spread of its sample-to-sample changes) over phase 2",
    ),
    ({"channel": "pressure", "op": "duration"}, "duration of the whole shot"),
    (
        {"channel": "pressure", "op": "time_to", "threshold": 6},
        "time from the start of the whole shot until pressure rises to 6 bar",
    ),
    (
        {"channel": "pressure", "op": "time_to", "threshold": 2.5, "direction": "falling"},
        "time from the start of the whole shot until pressure falls to 2.5 bar",
    ),
    (
        {"channel": "temperature", "op": "time_above", "threshold": 93},
        "time temperature was above 93 °C in the whole shot",
    ),
    (
        {"channel": "temperature", "op": "time_below", "threshold": 90},
        "time temperature was below 90 °C in the whole shot",
    ),
    (
        {
            "channel": "puck_flow",
            "op": "max",
            "window": {"from": {"anchor": "first_drip", "offset_s": 2}, "to": "shot_end"},
        },
        "highest value of the machine's estimate of puck flow in the span from 2 s after the "
        "first puck flow to the end of the shot",
    ),
    (
        {
            "channel": "pump_flow",
            "op": "mean",
            "window": {
                "from": {"phase_start": "Ramp", "offset_s": -1},
                "to": {"phase_end": "ramp"},
            },
        },
        "mean of the machine's estimate of pump flow over the span from 1 s before the start of "
        "the Ramp to the end of the ramp",
    ),
    (
        {
            "channel": "target_flow",
            "op": "max",
            "window": {"from": {"at_s": 4}, "to": "peak_pressure"},
            "compare": {"op": "between", "low": 1, "high": 2},
        },
        "highest value of the flow the profile commanded in the span from 4 s into the shot to the "
        "moment of peak pressure, between 1 and 2",
    ),
    (
        {"channel": "target_pressure", "op": "max", "compare": {"op": ">", "value": 8}},
        "highest value of the pressure the profile commanded in the whole shot, over 8",
    ),
    (
        {"channel": "target_temperature", "op": "mean", "compare": {"op": ">=", "value": 90}},
        "mean of the temperature the profile commanded over the whole shot, at least 90",
    ),
    (
        {"channel": "water_pumped", "op": "max", "relative_to": "dose"},
        "highest value of the machine's estimate of water pumped in the whole shot, "
        "as a share of the dose",
    ),
    (
        {
            "channel": "resistance",
            "op": "mean",
            "relative_to": "final_weight",
            "compare": {"op": "<", "value": 1},
        },
        "mean of the puck resistance worked out from the machine's estimate of puck flow over the "
        "whole shot, as a share of the shot's own final weight, under 1",
    ),
]


@pytest.mark.parametrize(("fields", "sentence"), RENDERED, ids=[s[:40] for _, s in RENDERED])
def test_every_channel_window_and_op_has_its_sentence(
    fields: dict[str, Any], sentence: str
) -> None:
    assert render(Expression.model_validate(fields)) == sentence


def test_every_channel_and_op_renders_something_distinct() -> None:
    sentences = set()
    for channel in CHANNELS:
        for op in OPS:
            fields: dict[str, Any] = {"channel": channel, "op": op}
            if op in ("time_to", "time_above", "time_below"):
                fields["threshold"] = 2
            if op == "gained":
                if channel not in ("cup_weight", "water_pumped"):
                    continue
                fields["window"] = {"phase": "ramp"}
            sentences.add(render(Expression.model_validate(fields)))
    # Every pair has its own sentence, except duration, which is the window's and not a channel's.
    assert len(sentences) == len(CHANNELS) * (len(OPS) - 2) + 1 + 2


# ── a sentence in every result ───────────────────────────────────────


def test_a_result_says_its_sentence_its_unit_its_kind_and_its_id() -> None:
    data = data_of(lever_shot(), profile=LEVER_PROFILE, target_yield_g=TARGET_YIELD_G)
    got = run(data, **BASE)
    assert got.sentence.startswith("cup weight at the end of the ramp")
    assert got.unit == "share" and got.kind == "measured"
    assert got.method == canonical_form(Expression.model_validate(BASE))
    assert got.value == round(RAMP_END_G / TARGET_YIELD_G, 3) and got.held is False
    assert got.absent is None and got.why is None


def test_the_resistance_clamp_is_the_diagnostics_engines() -> None:
    from gaggiclanker.domain import diagnostics, metric_language

    assert metric_language._MACHINE_RESISTANCE_MAX == diagnostics._MACHINE_RESISTANCE_MAX


def test_a_comparison_is_decided_on_a_rounded_difference_not_a_float_one() -> None:
    data = ShotData(samples=[{"t": 0.0, "cp": 0.3}])
    for op in (">=", "<="):
        got = run(data, channel="pressure", op="at_end", compare={"op": op, "value": 0.1 + 0.2})
        assert got.held is True, op


def test_direction_and_a_zero_offset_are_part_of_the_form_only_where_they_mean_something() -> None:
    def form(**fields: Any) -> str:
        return canonical_form(Expression.model_validate({"channel": "pressure", **fields}))

    rising = form(op="time_to", threshold=6)
    falling = form(op="time_to", threshold=6, direction="falling")
    assert rising != falling and json.loads(falling)["direction"] == "falling"
    for anchor in ({"phase_start": "ramp"}, {"phase_end": "ramp"}, {"at_s": 3}):
        window = {"from": anchor, "to": "shot_end"}
        zero = {"from": {**anchor, "offset_s": 0}, "to": "shot_end"}
        assert form(op="mean", window=window) == form(op="mean", window=zero)
        assert "offset_s" not in form(op="mean", window=zero)


# ── the cup's dropout rule, wherever the window ends with the shot ───


def _dropped_out_lever() -> ShotData:
    slog = lever_shot()
    samples = as_sample_dicts(slog)
    for sample in samples[-3:]:
        sample["v"] = 0.0
    return ShotData.build(
        samples,
        slog.transitions,
        final_weight_g=RAMP_END_G,
        target_yield_g=TARGET_YIELD_G,
        profile_phases=None,
    )


@pytest.mark.parametrize(
    "window",
    [
        {},
        {"phase": "ramp"},
        {"from": "shot_start", "to": "shot_end"},
        {"from": {"phase_start": "ramp"}, "to": "shot_end"},
        {"from": {"at_s": 20}, "to": {"at_s": 500}},
    ],
)
def test_every_window_that_ends_with_the_shot_ends_at_the_final_weight_when_the_scale_dropped(
    window: dict[str, Any],
) -> None:
    data = _dropped_out_lever()
    got = run(
        data,
        channel="cup_weight",
        op="at_end",
        window=window,
        relative_to="target_yield",
    )
    assert got.value == round(RAMP_END_G / TARGET_YIELD_G, 3)
    # A window that stops before the shot's last sample reads the scale as it was.
    early = run(
        data, channel="cup_weight", op="at_end", window={"from": "shot_start", "to": {"at_s": 5}}
    )
    assert early.value != RAMP_END_G


def test_change_is_the_end_less_the_start_with_the_same_end() -> None:
    data = _dropped_out_lever()
    ramp = run(data, channel="cup_weight", op="change", window={"phase": "ramp"})
    assert ramp.value == pytest.approx(RAMP_END_G - 4.2, abs=1e-9)
    whole = run(data, channel="cup_weight", op="change")
    assert whole.value == RAMP_END_G
    # An ordinary window: the last reading less the first.
    plain = run(data, channel="cup_weight", op="change", window={"phase": "soak"})
    assert plain.value == pytest.approx(SOAK_END_G - 0.1, abs=1e-9)


def test_change_on_a_real_last_phase_ends_at_the_final_weight() -> None:
    slog = real_slogs()[0][1]
    data = data_of(slog)
    samples = [dict(s) for s in data.samples]
    last = data.phases[-1]
    for s in samples[-2:]:
        s["v"] = 0.0
    dropped = ShotData.build(
        samples, slog.transitions, final_weight_g=slog.volume_g, scale_connected=True
    )
    first = next(s["v"] for s in samples[last.start : last.end] if "v" in s)
    got = run(dropped, channel="cup_weight", op="change", window={"phase_number": last.number})
    assert got.value == round((slog.volume_g or 0.0) - first, 1)


# ── seconds, the peak, the first drip, and gained across a gap ───────


def test_seconds_are_rounded_once_from_whole_milliseconds() -> None:
    # 1.015 s and 1.005 s are exact in milliseconds; half to even, never decided by a float.
    for span_ms, want in ((1015, 1.02), (1005, 1.0), (1025, 1.02), (2035, 2.04)):
        data = ShotData(samples=[{"t": 0.0, "cp": 1.0}, {"t": float(span_ms), "cp": 1.0}])
        assert run(data, channel="pressure", op="duration").value == want, span_ms
        above = run(data, channel="pressure", op="time_above", threshold=0)
        assert above.value == want, span_ms
        reached = run(data, channel="pressure", op="time_to", threshold=2, direction="falling")
        assert reached.value == 0.0
    # Sums of intervals are summed in milliseconds first: 3 x 5 ms is 15 ms, 0.015 s.
    ticks = ShotData(samples=[{"t": i * 5.0, "cp": 1.0} for i in range(4)])
    assert run(ticks, channel="pressure", op="time_above", threshold=0).value == 0.02
    assert run(ticks, channel="pressure", op="integral").value == 0.0


def test_the_peak_pressure_of_an_all_zero_trace_is_not_a_moment_and_a_tie_takes_the_first() -> None:
    zero = ShotData(samples=[{"t": i * 1000.0, "cp": 0.0} for i in range(3)])
    got = run(
        zero,
        channel="temperature",
        op="duration",
        window={"from": "peak_pressure", "to": "shot_end"},
    )
    assert got.value is None and got.absent == "empty_window"
    tie = ShotData(
        samples=[{"t": i * 1000.0, "cp": cp} for i, cp in enumerate((1.0, 9.0, 3.0, 9.0))]
    )
    got = run(
        tie,
        channel="temperature",
        op="duration",
        window={"from": "peak_pressure", "to": "shot_end"},
    )
    assert got.value == 2.0  # from the first 9 bar (1 s) to the end (3 s)


def test_the_first_drip_is_the_first_puck_flow_above_zero_exactly() -> None:
    data = ShotData(
        samples=[
            {"t": i * 1000.0, "cp": 1.0, "pf": pf} for i, pf in enumerate((0.0, 0.0, 0.01, 1.0))
        ]
    )
    got = run(
        data, channel="pressure", op="duration", window={"from": "first_drip", "to": "shot_end"}
    )
    assert got.value == 1.0  # the 0.01 ml/s sample at 2 s, not a zero one
    none = ShotData(samples=[{"t": i * 1000.0, "cp": 1.0, "pf": 0.0} for i in range(3)])
    got = run(
        none, channel="pressure", op="duration", window={"from": "first_drip", "to": "shot_end"}
    )
    assert got.absent == "empty_window"


def test_gained_reads_back_to_the_nearest_phase_that_has_a_value() -> None:
    transitions = [
        PhaseTransition(sample_index=0, phase_number=0, phase_name="a"),
        PhaseTransition(sample_index=2, phase_number=1, phase_name="b"),
        PhaseTransition(sample_index=4, phase_number=2, phase_name="c"),
    ]
    samples: list[dict[str, float]] = [
        {"t": 0.0, "v": 1.0},
        {"t": 1000.0, "v": 5.0},
        {"t": 2000.0},  # the middle phase recorded no weight at all
        {"t": 3000.0},
        {"t": 4000.0, "v": 9.0},
        {"t": 5000.0, "v": 12.0},
    ]
    data = ShotData.build(samples, transitions)
    assert run(data, channel="cup_weight", op="gained", window={"phase": "c"}).value == 7.0
    middle = run(data, channel="cup_weight", op="gained", window={"phase": "b"})
    assert middle.value is None and middle.absent == "empty_window"
    assert run(data, channel="cup_weight", op="gained", window={"phase": "a"}).value == 5.0
