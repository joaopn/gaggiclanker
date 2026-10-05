"""The brew ratio, worked out in one place.

The shots table's Ratio column, the shot information's ratio, a Set's trend and
the starting point's mean ratio all answer the same question, and four copies of
the rule drifted: one read the version's dose when the person entered none, the
others did not. They all call this.
"""

from __future__ import annotations

__all__ = ["brew_ratio"]


def brew_ratio(
    *,
    judged_dose_g: float | None,
    version_dose_g: float | None,
    judged_yield_g: float | None,
    scale_yield_g: float | None,
) -> float | None:
    """The yield over the dose, to two decimals; ``None`` without both halves.

    The dose is the judgement's when the person entered one and the filed
    version's otherwise: the dose exists nowhere on the machine unless somebody
    typed it. The yield is the judgement's own when it has one and the scale's
    otherwise (a reading at or below zero is no yield). A shot with no dose
    anywhere has no ratio, and saying so is better than inventing one from the
    nominal basket size.
    """
    dose = judged_dose_g or version_dose_g
    out = judged_yield_g or (scale_yield_g if scale_yield_g and scale_yield_g > 0 else None)
    if not dose or not out:
        return None
    return round(float(out) / float(dose), 2)
