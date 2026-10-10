"""No Set version names the copy of a pin.

A copy belongs to one Set version for ever, so a second version brewing it would put two Sets'
shots on one frozen profile. Every way a version gets its profile meets the same refusal: the
create and add-a-version routes, a design filling version 1, a proposal when it is made and when
it is accepted ("accepted by create means accept cannot fail"), and the pin itself refuses a
profile version some Set version already brews.
"""

from __future__ import annotations

import pytest

from gaggiclanker.db.repos.pins import PinsRepository, PinWrite
from gaggiclanker.db.repos.profile_board import BoardRowWrite, ProfileBoardRepository
from gaggiclanker.db.repos.sets import (
    DesignBrief,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
    VersionRefused,
)
from tests.sets.conftest import Fixtures, make_pin, make_profile_version
from tests.sets.test_proposals import _propose


async def _a_pinned_set(wired: Fixtures) -> tuple[int, int, int]:
    """A Set on "Blooming" with v1 pinned: (set id, v1, the copy's profile version)."""
    original = await make_profile_version(wired.db, "Blooming")
    made = await wired.sets.create(
        SetWrite(name="Guji", bean_id=wired.bean_id),
        SetVersionWrite(profile_version_id=original, dose_g=18.0),
    )
    v1 = await wired.sets.current_version(made.id)
    assert v1 is not None
    _pin, copy, _row = await make_pin(wired.db, v1.id, "Blooming v1")
    return made.id, v1.id, copy


async def _version_count(wired: Fixtures) -> int:
    return int(await wired.db.fetch_value("SELECT COUNT(*) FROM set_versions"))


async def test_a_new_set_and_a_new_version_cannot_name_a_copy(wired: Fixtures) -> None:
    set_id, _v1, copy = await _a_pinned_set(wired)
    before = await _version_count(wired)
    sets_before = int(await wired.db.fetch_value("SELECT COUNT(*) FROM sets"))

    with pytest.raises(VersionRefused) as refused:
        await wired.sets.create(
            SetWrite(name="Other", bean_id=wired.bean_id),
            SetVersionWrite(profile_version_id=copy),
        )
    assert refused.value.code == "PROFILE_IS_A_PIN"
    with pytest.raises(VersionRefused):
        await wired.sets.add_version(set_id, SetVersionPatch(profile_version_id=copy))

    assert await _version_count(wired) == before
    assert int(await wired.db.fetch_value("SELECT COUNT(*) FROM sets")) == sets_before


async def test_a_design_cannot_fill_version_one_with_a_copy(wired: Fixtures) -> None:
    _set, _v1, copy = await _a_pinned_set(wired)
    designing = await wired.sets.create_design(
        SetWrite(name="Design", bean_id=wired.bean_id), DesignBrief()
    )

    with pytest.raises(VersionRefused):
        await wired.sets.add_version(designing.id, SetVersionPatch(profile_version_id=copy))

    version = await wired.sets.current_version(designing.id)
    assert version is not None and version.profile_version_id is None


async def test_a_proposal_naming_a_copy_is_refused_when_made(wired: Fixtures) -> None:
    set_id, _v1, copy = await _a_pinned_set(wired)

    _, result = await _propose(wired, set_id, patch=SetVersionPatch(profile_version_id=copy))

    assert result.refused == "profile_is_a_pin"  # type: ignore[attr-defined]


async def test_a_proposal_whose_profile_became_a_copy_meanwhile_cannot_be_accepted(
    wired: Fixtures,
) -> None:
    set_id, v1, _copy = await _a_pinned_set(wired)
    meanwhile = await make_profile_version(wired.db, "Blooming other")
    proposals, made = await _propose(
        wired, set_id, patch=SetVersionPatch(profile_version_id=meanwhile)
    )
    proposal = made.proposal  # type: ignore[attr-defined]
    assert proposal is not None
    # The profile the proposal names becomes a pin's copy before the person answers.
    held = await PinsRepository(wired.db).live_for_version(v1)
    assert held is not None
    await PinsRepository(wired.db).mark_unpinned(held.id)
    row = await ProfileBoardRepository(wired.db).insert(
        BoardRowWrite(label="Blooming other", current_version_id=meanwhile, origin="draft")
    )
    await PinsRepository(wired.db).insert(
        PinWrite(set_version_id=v1, board_id=row.id, profile_version_id=meanwhile)
    )
    before = await _version_count(wired)

    result = await proposals.accept(set_id, proposal.id)

    assert result.refused == "profile_is_a_pin"
    assert await _version_count(wired) == before
    waiting = await proposals.get(set_id, proposal.id)
    assert waiting is not None and waiting.status == "proposed"


async def test_the_proposal_guard_does_not_touch_an_ordinary_profile(wired: Fixtures) -> None:
    set_id, _v1, _copy = await _a_pinned_set(wired)
    ordinary = await make_profile_version(wired.db, "9 Bar Espresso")

    proposals, made = await _propose(
        wired, set_id, patch=SetVersionPatch(profile_version_id=ordinary)
    )

    assert made.refused is None  # type: ignore[attr-defined]
    assert made.proposal is not None  # type: ignore[attr-defined]
    assert proposals is not None
