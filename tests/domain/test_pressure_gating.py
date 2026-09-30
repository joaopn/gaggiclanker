"""Standard boards have no pressure sensor — the diagnostics must say so.

This is the one behavioural change made to the vendored engine. Upstream had
no notion of a machine without a pressure sensor, so on a Standard board's hard
zeros it would report resistance VERY_LOW, channeling LOW and pressure
adherence EXCELLENT: three confident readings of a sensor that does not exist.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.domain.diagnostics import (
    compute_shot_diagnostics,
    compute_summary_diagnostics,
    transform_shot,
)
from gaggiclanker.domain.scoring import execution_score
from gaggiclanker.domain.slog import parse_slog
from tests.domain.helpers import make_slog, standard_board


def _samples(*, pressure: bool) -> list[dict[str, Any]]:
    return [
        {
            "t": i * 250,
            "tt": 93.0,
            "ct": 92.8 + (i % 3) * 0.1,
            "tp": 9.0 if pressure else 0.0,
            "cp": (2.0 + i * 0.7 if i < 8 else 9.0) if pressure else 0.0,
            "pf": 0.2 + i * 0.12,
            "fl": 0.2 + i * 0.12,
            "tf": 2.0,
            "v": i * 1.6,
        }
        for i in range(20)
    ]


def _pro() -> Any:
    return make_slog(_samples(pressure=True), [(0, 0, "Preinfusion"), (6, 1, "Extraction")])


def _standard() -> Any:
    return make_slog(_samples(pressure=False), [(0, 0, "Preinfusion"), (6, 1, "Extraction")])


def test_pro_board_gets_the_full_pressure_diagnostics() -> None:
    diagnostics = compute_shot_diagnostics(_pro(), phase_controls=("pressure", "pressure"))
    assert diagnostics is not None
    assert diagnostics["has_pressure"] is True
    assert diagnostics["resistance"] is not None
    assert diagnostics["channeling"] is not None
    assert diagnostics["profile_compliance"] is not None


def test_standard_board_omits_pressure_derived_blocks() -> None:
    diagnostics = compute_shot_diagnostics(_standard())
    assert diagnostics is not None
    assert diagnostics["has_pressure"] is False
    assert diagnostics["resistance"] is None
    assert diagnostics["channeling"] is None
    assert diagnostics["profile_compliance"] is None
    # What the machine *can* measure is still reported in full.
    assert diagnostics["temperature"]["stability_std_c"] >= 0
    assert diagnostics["weight"]["scale_connected"] is True
    assert "note" in diagnostics["extraction"]["annotations"]


def test_standard_board_summary_omits_the_same_things() -> None:
    summary = compute_summary_diagnostics(_standard(), phase_controls=("flow", "flow"))
    assert summary is not None
    assert summary["has_pressure"] is False
    assert summary["resistance_avg"] is None
    assert summary["channeling_risk"] is None
    assert summary["pressure_rmse_bar"] is None
    assert "resistance_level" not in summary["annotations"]
    # This synthetic board records a target flow and a pump flow (which no real
    # Standard board does, see below), so there is a flow to grade.
    assert summary["flow_rmse_ml_s"] is not None


def test_standard_board_phase_diagnostics_drop_pressure_metrics() -> None:
    transformed = transform_shot(_standard(), "per_phase")
    brew = transformed["phases"][1]["diagnostics"]
    assert "resistance_avg" not in brew
    assert "channeling_risk" not in brew
    assert "pressure_rmse_bar" not in brew
    assert brew["avg_flow_ml_s"] > 0


def test_standard_board_summary_stats_omit_the_pressure_block() -> None:
    assert transform_shot(_standard())["summary"]["pressure"] is None
    assert transform_shot(_pro())["summary"]["pressure"] is not None


def test_explicit_has_pressure_overrides_the_inference() -> None:
    """The capability flag from `evt:status` is better evidence than the trace.

    A Pro board that happened to record a flat zero (a failed sensor, a shot
    that never pressurised) should still be treated as having one.
    """
    diagnostics = compute_shot_diagnostics(_pro(), has_pressure=False)
    assert diagnostics is not None
    assert diagnostics["channeling"] is None


def test_score_on_a_standard_board_is_low_confidence() -> None:
    """Scoring a machine we cannot measure must not read as a clean pass."""
    score = execution_score(transform_shot(_standard(), "per_phase"))
    assert score.confidence == "low"
    assert "pressure sensor" in score.reason


def _every_phase(slog: Any, control: Any) -> Any:
    """A profile's phase controls with every phase of the shot steered the same way."""
    return (control,) * (max(s.phase or 0 for s in slog.samples) + 1)


SLOGS = sorted((Path(__file__).resolve().parents[1] / "fixtures" / "slog").glob("*.slog"))
FLOW = ("flow_adherence", "flow_overshoot", "flow_undershoot")


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
def test_a_real_standard_board_shot_has_no_flow_adherence_in_either_shape(path: Path) -> None:
    """A Standard board logs `pf = 0` and `tf = 0` on every sample: no flow to grade."""
    slog = standard_board(parse_slog(path.read_bytes()))
    assert slog.has_pressure is False, "the gate must be inferred from the trace, not forced"

    summary: Any = transform_shot(slog, "summary")["diagnostics"]
    assert summary["has_pressure"] is False
    assert not [k for k in summary["annotations"] if k in FLOW]
    assert summary["flow_rmse_ml_s"] is None
    assert summary["max_flow_overshoot_ml_s"] is None

    full: Any = transform_shot(slog, "per_phase")["diagnostics"]
    assert full["profile_compliance"] is None


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
def test_the_predicate_lets_the_summary_grade_a_no_pressure_shot_that_has_flow(
    path: Path,
) -> None:
    """The predicate is about the samples, not the board: with the gate forced off, a
    recording that carries a target and a measured flow somewhere is not refused.

    These recordings command flow only in preinfusion, so with every phase read as
    flow-steered the brew window the summary grades holds a zero target: this
    tests that the summary grades at all, not the quality of that grade (the
    synthetic board above has a real target in its brew window)."""
    slog = parse_slog(path.read_bytes())
    summary: Any = transform_shot(
        slog, "summary", has_pressure=False, phase_controls=_every_phase(slog, "flow")
    )["diagnostics"]
    assert summary["has_pressure"] is False
    assert {"flow_adherence", "flow_overshoot"} <= set(summary["annotations"])
    assert summary["flow_rmse_ml_s"] is not None


def test_a_no_pressure_shot_needs_both_a_target_and_a_measured_flow() -> None:
    for zeroed in ("tf", "fl"):
        samples = [{**s, zeroed: 0.0} for s in _samples(pressure=False)]
        slog = make_slog(samples, [(0, 0, "Preinfusion"), (6, 1, "Extraction")])
        summary: Any = compute_summary_diagnostics(slog, phase_controls=("flow", "flow"))
        assert summary is not None and summary["flow_rmse_ml_s"] is None, zeroed
        assert summary["flow_grading"] == "not_graded", zeroed


def test_a_negative_pump_flow_is_still_a_measured_flow() -> None:
    """The pump flow is logged with small negatives (`fl` allows them): still a reading."""
    samples = [{**s, "fl": -0.1 * (i % 3 + 1)} for i, s in enumerate(_samples(pressure=False))]
    slog = make_slog(samples, [(0, 0, "Preinfusion"), (6, 1, "Extraction")])
    summary: Any = compute_summary_diagnostics(slog, phase_controls=("flow", "flow"))
    assert summary is not None and summary["flow_rmse_ml_s"] is not None


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
def test_a_pure_pressure_profile_has_no_flow_to_grade_in_either_shape(path: Path) -> None:
    """A pressure profile on a board with a sensor: flow is not applicable, pressure is graded."""
    slog = parse_slog(path.read_bytes())
    slog = dataclasses.replace(
        slog, samples=[s.model_copy(update={"tf": 0.0}) for s in slog.samples]
    )
    assert slog.has_pressure
    controls = _every_phase(slog, "pressure")
    summary: Any = transform_shot(slog, "summary", phase_controls=controls)["diagnostics"]
    full: Any = transform_shot(slog, "per_phase", phase_controls=controls)["diagnostics"]
    assert summary["flow_rmse_ml_s"] is None
    assert summary["flow_grading"] == "not_applicable"
    assert summary["pressure_grading"] == "graded"
    compliance = full["profile_compliance"]
    assert compliance["flow_rmse_ml_s"] is None
    assert compliance["flow_grading"] == "not_applicable"
    assert compliance["pressure_grading"] == "graded"
