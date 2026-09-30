"""Adherence is graded on the target each phase steers by, read from its profile.

The firmware logs a pressure and a flow target on every advanced phase but only
one is the target; the profile says which. These tests pin that reading: what
is graded (and against what), what never is, and how "not applicable" differs
from "not graded".
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.domain.diagnostics import (
    _PUMP_FLOW_FILTER_TAU_S,
    ProfileComplianceMetrics,
    _steering,
    compute_shot_diagnostics,
    compute_summary_diagnostics,
    limit_holds,
    transform_shot,
)
from gaggiclanker.domain.phase_control import PhaseControl, phase_controls
from gaggiclanker.domain.scoring import execution_score
from gaggiclanker.domain.slog import Slog, parse_slog
from tests.domain.helpers import (
    SLOG_FIXTURES,
    constructed_controls,
    constructed_profile_for,
    make_slog,
)

SLOGS = sorted(SLOG_FIXTURES.glob("*.slog"))


def _compliance(slog: Slog, controls: tuple[PhaseControl, ...] | None) -> ProfileComplianceMetrics:
    diagnostics = compute_shot_diagnostics(slog, phase_controls=controls)
    assert diagnostics is not None
    compliance = diagnostics["profile_compliance"]
    assert compliance is not None
    return compliance


# ── reading the profile ──────────────────────────────────────────────


def test_a_phase_is_steered_by_the_target_of_its_pump() -> None:
    profile = {
        "phases": [
            {"pump": 0},
            {"pump": 100},
            {"pump": {"target": "pressure", "pressure": 9, "flow": 2}},
            {"pump": {"target": "flow", "pressure": 6, "flow": 2}},
        ]
    }
    assert phase_controls(profile) == ("power", "power", "pressure", "flow")


@pytest.mark.parametrize(
    "profile",
    [
        None,
        {},
        {"phases": "nope"},
        {"phases": [{}]},
        {"phases": [{"pump": None}]},
        {"phases": [{"pump": True}]},
        {"phases": [{"pump": 1.5}]},
        {"phases": [{"pump": {"target": "volumetric"}}]},
        {"phases": [{"pump": 0}, {"pump": {"pressure": 9}}]},
        {"phases": ["not a phase"]},
    ],
)
def test_a_profile_that_cannot_be_read_says_nothing(profile: Any) -> None:
    assert phase_controls(profile) is None


# ── the real fixture shots ───────────────────────────────────────────

#: Per shot and variant of the constructed profile: the pressure and flow
#: adherence (`None`: not applicable). Measured on the shots as they were, and
#: worked out here by hand only in the sense that the flow ones are tiny: the
#: pump did follow its flow target where one was steering. The pressure-first
#: figures leave out the samples the flow limit held (26, 28 and 16 of them in
#: the first phase); graded with them they were 2.32, 2.25 and 3.2.
EXPECTED = {
    ("shot_196", "pressure-first"): (2.2, None),
    ("shot_196", "flow-first"): (2.21, 0.08),
    ("shot_204", "pressure-first"): (2.04, None),
    ("shot_204", "flow-first"): (2.03, 0.03),
    ("shot_222", "pressure-first"): (3.1, None),
    ("shot_222", "flow-first"): (3.11, 0.1),
}


@pytest.mark.parametrize(("shot", "variant"), sorted(EXPECTED))
def test_a_real_shot_is_graded_on_what_its_phases_steered_by(shot: str, variant: str) -> None:
    (path,) = [p for p in SLOGS if p.stem.startswith(shot)]
    pressure, flow = EXPECTED[(shot, variant)]
    compliance = _compliance(parse_slog(path.read_bytes()), constructed_controls(shot, variant))

    assert compliance["pressure_rmse_bar"] == pressure
    assert compliance["pressure_grading"] == "graded"
    assert compliance["flow_rmse_ml_s"] == flow
    if flow is None:
        assert compliance["flow_grading"] == "not_applicable"
        assert not {"flow_adherence", "flow_overshoot", "flow_undershoot"} & set(
            compliance["annotations"]
        )
    else:
        assert compliance["flow_grading"] == "graded"
        assert compliance["annotations"]["flow_adherence"] == "EXCELLENT"


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
def test_a_pressure_profile_no_longer_costs_a_flow_penalty(path: Path) -> None:
    slog = parse_slog(path.read_bytes())
    controls = phase_controls(constructed_profile_for(path))
    score = execution_score(transform_shot(slog, "per_phase", phase_controls=controls))
    assert "flow_adherence" not in score.components
    assert score.confidence == "high"


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
def test_the_post_brew_tail_is_never_the_worst_overshoot(path: Path) -> None:
    """After the last target the logged targets are 0 and the pressure is still falling.

    Read as a pressure target of 0 that is an "overshoot" of the whole brew
    pressure, which is what the largest pressure overshoot used to report.
    """
    slog = parse_slog(path.read_bytes())
    compliance = _compliance(slog, constructed_controls(path.stem[:8]))
    assert compliance["max_pressure_overshoot_bar"] is not None
    assert compliance["max_pressure_overshoot_bar"] < 1.0


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
def test_no_profile_means_no_adherence_at_any_level(path: Path) -> None:
    slog = parse_slog(path.read_bytes())
    for detail in ("summary", "per_phase", "per_phase_detailed"):
        transformed = transform_shot(slog, detail)
        diagnostics: Any = transformed["diagnostics"]
        if detail == "summary":
            assert diagnostics["pressure_rmse_bar"] is None
            assert diagnostics["flow_rmse_ml_s"] is None
            assert diagnostics["pressure_grading"] == "not_graded"
            assert diagnostics["flow_grading"] == "not_graded"
            assert not [k for k in diagnostics["annotations"] if k.endswith(("adherence", "shoot"))]
        else:
            assert diagnostics["profile_compliance"] is None
            for phase in transformed["phases"]:
                phase_diag = phase.get("diagnostics", {})
                assert "pressure_rmse_bar" not in phase_diag
                assert "flow_rmse_ml_s" not in phase_diag
                assert "pressure_adherence" not in phase_diag.get("annotations", {})


# ── a synthetic shot with one phase of each kind ─────────────────────

#: phase 0: flow (target 2 ml/s, pressure limit 3), phase 1: a simple power
#: phase (logs 0/0), phase 2: pressure (9 bar, flow limit 2). Then the tail:
#: the last three samples log 0/0 inside phase 2 while the pressure falls.
PHASES = [(0, 0, "Fill"), (4, 1, "Bloom"), (8, 2, "Extraction")]
CONTROLS: tuple[PhaseControl, ...] = ("flow", "power", "pressure")


def _shot(names: tuple[str, str, str] = ("Fill", "Bloom", "Extraction")) -> Slog:
    samples: list[dict[str, Any]] = []
    for i in range(4):  # flow phase: the pump tracks 2 ml/s, the puck flow is still 0
        samples.append(
            {
                "t": i * 250,
                "cp": 1.0,
                "tp": 3.0,
                "pf": 0.0,
                "fl": 2.0,
                "tf": 2.0,
                "ct": 93,
                "tt": 93,
            }
        )
    for i in range(4, 8):  # power phase: 0/0, pressure and flow are whatever they are
        samples.append(
            {
                "t": i * 250,
                "cp": 0.5,
                "tp": 0.0,
                "pf": 0.0,
                "fl": 0.4,
                "tf": 0.0,
                "ct": 93,
                "tt": 93,
            }
        )
    for i in range(8, 14):  # pressure phase: 1 bar under target, flow limit 2 not reached
        samples.append(
            {
                "t": i * 250,
                "cp": 8.0,
                "tp": 9.0,
                "pf": 1.5,
                "fl": 1.5,
                "tf": 2.0,
                "ct": 93,
                "tt": 93,
            }
        )
    for i in range(14, 17):  # the tail: targets are 0, the pressure is still falling
        samples.append(
            {
                "t": i * 250,
                "cp": 6.0,
                "tp": 0.0,
                "pf": 0.5,
                "fl": 0.5,
                "tf": 0.0,
                "ct": 93,
                "tt": 93,
            }
        )
    return make_slog(samples, [(i, n, name) for (i, n, _), name in zip(PHASES, names, strict=True)])


def test_each_quantity_is_graded_over_the_samples_of_its_own_phases() -> None:
    compliance = _compliance(_shot(), CONTROLS)
    # Pressure: the six pressure-phase samples, all 1 bar under 9. The simple
    # phase and the tail (cp 0.5 and 6.0 against 0) are not in it.
    assert compliance["pressure_rmse_bar"] == 1.0
    assert compliance["max_pressure_overshoot_bar"] == 0.0
    assert compliance["max_pressure_undershoot_bar"] == 1.0
    # Flow: the four flow-phase samples, pump flow exactly on target. The puck
    # flow there is 0 against 2, which is the reading the fix removes.
    assert compliance["flow_rmse_ml_s"] == 0.0
    assert compliance["flow_grading"] == "graded"
    assert compliance["annotations"]["flow_adherence"] == "EXCELLENT"


def test_a_flow_target_is_compared_with_the_pump_flow_not_the_puck_flow() -> None:
    compliance = _compliance(_shot(), CONTROLS)
    assert compliance["flow_rmse_ml_s"] == 0.0  # `fl` tracks `tf`, `pf` is 0 throughout
    off = _shot()
    for sample in off.samples[:4]:
        sample.fl = 1.0  # the pump falls a ml/s short of its target
    assert _compliance(off, CONTROLS)["flow_rmse_ml_s"] == 1.0


def test_a_simple_phase_is_never_graded() -> None:
    compliance = _compliance(_shot(), ("flow", "power", "power"))
    assert compliance["pressure_grading"] == "not_applicable"
    assert compliance["pressure_rmse_bar"] is None
    only_power = _compliance(_shot(), ("power", "power", "power"))
    assert only_power["pressure_grading"] == only_power["flow_grading"] == "not_applicable"


def test_the_tail_after_the_last_target_is_never_graded() -> None:
    slog = _shot()
    # Were the three tail samples in, the RMSE would be sqrt((6*1 + 3*36)/9).
    assert _compliance(slog, CONTROLS)["pressure_rmse_bar"] == 1.0
    # A target on the last sample makes it not a tail: it is graded again.
    slog.samples[-1].tp = 9.0
    regraded = _compliance(slog, CONTROLS)["pressure_rmse_bar"]
    assert regraded is not None and regraded > 1.0


def test_a_ramp_between_phases_is_graded_on_its_interpolating_target() -> None:
    samples = [
        {"t": i * 250, "cp": 2.0 * i, "tp": 2.0 * i + 1.0, "fl": 0.0, "tf": 0.0, "pf": 0.0}
        for i in range(6)
    ]
    slog = make_slog(samples, [(0, 0, "Ramp")])
    compliance = _compliance(slog, ("pressure",))
    # The target runs 1 bar ahead of the pressure on every sample of the ramp,
    # except the first, where the target 1.0 is above the pressure 0 by the same 1.
    assert compliance["pressure_rmse_bar"] == 1.0


def test_a_phase_short_enough_to_be_skipped_by_the_transitions_is_still_read_by_its_number() -> (
    None
):
    """Sample phase numbers are authoritative: phase 1 is missing from the transition table."""
    slog = _shot()
    slog.header.transitions[:] = [t for t in slog.header.transitions if t.phase_number != 1]
    assert _compliance(slog, CONTROLS)["pressure_rmse_bar"] == 1.0


def test_a_phase_number_the_profile_does_not_have_leaves_the_shot_ungraded() -> None:
    slog = _shot()
    assert compute_shot_diagnostics(slog, phase_controls=("flow", "power")) is not None
    diagnostics = compute_shot_diagnostics(slog, phase_controls=("flow", "power"))
    assert diagnostics is not None
    assert diagnostics["profile_compliance"] is None
    summary = compute_summary_diagnostics(slog, phase_controls=("flow", "power"))
    assert summary is not None
    assert summary["pressure_grading"] == summary["flow_grading"] == "not_graded"


def test_a_sample_with_no_phase_number_leaves_the_shot_ungraded() -> None:
    slog = _shot()
    slog.samples[3].phase = None
    diagnostics = compute_shot_diagnostics(slog, phase_controls=CONTROLS)
    assert diagnostics is not None
    assert diagnostics["profile_compliance"] is None


def test_a_shot_that_never_recorded_the_pump_flow_cannot_be_graded_on_flow() -> None:
    slog = _shot()
    for sample in slog.samples:
        sample.fl = None
    compliance = _compliance(slog, CONTROLS)
    assert compliance["flow_rmse_ml_s"] is None
    assert compliance["flow_grading"] == "not_graded"
    assert (
        execution_score(transform_shot(slog, "per_phase", phase_controls=CONTROLS)).confidence
        == "medium"
    )


def test_too_few_samples_is_not_graded_where_a_target_applies() -> None:
    samples = [{"t": i * 250, "cp": 8.0, "tp": 9.0, "pf": 0, "fl": 0, "tf": 0} for i in range(2)]
    samples += [{"t": i * 250, "cp": 1.0, "tp": 3.0, "fl": 2.0, "tf": 2.0} for i in range(2, 8)]
    slog = make_slog(samples, [(0, 0, "Pressure"), (2, 1, "Flow")])
    compliance = _compliance(slog, ("pressure", "flow"))
    assert compliance["pressure_grading"] == "not_graded"
    assert compliance["flow_grading"] == "graded"


# ── per phase ────────────────────────────────────────────────────────


def test_each_phase_is_graded_only_on_its_own_target() -> None:
    transformed = transform_shot(_shot(), "per_phase", phase_controls=CONTROLS)
    by_name = {p["name"]: p.get("diagnostics", {}) for p in transformed["phases"]}
    fill, bloom, extraction = by_name["Fill"], by_name["Bloom"], by_name["Extraction"]

    assert fill["flow_rmse_ml_s"] == 0.0
    assert "pressure_rmse_bar" not in fill
    assert "pressure_adherence" not in fill["annotations"]

    assert "flow_rmse_ml_s" not in bloom
    assert "pressure_rmse_bar" not in bloom  # a simple phase: nothing to follow

    # The pressure phase: 6 graded samples 1 bar under target; the tail is not in it.
    assert extraction["pressure_rmse_bar"] == 1.0
    assert "flow_rmse_ml_s" not in extraction
    assert extraction["annotations"]["pressure_adherence"] == "FAIR"


def test_the_summary_grades_the_brew_window_on_the_same_rule() -> None:
    # Named so that no phase is a preinfusion by name: the whole shot is the brew window.
    slog = _shot(("Hold", "Rest", "Extraction"))
    summary = compute_summary_diagnostics(slog, phase_controls=CONTROLS)
    assert summary is not None
    assert summary["pressure_rmse_bar"] == 1.0
    assert summary["flow_rmse_ml_s"] == 0.0
    assert summary["pressure_grading"] == summary["flow_grading"] == "graded"

    # A flow phase that is a preinfusion by name is outside the window the summary reads.
    early = compute_summary_diagnostics(_shot(), phase_controls=CONTROLS)
    assert early is not None
    assert early["flow_rmse_ml_s"] is None
    assert early["flow_grading"] == "not_graded"


# ── samples the phase's limit held ───────────────────────────────────


def _limit_samples(control: PhaseControl, measured: float, limit: float, count: int = 6) -> Slog:
    """One phase of ``count`` samples whose target is 1 off its measurement.

    Pressure phase: target 9 bar, pressure 8, flow limit ``limit`` ml/s, pump
    flow ``measured``. Flow phase: target 2 ml/s, pump flow 1, pressure limit
    ``limit`` bar, pressure ``measured``.
    """
    if control == "pressure":
        row = {"cp": 8.0, "tp": 9.0, "fl": measured, "tf": limit, "pf": 0.0}
    else:
        row = {"cp": measured, "tp": limit, "fl": 1.0, "tf": 2.0, "pf": 0.0}
    return make_slog([{"t": i * 250, **row} for i in range(count)], [(0, 0, "Phase")])


def _rmse(slog: Slog, control: PhaseControl) -> float | None:
    c = _compliance(slog, (control,))
    return c["pressure_rmse_bar" if control == "pressure" else "flow_rmse_ml_s"]


@pytest.mark.parametrize(
    ("measured", "held"),
    [
        (2.0, True),  # on the limit
        (2.5, True),  # past it (the limit ramping down under the filter)
        (1.85, True),  # 0.15 short: the edge of the tolerance, held
        (1.84, False),  # 0.16 short: the pump is not at the limit
        (0.5, False),
    ],
)
def test_a_pressure_phase_sample_at_its_flow_limit_is_not_graded(
    measured: float, held: bool
) -> None:
    """The definition: limit > 0, target > 0 and pump flow >= limit - 0.15 ml/s."""
    slog = _limit_samples("pressure", measured, limit=2.0)
    if held:
        assert _compliance(slog, ("pressure",))["pressure_grading"] == "not_graded"
        assert _rmse(slog, "pressure") is None
    else:
        assert _rmse(slog, "pressure") == 1.0


@pytest.mark.parametrize(
    ("measured", "held"),
    [
        (6.0, True),  # on the limit
        (6.5, True),  # past it
        (5.4, True),  # 0.6 short, 10 % of the limit: the edge, held
        (5.39, False),
        (3.0, False),
    ],
)
def test_a_flow_phase_sample_at_its_pressure_limit_is_not_graded(
    measured: float, held: bool
) -> None:
    """The definition: limit > 0, target > 0 and pressure >= limit - max(0.2, 10 % of limit) bar."""
    slog = _limit_samples("flow", measured, limit=6.0)
    if held:
        assert _compliance(slog, ("flow",))["flow_grading"] == "not_graded"
        assert _rmse(slog, "flow") is None
    else:
        assert _rmse(slog, "flow") == 1.0


@pytest.mark.parametrize("limit", [0.0, -1.0])
def test_a_limit_of_zero_or_below_is_no_limit(limit: float) -> None:
    """The firmware acts on a limit only when it is above zero; `-1` is "hold the value
    at phase entry" in a profile and is resolved to a number before it is logged, so
    a logged value at or below zero is none: the samples are graded, however the
    measurement compares with it."""
    assert _rmse(_limit_samples("pressure", 2.0, limit), "pressure") == 1.0
    assert _rmse(_limit_samples("pressure", -2.0, limit), "pressure") == 1.0
    assert _rmse(_limit_samples("flow", 6.0, limit), "flow") == 1.0


def test_a_limit_with_no_target_is_not_one() -> None:
    """The firmware arbitrates only when both setpoints are above zero: with the target at 0 a
    flow at its "limit" is the only loop running and holds nothing back, so the sample is graded."""
    slog = make_slog(
        [{"t": i * 250, "cp": 8.0, "tp": 0.0, "fl": 2.0, "tf": 2.0, "pf": 0.0} for i in range(6)],
        [(0, 0, "Phase")],
    )
    assert _rmse(slog, "pressure") == 8.0


def test_a_pressure_limit_is_held_to_the_controllers_dead_band() -> None:
    """A flow phase held at its pressure limit settles below it: tf 2, limit 6, pressure 5.4."""
    assert _rmse(_limit_samples("flow", 5.4, 6.0), "flow") is None
    # Below 2 bar the 10 % is under the 0.2 bar floor: the floor decides.
    assert limit_holds({"tf": 2.0, "tp": 1.0, "cp": 0.8}, "flow")
    assert not limit_holds({"tf": 2.0, "tp": 1.0, "cp": 0.79}, "flow")


@pytest.mark.parametrize(
    ("control", "sample"),
    [
        # Exactly on the edge of the tolerance on the log's grid; in floats
        # 0.2 - 0.15 and 8.3 - 0.83 land a hair above 0.05 and 7.47.
        ("pressure", {"tp": 9.0, "tf": 0.20, "fl": 0.05}),
        ("flow", {"tf": 2.0, "tp": 8.3, "cp": 7.47}),
    ],
)
def test_the_edge_of_a_tolerance_is_decided_on_the_log_grid_not_by_float_rounding(
    control: PhaseControl, sample: dict[str, float]
) -> None:
    assert limit_holds(sample, control)


@pytest.mark.parametrize(
    ("control", "sample"),
    [
        ("pressure", {"tp": 9.0, "tf": 2.0, "cp": 8.0}),  # no pump flow recorded
        ("flow", {"tf": 2.0, "tp": 6.0, "fl": 2.0}),  # no pressure recorded
    ],
)
def test_a_sample_that_did_not_record_the_measurement_is_not_held(
    control: PhaseControl, sample: dict[str, float]
) -> None:
    assert not limit_holds(sample, control)


def test_a_climb_to_the_flow_limit_is_held_from_its_first_sample() -> None:
    """The logged pump flow lags the pump through a 0.5 Hz filter (tau ~ 0.32 s).

    Built by the filter itself: the pump is at the 2 ml/s limit from the first
    sample, and the logged value climbs to it. Read as logged, the first two
    samples are 0.91 and 0.55 short of the limit and graded as misses; undone,
    every sample is the limit's.
    """
    a = math.exp(-0.25 / _PUMP_FLOW_FILTER_TAU_S)
    logged, value = [], 0.0
    for _ in range(6):
        value = a * value + (1 - a) * 2.0
        logged.append(round(value, 2))
    assert logged[0] < 1.85 and logged[1] < 1.85  # the lag this is about
    samples = [
        {"t": i * 250, "cp": 0.3, "tp": 3.0, "fl": fl, "tf": 2.0, "pf": 0.0}
        for i, fl in enumerate(logged)
    ]
    held = [
        limit_holds(s, "pressure", samples[i - 1] if i else None) for i, s in enumerate(samples)
    ]
    assert held[1:] == [True] * 5  # the first has no sample before it to undo the filter with
    assert not held[0]


def test_the_filter_is_undone_over_the_samples_own_time_step_not_the_nominal_one() -> None:
    """Two samples 100 ms apart: the same logged flows, read as 250 ms apart, would not be held."""
    a = math.exp(-0.1 / _PUMP_FLOW_FILTER_TAU_S)
    before = {"t": 0, "cp": 0.3, "tp": 3.0, "fl": 0.0, "tf": 2.0}
    after = {"t": 100, "cp": 0.3, "tp": 3.0, "fl": round((1 - a) * 2.0, 2), "tf": 2.0}
    assert limit_holds(after, "pressure", before)
    assert not limit_holds({**after, "t": 350}, "pressure", {**before, "t": 100})


def test_a_phase_needs_three_graded_samples_for_an_adherence_of_its_own() -> None:
    """Per phase as for the whole shot: one or two samples are not a verdict.

    A phase is only diagnosed from three samples; here five, of which the flow
    limit held all but ``graded``.
    """

    def shot(graded: int) -> Slog:
        free = {"cp": 6.0, "tp": 9.0, "fl": 1.0, "tf": 0.0, "pf": 0.0}
        held = {"cp": 6.0, "tp": 9.0, "fl": 2.0, "tf": 2.0, "pf": 0.0}
        rows = [{"t": i * 250, **free} for i in range(6)]
        rows += [{"t": (6 + i) * 250, **(free if i < graded else held)} for i in range(5)]
        return make_slog(rows, [(0, 0, "Hold"), (6, 1, "Extraction")])

    for graded, verdict in [(2, False), (3, True)]:
        phases = transform_shot(shot(graded), "per_phase", phase_controls=("pressure", "pressure"))[
            "phases"
        ]
        assert phases[0]["diagnostics"]["pressure_rmse_bar"] == 3.0
        assert ("pressure_rmse_bar" in phases[1]["diagnostics"]) is verdict


def test_a_flow_phase_needs_three_graded_samples_for_an_adherence_of_its_own() -> None:
    free = {"cp": 1.0, "tp": 6.0, "fl": 1.0, "tf": 2.0, "pf": 0.0}
    held = {**free, "cp": 6.0}  # the pressure limit reached

    def shot(graded: int) -> Slog:
        rows = [{"t": i * 250, **free} for i in range(6)]
        rows += [{"t": (6 + i) * 250, **(free if i < graded else held)} for i in range(5)]
        return make_slog(rows, [(0, 0, "Hold"), (6, 1, "Extraction")])

    for graded, verdict in [(2, False), (3, True)]:
        phases = transform_shot(shot(graded), "per_phase", phase_controls=("flow", "flow"))[
            "phases"
        ]
        assert phases[0]["diagnostics"]["flow_rmse_ml_s"] == 1.0
        assert ("flow_rmse_ml_s" in phases[1]["diagnostics"]) is verdict


def test_the_steering_undoes_the_filter_with_each_samples_predecessor() -> None:
    """Through `_steering`, not only the predicate: the first sample has no
    predecessor and is graded; the ones after it, still under the limit as
    logged, are the limit's."""
    a = math.exp(-0.25 / _PUMP_FLOW_FILTER_TAU_S)
    value, samples = 0.0, []
    for i in range(6):
        value = a * value + (1 - a) * 2.0
        samples.append(
            {"t": i * 250.0, "phase": 0.0, "cp": 0.3, "tp": 3.0, "fl": round(value, 2), "tf": 2.0}
        )
    steering = _steering(samples, ("pressure",))
    assert steering is not None
    assert steering.per_sample == ["pressure"] + [None] * 5


def test_the_filter_constant_is_the_firmwares() -> None:
    """tau = 1 / (2 pi 0.5 Hz): 0.1 s after a step to 2 ml/s the log reads 0.54 (2.0 undone);
    a wrong constant would call a real 0.3 a limit, or 0.54 a miss."""
    before = {"t": 0, "cp": 0.3, "tp": 3.0, "fl": 0.0, "tf": 2.0}
    assert limit_holds({"t": 100, "cp": 0.3, "tp": 3.0, "fl": 0.54, "tf": 2.0}, "pressure", before)
    assert not limit_holds(
        {"t": 100, "cp": 0.3, "tp": 3.0, "fl": 0.3, "tf": 2.0}, "pressure", before
    )
    # 0.47 at 0.1 s undoes to 1.74, short of 1.85; tau / (tau + dt) in place of
    # exp(-dt / tau) would make it 1.97.
    assert not limit_holds(
        {"t": 100, "cp": 0.3, "tp": 3.0, "fl": 0.47, "tf": 2.0}, "pressure", before
    )
