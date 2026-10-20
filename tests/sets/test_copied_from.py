"""A Set's "copied from" note: written with the Set, served by name, gone with its source."""

from __future__ import annotations

from gaggiclanker.db.repos.sets import SetVersionWrite, SetWrite
from tests.sets.conftest import Fixtures


async def _two_sets(wired: Fixtures) -> tuple[int, int]:
    """A source Set and a Set copied from its first version: (source version id, copy id)."""
    source = await wired.sets.create(
        SetWrite(name="Ethiopia Guji", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        SetVersionWrite(dose_g=18),
    )
    version = await wired.sets.current_version(source.id)
    assert version is not None
    async with wired.db.transaction():
        copy_id = await wired.sets.create_in_transaction(
            SetWrite(name="Guji milk", bean_id=wired.bean_id),
            SetVersionWrite(dose_g=18),
            copied_from_version_id=version.id,
        )
    return version.id, copy_id


async def test_the_note_names_the_source_set_and_version(wired: Fixtures) -> None:
    version_id, copy_id = await _two_sets(wired)

    copy = await wired.sets.get(copy_id)

    assert copy is not None and copy.copied_from is not None
    assert copy.copied_from.model_dump() == {
        "set_id": copy.copied_from.set_id,
        "set_name": "Ethiopia Guji",
        "version_id": version_id,
        "version_label": "v1",
    }
    assert [row.copied_from is not None for row in await wired.sets.list_sets()] == [True, False]


async def test_a_set_made_any_other_way_has_no_note(wired: Fixtures) -> None:
    made = await wired.sets.create(
        SetWrite(name="Plain", bean_id=wired.bean_id), SetVersionWrite(dose_g=18)
    )

    assert made.copied_from is None


async def test_the_note_is_gone_with_its_source_version(wired: Fixtures) -> None:
    version_id, copy_id = await _two_sets(wired)
    # A designed Set that was discarded takes its versions with it; the copy is left without a note.
    source_set = (await wired.sets.get_version(version_id)).set_id  # type: ignore[union-attr]
    await wired.db.execute(
        "UPDATE recipes SET current_version_id = NULL WHERE id = ?", (source_set,)
    )
    await wired.db.execute("DELETE FROM recipe_versions WHERE id = ?", (version_id,))

    copy = await wired.sets.get(copy_id)

    assert copy is not None and copy.copied_from is None
