"""A Recipe's current version is a pointer, and "older" is when a version was made.

A version's name is an identifier, like a tag: nothing about it says what came
first. The Recipe row says which version it is on, `created_at` says what order
they were made in (the row id only breaks a tie inside the database), and these
tests forge the cases where the three disagree.
"""

from __future__ import annotations

from gaggiclanker.db.repos.recipes import RecipeVersionPatch, RecipeVersionWrite, RecipeWrite
from tests.profile_rows import another_version
from tests.recipes.conftest import Fixtures, make_profile_version, make_shot


async def _recipe(wired: Fixtures, **recipe: object) -> int:
    row = await wired.recipes.create(
        RecipeWrite(name="Guji on the Niche", bean_id=wired.bean_id),
        RecipeVersionWrite.model_validate(
            {
                "drink": "espresso",
                "dose_g": 18,
                "target_yield_g": 36,
                "grind_setting": "22",
                **recipe,
            }
        ),
    )
    return row.id


async def test_the_pointer_is_written_by_create_and_moved_by_every_append(
    wired: Fixtures,
) -> None:
    recipe_id = await _recipe(wired)
    first = await wired.recipes.current_version(recipe_id)
    assert first is not None and first.is_current
    assert (await wired.recipes.get(recipe_id)).current_version_id == first.id  # type: ignore[union-attr]

    second = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(grind_setting="21"))

    assert second is not None and second.is_current
    assert second.parent_version_id == first.id and second.parent_version_label == "v1"
    row = await wired.recipes.get(recipe_id)
    assert row is not None and row.current_version_id == second.id
    assert row.current_version_label == "v1.1"
    reread = await wired.recipes.get_version(first.id)
    assert reread is not None and not reread.is_current


async def test_the_pointer_not_the_newest_row_decides_what_is_current(wired: Fixtures) -> None:
    """Everything that reads "current" follows the Recipe's pointer, whatever else exists."""
    recipe_id = await _recipe(wired)
    first = await wired.recipes.current_version(recipe_id)
    assert first is not None
    await wired.recipes.add_version(recipe_id, RecipeVersionPatch(grind_setting="21"))
    await wired.db.execute(
        "UPDATE recipes SET current_version_id = ? WHERE id = ?", (first.id, recipe_id)
    )

    current = await wired.recipes.current_version(recipe_id)
    row = await wired.recipes.get(recipe_id)

    assert current is not None and current.id == first.id
    assert row is not None and row.current_version_label == "v1"
    # The next names continue the version the Recipe is on: a minor is v1.2, the
    # next free minor of major 1, and a major is the highest major + 1.
    assert (row.next_minor_label, row.next_major_label) == ("v1.2", "v2")


async def test_automatch_files_a_shot_under_the_version_the_recipe_is_on(wired: Fixtures) -> None:
    profile = await make_profile_version(wired.db, "Older profile")
    other = await another_version(wired.db, profile, description="newer")
    recipe_id = await _recipe(wired, profile_version_id=profile)
    first = await wired.recipes.current_version(recipe_id)
    assert first is not None
    newer = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(profile_version_id=other))
    assert newer is not None
    await wired.db.execute(
        "UPDATE recipes SET current_version_id = ? WHERE id = ?", (first.id, recipe_id)
    )

    shot = await make_shot(wired.db, "000001", profile_version_id=profile)
    match = await wired.recipes.profile_match(
        shot, profile_version_id=profile, device_profile_id=""
    )
    elsewhere = await make_shot(wired.db, "000002", profile_version_id=other)
    unmatched = await wired.recipes.profile_match(
        elsewhere, profile_version_id=other, device_profile_id=""
    )

    assert (match.outcome, match.recipe_version_id) == ("matched", first.id)
    assert unmatched.outcome == "unmatched"


async def test_versions_are_ordered_by_when_they_were_made_not_by_id(wired: Fixtures) -> None:
    recipe_id = await _recipe(wired)
    first = await wired.recipes.current_version(recipe_id)
    assert first is not None
    second = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(grind_setting="21"))
    third = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(grind_setting="20"))
    assert second is not None and third is not None
    # Forge it: the third row was made before the second, whatever its id says.
    await wired.db.execute(
        "UPDATE recipe_versions SET created_at = '2000-01-01T00:00:00.000Z' WHERE id = ?",
        (third.id,),
    )
    await wired.db.execute(
        "UPDATE recipe_versions SET created_at = '1999-01-01T00:00:00.000Z' WHERE id = ?",
        (first.id,),
    )

    versions = await wired.recipes.versions(recipe_id)

    assert [v.id for v in versions] == [second.id, third.id, first.id]
    trends = await wired.recipes.trends(recipe_id)
    assert [v.recipe_version_id for v in trends.versions] == [first.id, third.id, second.id]


async def test_a_prediction_compares_only_to_a_version_made_earlier(wired: Fixtures) -> None:
    recipe_id = await _recipe(wired)
    first = await wired.recipes.current_version(recipe_id)
    assert first is not None
    second = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(grind_setting="21"))
    third = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(grind_setting="20"))
    assert second is not None and third is not None
    await wired.db.execute(
        "UPDATE recipe_versions SET created_at = '2000-01-01T00:00:00.000Z' WHERE id = ?",
        (third.id,),
    )

    # The third row has the higher id and the earlier time: it is older than the second.
    assert await wired.recipes.comparison_target(recipe_id, second.id, third.id) is not None
    assert await wired.recipes.comparison_target(recipe_id, third.id, second.id) is None
    assert await wired.recipes.comparison_target(recipe_id, second.id, second.id) is None


async def test_an_old_roll_back_row_keeps_its_line_and_the_next_version_forks_from_the_current(
    wired: Fixtures,
) -> None:
    """A version written by the first roll back (v3 restores v1.1) is history that stays.

    Raw rows, because nothing writes `restores_version_id` any more: the line walks from the
    pointer by parent and steps over what a roll back stepped over.
    """
    db = wired.db
    recipe_id = await _recipe(wired)
    first = await wired.recipes.current_version(recipe_id)
    assert first is not None
    await db.execute("UPDATE recipe_versions SET created_at = '2026-01-01T00:00:00.000Z'")
    rows = [
        # id, major, minor, parent, restores, created
        (first.id + 1, 1, 1, first.id, None, "2026-01-02T00:00:00.000Z"),
        (first.id + 2, 2, 0, first.id + 1, None, "2026-01-03T00:00:00.000Z"),
        (first.id + 3, 3, 0, first.id + 2, first.id + 1, "2026-01-04T00:00:00.000Z"),
    ]
    for vid, major, minor, parent, restores, created in rows:
        await db.execute(
            "INSERT INTO recipe_versions (id, recipe_id, version_major, version_minor, "
            "parent_version_id, restores_version_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (vid, recipe_id, major, minor, parent, restores, created),
        )
    await db.execute("UPDATE recipes SET current_version_id = ?", (first.id + 3,))

    assert await wired.recipes.dead_end_versions([recipe_id]) == {first.id + 2}
    added = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(grind_setting="19"))
    assert added is not None and added.version_label == "v3.1"
    assert added.parent_version_id == first.id + 3
