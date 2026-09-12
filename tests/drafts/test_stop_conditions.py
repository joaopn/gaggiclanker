"""`diff_stop_conditions` — the comparison that decides whether a human has to tick a box.

crema's rule, and the reason it has a test file of its own: a stop condition is
what decides when the machine stops putting water through the puck, which decides
how much coffee is in the cup. Everything else in a profile changes *how* a shot
is pulled; this changes *how much*.

The pair of properties that matter:

* every added, removed and moved target is reported — a missed one is an
  acknowledgement nobody was asked for;
* a target that merely round-tripped through JSON is **not** reported. `9` and
  `9.0` are the same stop condition, and a diff that flagged them would train
  people to tick the box without reading it, which is worse than not asking.
"""

from __future__ import annotations

from typing import Any

from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.profile_policy import diff_stop_conditions
from tests.drafts.conftest import profile_fixture


def base() -> Profile:
    return Profile.model_validate(profile_fixture("firmware-9bar"))


def edited(**phase_changes: Any) -> Profile:
    document = profile_fixture("firmware-9bar")
    document["phases"][0].update(phase_changes)
    return Profile.model_validate(document)


def test_an_unchanged_profile_has_no_stop_condition_changes() -> None:
    assert diff_stop_conditions(base(), base()) == []


def test_nine_and_nine_point_zero_are_the_same_stop_condition() -> None:
    """The one that must not fire.

    A draft that came back from a model as `36.0` where the machine had `36` is
    the same profile. Flagging it would put an acknowledgement checkbox in front
    of somebody on every single draft, and a checkbox that is always there is a
    checkbox nobody reads.
    """
    after = edited(targets=[{"type": "volumetric", "operator": "gte", "value": 36.0}])
    assert diff_stop_conditions(base(), after) == []


def test_a_changed_value_is_reported_with_both_sides() -> None:
    after = edited(targets=[{"type": "volumetric", "operator": "gte", "value": 40}])
    changes = diff_stop_conditions(base(), after)
    assert len(changes) == 1
    change = changes[0]
    assert change.kind == "changed"
    assert change.target_type == "volumetric"
    assert change.phase_index == 0
    assert change.phase_name == "Pump"
    assert change.before is not None
    assert change.after is not None
    assert (change.before.value, change.after.value) == (36.0, 40.0)


def test_a_flipped_operator_is_a_change_even_at_the_same_value() -> None:
    """`gte 36` and `lte 36` are opposite instructions at the same number.

    The firmware reads any unrecognised spelling as `lte`, so an operator flip
    is exactly the silent inversion this whole feature is built to prevent, and
    a diff keyed only on the value would miss it.
    """
    after = edited(targets=[{"type": "volumetric", "operator": "lte", "value": 36}])
    changes = diff_stop_conditions(base(), after)
    assert [change.kind for change in changes] == ["changed"]
    assert changes[0].before is not None
    assert changes[0].after is not None
    assert (changes[0].before.operator, changes[0].after.operator) == ("gte", "lte")


def test_a_removed_target_is_reported() -> None:
    """The most dangerous edit in the set: the shot now runs to the clock."""
    changes = diff_stop_conditions(base(), edited(targets=[]))
    assert [change.kind for change in changes] == ["removed"]
    assert changes[0].before is not None
    assert changes[0].after is None


def test_an_added_target_is_reported() -> None:
    after = edited(
        targets=[
            {"type": "volumetric", "operator": "gte", "value": 36},
            {"type": "pressure", "operator": "lte", "value": 4},
        ]
    )
    changes = diff_stop_conditions(base(), after)
    assert [change.kind for change in changes] == ["added"]
    assert changes[0].target_type == "pressure"
    assert changes[0].before is None


def test_targets_are_matched_by_type_rather_than_by_position() -> None:
    """ "The volumetric target moved from 36 to 40" is how a person reads a diff.

    Reordering the list is not a change, and a positional diff would report two
    changes for a profile that brews identically.
    """
    before_doc = profile_fixture("firmware-adapt")
    after_doc = profile_fixture("firmware-adapt")
    after_doc["phases"][4]["targets"].reverse()
    changes = diff_stop_conditions(
        Profile.model_validate(before_doc), Profile.model_validate(after_doc)
    )
    assert changes == []


def test_a_phase_that_gained_targets_is_reported_against_its_own_index() -> None:
    document = profile_fixture("docs-medium-18g")
    after = profile_fixture("docs-medium-18g")
    after["phases"][4]["targets"] = [{"type": "pumped", "operator": "gte", "value": 60}]
    changes = diff_stop_conditions(Profile.model_validate(document), Profile.model_validate(after))
    assert [(change.phase_index, change.kind) for change in changes] == [(4, "added")]
    assert changes[0].phase_name == "Hammer"


def test_a_deleted_phase_takes_its_stop_conditions_with_it() -> None:
    """A shorter profile is not a free pass.

    Removing the last phase removes its volumetric target, and that is a yield
    change whether or not anybody edited a number.
    """
    document = profile_fixture("docs-cremina-lever")
    after = profile_fixture("docs-cremina-lever")
    after["phases"] = after["phases"][:-1]
    changes = diff_stop_conditions(Profile.model_validate(document), Profile.model_validate(after))
    assert [change.kind for change in changes] == ["removed"]
    assert changes[0].phase_index == 7
