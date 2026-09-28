"""Cutting a curve to a few dozen rows without losing what the diagnostics are about.

The fixture below is built so that a uniform stride — what the shot tools
used to do — steps over the peak, a half-second pressure drop and the first
drip at the same budget. The tests assert that it really does, so that the
selection keeping them proves something.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
from itertools import pairwise
from typing import Any

import pytest

from gaggiclanker.db.repos.shots import ShotSampleRow
from gaggiclanker.domain import diagnostics
from gaggiclanker.domain.slog import Slog, apply_phases, parse_slog
from gaggiclanker.shotinfo.downsample import (
    CURVE_POINTS,
    MIN_CURVE_POINTS,
    CurveEvents,
    _marker_runs,
    find_events,
    lttb,
    select_rows,
)
from gaggiclanker.sync.derive import derive_shot
from tests.shotinfo.conftest import SLOG

PHASES: list[dict[str, Any]] = [
    {"phase_number": 0, "name": "Preinfusion", "sample_count": 40},
    {"phase_number": 1, "name": "Ramp", "sample_count": 20},
    {"phase_number": 2, "name": "Extraction", "sample_count": 140},
]

#: The moments the fixture hides from a stride of seven.
PEAK = 101
DROP = (149, 150)
DRIP = 45


def _pressure(i: int) -> float:
    if i < 40:
        return round(2.0 * i / 40, 1)
    if i < 60:
        return round(2.0 + 7.0 * (i - 40) / 20, 1)
    if i == PEAK:
        return 9.6
    if i == DROP[1]:
        return 6.0
    if i == DROP[1] + 1:
        return 6.5
    # A plateau with a little sensor noise, so no two buckets are flat.
    return round(9.0 + 0.1 * math.sin(i / 3), 1)


def _flow(i: int) -> float:
    if i < DRIP:
        return 0.0
    return round(min(2.0, 0.2 * (i - DRIP + 1)) + 0.05 * math.cos(i / 4), 2)


def shot(
    count: int = 200,
    *,
    pressure: bool = True,
    puck_flow: bool = True,
    phases: bool = True,
) -> list[ShotSampleRow]:
    """A 50-second shot: pre-infusion, ramp, a long extraction with a peak and a drop."""
    return [
        ShotSampleRow(
            t_ms=i * 250,
            cp=_pressure(i) if pressure else None,
            pf=_flow(i) if puck_flow else None,
            v=round(max(0.0, (i - 60) * 0.25), 1),
            ct=93.0,
            phase_number=(0 if i < 40 else 1 if i < 60 else 2) if phases else None,
        )
        for i in range(count)
    ]


def events_of(samples: list[ShotSampleRow], **recorded: bool) -> CurveEvents:
    return find_events(
        samples,
        PHASES,
        pressure=recorded.get("pressure", True),
        puck_flow=recorded.get("puck_flow", True),
        sample_interval_ms=250,
    )


def select(samples: list[ShotSampleRow], target: int, **recorded: bool) -> list[int]:
    events = events_of(samples, **recorded)
    return select_rows(
        samples,
        events,
        target,
        pressure=recorded.get("pressure", True),
        puck_flow=recorded.get("puck_flow", True),
    )


def stride(count: int, target: int) -> list[int]:
    """What the shot tools did before: every k-th sample, from the first."""
    step = math.ceil(count / target)
    return [i for i in range(count) if i % step == 0]


# -- events -----------------------------------------------------------------


def test_the_events_are_the_engine_s_moments() -> None:
    events = events_of(shot())

    assert events.phase_edges == (0, 39, 40, 59, 60, 199)
    assert events.peak_pressure == PEAK
    assert events.first_drip == DRIP
    assert events.pressure_drop == DROP


def test_a_uniform_stride_loses_the_peak_the_drop_and_the_drip_and_the_selection_keeps_them() -> (
    None
):
    samples = shot()
    target = 30
    uniform = stride(len(samples), target)
    assert len(uniform) <= target

    # The stride really does lose them: no peak, no drop, no first drip.
    assert PEAK not in uniform and DRIP not in uniform
    assert not set(DROP) & set(uniform)
    pressures = [samples[i].cp or 0.0 for i in uniform]
    assert max(pressures) < 9.6
    steepest = min(b - a for a, b in pairwise(pressures))
    assert steepest > -1.0, "the stride shows no drop worth the name"

    rows = select(samples, target)

    assert PEAK in rows and DRIP in rows
    assert set(DROP) <= set(rows)
    assert len(rows) <= target


@pytest.mark.parametrize("target", [MIN_CURVE_POINTS, 30, CURVE_POINTS, 120])
def test_every_event_survives_at_every_target(target: int) -> None:
    samples = shot()
    events = events_of(samples)

    rows = select(samples, target)

    assert {0, len(samples) - 1} <= set(rows)
    assert set(events.phase_edges) <= set(rows)
    assert set(events.positions()) <= set(rows)


def test_a_table_before_v5_has_no_phase_edges_and_still_finds_its_drop() -> None:
    samples = shot(phases=False)

    events = events_of(samples)

    assert events.phase_edges == ()
    # No table: the engine's brew window is "from half the peak", and the drop
    # is in it either way.
    assert events.pressure_drop == DROP
    assert events.peak_pressure == PEAK


# -- budget -----------------------------------------------------------------


@pytest.mark.parametrize("target", [MIN_CURVE_POINTS, 11, 25, CURVE_POINTS, 150, 199])
def test_the_rows_stay_within_the_target_plus_the_events(target: int) -> None:
    samples = shot()
    events = events_of(samples)

    rows = select(samples, target)

    assert len(rows) <= target + len(events.positions())
    assert len(rows) <= max(target, len({0, len(samples) - 1, *events.positions()}))
    assert rows == sorted(set(rows)), "ascending, each once"


def test_a_shot_no_longer_than_the_target_is_whole() -> None:
    samples = shot(60)

    assert select(samples, 60) == list(range(60))
    assert select(samples, 200) == list(range(60))
    assert select(samples, 59) != list(range(60))


def test_the_floor_leaves_room_for_nothing_but_the_events() -> None:
    samples = shot()
    events = events_of(samples)

    rows = select(samples, MIN_CURVE_POINTS)

    assert rows == sorted({0, len(samples) - 1, *events.positions()})


# -- what the shot recorded -------------------------------------------------


def _expected(
    samples: list[ShotSampleRow], events: CurveEvents, shares: dict[str, int]
) -> list[int]:
    kept = {0, len(samples) - 1, *events.positions()}
    times = [float(sample.t_ms) for sample in samples]
    for field, share in shares.items():
        values = [float(getattr(sample, field)) for sample in samples]
        kept.update(lttb(times, values, share + 2))
    return sorted(kept)


def test_with_both_channels_the_budget_is_split_between_them() -> None:
    samples = shot()
    events = events_of(samples)
    left = 40 - len({0, len(samples) - 1, *events.positions()})

    rows = select(samples, 40)

    assert rows == _expected(samples, events, {"cp": (left + 1) // 2, "pf": left // 2})


def test_without_a_pressure_sensor_it_is_all_puck_flow_and_no_pressure_event() -> None:
    samples = shot()
    events = events_of(samples, pressure=False)

    rows = select(samples, 40, pressure=False)

    assert events.peak_pressure is None and events.pressure_drop is None
    assert events.first_drip == DRIP
    left = 40 - len({0, len(samples) - 1, *events.positions()})
    assert rows == _expected(samples, events, {"pf": left})


def test_without_puck_flow_it_is_all_pressure_and_no_first_drip() -> None:
    samples = shot(puck_flow=False)
    events = events_of(samples, puck_flow=False)

    rows = select(samples, 40, puck_flow=False)

    assert events.first_drip is None
    assert events.peak_pressure == PEAK
    left = 40 - len({0, len(samples) - 1, *events.positions()})
    assert rows == _expected(samples, events, {"cp": left})


def test_with_neither_the_rest_is_even_in_time() -> None:
    samples = shot(pressure=False, puck_flow=False)
    events = events_of(samples, pressure=False, puck_flow=False)

    rows = select(samples, 30, pressure=False, puck_flow=False)

    assert events.peak_pressure is None
    assert events.first_drip is None
    assert events.pressure_drop is None
    kept = {0, len(samples) - 1, *events.positions()}
    left = 30 - len(kept)
    # Moments strictly inside the shot, the nearer sample, the earlier on a tie:
    # the first and last are kept already and cost none of the budget.
    even = {math.ceil(step * 199 / (left + 1) - 0.5) for step in range(1, left + 1)}
    assert not even & {0, 199}
    assert set(rows) == kept | even
    assert len(rows) <= 30


# -- determinism ------------------------------------------------------------


def test_the_same_samples_give_the_same_rows() -> None:
    assert select(shot(), CURVE_POINTS) == select(shot(), CURVE_POINTS)


def test_what_the_selection_does_not_read_cannot_move_it() -> None:
    """Weight and temperature are cut at the rows pressure and flow choose, never choose them."""
    samples = shot()
    rng = random.Random(7)  # noqa: S311 - a fixed seed, not a secret
    scrambled = [
        sample.model_copy(update={"v": rng.uniform(0, 40), "ct": rng.uniform(85, 96)})
        for sample in samples
    ]

    assert select(scrambled, 30) == select(samples, 30)


def test_equal_points_swapped_give_the_same_rows() -> None:
    """Two samples with equal values trade places: the rows are the same positions."""
    samples = shot()
    plateau = [i for i in range(61, 199) if i not in (PEAK, *DROP, DROP[1] + 1)]
    pairs = [
        (a, b)
        for a in plateau
        for b in plateau
        if a < b and (samples[a].cp, samples[a].pf) == (samples[b].cp, samples[b].pf)
    ]
    assert pairs, "the fixture has equal points to swap"
    swapped = list(samples)
    for a, b in pairs[:10]:
        swapped[a] = samples[b].model_copy(update={"t_ms": samples[a].t_ms})
        swapped[b] = samples[a].model_copy(update={"t_ms": samples[b].t_ms})

    assert select(swapped, 30) == select(samples, 30)


# -- the engine's phases -----------------------------------------------------


def _late_table() -> Slog:
    """A real shot whose table starts late, with a sharp drop before it.

    The first transition is taken out, so the table starts at sample 29 and
    the engine leaves samples 0-28 out of every phase and out of its brew
    window. Those samples carry the shot's highest pressure, flowing, and then
    its steepest drop: a reader that put them in a phase would find that drop.
    """
    slog = parse_slog(SLOG.read_bytes())
    transitions = slog.transitions[1:]
    samples = []
    for i, sample in enumerate(slog.samples):
        update: dict[str, Any] = {}
        if 5 <= i <= 12:
            update = {"cp": 10.0, "pf": 2.0}
        elif 13 <= i <= 28:
            update = {"cp": 2.0, "pf": 2.0}
        samples.append(sample.model_copy(update=update))
    apply_phases(samples, transitions, slog.header.version)
    header = slog.header.model_copy(update={"transitions": transitions})
    return dataclasses.replace(slog, header=header, samples=samples)


def _stored(slog: Slog) -> tuple[list[ShotSampleRow], list[dict[str, Any]], int]:
    derived = derive_shot(slog, b"late", device_id="000204")
    assert derived.shot.phases_json is not None
    interval = derived.shot.sample_interval_ms
    assert interval is not None
    return derived.samples, json.loads(derived.shot.phases_json), interval


def _engine_edges(slog: Slog) -> tuple[int, ...]:
    ranges = diagnostics._phase_ranges(len(slog.samples), slog.transitions)
    return tuple(i for _, span in ranges if span for i in (span[0], span[-1]))


def test_a_table_that_starts_late_is_sliced_as_the_engine_sliced_it() -> None:
    slog = _late_table()
    samples, phases, interval = _stored(slog)
    dt = interval / 1000
    engine = diagnostics.largest_pressure_drop(
        diagnostics.as_sample_dicts(slog), slog.transitions, dt
    )
    assert slog.transitions[0].sample_index == 29
    assert engine is not None and engine[0] > 28

    events = find_events(
        samples, phases, pressure=True, puck_flow=True, sample_interval_ms=interval
    )

    assert events.pressure_drop == engine
    assert events.phase_edges == _engine_edges(slog)
    assert events.phase_edges[0] == 29
    # The per-sample markers alone put samples 0-28 in a phase and find the
    # drop the engine never assessed: the test tells the two readings apart.
    runs = _marker_runs(samples, phases)
    assert diagnostics.largest_pressure_drop(diagnostics.as_sample_dicts(slog), runs, dt) == (
        12,
        13,
    )


def test_phase_lines_that_do_not_account_for_the_samples_fall_back_to_the_markers() -> None:
    samples = shot()
    unsliced = [{k: v for k, v in phase.items() if k != "sample_count"} for phase in PHASES]
    too_many = [{**phase, "sample_count": 500} for phase in PHASES]

    assert find_events(
        samples, unsliced, pressure=True, puck_flow=True, sample_interval_ms=250
    ) == events_of(samples)
    assert find_events(
        samples, too_many, pressure=True, puck_flow=True, sample_interval_ms=250
    ) == events_of(samples)


def test_a_steeper_drop_in_pre_infusion_is_not_the_event() -> None:
    """The engine assesses the brew phases only; so is the event found there."""
    phases: list[dict[str, Any]] = [
        {"phase_number": 0, "name": "Preinfusion", "sample_count": 30},
        {"phase_number": 1, "name": "Extraction", "sample_count": 70},
    ]

    def pressure(i: int) -> float:
        if i < 10:
            return 9.0
        if i < 30:
            return 3.0 if i > 10 else 2.0
        return 8.0 if i == 71 else 9.0

    samples = [
        ShotSampleRow(t_ms=i * 250, cp=pressure(i), pf=1.5, phase_number=0 if i < 30 else 1)
        for i in range(100)
    ]
    events = find_events(samples, phases, pressure=True, puck_flow=True, sample_interval_ms=250)

    # 9 → 2 bar at 9-10 is the steepest step of the shot, but in pre-infusion.
    assert events.pressure_drop == (70, 71)
    assert events.phase_edges == (0, 29, 30, 99)


def test_one_phase_that_is_the_whole_shot_has_no_edges_of_its_own() -> None:
    samples = shot(phases=False)
    one = [{"phase_number": 0, "name": "extraction", "sample_count": 200}]
    stamped = [sample.model_copy(update={"phase_number": 0}) for sample in samples]

    events = find_events(stamped, one, pressure=True, puck_flow=True, sample_interval_ms=250)

    assert events.phase_edges == ()


# -- LTTB -------------------------------------------------------------------


def test_lttb_keeps_the_ends_and_the_turns() -> None:
    xs = [float(i) for i in range(11)]
    ys = [0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.0, -5.0, 0.0, 0.0, 0.0]

    kept = lttb(xs, ys, 5)

    assert kept[0] == 0 and kept[-1] == 10
    assert 3 in kept and 7 in kept
    assert len(kept) == 5


def test_lttb_breaks_ties_towards_the_earlier_point() -> None:
    xs = [float(i) for i in range(10)]
    flat = [1.0] * 10

    assert lttb(xs, flat, 4) == [0, 1, 5, 9]


def test_lttb_at_or_above_the_length_is_every_point() -> None:
    xs = [0.0, 1.0, 2.0]
    assert lttb(xs, xs, 3) == [0, 1, 2]
    assert lttb(xs, xs, 10) == [0, 1, 2]
    assert lttb([float(i) for i in range(6)], [0.0] * 6, 2) == [0, 5]
