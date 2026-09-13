"""The recipe numbers a profile document already states.

A GaggiMate profile carries two numbers a person would otherwise type into a
Set's recipe: the brew temperature, and — when a phase stops on the scale — the
weight in the cup. Picking a profile for a new Set fills those in, and the style
detector reads the same yield to tell an allongé from a short low-pressure shot.
Both read it through here, so "what yield does this profile aim for" has one
answer rather than two that could drift.

Pure and deterministic: the same document gives the same numbers, with no
dependence on the order a dict was built in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["ProfileRecipe", "max_volumetric_target_g", "profile_recipe"]


@dataclass(frozen=True, slots=True)
class ProfileRecipe:
    """What a profile says about the recipe. ``None`` means it does not say."""

    temperature_c: float | None = None
    target_yield_g: float | None = None


def _number(value: Any) -> float | None:
    # `bool` is an `int` to Python; a stray `true` is not a temperature.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def max_volumetric_target_g(phases: Iterable[Any]) -> float | None:
    """The largest `volumetric` stop condition across the phases, in grams.

    The largest rather than the last: a lever or a blooming profile stops each
    step at a growing weight (the Cremina profile's phases end at 6, 14, 26 and
    36 g), and the one that ends the shot is the biggest. `pumped` targets are
    water through the pump, not coffee in the cup, and are not a yield. A target
    of 0 is no target, so a profile whose only volumetric stops are 0 answers
    ``None`` rather than "a 0 g shot".
    """
    values: list[float] = []
    for phase in phases:
        if not isinstance(phase, Mapping):
            continue
        for target in phase.get("targets") or []:
            if not isinstance(target, Mapping) or str(target.get("type", "")) != "volumetric":
                continue
            value = _number(target.get("value"))
            if value is not None and value > 0:
                values.append(value)
    return max(values) if values else None


def profile_recipe(profile: Mapping[str, Any] | None) -> ProfileRecipe:
    """The temperature and target yield a profile document states.

    The temperature is the profile's own `temperature` when it is above 0 — the
    firmware writes 0 for "not set", and a phase-level temperature is a local
    override rather than the brew temperature, so it is not consulted.
    """
    if not profile:
        return ProfileRecipe()
    temperature = _number(profile.get("temperature"))
    phases = profile.get("phases")
    return ProfileRecipe(
        temperature_c=temperature if temperature is not None and temperature > 0 else None,
        target_yield_g=max_volumetric_target_g(phases if isinstance(phases, list) else []),
    )
