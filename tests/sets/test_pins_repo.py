"""The pin records, and the one function that says whose signature judges a shot on a copy."""

from __future__ import annotations

import sqlite3

import pytest

from gaggiclanker.db.repos.pins import (
    PinsRepository,
    PinWrite,
    judged_profile_version,
    judged_profile_versions,
)
from gaggiclanker.db.repos.profile_board import BoardRowWrite, ProfileBoardRepository
from gaggiclanker.db.repos.sets import SetVersionPatch, SetVersionWrite, SetWrite
from tests.sets.conftest import Fixtures, make_profile_version, make_shot


async def _pin(
    wired: Fixtures, set_version_id: int, copy_label: str, original: int
) -> tuple[int, int, int]:
    """A copy of ``original`` under ``copy_label``, its row, and the pin: (pin, copy, row)."""
    copy = await make_profile_version(wired.db, copy_label)
    row = await ProfileBoardRepository(wired.db).insert(
        BoardRowWrite(label=copy_label, current_version_id=copy, origin="draft")
    )
    pin = await PinsRepository(wired.db).insert(
        PinWrite(set_version_id=set_version_id, board_id=row.id, profile_version_id=copy)
    )
    return pin.id, copy, row.id


async def _a_set(wired: Fixtures, original: int) -> tuple[int, int]:
    """A Set on ``original`` with v1 and a later v1.1: their ids."""
    made = await wired.sets.create(
        SetWrite(name="Guji", bean_id=wired.bean_id),
        SetVersionWrite(profile_version_id=original, dose_g=18.0),
    )
    first = await wired.sets.current_version(made.id)
    assert first is not None
    second = await wired.sets.add_version(made.id, SetVersionPatch(dose_g=18.5))
    assert second is not None
    return first.id, second.id


async def test_a_pin_names_its_set_version_and_the_copy_is_judged_by_the_original(
    wired: Fixtures,
) -> None:
    original = await make_profile_version(wired.db, "Blooming")
    v1, _v2 = await _a_set(wired, original)
    pin_id, copy, _row = await _pin(wired, v1, "Blooming v1", original)

    pins = PinsRepository(wired.db)
    assert (await pins.live_for_version(v1)) is not None
    assert (await pins.by_copy(copy)) is not None
    assert await judged_profile_version(wired.db, copy) == original
    assert await judged_profile_version(wired.db, original) == original
    # An unknown id maps to itself: nothing to judge it by but itself.
    assert await judged_profile_version(wired.db, 9999) == 9999
    assert await judged_profile_versions(wired.db, [copy, original]) == {
        copy: original,
        original: original,
    }
    # Unpinned, the copy is still the pinned version's: its shots are judged the same way.
    await pins.mark_unpinned(pin_id)
    assert await pins.live_for_version(v1) is None
    assert await judged_profile_version(wired.db, copy) == original


async def test_the_detail_counts_shots_filed_under_the_version_on_the_copy(
    wired: Fixtures,
) -> None:
    original = await make_profile_version(wired.db, "Blooming")
    v1, v2 = await _a_set(wired, original)
    pin_id, copy, _row = await _pin(wired, v1, "Blooming v1", original)
    on_copy = await make_shot(wired.db, "000601", profile_version_id=copy)
    on_original = await make_shot(wired.db, "000602", profile_version_id=original)
    elsewhere = await make_shot(wired.db, "000603", profile_version_id=copy)
    for shot, version in ((on_copy, v1), (on_original, v1), (elsewhere, v2)):
        assert await wired.sets.assign_shot(shot, version)

    detail = await PinsRepository(wired.db).detail(pin_id)

    assert detail is not None
    assert (detail.name, detail.version_label, detail.original_label) == (
        "Blooming v1",
        "v1",
        "Blooming",
    )
    assert detail.original_version_id == original
    assert detail.shot_count == 1


async def test_live_pins_of_a_set_come_newest_first_and_leave_out_unpinned_ones(
    wired: Fixtures,
) -> None:
    original = await make_profile_version(wired.db, "Blooming")
    v1, v2 = await _a_set(wired, original)
    old_pin, _, _ = await _pin(wired, v1, "Blooming v1", original)
    new_pin, _, _ = await _pin(wired, v2, "Blooming v1.1", original)
    pins = PinsRepository(wired.db)
    made = await wired.sets.get((await wired.sets.versions_by_id([v1]))[v1].set_id)
    assert made is not None

    assert [p.id for p in await pins.live_for_set(made.id)] == [new_pin, old_pin]
    await pins.mark_unpinned(old_pin)
    assert [p.id for p in await pins.live_for_set(made.id)] == [new_pin]


async def test_a_copy_belongs_to_one_pin_and_a_version_to_one_live_pin(wired: Fixtures) -> None:
    original = await make_profile_version(wired.db, "Blooming")
    v1, v2 = await _a_set(wired, original)
    _pin_id, copy, row = await _pin(wired, v1, "Blooming v1", original)
    pins = PinsRepository(wired.db)

    with pytest.raises(sqlite3.IntegrityError):
        await pins.insert(PinWrite(set_version_id=v2, board_id=row, profile_version_id=copy))
    other = await make_profile_version(wired.db, "Blooming other")
    with pytest.raises(sqlite3.IntegrityError):
        await pins.insert(PinWrite(set_version_id=v1, board_id=row, profile_version_id=other))
