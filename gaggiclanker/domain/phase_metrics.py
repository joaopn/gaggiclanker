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

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple, TypedDict

from gaggiclanker.domain.cup_flow import cup_first_drip_index, cup_flow
from gaggiclanker.domain.metric_language import Expression, ShotData, evaluate_in_phase
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
    "stored_expressions",
]

#: The first log version whose transition table says why each phase ended
#: (``transition_reason``, which was reserved padding in version 5) and whose
#: header carries the reason the whole shot ended.
EXIT_REASONS_FROM_VERSION = 6

#: The "fast flow" window, as the maintainer defined it: the cup flow (``vf``, read at zero
#: when below it), averaged over any window of consecutive samples that spans 1.0 s, is above
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
    """The first window of fast cup flow at high pressure."""

    #: The phase holding the window's first sample, or ``None`` when the shot
    #: has no phase numbers.
    phase_number: int | None
    start_s: float
    end_s: float
    #: The mean cup flow over the window, g/s.
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
    #: Set only on the phase that holds the shot's first puck flow.
    first_drip_s: float
    #: Set only on the phase that holds the moment coffee first reached the cup; needs a scale.
    cup_first_drip_s: float


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
    #: The first window of fast cup flow at high pressure, or ``None``.
    fast_flow: FastFlow | None


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


def last_phase_reached(samples: Sequence[Mapping[str, float]]) -> int | None:
    """The highest phase number any sample was recorded in, or ``None`` with none recorded.

    The one definition of "a phase began": the machine entered it when a sample carries its
    number or a later one. The universal ``skipped`` warning and a signature's ``reached``
    check both read it, so a transition logged with no sample after it is not held on one
    and skipped on the other.
    """
    reached = [int(s["phase"]) for s in samples if "phase" in s]
    return max(reached) if reached else None


def phase_began(number: int, samples: Sequence[Mapping[str, float]]) -> bool:
    """Whether the phase with this number (its index in the profile) began on this shot."""
    last = last_phase_reached(samples)
    return last is not None and number <= last


def phases_not_reached(
    names: Sequence[str] | None, samples: Sequence[Mapping[str, float]]
) -> list[NotReached]:
    """The profile's phases after the last one any sample was recorded in.

    "Reached" is :func:`phase_began`: read from the samples' own phase numbers, the highest
    one being the last phase the machine entered, and every profile phase after it never began.
    Nothing is said without a profile, or about a profile with fewer phases than
    the shot ran (that profile is not the one the shot ran).
    """
    if not names:
        return []
    last = last_phase_reached(samples)
    if last is None:
        return []
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
        if any(cup_flow(s) is None or "cp" not in s for s in window):
            continue
        if any(s["cp"] < floor for s in window):
            continue
        mean = _mean([flow for s in window if (flow := cup_flow(s)) is not None])
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


def _stored(key: str, channel: str, op: str) -> _Stored:
    # The window is a phase, and which one is the position `evaluate_in_phase` is given; the
    # placeholder only makes the expression one a phase window may be read over.
    window = {"phase_number": 0}
    expression = Expression.model_validate({"channel": channel, "op": op, "window": window})
    return _Stored(key, expression)


class _Stored(NamedTuple):
    """A per-phase number the derivation stores, and the expression it is."""

    key: str
    #: Read over each phase in turn; its window is the phase.
    expression: Expression


#: Every per-phase number that is a window statistic, as the expression it is, in the order
#: they are stored. The shot's first drip is a fact, not a statistic, and sits between the two.
_STORED_BEFORE_DRIP: tuple[_Stored, ...] = (
    _stored("puck_flow_mean_ml_s", "puck_flow", "mean"),
    _stored("puck_flow_peak_ml_s", "puck_flow", "max"),
    _stored("temperature_min_c", "temperature", "min"),
    _stored("temperature_target_c", "target_temperature", "mean"),
)
_STORED_AFTER_DRIP: tuple[_Stored, ...] = (
    _stored("pressure_peak_bar", "pressure", "max"),
    _stored("pressure_end_bar", "pressure", "at_end"),
    _stored("cup_weight_end_g", "cup_weight", "at_end"),
    _stored("cup_weight_gained_g", "cup_weight", "gained"),
    _stored("scale_flow_mean_g_s", "scale_flow", "mean"),
    _stored("scale_flow_peak_g_s", "scale_flow", "max"),
    _stored("water_pumped_ml", "water_pumped", "gained"),
)


def stored_expressions() -> dict[str, Expression]:
    """Each stored per-phase number's key, with the expression the derivation reads it by."""
    return {stored.key: stored.expression for stored in (*_STORED_BEFORE_DRIP, *_STORED_AFTER_DRIP)}


def _store(
    entry: PhaseMetrics,
    group: Sequence[_Stored],
    data: ShotData,
    position: int,
) -> None:
    for stored in group:
        value = evaluate_in_phase(stored.expression, data, position).value
        if value is not None:
            entry[stored.key] = value  # type: ignore[literal-required]


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

    data = ShotData.build(
        samples,
        transitions,
        profile_phases=names,
        has_pressure=has_pressure,
        scale_connected=scale_connected,
        final_weight_g=final_weight_g,
    )
    # No puck flow without a pressure sensor: its zeros are no drip, and no moment of one.
    drip = next((i for i, s in enumerate(samples) if has_pressure and s.get("pf", 0.0) > 0.0), None)
    cup_drip = cup_first_drip_index(samples) if scale_connected else None
    metrics: dict[int, PhaseMetrics] = {}
    for index, transition in enumerate(transitions):
        span = data.phases[index]
        if span.start >= span.end:
            continue
        entry = PhaseMetrics()
        recorded_reason = shot["exit_reasons"]
        entry["ended_by"] = (
            _ended_by(transitions, index, final_exit_reason) if recorded_reason else 0
        )
        _store(entry, _STORED_BEFORE_DRIP, data, index)
        if drip is not None and span.start <= drip < span.end and "t" in samples[drip]:
            entry["first_drip_s"] = round(samples[drip]["t"] / 1000.0, 1)
        # Placed by sample, like the puck drip: a rounded phase start would put a drip that
        # is the first sample of a phase into the phase before it.
        if cup_drip is not None and span.start <= cup_drip < span.end and "t" in samples[cup_drip]:
            entry["cup_first_drip_s"] = round(samples[cup_drip]["t"] / 1000.0, 2)
        _store(entry, _STORED_AFTER_DRIP, data, index)
        metrics[transition.phase_number] = entry
    return shot, metrics
