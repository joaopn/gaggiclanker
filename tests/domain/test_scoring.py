"""Execution score: the deterministic half of judging a shot."""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.domain.diagnostics import TransformedShot, transform_shot
from gaggiclanker.domain.scoring import Recipe, execution_score
from gaggiclanker.domain.slog import encode_slog, parse_slog
from tests.domain.helpers import make_slog, slog_from_export


def _clean_shot() -> TransformedShot:
    """A steady 9-bar extraction that tracks its targets."""
    samples: list[dict[str, Any]] = [
        {
            "t": i * 250,
            "tt": 93.0,
            "ct": 93.0,
            "tp": 9.0,
            "cp": 9.0,
            "pf": 2.0,
            "tf": 2.0,
            "v": i * 1.5,
        }
        for i in range(24)
    ]
    return transform_shot(
        make_slog(samples, [(0, 0, "Extraction")], final_weight_g=36.0), "per_phase"
    )


def _with(shot: TransformedShot, **over: Any) -> TransformedShot:
    diagnostics = shot["diagnostics"]
    assert diagnostics is not None
    for key, value in over.items():
        block, _, field = key.partition("__")
        target = diagnostics[block] if block in diagnostics else None  # type: ignore[literal-required]
        assert target is not None
        target[field] = value
    return shot


def test_clean_shot_scores_ten() -> None:
    score = execution_score(_clean_shot())
    assert score.score == 10.0
    assert score.components == {}
    assert score.confidence == "high"
    assert "Clean execution" in score.reason


def test_no_diagnostics_is_a_neutral_five() -> None:
    """Too little telemetry is not a bad shot; refuse to pretend otherwise."""
    shot = transform_shot(make_slog([{"t": 0, "cp": 9.0, "pf": 2.0}]))
    score = execution_score(shot)
    assert score.score == 5.0
    assert score.confidence == "low"
    assert score.components == {"data_quality": -5.0}


@pytest.mark.parametrize(
    ("risk", "expected"),
    [("LOW", 10.0), ("MODERATE", 9.0), ("HIGH", 7.8), ("VERY_HIGH", 6.6)],
)
def test_channeling_is_the_heaviest_single_penalty(risk: str, expected: float) -> None:
    score = execution_score(_with(_clean_shot(), channeling__channeling_risk=risk))
    assert score.score == expected


def test_erosion_penalty_fires_on_the_labels_the_engine_actually_emits() -> None:
    """The vendored bug: upstream looked for "HIGH"/"VERY_HIGH" here.

    Those are *level* labels. Erosion is banded FLAT / GRADUAL_DECLINE /
    MODERATE_DECLINE / STEEP_DECLINE / INCREASING, so the penalty could never
    fire on any shot at all.
    """
    shot = _clean_shot()
    diagnostics = shot["diagnostics"]
    assert diagnostics is not None
    resistance = diagnostics["resistance"]  # type: ignore[typeddict-item]
    assert resistance is not None

    resistance["annotations"]["erosion"] = "MODERATE_DECLINE"
    assert execution_score(shot).components["resistance_erosion"] == -0.7

    resistance["annotations"]["erosion"] = "STEEP_DECLINE"
    assert execution_score(shot).components["resistance_erosion"] == -1.2

    for benign in ("FLAT", "GRADUAL_DECLINE", "INCREASING", "HIGH", "VERY_HIGH"):
        resistance["annotations"]["erosion"] = benign
        assert "resistance_erosion" not in execution_score(shot).components


def test_temperature_penalty_is_capped() -> None:
    shot = _with(_clean_shot(), temperature__stability_std_c=50.0)
    assert execution_score(shot).components["temperature_stability"] == -1.2


def test_adherence_penalties_have_deadbands() -> None:
    """Small tracking error is normal; only a real miss should cost anything."""
    shot = _clean_shot()
    diagnostics = shot["diagnostics"]
    assert diagnostics is not None
    compliance = diagnostics["profile_compliance"]  # type: ignore[typeddict-item]
    assert compliance is not None

    compliance["pressure_rmse_bar"] = 0.5
    compliance["flow_rmse_ml_s"] = 0.35
    assert execution_score(shot).components == {}

    compliance["pressure_rmse_bar"] = 1.5
    compliance["flow_rmse_ml_s"] = 1.35
    components = execution_score(shot).components
    assert components["pressure_adherence"] == -0.5
    assert components["flow_adherence"] == -0.8


def test_recipe_yield_deviation_inside_ten_percent_is_free() -> None:
    shot = _clean_shot()
    assert shot["final_weight_g"] == 36.0
    assert execution_score(shot, Recipe(target_yield_g=34.0)).components == {}
    assert "recipe_yield" in execution_score(shot, Recipe(target_yield_g=28.0)).components


def test_a_shot_with_no_recipe_is_never_judged_against_one() -> None:
    """Absent targets impose no generic ideal — that is the honest answer."""
    assert execution_score(_clean_shot(), Recipe()).components == {}


def test_wrong_profile_costs_half_a_point() -> None:
    score = execution_score(_clean_shot(), Recipe(target_profile_id="somewhere-else"))
    assert score.components["recipe_profile"] == -0.5


def test_every_penalty_at_once_is_clamped_to_one() -> None:
    """Worst case is 9.9 points of penalty; the floor takes it from 0.1 to 1.0.

    The caps are the load-bearing part — 3.4 channeling + 1.2 temperature + 1.0
    pressure + 1.4 flow + 1.2 erosion + 1.2 yield + 0.5 profile — so no single
    noisy sensor can drag a shot down, and only a shot that failed *everything*
    reaches the floor at all.
    """
    shot = _with(
        _clean_shot(),
        channeling__channeling_risk="VERY_HIGH",
        temperature__stability_std_c=50.0,
    )
    diagnostics = shot["diagnostics"]
    assert diagnostics is not None
    compliance = diagnostics["profile_compliance"]  # type: ignore[typeddict-item]
    assert compliance is not None
    compliance["pressure_rmse_bar"] = 99.0
    compliance["flow_rmse_ml_s"] = 99.0
    resistance = diagnostics["resistance"]  # type: ignore[typeddict-item]
    assert resistance is not None
    resistance["annotations"]["erosion"] = "STEEP_DECLINE"

    score = execution_score(shot, Recipe(target_yield_g=1.0, target_profile_id="other"))
    assert sum(-v for v in score.components.values()) == pytest.approx(9.9)
    assert score.score == 1.0
    assert set(score.components) == {
        "channeling",
        "temperature_stability",
        "pressure_adherence",
        "flow_adherence",
        "resistance_erosion",
        "recipe_yield",
        "recipe_profile",
    }


def test_reason_names_the_dominant_fault() -> None:
    score = execution_score(_with(_clean_shot(), channeling__channeling_risk="HIGH"))
    assert "channeling" in score.reason


def test_insufficient_channeling_data_lowers_confidence() -> None:
    score = execution_score(_with(_clean_shot(), channeling__channeling_risk="INSUFFICIENT_DATA"))
    assert score.confidence == "low"


def test_summary_detail_level_scores_too() -> None:
    """The summary block carries the same indicators under flatter keys."""
    samples: list[dict[str, Any]] = [
        {"t": i * 250, "tt": 93.0, "ct": 93.0, "tp": 9.0, "cp": 9.0, "pf": 2.0, "tf": 2.0}
        for i in range(24)
    ]
    shot = transform_shot(make_slog(samples, [(0, 0, "Extraction")]), "summary")
    score = execution_score(shot)
    assert score.score == 10.0
    assert score.confidence == "medium"


def test_score_on_the_maintainers_real_shot_is_stable() -> None:
    """A regression pin on a real curve, not a synthetic one."""
    slog = parse_slog(encode_slog(slog_from_export("shot-129.json")), "000129")
    score = execution_score(transform_shot(slog, "per_phase"))
    assert score.score == 7.7
    assert score.confidence == "high"
    # The erosion penalty is the fixed bug firing on real data.
    assert score.components["resistance_erosion"] == -0.7
    assert score.components["flow_adherence"] == -1.32
    assert score.as_dict()["score"] == 7.7
