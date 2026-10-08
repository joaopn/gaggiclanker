"""A phase's type is its profile's word first, then its name, then its curve.

Built on a real shot whose first phase ended before the machine logged a sample: the log opens
in the profile's second phase, which a curve rule that counts the logged order mistook for a
pre-infusion.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from gaggiclanker.domain.diagnostics import _classify_phase
from gaggiclanker.domain.phase_control import phase_types
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.sync.derive import derive_shot

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RAW = (FIXTURES / "slog_edge" / "shot_225_fill_ended_at_start.slog").read_bytes()
PROFILE: dict[str, Any] = json.loads((FIXTURES / "slog_edge" / "alma_lever_16_32.json").read_text())


def _types(profile: Mapping[str, Any] | None) -> dict[str, str]:
    derived = derive_shot(
        parse_slog(RAW, "000225"), RAW, device_id="000225", source="import", profile=profile
    )
    return {
        phase["name"]: phase["diagnostics"]["phase_type"]
        for phase in json.loads(derived.shot.phases_json or "[]")
    }


def test_the_ramp_is_a_brew_phase_because_the_profile_says_so() -> None:
    assert _types(PROFILE) == {"Ramp": "brew", "Decline": "decline"}


def test_without_a_profile_the_curve_counts_the_profiles_phase_number_not_the_logged_order() -> (
    None
):
    # The Ramp is the first phase logged and the profile's second: the "first phase, low and
    # rising, is a pre-infusion" rule is about the profile's first phase.
    assert _types(None) == {"Ramp": "brew", "Decline": "decline"}


def test_a_profile_that_calls_the_phase_a_preinfusion_wins_over_its_name_and_curve() -> None:
    profile = copy.deepcopy(PROFILE)
    profile["phases"][1]["phase"] = "preinfusion"
    assert _types(profile)["Ramp"] == "preinfusion"


def test_a_brew_phase_may_still_be_the_decline_its_name_or_curve_shows() -> None:
    assert _classify_phase("Decline", None, 2, 3, profile_type="brew") == "decline"
    assert _classify_phase("Pre-infusion", None, 0, 3, profile_type="brew") == "brew"
    assert _classify_phase("Pre-infusion", None, 0, 3, profile_type="preinfusion") == "preinfusion"


def test_the_profile_types_are_read_leniently() -> None:
    assert phase_types(None) is None
    assert phase_types({"phases": "x"}) is None
    assert phase_types(PROFILE) == ("preinfusion", "brew", "brew")
    assert phase_types({"phases": [{"phase": "other"}, 3, {"name": "x"}]}) == (None, None, None)
