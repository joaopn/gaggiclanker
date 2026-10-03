"""A Set's current version is a pointer, and "older" is when a version was made.

A version's name is an identifier, like a tag: nothing about it says what came
first. The Set row says which version it is on, `created_at` says what order
they were made in (the row id only breaks a tie inside the database), and these
tests forge the cases where the three disagree.
"""

from __future__ import annotations

from gaggiclanker.db.repos.sets import SetVersionPatch, SetVersionWrite, SetWrite
from tests.sets.conftest import Fixtures, make_profile_version, make_shot


async def _set(wired: Fixtures, **recipe: object) -> int:
    row = await wired.sets.create(
        SetWrite(name="Guji on the Niche", bean_id=wired.bean_id),
        SetVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22", **recipe),  # type: ignore[arg-type]
    )
    return row.id


async def test_the_pointer_is_written_by_create_and_moved_by_every_append(
    wired: Fixtures,
) -> None:
    set_id = await _set(wired)
    first = await wired.sets.current_version(set_id)
    assert first is not None and first.is_current
    assert (await wired.sets.get(set_id)).current_version_id == first.id  # type: ignore[union-attr]

    second = await wired.sets.add_version(set_id, SetVersionPatch(grind_setting="21"))

    assert second is not None and second.is_current
    assert second.parent_version_id == first.id and second.parent_version_label == "v1"
    row = await wired.sets.get(set_id)
    assert row is not None and row.current_version_id == second.id
    assert row.current_version_label == "v1.1"
    reread = await wired.sets.get_version(first.id)
    assert reread is not None and not reread.is_current


async def test_the_pointer_not_the_newest_row_decides_what_is_current(wired: Fixtures) -> None:
    """Everything that reads "current" follows the Set's pointer, whatever else exists."""
    set_id = await _set(wired)
    first = await wired.sets.current_version(set_id)
    assert first is not None
    await wired.sets.add_version(set_id, SetVersionPatch(grind_setting="21"))
    await wired.db.execute(
        "UPDATE sets SET current_version_id = ? WHERE id = ?", (first.id, set_id)
    )

    current = await wired.sets.current_version(set_id)
    row = await wired.sets.get(set_id)

    assert current is not None and current.id == first.id
    assert row is not None and row.current_version_label == "v1"
    # The next names continue the version the Set is on: a minor is v1.2, the
    # next free minor of major 1, and a major is the highest major + 1.
    assert (row.next_minor_label, row.next_major_label) == ("v1.2", "v2")


async def test_automatch_files_a_shot_under_the_version_the_set_is_on(wired: Fixtures) -> None:
    profile = await make_profile_version(wired.db, "Older profile")
    other = await make_profile_version(wired.db, "Newer profile")
    set_id = await _set(wired, profile_version_id=profile)
    first = await wired.sets.current_version(set_id)
    assert first is not None
    newer = await wired.sets.add_version(set_id, SetVersionPatch(profile_version_id=other))
    assert newer is not None
    await wired.db.execute(
        "UPDATE sets SET current_version_id = ? WHERE id = ?", (first.id, set_id)
    )

    shot = await make_shot(wired.db, "000001", profile_version_id=profile)
    match = await wired.sets.profile_match(shot, profile_version_id=profile, device_profile_id="")
    elsewhere = await make_shot(wired.db, "000002", profile_version_id=other)
    unmatched = await wired.sets.profile_match(
        elsewhere, profile_version_id=other, device_profile_id=""
    )

    assert (match.outcome, match.set_version_id) == ("matched", first.id)
    assert unmatched.outcome == "unmatched"


async def test_versions_are_ordered_by_when_they_were_made_not_by_id(wired: Fixtures) -> None:
    set_id = await _set(wired)
    first = await wired.sets.current_version(set_id)
    assert first is not None
    second = await wired.sets.add_version(set_id, SetVersionPatch(grind_setting="21"))
    third = await wired.sets.add_version(set_id, SetVersionPatch(grind_setting="20"))
    assert second is not None and third is not None
    # Forge it: the third row was made before the second, whatever its id says.
    await wired.db.execute(
        "UPDATE set_versions SET created_at = '2000-01-01T00:00:00.000Z' WHERE id = ?",
        (third.id,),
    )
    await wired.db.execute(
        "UPDATE set_versions SET created_at = '1999-01-01T00:00:00.000Z' WHERE id = ?",
        (first.id,),
    )

    versions = await wired.sets.versions(set_id)

    assert [v.id for v in versions] == [second.id, third.id, first.id]
    trends = await wired.sets.trends(set_id)
    assert [v.set_version_id for v in trends.versions] == [first.id, third.id, second.id]


async def test_a_prediction_compares_only_to_a_version_made_earlier(wired: Fixtures) -> None:
    set_id = await _set(wired)
    first = await wired.sets.current_version(set_id)
    assert first is not None
    second = await wired.sets.add_version(set_id, SetVersionPatch(grind_setting="21"))
    third = await wired.sets.add_version(set_id, SetVersionPatch(grind_setting="20"))
    assert second is not None and third is not None
    await wired.db.execute(
        "UPDATE set_versions SET created_at = '2000-01-01T00:00:00.000Z' WHERE id = ?",
        (third.id,),
    )

    # The third row has the higher id and the earlier time: it is older than the second.
    assert await wired.sets.comparison_target(set_id, second.id, third.id) is not None
    assert await wired.sets.comparison_target(set_id, third.id, second.id) is None
    assert await wired.sets.comparison_target(set_id, second.id, second.id) is None
