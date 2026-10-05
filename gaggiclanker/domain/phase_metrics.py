"""What each phase of a shot did, as plain numbers, and the facts about the shot as a whole.

A shot's expectations differ by phase: a pre-infusion is meant to pass no
water to the cup, a ramp is meant to climb in pressure, a decline is meant to
fall. A reading taken over the whole shot flattens all of that. So every phase
the transition table records gets its own numbers, each a plain value with a
unit and none of them a band, a grade or a verdict.

Pure Python over the dict-shaped samples the diagnostics engine works on, so
it needs neither the database nor the web. It reads three things the shot's
own bytes and its profile say, and nothing about where the shot is filed:

* the phase table and what ended each phase (:data:`PHASE_EXIT_REASONS`);
* the samples of each phase, by their phase number;
* the profile's phase names, when the shot is linked to a profile.

**Absent sensors are zeros.** The firmware writes every field of every
sample, so a machine with no scale or no pressure sensor reaches here as
columns of zeros. A zero is therefore never read as "there was no weight": the
gates are the shot's own flags (``scale_connected``, ``has_pressure``), and a
number the shot cannot have is left out of the result, never written as 0.

What depends on where a shot is filed (the share of the version's target
yield, the over/under warnings) is not here: it is worked out when a shot is
read, in :mod:`gaggiclanker.domain.warnings` and the shot information
catalogue, so filing, moving or discarding a shot never needs a re-derivation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from gaggiclanker.domain.models import PhaseTransition

__all__ = [
    "EXIT_REASONS_FROM_VERSION",
    "FAST_FLOW_PRESSURE_SHARE",
    "FAST_FLOW_SCALE_FLOW_G_S",
    "FAST_FLOW_WINDOW_MS",
    "FastFlow",
    "PhaseMetrics",
    "ShotMetrics",
    "compute_phase_metrics",
    "find_fast_flow",
    "phases_not_reached",
]

#: The first log version whose transition table says why each phase ended
#: (``transition_reason``, which was reserved padding in version 5) and whose
#: header carries the reason the whole shot ended.
EXIT_REASONS_FROM_VERSION = 6

#: The "fast flow" window, as the maintainer defined it: the scale flow ``vf``,
#: averaged over any window of consecutive samples that spans 1.0 s, is above
#: 3.0 g/s while every sample in the window has a pressure of at least 80 % of
#: the shot's peak. A window is the run of samples from one to the first whose
#: time is 1.0 s or more after it, both ends included, and its mean is the plain
#: mean of those samples' scale flow. At exactly 3.0 g/s it does not fire.
FAST_FLOW_SCALE_FLOW_G_S = 3.0
FAST_FLOW_WINDOW_MS = 1000
FAST_FLOW_PRESSURE_SHARE = 0.8

#: Logged values carry one or two decimals; float arithmetic on them must not
#: decide a comparison that sits exactly on a threshold.
_EDGE_EPSILON = 1e-9


class FastFlow(TypedDict):
    """The first window of fast scale flow at high pressure."""

    #: The phase holding the window's first sample, or ``None`` when the shot
    #: has no phase numbers.
    phase_number: int | None
    start_s: float
    end_s: float
    #: The mean scale flow over the window, g/s.
    mean_g_s: float
    #: The lowest pressure in the window, and the shot's peak it is judged against.
    pressure_min_bar: float
    peak_pressure_bar: float


class PhaseMetrics(TypedDict, total=False):
    """One phase's numbers. A key is absent when the shot cannot have the value.

    The phase's start, duration, mean pressure and temperature, its adherence
    and its resistance sit beside these on the phase itself; this is the rest.
    """

    #: Why the phase ended, as the firmware's exit-reason code (0 is "Unknown":
    #: a log from before the firmware recorded it).
    ended_by: int
    #: Needs a scale.
    cup_weight_end_g: float
    cup_weight_gained_g: float
    scale_flow_mean_g_s: float
    scale_flow_peak_g_s: float
    puck_flow_mean_ml_s: float
    puck_flow_peak_ml_s: float
    #: Needs the pumped-water counter (log version 7, a board that fills it).
    water_pumped_ml: float
    #: Need a pressure sensor.
    pressure_peak_bar: float
    pressure_end_bar: float
    temperature_min_c: float
    temperature_target_c: float
    #: Set only on the phase that holds the shot's first drip.
    first_drip_s: float


class NotReached(TypedDict):
    phase_number: int
    name: str


class ShotMetrics(TypedDict):
    """What is true of the shot as a whole, from its bytes and its profile."""

    #: False for a log with no transition table (version 4 and earlier): the
    #: shot has shot-wide numbers only, and says so.
    per_phase: bool
    #: Whether the log records why each phase ended (version 6 and later).
    exit_reasons: bool
    #: The linked profile's phase names, in order; ``None`` with no profile.
    profile_phases: list[str] | None
    #: The profile's phases the shot never began, in order.
    phases_not_reached: list[NotReached]
    #: The first window of fast scale flow at high pressure, or ``None``.
    fast_flow: FastFlow | None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def profile_phase_names(profile: Mapping[str, Any] | None) -> list[str] | None:
    """The phase names of a profile document, in order, or ``None`` when it has none to read."""
    if profile is None:
        return None
    phases = profile.get("phases")
    if not isinstance(phases, list) or not phases:
        return None
    return [
        str(phase.get("name") or "").strip() if isinstance(phase, Mapping) else ""
        for phase in phases
    ]


def phases_not_reached(
    names: Sequence[str] | None, samples: Sequence[Mapping[str, float]]
) -> list[NotReached]:
    """The profile's phases after the last one any sample was recorded in.

    "Reached" is read from the samples' own phase numbers: the highest one is the
    last phase the machine entered, and every profile phase after it never began.
    Nothing is said without a profile, or about a profile with fewer phases than
    the shot ran (that profile is not the one the shot ran).
    """
    if not names:
        return []
    reached = [int(s["phase"]) for s in samples if "phase" in s]
    if not reached:
        return []
    last = max(reached)
    return [
        NotReached(phase_number=number, name=names[number])
        for number in range(last + 1, len(names))
    ]


def find_fast_flow(
    samples: Sequence[Mapping[str, float]], *, has_pressure: bool, scale_connected: bool
) -> FastFlow | None:
    """The first window where the scale flow was fast at high pressure, or ``None``.

    Needs a scale and a pressure sensor: on a machine with either missing the
    column is zeros, and a window of zeros fires nothing, but the gate is the
    shot's own flags and not what the zeros happen to compute to.
    """
    if not (has_pressure and scale_connected):
        return None
    rows = [s for s in samples if "t" in s]
    peak = max((s["cp"] for s in rows if "cp" in s), default=0.0)
    if peak <= 0:
        return None
    floor = FAST_FLOW_PRESSURE_SHARE * peak - _EDGE_EPSILON
    last = 0
    for first in range(len(rows)):
        last = max(last, first)
        started = rows[first]["t"]
        while last < len(rows) and rows[last]["t"] - started < FAST_FLOW_WINDOW_MS:
            last += 1
        if last >= len(rows):
            return None
        window = rows[first : last + 1]
        if any("vf" not in s or "cp" not in s for s in window):
            continue
        if any(s["cp"] < floor for s in window):
            continue
        mean = _mean([s["vf"] for s in window])
        if mean > FAST_FLOW_SCALE_FLOW_G_S + _EDGE_EPSILON:
            phase = rows[first].get("phase")
            return FastFlow(
                phase_number=int(phase) if phase is not None else None,
                start_s=round(started / 1000.0, 2),
                end_s=round(rows[last]["t"] / 1000.0, 2),
                mean_g_s=round(mean, 2),
                pressure_min_bar=round(min(s["cp"] for s in window), 1),
                peak_pressure_bar=round(peak, 1),
            )
    return None


def _water_by_sample(samples: Sequence[Mapping[str, float]]) -> list[float | None]:
    """The pumped-water counter, with every sample at or after a reset left out.

    The controller zeroes the counter when it stops the pump on a weight target
    (tens of millilitres, then next to nothing on the following sample), so
    the samples that follow a fall are a counter for another thing and are not
    read. ``None`` marks a sample
    that is not read: a counter that was not recorded, or one that has been
    reset. A counter that never rose over the samples before any reset is no
    measurement (a board that does not fill it sends zeros), so every sample is
    ``None`` then.
    """
    values: list[float | None] = []
    previous: float | None = None
    reset = False
    for sample in samples:
        value = _number(sample.get("wp"))
        if value is None or value < 0:
            values.append(None)
            continue
        if reset or (previous is not None and value < previous):
            reset = True
            values.append(None)
            continue
        previous = value
        values.append(value)
    counted = [v for v in values if v is not None]
    if not counted or max(counted) <= 0:
        return [None] * len(values)
    return values


def _ended_by(transitions: Sequence[PhaseTransition], index: int, final_exit_reason: int) -> int:
    """Why the phase at ``index`` of the transition table ended.

    A row's reason is why the *previous* phase ended, so a phase's own end is
    the next row's reason, and the last phase's is the header's.
    """
    if index + 1 < len(transitions):
        return transitions[index + 1].transition_reason
    return final_exit_reason


def compute_phase_metrics(
    samples: Sequence[Mapping[str, float]],
    transitions: Sequence[PhaseTransition],
    *,
    version: int,
    final_exit_reason: int,
    has_pressure: bool,
    scale_connected: bool,
    profile: Mapping[str, Any] | None = None,
    final_weight_g: float | None = None,
) -> tuple[ShotMetrics, dict[int, PhaseMetrics]]:
    """The shot's facts, and each recorded phase's numbers by its phase number.

    ``samples`` are the diagnostics engine's dicts (a key is present only when
    the firmware recorded the field). A phase with no sample of its own gets no
    entry. Without a transition table (version 4 and earlier) no phase has
    numbers: the shot is one phase of the engine's making, and ``per_phase`` says so.
    """
    names = profile_phase_names(profile)
    shot = ShotMetrics(
        per_phase=bool(transitions),
        exit_reasons=version >= EXIT_REASONS_FROM_VERSION,
        profile_phases=names,
        phases_not_reached=phases_not_reached(names, samples),
        fast_flow=find_fast_flow(
            samples, has_pressure=has_pressure, scale_connected=scale_connected
        ),
    )
    if not transitions:
        return shot, {}

    water = _water_by_sample(samples)
    drip = next((i for i, s in enumerate(samples) if s.get("pf", 0.0) > 0.0), None)
    count = len(samples)
    metrics: dict[int, PhaseMetrics] = {}
    # What the previous phase left behind, for "gained" and the water's rise.
    previous_weight = 0.0
    previous_water = 0.0
    for index, transition in enumerate(transitions):
        start = min(transition.sample_index, count)
        end = (
            min(transitions[index + 1].sample_index, count)
            if index + 1 < len(transitions)
            else count
        )
        span = range(start, end)
        if not span:
            continue
        rows = [samples[i] for i in span]
        entry = PhaseMetrics()
        recorded_reason = shot["exit_reasons"]
        entry["ended_by"] = (
            _ended_by(transitions, index, final_exit_reason) if recorded_reason else 0
        )

        flows = [s["pf"] for s in rows if "pf" in s]
        if flows:
            entry["puck_flow_mean_ml_s"] = round(_mean(flows), 2)
            entry["puck_flow_peak_ml_s"] = round(max(flows), 2)
        temperatures = [s["ct"] for s in rows if "ct" in s]
        if temperatures:
            entry["temperature_min_c"] = round(min(temperatures), 1)
        targets = [s["tt"] for s in rows if s.get("tt", 0.0) > 0]
        if targets:
            entry["temperature_target_c"] = round(_mean(targets), 1)
        if drip is not None and span.start <= drip < span.stop and "t" in samples[drip]:
            entry["first_drip_s"] = round(samples[drip]["t"] / 1000.0, 1)

        if has_pressure:
            pressures = [s["cp"] for s in rows if "cp" in s]
            if pressures:
                entry["pressure_peak_bar"] = round(max(pressures), 1)
                entry["pressure_end_bar"] = round(pressures[-1], 1)

        if scale_connected:
            weights = [s["v"] for s in rows if "v" in s]
            if weights:
                end_weight = weights[-1]
                # The scale reads 0 once the cup is lifted or it resets, so a shot can
                # end in zeros: the last phase then ends at the weight the Yield is
                # (see `Slog.volume_g`), not at a cup that vanished at the last sample.
                if end >= count and end_weight <= 0 and (final_weight_g or 0.0) > 0:
                    end_weight = final_weight_g or 0.0
                entry["cup_weight_end_g"] = round(end_weight, 1)
                entry["cup_weight_gained_g"] = round(end_weight - previous_weight, 1)
                previous_weight = end_weight
            scale_flows = [s["vf"] for s in rows if "vf" in s]
            if scale_flows:
                entry["scale_flow_mean_g_s"] = round(_mean(scale_flows), 2)
                entry["scale_flow_peak_g_s"] = round(max(scale_flows), 2)

        counted = [w for i in span if (w := water[i]) is not None]
        if counted:
            last = counted[-1]
            entry["water_pumped_ml"] = round(last - previous_water, 1)
            previous_water = last

        metrics[transition.phase_number] = entry
    return shot, metrics
