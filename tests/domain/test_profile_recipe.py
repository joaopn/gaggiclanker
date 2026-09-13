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
