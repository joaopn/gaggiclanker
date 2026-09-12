"""Standard boards have no pressure sensor — the diagnostics must say so.

This is the one behavioural change made to the vendored engine. Upstream had
no notion of a machine without a pressure sensor, so on a Standard board's hard
zeros it would report resistance VERY_LOW, channeling LOW and pressure
adherence EXCELLENT: three confident readings of a sensor that does not exist.
"""

from __future__ import annotations

from typing import Any

from gaggiclanker.domain.diagnostics import (
    compute_shot_diagnostics,
    compute_summary_diagnostics,
    transform_shot,
)
from gaggiclanker.domain.scoring import execution_score
from tests.domain.helpers import make_slog


def _samples(*, pressure: bool) -> list[dict[str, Any]]:
    return [
        {
            "t": i * 250,
            "tt": 93.0,
            "ct": 92.8 + (i % 3) * 0.1,
            "tp": 9.0 if pressure else 0.0,
            "cp": (2.0 + i * 0.7 if i < 8 else 9.0) if pressure else 0.0,
            "pf": 0.2 + i * 0.12,
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
    diagnostics = compute_shot_diagnostics(_pro())
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
    summary = compute_summary_diagnostics(_standard())
    assert summary is not None
    assert summary["has_pressure"] is False
    assert summary["resistance_avg"] is None
    assert summary["channeling_risk"] is None
    assert summary["pressure_rmse_bar"] is None
    assert "resistance_level" not in summary["annotations"]
    # Flow adherence needs no pressure sensor, so it survives.
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
