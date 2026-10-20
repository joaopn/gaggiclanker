"""A Recipe has one profile and a profile has at most one Recipe.

``recipes.board_id`` is the Recipe's profile, unique. No constraint can say that every version of a
Recipe names a version its profile owns, so the one place a Recipe version is written checks it.
"""

from __future__ import annotations

import sqlite3

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import BoardRowWrite, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.recipes import (
    DesignBrief,
    RecipesRepository,
    RecipeVersionPatch,
    RecipeVersionWrite,
    RecipeWrite,
    VersionRefused,
    version_changes,
)
from tests.drafts.helpers import assert_recipes_follow_their_profiles, assert_versions_owned
from tests.profile_rows import another_version
from tests.recipes.conftest import Fixtures, make_profile_version, make_shot


async def profile(fx: Fixtures, label: str) -> tuple[int, int]:
    """A profile (a board row) and the version it has active."""
    version = await make_profile_version(fx.db, label)
    row = await ProfileBoardRepository(fx.db).insert(
        BoardRowWrite(label=label, current_version_id=version, origin="adopted")
    )
    return row.id, row.current_version_id


async def new_recipe(fx: Fixtures, name: str, version: int | None):  # type: ignore[no-untyped-def]
    return await fx.recipes.create(
        RecipeWrite(name=name, bean_id=fx.bean_id), RecipeVersionWrite(profile_version_id=version)
    )


async def test_a_new_recipe_takes_the_profile_its_first_version_brews(wired: Fixtures) -> None:
    board, version = await profile(wired, "Bloom")

    made = await new_recipe(wired, "Guji", version)

    assert made.board_id == board and made.profile_label == "Bloom"
    assert await wired.recipes.recipe_of_profile(board) == (made.id, "Guji")


async def test_a_profile_that_has_a_recipe_is_refused_with_the_way_forward(wired: Fixtures) -> None:
    board, version = await profile(wired, "Bloom")
    await new_recipe(wired, "Guji", version)

    with pytest.raises(VersionRefused) as refused:
        await new_recipe(wired, "Kenya", version)

    assert refused.value.refused == "profile_has_a_recipe"
    assert refused.value.message == (
        "Bloom belongs to Guji. Duplicate Guji to start a new one on a copy."
    )
    assert len(await wired.recipes.list_recipes()) == 1, "nothing was written"
    assert board


async def test_the_index_is_the_backstop_for_two_recipes_on_one_profile(wired: Fixtures) -> None:
    board, version = await profile(wired, "Bloom")
    first = await new_recipe(wired, "Guji", version)
    other = await new_recipe(wired, "Kenya", None)

    with pytest.raises(sqlite3.IntegrityError):
        await wired.db.execute("UPDATE recipes SET board_id = ? WHERE id = ?", (board, other.id))
    assert first.board_id == board


async def test_a_recipe_may_have_no_profile_while_it_has_never_named_one(wired: Fixtures) -> None:
    bare = await new_recipe(wired, "Bare", None)

    assert bare.board_id is None


async def test_a_new_recipe_starts_on_the_active_version_of_a_profile_with_no_recipe(
    wired: Fixtures,
) -> None:
    board, version = await profile(wired, "Bloom")
    older = await another_version(wired.db, version, description="older")
    await wired.db.execute(
        "UPDATE profile_board SET current_version_id = ? WHERE id = ?", (older, board)
    )
    loose = await make_profile_version(wired.db, "Imported", owned=False)

    for named in (version, loose):
        with pytest.raises(VersionRefused) as refused:
            await new_recipe(wired, "Refused", named)
        assert refused.value.refused == "not_the_active_version"

    started = await new_recipe(wired, "Started", older)
    assert started.board_id == board


async def test_only_the_starting_point_starts_a_recipe_on_a_loose_version(wired: Fixtures) -> None:
    loose = await make_profile_version(wired.db, "Drafted", owned=False)

    made = await wired.recipes.create(
        RecipeWrite(name="From a draft", bean_id=wired.bean_id),
        RecipeVersionWrite(profile_version_id=loose, dose_g=18.0),
        from_a_draft=True,
    )
    assert made.board_id is None
    with pytest.raises(VersionRefused):
        await wired.recipes.create(
            RecipeWrite(name="Second", bean_id=wired.bean_id),
            RecipeVersionWrite(profile_version_id=loose, dose_g=18.0),
            from_a_draft=True,
        )


async def test_every_version_of_a_recipe_names_a_version_its_profile_owns(wired: Fixtures) -> None:
    board, version = await profile(wired, "Bloom")
    other_board, other_version = await profile(wired, "Other")
    made = await new_recipe(wired, "Guji", version)
    mine = await ProfileBoardRepository(wired.db).get(board)
    assert mine is not None

    for foreign in (other_version, await make_profile_version(wired.db, "Nobody's")):
        with pytest.raises(VersionRefused) as refused:
            await wired.recipes.add_version(
                made.id, RecipeVersionPatch(profile_version_id=foreign, dose_g=19)
            )
        assert refused.value.refused == "other_profile"
    assert len(await wired.recipes.versions(made.id)) == 1, "nothing was written"
    # Its own profile's version is fine, and so is leaving the profile alone.
    own = await ProfileBoardRepository(wired.db).version_for(
        board, await make_profile_version(wired.db, "Bloom 2")
    )
    added = await wired.recipes.add_version(made.id, RecipeVersionPatch(profile_version_id=own))
    kept = await wired.recipes.add_version(made.id, RecipeVersionPatch(dose_g=19))
    assert added is not None and kept is not None
    assert kept.profile_version_id == own
    assert other_board


async def test_a_recipe_with_no_profile_gets_one_at_its_next_version(wired: Fixtures) -> None:
    board, version = await profile(wired, "Bloom")
    bare = await new_recipe(wired, "Bare", None)

    added = await wired.recipes.add_version(bare.id, RecipeVersionPatch(profile_version_id=version))

    assert added is not None and added.profile_version_id == version
    stored = await wired.recipes.get(bare.id)
    assert stored is not None and stored.board_id == board


async def test_that_profile_is_the_one_a_new_recipe_may_take(wired: Fixtures) -> None:
    taken_board, taken = await profile(wired, "Taken")
    await new_recipe(wired, "Holder", taken)
    older_board, older = await profile(wired, "Moved on")
    await wired.db.execute(
        "UPDATE profile_board SET current_version_id = ? WHERE id = ?",
        (await another_version(wired.db, older, description="newer"), older_board),
    )
    bare = await new_recipe(wired, "Bare", None)

    for refused_version, why in (
        (taken, "profile_has_a_recipe"),
        (older, "not_the_active_version"),
    ):
        with pytest.raises(VersionRefused) as refused:
            await wired.recipes.profile_to_start_on(refused_version)
        assert refused.value.refused == why
    stored = await wired.recipes.get(bare.id)
    assert stored is not None and stored.board_id is None and taken_board


async def test_a_recipe_on_a_loose_version_is_given_a_profile_that_takes_the_version_over(
    wired: Fixtures,
) -> None:
    board, version = await profile(wired, "Bloom")
    loose = await make_profile_version(wired.db, "Imported", owned=False)
    older = await wired.recipes.create(
        RecipeWrite(name="Old", bean_id=wired.bean_id),
        RecipeVersionWrite(profile_version_id=loose),
        from_a_draft=True,
    )

    added = await wired.recipes.add_version(
        older.id, RecipeVersionPatch(profile_version_id=version)
    )

    assert added is not None
    stored = await wired.recipes.get(older.id)
    assert stored is not None and stored.board_id == board
    owners = {
        await ProfilesRepository(wired.db).owner_of(v.profile_version_id or 0)
        for v in await wired.recipes.versions(older.id)
    }
    assert owners == {board}, "every version of the Recipe is that profile's now"


async def test_a_recipe_on_two_loose_versions_is_given_a_profile_that_takes_both(
    wired: Fixtures,
) -> None:
    board, version = await profile(wired, "Bloom")
    first = await make_profile_version(wired.db, "First file", owned=False)
    second = await make_profile_version(wired.db, "Second file", owned=False)
    made = await wired.recipes.create(
        RecipeWrite(name="Old", bean_id=wired.bean_id),
        RecipeVersionWrite(profile_version_id=first),
        from_a_draft=True,
    )
    await wired.recipes.add_version(made.id, RecipeVersionPatch(profile_version_id=second))

    await wired.recipes.add_version(made.id, RecipeVersionPatch(profile_version_id=version))

    stored = await wired.recipes.get(made.id)
    assert stored is not None and stored.board_id == board
    for v in await wired.recipes.versions(made.id):
        assert await ProfilesRepository(wired.db).owner_of(v.profile_version_id or 0) == board


async def test_a_mirrored_file_no_profile_stands_on_is_something_a_recipe_may_start_on(
    wired: Fixtures,
) -> None:
    """With Writes off the list is never adopted, so the files the sync mirrored are what a
    person has. The Recipe has no profile until the list takes the file over, and then it has."""
    file_version = await make_profile_version(wired.db, "On the machine", owned=False)
    await ProfilesRepository(wired.db).upsert_device_profile(
        device_id="abc123", version_id=file_version
    )
    assert (file_version, "On the machine") in await wired.recipes.profile_choices()

    made = await new_recipe(wired, "Writes off", file_version)

    assert made.board_id is None
    assert all(c[0] != file_version for c in await wired.recipes.profile_choices()), "now named"
    row = await ProfileBoardRepository(wired.db).insert(
        BoardRowWrite(
            label="On the machine",
            current_version_id=file_version,
            origin="adopted",
            device_profile_id="abc123",
        )
    )
    stored = await wired.recipes.get(made.id)
    assert stored is not None and stored.board_id == row.id, "the adopting list gave it the profile"
    with pytest.raises(VersionRefused):
        await new_recipe(wired, "Second", file_version)


async def test_a_file_a_profile_stands_on_or_a_recipe_names_is_not_offered(wired: Fixtures) -> None:
    stood = await make_profile_version(wired.db, "Stood on", owned=False)
    await ProfilesRepository(wired.db).upsert_device_profile(device_id="one", version_id=stood)
    await ProfileBoardRepository(wired.db).insert(
        BoardRowWrite(
            label="Stood on", current_version_id=stood, origin="adopted", device_profile_id="one"
        )
    )
    deleted = await make_profile_version(wired.db, "Deleted", owned=False)
    await ProfilesRepository(wired.db).upsert_device_profile(device_id="two", version_id=deleted)
    await wired.db.execute(
        "UPDATE device_profiles SET deleted_at = '2026-10-13' WHERE device_id='two'"
    )

    choices = await wired.recipes.profile_choices()

    assert choices == [(stood, "Stood on")], "the profile once, not its file again"
    with pytest.raises(VersionRefused):
        await new_recipe(wired, "On a deleted file", deleted)


async def test_a_design_filled_from_a_card_whose_version_the_profile_moved_on_from_follows(
    wired: Fixtures,
) -> None:
    board, first = await profile(wired, "Designed")
    later = await another_version(wired.db, first, description="later")
    await wired.db.execute(
        "UPDATE profile_board SET current_version_id = ? WHERE id = ?", (later, board)
    )
    designing = await wired.recipes.create_design(
        RecipeWrite(name="Design", bean_id=wired.bean_id), DesignBrief()
    )

    await wired.recipes.add_version(designing.id, RecipeVersionPatch(profile_version_id=first))

    versions = await wired.recipes.versions(designing.id)
    assert [v.version_label for v in reversed(versions)] == ["v1", "v1.1"]
    current = await wired.recipes.current_version(designing.id)
    assert current is not None and current.profile_version_id == later


async def test_following_a_profile_is_the_function_that_adds_its_recipes_version(
    wired: Fixtures,
) -> None:
    board, version = await profile(wired, "Bloom")
    made = await new_recipe(wired, "Guji", version)
    newer = await ProfileBoardRepository(wired.db).version_for(
        board, await make_profile_version(wired.db, "Bloom", temperature=95)
    )

    followed = await wired.recipes.follow_profile(board, newer)
    again = await wired.recipes.follow_profile(board, newer)
    nobody = await wired.recipes.follow_profile(await profile_without_recipe(wired), newer)

    assert followed is not None and followed.profile_version_id == newer
    assert followed.version_label == "v1.1" and followed.recipe_id == made.id
    assert again is None and nobody is None, "already there, and a profile with no Recipe"


async def profile_without_recipe(fx: Fixtures) -> int:
    return (await profile(fx, "Alone"))[0]


async def test_a_recipe_being_designed_gets_its_profile_with_its_first_recipe(
    wired: Fixtures,
) -> None:
    board, version = await profile(wired, "Designed")
    designing = await wired.recipes.create_design(
        RecipeWrite(name="Design", bean_id=wired.bean_id), DesignBrief()
    )
    assert designing.board_id is None

    filled = await wired.recipes.add_version(
        designing.id, RecipeVersionPatch(profile_version_id=version)
    )

    assert filled is not None and filled.version_label == "v1"
    after = await wired.recipes.get(designing.id)
    assert after is not None and after.board_id == board and after.designing is False


async def test_a_design_cannot_take_a_profile_that_has_a_recipe(wired: Fixtures) -> None:
    _board, version = await profile(wired, "Taken")
    await new_recipe(wired, "Owner", version)
    designing = await wired.recipes.create_design(
        RecipeWrite(name="Design", bean_id=wired.bean_id), DesignBrief()
    )

    with pytest.raises(VersionRefused) as refused:
        await wired.recipes.add_version(
            designing.id, RecipeVersionPatch(profile_version_id=version)
        )

    assert refused.value.refused == "profile_has_a_recipe"
    after = await wired.recipes.get(designing.id)
    assert after is not None and after.designing is True and after.board_id is None


# ── over the API ─────────────────────────────────────────────────────────


async def test_the_route_refuses_a_profile_that_has_a_recipe_in_the_spec_s_words(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    db = app.state.db
    version = await make_profile_version(db, "Bloom 6 bar")
    row = await ProfileBoardRepository(db).insert(
        BoardRowWrite(label="Bloom 6 bar", current_version_id=version, origin="adopted")
    )
    bean = (await client.post("/api/beans", json={"name": "Guji"})).json()["data"]["id"]

    def body(name: str) -> dict[str, object]:
        return {
            "name": name,
            "bean_id": bean,
            "version": {"profile_version_id": row.current_version_id},
        }

    first = await client.post("/api/recipes", json=body("Guji natural"))
    second = await client.post("/api/recipes", json=body("Kenya AA"))

    assert first.status_code == 201 and first.json()["data"]["board_id"] == row.id
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "PROFILE_HAS_A_RECIPE"
    assert error["message"] == (
        "Bloom 6 bar belongs to Guji natural. Duplicate Guji natural to start a new one on a copy."
    )


async def test_a_new_version_has_no_profile_to_choose(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    db = app.state.db
    version = await make_profile_version(db, "Bloom")
    row = await ProfileBoardRepository(db).insert(
        BoardRowWrite(label="Bloom", current_version_id=version, origin="adopted")
    )
    bean = (await client.post("/api/beans", json={"name": "Guji"})).json()["data"]["id"]
    made = (
        await client.post(
            "/api/recipes",
            json={
                "name": "S",
                "bean_id": bean,
                "version": {"profile_version_id": row.current_version_id},
            },
        )
    ).json()["data"]

    response = await client.post(
        f"/api/recipes/{made['id']}/versions", json={"profile_version_id": version, "dose_g": 19}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNPROCESSABLE_ENTITY"


async def test_a_version_that_brews_a_newer_version_of_the_profile_logs_a_brew_change(
    wired: Fixtures,
) -> None:
    """One profile, one name: the log says the brew changed (and the temperature that came with
    it), never "Foo → Foo"."""
    board, version = await profile(wired, "Bloom")
    made = await new_recipe(wired, "Guji", version)
    newer = await ProfileBoardRepository(wired.db).version_for(
        board, await make_profile_version(wired.db, "Bloom", temperature=95)
    )
    followed = await wired.recipes.follow_profile(board, newer)
    assert followed is not None
    second, first = await wired.recipes.versions(made.id)  # newest first

    changes = version_changes(second, first, {version: "Bloom", newer: "Bloom"})

    assert [(c.field, c.label, c.before, c.after) for c in changes] == [
        ("profile_version_id", "Brew changed", None, None),
        ("profile_temperature_c", "Temperature", "93 °C", "95 °C"),
    ]
    assert changes[1].from_profile


async def test_a_profile_that_takes_over_a_loose_version_takes_its_recipe_with_it(
    wired: Fixtures,
) -> None:
    """The starting point's Recipe brews the draft's version until the draft is put; the profile
    the put makes is the Recipe's."""
    loose = await make_profile_version(wired.db, "Drafted", owned=False)
    made = await wired.recipes.create(
        RecipeWrite(name="From a draft", bean_id=wired.bean_id),
        RecipeVersionWrite(profile_version_id=loose, dose_g=18.0),
        from_a_draft=True,
    )
    board = ProfileBoardRepository(wired.db)
    row = await board.insert(
        BoardRowWrite(label="Drafted", current_version_id=loose, origin="draft", on_machine=False)
    )

    assert await board.version_for(row.id, loose) == loose
    stored = await wired.recipes.get(made.id)
    assert stored is not None and stored.board_id == row.id


async def test_a_profile_with_a_recipe_gets_its_own_copy_and_the_loose_version_stays_the_recipes(
    wired: Fixtures,
) -> None:
    loose = await make_profile_version(wired.db, "Drafted", owned=False)
    made = await wired.recipes.create(
        RecipeWrite(name="From a draft", bean_id=wired.bean_id),
        RecipeVersionWrite(profile_version_id=loose, dose_g=18.0),
        from_a_draft=True,
    )
    holder, _version = await profile(wired, "Taken")
    await new_recipe(wired, "Holder", _version)
    board = ProfileBoardRepository(wired.db)
    row = await board.get(holder)
    assert row is not None

    mine = await board.version_for(row.id, loose)

    assert mine != loose
    assert await ProfilesRepository(wired.db).owner_of(mine) == row.id
    assert await ProfilesRepository(wired.db).owner_of(loose) is None
    stored = await wired.recipes.get(made.id)
    assert stored is not None and stored.board_id is None


async def test_the_add_a_version_route_gives_a_design_its_profile_under_new_recipes_rule(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    db = app.state.db
    free = await make_profile_version(db, "Free")
    older = await make_profile_version(db, "Moved on")
    await db.execute(
        "UPDATE profile_board SET current_version_id = ? "
        "WHERE id = (SELECT board_id FROM profile_versions WHERE id = ?)",
        (await another_version(db, older, description="newer"), older),
    )
    taken = await make_profile_version(db, "Taken")
    bean = (await client.post("/api/beans", json={"name": "Guji"})).json()["data"]["id"]
    grinder = (
        await client.post("/api/grinders", json={"name": "Niche", "step_unit": "numbers"})
    ).json()["data"]["id"]
    held = (
        await client.post(
            "/api/recipes",
            json={"name": "Holder", "bean_id": bean, "version": {"profile_version_id": taken}},
        )
    ).json()["data"]
    design = (
        await client.post("/api/recipes/design", json={"bean_id": bean, "grinder_id": grinder})
    ).json()["data"]["recipe"]

    async def add(version: int) -> httpx.Response:
        return await client.post(
            f"/api/recipes/{design['id']}/versions",
            json={"profile_version_id": version, "dose_g": 18},
        )

    assert (await add(older)).status_code == 409
    refused = await add(taken)
    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == (
        f"Taken belongs to {held['name']}. Duplicate {held['name']} to start a new one on a copy."
    )
    done = await add(free)
    assert done.status_code == 201
    page = (await client.get(f"/api/recipes/{design['id']}")).json()["data"]["recipe"]
    assert page["designing"] is False and page["profile_label"] == "Free"


async def test_the_choices_route_lists_free_profiles_and_the_files_nobody_stands_on(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    db = app.state.db
    free = await make_profile_version(db, "Free")
    held = await make_profile_version(db, "Held")
    file_version = await make_profile_version(db, "A file", owned=False)
    await ProfilesRepository(db).upsert_device_profile(device_id="f1", version_id=file_version)
    bean = (await client.post("/api/beans", json={"name": "Guji"})).json()["data"]["id"]
    await client.post(
        "/api/recipes",
        json={"name": "Holder", "bean_id": bean, "version": {"profile_version_id": held}},
    )

    response = await client.get("/api/recipes/profile-choices")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"] == [
        {"profile_version_id": free, "label": "Free"},
        {"profile_version_id": file_version, "label": "A file"},
    ]


async def test_a_recipe_on_a_loose_version_is_given_a_profile_through_the_route(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """The old Recipe whose versions name a file no profile has: its next version gives it one."""
    db = app.state.db
    loose = await make_profile_version(db, "On a file", owned=False)
    free = await make_profile_version(db, "Free")
    bean = (await client.post("/api/beans", json={"name": "Guji"})).json()["data"]["id"]
    recipes = RecipesRepository(db)
    old = await recipes.create(
        RecipeWrite(name="Old", bean_id=bean),
        RecipeVersionWrite(profile_version_id=loose),
        from_a_draft=True,
    )

    response = await client.post(
        f"/api/recipes/{old.id}/versions", json={"profile_version_id": free, "dose_g": 18}
    )

    assert response.status_code == 201, response.text
    page = (await client.get(f"/api/recipes/{old.id}")).json()["data"]["recipe"]
    assert page["profile_label"] == "Free"


@pytest.mark.parametrize("order", ["current first", "older first"])
async def test_a_recipe_on_two_unadopted_files_is_taken_through_its_current_one(
    wired: Fixtures, order: str
) -> None:
    """Writes off: the Recipe started on file A and was moved to file B. Whichever file the list
    adopts first, the Recipe's profile is the one that has its current version (B), all its
    versions end up that profile's, it brews what the profile has active, and a shot on the
    current file files under it."""
    profiles = ProfilesRepository(wired.db)
    board = ProfileBoardRepository(wired.db)
    first = await make_profile_version(wired.db, "File A", owned=False)
    second = await make_profile_version(wired.db, "File B", owned=False)
    await profiles.upsert_device_profile(device_id="a", version_id=first)
    await profiles.upsert_device_profile(device_id="b", version_id=second)
    made = await new_recipe(wired, "Off A", first)
    assert await wired.recipes.add_version(made.id, RecipeVersionPatch(profile_version_id=second))

    async def adopt(label: str, version: int, device: str) -> int:
        row = await board.insert(
            BoardRowWrite(
                label=label, current_version_id=version, origin="adopted", device_profile_id=device
            )
        )
        # The next sync points the mirror at the version the profile stands on.
        await profiles.upsert_device_profile(device_id=device, version_id=row.current_version_id)
        return row.id

    if order == "current first":
        row_b = await adopt("File B", second, "b")
        await adopt("File A", first, "a")
    else:
        await adopt("File A", first, "a")
        stored = await wired.recipes.get(made.id)
        assert stored is not None and stored.board_id is None, "an older version takes no Recipe"
        row_b = await adopt("File B", second, "b")

    stored = await wired.recipes.get(made.id)
    assert stored is not None and stored.board_id == row_b
    for version in await wired.recipes.versions(made.id):
        assert await profiles.owner_of(version.profile_version_id or 0) == row_b
    await assert_versions_owned(wired.db)
    await assert_recipes_follow_their_profiles(wired.db)
    shot = await make_shot(wired.db, "000900", profile_version_id=second)
    assert await wired.recipes.profile_match(shot, profile_version_id=second, device_profile_id="b")
    filed = await wired.db.fetch_value("SELECT recipe_version_id FROM shots WHERE id = ?", (shot,))
    current = await wired.recipes.current_version(made.id)
    assert current is not None and filed == current.id


async def test_a_recipe_taken_through_a_listed_but_inactive_version_follows_the_active_one(
    wired: Fixtures,
) -> None:
    """The profile listed the file's version without making it active; taking the Recipe over
    through it leaves the Recipe behind, so it follows the profile to what it has active."""
    profiles = ProfilesRepository(wired.db)
    board = ProfileBoardRepository(wired.db)
    loose = await make_profile_version(wired.db, "On a file", owned=False)
    await profiles.upsert_device_profile(device_id="f", version_id=loose)
    made = await new_recipe(wired, "Off", loose)
    row_id, active = await profile(wired, "The profile")

    await board.add_version(row_id, loose, "machine")

    stored = await wired.recipes.get(made.id)
    assert stored is not None and stored.board_id == row_id
    current = await wired.recipes.current_version(made.id)
    assert current is not None and current.profile_version_id == active
    await assert_versions_owned(wired.db)
    await assert_recipes_follow_their_profiles(wired.db)


async def test_a_second_recipe_on_a_shared_loose_version_leaves_the_profile_its_own_copy(
    wired: Fixtures,
) -> None:
    """Two Recipes name one loose version (an old database): when a profile takes the first Recipe's
    other version over, the shared one is copied for the profile and the Recipe names the copy."""
    profiles = ProfilesRepository(wired.db)
    shared = await make_profile_version(wired.db, "Shared", owned=False)
    own = await make_profile_version(wired.db, "Own file", owned=False)
    await profiles.upsert_device_profile(device_id="own", version_id=own)
    await profiles.upsert_device_profile(device_id="shared", version_id=shared)
    a = await new_recipe(wired, "A", shared)
    moved = await wired.recipes.add_version(a.id, RecipeVersionPatch(profile_version_id=own))
    assert moved is not None
    other = await wired.recipes.create(
        RecipeWrite(name="B", bean_id=wired.bean_id),
        RecipeVersionWrite(profile_version_id=None),
    )
    await wired.db.execute(
        "UPDATE recipe_versions SET profile_version_id = ? WHERE recipe_id = ?", (shared, other.id)
    )

    row = await ProfileBoardRepository(wired.db).insert(
        BoardRowWrite(
            label="Own file", current_version_id=own, origin="adopted", device_profile_id="own"
        )
    )

    for version in await wired.recipes.versions(a.id):
        assert await profiles.owner_of(version.profile_version_id or 0) == row.id
    b_version = (await wired.recipes.versions(other.id))[0]
    assert b_version.profile_version_id == shared, "the other Recipe keeps what it named"


async def test_the_choices_are_exactly_what_a_new_recipe_accepts(wired: Fixtures) -> None:
    """One function decides both: every version a Recipe could be created on is offered, and every
    offered one is accepted (utility files and deleted profiles are answered the same way)."""
    profiles = ProfilesRepository(wired.db)
    free = await make_profile_version(wired.db, "Free")
    held = await make_profile_version(wired.db, "Held")
    await new_recipe(wired, "Holder", held)
    deleted = await make_profile_version(wired.db, "Deleted")
    await wired.db.execute(
        "UPDATE profile_board SET deleted_at = '2026-10-13' "
        "WHERE id = (SELECT board_id FROM profile_versions WHERE id = ?)",
        (deleted,),
    )
    older = await make_profile_version(wired.db, "Moved on")
    await wired.db.execute(
        "UPDATE profile_board SET current_version_id = ? "
        "WHERE id = (SELECT board_id FROM profile_versions WHERE id = ?)",
        (await another_version(wired.db, older, description="newer"), older),
    )
    file_version = await make_profile_version(wired.db, "File", owned=False)
    await profiles.upsert_device_profile(device_id="f", version_id=file_version)
    utility = await make_profile_version(wired.db, "Utility file", owned=False)
    await profiles.upsert_device_profile(device_id="u", version_id=utility)
    await wired.db.execute("UPDATE profile_versions SET utility = 1 WHERE id = ?", (utility,))
    gone = await make_profile_version(wired.db, "Gone file", owned=False)
    await profiles.upsert_device_profile(device_id="g", version_id=gone)
    await wired.db.execute(
        "UPDATE device_profiles SET deleted_at = '2026-10-13' WHERE device_id='g'"
    )

    offered = {version for version, _ in await wired.recipes.profile_choices()}

    everything = await wired.db.fetch_all("SELECT id FROM profile_versions")
    for row in everything:
        version = int(row["id"])
        try:
            await wired.recipes.profile_to_start_on(version)
            accepted = True
        except VersionRefused:
            accepted = False
        if accepted:
            # Every accepted version is a live profile's active one or a mirrored file's.
            continue
        assert version not in offered, f"offered {version} but New Recipe refuses it"
    assert {free, file_version, utility} <= offered
    with pytest.raises(VersionRefused) as refused:
        await wired.recipes.profile_to_start_on(deleted)
    assert refused.value.refused == "profile_deleted"
    assert "deleted from the list" in refused.value.message
    assert not {held, deleted, older, gone} & offered


async def test_a_recipe_with_no_profile_now_is_given_one_though_an_older_version_names_a_file(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """An old Recipe: a version on a file, then a newer "any profile" one. Its next version names a
    free profile, which is the Recipe's whatever the current version is, and the file's version is
    settled for it."""
    db = app.state.db
    loose = await make_profile_version(db, "On a file", owned=False)
    free = await make_profile_version(db, "Free")
    bean = (await client.post("/api/beans", json={"name": "Guji"})).json()["data"]["id"]
    recipes = RecipesRepository(db)
    old = await recipes.create(
        RecipeWrite(name="OnLoose", bean_id=bean),
        RecipeVersionWrite(profile_version_id=loose),
        from_a_draft=True,
    )
    assert await recipes.add_version(old.id, RecipeVersionPatch(profile_version_id=None))

    response = await client.post(
        f"/api/recipes/{old.id}/versions", json={"profile_version_id": free, "dose_g": 18.5}
    )

    assert response.status_code == 201, response.text
    await assert_versions_owned(db)
    await assert_recipes_follow_their_profiles(db)
