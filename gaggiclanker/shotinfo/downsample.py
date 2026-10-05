"""Which rows of a shot's curve a model reads: a shape-preserving few dozen, not all of them.

The machine logs a sample every 250 ms, so a 55-second shot is some 210 rows
and, at the default channels, over 2,000 tokens. A model reading the curve
needs its shape and its moments, not every quarter-second of a plateau. So the
table is cut to a target number of rows, and cut on purpose:

* **Rows, not channels.** Every channel is cut at the same timestamps, so a row
  still reads across — pressure, flow and weight at one moment. The selection
  is one per shot, whichever channels the tiers show, so moving a channel
  between tiers never moves a timestamp.
* **Events are always kept**: the first and last sample, the first and last
  sample of every phase, peak pressure, first drip, and both samples of the
  largest pressure drop. Each is found by the diagnostics engine's own rule
  (:func:`~gaggiclanker.domain.diagnostics.first_drip_index` and its
  siblings), so the row the model reads is the sample the engine read the
  number from. A channel the shot did not record contributes no event.
* **The rest of the budget keeps the shape**, by largest-triangle-three-buckets
  on pressure and on puck flow, half each; all of it on the one of the two a
  shot recorded; evenly in time when it recorded neither.

A fixed stride would keep the general shape and step over a half-second
pressure drop, a channel opening; that is what this replaces.

It is **deterministic**: plain functions of the samples, no randomness, ties
broken towards the earlier sample, nothing iterated in set or dict order. The
same samples and target give the same rows.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gaggiclanker.db.repos.shots import ShotSampleRow
from gaggiclanker.domain.diagnostics import (
    SAMPLE_FIELDS,
    SampleDict,
    first_drip_index,
    largest_pressure_drop,
    peak_pressure_index,
)
from gaggiclanker.domain.models import PhaseTransition

__all__ = [
    "CURVE_POINTS",
    "MIN_CURVE_POINTS",
    "CurveEvents",
    "find_events",
    "lttb",
    "select_rows",
]

#: How many rows a curve is cut to when the caller does not say: the
#: `chatCurvePoints` setting's default, which is what every caller passes.
CURVE_POINTS = 60

#: The fewest the setting allows: below it the events alone fill the table.
MIN_CURVE_POINTS = 10


@dataclass(frozen=True, slots=True)
class CurveEvents:
    """The samples a cut curve always keeps, by position in the shot's samples.

    Each is absent when the shot has no such moment: no phase table, no
    pressure sensor, a puck flow that never rose, a steady state too short for
    the engine to assess a drop.
    """

    #: The first and last sample of every phase, in time order.
    phase_edges: tuple[int, ...] = ()
    peak_pressure: int | None = None
    first_drip: int | None = None
    #: Where the largest pressure drop starts and ends.
    pressure_drop: tuple[int, int] | None = None

    def positions(self) -> list[int]:
        """Every event's sample, ascending, each once."""
        found = [*self.phase_edges, *(self.pressure_drop or ())]
        found += [index for index in (self.peak_pressure, self.first_drip) if index is not None]
        return sorted(set(found))


def find_events(
    samples: Sequence[ShotSampleRow],
    phases: Sequence[Mapping[str, Any]],
    *,
    pressure: bool,
    puck_flow: bool,
    sample_interval_ms: int | None,
) -> CurveEvents:
    """The shot's moments, found by the engine's own rules on the stored samples.

    ``pressure`` and ``puck_flow`` say whether the shot recorded the channel at
    all (a pressure channel on a machine without a sensor is not recorded); an
    unrecorded channel contributes no event. ``phases`` is the stored phase
    list, read for the phase names the engine's brew window is chosen by.
    """
    engine = _engine_samples(samples)
    transitions = _transitions(samples, phases)
    # The engine divides by the nominal interval; only which step is the
    # steepest is read here, and that does not depend on the interval, so a
    # row that lost it still finds its drop.
    dt = (sample_interval_ms or 1000) / 1000
    return CurveEvents(
        phase_edges=tuple(
            index for span in _phase_spans(transitions, len(samples)) for index in span
        ),
        peak_pressure=peak_pressure_index(engine) if pressure else None,
        first_drip=first_drip_index(engine) if puck_flow else None,
        pressure_drop=largest_pressure_drop(engine, transitions, dt) if pressure else None,
    )


def select_rows(
    samples: Sequence[ShotSampleRow],
    events: CurveEvents,
    target: int,
    *,
    pressure: bool,
    puck_flow: bool,
) -> list[int]:
    """The positions of the rows to write, ascending: every event, then the shape.

    A shot with no more samples than ``target`` is written whole. Otherwise the
    first and last sample and every event are kept, and what is left of
    ``target`` is spent by :func:`lttb` on pressure and on puck flow, half each
    (pressure takes an odd one), on the one of them the shot recorded, or
    evenly in time when it recorded neither. The result is never more than
    ``target`` rows unless the events alone are more.
    """
    count = len(samples)
    if count <= target:
        return list(range(count))
    kept = {0, count - 1, *events.positions()}
    left = max(target - len(kept), 0)
    times = [float(sample.t_ms) for sample in samples]
    series: list[tuple[str, int]]
    if pressure and puck_flow:
        series = [("cp", (left + 1) // 2), ("pf", left // 2)]
    elif pressure:
        series = [("cp", left)]
    elif puck_flow:
        series = [("pf", left)]
    else:
        kept.update(_even_in_time(times, left))
        series = []
    for field, share in series:
        # The first and last sample are kept already, so LTTB's two fixed
        # points are free: `share` is what it adds in between.
        kept.update(_lttb_channel(samples, times, field, share + 2))
    return sorted(kept)


def lttb(xs: Sequence[float], ys: Sequence[float], threshold: int) -> list[int]:
    """Largest-triangle-three-buckets: ``threshold`` points that keep a line's shape.

    Sveinn Steinarsson's algorithm ("Downsampling Time Series for Visual
    Representation", University of Iceland, 2013). The first and last points
    are kept; the points between are split into ``threshold - 2`` buckets of
    near-equal count, and each bucket keeps the point that makes the largest
    triangle with the point kept before it and the average of the next
    bucket — the point that bends the line most, so peaks, dips and turns
    survive where a stride would step over them.

    Returns positions, ascending. Bucket edges are integer arithmetic and a tie
    keeps the earlier point, so the answer never depends on rounding or order.
    """
    count = len(xs)
    if threshold >= count or count <= 2:
        return list(range(count))
    if threshold <= 2:
        return [0, count - 1]
    buckets = threshold - 2
    middle = count - 2

    def edge(bucket: int) -> int:
        return bucket * middle // buckets + 1

    kept = [0]
    anchor = 0
    for bucket in range(buckets):
        start, end = edge(bucket), edge(bucket + 1)
        after_start, after_end = end, min(edge(bucket + 2), count)
        if bucket == buckets - 1:
            after_start, after_end = count - 1, count
        width = after_end - after_start
        avg_x = sum(xs[after_start:after_end]) / width
        avg_y = sum(ys[after_start:after_end]) / width
        ax, ay = xs[anchor], ys[anchor]
        best, best_area = start, -1.0
        for index in range(start, end):
            # Twice the triangle's area; the factor does not change the winner.
            area = abs((ax - avg_x) * (ys[index] - ay) - (ax - xs[index]) * (avg_y - ay))
            if area > best_area:
                best, best_area = index, area
        kept.append(best)
        anchor = best
    kept.append(count - 1)
    return kept


def _lttb_channel(
    samples: Sequence[ShotSampleRow], times: list[float], field: str, threshold: int
) -> list[int]:
    """LTTB on one channel, over the samples that carry a value for it."""
    carried = [index for index, sample in enumerate(samples) if getattr(sample, field) is not None]
    values = [float(getattr(samples[index], field)) for index in carried]
    chosen = lttb([times[index] for index in carried], values, threshold)
    return [carried[position] for position in chosen]


def _even_in_time(times: list[float], count: int) -> list[int]:
    """``count`` samples nearest to evenly spaced moments strictly inside the shot.

    The first and last sample are kept already, so the moments are the
    ``count`` that split the shot into ``count + 1`` equal stretches.
    """
    if count <= 0 or not times:
        return []
    first, last = times[0], times[-1]
    out: list[int] = []
    for step in range(1, count + 1):
        moment = first + (last - first) * step / (count + 1)
        right = min(bisect_left(times, moment), len(times) - 1)
        left = max(right - 1, 0)
        # The nearer of the two neighbours; on a tie, the earlier one.
        out.append(left if moment - times[left] <= times[right] - moment else right)
    return out


def _engine_samples(samples: Sequence[ShotSampleRow]) -> list[SampleDict]:
    """The stored rows as the engine's sample dicts: a field is absent when not recorded."""
    out: list[SampleDict] = []
    for sample in samples:
        # `t` is `t_ms` on a row; every other field has the engine's name.
        row: SampleDict = {"t": float(sample.t_ms)}
        for name in SAMPLE_FIELDS:
            value = getattr(sample, name, None)
            if value is not None:
                row[name] = float(value)
        if sample.phase_number is not None:
            row["phase"] = float(sample.phase_number)
        out.append(row)
    return out


def _transitions(
    samples: Sequence[ShotSampleRow], phases: Sequence[Mapping[str, Any]]
) -> list[PhaseTransition]:
    """The shot's phase table as the engine sliced it, rebuilt from the stored phase lines.

    A file before v5 has no table, and its samples carry no phase marker: no
    transitions, as the engine had none. Otherwise the stored phase lines are
    the engine's own slices, in order, each with its ``sample_count``; they
    run back to back to the last sample, so the first starts at the samples
    left over. That is where a table that starts late puts it: the engine
    leaves the samples recorded before the table's first entry out of every
    phase, and so out of its brew window, and so does this.

    A row whose phase lines do not account for its samples (written by hand,
    or lacking the counts) falls back to where each phase's marker starts.
    """
    if not samples or any(sample.phase_number is None for sample in samples):
        return []
    counts = [phase.get("sample_count") for phase in phases]
    sliced = [count for count in counts if isinstance(count, int) and not isinstance(count, bool)]
    if phases and len(sliced) == len(phases) and all(sliced) and sum(sliced) <= len(samples):
        start = len(samples) - sum(sliced)
        out: list[PhaseTransition] = []
        for phase, count in zip(phases, sliced, strict=True):
            out.append(_transition(start, phase.get("phase_number"), phase.get("name")))
            start += count
        return out
    return _marker_runs(samples, phases)


def _marker_runs(
    samples: Sequence[ShotSampleRow], phases: Sequence[Mapping[str, Any]]
) -> list[PhaseTransition]:
    """One phase per run of the samples' phase marker, named from the phase lines."""
    names: dict[int, str] = {}
    for phase in phases:
        number, name = phase.get("phase_number"), phase.get("name")
        if isinstance(number, int) and isinstance(name, str):
            names.setdefault(number, name)
    numbers = [sample.phase_number for sample in samples]
    return [
        _transition(index, number, names.get(number or 0, ""))
        for index, number in enumerate(numbers)
        if index == 0 or number != numbers[index - 1]
    ]


def _transition(index: int, number: Any, name: Any) -> PhaseTransition:
    return PhaseTransition(
        sample_index=index,
        phase_number=number if isinstance(number, int) and number >= 0 else 0,
        phase_name=name if isinstance(name, str) else "",
    )


def _phase_spans(transitions: list[PhaseTransition], count: int) -> list[tuple[int, int]]:
    """Each phase's first and last sample; nothing for one phase that is the whole shot."""
    if not transitions:
        return []
    starts = [transition.sample_index for transition in transitions]
    ends = [*(start - 1 for start in starts[1:]), count - 1]
    spans = [(start, end) for start, end in zip(starts, ends, strict=True) if start <= end]
    # A shot of one phase (or a table the engine read as none) has no edge
    # but the shot's own first and last sample, which are kept anyway; the
    # heading should not promise phase edges it does not have.
    return [] if spans == [(0, count - 1)] else spans
