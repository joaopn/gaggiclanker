"""A Recipe's "copied from" note: written with the Recipe, served by name, gone with its source."""

from __future__ import annotations

from gaggiclanker.db.repos.recipes import RecipeVersionWrite, RecipeWrite
from tests.recipes.conftest import Fixtures


async def _two_recipes(wired: Fixtures) -> tuple[int, int]:
    """A source Recipe and a Recipe copied from its first version: (source version id, copy id)."""
    source = await wired.recipes.create(
        RecipeWrite(name="Ethiopia Guji", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        RecipeVersionWrite(drink="espresso", dose_g=18),
    )
    version = await wired.recipes.current_version(source.id)
    assert version is not None
    async with wired.db.transaction():
        copy_id = await wired.recipes.create_in_transaction(
            RecipeWrite(name="Guji milk", bean_id=wired.bean_id),
            RecipeVersionWrite(drink="espresso", dose_g=18),
            copied_from_version_id=version.id,
        )
    return version.id, copy_id


async def test_the_note_names_the_source_recipe_and_version(wired: Fixtures) -> None:
    version_id, copy_id = await _two_recipes(wired)

    copy = await wired.recipes.get(copy_id)

    assert copy is not None and copy.copied_from is not None
    assert copy.copied_from.model_dump() == {
        "recipe_id": copy.copied_from.recipe_id,
        "recipe_name": "Ethiopia Guji",
        "version_id": version_id,
        "version_label": "v1",
    }
    assert [row.copied_from is not None for row in await wired.recipes.list_recipes()] == [
        True,
        False,
    ]


async def test_a_recipe_made_any_other_way_has_no_note(wired: Fixtures) -> None:
    made = await wired.recipes.create(
        RecipeWrite(name="Plain", bean_id=wired.bean_id),
        RecipeVersionWrite(drink="espresso", dose_g=18),
    )

    assert made.copied_from is None


async def test_the_note_is_gone_with_its_source_version(wired: Fixtures) -> None:
    version_id, copy_id = await _two_recipes(wired)
    # A designed Recipe that was discarded takes its versions with it; the copy is left without a
    # note.
    source_recipe = (await wired.recipes.get_version(version_id)).recipe_id  # type: ignore[union-attr]
    await wired.db.execute(
        "UPDATE recipes SET current_version_id = NULL WHERE id = ?", (source_recipe,)
    )
    await wired.db.execute("DELETE FROM recipe_versions WHERE id = ?", (version_id,))

    copy = await wired.recipes.get(copy_id)

    assert copy is not None and copy.copied_from is None
