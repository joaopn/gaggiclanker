"""Deterministic shot diagnostics: the numbers, and no verdicts on them.

Vendored from gaggimate-mcp (`transformers/shot.py`, MIT, commit 0af88ad) and
adapted to this project's `.slog` model — see `gaggiclanker/domain/VENDORED.md`
for the licence and the list of changes. What was kept of upstream is the
arithmetic: puck resistance, the adherence of the measured pressure and flow to
the profile, and the shot's summary statistics. What was dropped is everything
that judged those numbers: the threshold bands and their labels (calibrated on
other people's shots), the channeling risk, the execution score. A number is
reported as a number; what it means for a given profile is for the reader, and
for a profile's own signature to say. What is plainly wrong with a shot
whatever its profile is is :mod:`gaggiclanker.domain.warnings`; what each phase
did is :mod:`gaggiclanker.domain.phase_metrics`.

What changed here, and why:

* Input is a :class:`~gaggiclanker.domain.slog.Slog`, so `t` is real elapsed
  milliseconds (v6+) rather than a sample index times the nominal interval, and
  the per-sample phase comes from the header's transition table.
* Everything pressure-derived is gated on ``has_pressure``. GaggiMate Standard
  boards have no pressure sensor and record a hard zero, which would otherwise
  produce a confident resistance of nothing at all. A missing sensor must read
  as *absent*, not as *good*.
* Puck resistance is the machine's own ``pr²`` when the shot recorded a valid
  ``pr`` (see :func:`_build_resistance`), and upstream's ``P / F²`` otherwise.
  ``pr²`` is the same quadratic model on the same scale.
* Profile compliance is graded over the samples of the phases that steer by each
  target, read from the shot's profile (`phase_controls`), and flow is compared
  with the pump flow; see :func:`_steering` and `VENDORED.md` item 8.

A sample's field is absent from the working dict when the firmware never
recorded it (its `fieldsMask` bit was clear). That is why the code reads
``s.get('cp', 0.0)`` for a value but ``'tp' in s`` for a decision: "not
recorded" and "recorded as zero" are different facts.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal, NamedTuple, NotRequired, TypedDict

from gaggiclanker.domain.models import PhaseTransition
from gaggiclanker.domain.phase_control import PhaseControl
from gaggiclanker.domain.phase_metrics import PhaseMetrics
from gaggiclanker.domain.slog import Slog

#: A sample flattened to plain numbers. Keys are the `.slog` field names; a key
#: is present only when the firmware recorded that field.
SampleDict = dict[str, float]

DetailLevel = Literal["summary", "per_phase", "per_phase_detailed"]
VALID_DETAIL_LEVELS: tuple[str, ...] = ("summary", "per_phase", "per_phase_detailed")

#: The `.slog` fields a :data:`SampleDict` may carry, besides ``phase``.
SAMPLE_FIELDS = ("t", "tt", "ct", "tp", "cp", "fl", "tf", "pf", "vf", "v", "ev", "pr", "wp")


# ═══════════════════════════════════════════════════════════════════
# OUTPUT SHAPES
# ═══════════════════════════════════════════════════════════════════


class TemperatureSummary(TypedDict):
    min_c: float
    max_c: float
    avg_c: float
    target_avg_c: float


class PressureSummary(TypedDict):
    min_bar: float
    max_bar: float
    avg_bar: float
    peak_time_s: float


class FlowSummary(TypedDict):
    total_volume_ml: float
    avg_flow_ml_s: float
    peak_flow_ml_s: float
    time_to_first_drip_s: float | None


class ExtractionSummary(TypedDict):
    preinfusion_time_s: float
    main_extraction_time_s: float
    total_time_s: float


class ShotSummary(TypedDict):
    temperature: TemperatureSummary
    pressure: PressureSummary | None
    flow: FlowSummary
    extraction: ExtractionSummary


class TransformedSample(TypedDict):
    time_seconds: float
    temperature_c: float
    pressure_bar: float
    flow_ml_s: float
    weight_g: float


class PhaseDiagnostics(TypedDict, total=False):
    """What a phase's samples say about the profile and the puck. A key is absent when the
    shot cannot have the value (no pressure sensor, no target to follow, too few samples)."""

    phase_type: str
    avg_pressure_bar: float
    avg_flow_ml_s: float
    pressure_rmse_bar: float
    flow_rmse_ml_s: float
    resistance_avg: float
    resistance_slope: float
    resistance_source: ResistanceSource
    # preinfusion
    ramp_rate_bar_s: float
    saturation_time_s: float
    # decline
    taper_rate_bar_s: float
    taper_smoothness: float


class PhaseData(TypedDict):
    name: str
    phase_number: int
    start_time_seconds: float
    duration_seconds: float
    sample_count: int
    avg_temperature_c: float
    avg_pressure_bar: float
    total_flow_ml: float
    samples: NotRequired[list[TransformedSample]]
    diagnostics: NotRequired[PhaseDiagnostics]
    #: What the phase did, in plain numbers; added where the shot is derived
    #: (``sync/derive.py``), since it needs what only that step knows (the
    #: scale flag, the profile).
    metrics: NotRequired[PhaseMetrics]


#: Where a shot's resistance samples came from: the machine's own per-sample
#: ``pr`` (squared, so it is the quadratic model's quantity), or our ``P / F²``.
ResistanceSource = Literal["machine", "computed"]


class ResistanceDiagnostics(TypedDict):
    """Puck resistance ``R`` (quadratic Darcy model, ``R = P / F²``).

    It folds grind fineness, dose and puck prep into one number whose *shape over
    time* is the interesting part. ``R`` is the machine's own ``pr²`` when the
    shot carries it and ``P / F²`` from the logged pressure and flow otherwise;
    ``source`` says which. Its mean and its slope over the window, and nothing
    that grades either.
    """

    source: ResistanceSource
    avg: float
    slope: float


class ExtractionMetrics(TypedDict):
    flow_avg_brew_ml_s: float


class WeightDiagnostics(TypedDict):
    rate_avg_g_s: float | None
    scale_connected: bool


#: Whether one of the two adherences was worked out, and if not, why.
#:
#: * ``graded``: the profile steers by it and enough samples were measured;
#: * ``not_applicable``: the profile has no phase that steers by it (a pressure
#:   profile has no flow to follow), so there is nothing to grade and nothing
#:   missing;
#: * ``not_graded``: it should have a number and does not (too few samples, in
#:   all, because the limit held nearly every one, or the shot did not record the
#:   measurement).
#:
#: A reader tells the two apart: ``not_applicable`` is not a gap, ``not_graded`` is.
Grading = Literal["graded", "not_applicable", "not_graded"]


class ProfileComplianceMetrics(TypedDict):
    """RMSE of actual against commanded pressure and flow.

    Each is graded only over the samples of the phases that steer by it, read
    from the profile the shot was brewed with (see :class:`_Steering`): the
    firmware logs both targets on every advanced phase, but one of them is a
    limit, and grading a pressure shot's flow against its flow limit made every
    pressure profile look like it had missed its flow.

    Flow is compared with the *pump* flow (`fl`), because that is what the
    firmware's flow mode controls: it converts the target to a pump duty cycle
    through the pump's flow model and never looks at the puck flow.

    Neither is a grind signal by itself: the pump flow is the pump model's
    estimate for the power the controller chose (not a measurement), so it
    leaves its target only when the pump runs out of power, a pressure limit
    takes over or the smoothing lags, and the controller drives the pump to hold
    pressure. A pressure overshoot above 1 bar is remarkable — it means the
    controller ran out of room. A block with no profile behind it does not exist
    (``None`` in :class:`ShotDiagnostics`).
    """

    pressure_rmse_bar: float | None
    flow_rmse_ml_s: float | None
    max_pressure_overshoot_bar: float | None
    max_pressure_undershoot_bar: float | None
    max_flow_overshoot_ml_s: float | None
    max_flow_undershoot_ml_s: float | None
    pressure_grading: Grading
    flow_grading: Grading


class ShotDiagnostics(TypedDict):
    """Full diagnostics. The pressure-derived blocks are None on a machine
    without a pressure sensor — see `has_pressure`."""

    has_pressure: bool
    resistance: ResistanceDiagnostics | None
    extraction: ExtractionMetrics
    weight: WeightDiagnostics
    profile_compliance: ProfileComplianceMetrics | None


class SummaryDiagnostics(TypedDict):
    """The small set of numbers that carries most of the signal."""

    has_pressure: bool
    resistance_avg: float | None
    resistance_slope: float | None
    resistance_source: ResistanceSource | None
    pressure_rmse_bar: float | None
    max_overshoot_bar: float | None
    flow_rmse_ml_s: float | None
    max_flow_overshoot_ml_s: float | None
    pressure_grading: Grading
    flow_grading: Grading
    scale_connected: bool


class TransformedShot(TypedDict):
    shot_id: str | None
    profile_name: str
    profile_id: str
    timestamp: int
    duration_seconds: float
    final_weight_g: float | None
    has_pressure: bool
    summary: ShotSummary
    phases: list[PhaseData]
    diagnostics: ShotDiagnostics | SummaryDiagnostics | None
    detail_level: str


# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════


#: Fewer samples than this in the window the largest pressure drop is read over
#: (see :func:`_steady_state`) and none is reported.
_MIN_STEADY_STATE_SAMPLES: int = 5

#: Fewer samples than this, or fewer brew-phase samples than the second, and
#: there are no shot diagnostics at all.
_MIN_SHOT_SAMPLES: int = 5
_MIN_BREW_SAMPLES: int = 3

#: The machine's own resistance is used for a window only when at least this
#: many of its samples carry a valid one; below it, a mean and a slope of the
#: machine's values would rest on a couple of points, and ours is used instead.
_MIN_MACHINE_RESISTANCE_SAMPLES: int = 3

#: The firmware's own validity range for ``pr`` (its shot analyzer reads a sample
#: only when ``0 < pr < 100``): zero is "not yet estimated" (a Standard board, or
#: before the estimator started) and the top of the range is its clamp.
_MACHINE_RESISTANCE_MAX: float = 100.0


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════


def as_sample_dicts(slog: Slog) -> list[SampleDict]:
    """Flatten a parsed shot's samples to the dicts the engine works on.

    A field the firmware did not record is left *out* of the dict rather than
    defaulted, because several metrics branch on whether it was recorded.
    """
    out: list[SampleDict] = []
    for sample in slog.samples:
        row: SampleDict = {}
        for name in SAMPLE_FIELDS:
            value = getattr(sample, name)
            if value is not None:
                row[name] = float(value)
        if sample.phase is not None:
            row["phase"] = float(sample.phase)
        out.append(row)
    return out


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_std(values: list[float]) -> float:
    """Population standard deviation; 0.0 for fewer than two values."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def _linear_slope(values: list[float], dt: float) -> float:
    """Least-squares slope in units per second; 0.0 if there is nothing to fit."""
    n = len(values)
    if n < 2 or dt <= 0:
        return 0.0
    x = [i * dt for i in range(n)]
    x_mean = sum(x) / n
    y_mean = sum(values) / n
    numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, values, strict=True))
    denominator = sum((xi - x_mean) ** 2 for xi in x)
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _trim_ramp_up[T](
    pressures: list[float],
    flows: list[float],
    samples: list[T],
    threshold_pct: float = 0.90,
) -> tuple[list[float], list[float], list[T]]:
    """Drop the ramp-up: everything before pressure first reaches 90 % of peak.

    Ramping is the profile's intent, not the puck's behaviour, and leaving it
    in makes every pressure-led shot look unstable. ``samples`` rides along
    with the two value lists: the sample dicts themselves, or their positions
    in the shot when a caller needs to know *which* samples were kept.
    """
    if not pressures:
        return pressures, flows, samples

    peak = max(pressures)
    if peak <= 0:
        return pressures, flows, samples

    target = peak * threshold_pct
    for i, p in enumerate(pressures):
        if p >= target:
            return pressures[i:], flows[i:], samples[i:]

    return pressures, flows, samples


def _strip_flow_edges[T](
    pressures: list[float],
    flows: list[float],
    samples: list[T],
    thr: float = 0.1,
) -> tuple[list[float], list[float], list[T], tuple[int, int]]:
    """Drop leading and trailing samples with flow below `thr` ml/s.

    Leading: pressure ramped but the valve has not opened. Trailing: the
    volumetric cutoff fired and pressure is trapped in the puck, which falls
    away on its own and is not a drop worth keeping the curve's shape for.

    Returns the trimmed lists plus how many samples came off each end.
    """
    n = len(flows)
    i = 0
    while i < n and flows[i] < thr:
        i += 1
    if i == n:
        return [], [], [], (n, 0)
    j = n - 1
    while j > i and flows[j] < thr:
        j -= 1
    return pressures[i : j + 1], flows[i : j + 1], samples[i : j + 1], (i, n - 1 - j)


def _compute_rmse(actual: list[float], target: list[float]) -> float:
    if not actual or len(actual) != len(target):
        return 0.0
    sse = sum((a - t) ** 2 for a, t in zip(actual, target, strict=True))
    return math.sqrt(sse / len(actual))


def _round2(value: float) -> float:
    return round(value * 100) / 100


def _round1(value: float) -> float:
    return round(value * 10) / 10


# ═══════════════════════════════════════════════════════════════════
# PHASE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════

_PREINFUSION_KEYWORDS = (
    "preinfusion",
    "pre-infusion",
    "pi",
    "soak",
    "bloom",
    "fill",
    "preinfuse",
)

_DECLINE_KEYWORDS = (
    "decline",
    "taper",
    "ramp-down",
    "ramp down",
    "cool down",
    "cooldown",
)


def _classify_phase_by_name(name: str) -> str | None:
    """'preinfusion' / 'decline' / None, by substring so creative names match."""
    normalized = name.lower().strip()
    for kw in _PREINFUSION_KEYWORDS:
        if kw in normalized:
            return "preinfusion"
    for kw in _DECLINE_KEYWORDS:
        if kw in normalized:
            return "decline"
    return None


def _classify_phase_by_telemetry(
    phase_samples: list[SampleDict], phase_index: int, total_phases: int
) -> str:
    """Fallback when the name says nothing: read the pressure trace instead."""
    pressures = [s.get("cp", 0.0) for s in phase_samples]
    if len(pressures) < 2:
        return "brew"

    avg_p = sum(pressures) / len(pressures)
    slope = (pressures[-1] - pressures[0]) / max(len(pressures) - 1, 1)

    if phase_index == 0 and avg_p < 5.0 and slope >= 0:
        return "preinfusion"
    if phase_index == total_phases - 1 and phase_index > 0 and slope < -0.3:
        return "decline"
    return "brew"


def _classify_phase(
    name: str,
    phase_samples: list[SampleDict] | None = None,
    phase_index: int = 0,
    total_phases: int = 1,
) -> str:
    """Classify a phase as preinfusion / brew / decline; name first, then shape."""
    result = _classify_phase_by_name(name)
    if result is not None:
        return result
    if phase_samples is not None:
        return _classify_phase_by_telemetry(phase_samples, phase_index, total_phases)
    return "brew"


def _phase_ranges(
    count: int, transitions: list[PhaseTransition]
) -> list[tuple[PhaseTransition, range]]:
    """Pair each transition with the positions of the samples recorded while it was active.

    Clamped to the ``count`` samples there are, as a slice would be: a table
    can name a sample index the recording never reached.
    """
    result: list[tuple[PhaseTransition, range]] = []
    for i, transition in enumerate(transitions):
        start = transition.sample_index
        end = transitions[i + 1].sample_index if i + 1 < len(transitions) else count
        result.append((transition, range(min(start, count), min(end, count))))
    return result


def _brew_phase_positions(
    samples: list[SampleDict], transitions: list[PhaseTransition]
) -> list[int]:
    """Where the samples of :func:`_get_brew_phase_samples` sit in the shot, in order."""
    if not samples:
        return []

    if transitions:
        brew: list[int] = []
        for transition, span in _phase_ranges(len(samples), transitions):
            if _classify_phase_by_name(transition.phase_name) == "preinfusion":
                continue
            brew.extend(span)
        return brew if brew else list(range(len(samples)))

    pressures = [s.get("cp", 0.0) for s in samples]
    peak = max(pressures) if pressures else 0.0
    if peak > 0:
        threshold = peak * 0.5
        for i, p in enumerate(pressures):
            if p >= threshold:
                return list(range(i, len(samples)))
    return list(range(len(samples)))


def _get_brew_phase_samples(
    samples: list[SampleDict], transitions: list[PhaseTransition]
) -> list[SampleDict]:
    """Everything that is not a pre-infusion phase.

    With no transition table (v4 and earlier) there is nothing to go on but the
    trace, so fall back to "after pressure first reaches half its peak".
    """
    return [samples[i] for i in _brew_phase_positions(samples, transitions)]


# ═══════════════════════════════════════════════════════════════════
# THE WINDOW THE LARGEST PRESSURE DROP IS READ OVER
# ═══════════════════════════════════════════════════════════════════


def _steady_state[T](
    brew_pressures: list[float], brew_flows: list[float], brew_samples: list[T]
) -> tuple[list[float], list[float], list[T], int, tuple[int, int]]:
    """The window the largest pressure drop is read over: the brew less ramp-up and dry edges.

    Everything before pressure first reaches 90 % of its peak is a ramp, and the
    zero-flow samples at either end are a valve not yet open or pressure trapped
    in the puck after a weight stop. Used only to find the moment the curve keeps
    (:func:`largest_pressure_drop`); no number about the shot is read from it.

    Returns the trimmed lists, how many ramp-up samples came off, and how many
    zero-flow samples came off each end.
    """
    ss_pressures, ss_flows, ss_samples = _trim_ramp_up(brew_pressures, brew_flows, brew_samples)
    ramp_excluded = len(brew_pressures) - len(ss_pressures)
    ss_pressures, ss_flows, ss_samples, edges = _strip_flow_edges(
        ss_pressures, ss_flows, ss_samples
    )
    return ss_pressures, ss_flows, ss_samples, ramp_excluded, edges


def _pressure_rates(pressures: list[float], dt: float) -> list[float]:
    """Sample-to-sample pressure change in bar/s: entry ``i`` is samples ``i`` to ``i + 1``."""
    return [(pressures[i] - pressures[i - 1]) / dt for i in range(1, len(pressures))]


# ═══════════════════════════════════════════════════════════════════
# SUMMARY STATISTICS
# ═══════════════════════════════════════════════════════════════════


def calculate_total_volume(samples: list[SampleDict], interval_ms: int) -> float:
    """Integrate puck flow over the shot, in ml."""
    interval_seconds = interval_ms / 1000.0
    return _round1(sum(s.get("pf", 0.0) for s in samples) * interval_seconds)


# ═══════════════════════════════════════════════════════════════════
# WHERE THE HEADLINE MOMENTS ARE
# ═══════════════════════════════════════════════════════════════════
#
# The sample behind a number, for a reader that shows the curve and must not
# lose the moment a diagnostic is about. Each is the engine's own rule, used by
# the engine itself, so the sample and the number cannot drift apart.


def first_drip_index(samples: list[SampleDict]) -> int | None:
    """The sample the time to first drip is read from: the first puck flow above zero.

    ``None`` when puck flow never rose, or was not recorded.
    """
    flows = [s["pf"] for s in samples if "pf" in s]
    return next((i for i, flow in enumerate(flows) if flow > 0.0), None)


def peak_pressure_index(samples: list[SampleDict]) -> int | None:
    """The sample the time of peak pressure is read from: the first at the maximum.

    ``None`` when no pressure above zero was recorded.
    """
    pressures = [s["cp"] for s in samples if "cp" in s]
    peak = max(pressures) if pressures else 0.0
    return pressures.index(peak) if pressures and peak > 0 else None


def largest_pressure_drop(
    samples: list[SampleDict], transitions: list[PhaseTransition], dt: float
) -> tuple[int, int] | None:
    """The two samples the steepest single-sample fall in pressure runs between.

    Read over the brew window with its ramp-up and dry edges trimmed (see
    :func:`_steady_state`), the first of equal steps winning. ``None`` wherever
    there is nothing to say: too few samples, too few brew or window samples. The
    caller decides whether the shot had a pressure sensor at all. A landmark the
    curve keeps its shape around; no number about the shot is read from it.
    """
    if len(samples) < _MIN_SHOT_SAMPLES or dt <= 0:
        return None
    brew = _brew_phase_positions(samples, transitions)
    if len(brew) < _MIN_BREW_SAMPLES:
        return None
    pressures = [samples[i].get("cp", 0.0) for i in brew]
    flows = [samples[i].get("pf", 0.0) for i in brew]
    ss_pressures, _, positions, _, _ = _steady_state(pressures, flows, brew)
    if len(ss_pressures) < _MIN_STEADY_STATE_SAMPLES:
        return None
    rates = _pressure_rates(ss_pressures, dt)
    step = rates.index(min(rates))
    return positions[step], positions[step + 1]


def calculate_summary(slog: Slog, *, has_pressure: bool | None = None) -> ShotSummary:
    """Headline statistics for a shot."""
    samples = as_sample_dicts(slog)
    pressure_ok = slog.has_pressure if has_pressure is None else has_pressure

    temperatures = [s["ct"] for s in samples if "ct" in s]
    target_temps = [s["tt"] for s in samples if "tt" in s]
    pressures = [s["cp"] for s in samples if "cp" in s]
    flows = [s["pf"] for s in samples if "pf" in s]
    times = [s.get("t", 0.0) / 1000.0 for s in samples]

    temp_summary = TemperatureSummary(
        min_c=_round1(min(temperatures)) if temperatures else 0.0,
        max_c=_round1(max(temperatures)) if temperatures else 0.0,
        avg_c=_round1(_safe_mean(temperatures)),
        target_avg_c=_round1(_safe_mean(target_temps)),
    )

    peak_pressure = max(pressures) if pressures else 0.0
    peak = peak_pressure_index(samples)
    peak_index = peak if peak is not None else 0
    peak_time = times[peak_index] if peak_index < len(times) else 0.0

    pressure_summary: PressureSummary | None = None
    if pressure_ok:
        pressure_summary = PressureSummary(
            min_bar=_round1(min(pressures)) if pressures else 0.0,
            max_bar=_round1(max(pressures)) if pressures else 0.0,
            avg_bar=_round1(_safe_mean(pressures)),
            peak_time_s=_round1(peak_time),
        )

    drip = first_drip_index(samples)
    time_to_first_drip = _round1(times[drip]) if drip is not None else None

    flow_summary = FlowSummary(
        total_volume_ml=calculate_total_volume(samples, slog.sample_interval),
        avg_flow_ml_s=_round1(_safe_mean(flows)),
        peak_flow_ml_s=_round1(max(flows)) if flows else 0.0,
        time_to_first_drip_s=time_to_first_drip,
    )

    # Pre-infusion is read off the pressure trace: the moment pressure first
    # reaches half its peak is where wetting ends and extraction begins.
    preinfusion_time = 0.0
    if peak_pressure > 0:
        threshold = peak_pressure * 0.5
        for i, pressure in enumerate(pressures):
            if pressure >= threshold:
                preinfusion_time = times[i] if i < len(times) else 0.0
                break

    total_time = slog.duration_ms / 1000.0
    extraction_summary = ExtractionSummary(
        preinfusion_time_s=_round1(preinfusion_time),
        main_extraction_time_s=_round1(max(0.0, total_time - preinfusion_time)),
        total_time_s=_round1(total_time),
    )

    return ShotSummary(
        temperature=temp_summary,
        pressure=pressure_summary,
        flow=flow_summary,
        extraction=extraction_summary,
    )


# ═══════════════════════════════════════════════════════════════════
# FULL AND SUMMARY DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════════


def compute_shot_diagnostics(
    slog: Slog,
    *,
    has_pressure: bool | None = None,
    phase_controls: Sequence[PhaseControl] | None = None,
) -> ShotDiagnostics | None:
    """Full diagnostics, or None when there is too little to say anything.

    Too little means fewer than 5 samples overall or fewer than 3 in the brew
    phase. `has_pressure` defaults to whether the trace carries any non-zero
    pressure at all, which is how a Standard board shows up.

    `phase_controls` is what each phase of the shot's profile steers by (see
    :func:`~gaggiclanker.domain.phase_control.phase_controls`). Without it the
    profile compliance is not worked out at all.
    """
    samples = as_sample_dicts(slog)
    if len(samples) < _MIN_SHOT_SAMPLES:
        return None

    pressure_ok = slog.has_pressure if has_pressure is None else has_pressure
    dt = slog.sample_interval / 1000.0

    brew_positions = _brew_phase_positions(samples, slog.transitions)
    brew_samples = [samples[i] for i in brew_positions]
    if len(brew_samples) < _MIN_BREW_SAMPLES:
        return None

    steering = _steering(samples, phase_controls)
    brew_flows = [s.get("pf", 0.0) for s in brew_samples]
    brew_weights = [s.get("v", 0.0) for s in brew_samples]
    has_scale = any(w > 0 for w in brew_weights)

    resistance: ResistanceDiagnostics | None = None
    profile_compliance: ProfileComplianceMetrics | None = None

    if pressure_ok:
        resistance = _build_resistance(brew_samples, dt)
        profile_compliance = _compute_profile_compliance(samples, steering)

    extraction = ExtractionMetrics(flow_avg_brew_ml_s=_round2(_safe_mean(brew_flows)))

    w_rate_avg: float | None = None
    if has_scale:
        weight_rates = [
            rate
            for i in range(1, len(brew_weights))
            # Only accumulating weight: a negative step is the scale settling
            # or the cup being nudged, not coffee leaving the puck.
            if (rate := (brew_weights[i] - brew_weights[i - 1]) / dt) >= 0
        ]
        if weight_rates:
            w_rate_avg = _round2(_safe_mean(weight_rates))

    return ShotDiagnostics(
        has_pressure=pressure_ok,
        resistance=resistance,
        extraction=extraction,
        weight=WeightDiagnostics(rate_avg_g_s=w_rate_avg, scale_connected=has_scale),
        profile_compliance=profile_compliance,
    )


def _build_resistance(window: list[SampleDict], dt: float) -> ResistanceDiagnostics:
    """Puck resistance ``R``, its mean and its slope, over a window of samples.

    The one place the quantity is computed: the full block, the summary and each
    phase all call it, so they cannot drift apart.

    The window is the samples with a flow above 0.1 ml/s: dividing by a
    near-zero flow produces an arbitrarily large number that says nothing about
    the puck. Over that window the machine's own value is preferred. The
    firmware computes ``pr = sqrt(P) / Q_puck`` from its compensated puck-flow
    estimate, so ``pr²`` is the same quadratic model as ``P / F²`` on the same
    scale. A window with fewer than
    :data:`_MIN_MACHINE_RESISTANCE_SAMPLES` valid ``pr`` samples (no such field,
    a board without the estimator, zeros, the clamp) falls back to ``P / F²``.
    """
    flowing = [s for s in window if s.get("pf", 0.0) > 0.1]
    machine_values = [
        pr * pr for s in flowing if 0.0 < (pr := s.get("pr", 0.0)) < _MACHINE_RESISTANCE_MAX
    ]
    source: ResistanceSource
    if len(machine_values) >= _MIN_MACHINE_RESISTANCE_SAMPLES:
        source = "machine"
        resistance_values = machine_values
    else:
        source = "computed"
        resistance_values = [s.get("cp", 0.0) / (s["pf"] * s["pf"]) for s in flowing]

    return ResistanceDiagnostics(
        source=source,
        avg=_round2(_safe_mean(resistance_values)),
        slope=_round2(_linear_slope(resistance_values, dt)),
    )


def _flow_commanded_and_measured(samples: list[SampleDict]) -> bool:
    """Whether the samples hold a flow that was commanded and one that was measured.

    A GaggiMate Standard board has no pressure sensor and no dimmed pump: the
    controller reports zero for the pump and puck flow unless the board has the
    pressure capability, the display sets the target flow only when it does, and
    every Standard revision in the firmware's board table is built without it.
    The log records every field, so such a shot carries `fl = 0` and `tf = 0` on
    every sample, and comparing the two would report a perfect adherence to a
    profile nobody measured.

    A no-pressure shot that does carry a real target and a real flow (a board
    whose firmware says otherwise) is graded, but only by the summary shape,
    which asks this. The full block, the one that is stored and shown, never
    grades flow without pressure. That is fine: the summary shape is reached
    only through `transform_shot("summary")`, which nothing stored or shown
    uses. A negative pump flow still counts as a measurement, hence `!= 0` and
    not `> 0`.
    """
    return any(s.get("tf", 0.0) > 0 for s in samples) and any(
        s.get("fl", 0.0) != 0 for s in samples
    )


#: Fewest samples a phase set needs before its adherence is a number.
_MIN_ADHERENCE_SAMPLES = 3


class _Steering(NamedTuple):
    """What the profile says each sample of one shot was steered by.

    ``per_sample`` is aligned with the shot's samples: ``"pressure"`` or
    ``"flow"`` when the sample belongs to a phase that steers by that quantity
    and its logged target is a real one, ``None`` when there is nothing to grade
    against (a simple power phase, or the post-brew tail).
    """

    pressure_applicable: bool
    flow_applicable: bool
    per_sample: list[PhaseControl | None]


def _steering(
    samples: list[SampleDict], controls: Sequence[PhaseControl] | None
) -> _Steering | None:
    """Which samples may be graded against which target, or ``None`` when none may.

    The firmware logs both `tp` and `tf` in an advanced pump phase (one is the
    target, the other a soft limit) and 0/0 in a simple phase, when inactive and
    in the extended-recording tail that follows the brew (up to three seconds,
    inside the last phase). Only the profile says which is the target, so the
    profile's phase list is the authority, read by each sample's own phase
    number: a phase shorter than one sample is missing from the transition
    table, but never from the samples.

    Nothing is graded when there is no profile, or when a sample has no phase
    number or one the profile does not have: that is a profile that is not the
    one the shot ran, and a verdict against it would be a guess. The tail, the
    samples after the last one that carried a target, is never graded: a
    controller that has stopped is not following a profile. Ramps between
    phases are graded, because the target really does interpolate there.
    """
    if controls is None:
        return None
    last_target = max(
        (i for i, s in enumerate(samples) if s.get("tp", 0.0) > 0 or s.get("tf", 0.0) > 0),
        default=-1,
    )
    per_sample: list[PhaseControl | None] = []
    for i, sample in enumerate(samples):
        phase = sample.get("phase")
        if phase is None or phase != int(phase) or not 0 <= int(phase) < len(controls):
            return None
        control = controls[int(phase)]
        previous = samples[i - 1] if i > 0 else None
        graded = (
            i <= last_target and control != "power" and not limit_holds(sample, control, previous)
        )
        per_sample.append(control if graded else None)
    return _Steering("pressure" in controls, "flow" in controls, per_sample)


#: The pump flow `fl` the log carries is the firmware's ``exportPumpFlowRate``:
#: the flow model of the duty the controller chose, through a first-order low
#: pass at ``_filterEstimatorFrequency / 2`` = 0.5 Hz (``PressureController.cpp``
#: line 177, the frequency in ``PressureController.h``), so its time constant is
#: 1 / (2π · 0.5 Hz) ≈ 0.32 s. It therefore *lags* the pump: for the first
#: sample or two after a phase starts, or after the limit's ask takes over, it is
#: still climbing although the limit is already in charge.
_PUMP_FLOW_FILTER_TAU_S = 1.0 / (2.0 * math.pi * 0.5)

#: How far a measurement may sit short of a limit and still be the limit's.
#: Flow: 0.15 ml/s. Pressure: the
#: larger of 0.2 bar and 10 % of the limit: the controller's own dead band is
#: ``_deadbandCoefficient`` = 0.1 times the setpoint (``PressureController.h``,
#: used at ``.cpp`` line 271), and its integral is reset whenever the flow ask
#: wins, so a flow phase held at its pressure limit settles below it (by 0.6 to
#: 0.9 bar under a 6 to 9 bar limit in a simplified simulation of the controller,
#: not a measurement on a machine).
_FLOW_LIMIT_TOLERANCE_ML_S = 0.15
_PRESSURE_LIMIT_TOLERANCE_BAR = 0.2
_PRESSURE_LIMIT_TOLERANCE_FRACTION = 0.1

#: The logs carry two decimals of flow and one of pressure, so a value at the
#: exact edge of a tolerance is held; float subtraction (`8.3 - 0.83`) must not
#: decide it.
_EDGE_EPSILON = 1e-9


def _defiltered_pump_flow(sample: SampleDict, previous: SampleDict | None) -> float | None:
    """The flow the pump model was asked for, undoing the log's low pass.

    The logged ``fl[i] = a · fl[i-1] + (1 - a) · q`` for the model flow ``q``
    held over the interval between the samples, with ``a = exp(-dt / tau)``;
    so ``q = (fl[i] - a · fl[i-1]) / (1 - a)``. ``dt`` is the samples' own
    time difference, not the nominal interval. ``None`` when either sample lacks
    the pump flow or the time.
    """
    if previous is None or "fl" not in sample or "fl" not in previous:
        return None
    if "t" not in sample or "t" not in previous:
        return None
    dt = (sample["t"] - previous["t"]) / 1000.0
    if dt <= 0:
        return None
    a = math.exp(-dt / _PUMP_FLOW_FILTER_TAU_S)
    return (sample["fl"] - a * previous["fl"]) / (1.0 - a)


def limit_holds(
    sample: SampleDict, control: PhaseControl, previous: SampleDict | None = None
) -> bool:
    """Whether the phase's *limit*, not its target, was in charge of this sample.

    The firmware drives the pump by ``min(flowOutput, pressureOutput)`` whenever
    both the pressure and the flow setpoints are above zero, in either mode
    (``PressureController::update``): the flow limit of a pressure phase caps
    the duty the pressure loop asks for, and the pressure limit of a flow phase
    caps the flow loop's. A sample where the limit is the lower ask says how
    the limit was held, not how the target was followed, so it is not graded.
    A limit of 0 (or below: the firmware tests ``> 0``; a profile's ``-1`` is
    resolved to the value at phase entry before it is logged) is no limit.

    The limit's own measured value sits at the limit, from below or past it:

    * pressure phase: ``tf > 0`` and the pump flow reaches ``tf`` less 0.15 ml/s,
      either as logged (``fl``) or de-filtered (see
      :func:`_defiltered_pump_flow`: the logged value lags the pump, so the
      first samples of a climb to the limit read low although the limit already
      holds). A held flow limit is the feed-forward duty for ``tf``, and a
      pressure-held sample has the flow well below it;
    * flow phase: ``tp > 0`` and the pressure ``cp`` reaches ``tp`` less
      ``max(0.2, 0.1 · tp)`` bar (the controller's dead band, see above).

    Both are one-sided: reaching or exceeding the limit is holding it, and a
    value on the edge of the tolerance counts. A sample that does not record the
    measurement is not held (nothing to compare).
    """
    if control == "pressure":
        target, limit = sample.get("tp", 0.0), sample.get("tf", 0.0)
        floor = limit - _FLOW_LIMIT_TOLERANCE_ML_S - _EDGE_EPSILON
        readings = [sample.get("fl"), _defiltered_pump_flow(sample, previous)]
    elif control == "flow":
        target, limit = sample.get("tf", 0.0), sample.get("tp", 0.0)
        tolerance = max(_PRESSURE_LIMIT_TOLERANCE_BAR, _PRESSURE_LIMIT_TOLERANCE_FRACTION * limit)
        floor = limit - tolerance - _EDGE_EPSILON
        readings = [sample.get("cp")]
    else:
        return False
    return limit > 0 and target > 0 and any(r is not None and r >= floor for r in readings)


def _adherence_of(
    pairs: list[tuple[float, float]],
) -> tuple[float, float, float] | None:
    """RMSE, largest overshoot and largest undershoot of ``(measured, target)`` pairs."""
    if len(pairs) < _MIN_ADHERENCE_SAMPLES:
        return None
    measured = [a for a, _ in pairs]
    targets = [t for _, t in pairs]
    deviations = [a - t for a, t in pairs]
    return (
        _round2(_compute_rmse(measured, targets)),
        _round2(max(0.0, max(deviations))),
        _round2(max(0.0, -min(deviations))),
    )


def _pressure_pairs(
    samples: Sequence[SampleDict], per_sample: Sequence[PhaseControl | None]
) -> list[tuple[float, float]]:
    return [
        (s.get("cp", 0.0), s["tp"])
        for s, control in zip(samples, per_sample, strict=True)
        if control == "pressure" and "tp" in s
    ]


def _flow_pairs(
    samples: Sequence[SampleDict], per_sample: Sequence[PhaseControl | None]
) -> list[tuple[float, float]]:
    """Pump flow against the flow target, in the flow-steered samples.

    The pump flow (`fl`), not the puck flow (`pf`): the firmware's flow mode
    turns the target into a pump duty cycle through the pump's flow model, so
    the pump flow is what it steers; the puck flow is water that got through the
    puck and is 0 for as long as the puck is filling. A shot that did not record
    `fl` cannot be graded.
    """
    return [
        (s["fl"], s["tf"])
        for s, control in zip(samples, per_sample, strict=True)
        if control == "flow" and "tf" in s and "fl" in s
    ]


def _grading(applicable: bool, worked_out: bool) -> Grading:
    if not applicable:
        return "not_applicable"
    return "graded" if worked_out else "not_graded"


def _compute_profile_compliance(
    samples: list[SampleDict], steering: _Steering | None
) -> ProfileComplianceMetrics | None:
    """How closely the machine followed the commanded pressure and flow.

    ``None`` when the shot has no usable profile (see :func:`_steering`): there
    is then nothing to say, which is not the same as a clean pass.
    """
    if steering is None:
        return None
    pressure = _adherence_of(_pressure_pairs(samples, steering.per_sample))
    flow = _adherence_of(_flow_pairs(samples, steering.per_sample))

    p_rmse = max_overshoot = max_undershoot = None
    if pressure is not None:
        p_rmse, max_overshoot, max_undershoot = pressure
    f_rmse = max_flow_overshoot = max_flow_undershoot = None
    if flow is not None:
        f_rmse, max_flow_overshoot, max_flow_undershoot = flow

    return ProfileComplianceMetrics(
        pressure_rmse_bar=p_rmse,
        flow_rmse_ml_s=f_rmse,
        max_pressure_overshoot_bar=max_overshoot,
        max_pressure_undershoot_bar=max_undershoot,
        max_flow_overshoot_ml_s=max_flow_overshoot,
        max_flow_undershoot_ml_s=max_flow_undershoot,
        pressure_grading=_grading(steering.pressure_applicable, pressure is not None),
        flow_grading=_grading(steering.flow_applicable, flow is not None),
    )


def compute_summary_diagnostics(
    slog: Slog,
    *,
    has_pressure: bool | None = None,
    phase_controls: Sequence[PhaseControl] | None = None,
) -> SummaryDiagnostics | None:
    """The cheap detail level: key numbers only, over the same brew window.

    Adherence is read over the brew window, in the samples of the phases that
    steer by it (see :func:`_steering`); without `phase_controls` it is not
    worked out.
    """
    samples = as_sample_dicts(slog)
    if len(samples) < _MIN_SHOT_SAMPLES:
        return None

    pressure_ok = slog.has_pressure if has_pressure is None else has_pressure
    dt = slog.sample_interval / 1000.0
    brew_positions = _brew_phase_positions(samples, slog.transitions)
    brew_samples = [samples[i] for i in brew_positions]
    if len(brew_samples) < _MIN_BREW_SAMPLES:
        return None

    brew_weights = [s.get("v", 0.0) for s in brew_samples]
    steering = _steering(samples, phase_controls)
    brew_steering: list[PhaseControl | None] = (
        [steering.per_sample[i] for i in brew_positions]
        if steering is not None
        else [None] * len(brew_positions)
    )

    r_avg: float | None = None
    r_slope: float | None = None
    r_source: ResistanceSource | None = None
    p_rmse: float | None = None
    max_overshoot: float | None = None

    if pressure_ok:
        resistance = _build_resistance(brew_samples, dt)
        r_avg = resistance["avg"]
        r_slope = resistance["slope"]
        r_source = resistance["source"]

        pressure = _adherence_of(_pressure_pairs(brew_samples, brew_steering))
        if pressure is not None:
            p_rmse, max_overshoot = pressure[0], pressure[1]

    f_rmse: float | None = None
    max_flow_overshoot: float | None = None
    # A board without a pressure sensor has no flow to grade (see the predicate);
    # a pressure shot grades whatever its profile steers by flow.
    flow_gradable = pressure_ok or _flow_commanded_and_measured(samples)
    flow = _adherence_of(_flow_pairs(brew_samples, brew_steering)) if flow_gradable else None
    if flow is not None:
        f_rmse, max_flow_overshoot = flow[0], flow[1]

    pressure_grading: Grading = "not_graded"
    flow_grading: Grading = "not_graded"
    if steering is not None:
        if pressure_ok:
            pressure_grading = _grading(steering.pressure_applicable, p_rmse is not None)
        if flow_gradable:
            flow_grading = _grading(steering.flow_applicable, f_rmse is not None)

    return SummaryDiagnostics(
        has_pressure=pressure_ok,
        resistance_avg=r_avg,
        resistance_slope=r_slope,
        resistance_source=r_source,
        pressure_rmse_bar=p_rmse,
        max_overshoot_bar=max_overshoot,
        flow_rmse_ml_s=f_rmse,
        max_flow_overshoot_ml_s=max_flow_overshoot,
        pressure_grading=pressure_grading,
        flow_grading=flow_grading,
        scale_connected=any(w > 0 for w in brew_weights),
    )


# ═══════════════════════════════════════════════════════════════════
# PER-PHASE
# ═══════════════════════════════════════════════════════════════════


def _compute_phase_diagnostics(
    phase_samples: list[SampleDict],
    phase_type: str,
    dt: float,
    *,
    has_pressure: bool = True,
    steering: Sequence[PhaseControl | None] | None = None,
) -> PhaseDiagnostics:
    """What one phase's samples say about the profile and the puck.

    ``steering`` says, for each of the phase's samples, what the profile steered
    it by (a slice of :attr:`_Steering.per_sample`). A phase is graded only on
    its own target: a pressure phase has a pressure adherence and no flow one,
    and a flow phase the reverse. A phase with nothing to grade (a power phase,
    the post-brew tail, no profile) carries neither. A brew phase carries the
    shot's own resistance (:func:`_build_resistance`) over its samples, a
    preinfusion phase its pressure ramp and when its flow settled, and a decline
    phase how fast and how smoothly the pressure fell: numbers, never bands.
    """
    pressures = [s.get("cp", 0.0) for s in phase_samples]
    flows = [s.get("pf", 0.0) for s in phase_samples]

    p_pairs = _pressure_pairs(phase_samples, steering) if steering is not None else []
    f_pairs = _flow_pairs(phase_samples, steering) if steering is not None else []

    result: PhaseDiagnostics = {
        "phase_type": phase_type,
        "avg_flow_ml_s": _round2(_safe_mean(flows)),
    }
    # As for the whole shot (`_adherence_of`): fewer samples than this is not a verdict.
    if len(f_pairs) >= _MIN_ADHERENCE_SAMPLES:
        result["flow_rmse_ml_s"] = _round2(
            _compute_rmse([a for a, _ in f_pairs], [t for _, t in f_pairs])
        )

    if not has_pressure:
        return result

    result["avg_pressure_bar"] = _round2(_safe_mean(pressures))
    if len(p_pairs) >= _MIN_ADHERENCE_SAMPLES:
        result["pressure_rmse_bar"] = _round2(
            _compute_rmse([a for a, _ in p_pairs], [t for _, t in p_pairs])
        )

    if phase_type == "brew":
        resistance = _build_resistance(phase_samples, dt)
        result["resistance_avg"] = resistance["avg"]
        result["resistance_slope"] = resistance["slope"]
        result["resistance_source"] = resistance["source"]
    elif phase_type == "preinfusion":
        result["ramp_rate_bar_s"] = _round2(_linear_slope(pressures, dt))
        # Saturation: the first point where flow has settled, a three-sample
        # window that is both steady and actually flowing. The whole phase when
        # it never settled.
        sat_time = _round2(len(phase_samples) * dt)
        for i in range(2, len(flows)):
            window = flows[max(0, i - 2) : i + 1]
            if len(window) >= 2 and _safe_std(window) < 0.15 and _safe_mean(window) > 0.1:
                sat_time = _round2(i * dt)
                break
        result["saturation_time_s"] = sat_time
    elif phase_type == "decline":
        result["taper_rate_bar_s"] = _round2(_linear_slope(pressures, dt))
        p_derivs = [(pressures[i] - pressures[i - 1]) / dt for i in range(1, len(pressures))]
        result["taper_smoothness"] = _round2(_safe_std(p_derivs)) if p_derivs else 0.0
    return result


def _select_samples(samples: list[SampleDict]) -> list[TransformedSample]:
    """About five evenly-spaced points per phase, each averaged with its
    neighbours — enough to see the curve's shape without carrying every sample.
    """
    n = len(samples)
    indices: list[int] = (
        list(range(n)) if n <= 5 else sorted({round(i * (n - 1) / 4) for i in range(5)})
    )

    result: list[TransformedSample] = []
    for idx in indices:
        if idx >= len(samples):
            continue
        window = samples[max(0, idx - 1) : min(len(samples), idx + 2)]
        wn = len(window)
        result.append(
            TransformedSample(
                time_seconds=_round1(samples[idx].get("t", 0.0) / 1000.0),
                temperature_c=_round1(sum(s.get("ct", 0.0) for s in window) / wn),
                pressure_bar=_round1(sum(s.get("cp", 0.0) for s in window) / wn),
                flow_ml_s=_round1(sum(s.get("pf", 0.0) for s in window) / wn),
                weight_g=_round1(sum(s.get("v", 0.0) for s in window) / wn),
            )
        )
    return result


def build_phases(
    slog: Slog,
    *,
    include_samples: bool = True,
    include_diagnostics: bool = False,
    has_pressure: bool | None = None,
    phase_controls: Sequence[PhaseControl] | None = None,
) -> list[PhaseData]:
    """Per-phase statistics, optionally with samples and diagnostics."""
    samples = as_sample_dicts(slog)
    if not samples:
        return []

    pressure_ok = slog.has_pressure if has_pressure is None else has_pressure
    dt = slog.sample_interval / 1000.0
    transitions = slog.transitions
    phases: list[PhaseData] = []
    steering = _steering(samples, phase_controls)

    if transitions:
        for i, (transition, span) in enumerate(_phase_ranges(len(samples), transitions)):
            phase_samples = samples[span.start : span.stop]
            if not phase_samples:
                continue

            temperatures = [s["ct"] for s in phase_samples if "ct" in s]
            pressures = [s["cp"] for s in phase_samples if "cp" in s]

            start_time = phase_samples[0].get("t", 0.0) / 1000.0
            end_time = phase_samples[-1].get("t", 0.0) / 1000.0
            # A phase lasts until the next one starts, so the last sample's own
            # interval belongs to it.
            duration = max(0.0, end_time - start_time + slog.sample_interval / 1000.0)

            pd: PhaseData = {
                "name": transition.phase_name,
                "phase_number": transition.phase_number,
                "start_time_seconds": _round1(start_time),
                "duration_seconds": _round1(duration),
                "sample_count": len(phase_samples),
                "avg_temperature_c": _round1(_safe_mean(temperatures)),
                "avg_pressure_bar": _round1(_safe_mean(pressures)) if pressure_ok else 0.0,
                "total_flow_ml": calculate_total_volume(phase_samples, slog.sample_interval),
            }

            if include_samples:
                pd["samples"] = _select_samples(phase_samples)

            if include_diagnostics and dt > 0 and len(phase_samples) >= 3:
                phase_type = _classify_phase(
                    transition.phase_name,
                    phase_samples=phase_samples,
                    phase_index=i,
                    total_phases=len(transitions),
                )
                pd["diagnostics"] = _compute_phase_diagnostics(
                    phase_samples,
                    phase_type,
                    dt,
                    has_pressure=pressure_ok,
                    steering=steering.per_sample[span.start : span.stop] if steering else None,
                )

            phases.append(pd)
        return phases

    # No transition table (v4 and earlier): the whole shot is one phase.
    temperatures = [s.get("ct", 0.0) for s in samples]
    pressures = [s.get("cp", 0.0) for s in samples]
    single: PhaseData = {
        "name": "extraction",
        "phase_number": 0,
        "start_time_seconds": 0.0,
        "duration_seconds": _round1(slog.duration_ms / 1000.0),
        "sample_count": len(samples),
        "avg_temperature_c": _round1(_safe_mean(temperatures)),
        "avg_pressure_bar": _round1(_safe_mean(pressures)) if pressure_ok else 0.0,
        "total_flow_ml": calculate_total_volume(samples, slog.sample_interval),
    }
    if include_samples:
        single["samples"] = _select_samples(samples)
    if include_diagnostics and dt > 0 and len(samples) >= 3:
        single["diagnostics"] = _compute_phase_diagnostics(
            samples,
            "brew",
            dt,
            has_pressure=pressure_ok,
            steering=steering.per_sample if steering else None,
        )
    phases.append(single)
    return phases


def transform_shot(
    slog: Slog,
    detail: str = "summary",
    *,
    has_pressure: bool | None = None,
    phase_controls: Sequence[PhaseControl] | None = None,
) -> TransformedShot:
    """Render a shot at one of three detail levels.

    - **summary**: key indicators only, phases without samples. Cheap enough to
      attach to every shot in a list.
    - **per_phase**: full shot diagnostics plus per-phase metrics — this is what
      tells you *which* phase went wrong.
    - **per_phase_detailed**: the above plus ~5 averaged samples per phase, to
      see the shape of the curve.

    An unrecognised `detail` falls back to summary rather than raising: this
    feeds an LLM prompt, and a typo should cost tokens, not the analysis.

    `phase_controls` is what each phase of the shot's profile steers the pump by.
    Without it no adherence is worked out, at any level.
    """
    if detail not in VALID_DETAIL_LEVELS:
        detail = "summary"

    pressure_ok = slog.has_pressure if has_pressure is None else has_pressure
    summary_stats = calculate_summary(slog, has_pressure=pressure_ok)

    diagnostics: ShotDiagnostics | SummaryDiagnostics | None
    if detail == "summary":
        phases = build_phases(
            slog,
            include_samples=False,
            include_diagnostics=False,
            has_pressure=pressure_ok,
            phase_controls=phase_controls,
        )
        diagnostics = compute_summary_diagnostics(
            slog, has_pressure=pressure_ok, phase_controls=phase_controls
        )
    elif detail == "per_phase":
        phases = build_phases(
            slog,
            include_samples=False,
            include_diagnostics=True,
            has_pressure=pressure_ok,
            phase_controls=phase_controls,
        )
        diagnostics = compute_shot_diagnostics(
            slog, has_pressure=pressure_ok, phase_controls=phase_controls
        )
    else:
        phases = build_phases(
            slog,
            include_samples=True,
            include_diagnostics=True,
            has_pressure=pressure_ok,
            phase_controls=phase_controls,
        )
        diagnostics = compute_shot_diagnostics(
            slog, has_pressure=pressure_ok, phase_controls=phase_controls
        )

    return TransformedShot(
        shot_id=slog.shot_id,
        profile_name=slog.profile_name,
        profile_id=slog.profile_id,
        timestamp=slog.timestamp,
        duration_seconds=_round1(slog.duration_ms / 1000.0),
        final_weight_g=slog.volume_g,
        has_pressure=pressure_ok,
        summary=summary_stats,
        phases=phases,
        diagnostics=diagnostics,
        detail_level=detail,
    )
