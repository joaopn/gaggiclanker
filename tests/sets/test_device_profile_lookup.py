"""Where a Set's profile is on the machine, and who still stands on a file.

The profile board's write phase asks these questions of the Set history before it replaces or
removes a file, and the board's own tests only reach them through a whole sync. They are pinned
here against the repository directly, one rule each, because each was once a way to delete a
profile a Set was brewing: the file is found by looking back through the Set's versions (a
grind or yield change names no file of its own), a Set is "using" a file by its device id or by
its stored profile, and what a removal cleared is the only thing a restore repoints.
"""

from __future__ import annotations

from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionWrite, SetWrite
from tests.sets.conftest import Fixtures, make_profile_version


async def new_set(
    wired: Fixtures, name: str, profile: int | None, *, device: str | None = None
) -> int:
    """A Set whose first version brews ``profile``, which then sits on the machine as ``device``."""
    row = await wired.sets.create(
        SetWrite(name=name, bean_id=wired.bean_id),
        SetVersionWrite(profile_version_id=profile, dose_g=18, target_yield_g=36),
    )
    if device is not None:
        await wired.db.execute(
            "UPDATE set_versions SET pushed_device_profile_id = ? WHERE set_id = ?",
            (device, row.id),
        )
    return row.id


async def grind_version(sets: SetsRepository, set_id: int, grind: float = 20) -> None:
    await sets.add_version(set_id, SetVersionPatch(grind_value=grind, grind_setting=str(grind)))


async def test_a_grind_change_keeps_naming_the_file_its_profile_is_on(wired: Fixtures) -> None:
    profile = await make_profile_version(wired.db, "Looked back")
    set_id = await new_set(wired, "A", profile, device="X1")

    await grind_version(wired.sets, set_id)

    assert await wired.sets.current_device_profile(set_id) == ("X1", profile)
    current = await wired.sets.current_version(set_id)
    assert current is not None and current.pushed_device_profile_id == "X1", "carried forward"


async def test_the_lookback_finds_the_file_when_older_data_never_carried_the_id(
    wired: Fixtures,
) -> None:
    profile = await make_profile_version(wired.db, "Older data")
    set_id = await new_set(wired, "A", profile, device="X1")
    await grind_version(wired.sets, set_id)
    await wired.db.execute(
        "UPDATE set_versions SET pushed_device_profile_id = NULL "
        "WHERE set_id = ? AND version_major = 1 AND version_minor = 1",
        (set_id,),
    )

    assert await wired.sets.current_device_profile(set_id) == ("X1", profile)


async def test_the_lookback_prefers_the_newest_version_naming_the_profile(
    wired: Fixtures,
) -> None:
    """One stored profile under two device ids (the first copy was deleted and pushed again)."""
    profile = await make_profile_version(wired.db, "Twice")
    set_id = await new_set(wired, "A", profile, device="old")
    await wired.sets.add_version(
        set_id, SetVersionPatch(profile_version_id=profile, pushed_device_profile_id="new")
    )

    assert await wired.sets.current_device_profile(set_id) == ("new", profile)
    assert await wired.sets.device_profile_of(set_id, profile) == "new"


async def test_a_version_that_names_a_different_profile_does_not_inherit_the_file(
    wired: Fixtures,
) -> None:
    mine = await make_profile_version(wired.db, "Mine")
    other = await make_profile_version(wired.db, "Other")
    set_id = await new_set(wired, "A", mine, device="X1")

    await wired.sets.add_version(set_id, SetVersionPatch(profile_version_id=other))

    assert await wired.sets.current_device_profile(set_id) is None
    assert await wired.sets.device_profile_of(set_id, mine) == "X1", "still where it was pushed"


async def test_a_set_uses_a_file_by_device_id_or_by_stored_profile_and_only_by_its_newest(
    wired: Fixtures,
) -> None:
    profile = await make_profile_version(wired.db, "Shared")
    moved_on = await make_profile_version(wired.db, "Moved on to")
    by_id = await new_set(wired, "ByDevice", None, device="X1")
    by_profile = await new_set(wired, "ByProfile", profile)
    left = await new_set(wired, "Left", profile, device="X1")
    await wired.sets.add_version(left, SetVersionPatch(profile_version_id=moved_on))

    using = await wired.sets.sets_currently_using("X1", profile)

    assert using == ["ByDevice", "ByProfile"], "a Set that moved on no longer uses it"
    assert by_id and by_profile
    assert await wired.sets.sets_currently_using("X1", profile, excluding=by_id) == ["ByProfile"]
    assert await wired.sets.sets_currently_using("nothing", None) == []


async def test_clearing_a_file_returns_the_versions_that_named_it_and_restore_repoints_only_them(
    wired: Fixtures,
) -> None:
    profile = await make_profile_version(wired.db, "Cleared")
    mine = await new_set(wired, "Mine", profile, device="X1")
    other = await new_set(wired, "Other", profile)  # same content, never on a file
    mine_versions = [v.id for v in await wired.sets.versions(mine)]
    other_versions = [v.id for v in await wired.sets.versions(other)]

    cleared = await wired.sets.clear_pushed_device_profile("X1")

    assert cleared == mine_versions
    assert await wired.sets.current_device_profile(mine) is None
    assert await wired.sets.clear_pushed_device_profile("X1") == [], "nothing left to clear"
    assert await wired.sets.clear_pushed_device_profile("") == []
    assert await wired.sets.restore_pushed_device_profile(cleared, "X2") == len(cleared)
    assert await wired.sets.current_device_profile(mine) == ("X2", profile)
    assert await wired.sets.current_device_profile(other) is None, "never named a file"
    assert await wired.sets.restore_pushed_device_profile(other_versions, "X3") == len(
        other_versions
    ), "only what is asked for, and only a version that names no file"
    assert await wired.sets.restore_pushed_device_profile(mine_versions, "X4") == 0
    assert await wired.sets.restore_pushed_device_profile([], "X5") == 0
