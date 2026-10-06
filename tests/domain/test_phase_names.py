"""A profile's phase name meets the 24 bytes the firmware logs of it by one rule."""

from __future__ import annotations

import pytest

from gaggiclanker.domain.diagnostics import as_sample_dicts
from gaggiclanker.domain.metric_language import Expression, ShotData, evaluate
from gaggiclanker.domain.phase_names import (
    LOGGED_NAME_BYTES,
    clashing_names,
    cut_logged_name,
    phase_key,
    same_phase,
)
from tests.lever_shot import lever_shot


def test_a_name_is_cut_to_24_bytes_on_a_character_boundary() -> None:
    assert LOGGED_NAME_BYTES == 24
    assert cut_logged_name("ramp") == "ramp"
    assert cut_logged_name("a" * 24) == "a" * 24
    assert cut_logged_name("a" * 30) == "a" * 24
    # "É" is two bytes: 23 bytes of É and a, then the next É would be cut through its middle.
    assert cut_logged_name("a" + "É" * 12) == "a" + "É" * 11
    assert len(cut_logged_name("a" + "É" * 12).encode()) == 23


@pytest.mark.parametrize(
    ("logged", "profile", "same"),
    [
        ("Pre-infusion with a long", "Pre-infusion with a long soak", True),
        ("pre-infusion with a long", "Pre-infusion with a long soak", True),
        ("Pre-infusion with a lon", "Pre-infusion with a long soak", False),
        ("Pre-infusion with a long soak", "Pre-infusion with a long soak", True),
        ("ramp", "Ramp", True),
        ("ramp", "ramp up", False),
        ("a" + "É" * 11 + "�", "a" + "É" * 12 + " tail", True),
    ],
)
def test_a_logged_name_matches_the_profile_name_it_was_cut_from(
    logged: str, profile: str, same: bool
) -> None:
    assert same_phase(logged, profile) is same
    assert same_phase(profile, logged) is same


def test_names_that_a_log_cannot_tell_apart_are_clashes() -> None:
    assert clashing_names(["a" * 30 + "x", "a" * 30 + "y"]) == [("a" * 30 + "x", "a" * 30 + "y")]
    assert clashing_names(["preinfusion", "ramp", "decline"]) == []
    # The same name twice is a repeated phase, not two a log cannot tell apart.
    assert clashing_names(["Pressurize", "Ramp", "pressurize"]) == []
    assert phase_key("A" * 40) == "a" * 24
    assert phase_key("Pre  infusion with a long soak") == "pre infusion with a lon"


@pytest.mark.parametrize(
    "long_name",
    [
        "ramp and then some more words",
        "ramp  and then  some more words",
        " ramp and then some words",
    ],
)
def test_a_window_over_a_long_phase_name_reads_the_log_of_the_cut_one(long_name: str) -> None:
    slog = lever_shot()
    transitions = [
        t.model_copy(
            update={
                "phase_name": cut_logged_name(long_name) if t.phase_name == "ramp" else t.phase_name
            }
        )
        for t in slog.transitions
    ]
    data = ShotData.build(
        as_sample_dicts(slog),
        transitions,
        profile_phases=["preinfusion", "soak", long_name, "decline"],
        final_weight_g=slog.header.final_weight_g,
    )
    window = {"channel": "cup_weight", "op": "at_end", "window": {"phase": long_name}}
    found = evaluate(Expression.model_validate(window), data)
    assert found.value is not None and found.absent is None
    # The phase the shot never reached is still not reached, not "no such phase".
    gone = evaluate(Expression.model_validate({**window, "window": {"phase": "decline"}}), data)
    assert gone.absent == "phase_not_reached"
