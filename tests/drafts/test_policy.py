"""Layer 2, against every profile this repository ships and against nonsense.

Two halves, and they answer different questions.

The **fixtures** half asks whether the policy is usable: nine real profiles from
the firmware's own `data/p/`, the docs site and gaggimate-mcp, none of which
anybody wrote with this policy in mind. If a bound rejects one of them the bound
is wrong, not the profile — the numbers in these files are what people actually
brew with.

The **adversarial** half asks whether it catches what the firmware would
silently accept, and it is deliberately split across the two layers it needs:
zero phases, a float `pump`, `operator: "gt"` and `type: "weight"` never reach
the policy at all, because the strict schema refuses them first. Naming which
layer catches which is the point of the test; a single "it is rejected" would
pass even if one layer stopped working.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.profile_policy import (
    DEFAULT_BOUNDS,
    POLICY_SETTING_KEYS,
    PolicyBounds,
    ProfileRejected,
    bounds_from,
    check,
    clamp,
    enforce,
)
from gaggiclanker.settings import SETTINGS_REGISTRY
from tests.drafts.conftest import every_profile_fixture, profile_fixture

FIXTURE_IDS = [name for name, _ in every_profile_fixture()]


# ── the shipped profiles ─────────────────────────────────────────────


@pytest.mark.parametrize(("name", "document"), every_profile_fixture(), ids=FIXTURE_IDS)
def test_every_shipped_profile_passes_the_policy_untouched(
    name: str, document: dict[str, Any]
) -> None:
    """No clamps and no violations, on all nine.

    These are real profiles: the firmware's `9bar`, `adapt`, `lever`, `lmleva`
    and the backflush utility, the docs site's three, and gaggimate-mcp's
    automatic pro. A bound that moves any number in them is a bound set for a
    machine nobody owns.
    """
    profile = Profile.model_validate(document)
    clamped, changes = clamp(profile)
    assert changes == [], f"{name} was clamped: {[change.path for change in changes]}"
    assert check(clamped) == []


@pytest.mark.parametrize(("name", "document"), every_profile_fixture(), ids=FIXTURE_IDS)
def test_clamping_a_compliant_profile_changes_nothing_at_all(
    name: str, document: dict[str, Any]
) -> None:
    """Not merely "no changes reported" — the same document comes out.

    A clamp that silently rebuilt a phase would report nothing and still produce
    a different profile, and the round-trip comparison downstream would then be
    comparing against something the person never approved.
    """
    profile = Profile.model_validate(document)
    clamped, _ = clamp(profile)
    assert clamped.model_dump() == profile.model_dump()


def test_the_utility_backflush_terminates_on_duration_alone() -> None:
    """Nine phases, no targets, ten seconds each: the clock is the stop condition.

    Worth its own test because the termination rule has two arms and this is the
    only shipped profile that relies on the second one.
    """
    profile = Profile.model_validate(profile_fixture("firmware-flush"))
    assert profile.utility is True
    assert all(not phase.targets for phase in profile.phases)
    assert check(profile) == []


# ── layer 1 catches these, and says so ───────────────────────────────


#: Four documents the firmware parses without complaint and misreads. Kept as a
#: table rather than four tests because the point is that all four fail at the
#: *same* layer, and a table makes a fifth one a one-line addition.
FIRMWARE_TRAPS: dict[str, Callable[[dict[str, Any]], None]] = {
    "zero-phases": lambda doc: doc.update(phases=[]),
    "float-pump": lambda doc: doc["phases"][0].update(pump=100.0),
    "operator-gt": lambda doc: doc["phases"][0]["targets"][0].update(operator="gt"),
    "target-weight": lambda doc: doc["phases"][0]["targets"][0].update(type="weight"),
}


@pytest.mark.parametrize("trap", list(FIRMWARE_TRAPS), ids=list(FIRMWARE_TRAPS))
def test_the_schema_refuses_what_the_firmware_would_accept_and_ruin(trap: str) -> None:
    """Four documents the firmware parses without complaint and misreads.

    * **zero phases** crashes brew start on the display;
    * **`pump: 100.0`** is parsed as the advanced object form with every field
      zero, leaving a profile that never runs the pump — the reason `pump` is a
      `StrictInt`;
    * **`operator: "gt"`** parses as `lte` (`profile.h:291`), silently inverting
      a stop condition, so a "stop at 9 bar" becomes "stop below 9 bar";
    * **`type: "weight"`** is silently dropped, leaving a phase with one fewer
      stop condition than its author wrote.

    None of these is the policy's job. The policy never sees them.
    """
    document = profile_fixture("firmware-9bar")
    FIRMWARE_TRAPS[trap](document)
    with pytest.raises(ValidationError):
        Profile.model_validate(document)


# ── layer 2 catches these ────────────────────────────────────────────


def test_a_hundred_and_fifty_degrees_is_clamped_to_the_ceiling() -> None:
    """The firmware takes 150 °C. The policy takes 100, and says what it moved."""
    document = profile_fixture("firmware-9bar")
    document["temperature"] = 150
    clamped, changes = clamp(Profile.model_validate(document))
    assert clamped.temperature == DEFAULT_BOUNDS.temperature_max_c
    assert [change.path for change in changes] == ["temperature"]
    assert changes[0].before == 150
    assert changes[0].after == 100
    assert "60-100 °C" in changes[0].reason
    assert check(clamped) == []


def test_a_three_hundred_second_phase_is_clamped_and_the_profile_still_terminates() -> None:
    """300 s is the firmware's own per-phase cap; 120 is the policy's.

    The clamp brings it inside the ceiling, which also satisfies the termination
    rule — a phase bounded by the clock ends whether or not anything else stops
    it.
    """
    document = profile_fixture("firmware-9bar")
    document["phases"][0]["duration"] = 300
    clamped, changes = clamp(Profile.model_validate(document))
    assert clamped.phases[0].duration == DEFAULT_BOUNDS.phase_duration_max_s
    assert [change.field for change in changes] == ["duration"]
    assert check(clamped) == []


def test_a_long_phase_with_nothing_to_stop_it_is_a_violation_before_the_clamp() -> None:
    """The failure the termination rule exists for, caught on the unclamped form.

    A 300 s phase whose last brew phase has no volumetric or pumped target is
    300 s of nine bar into a full basket. `check` says so; the clamp then fixes
    it by bounding the duration, which is why `enforce` accepts it in the end —
    but the violation is real on the document as written.
    """
    document = profile_fixture("firmware-9bar")
    document["phases"][0]["duration"] = 300
    document["phases"][0]["targets"] = []
    violations = check(Profile.model_validate(document))
    assert [violation.field for violation in violations] == ["duration", "targets"]
    assert "nothing ends this profile" in violations[1].message


def test_pressure_and_flow_are_clamped_to_the_pump_s_own_limits() -> None:
    document = profile_fixture("firmware-adapt")
    document["phases"][4]["pump"] = {"target": "pressure", "pressure": 12, "flow": 14}
    clamped, changes = clamp(Profile.model_validate(document))
    assert [change.path for change in changes] == ["phases[4].pump.flow"]
    pump = clamped.phases[4].pump
    assert not isinstance(pump, int)
    assert pump.flow == DEFAULT_BOUNDS.flow_max_ml_s
    assert pump.pressure == 12


def test_the_hold_measured_sentinel_survives_the_clamp() -> None:
    """`-1` means "hold whatever was measured at phase entry", not "minus one bar".

    Clamping it to 0 would turn a hold into "no limit", which is a different
    profile that runs a different shot. `firmware-adapt` uses it, so this is a
    regression test for a real document.
    """
    profile = Profile.model_validate(profile_fixture("firmware-adapt"))
    clamped, changes = clamp(profile)
    pump = clamped.phases[5].pump
    assert not isinstance(pump, int)
    assert pump.flow == -1.0
    assert changes == []


def test_a_transition_longer_than_its_phase_is_clamped_to_the_phase() -> None:
    """The firmware clamps this itself, silently. Doing it here makes it visible."""
    document = profile_fixture("firmware-lever")
    document["phases"][3]["transition"]["duration"] = 40
    clamped, changes = clamp(Profile.model_validate(document))
    assert clamped.phases[3].transition is not None
    assert clamped.phases[3].transition.duration == clamped.phases[3].duration
    assert changes[0].reason.startswith("a transition cannot be longer")


def test_eleven_phases_is_refused_rather_than_trimmed() -> None:
    """The one thing a clamp must not do.

    Dropping the eleventh phase would produce a compliant profile that brews
    something else, and hand it to somebody as "made safe". Refusing names the
    number and tells them to remove a phase themselves.
    """
    document = profile_fixture("firmware-flush")
    document["phases"].append(copy.deepcopy(document["phases"][0]))
    document["phases"].append(copy.deepcopy(document["phases"][0]))
    profile = Profile.model_validate(document)
    with pytest.raises(ProfileRejected) as caught:
        enforce(profile)
    assert len(profile.phases) == 11
    assert "11 phases" in caught.value.violations[0].message
    assert "Remove phases yourself" in caught.value.violations[0].message


def test_a_phase_temperature_of_zero_is_left_alone() -> None:
    """0 is "inherit the profile temperature", not "freezing".

    Clamping it to 60 would turn "whatever the profile says" into a hard 60 °C
    on that one phase, which is a different profile and a ruined shot.
    """
    document = profile_fixture("firmware-9bar")
    assert document["phases"][0]["temperature"] == 0
    clamped, changes = clamp(Profile.model_validate(document))
    assert clamped.phases[0].temperature == 0
    assert changes == []


def test_enforce_returns_the_clamped_profile_and_the_list_of_what_moved() -> None:
    document = profile_fixture("firmware-9bar")
    document["temperature"] = 140
    document["phases"][0]["duration"] = 200
    profile, changes = enforce(Profile.model_validate(document))
    assert profile.temperature == 100
    assert profile.phases[0].duration == 120
    assert {change.field for change in changes} == {"temperature", "duration"}


# ── the bounds are settings ──────────────────────────────────────────


def test_every_policy_key_is_in_the_settings_registry() -> None:
    """The list this module reads and the list the Settings page writes are one.

    A key here that the registry does not declare resolves to nothing and the
    bound silently reverts to its default, which is the worst kind of safety
    setting: one that looks configured and is not.
    """
    for key in POLICY_SETTING_KEYS:
        assert key in SETTINGS_REGISTRY, key


def test_bounds_come_from_settings_and_fall_back_per_key() -> None:
    bounds = bounds_from({"profilePolicyTemperatureMaxC": 96.0, "profilePolicyMaxPhases": 4})
    assert bounds.temperature_max_c == 96.0
    assert bounds.max_phases == 4
    # Untouched keys keep the shipped default rather than becoming None.
    assert bounds.temperature_min_c == DEFAULT_BOUNDS.temperature_min_c


def test_an_unusable_bound_takes_the_default_rather_than_failing() -> None:
    """A typo in one bound must not be the thing that stops the net existing."""
    bounds = bounds_from({"profilePolicyPressureMaxBar": "nine bar"})
    assert bounds.pressure_max_bar == DEFAULT_BOUNDS.pressure_max_bar


def test_a_tightened_bound_clamps_a_profile_that_passed_the_default() -> None:
    """crema's own ceiling is 96 °C. Somebody who prefers it gets it."""
    profile = Profile.model_validate(profile_fixture("firmware-9bar"))
    assert check(profile) == []
    tighter = PolicyBounds(temperature_max_c=90.0)
    clamped, changes = clamp(profile, tighter)
    assert clamped.temperature == 90.0
    assert changes[0].after == 90.0
    assert check(clamped, tighter) == []


def test_inverted_bounds_are_reported_rather_than_silently_emptying_the_range() -> None:
    """Somebody types the minimum into the maximum field. Say so; clamp nothing."""
    inverted = PolicyBounds(temperature_min_c=100.0, temperature_max_c=60.0)
    profile = Profile.model_validate(profile_fixture("firmware-9bar"))
    clamped, changes = clamp(profile, inverted)
    assert changes == []
    assert clamped.temperature == profile.temperature
    messages = [violation.message for violation in check(clamped, inverted)]
    assert any("inverted" in message for message in messages)
