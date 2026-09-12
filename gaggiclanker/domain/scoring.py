"""Deterministic execution score.

Vendored from crema (`src/crema/scoring.py`, MIT) and adapted — see
`gaggiclanker/domain/VENDORED.md`.

The score answers one question: **how cleanly did the machine execute this
shot?** It says nothing about whether the coffee tasted good — that is the
maintainer's own cup rating, and conflating the two would let a well-pulled
shot of stale beans drag down the diagnostic signal.

Penalties are capped on purpose. One noisy sensor should not fail an otherwise
clean shot, while high-confidence channeling stays the strongest single fault.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from gaggiclanker.domain.diagnostics import ShotDiagnostics, SummaryDiagnostics, TransformedShot

#: Channeling is the one fault worth more than a couple of points: it is the
#: difference between a shot that under-extracted and a shot that never had a
#: chance.
_CHANNELING_PENALTIES: dict[str, float] = {
    "LOW": 0.0,
    "MODERATE": 1.0,
    "HIGH": 2.2,
    "VERY_HIGH": 3.4,
}

#: Erosion labels come from `_RESISTANCE_SLOPE_BANDS`: INCREASING, FLAT,
#: GRADUAL_DECLINE, MODERATE_DECLINE, STEEP_DECLINE. Upstream checked for
#: "HIGH"/"VERY_HIGH" here, which are *level* labels — so the penalty never
#: fired. A declining puck resistance is the puck eroding under pressure, and
#: the steeper the decline the more of the bed has given way.
_EROSION_PENALTIES: dict[str, float] = {
    "MODERATE_DECLINE": 0.7,
    "STEEP_DECLINE": 1.2,
}


@dataclass(frozen=True)
class Recipe:
    """What this shot was *meant* to be. Every field optional.

    An absent target never imposes a generic espresso ideal: a score without a
    recipe judges execution only, which is the honest answer when nobody said
    what the shot was aiming for.
    """

    target_yield_g: float | None = None
    target_profile_id: str | None = None


@dataclass(frozen=True)
class ExecutionScore:
    """A reproducible 1-10 score and the evidence behind it."""

    score: float
    confidence: str
    reason: str
    #: Penalty per component, negative, keyed by fault name. Empty = clean.
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "confidence": self.confidence,
            "reason": self.reason,
            "components": dict(self.components),
        }


def execution_score(
    shot: TransformedShot,
    recipe: Recipe | None = None,
) -> ExecutionScore:
    """Score how cleanly the machine executed *shot*.

    Works from either detail level's diagnostics: the summary block carries the
    same indicators under flatter keys.
    """
    diagnostics = shot.get("diagnostics")
    if diagnostics is None:
        return ExecutionScore(
            score=5.0,
            confidence="low",
            reason="Telemetry is too sparse for an execution assessment.",
            components={"data_quality": -5.0},
        )

    # The two detail levels carry the same indicators under different keys;
    # `resistance` only exists on the full block.
    full: ShotDiagnostics | None = None
    summary: SummaryDiagnostics | None = None
    if "resistance" in diagnostics:
        full = cast(ShotDiagnostics, diagnostics)
    else:
        summary = diagnostics

    penalties: dict[str, float] = {}

    risk = _channeling_risk(full, summary)
    channel_penalty = _CHANNELING_PENALTIES.get(risk or "", 0.0)
    if channel_penalty:
        penalties["channeling"] = channel_penalty

    stability = _temperature_stability(full, summary)
    if stability is not None and stability > 0.5:
        penalties["temperature_stability"] = min(1.2, round((stability - 0.5) * 0.6, 2))

    pressure_rmse, flow_rmse = _adherence(full, summary)
    if pressure_rmse is not None and pressure_rmse > 0.5:
        penalties["pressure_adherence"] = min(1.0, round((pressure_rmse - 0.5) * 0.5, 2))
    if flow_rmse is not None and flow_rmse > 0.35:
        penalties["flow_adherence"] = min(1.4, round((flow_rmse - 0.35) * 0.8, 2))

    erosion = _erosion(full, summary)
    if erosion in _EROSION_PENALTIES:
        penalties["resistance_erosion"] = _EROSION_PENALTIES[erosion]

    if recipe is not None:
        target_yield = recipe.target_yield_g
        actual_yield = shot.get("final_weight_g")
        if target_yield and actual_yield is not None:
            deviation = abs(actual_yield - target_yield) / target_yield
            if deviation > 0.10:
                penalties["recipe_yield"] = min(1.2, round((deviation - 0.10) * 3.0, 2))
        if recipe.target_profile_id and recipe.target_profile_id != shot.get("profile_id"):
            penalties["recipe_profile"] = 0.5

    # A machine with no pressure sensor cannot be scored on channeling,
    # adherence or erosion at all — say so rather than scoring it a clean 10.
    if not shot.get("has_pressure", True):
        confidence = "low"
    elif risk == "INSUFFICIENT_DATA":
        confidence = "low"
    elif flow_rmse is None or full is None:
        confidence = "medium"
    else:
        confidence = "high"

    score = max(1.0, round(10.0 - sum(penalties.values()), 1))

    if penalties:
        dominant = max(penalties, key=lambda k: penalties[k])
        reason = (
            f"Execution capped by {dominant.replace('_', ' ')} "
            f"({penalties[dominant]:g} point penalty)."
        )
    elif not shot.get("has_pressure", True):
        reason = (
            "No material telemetry faults detected, but this machine has no "
            "pressure sensor so most execution checks did not run."
        )
    else:
        reason = "Clean execution: no material telemetry faults detected."

    return ExecutionScore(
        score=score,
        confidence=confidence,
        reason=reason,
        components={k: -v for k, v in penalties.items()},
    )


def _channeling_risk(
    full: ShotDiagnostics | None, summary: SummaryDiagnostics | None
) -> str | None:
    if full is not None:
        channeling = full["channeling"]
        return channeling["channeling_risk"] if channeling else None
    return summary["channeling_risk"] if summary else None


def _temperature_stability(
    full: ShotDiagnostics | None, summary: SummaryDiagnostics | None
) -> float | None:
    if full is not None:
        return full["temperature"]["stability_std_c"]
    return summary["temperature_stability_c"] if summary else None


def _adherence(
    full: ShotDiagnostics | None, summary: SummaryDiagnostics | None
) -> tuple[float | None, float | None]:
    if full is not None:
        compliance = full["profile_compliance"]
        if compliance is None:
            return None, None
        return compliance["pressure_rmse_bar"], compliance["flow_rmse_ml_s"]
    if summary is None:
        return None, None
    return summary["pressure_rmse_bar"], summary["flow_rmse_ml_s"]


def _erosion(full: ShotDiagnostics | None, summary: SummaryDiagnostics | None) -> str | None:
    if full is not None:
        resistance = full["resistance"]
        return resistance["annotations"].get("erosion") if resistance else None
    if summary is None:
        return None
    return summary["annotations"].get("resistance_erosion")
