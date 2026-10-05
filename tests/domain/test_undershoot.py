"""The largest undershoot is how far below target the shot fell, and 0 when it never did.

It was `abs(min(deviation))`, which for a shot wholly above its target is the
smallest *overshoot*: a shot that overshot by 0.1 to 0.8 reported an undershoot
of 0.1.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.domain.diagnostics import compute_shot_diagnostics
from gaggiclanker.domain.phase_control import PhaseControl
from tests.domain.helpers import make_slog


def _diagnostics(deviations: list[float], control: PhaseControl) -> Any:
    samples: list[dict[str, Any]] = []
    for i, dev in enumerate(deviations):
        sample: dict[str, Any] = {
            "t": i * 250,
            "ct": 93.0 + dev,
            "tt": 93.0,
            "cp": 6.0,
            "pf": 1.5,
            "fl": 1.5,
            "tp": 0.0,
            "tf": 0.0,
        }
        if control == "pressure":
            sample["tp"], sample["cp"] = 6.0, 6.0 + dev
        else:
            sample["tf"], sample["fl"] = 1.5, 1.5 + dev
        samples.append(sample)
    diagnostics = compute_shot_diagnostics(
        make_slog(samples, [(0, 0, "Brew")]), phase_controls=(control,)
    )
    assert diagnostics is not None
    return diagnostics


def _undershoots(deviations: list[float], control: PhaseControl) -> tuple[float, float]:
    """The shot's own largest undershoot and overshoot, for the target it steers by."""
    compliance = _diagnostics(deviations, control)["profile_compliance"]
    if control == "pressure":
        return compliance["max_pressure_undershoot_bar"], compliance["max_pressure_overshoot_bar"]
    return compliance["max_flow_undershoot_ml_s"], compliance["max_flow_overshoot_ml_s"]


ALL_ABOVE = [0.1, 0.3, 0.5, 0.8, 0.6, 0.4, 0.2, 0.7]
MIXED = [0.5, -0.3, 0.2, -0.7, 0.1, -0.2, 0.4, 0.3]


@pytest.mark.parametrize("control", ["pressure", "flow"])
def test_a_shot_wholly_above_target_has_no_undershoot(control: PhaseControl) -> None:
    undershoot, overshoot = _undershoots(ALL_ABOVE, control)
    assert undershoot == 0.0
    assert overshoot == 0.8


@pytest.mark.parametrize("control", ["pressure", "flow"])
def test_the_undershoot_of_a_mixed_shot_is_its_deepest_dip(control: PhaseControl) -> None:
    undershoot, overshoot = _undershoots(MIXED, control)
    assert undershoot == 0.7
    assert overshoot == 0.5


def test_a_shot_wholly_below_target_has_no_overshoot_and_its_deepest_dip() -> None:
    undershoot, overshoot = _undershoots([-0.1, -0.4, -0.9, -0.5, -0.2, -0.3], "pressure")
    assert (undershoot, overshoot) == (0.9, 0.0)
