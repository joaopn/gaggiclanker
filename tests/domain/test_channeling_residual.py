"""The channeling block's flow-versus-target residual is read only where flow steers.

The logged flow target `tf` is a target in a flow-steered phase and a limit (or
0) in a pressure-steered one. Pairing the puck flow with it everywhere made the
residual, and the channeling score it feeds, depend on a number the machine was
not steering by. Samples a phase's limit was holding are not a target either
(`limit_holds`). With nothing flow-steered the residual is absent, never 0.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from gaggiclanker.domain.diagnostics import (
    compute_shot_diagnostics,
    compute_summary_diagnostics,
    transform_shot,
)
from gaggiclanker.domain.phase_control import PhaseControl
from gaggiclanker.domain.slog import Slog
from tests.domain.helpers import make_slog, slog_from_export

#: A slow ramp: puck flow far from a constant limit, yet no jitter and no cliff.
RAMP = [0.2 + 0.2 * i for i in range(12)]


def _std(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _sample(i: int, pf: float, *, tp: float, tf: float, cp: float = 6.0, phase: int = 0) -> Any:
    return {
        "t": i * 250,
        "ct": 93.0,
        "tt": 93.0,
        "cp": cp,
        "pf": pf,
        "fl": pf,
        "tp": tp,
        "tf": tf,
        "phase": phase,
    }


def _channeling(slog: Slog, controls: tuple[PhaseControl, ...] | None) -> Any:
    diagnostics = compute_shot_diagnostics(slog, phase_controls=controls)
    assert diagnostics is not None
    channeling = diagnostics["channeling"]
    assert channeling is not None
    return channeling


def _single_phase(samples: list[dict[str, Any]]) -> Slog:
    return make_slog(samples, [(0, 0, "Brew")])


def test_a_pressure_phase_has_no_flow_residual_against_its_flow_limit() -> None:
    slog = _single_phase([_sample(i, pf, tp=6.0, tf=3.0) for i, pf in enumerate(RAMP)])
    channeling = _channeling(slog, ("pressure",))
    assert channeling["flow_vs_target_residual_ml_s"] is None
    assert channeling["annotations"]["flow_vs_target"] == "N/A"
    assert "flow_vs_target" not in channeling["annotations"]["primary_signal"]


def test_a_flow_phase_reads_its_residual_against_the_flow_target() -> None:
    # tp 0 beside tf: no pressure limit, so nothing is held. The target steps
    # up and down so the residual is not zero.
    targets = [1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 1.0, 1.0, 1.0, 1.0]
    slog = _single_phase(
        [
            _sample(i, pf, tp=0.0, tf=tf)
            for i, (pf, tf) in enumerate(zip(RAMP, targets, strict=True))
        ]
    )
    channeling = _channeling(slog, ("flow",))
    expected = round(_std([pf - tf for pf, tf in zip(RAMP, targets, strict=True)]), 2)
    assert channeling["flow_vs_target_residual_ml_s"] == expected
    assert expected > 0.3
    assert channeling["annotations"]["flow_vs_target"] != "N/A"


def test_without_a_profile_the_residual_is_absent_not_zero() -> None:
    slog = _single_phase([_sample(i, pf, tp=0.0, tf=2.0) for i, pf in enumerate(RAMP)])
    channeling = _channeling(slog, None)
    assert channeling["flow_vs_target_residual_ml_s"] is None
    assert channeling["annotations"]["flow_vs_target"] == "N/A"


def test_a_power_phase_has_no_residual() -> None:
    slog = _single_phase([_sample(i, pf, tp=0.0, tf=0.0) for i, pf in enumerate(RAMP)])
    assert _channeling(slog, ("power",))["flow_vs_target_residual_ml_s"] is None


def test_only_the_flow_steered_samples_of_a_mixed_shot_count() -> None:
    # Four pressure-phase samples with a wild flow against their limit, then a
    # flow phase whose puck flow follows its target to within 0.05 ml/s.
    samples = [_sample(i, 0.2 + 0.4 * i, tp=6.0, tf=6.0, phase=0) for i in range(4)]
    flow_part = [1.0, 1.05, 0.95, 1.0, 1.05, 0.95, 1.0, 1.05]
    samples += [_sample(4 + i, pf, tp=0.0, tf=1.0, phase=1) for i, pf in enumerate(flow_part)]
    slog = make_slog(samples, [(0, 0, "Brew"), (4, 1, "Hold")])
    got = _channeling(slog, ("pressure", "flow"))["flow_vs_target_residual_ml_s"]
    # The window's ramp-up trim may drop some of the early samples, never the flow ones.
    assert got is not None
    assert got == round(_std([pf - 1.0 for pf in flow_part]), 2)


def test_samples_held_at_the_phases_pressure_limit_are_left_out() -> None:
    # A flow phase with a 6 bar limit. Half the samples sit at the limit (cp 6:
    # the limit is in charge) and their flow is far off the target; the others
    # follow the target to within 0.05. Only the latter may be read.
    followed = [1.0, 1.05, 0.95, 1.0, 1.05, 0.95]
    held = [2.5, 0.2, 2.8, 0.1, 2.6, 0.3]
    samples = []
    for i in range(12):
        is_held = i % 2 == 1
        pf = held[i // 2] if is_held else followed[i // 2]
        samples.append(_sample(i, pf, tp=6.0, tf=1.0, cp=6.0 if is_held else 4.0))
    slog = _single_phase(samples)
    got = _channeling(slog, ("flow",))["flow_vs_target_residual_ml_s"]
    assert got == round(_std([pf - 1.0 for pf in followed]), 2)
    assert got is not None and got < 0.1


def test_a_flow_phase_held_throughout_by_its_limit_has_no_residual() -> None:
    slog = _single_phase([_sample(i, pf, tp=6.0, tf=1.0, cp=6.0) for i, pf in enumerate(RAMP)])
    assert _channeling(slog, ("flow",))["flow_vs_target_residual_ml_s"] is None


def test_an_absent_residual_does_not_move_the_risk_by_itself() -> None:
    # The same trace: read as pressure-steered the residual against the limit is
    # not counted, so the risk is that of the other indicators (none fired).
    # Read as flow-steered against the same tf it would have scored two points.
    steep = [0.2 + 0.25 * i for i in range(12)]
    samples = [_sample(i, pf, tp=0.0, tf=3.0) for i, pf in enumerate(steep)]
    slog = _single_phase(samples)
    as_pressure = _channeling(slog, ("pressure",))
    as_flow = _channeling(slog, ("flow",))
    assert as_flow["flow_vs_target_residual_ml_s"] >= 0.7
    assert as_flow["channeling_risk"] == "MODERATE"
    assert as_pressure["flow_vs_target_residual_ml_s"] is None
    assert as_pressure["channeling_risk"] == "LOW"
    assert as_pressure["annotations"]["primary_signal"] == "none"


def test_the_summary_and_the_phase_paths_follow_the_same_rule() -> None:
    samples = [_sample(i, pf, tp=6.0, tf=3.0) for i, pf in enumerate(RAMP)]
    slog = _single_phase(samples)
    summary = compute_summary_diagnostics(slog, phase_controls=("pressure",))
    assert summary is not None
    assert summary["annotations"]["channeling_risk"] == "LOW"

    phases = transform_shot(slog, "per_phase", phase_controls=("pressure",))["phases"]
    annotations = phases[0]["diagnostics"]["annotations"]
    assert annotations["channeling_flow_vs_target"] == "N/A"

    flow_phases = transform_shot(
        _single_phase([_sample(i, pf, tp=0.0, tf=3.0) for i, pf in enumerate(RAMP)]),
        "per_phase",
        phase_controls=("flow",),
    )["phases"]
    assert flow_phases[0]["diagnostics"]["annotations"]["channeling_flow_vs_target"] != "N/A"


def test_real_shot_129_has_no_residual_under_a_pressure_profile() -> None:
    controls: tuple[PhaseControl, ...] = ("pressure",) * 4
    channeling = _channeling(slog_from_export("shot-129.json"), controls)
    assert channeling["flow_vs_target_residual_ml_s"] is None
    assert channeling["annotations"]["flow_vs_target"] == "N/A"


@pytest.mark.parametrize("controls", [None, ("pressure",) * 4])
def test_the_summary_risk_of_shot_129_is_unchanged_by_the_absent_residual(
    controls: tuple[PhaseControl, ...] | None,
) -> None:
    summary = compute_summary_diagnostics(
        slog_from_export("shot-129.json"), phase_controls=controls
    )
    assert summary is not None
    assert summary["annotations"]["channeling_risk"] == "LOW"


STEEP = [0.2 + 0.25 * i for i in range(12)]


def test_the_summary_scores_a_flow_steered_residual_like_the_full_block() -> None:
    slog = _single_phase([_sample(i, pf, tp=0.0, tf=3.0) for i, pf in enumerate(STEEP)])
    full = _channeling(slog, ("flow",))
    summary = compute_summary_diagnostics(slog, phase_controls=("flow",))
    assert summary is not None
    assert full["channeling_risk"] == "MODERATE"
    assert summary["annotations"]["channeling_risk"] == "MODERATE"
    # Without the steering the summary has no residual and says LOW.
    unsteered = compute_summary_diagnostics(slog, phase_controls=None)
    assert unsteered is not None
    assert unsteered["annotations"]["channeling_risk"] == "LOW"


def test_the_steering_follows_the_brew_window_after_a_preinfusion() -> None:
    # A pressure-steered pre-infusion (not part of the brew window) with a wild
    # flow against its limit, then a flow-steered brew. The brew samples sit at
    # positions 4..15: a steering read from the first 12 positions would call
    # the first four of them pressure-steered and lose their residual.
    pre = [_sample(i, 0.5 + 0.9 * (i % 2), tp=3.0, tf=3.0, cp=3.0, phase=0) for i in range(4)]
    brew = [_sample(4 + i, pf, tp=0.0, tf=3.0, phase=1) for i, pf in enumerate(STEEP)]
    slog = make_slog(pre + brew, [(0, 0, "Preinfusion"), (4, 1, "Brew")])
    controls: tuple[PhaseControl, ...] = ("pressure", "flow")
    got = _channeling(slog, controls)
    assert got["flow_vs_target_residual_ml_s"] == round(_std([pf - 3.0 for pf in STEEP]), 2)
    assert got["channeling_risk"] == "MODERATE"
    summary = compute_summary_diagnostics(slog, phase_controls=controls)
    assert summary is not None
    assert summary["annotations"]["channeling_risk"] == "MODERATE"


def test_a_flow_steered_sample_with_no_target_logged_is_not_a_residual() -> None:
    # The first four samples log tf = 0 (a target not yet set) beside a large
    # puck flow; they are not part of the commanded trajectory.
    followed = [1.0, 1.05, 0.95, 1.0, 1.05, 0.95, 1.0, 1.05]
    samples = [_sample(i, 5.0, tp=0.0, tf=0.0) for i in range(4)]
    samples += [_sample(4 + i, pf, tp=0.0, tf=1.0) for i, pf in enumerate(followed)]
    got = _channeling(_single_phase(samples), ("flow",))["flow_vs_target_residual_ml_s"]
    assert got == round(_std([pf - 1.0 for pf in followed]), 2)
