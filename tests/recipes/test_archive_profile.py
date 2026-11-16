"""Archive takes the Recipe's own profile off the machine; Unarchive puts it back.

Archive switches the profile's "On the machine" switch off in the transaction that archives the
Recipe and unpins its pins, through the board's one write; Unarchive switches it on again whether
or not it was on before the Archive. The star is never touched. A profile with no live row (a
Recipe being designed, a deleted row) changes nothing, and neither does a profile that is
already as asked. Tests that something is left alone come in two halves: the same setup with
the guard's condition flipped is shown to change, so the test could fail.
"""

from __future__ import annotations

import pytest

from gaggiclanker.db.repos.pins import PinsRepository
from gaggiclanker.db.repos.profile_board import (
    BoardRow,
    BoardRowPatch,
    ProfileBoardRepository,
)
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.recipes import RecipeVersionPatch, RecipeVersionWrite, RecipeWrite
from tests.recipes.conftest import Fixtures, make_pin, make_profile_version

LONG_AGO = "2026-01-01T00:00:00.000Z"


async def a_recipe(
    wired: Fixtures, label: str, *, on: bool = True, starred: bool = True
) -> tuple[int, int]:
    """A Recipe on a new profile (its row switched ``on`` and starred as asked, last written long
    ago): (recipe id, board id)."""
    version = await make_profile_version(wired.db, label)
    made = await wired.recipes.create(
        RecipeWrite(name=label, bean_id=wired.bean_id),
        RecipeVersionWrite(drink="espresso", profile_version_id=version, dose_g=18.0),
    )
    assert made.board_id is not None
    await ProfileBoardRepository(wired.db).update(
        made.board_id, BoardRowPatch(on_machine=on, on_home_screen=starred)
    )
    await wired.db.execute(
        "UPDATE profile_board SET updated_at = ? WHERE id = ?", (LONG_AGO, made.board_id)
    )
    return made.id, made.board_id


async def row_of(wired: Fixtures, board_id: int) -> BoardRow:
    row = await ProfileBoardRepository(wired.db).get(board_id)
    assert row is not None
    return row


# ── Archive ──────────────────────────────────────────────────────────────


async def test_archive_switches_the_profile_off_and_keeps_its_star(wired: Fixtures) -> None:
    recipe_id, board_id = await a_recipe(wired, "Huila", on=True, starred=True)
    before = await row_of(wired, board_id)
    assert before.on_machine and before.on_home_screen, "the precondition: on and starred"

    archived = await wired.recipes.archive(recipe_id)

    assert archived is not None and archived.archived is True
    after = await row_of(wired, board_id)
    assert after.on_machine is False
    assert after.on_home_screen is True
    assert after.updated_at != LONG_AGO, "written the way the Profiles page's switch writes it"
    # Only the switch changed: the row keeps its version, name and the rest.
    assert after.model_copy(update={"on_machine": True, "updated_at": LONG_AGO}) == before


async def test_archive_leaves_a_profile_that_is_already_off_as_it_was(wired: Fixtures) -> None:
    on_id, on_board = await a_recipe(wired, "Huila", on=True)
    off_id, off_board = await a_recipe(wired, "Alturas", on=False)

    await wired.recipes.archive(on_id)
    await wired.recipes.archive(off_id)

    assert (await row_of(wired, on_board)).updated_at != LONG_AGO, "the guard's other side writes"
    assert (await row_of(wired, off_board)).updated_at == LONG_AGO, "nothing was written"
    assert (await row_of(wired, off_board)).on_machine is False


async def test_archive_leaves_a_deleted_profile_row_alone(wired: Fixtures) -> None:
    live_id, live_board = await a_recipe(wired, "Huila", on=True)
    gone_id, gone_board = await a_recipe(wired, "Alturas", on=True)
    await wired.db.execute(
        "UPDATE profile_board SET deleted_at = ? WHERE id = ?", (LONG_AGO, gone_board)
    )

    await wired.recipes.archive(live_id)
    await wired.recipes.archive(gone_id)

    assert (await row_of(wired, live_board)).on_machine is False, "the same setup, live: switched"
    gone = await row_of(wired, gone_board)
    assert (gone.on_machine, gone.updated_at) == (True, LONG_AGO)


async def test_archiving_a_recipe_with_no_profile_changes_no_profile(wired: Fixtures) -> None:
    _other_id, other_board = await a_recipe(wired, "Huila", on=True)
    designing = await wired.recipes.create(
        RecipeWrite(name="On paper", bean_id=wired.bean_id),
        RecipeVersionWrite(drink="espresso", profile_version_id=None),
    )
    assert designing.board_id is None

    archived = await wired.recipes.archive(designing.id)

    assert archived is not None and archived.archived is True
    assert await wired.recipes.profile_standing(designing.id) is None
    other = await row_of(wired, other_board)
    assert (other.on_machine, other.updated_at) == (True, LONG_AGO)


async def test_archiving_one_recipe_leaves_the_profile_of_another_on(wired: Fixtures) -> None:
    first_id, first_board = await a_recipe(wired, "Huila", on=True)
    _second_id, second_board = await a_recipe(wired, "Alturas", on=True)

    await wired.recipes.archive(first_id)

    assert (await row_of(wired, first_board)).on_machine is False
    assert (await row_of(wired, second_board)).on_machine is True


async def test_archive_switches_the_profile_off_with_the_pins_in_one_transaction(
    wired: Fixtures, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The switch is written in the archive's transaction: when it fails, nothing of the archive
    stays (the Recipe is live, its pin is pinned)."""
    recipe_id, board_id = await a_recipe(wired, "Huila", on=True)
    v1 = (await wired.recipes.current_version(recipe_id)).id  # type: ignore[union-attr]
    pin_id, _copy, _row = await make_pin(wired.db, v1, "Huila v1")
    real = ProfileBoardRepository.update

    async def fail_for_the_recipes_profile(
        self: ProfileBoardRepository, row_id: int, patch: BoardRowPatch
    ) -> BoardRow | None:
        if row_id == board_id:
            raise RuntimeError("the switch's write fails")
        return await real(self, row_id, patch)

    monkeypatch.setattr(ProfileBoardRepository, "update", fail_for_the_recipes_profile)

    with pytest.raises(RuntimeError):
        await wired.recipes.archive(recipe_id)

    row = await wired.recipes.get(recipe_id)
    assert row is not None and row.archived is False
    pin = await PinsRepository(wired.db).get(pin_id)
    assert pin is not None and pin.unpinned_at is None
    assert (await row_of(wired, board_id)).on_machine is True


# ── Unarchive ────────────────────────────────────────────────────────────


async def test_unarchive_switches_the_profile_on_and_says_so(wired: Fixtures) -> None:
    recipe_id, board_id = await a_recipe(wired, "Huila", on=True, starred=False)
    await wired.recipes.archive(recipe_id)

    back = await wired.recipes.unarchive(recipe_id)

    assert back is not None and back.archived is False and back.profile_switched_on is True
    row = await row_of(wired, board_id)
    assert row.on_machine is True
    assert row.on_home_screen is False, "the star is as it was"
    assert row.updated_at != LONG_AGO


async def test_unarchive_switches_the_profile_on_even_when_it_was_off_before_the_archive(
    wired: Fixtures,
) -> None:
    recipe_id, board_id = await a_recipe(wired, "Huila", on=False)
    await wired.recipes.archive(recipe_id)
    assert (await row_of(wired, board_id)).on_machine is False, "nothing was on to take off"

    back = await wired.recipes.unarchive(recipe_id)

    assert back is not None and back.profile_switched_on is True
    assert (await row_of(wired, board_id)).on_machine is True


async def test_unarchive_says_nothing_was_switched_when_the_profile_was_already_on(
    wired: Fixtures,
) -> None:
    recipe_id, board_id = await a_recipe(wired, "Huila", on=True)
    await wired.recipes.archive(recipe_id)
    # Someone switched it back on from the Profiles page while the Recipe was archived.
    await ProfileBoardRepository(wired.db).update(board_id, BoardRowPatch(on_machine=True))

    back = await wired.recipes.unarchive(recipe_id)

    assert back is not None and back.profile_switched_on is False
    assert (await row_of(wired, board_id)).on_machine is True


async def test_unarchive_of_a_recipe_with_no_profile_or_a_deleted_row_switches_nothing(
    wired: Fixtures,
) -> None:
    designing = await wired.recipes.create(
        RecipeWrite(name="On paper", bean_id=wired.bean_id),
        RecipeVersionWrite(drink="espresso", profile_version_id=None),
    )
    gone_id, gone_board = await a_recipe(wired, "Alturas", on=False)
    await wired.recipes.archive(designing.id)
    await wired.recipes.archive(gone_id)
    await wired.db.execute(
        "UPDATE profile_board SET deleted_at = ? WHERE id = ?", (LONG_AGO, gone_board)
    )

    no_profile = await wired.recipes.unarchive(designing.id)
    deleted = await wired.recipes.unarchive(gone_id)

    assert no_profile is not None and no_profile.profile_switched_on is False
    assert deleted is not None and deleted.profile_switched_on is False
    assert (await row_of(wired, gone_board)).on_machine is False


async def test_unarchive_of_a_live_recipe_does_not_touch_the_profile_a_person_switched_off(
    wired: Fixtures,
) -> None:
    archived_id, archived_board = await a_recipe(wired, "Huila", on=False)
    live_id, live_board = await a_recipe(wired, "Alturas", on=False)
    await wired.recipes.archive(archived_id)

    switched = await wired.recipes.unarchive(archived_id)
    untouched = await wired.recipes.unarchive(live_id)

    assert switched is not None and switched.profile_switched_on is True
    assert (await row_of(wired, archived_board)).on_machine is True, "the guard's other side"
    assert untouched is not None and untouched.profile_switched_on is False
    live = await row_of(wired, live_board)
    assert (live.on_machine, live.updated_at) == (False, LONG_AGO)


async def test_unarchive_still_answers_none_for_a_recipe_that_does_not_exist(
    wired: Fixtures,
) -> None:
    assert await wired.recipes.unarchive(9999) is None
    assert await wired.recipes.archive(9999) is None


# ── what Archive would take off ──────────────────────────────────────────


async def test_the_profile_standing_names_what_the_machine_shows_and_where_it_stands(
    wired: Fixtures,
) -> None:
    recipe_id, board_id = await a_recipe(wired, "Huila", on=True)
    await wired.recipes.add_version(recipe_id, RecipeVersionPatch(dose_g=18.5))  # v1.1
    board = ProfileBoardRepository(wired.db)
    row = await row_of(wired, board_id)

    standing = await wired.recipes.profile_standing(recipe_id)

    assert standing is not None
    assert (standing.machine_name, standing.on_machine, standing.selected) == (
        "Huila v1.1",
        True,
        False,
    ), "no file recorded: not selected"

    # The archive's mirror of the machine says the profile's file is the selected one.
    await ProfilesRepository(wired.db).upsert_device_profile(
        device_id="file-1", version_id=row.current_version_id, selected=True, label="Huila v1.1"
    )
    await board.update(
        board_id,
        BoardRowPatch(
            device_profile_id="file-1",
            device_version_id=row.current_version_id,
            device_label="Huila v1.1",
        ),
    )
    selected = await wired.recipes.profile_standing(recipe_id)
    assert selected is not None and selected.selected is True

    # Switched off, a deleted mirror entry or a deleted row: the facts follow.
    await board.update(board_id, BoardRowPatch(on_machine=False))
    off = await wired.recipes.profile_standing(recipe_id)
    assert off is not None and off.on_machine is False
    await wired.db.execute("UPDATE device_profiles SET deleted_at = ?", (LONG_AGO,))
    gone = await wired.recipes.profile_standing(recipe_id)
    assert gone is not None and gone.selected is False
    await wired.db.execute("UPDATE profile_board SET deleted_at = ?", (LONG_AGO,))
    assert await wired.recipes.profile_standing(recipe_id) is None


async def test_the_archived_owner_of_a_profile_is_found_by_profile(wired: Fixtures) -> None:
    live_id, live_board = await a_recipe(wired, "Huila")
    old_id, old_board = await a_recipe(wired, "Alturas")
    await wired.recipes.archive(old_id)

    owners = await wired.recipes.archived_recipes_by_profile()

    assert set(owners) == {old_board}
    assert (owners[old_board].id, owners[old_board].name) == (old_id, "Alturas")
    assert set(await wired.recipes.live_recipes_by_profile()) == {live_board}
    assert live_id
