"""The recipe numbers read out of a profile document.

Over the real fixtures, because the shapes that matter are the ones the firmware
and the published profiles actually use: a single volumetric stop, a staircase
of growing stops, `pumped` targets that are water rather than coffee, and the
utility profiles that stop on nothing at all.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gaggiclanker.domain.profile_recipe import (
    ProfileRecipe,
    max_volumetric_target_g,
    profile_recipe,
)
from tests.domain.helpers import PROFILE_FIXTURES


def _load(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((PROFILE_FIXTURES / f"{name}.json").read_text())
    return document


@pytest.mark.parametrize(
    ("name", "temperature_c", "target_yield_g"),
    [
        ("firmware-9bar", 93.0, 36.0),
        # Every `pumped` 100 is water through the pump; only the volumetric 38 is the cup.
        ("firmware-adapt", 93.0, 38.0),
        # A staircase of stops at 6, 14, 26 and 36 g: the shot ends at the largest.
        ("docs-cremina-lever", 89.0, 36.0),
        ("docs-medium-18g", 93.0, 36.0),
        ("firmware-lever", 86.5, 36.0),
        ("firmware-lmleva", 89.0, 36.0),
        ("mcp-automatic-pro-18g", 91.0, 36.0),
        # The utility profiles heat the group but stop on nothing: no yield.
        ("firmware-flush", 93.0, None),
        ("docs-backflush", 93.0, None),
    ],
)
def test_the_fixtures(name: str, temperature_c: float, target_yield_g: float | None) -> None:
    assert profile_recipe(_load(name)) == ProfileRecipe(
        temperature_c=temperature_c, target_yield_g=target_yield_g
    )


def test_a_zero_temperature_is_the_firmware_saying_not_set() -> None:
    document = _load("firmware-9bar")
    document["temperature"] = 0
    assert profile_recipe(document).temperature_c is None


def test_a_zero_volumetric_stop_is_no_yield() -> None:
    phases = [{"targets": [{"type": "volumetric", "value": 0}, {"type": "pumped", "value": 90}]}]
    assert max_volumetric_target_g(phases) is None


def test_nothing_to_read_is_nothing_stated() -> None:
    assert profile_recipe(None) == ProfileRecipe()
    assert profile_recipe({}) == ProfileRecipe()
    # Malformed shapes are read past rather than raised on: the list row must
    # render for a version whatever its document looks like.
    assert profile_recipe({"temperature": True, "phases": "nope"}) == ProfileRecipe()
    assert max_volumetric_target_g([None, {"targets": [None, {"type": "volumetric"}]}]) is None


def test_the_same_document_gives_the_same_numbers_whatever_the_phase_order() -> None:
    document = _load("docs-cremina-lever")
    reordered = {**document, "phases": list(reversed(document["phases"]))}
    assert profile_recipe(document) == profile_recipe(reordered)


# ── the ratio target (firmware 1.10) ─────────────────────────────────


def test_a_ratio_target_is_the_dose_times_the_ratio() -> None:
    phases = [{"targets": [{"type": "ratio", "value": 2.0}]}]
    assert max_volumetric_target_g(phases, 18) == 36.0
    document = _load("firmware-9bar")
    document["dose"] = 18
    document["phases"][0]["targets"] = [{"type": "ratio", "operator": "gte", "value": 2.5}]
    assert profile_recipe(document).target_yield_g == 45.0


@pytest.mark.parametrize("dose", [None, 0, 0.0, -3, "18", True])
def test_a_ratio_target_with_no_dose_is_no_yield(dose: Any) -> None:
    """The firmware ignores a ratio with no dose to multiply, and so does the yield; a stray
    string or `true` is no dose either."""
    phases = [{"targets": [{"type": "ratio", "value": 2.0}]}]
    assert max_volumetric_target_g(phases, dose) is None


def test_a_ratio_and_a_weight_target_give_the_larger_yield() -> None:
    phases = [
        {"targets": [{"type": "volumetric", "value": 30}]},
        {"targets": [{"type": "ratio", "value": 2.0}]},
    ]
    assert max_volumetric_target_g(phases, 18) == 36.0
    assert max_volumetric_target_g(phases, 10) == 30.0
    assert max_volumetric_target_g(phases) == 30.0


def test_a_ratio_of_zero_is_no_target() -> None:
    assert max_volumetric_target_g([{"targets": [{"type": "ratio", "value": 0}]}], 18) is None


def test_the_ratio_fixtures_the_web_is_pinned_on_too() -> None:
    """`web/src/lib/profileInfo.test.tsx` reads the same two files: 2 x 18 g with the dose and
    nothing without it."""
    from tests.domain.helpers import FIXTURES

    with_dose = json.loads((FIXTURES / "ratio_profile" / "ratio-18g.json").read_text())
    without = json.loads((FIXTURES / "ratio_profile" / "ratio-no-dose.json").read_text())
    assert profile_recipe(with_dose).target_yield_g == 36.0
    assert profile_recipe(without).target_yield_g is None


def test_an_inexact_ratio_yield_is_rounded_to_a_tenth_like_the_web() -> None:
    """2.1 x 18 is 37.800000000000004 in floating point; the web's `ratioStop.ts` shows 37.8."""
    from tests.domain.helpers import FIXTURES

    document = json.loads((FIXTURES / "ratio_profile" / "ratio-2-1.json").read_text())
    assert profile_recipe(document).target_yield_g == 37.8
    assert max_volumetric_target_g(document["phases"], 18) == 37.8


def test_an_exact_tie_rounds_to_the_even_tenth_like_the_web() -> None:
    """2.5 x 14.5 is exactly 36.25: Python rounds the tie to 36.2, and the web's `ratioStop.ts`
    must say the same, not `Math.round`'s 36.3."""
    from tests.domain.helpers import FIXTURES

    document = json.loads((FIXTURES / "ratio_profile" / "ratio-tie.json").read_text())
    assert profile_recipe(document).target_yield_g == 36.2
