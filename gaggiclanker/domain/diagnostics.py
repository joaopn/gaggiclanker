"""Deterministic shot diagnostics.

Vendored from gaggimate-mcp (`transformers/shot.py`, MIT, commit 0af88ad) and
adapted to this project's `.slog` model — see `gaggiclanker/domain/VENDORED.md`
for the licence and the list of changes. **Every band threshold and label is
upstream's**, deliberately: they were calibrated against real shots, and the
project's own tests pin them so a "tidy-up" cannot quietly move a boundary.

What changed here, and why:

* Input is a :class:`~gaggiclanker.domain.slog.Slog`, so `t` is real elapsed
  milliseconds (v6+) rather than a sample index times the nominal interval, and
  the per-sample phase comes from the header's transition table.
* Everything pressure-derived is gated on ``has_pressure``. GaggiMate Standard
  boards have no pressure sensor and record a hard zero, which would otherwise
  produce a confident VERY_LOW resistance reading and a LOW channeling risk out
  of nothing at all. A missing sensor must read as *absent*, not as *good*.

A sample's field is absent from the working dict when the firmware never
recorded it (its `fieldsMask` bit was clear). That is why the code reads
``s.get('cp', 0.0)`` for a value but ``'tp' in s`` for a decision: "not
recorded" and "recorded as zero" are different facts.
"""

from __future__ import annotations

import math
from typing import Literal, NotRequired, TypedDict

from gaggiclanker.domain.models import PhaseTransition
from gaggiclanker.domain.slog import Slog

#: A sample flattened to plain numbers. Keys are the `.slog` field names; a key
#: is present only when the firmware recorded that field.
SampleDict = dict[str, float]

DetailLevel = Literal["summary", "per_phase", "per_phase_detailed"]
VALID_DETAIL_LEVELS: tuple[str, ...] = ("summary", "per_phase", "per_phase_detailed")

_SAMPLE_FIELDS = ("t", "tt", "ct", "tp", "cp", "fl", "tf", "pf", "vf", "v", "ev", "pr", "wp")


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
    """Per-phase metrics. Which keys appear depends on `phase_type`."""

    phase_type: str
    avg_pressure_bar: float
    avg_flow_ml_s: float
    pressure_rmse_bar: float
    flow_rmse_ml_s: float
    # preinfusion
    ramp_rate_bar_s: float
    saturation_time_s: float
    # brew
    resistance_avg: float
    resistance_slope: float
    channeling_risk: str
    flow_jitter_ml_s: float
    pressure_jitter_bar: float
    # decline
    taper_rate_bar_s: float
    taper_smoothness: float
    annotations: dict[str, str]


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


class ResistanceDiagnostics(TypedDict):
    """Puck resistance, ``R = P / F²`` (quadratic Darcy model).

    The master diagnostic: it folds grind fineness, dose, puck prep and
    channeling into one number whose *shape over time* is the interesting part.
    """

    avg: float
    std: float
    slope: float
    peak: float
    peak_timing_pct: float
    annotations: dict[str, str]


class ChannelingIndicators(TypedDict):
    """Four independent puck-stability signals, scored together.

    One flag is usually noise; two or more aligned flags are a real signal,
    which is why `annotations.primary_signal` names which ones fired. The
    `*_spread` and shape entries are descriptors for interpreting the
    indicators — they are not scored.
    """

    flow_jitter_ml_s: float
    """Std of first differences of puck flow: instability after removing
    whatever trajectory the profile intended. Blind to designed ramps."""

    flow_vs_target_residual_ml_s: float | None
    """Std of (actual - target) flow; None on pressure-led profiles that
    command no target flow."""

    pressure_max_drop_rate_bar_s: float
    """Worst single-sample dP/dt — an abrupt channel opening, which jitter can
    miss because it is one sample wide."""

    flow_acceleration_late_ml_s2: float
    """Late-window flow slope minus the overall slope. Detrended on purpose: a
    designed flow ramp would otherwise score as runaway every time."""

    flow_spread_ml_s: float
    """Population std of raw flow. A descriptor, not a stability metric — it
    includes intended ramps. Read it with `annotations.flow_shape`."""

    pressure_jitter_bar: float
    """Sample-to-sample pressure instability; the fallback indicator when no
    target flow is commanded."""

    channeling_risk: str
    """LOW | MODERATE | HIGH | VERY_HIGH | INSUFFICIENT_DATA."""

    annotations: dict[str, str]


class TemperatureDiagnostics(TypedDict):
    overshoot_c: float
    undershoot_c: float
    stability_std_c: float
    annotations: dict[str, str]


class ExtractionMetrics(TypedDict):
    pressure_auc_bar_s: float
    pressure_slope_brew_bar_s: float
    flow_slope_brew_ml_s2: float
    flow_avg_brew_ml_s: float
    annotations: dict[str, str]


class WeightDiagnostics(TypedDict):
    rate_avg_g_s: float | None
    rate_std_g_s: float | None
    scale_connected: bool
    annotations: dict[str, str]


class ProfileComplianceMetrics(TypedDict):
    """RMSE of actual against commanded pressure and flow.

    Flow deviation is the better grind signal: the firmware's PID actively
    drives pump power to hold target pressure, so pressure error is masked by
    the controller, while flow is a *consequence* of grind, dose and puck prep
    and cannot be masked. A pressure overshoot above 1 bar is therefore
    remarkable — it means the controller ran out of room.
    """

    pressure_rmse_bar: float
    flow_rmse_ml_s: float | None
    max_pressure_overshoot_bar: float
    max_pressure_undershoot_bar: float
    max_flow_overshoot_ml_s: float | None
    max_flow_undershoot_ml_s: float | None
    annotations: dict[str, str]


class ShotDiagnostics(TypedDict):
    """Full diagnostics. The pressure-derived blocks are None on a machine
    without a pressure sensor — see `has_pressure`."""

    has_pressure: bool
    resistance: ResistanceDiagnostics | None
    channeling: ChannelingIndicators | None
    temperature: TemperatureDiagnostics
    extraction: ExtractionMetrics
    weight: WeightDiagnostics
    profile_compliance: ProfileComplianceMetrics | None


class SummaryDiagnostics(TypedDict):
    """The small set of indicators that carries most of the signal."""

    has_pressure: bool
    resistance_avg: float | None
    resistance_slope: float | None
    channeling_risk: str | None
    temperature_stability_c: float
    pressure_rmse_bar: float | None
    max_overshoot_bar: float | None
    flow_rmse_ml_s: float | None
    max_flow_overshoot_ml_s: float | None
    scale_connected: bool
    annotations: dict[str, str]


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
# ANNOTATION THRESHOLD BANDS
# Upstream's calibration. Do not adjust without a fixture that shows why.
# ═══════════════════════════════════════════════════════════════════

# Pressure volatility uses the coefficient of variation (std/mean) once mean
# pressure clears this floor, so a 0.3 bar swing at 2 bar and a 1.35 bar swing
# at 9 bar both read as ~15 % relative instability. Below it, CV is noise.
_CV_MIN_PRESSURE_BAR: float = 1.0

_PRESSURE_CV_BANDS: list[tuple[float, str]] = [
    (0.02, "VERY_STABLE"),
    (0.05, "STABLE"),
    (0.10, "MODERATE_JITTER"),
    (0.18, "JITTERY"),
    (float("inf"), "VOLATILE"),
]

# Absolute fallback — only when mean pressure < _CV_MIN_PRESSURE_BAR.
_PRESSURE_VOLATILITY_BANDS: list[tuple[float, str]] = [
    (0.15, "VERY_STABLE"),
    (0.35, "STABLE"),
    (0.6, "MODERATE_JITTER"),
    (1.0, "JITTERY"),
    (float("inf"), "VOLATILE"),
]

_FLOW_VOLATILITY_BANDS: list[tuple[float, str]] = [
    (0.10, "VERY_STABLE"),
    (0.25, "STABLE"),
    (0.50, "MODERATE_JITTER"),
    (0.80, "JITTERY"),
    (float("inf"), "VOLATILE"),
]

# Applied to first-difference std, so tighter than raw-std bands: jitter
# isolates sample-to-sample noise and discards the designed trend. Calibrated
# against 26 real shots that measured 0.013-0.020 ml/s across the board.
# JITTERY is where genuine channeling should register.
_FLOW_JITTER_BANDS: list[tuple[float, str]] = [
    (0.025, "VERY_STABLE"),
    (0.050, "STABLE"),
    (0.100, "MODERATE_JITTER"),
    (0.200, "JITTERY"),
    (float("inf"), "VOLATILE"),
]

_PRESSURE_JITTER_BANDS: list[tuple[float, str]] = [
    (0.05, "VERY_STABLE"),
    (0.10, "STABLE"),
    (0.20, "MODERATE_JITTER"),
    (0.40, "JITTERY"),
    (float("inf"), "VOLATILE"),
]

_FLOW_VS_TARGET_BANDS: list[tuple[float, str]] = [
    (0.15, "WITHIN_TOLERANCE"),
    (0.35, "MINOR_DEVIATION"),
    (0.70, "NOTABLE_DEVIATION"),
    (float("inf"), "SEVERE_DEVIATION"),
]

_RESISTANCE_LEVEL_BANDS: list[tuple[float, str]] = [
    (0.5, "VERY_LOW"),
    (1.5, "LOW"),
    (3.0, "MODERATE"),
    (5.0, "HIGH"),
    (float("inf"), "VERY_HIGH"),
]

_RESISTANCE_STABILITY_BANDS: list[tuple[float, str]] = [
    (0.2, "VERY_STABLE"),
    (0.5, "STABLE"),
    (1.0, "MODERATE"),
    (float("inf"), "VOLATILE"),
]

_RESISTANCE_PEAK_TIMING_BANDS: list[tuple[float, str]] = [
    (0.15, "EARLY"),
    (0.35, "GOOD_TIMING"),
    (0.60, "MID_SHOT"),
    (float("inf"), "LATE"),
]

_FLOW_ACCELERATION_BANDS: list[tuple[float, str]] = [
    (0.02, "STABLE"),
    (0.05, "SLIGHT_ACCELERATION"),
    (0.10, "MODERATE_ACCELERATION"),
    (float("inf"), "RAPID_ACCELERATION"),
]

_TEMP_OVERSHOOT_BANDS: list[tuple[float, str]] = [
    (0.5, "MINIMAL"),
    (1.0, "SLIGHT"),
    (2.0, "MODERATE"),
    (float("inf"), "SIGNIFICANT"),
]

_TEMP_STABILITY_BANDS: list[tuple[float, str]] = [
    (0.3, "VERY_STABLE"),
    (0.8, "STABLE"),
    (1.5, "MODERATE"),
    (float("inf"), "UNSTABLE"),
]

# Descending: value >= lower_bound -> label
_RESISTANCE_SLOPE_BANDS: list[tuple[float, str]] = [
    (0.05, "INCREASING"),
    (-0.02, "FLAT"),
    (-0.08, "GRADUAL_DECLINE"),
    (-0.15, "MODERATE_DECLINE"),
    (float("-inf"), "STEEP_DECLINE"),
]

_PRESSURE_DROP_RATE_BANDS: list[tuple[float, str]] = [
    (-1.0, "NORMAL"),
    (-2.5, "MODERATE_DROP"),
    (-5.0, "STEEP_DROP"),
    (float("-inf"), "CLIFF"),
]

_PROFILE_ADHERENCE_BANDS: list[tuple[float, str]] = [
    (0.3, "EXCELLENT"),
    (0.8, "GOOD"),
    (1.5, "FAIR"),
    (float("inf"), "POOR"),
]

_PRESSURE_OVERSHOOT_BANDS: list[tuple[float, str]] = [
    (0.25, "WITHIN_TOLERANCE"),
    (0.5, "MINOR_OVERSHOOT"),
    (1.0, "NOTABLE_OVERSHOOT"),
    (float("inf"), "SEVERE_OVERSHOOT"),
]

_FLOW_DEVIATION_BANDS: list[tuple[float, str]] = [
    (0.3, "WITHIN_TOLERANCE"),
    (0.7, "MINOR_DEVIATION"),
    (1.5, "NOTABLE_DEVIATION"),
    (float("inf"), "SEVERE_DEVIATION"),
]

_TAPER_SMOOTHNESS_BANDS: list[tuple[float, str]] = [
    (0.2, "VERY_SMOOTH"),
    (0.5, "SMOOTH"),
    (1.0, "MODERATE"),
    (float("inf"), "ROUGH"),
]

_RAMP_RATE_BANDS: list[tuple[float, str]] = [
    (0.5, "GENTLE"),
    (1.5, "MODERATE"),
    (3.0, "BRISK"),
    (5.0, "AGGRESSIVE"),
    (float("inf"), "VERY_AGGRESSIVE"),
]

#: Fewer steady-state samples than this and channeling is not assessed at all.
_MIN_STEADY_STATE_SAMPLES: int = 5


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
        for name in _SAMPLE_FIELDS:
            value = getattr(sample, name)
            if value is not None:
                row[name] = float(value)
        if sample.phase is not None:
            row["phase"] = float(sample.phase)
        out.append(row)
    return out


def _annotate_ascending(value: float, bands: list[tuple[float, str]]) -> str:
    """Classify with ascending (upper_bound, label) bands."""
    for upper, label in bands:
        if value < upper:
            return label
    return bands[-1][1]


def _annotate_descending(value: float, bands: list[tuple[float, str]]) -> str:
    """Classify with descending (lower_bound, label) bands."""
    for lower, label in bands:
        if value >= lower:
            return label
    return bands[-1][1]


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


def _jitter_std(values: list[float]) -> float:
    """Population std of first differences — noise around whatever trend exists.

    A flat signal scores zero; so does a smooth linear ramp, because every
    difference is equal. Only oscillation and spikes register, which is what
    makes this insensitive to intentional profile trajectories.
    """
    if len(values) < 3:
        return 0.0
    diffs = [values[i] - values[i - 1] for i in range(1, len(values))]
    return _safe_std(diffs)


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


def _late_flow_runaway(flows: list[float], dt: float) -> float:
    """Excess flow acceleration in the last 40 % of the window (ml/s²).

    `late_slope - overall_slope`, so a clean linear ramp — which is a profile
    doing exactly what it was told — scores 0, and only a late *departure* from
    the designed trajectory scores positive.
    """
    n = len(flows)
    if n < 6:
        return 0.0
    late_start = int(n * 0.6)
    return _linear_slope(flows[late_start:], dt) - _linear_slope(flows, dt)


def _flow_shape_label(flows: list[float], dt: float) -> str:
    """FLAT / RAMPING_UP / RAMPING_DOWN for a flow window.

    Context for reading `flow_spread_ml_s`: high spread on a ramping profile is
    the profile working, not the puck failing.
    """
    if len(flows) < 2:
        return "FLAT"
    slope = _linear_slope(flows, dt)
    if slope > 0.03:
        return "RAMPING_UP"
    if slope < -0.03:
        return "RAMPING_DOWN"
    return "FLAT"


def _residual_std_vs_target(samples: list[SampleDict]) -> float | None:
    """Population std of (actual flow - commanded flow).

    None when too few samples command a target flow, so "tracked target badly"
    stays distinguishable from "no target was set".
    """
    pairs = [(s.get("pf", 0.0), s["tf"]) for s in samples if s.get("tf", 0.0) > 0]
    if len(pairs) < 3:
        return None
    return _safe_std([actual - target for actual, target in pairs])


def _pressure_volatility_label(std: float, mean: float) -> str:
    """Volatility by coefficient of variation, absolute bands at low pressure."""
    if mean >= _CV_MIN_PRESSURE_BAR and mean > 0:
        return _annotate_ascending(std / mean, _PRESSURE_CV_BANDS)
    return _annotate_ascending(std, _PRESSURE_VOLATILITY_BANDS)


def _trim_ramp_up(
    pressures: list[float],
    flows: list[float],
    samples: list[SampleDict],
    threshold_pct: float = 0.90,
) -> tuple[list[float], list[float], list[SampleDict]]:
    """Drop the ramp-up: everything before pressure first reaches 90 % of peak.

    Ramping is the profile's intent, not the puck's behaviour, and leaving it
    in makes every pressure-led shot look unstable.
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


def _strip_flow_edges(
    pressures: list[float],
    flows: list[float],
    samples: list[SampleDict],
    thr: float = 0.1,
) -> tuple[list[float], list[float], list[SampleDict], tuple[int, int]]:
    """Drop leading and trailing samples with flow below `thr` ml/s.

    Leading: pressure ramped but the valve has not opened. Trailing: the
    volumetric cutoff fired and pressure is trapped in the puck — that tail is
    what produced the original false-positive channeling readings.

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


def _phase_slices(
    samples: list[SampleDict], transitions: list[PhaseTransition]
) -> list[tuple[PhaseTransition, list[SampleDict]]]:
    """Pair each transition with the samples recorded while it was active."""
    result: list[tuple[PhaseTransition, list[SampleDict]]] = []
    for i, transition in enumerate(transitions):
        start = transition.sample_index
        end = transitions[i + 1].sample_index if i + 1 < len(transitions) else len(samples)
        result.append((transition, samples[start:end]))
    return result


def _get_brew_phase_samples(
    samples: list[SampleDict], transitions: list[PhaseTransition]
) -> list[SampleDict]:
    """Everything that is not a pre-infusion phase.

    With no transition table (v4 and earlier) there is nothing to go on but the
    trace, so fall back to "after pressure first reaches half its peak".
    """
    if not samples:
        return []

    if transitions:
        brew: list[SampleDict] = []
        for transition, window in _phase_slices(samples, transitions):
            if _classify_phase_by_name(transition.phase_name) == "preinfusion":
                continue
            brew.extend(window)
        return brew if brew else samples

    pressures = [s.get("cp", 0.0) for s in samples]
    peak = max(pressures) if pressures else 0.0
    if peak > 0:
        threshold = peak * 0.5
        for i, p in enumerate(pressures):
            if p >= threshold:
                return samples[i:]
    return samples


# ═══════════════════════════════════════════════════════════════════
# CHANNELING
# ═══════════════════════════════════════════════════════════════════


def _assess_channeling_risk(
    flow_jitter: float,
    flow_vs_tgt: float | None,
    pressure_max_drop_rate: float,
    flow_acceleration_late: float,
    pressure_jitter: float,
) -> str:
    """Score four indicators 0-2 each and band the total.

    1. flow jitter — the primary fingerprint, blind to designed ramps.
    2. flow-vs-target, or pressure jitter on pressure-led profiles. The two
       share one scoring slot so the 0-8 range means the same thing whatever
       variable the profile commands.
    3. steepest pressure collapse — a cliff one sample wide that jitter misses.
    4. late flow runaway — channeling that develops at the end.

    0-1 LOW, 2-3 MODERATE, 4-5 HIGH, 6-8 VERY_HIGH.
    """
    score = 0

    if flow_jitter >= 0.05:
        score += 1
    if flow_jitter >= 0.10:
        score += 1

    if flow_vs_tgt is not None:
        if flow_vs_tgt >= 0.35:
            score += 1
        if flow_vs_tgt >= 0.70:
            score += 1
    else:
        if pressure_jitter >= 0.10:
            score += 1
        if pressure_jitter >= 0.20:
            score += 1

    if pressure_max_drop_rate <= -1.5:
        score += 1
    if pressure_max_drop_rate <= -3.0:
        score += 1

    if flow_acceleration_late >= 0.05:
        score += 1
    if flow_acceleration_late >= 0.10:
        score += 1

    if score <= 1:
        return "LOW"
    if score <= 3:
        return "MODERATE"
    if score <= 5:
        return "HIGH"
    return "VERY_HIGH"


def _window_confidence(n: int) -> str:
    """How much a channeling verdict from `n` steady-state samples is worth."""
    if n < _MIN_STEADY_STATE_SAMPLES:
        return "INSUFFICIENT"
    if n < 8:
        return "LOW"
    if n < 15:
        return "MEDIUM"
    return "HIGH"


def _channeling_primary_signal(
    flow_jitter: float,
    flow_vs_tgt: float | None,
    pressure_max_drop_rate: float,
    flow_acceleration_late: float,
    pressure_jitter: float,
) -> str:
    """Comma-separated names of the indicators that fired, or "none"."""
    signals: list[str] = []
    if flow_jitter >= 0.05:
        signals.append("flow_jitter")
    if flow_vs_tgt is not None and flow_vs_tgt >= 0.35:
        signals.append("flow_vs_target")
    elif flow_vs_tgt is None and pressure_jitter >= 0.10:
        signals.append("pressure_jitter_fallback")
    if pressure_max_drop_rate <= -1.5:
        signals.append("pressure_cliff")
    if flow_acceleration_late >= 0.05:
        signals.append("late_flow_runaway")
    return ",".join(signals) if signals else "none"


def _channeling_guidance(risk: str, primary: str, confidence: str, flow_shape: str) -> str:
    """One sentence framing the numbers, so a reader knows what to expect."""
    if risk == "INSUFFICIENT_DATA":
        return (
            "Steady-state window too short for a reliable channeling "
            "assessment — treat other diagnostics as the primary signal."
        )
    if risk == "LOW":
        if primary != "none":
            return (
                f"LOW overall; sub-threshold signals noted ({primary}) "
                "but did not aggregate into concern."
            )
        if flow_shape == "FLAT":
            return "Flat flow held steadily — no channeling signature."
        return (
            f"Flow traces a {flow_shape.lower().replace('_', ' ')} "
            "trajectory cleanly — no channeling signature."
        )
    if confidence in ("LOW", "MEDIUM") and risk in ("HIGH", "VERY_HIGH"):
        return (
            f"{risk} rating from a small steady-state window "
            f"(confidence={confidence}); verify against flow_shape and primary_signal."
        )
    signal_count = 0 if primary == "none" else primary.count(",") + 1
    if signal_count >= 2:
        return f"{signal_count} independent indicators align ({primary}) — channeling likely real."
    return f"Single-indicator flag ({primary}); verify against other diagnostics."


def _build_channeling(
    brew_pressures: list[float],
    brew_flows: list[float],
    brew_samples: list[SampleDict],
    dt: float,
) -> ChannelingIndicators:
    """The whole channeling block, from a brew window.

    Shared by the full-shot, summary and per-phase paths so all three trim the
    window the same way and cannot disagree about the same shot.
    """
    ss_pressures, ss_flows, ss_samples = _trim_ramp_up(brew_pressures, brew_flows, brew_samples)
    ramp_excluded = len(brew_pressures) - len(ss_pressures)

    ss_pressures, ss_flows, ss_samples, (zf_lead, zf_tail) = _strip_flow_edges(
        ss_pressures, ss_flows, ss_samples
    )

    n = len(ss_pressures)
    confidence = _window_confidence(n)

    if n < _MIN_STEADY_STATE_SAMPLES:
        shape = _flow_shape_label(ss_flows, dt)
        return ChannelingIndicators(
            flow_jitter_ml_s=0.0,
            flow_vs_target_residual_ml_s=None,
            pressure_max_drop_rate_bar_s=0.0,
            flow_acceleration_late_ml_s2=0.0,
            flow_spread_ml_s=_round2(_safe_std(ss_flows)) if ss_flows else 0.0,
            pressure_jitter_bar=0.0,
            channeling_risk="INSUFFICIENT_DATA",
            annotations={
                "flow_jitter": "N/A",
                "flow_vs_target": "N/A",
                "pressure_drop": "N/A",
                "late_flow_trend": "N/A",
                "pressure_jitter": "N/A",
                "flow_shape": shape,
                "window_confidence": confidence,
                "primary_signal": "none",
                "guidance": _channeling_guidance("INSUFFICIENT_DATA", "none", confidence, shape),
                "note": (
                    f"Only {n} steady-state samples after trim "
                    f"(ramp_excluded={ramp_excluded}, "
                    f"zero_flow_lead={zf_lead}, zero_flow_tail={zf_tail}); "
                    f"need {_MIN_STEADY_STATE_SAMPLES} for assessment."
                ),
            },
        )

    # Score on the raw values and round only for output: rounding first would
    # move a value across a band boundary.
    flow_jitter_raw = _jitter_std(ss_flows)
    pressure_jitter_raw = _jitter_std(ss_pressures)
    flow_vs_tgt_raw = _residual_std_vs_target(ss_samples)
    p_derivatives = [
        (ss_pressures[i] - ss_pressures[i - 1]) / dt for i in range(1, len(ss_pressures))
    ]
    p_max_drop_raw = min(p_derivatives) if p_derivatives else 0.0
    f_accel_late_raw = _late_flow_runaway(ss_flows, dt)

    flow_jitter = _round2(flow_jitter_raw)
    pressure_jitter = _round2(pressure_jitter_raw)
    flow_vs_tgt = _round2(flow_vs_tgt_raw) if flow_vs_tgt_raw is not None else None
    p_max_drop = _round2(p_max_drop_raw)
    f_accel_late = _round2(f_accel_late_raw)

    flow_spread = _round2(_safe_std(ss_flows))
    flow_shape = _flow_shape_label(ss_flows, dt)

    risk = _assess_channeling_risk(
        flow_jitter=flow_jitter_raw,
        flow_vs_tgt=flow_vs_tgt_raw,
        pressure_max_drop_rate=p_max_drop_raw,
        flow_acceleration_late=f_accel_late_raw,
        pressure_jitter=pressure_jitter_raw,
    )
    primary = _channeling_primary_signal(
        flow_jitter_raw,
        flow_vs_tgt_raw,
        p_max_drop_raw,
        f_accel_late_raw,
        pressure_jitter_raw,
    )

    annotations: dict[str, str] = {
        "flow_jitter": _annotate_ascending(flow_jitter, _FLOW_JITTER_BANDS),
        "flow_vs_target": (
            _annotate_ascending(flow_vs_tgt, _FLOW_VS_TARGET_BANDS)
            if flow_vs_tgt is not None
            else "N/A"
        ),
        "pressure_drop": _annotate_descending(p_max_drop, _PRESSURE_DROP_RATE_BANDS),
        "late_flow_trend": _annotate_ascending(f_accel_late, _FLOW_ACCELERATION_BANDS),
        "pressure_jitter": _annotate_ascending(pressure_jitter, _PRESSURE_JITTER_BANDS),
        "flow_shape": flow_shape,
        "window_confidence": confidence,
        "primary_signal": primary,
        "guidance": _channeling_guidance(risk, primary, confidence, flow_shape),
    }
    if ramp_excluded or zf_lead or zf_tail:
        parts = []
        if ramp_excluded:
            parts.append(f"{ramp_excluded} ramp-up")
        if zf_lead:
            parts.append(f"{zf_lead} leading zero-flow")
        if zf_tail:
            parts.append(f"{zf_tail} trailing zero-flow")
        annotations["note"] = f"Trimmed {', '.join(parts)} samples before assessment."

    return ChannelingIndicators(
        flow_jitter_ml_s=flow_jitter,
        flow_vs_target_residual_ml_s=flow_vs_tgt,
        pressure_max_drop_rate_bar_s=p_max_drop,
        flow_acceleration_late_ml_s2=f_accel_late,
        flow_spread_ml_s=flow_spread,
        pressure_jitter_bar=pressure_jitter,
        channeling_risk=risk,
        annotations=annotations,
    )


# ═══════════════════════════════════════════════════════════════════
# SUMMARY STATISTICS
# ═══════════════════════════════════════════════════════════════════


def calculate_total_volume(samples: list[SampleDict], interval_ms: int) -> float:
    """Integrate puck flow over the shot, in ml."""
    interval_seconds = interval_ms / 1000.0
    return _round1(sum(s.get("pf", 0.0) for s in samples) * interval_seconds)


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
    peak_pressure_index = pressures.index(peak_pressure) if pressures and peak_pressure > 0 else 0
    peak_time = times[peak_pressure_index] if peak_pressure_index < len(times) else 0.0

    pressure_summary: PressureSummary | None = None
    if pressure_ok:
        pressure_summary = PressureSummary(
            min_bar=_round1(min(pressures)) if pressures else 0.0,
            max_bar=_round1(max(pressures)) if pressures else 0.0,
            avg_bar=_round1(_safe_mean(pressures)),
            peak_time_s=_round1(peak_time),
        )

    time_to_first_drip: float | None = None
    for i, flow in enumerate(flows):
        if flow > 0.0:
            time_to_first_drip = _round1(times[i]) if i < len(times) else None
            break

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
    slog: Slog, *, has_pressure: bool | None = None
) -> ShotDiagnostics | None:
    """Full diagnostics, or None when there is too little to say anything.

    Too little means fewer than 5 samples overall or fewer than 3 in the brew
    phase. `has_pressure` defaults to whether the trace carries any non-zero
    pressure at all, which is how a Standard board shows up.
    """
    samples = as_sample_dicts(slog)
    if len(samples) < 5:
        return None

    pressure_ok = slog.has_pressure if has_pressure is None else has_pressure
    dt = slog.sample_interval / 1000.0

    brew_samples = _get_brew_phase_samples(samples, slog.transitions)
    if len(brew_samples) < 3:
        return None

    brew_pressures = [s.get("cp", 0.0) for s in brew_samples]
    brew_flows = [s.get("pf", 0.0) for s in brew_samples]
    brew_temps = [s.get("ct", 0.0) for s in brew_samples]
    brew_target_temps = [s.get("tt", 0.0) for s in brew_samples]
    all_pressures = [s.get("cp", 0.0) for s in samples]
    brew_weights = [s.get("v", 0.0) for s in brew_samples]
    has_scale = any(w > 0 for w in brew_weights)

    resistance: ResistanceDiagnostics | None = None
    channeling: ChannelingIndicators | None = None
    profile_compliance: ProfileComplianceMetrics | None = None

    if pressure_ok:
        resistance = _build_resistance(brew_pressures, brew_flows, dt)
        channeling = _build_channeling(brew_pressures, brew_flows, brew_samples, dt)
        profile_compliance = _compute_profile_compliance(samples)

    temp_deviations = [
        ct - tt for ct, tt in zip(brew_temps, brew_target_temps, strict=True) if tt > 0
    ]
    t_overshoot = max(temp_deviations) if temp_deviations else 0.0
    t_undershoot = abs(min(temp_deviations)) if temp_deviations else 0.0
    t_std = _round2(_safe_std(brew_temps))

    temperature = TemperatureDiagnostics(
        overshoot_c=_round2(max(0.0, t_overshoot)),
        undershoot_c=_round2(max(0.0, t_undershoot)),
        stability_std_c=t_std,
        annotations={
            "overshoot": _annotate_ascending(max(0.0, t_overshoot), _TEMP_OVERSHOOT_BANDS),
            "undershoot": _annotate_ascending(max(0.0, t_undershoot), _TEMP_OVERSHOOT_BANDS),
            "stability": _annotate_ascending(t_std, _TEMP_STABILITY_BANDS),
        },
    )

    p_auc = _round2(sum(p * dt for p in all_pressures))
    p_slope = _round2(_linear_slope(brew_pressures, dt))
    f_slope = _round2(_linear_slope(brew_flows, dt))
    f_avg = _round2(_safe_mean(brew_flows))

    extraction_annotations: dict[str, str] = {
        "flow_trend": (
            "DECLINING" if f_slope < -0.02 else "STABLE" if f_slope < 0.02 else "INCREASING"
        ),
    }
    if pressure_ok:
        extraction_annotations["pressure_trend"] = _annotate_descending(
            p_slope, _RESISTANCE_SLOPE_BANDS
        )
    else:
        extraction_annotations["note"] = _NO_PRESSURE_NOTE

    extraction = ExtractionMetrics(
        pressure_auc_bar_s=p_auc if pressure_ok else 0.0,
        pressure_slope_brew_bar_s=p_slope if pressure_ok else 0.0,
        flow_slope_brew_ml_s2=f_slope,
        flow_avg_brew_ml_s=f_avg,
        annotations=extraction_annotations,
    )

    w_rate_avg: float | None = None
    w_rate_std: float | None = None
    weight_annotations: dict[str, str] = {}
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
            w_rate_std = _round2(_safe_std(weight_rates))
            weight_annotations["rate_stability"] = _annotate_ascending(
                w_rate_std, _FLOW_VOLATILITY_BANDS
            )
    else:
        weight_annotations["note"] = "No scale data available"

    weight = WeightDiagnostics(
        rate_avg_g_s=w_rate_avg,
        rate_std_g_s=w_rate_std,
        scale_connected=has_scale,
        annotations=weight_annotations,
    )

    return ShotDiagnostics(
        has_pressure=pressure_ok,
        resistance=resistance,
        channeling=channeling,
        temperature=temperature,
        extraction=extraction,
        weight=weight,
        profile_compliance=profile_compliance,
    )


#: Said once, in the one place a reader will look for it.
_NO_PRESSURE_NOTE = (
    "No pressure sensor on this machine (GaggiMate Standard board): "
    "pressure-derived diagnostics are omitted rather than reported as zero."
)


def _build_resistance(
    brew_pressures: list[float], brew_flows: list[float], dt: float
) -> ResistanceDiagnostics:
    """Puck resistance R = P / F² and its shape over the brew phase.

    Samples below 0.1 ml/s are skipped: dividing by a near-zero flow produces
    an arbitrarily large number that says nothing about the puck.
    """
    resistance_values = [
        p / (f * f) for p, f in zip(brew_pressures, brew_flows, strict=True) if f > 0.1
    ]

    r_avg = _round2(_safe_mean(resistance_values))
    r_std = _round2(_safe_std(resistance_values))
    r_slope = _round2(_linear_slope(resistance_values, dt))
    if resistance_values:
        r_peak_val = max(resistance_values)
        r_peak = _round2(r_peak_val)
        r_peak_idx = resistance_values.index(r_peak_val)
        r_peak_timing = _round2(r_peak_idx / len(resistance_values))
    else:
        r_peak = 0.0
        r_peak_timing = 0.0

    return ResistanceDiagnostics(
        avg=r_avg,
        std=r_std,
        slope=r_slope,
        peak=r_peak,
        peak_timing_pct=r_peak_timing,
        annotations={
            "level": _annotate_ascending(r_avg, _RESISTANCE_LEVEL_BANDS),
            "stability": _annotate_ascending(r_std, _RESISTANCE_STABILITY_BANDS),
            "erosion": _annotate_descending(r_slope, _RESISTANCE_SLOPE_BANDS),
            "saturation": _annotate_ascending(r_peak_timing, _RESISTANCE_PEAK_TIMING_BANDS),
        },
    )


def _compute_profile_compliance(samples: list[SampleDict]) -> ProfileComplianceMetrics | None:
    """How closely the machine followed the commanded pressure and flow."""
    p_pairs = [(s.get("cp", 0.0), s["tp"]) for s in samples if "tp" in s]
    if len(p_pairs) < 3:
        return None

    p_rmse = _round2(_compute_rmse([a for a, _ in p_pairs], [t for _, t in p_pairs]))
    deviations = [a - t for a, t in p_pairs]
    max_overshoot = _round2(max(0.0, max(deviations)))
    max_undershoot = _round2(max(0.0, abs(min(deviations))))

    f_pairs = [(s.get("pf", 0.0), s["tf"]) for s in samples if "tf" in s]
    f_rmse: float | None = None
    max_flow_overshoot: float | None = None
    max_flow_undershoot: float | None = None
    if len(f_pairs) >= 3:
        f_rmse = _round2(_compute_rmse([a for a, _ in f_pairs], [t for _, t in f_pairs]))
        f_deviations = [a - t for a, t in f_pairs]
        max_flow_overshoot = _round2(max(0.0, max(f_deviations)))
        max_flow_undershoot = _round2(max(0.0, abs(min(f_deviations))))

    annotations: dict[str, str] = {
        "pressure_adherence": _annotate_ascending(p_rmse, _PROFILE_ADHERENCE_BANDS),
        "pressure_overshoot": _annotate_ascending(max_overshoot, _PRESSURE_OVERSHOOT_BANDS),
    }
    if f_rmse is not None:
        annotations["flow_adherence"] = _annotate_ascending(f_rmse, _PROFILE_ADHERENCE_BANDS)
    if max_flow_overshoot is not None:
        annotations["flow_overshoot"] = _annotate_ascending(
            max_flow_overshoot, _FLOW_DEVIATION_BANDS
        )
    if max_flow_undershoot is not None:
        annotations["flow_undershoot"] = _annotate_ascending(
            max_flow_undershoot, _FLOW_DEVIATION_BANDS
        )

    return ProfileComplianceMetrics(
        pressure_rmse_bar=p_rmse,
        flow_rmse_ml_s=f_rmse,
        max_pressure_overshoot_bar=max_overshoot,
        max_pressure_undershoot_bar=max_undershoot,
        max_flow_overshoot_ml_s=max_flow_overshoot,
        max_flow_undershoot_ml_s=max_flow_undershoot,
        annotations=annotations,
    )


def compute_summary_diagnostics(
    slog: Slog, *, has_pressure: bool | None = None
) -> SummaryDiagnostics | None:
    """The cheap detail level: key indicators only, same trims and bands."""
    samples = as_sample_dicts(slog)
    if len(samples) < 5:
        return None

    pressure_ok = slog.has_pressure if has_pressure is None else has_pressure
    dt = slog.sample_interval / 1000.0
    brew_samples = _get_brew_phase_samples(samples, slog.transitions)
    if len(brew_samples) < 3:
        return None

    brew_pressures = [s.get("cp", 0.0) for s in brew_samples]
    brew_flows = [s.get("pf", 0.0) for s in brew_samples]
    brew_temps = [s.get("ct", 0.0) for s in brew_samples]
    brew_weights = [s.get("v", 0.0) for s in brew_samples]

    r_avg: float | None = None
    r_slope: float | None = None
    risk: str | None = None
    p_rmse: float | None = None
    max_overshoot: float | None = None

    annotations: dict[str, str] = {}

    if pressure_ok:
        r_values = [p / (f * f) for p, f in zip(brew_pressures, brew_flows, strict=True) if f > 0.1]
        r_avg = _round2(_safe_mean(r_values))
        r_slope = _round2(_linear_slope(r_values, dt))
        risk = _build_channeling(brew_pressures, brew_flows, brew_samples, dt)["channeling_risk"]

        p_rmse = 0.0
        max_overshoot = 0.0
        p_targets = [(s.get("cp", 0.0), s["tp"]) for s in brew_samples if "tp" in s]
        if p_targets:
            p_rmse = _round2(_compute_rmse([a for a, _ in p_targets], [t for _, t in p_targets]))
            max_overshoot = _round2(max(0.0, max(a - t for a, t in p_targets)))

        annotations["resistance_level"] = _annotate_ascending(r_avg, _RESISTANCE_LEVEL_BANDS)
        annotations["resistance_erosion"] = _annotate_descending(r_slope, _RESISTANCE_SLOPE_BANDS)
        annotations["channeling_risk"] = risk
        annotations["pressure_adherence"] = _annotate_ascending(p_rmse, _PROFILE_ADHERENCE_BANDS)
        annotations["pressure_overshoot"] = _annotate_ascending(
            max_overshoot, _PRESSURE_OVERSHOOT_BANDS
        )
    else:
        annotations["note"] = _NO_PRESSURE_NOTE

    t_std = _round2(_safe_std(brew_temps))
    annotations["temperature_stability"] = _annotate_ascending(t_std, _TEMP_STABILITY_BANDS)

    f_rmse: float | None = None
    max_flow_overshoot: float | None = None
    f_targets = [(s.get("pf", 0.0), s["tf"]) for s in brew_samples if "tf" in s]
    if len(f_targets) >= 3:
        f_rmse = _round2(_compute_rmse([a for a, _ in f_targets], [t for _, t in f_targets]))
        max_flow_overshoot = _round2(max(0.0, max(a - t for a, t in f_targets)))
        annotations["flow_adherence"] = _annotate_ascending(f_rmse, _PROFILE_ADHERENCE_BANDS)
        annotations["flow_overshoot"] = _annotate_ascending(
            max_flow_overshoot, _FLOW_DEVIATION_BANDS
        )

    return SummaryDiagnostics(
        has_pressure=pressure_ok,
        resistance_avg=r_avg,
        resistance_slope=r_slope,
        channeling_risk=risk,
        temperature_stability_c=t_std,
        pressure_rmse_bar=p_rmse,
        max_overshoot_bar=max_overshoot,
        flow_rmse_ml_s=f_rmse,
        max_flow_overshoot_ml_s=max_flow_overshoot,
        scale_connected=any(w > 0 for w in brew_weights),
        annotations=annotations,
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
) -> PhaseDiagnostics:
    """Metrics for one phase, chosen by what that kind of phase is for."""
    pressures = [s.get("cp", 0.0) for s in phase_samples]
    flows = [s.get("pf", 0.0) for s in phase_samples]

    avg_p = _round2(_safe_mean(pressures))
    avg_f = _round2(_safe_mean(flows))

    p_pairs = [(s.get("cp", 0.0), s["tp"]) for s in phase_samples if "tp" in s]
    p_rmse = (
        _round2(_compute_rmse([a for a, _ in p_pairs], [t for _, t in p_pairs])) if p_pairs else 0.0
    )

    f_pairs = [(s.get("pf", 0.0), s["tf"]) for s in phase_samples if "tf" in s]
    f_rmse = (
        _round2(_compute_rmse([a for a, _ in f_pairs], [t for _, t in f_pairs])) if f_pairs else 0.0
    )

    annotations: dict[str, str] = {}
    result: PhaseDiagnostics = {
        "phase_type": phase_type,
        "avg_flow_ml_s": avg_f,
        "flow_rmse_ml_s": f_rmse,
        "annotations": annotations,
    }

    if not has_pressure:
        annotations["note"] = _NO_PRESSURE_NOTE
        return result

    result["avg_pressure_bar"] = avg_p
    result["pressure_rmse_bar"] = p_rmse
    annotations["pressure_adherence"] = _annotate_ascending(p_rmse, _PROFILE_ADHERENCE_BANDS)

    if phase_type == "preinfusion":
        ramp_rate = _round2(_linear_slope(pressures, dt))
        # Saturation: the first point where flow has settled — a three-sample
        # window that is both steady and actually flowing. Default to the whole
        # phase, meaning it never settled.
        sat_time = _round2(len(phase_samples) * dt)
        for i in range(2, len(flows)):
            window = flows[max(0, i - 2) : i + 1]
            if len(window) >= 2 and _safe_std(window) < 0.15 and _safe_mean(window) > 0.1:
                sat_time = _round2(i * dt)
                break
        result["ramp_rate_bar_s"] = ramp_rate
        result["saturation_time_s"] = sat_time
        annotations["ramp_rate"] = _annotate_ascending(abs(ramp_rate), _RAMP_RATE_BANDS)

    elif phase_type == "brew":
        r_values = [p / (f * f) for p, f in zip(pressures, flows, strict=True) if f > 0.1]
        r_avg = _round2(_safe_mean(r_values))
        r_slope = _round2(_linear_slope(r_values, dt))

        ch = _build_channeling(pressures, flows, phase_samples, dt)

        result["resistance_avg"] = r_avg
        result["resistance_slope"] = r_slope
        result["channeling_risk"] = ch["channeling_risk"]
        result["flow_jitter_ml_s"] = ch["flow_jitter_ml_s"]
        result["pressure_jitter_bar"] = ch["pressure_jitter_bar"]

        annotations["resistance_level"] = _annotate_ascending(r_avg, _RESISTANCE_LEVEL_BANDS)
        annotations["resistance_erosion"] = _annotate_descending(r_slope, _RESISTANCE_SLOPE_BANDS)
        annotations["channeling"] = ch["channeling_risk"]
        # Namespaced so the rich channeling annotations cannot collide with the
        # phase's own keys.
        for key in (
            "flow_jitter",
            "flow_vs_target",
            "pressure_drop",
            "late_flow_trend",
            "pressure_jitter",
            "flow_shape",
            "window_confidence",
            "primary_signal",
            "guidance",
        ):
            annotations[f"channeling_{key}"] = ch["annotations"][key]
        if "note" in ch["annotations"]:
            annotations["channeling_note"] = ch["annotations"]["note"]

    elif phase_type == "decline":
        taper_rate = _round2(_linear_slope(pressures, dt))
        p_derivs = [(pressures[i] - pressures[i - 1]) / dt for i in range(1, len(pressures))]
        taper_smooth = _round2(_safe_std(p_derivs)) if p_derivs else 0.0
        result["taper_rate_bar_s"] = taper_rate
        result["taper_smoothness"] = taper_smooth
        annotations["taper_smoothness"] = _annotate_ascending(taper_smooth, _TAPER_SMOOTHNESS_BANDS)

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
) -> list[PhaseData]:
    """Per-phase statistics, optionally with samples and diagnostics."""
    samples = as_sample_dicts(slog)
    if not samples:
        return []

    pressure_ok = slog.has_pressure if has_pressure is None else has_pressure
    dt = slog.sample_interval / 1000.0
    transitions = slog.transitions
    phases: list[PhaseData] = []

    if transitions:
        slices = _phase_slices(samples, transitions)
        for i, (transition, phase_samples) in enumerate(slices):
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
                    phase_samples, phase_type, dt, has_pressure=pressure_ok
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
            samples, "brew", dt, has_pressure=pressure_ok
        )
    phases.append(single)
    return phases


def transform_shot(
    slog: Slog,
    detail: str = "summary",
    *,
    has_pressure: bool | None = None,
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
    """
    if detail not in VALID_DETAIL_LEVELS:
        detail = "summary"

    pressure_ok = slog.has_pressure if has_pressure is None else has_pressure
    summary_stats = calculate_summary(slog, has_pressure=pressure_ok)

    diagnostics: ShotDiagnostics | SummaryDiagnostics | None
    if detail == "summary":
        phases = build_phases(
            slog, include_samples=False, include_diagnostics=False, has_pressure=pressure_ok
        )
        diagnostics = compute_summary_diagnostics(slog, has_pressure=pressure_ok)
    elif detail == "per_phase":
        phases = build_phases(
            slog, include_samples=False, include_diagnostics=True, has_pressure=pressure_ok
        )
        diagnostics = compute_shot_diagnostics(slog, has_pressure=pressure_ok)
    else:
        phases = build_phases(
            slog, include_samples=True, include_diagnostics=True, has_pressure=pressure_ok
        )
        diagnostics = compute_shot_diagnostics(slog, has_pressure=pressure_ok)

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
