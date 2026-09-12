"""Style detection, over the real profile fixtures.

The fixtures are what the archive actually holds — two lever profiles from the
firmware and the docs, a flow-led Automatic Pro, a plain 9 bar single-phase, a
bloom, and two backflushes — so this file doubles as the record of what each one
is taken to be.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.analyzer.style import detect_style

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "profiles"


def _profile(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return document


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # Both Cremina profiles decline several bar over tens of seconds. The
        # docs one spells it as four 10 s steps of a bar each, which is exactly
        # the case a per-phase test would miss.
        ("docs-cremina-lever", "lever"),
        ("firmware-lever", "lever"),
        ("firmware-lmleva", "lever"),
        # A pump-off phase is the most specific thing a profile can say, and it
        # is checked before the 9 bar hold that follows it.
        ("docs-medium-18g", "bloom"),
        ("firmware-9bar", "classic"),
        # Flow-led, but the flow targets are in the *preinfusion* phases; the
        # brew phases are 12 bar capped and 1.2 ml/s, so this is a classic shot
        # spelled in flow, not a turbo one.
        ("mcp-automatic-pro-18g", "classic"),
        ("firmware-adapt", "classic"),
        # Neither backflush brews anything. One carries the firmware's flag, the
        # other predates it and is caught structurally.
        ("firmware-flush", "utility"),
        ("docs-backflush", "utility"),
    ],
)
def test_the_fixture_profiles_classify(name: str, expected: str) -> None:
    verdict = detect_style(_profile(name), dose_g=18.0)
    assert verdict.style == expected, verdict.render()
    assert verdict.tier == "profile"
    assert verdict.evidence, "a verdict with no evidence is an assertion"


def test_a_backflush_is_not_read_as_a_bloom() -> None:
    """The specific regression the utility test exists for.

    Every `Depressurize` phase runs the pump at zero, which is the bloom
    signature. Without the utility check first, the analyzer would hand the
    model bloom expectations for a cleaning cycle.
    """
    verdict = detect_style(_profile("docs-backflush"))
    assert verdict.style == "utility"
    assert "no stop condition" in verdict.evidence[0]


def _one_phase_profile(pump: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": "nine bar with headroom",
        "phases": [
            {
                "name": "brew",
                "phase": "brew",
                "duration": 28,
                "pump": pump,
                "targets": [{"type": "volumetric", "value": 36}],
            }
        ],
    }


def test_turbo_needs_a_flow_targeted_brew_phase() -> None:
    """A pressure phase's `flow` is a ceiling, not a setpoint."""
    ceiling = _one_phase_profile({"target": "pressure", "pressure": 9, "flow": 5})
    assert detect_style(ceiling, dose_g=18).style == "classic"

    setpoint = _one_phase_profile({"target": "flow", "pressure": 9, "flow": 5})
    assert detect_style(setpoint, dose_g=18).style == "turbo"


def test_allonge_needs_a_dose_to_be_a_ratio() -> None:
    """1:3.5 is a ratio, and a ratio needs both numbers."""
    profile: dict[str, Any] = {
        "label": "long and gentle",
        "phases": [
            {
                "name": "brew",
                "phase": "brew",
                "duration": 45,
                "pump": {"target": "pressure", "pressure": 6, "flow": 0},
                "targets": [{"type": "volumetric", "value": 70}],
            }
        ],
    }
    assert detect_style(profile, dose_g=18).style == "allonge"
    # No dose: the allongé test is skipped rather than guessed, and 6 bar with
    # no bloom and no decline is a dark profile.
    assert detect_style(profile).style == "dark"


def test_phase_names_are_the_fallback() -> None:
    """A profile whose numbers say nothing, but whose author did."""
    unreadable: dict[str, Any] = {
        "label": "Turbo experiment",
        "phases": [{"name": "go", "pump": None}],
    }
    verdict = detect_style(unreadable)
    assert verdict.style == "turbo"
    assert verdict.tier == "phase_names"


def test_a_real_shot_is_read_off_its_header_name_before_its_curve() -> None:
    """The archive's own shots, whose profiles the mirror never captured.

    Both of these are named "... Bloom [AI]" in the `.slog` header. Read off
    the telemetry alone they fingerprint as lever — a long shot that peaked well
    under 9 bar — which is exactly the wrong expectation to hand the model.
    """
    from gaggiclanker.domain.slog import parse_slog

    slogs = Path(__file__).resolve().parents[1] / "fixtures" / "slog"
    for name in ("shot_204_ramping_flow", "shot_222_hold_false_positive"):
        header = parse_slog((slogs / f"{name}.slog").read_bytes()).header
        assert "Bloom" in header.profile_name, "the fixture's own name is the point"

        verdict = detect_style(None, profile_name=header.profile_name)

        assert verdict.style == "bloom", f"{name}: {verdict.render()}"
        assert verdict.tier == "phase_names"


def test_the_header_name_loses_to_a_stored_profile() -> None:
    """A name is what somebody typed; the profile is what the machine ran."""
    verdict = detect_style(_profile("firmware-9bar"), dose_g=18, profile_name="Amigo Bloom [AI]")

    assert verdict.style == "classic"
    assert verdict.tier == "profile"


def test_telemetry_is_the_last_resort() -> None:
    """A shot whose profile the mirror never captured."""
    summary: dict[str, Any] = {
        "flow": {"avg_flow_ml_s": 4.2},
        "pressure": {"max_bar": 6.0},
        "extraction": {"total_time_s": 14.0},
    }
    verdict = detect_style(None, summary=summary)
    assert verdict.style == "turbo"
    assert verdict.tier == "telemetry"

    assert detect_style(None, summary=None).style == "unknown"
    # A name that says nothing falls through to the curve rather than stopping.
    assert detect_style(None, summary=summary, profile_name="Gratus 16:32").style == "turbo"


def test_a_verdict_is_stable() -> None:
    """Detection is pure: the same profile always gives the same answer."""
    profile = _profile("firmware-lever")
    first = detect_style(profile, dose_g=18)
    second = detect_style(profile, dose_g=18)
    assert (first.style, first.tier, first.evidence) == (second.style, second.tier, second.evidence)
