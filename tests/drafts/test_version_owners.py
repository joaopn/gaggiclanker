"""A profile owns its versions: who passes the owner, how a version is attached to a profile, and
the rule that holds after every write.

* ``ensure_version`` finds or makes a version by its owner and its content, and has no default
  for the owner, so every writer says whose it is.
* ``version_for`` is the one function that attaches a version to a profile: an ownerless version
  is taken over, a profile's own is itself, another profile's becomes a copy of that profile's own.
* After every path that lists a version, every listed version is its lister's, a profile's own
  columns name only versions it owns, and no profile holds one brew twice (walked path by path,
  then by a randomised driver over the board service).
"""

from __future__ import annotations

import copy
import inspect
import json
import random
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import BoardRowPatch, BoardRowWrite, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.signatures.service import SignatureService
from tests.drafts.conftest import BASE_LABEL, base_version_id, data, profile_fixture
from tests.drafts.helpers import (
    assert_versions_owned,
    make_recipe_on,
    manual_draft,
    same_name_draft,
    tombstone,
)
from tests.drafts.test_board import (
    adopted,
    approve,
    edit_on_display,
    get_board,
    pull,
    put,
    resolve,
    row_for,
)
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


def _profile(label: str, *, bar: float = 9) -> Profile:
    document = profile_fixture("docs-medium-18g")
    document["label"] = label
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    return Profile.model_validate(document)


async def _board(app: FastAPI, label: str) -> int:
    row = await app.state.db.fetch_one("SELECT id FROM profile_board WHERE label = ?", (label,))
    assert row is not None
    return int(row["id"])


# ── 7: ensure_version ────────────────────────────────────────────────────


def test_the_owner_has_no_default_and_must_be_named() -> None:
    parameter = inspect.signature(ProfilesRepository.ensure_version).parameters["owner"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert not hasattr(ProfilesRepository, "get_version_by_hash")


async def test_a_version_is_found_by_its_owner_and_its_content(adopted: Adopted) -> None:
    app, _client, _ = adopted
    profiles = ProfilesRepository(app.state.db)
    board = ProfileBoardRepository(app.state.db)
    first = await board.insert(BoardRowWrite(label="First", current_version_id=1, origin="adopted"))
    second = await board.insert(
        BoardRowWrite(label="Second", current_version_id=2, origin="adopted")
    )
    document = _profile("Same brew")
    mine, made = await profiles.ensure_version(document, owner=first.id)
    again, made_again = await profiles.ensure_version(document, owner=first.id)
    other, made_other = await profiles.ensure_version(document, owner=second.id)
    loose, made_loose = await profiles.ensure_version(document, owner=None)
    loose_again, made_loose_again = await profiles.ensure_version(document, owner=None)

    assert (made, made_again, made_other, made_loose, made_loose_again) == (
        True,
        False,
        True,
        True,
        False,
    )
    assert again.id == mine.id and loose_again.id == loose.id
    assert len({mine.id, other.id, loose.id}) == 3
    assert (mine.board_id, other.board_id, loose.board_id) == (first.id, second.id, None)
    assert mine.content_hash == other.content_hash == loose.content_hash
    found = await profiles.find_version(mine.content_hash, owner=second.id)
    assert found is not None and found.id == other.id


async def test_the_mirror_stores_what_a_file_holds_as_a_version_of_the_profile_standing_on_it(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    file = row["machine"]["device_id"]
    await edit_on_display(fake, file)
    await pull(app)
    held = await app.state.db.fetch_value(
        "SELECT pv.board_id FROM device_profiles d "
        "JOIN profile_versions pv ON pv.id = d.current_version_id WHERE d.device_id = ?",
        (file,),
    )
    assert held == row["row"]["id"]
    await assert_versions_owned(app.state.db)


async def test_a_tombstoned_profile_still_owns_what_its_file_holds(adopted: Adopted) -> None:
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    file = row["machine"]["device_id"]
    await tombstone(client, row["row"]["id"])
    await edit_on_display(fake, file)
    fake.ws_requests.clear()
    await app.state.connection.engine.sync_profiles(trigger="test")
    held = await app.state.db.fetch_value(
        "SELECT pv.board_id FROM device_profiles d "
        "JOIN profile_versions pv ON pv.id = d.current_version_id WHERE d.device_id = ?",
        (file,),
    )
    assert held == row["row"]["id"]


async def test_a_file_no_profile_stands_on_holds_a_version_that_belongs_to_none(
    adopted: Adopted,
) -> None:
    app, _client, fake = adopted
    made = copy.deepcopy(fake.profiles[0])
    made.update(id="display-made", label="Made on the display", temperature=91.0)
    fake.profiles.append(made)
    profiles = ProfilesRepository(app.state.db)
    assert await profiles.standing_profile("display-made") is None
    await app.state.connection.engine.sync_profiles(trigger="test")
    # The sync that finds the file also makes it a profile, which takes the version over.
    row = row_for(await get_board(_client), "Made on the display")["row"]
    version = await profiles.find_version_for_device_profile("display-made")
    assert version is not None and version.board_id == row["id"]
    await assert_versions_owned(app.state.db)


async def test_an_import_stores_a_version_that_belongs_to_no_profile_until_the_list_takes_it(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    document = profile_fixture("docs-medium-18g")
    document["label"] = "Imported"
    response = await client.post(
        "/api/import",
        files=[("files", ("imported.json", json.dumps(document).encode(), "application/json"))],
    )
    assert response.status_code < 300, response.text
    row = row_for(await get_board(client), "Imported")["row"]
    owner = await app.state.db.fetch_value(
        "SELECT board_id FROM profile_versions WHERE id = ?", (row["current_version_id"],)
    )
    assert owner == row["id"]
    again = await client.post(
        "/api/import",
        files=[("files", ("imported.json", json.dumps(document).encode(), "application/json"))],
    )
    assert data(again)["items"][0]["status"] == "skipped"
    assert (
        await app.state.db.fetch_value(
            "SELECT COUNT(*) FROM profile_versions WHERE label = 'Imported'"
        )
        == 1
    )
    await assert_versions_owned(app.state.db)


async def test_the_empty_baseline_belongs_to_no_profile(adopted: Adopted) -> None:
    app, _client, _ = adopted
    base = await ProfilesRepository(app.state.db).empty_base()
    owner = await app.state.db.fetch_value(
        "SELECT board_id FROM profile_versions WHERE id = ?", (base,)
    )
    assert owner is None


async def test_a_draft_for_a_new_profile_belongs_to_none_and_a_change_to_a_profile_belongs_to_it(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    new = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    change = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    owners = {
        draft["id"]: await app.state.db.fetch_value(
            "SELECT board_id FROM profile_versions WHERE id = ?", (draft["draft_version_id"],)
        )
        for draft in (new, change)
    }
    assert owners == {new["id"]: None, change["id"]: row["id"]}


# ── 8: version_for ───────────────────────────────────────────────────────


async def _repo(app: FastAPI) -> tuple[ProfileBoardRepository, ProfilesRepository]:
    return ProfileBoardRepository(app.state.db), ProfilesRepository(app.state.db)


async def _row(app: FastAPI, board: ProfileBoardRepository, label: str, version: int) -> int:
    row = await board.insert(BoardRowWrite(label=label, current_version_id=version, origin="draft"))
    return row.id


async def test_an_ownerless_version_is_taken_over_by_the_profile_that_lists_it(
    adopted: Adopted,
) -> None:
    app, _client, _ = adopted
    board, profiles = await _repo(app)
    loose, _ = await profiles.ensure_version(_profile("Taken over"), owner=None)
    row = await board.insert(
        BoardRowWrite(label="Taken over", current_version_id=loose.id, origin="draft")
    )
    assert row.current_version_id == loose.id
    after = await profiles.get_version(loose.id)
    assert after is not None and after.board_id == row.id
    assert await board.version_for(row.id, loose.id) == loose.id


async def test_a_profile_that_already_has_the_brew_keeps_it_and_leaves_the_loose_one_alone(
    adopted: Adopted,
) -> None:
    app, _client, _ = adopted
    board, profiles = await _repo(app)
    first, _ = await profiles.ensure_version(_profile("Twice"), owner=None)
    row = await _row(app, board, "Twice", first.id)
    loose, _ = await profiles.ensure_version(_profile("Twice"), owner=None)
    assert loose.id != first.id
    assert await board.version_for(row, loose.id) == first.id
    still = await profiles.get_version(loose.id)
    assert still is not None and still.board_id is None
    # The profile's own columns that named the loose one name its own.
    await board.update(row, BoardRowPatch(failed_version_id=loose.id))
    await app.state.db.execute("PRAGMA foreign_keys = OFF")
    assert await board.version_for(row, loose.id) == first.id
    got = await board.get(row)
    assert got is not None and got.failed_version_id == first.id


async def test_another_profiles_version_becomes_a_copy_that_is_this_profiles_own(
    adopted: Adopted,
) -> None:
    app, _client, _ = adopted
    board, profiles = await _repo(app)
    version, _ = await profiles.ensure_version(_profile("Shared"), owner=None)
    first = await _row(app, board, "First", version.id)
    other = await profiles.ensure_version(_profile("Placeholder", bar=8.5), owner=None)
    second = await _row(app, board, "Second", other[0].id)
    await board.update(second, BoardRowPatch(previous_version_id=version.id))

    copy_id = await board.version_for(second, version.id)

    assert copy_id not in (version.id, other[0].id)
    copied = await profiles.get_version(copy_id)
    original = await profiles.get_version(version.id)
    assert copied is not None and original is not None
    assert copied.board_id == second and original.board_id == first
    for same in (
        "content_hash",
        "label",
        "type",
        "profile",
        "device_profile",
        "source",
        "created_at",
    ):
        assert getattr(copied, same) == getattr(original, same), same
    # The profile's columns that named the original name its own copy now, and asking again
    # gives the same copy.
    row = await board.get(second)
    assert row is not None and row.previous_version_id == copy_id
    assert await board.version_for(second, version.id) == copy_id
    await assert_versions_owned(app.state.db)


async def test_the_copy_carries_the_signature_in_force_with_carry_quietly(
    adopted: Adopted,
) -> None:
    app, _client, _ = adopted
    board, profiles = await _repo(app)
    version, _ = await profiles.ensure_version(_profile("Signed"), owner=None)
    await _row(app, board, "Signed", version.id)
    signatures = SignatureService(app.state.db)
    first_phase = _profile("Signed").model_dump()["phases"][0]["name"]
    await signatures.propose(
        version.id,
        [ExpectationInput(tier="critical", kind="reached", phase=first_phase)],
        reason="why",
    )
    second_start, _ = await profiles.ensure_version(_profile("Elsewhere", bar=8.4), owner=None)
    second = await _row(app, board, "Signed too", second_start.id)

    copy_id = await board.version_for(second, version.id)

    assert await signatures.count_in_force(copy_id) == await signatures.count_in_force(version.id)
    assert await signatures.count_in_force(copy_id) == 1


async def test_listing_a_version_goes_through_version_for(adopted: Adopted) -> None:
    """``add_version`` attaches first, so a version can never be listed on a profile it is not
    the version of."""
    app, _client, _ = adopted
    board, profiles = await _repo(app)
    version, _ = await profiles.ensure_version(_profile("Listed"), owner=None)
    first = await _row(app, board, "Listed", version.id)
    start, _ = await profiles.ensure_version(_profile("Start", bar=8.3), owner=None)
    second = await _row(app, board, "Listed copy", start.id)
    assert await board.add_version(second, version.id, "edit") is True
    listed = [v.version_id for v in await board.list_versions(second)]
    assert version.id not in listed and len(listed) == 2
    assert [v.version_id for v in await board.list_versions(first)] == [version.id]
    await assert_versions_owned(app.state.db)


# ── the rule, after every path that lists a version ──────────────────────


async def _put_new(app: FastAPI, client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    return await put(client, await manual_draft(app, client, BASE_LABEL, name, 6.5))


async def test_every_write_path_leaves_every_version_with_its_owner(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    db = app.state.db
    await assert_versions_owned(db)  # the mirror and the first sync's joins

    # A new profile, and a change to it.
    first = await _put_new(app, client, "Alpha")
    await pull(app)
    await assert_versions_owned(db)
    await put(client, await same_name_draft(app, client, provider, "Alpha", 7.2))
    await pull(app)
    await assert_versions_owned(db)

    # A revived profile: deleted while its file waits, put again.
    await tombstone(client, first["id"])
    again = await manual_draft(app, client, BASE_LABEL, "Alpha", 6.5)
    await approve(app, again)
    await assert_versions_owned(db)

    # A file made on the display joins the list, and so does a relocated one: a file no profile
    # holds the id of is a profile of its own.
    made = copy.deepcopy(fake.profiles[0])
    made.update(id="display-made", label="Display made", temperature=90.5)
    fake.profiles.append(made)
    person = row_for(await get_board(client), BASE_LABEL)
    gone = person["machine"]["device_id"]
    relocated = copy.deepcopy(next(p for p in fake.profiles if p["id"] == gone))
    relocated["id"] = "relocated"
    fake.profiles = [p for p in fake.profiles if p["id"] != gone] + [relocated]
    await pull(app)
    await assert_versions_owned(db)
    await pull(app)
    await assert_versions_owned(db)

    # A profile's own file edited on the display (a conflict records it), kept as the machine's
    # side.
    row = row_for(await get_board(client), BASE_LABEL)
    await edit_on_display(fake, row["machine"]["device_id"], bump=2)
    await pull(app)
    await assert_versions_owned(db)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    kept = await resolve(client, row["row"]["id"], "machine", seen["machine"]["content_hash"])
    assert kept.status_code == 200
    await assert_versions_owned(db)

    # A profile edited on the display (a conflict records it), then an older version made active.
    await edit_on_display(fake, row["machine"]["device_id"], bump=1)
    await pull(app)
    await assert_versions_owned(db)
    board = await get_board(client)
    listed = data(await client.get(f"/api/profile-board/{row['row']['id']}/versions"))
    older = [v for v in listed["versions"] if not v["is_active"]]
    if older:
        await client.put(
            f"/api/profile-board/{row['row']['id']}/active-version",
            json={"version_id": older[0]["version_id"]},
        )
    assert board["rows"]
    await assert_versions_owned(db)


async def test_a_pin_and_a_duplicate_leave_every_version_with_its_owner(adopted: Adopted) -> None:
    app, client, _ = adopted
    base = await base_version_id(app, BASE_LABEL)
    recipe_id = await make_recipe_on(client, "Guji", base)
    detail = data(await client.get(f"/api/recipes/{recipe_id}"))
    version = detail["versions"][0]["version"]["id"]
    pinned = data(await client.post(f"/api/recipes/{recipe_id}/versions/{version}/pin", json={}))
    await assert_versions_owned(app.state.db)
    data(await client.post(f"/api/pins/{pinned['pin']['id']}/unpin"))
    data(await client.post(f"/api/recipes/{recipe_id}/versions/{version}/pin", json={}))
    made = await client.post(
        f"/api/recipes/{recipe_id}/versions/{version}/duplicate",
        json={
            "recipe_name": "Copy",
            "profile_name": "Blooming milk",
            "insight_ids": [],
            "bean_id": detail["recipe"]["bean_id"],
        },
    )
    assert made.status_code < 300, made.text
    await assert_versions_owned(app.state.db)
    # The copies are versions of their own: the original and the copies are three profiles.
    owners = await app.state.db.fetch_all(
        "SELECT DISTINCT board_id FROM profile_versions "
        "WHERE label IN (?, ?, ?) AND board_id IS NOT NULL",
        (BASE_LABEL, f"{BASE_LABEL} v1", "Blooming milk"),
    )
    assert len(owners) == 3


async def test_a_second_profile_put_from_a_draft_of_a_brew_a_deleted_profile_has_has_its_own_copy(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    first = await manual_draft(app, client, BASE_LABEL, "Twin", 6.1)
    second = await manual_draft(app, client, BASE_LABEL, "Twin", 6.1)
    assert first["draft_version_id"] == second["draft_version_id"]
    one = await put(client, first)
    await tombstone(client, one["id"])
    two = await put(client, second)
    assert two["id"] != one["id"]
    assert two["current_version_id"] != one["current_version_id"]
    moved = data(await client.get(f"/api/profile-drafts/{second['id']}"))["draft"]
    assert moved["draft_version_id"] == two["current_version_id"]
    await assert_versions_owned(app.state.db)


# ── randomised: the same rule over the board service ─────────────────────


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
async def test_a_random_walk_over_the_board_keeps_every_version_with_its_owner(
    adopted: Adopted, seed: int
) -> None:
    app, client, fake = adopted
    rng = random.Random(seed)  # noqa: S311 - a seeded test driver, not security
    counter = 0

    async def rows() -> list[dict[str, Any]]:
        return list((await get_board(client))["rows"])

    for _step in range(22):
        operation = rng.choice(
            [
                "new",
                "change",
                "pull",
                "toggle",
                "active",
                "delete",
                "edit_display",
                "conflict",
                "import",
                "pin",
                "duplicate",
                "display_file",
                "twin_drafts",
                "recipe_on_a_profile",
                "recipe_version",
                "recipe_revert",
            ]
        )
        counter += 1
        live = await rows()
        row = rng.choice(live) if live else None
        if operation == "new":
            await put(
                client, await manual_draft(app, client, BASE_LABEL, f"R{counter}", 5 + rng.random())
            )
        elif operation == "change" and row is not None:
            label = row["row"]["label"]
            made = await client.post(
                "/api/profile-drafts",
                json={
                    "base_version_id": row["row"]["current_version_id"],
                    "profile": _changed(row, rng),
                    "change_summary": "random",
                },
            )
            if made.status_code == 201:
                await client.post("/api/profile-board", json={"draft_id": data(made)["id"]})
            del label
        elif operation == "pull":
            await pull(app)
        elif operation == "toggle" and row is not None:
            await client.put(
                f"/api/profile-board/{row['row']['id']}/on-machine",
                json={"on": rng.random() < 0.5},
            )
        elif operation == "active" and row is not None:
            listed = data(await client.get(f"/api/profile-board/{row['row']['id']}/versions"))
            options = [v["version_id"] for v in listed["versions"]]
            if options:
                await client.put(
                    f"/api/profile-board/{row['row']['id']}/active-version",
                    json={"version_id": rng.choice(options)},
                )
        elif operation == "delete" and row is not None:
            await tombstone(client, row["row"]["id"])
        elif operation == "edit_display" and fake.profiles:
            await edit_on_display(
                fake, str(rng.choice(fake.profiles)["id"]), bump=rng.random() + 0.5
            )
        elif operation == "conflict" and row is not None:
            seen = await client.get(f"/api/profile-board/{row['row']['id']}/conflict")
            if seen.status_code == 200 and data(seen) is not None:
                await resolve(
                    client,
                    row["row"]["id"],
                    rng.choice(["app", "machine"]),
                    data(seen)["machine"]["content_hash"],
                )
        elif operation == "import":
            document = profile_fixture("docs-medium-18g")
            document["label"] = rng.choice([f"I{counter}", BASE_LABEL, "Imported twice"])
            await client.post(
                "/api/import",
                files=[("files", ("p.json", json.dumps(document).encode(), "application/json"))],
            )
        elif operation == "pin" and row is not None:
            recipe_id = await _recipe_on(client, f"S{counter}", row["row"]["current_version_id"])
            if recipe_id is None:
                continue
            detail = data(await client.get(f"/api/recipes/{recipe_id}"))
            await client.post(
                f"/api/recipes/{recipe_id}/versions/{detail['versions'][0]['version']['id']}/pin",
                json={},
            )
        elif operation == "duplicate" and row is not None:
            recipe_id = await _recipe_on(client, f"D{counter}", row["row"]["current_version_id"])
            if recipe_id is None:
                continue
            detail = data(await client.get(f"/api/recipes/{recipe_id}"))
            await client.post(
                f"/api/recipes/{recipe_id}/versions/{detail['versions'][0]['version']['id']}/duplicate",
                json={
                    "recipe_name": f"Copy {counter}",
                    "profile_name": f"Dup {counter}",
                    "insight_ids": [],
                    "bean_id": detail["recipe"]["bean_id"],
                },
            )
        elif operation == "twin_drafts":
            await _twin_drafts(app, client, f"Twin{counter}")
        elif operation == "recipe_on_a_profile" and row is not None:
            # A profile that has a Recipe is refused (a 409), and one that has none gets one.
            await _recipe_on(client, f"W{counter}", row["row"]["current_version_id"])
        elif operation in ("recipe_version", "recipe_revert"):
            recipes = await db_recipes(app)
            if recipes:
                chosen = rng.choice(recipes)
                if operation == "recipe_version":
                    await client.post(
                        f"/api/recipes/{chosen}/versions",
                        json={"grind_setting": str(counter), "intent": "walk"},
                    )
                else:
                    versions = [
                        v["version"]
                        for v in data(await client.get(f"/api/recipes/{chosen}"))["versions"]
                    ]
                    await client.post(
                        f"/api/recipes/{chosen}/rollback",
                        json={"to_version_id": rng.choice(versions)["id"], "note": "walk"},
                    )
        elif operation == "display_file":
            file = copy.deepcopy(fake.profiles[0])
            file.update(id=f"disp{counter}", label=f"Display {counter}", temperature=88.0 + counter)
            fake.profiles.append(file)
        await assert_versions_owned(app.state.db)
    await pull(app)
    await assert_versions_owned(app.state.db)


async def db_recipes(app: FastAPI) -> list[int]:
    rows = await app.state.db.fetch_all("SELECT id FROM recipes WHERE designing = 0 ORDER BY id")
    return [int(r["id"]) for r in rows]


async def _twin_drafts(app: FastAPI, client: httpx.AsyncClient, name: str) -> None:
    """Two drafts of one new profile; the first is put and its profile deleted, then the second
    is put: the second profile's version is its own copy of the first's brew."""
    first = await manual_draft(app, client, BASE_LABEL, name, 6.1)
    second = await manual_draft(app, client, BASE_LABEL, name, 6.1)
    assert first["draft_version_id"] == second["draft_version_id"]
    row = await put(client, first)
    await tombstone(client, row["id"])
    await client.post("/api/profile-board", json={"draft_id": second["id"]})


async def _recipe_on(client: httpx.AsyncClient, name: str, version_id: int) -> int | None:
    """A Recipe on a profile version, or ``None`` when the app refuses it (a pin's frozen copy)."""
    bean = data(await client.post("/api/beans", json={"name": name, "roaster": "nobody"}))
    response = await client.post(
        "/api/recipes",
        json={
            "name": name,
            "bean_id": bean["id"],
            "version": {
                "drink": "espresso",
                "dose_g": 18.0,
                "target_yield_g": 36.0,
                "profile_version_id": version_id,
            },
        },
    )
    return int(data(response)["id"]) if response.status_code < 300 else None


def _changed(row: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """The row's active document with its first pump pressure moved (its name kept)."""
    document: dict[str, Any] = copy.deepcopy(row["active_version"]["profile"])
    document["phases"][0]["pump"] = {
        "target": "pressure",
        "pressure": 5 + rng.random() * 4,
        "flow": 0,
    }
    return document


async def test_a_file_edited_back_to_an_older_version_is_recorded_as_the_profiles_own(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await put(client, await manual_draft(app, client, BASE_LABEL, "Alpha", 6.5))
    await pull(app)
    await put(client, await same_name_draft(app, client, provider, "Alpha", 7.2))
    await pull(app)
    board = row_for(await get_board(client), "Alpha")
    file = board["machine"]["device_id"]
    listed = data(await client.get(f"/api/profile-board/{row['id']}/versions"))
    older = next(v for v in listed["versions"] if not v["is_active"])
    next(p for p in fake.profiles if p["id"] == file).update(
        {**older["profile"], "id": file, "favorite": True, "selected": False}
    )

    await pull(app)

    await assert_versions_owned(app.state.db)
    # What the file held is a version of the profile (it was recorded, then replaced).
    assert (
        await app.state.db.fetch_value(
            "SELECT board_id FROM profile_versions WHERE id = ?", (older["version_id"],)
        )
        == row["id"]
    )


async def test_a_file_held_by_two_files_is_found_through_the_file_its_profile_stands_on(
    adopted: Adopted,
) -> None:
    from gaggiclanker.db.repos.profile_board import BoardRowPatch, BoardRowWrite

    app, _client, _ = adopted
    board, profiles = await _repo(app)
    version, _ = await profiles.ensure_version(_profile("Two files"), owner=None)
    row = await board.insert(
        BoardRowWrite(label="Two files", current_version_id=version.id, origin="adopted")
    )
    await profiles.upsert_device_profile(device_id="a-file", version_id=version.id)
    await profiles.upsert_device_profile(device_id="z-file", version_id=version.id)
    await board.update(row.id, BoardRowPatch(device_profile_id="z-file"))
    assert await profiles.find_device_id_for_version(version.id) == "z-file"
    await board.update(row.id, BoardRowPatch(device_profile_id="a-file"))
    assert await profiles.find_device_id_for_version(version.id) == "a-file"


async def test_the_profile_of_a_change_not_yet_put_is_its_destination_though_it_lists_nothing(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    change = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    listed = await app.state.db.fetch_value(
        "SELECT COUNT(*) FROM profile_board_versions WHERE version_id = ?",
        (change["draft_version_id"],),
    )
    assert listed == 0
    assert change["destination_board_id"] == row["id"]


async def test_a_base_held_only_by_the_file_a_live_profile_stands_on_belongs_to_that_profile(
    adopted: Adopted,
) -> None:
    from gaggiclanker.db.repos.profile_board import BoardRowPatch, BoardRowWrite
    from gaggiclanker.drafts.live_lineage import LiveLineage

    app, _client, _ = adopted
    board, profiles = await _repo(app)
    loose, _ = await profiles.ensure_version(_profile("Held by a file"), owner=None)
    own, _ = await profiles.ensure_version(_profile("Its own"), owner=None)
    row = await board.insert(
        BoardRowWrite(label="Its own", current_version_id=own.id, origin="adopted")
    )
    await profiles.upsert_device_profile(device_id="held-file", version_id=loose.id)
    await board.update(row.id, BoardRowPatch(device_profile_id="held-file"))
    lineage = LiveLineage(app.state.db)
    found = await lineage.base_profile(loose.id)
    assert found is not None and found.id == row.id
    assert await lineage.base_profile(own.id) is not None
