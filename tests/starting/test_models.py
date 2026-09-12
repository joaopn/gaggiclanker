"""The output contract: what a model may answer, and what is rejected.

Every rejection here costs one corrective turn (`llm/service.py`) rather than a
wrong row, which is the trade a strict schema buys.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.profile_policy import check
from gaggiclanker.starting.models import OPTION_KEYS, StartingPointResult
from tests.starting.conftest import (
    GOOD_OUTPUT,
    GOOD_PROFILE,
    REFUSED_PROFILE,
    UNTERMINATED_PROFILE,
)


def _output(**changes: object) -> dict[str, object]:
    return {**json.loads(json.dumps(GOOD_OUTPUT)), **changes}


def test_the_good_output_validates_and_is_lookupable_by_key() -> None:
    result = StartingPointResult.model_validate(GOOD_OUTPUT)
    assert [option.option for option in result.options] == list(OPTION_KEYS)
    assert result.option("recommended") is not None
    assert result.option("nonsense") is None


def test_an_unknown_option_key_is_rejected() -> None:
    """A fourth kind of option would render as a card nobody sees."""
    broken = _output()
    broken["options"][2]["option"] = "balanced"  # type: ignore[index]
    with pytest.raises(ValidationError):
        StartingPointResult.model_validate(broken)


def test_three_of_the_same_option_is_rejected() -> None:
    """`min_length=3` alone would accept it and lose two thirds of the answer."""
    broken = _output()
    for option in broken["options"]:  # type: ignore[attr-defined]
        option["option"] = "recommended"
    with pytest.raises(ValidationError, match="exactly one of each"):
        StartingPointResult.model_validate(broken)


def test_two_options_is_rejected() -> None:
    broken = _output()
    broken["options"] = broken["options"][:2]  # type: ignore[index]
    with pytest.raises(ValidationError):
        StartingPointResult.model_validate(broken)


def test_an_option_may_carry_a_whole_profile_and_it_is_a_real_profile() -> None:
    """The document is validated by `Profile` itself, not by a lookalike."""
    good = _output()
    good["options"][1]["profile"] = GOOD_PROFILE  # type: ignore[index]
    result = StartingPointResult.model_validate(good)
    profile = result.option("recommended")
    assert profile is not None and profile.profile is not None
    assert profile.profile.label == "Kenya light 1:2.5"
    # ... and the safety policy is what decides whether it may be written.
    assert check(profile.profile) == []


def test_a_profile_the_policy_refuses_fails_at_propose_time() -> None:
    """Eleven phases, and the policy allows ten.

    Checked *here* rather than only when somebody accepts the option, because
    the alternative is an `ok` run whose card cannot be taken. Failing now costs
    the corrective turn the LLM layer already budgets for; failing at accept
    time costs the user a call and tells them nothing until they press a button.
    """
    unsafe = _output()
    unsafe["options"][1]["profile"] = REFUSED_PROFILE  # type: ignore[index]
    with pytest.raises(ValidationError, match="safety policy"):
        StartingPointResult.model_validate(unsafe)
    # ... and it really is the policy talking, not the schema.
    assert check(Profile.model_validate(REFUSED_PROFILE))


def test_a_profile_that_never_terminates_is_rejected() -> None:
    """The rule this feature adds to the shared policy's verdict.

    `profile_policy` clamps every duration inside its ceiling first, so to it a
    profile with no stop anywhere merely "stops on time" — the right answer for
    a backflush. The prompt asks for a stop near the yield and an option without
    one floods the cup, so the rule lives on the option rather than in the
    policy, where it would start refusing utility profiles written years ago.
    """
    unsafe = _output()
    unsafe["options"][1]["profile"] = UNTERMINATED_PROFILE  # type: ignore[index]
    with pytest.raises(ValidationError, match="volumetric or pumped stop"):
        StartingPointResult.model_validate(unsafe)
    # The shared policy is content with it, which is the point of the split.
    assert check(Profile.model_validate(UNTERMINATED_PROFILE)) == []


def test_a_number_the_clamp_would_fix_is_not_rejected() -> None:
    """`clamp` then `check`, which is the draft service's order.

    Rejecting a 101 °C that the clamp would have moved to 100 would cost a
    corrective turn — and a second paid call — over a field the accept path was
    going to fix anyway.
    """
    warm = _output()
    warm["options"][1]["profile"] = {**GOOD_PROFILE, "temperature": 101.0}  # type: ignore[index]
    result = StartingPointResult.model_validate(warm)
    option = result.option("recommended")
    assert option is not None and option.profile is not None
    # Stored as answered; the clamp happens on the way into the draft.
    assert option.profile.temperature == 101.0


def test_a_profile_document_that_is_not_a_profile_fails_validation() -> None:
    broken = _output()
    broken["options"][1]["profile"] = {"label": "No phases", "type": "pro", "phases": []}  # type: ignore[index]
    with pytest.raises(ValidationError):
        StartingPointResult.model_validate(broken)


def test_an_option_may_not_name_a_profile_and_carry_one() -> None:
    """Both set is ambiguous, not richer: the accept path would have to pick."""
    broken = _output()
    broken["options"][1]["profile"] = GOOD_PROFILE  # type: ignore[index]
    broken["options"][1]["profile_version_id"] = 7  # type: ignore[index]
    with pytest.raises(ValidationError, match="not both"):
        StartingPointResult.model_validate(broken)


def test_an_invented_key_is_rejected_rather_than_stored() -> None:
    broken = _output()
    broken["options"][1]["brew_ratio"] = 2.5  # type: ignore[index]
    with pytest.raises(ValidationError):
        StartingPointResult.model_validate(broken)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dose_g", 0),
        ("dose_g", 200),
        ("yield_g", -1),
        ("ratio", 0),
        ("temperature_c", 40),
        ("temperature_c", 130),
        ("grind_setting", ""),
    ],
)
def test_numbers_outside_the_plausible_range_are_rejected(field: str, value: object) -> None:
    """A starting point that scalds the puck is not a starting point."""
    broken = _output()
    broken["options"][1][field] = value  # type: ignore[index]
    with pytest.raises(ValidationError):
        StartingPointResult.model_validate(broken)


def test_a_stored_option_can_be_read_back(tmp_path: object) -> None:
    """The accept path re-validates a run's output days after it was stored.

    `Profile` refuses an `annotations` key on the way in, and a plain
    `model_dump` emits one — so without the serializer the profile an option
    carried could be written and never read.
    """
    good = _output()
    good["options"][1]["profile"] = GOOD_PROFILE  # type: ignore[index]
    stored = StartingPointResult.model_validate(good).model_dump(mode="json")
    reread = StartingPointResult.model_validate(json.loads(json.dumps(stored)))
    option = reread.option("recommended")
    assert option is not None and option.profile is not None
    assert option.profile.label == "Kenya light 1:2.5"
