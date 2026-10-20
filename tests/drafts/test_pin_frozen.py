"""A pinned copy is frozen: it changes only by unpinning it.

The refusal is server side (409 `PIN_FROZEN`, a sentence naming the pin): the switch, the active
version, and any draft that would continue the copy. The star still works, and so does a
conflict: an edit made on the machine to the copy's file is asked about like any other, keeping
the app's side as always, and keeping the machine's side gives the pin's Recipe a new minor version
with the machine's brew, which the pin then names. The draft refusal lives in the one
function that decides where a draft goes, so drafting, the put and the landing the page shows
cannot disagree.
"""

from __future__ import annotations

import copy as _copy
import json
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
from gaggiclanker.db.repos.recipes import RecipesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from tests.drafts.conftest import BASE_LABEL, base_version_id, data, error
from tests.drafts.test_board import adopted, get_board, pull, row_for
from tests.drafts.test_pins import _pin, _recipe_with_version
from tests.recipes.conftest import make_shot

__all__ = ["adopted"]  # the fixture, re-exported for this module

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]
NAME = f"{BASE_LABEL} v1"
SENTENCE = f"{NAME} is pinned to Guji v1: unpin it to change it."


async def _pinned_row(app: FastAPI, client: httpx.AsyncClient) -> dict[str, Any]:
    set_id, version_id = await _recipe_with_version(app, client)
    data(await _pin(client, set_id, version_id))
    return dict(row_for(await get_board(client), NAME))


async def test_the_switch_and_the_active_version_are_refused_with_a_sentence_naming_the_pin(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    row = await _pinned_row(app, client)
    row_id = row["row"]["id"]

    off = await client.put(f"/api/profile-board/{row_id}/on-machine", json={"on": False})
    active = await client.put(
        f"/api/profile-board/{row_id}/active-version",
        json={"version_id": row["row"]["current_version_id"]},
    )

    for refused in (off, active):
        assert refused.status_code == 409
        assert error(refused)["code"] == "PIN_FROZEN" and error(refused)["message"] == SENTENCE
    after = row_for(await get_board(client), NAME)
    assert after["on_machine"] is True


async def test_the_star_still_works(adopted: Adopted) -> None:
    app, client, _fake = adopted
    row = await _pinned_row(app, client)

    response = await client.put(
        f"/api/profile-board/{row['row']['id']}/starred", json={"starred": False}
    )

    assert response.status_code == 200, response.text
    assert row_for(await get_board(client), NAME)["starred"] is False


async def _edited_on_the_machine(
    app: FastAPI, client: httpx.AsyncClient, fake: FakeDevice
) -> tuple[dict[str, Any], dict[str, str]]:
    """A pinned copy on the machine, edited there: its row and the conflict's content hash."""
    row = await _pinned_row(app, client)
    await pull(app)  # the copy reaches the machine
    edited = next(p for p in fake.profiles if p["label"] == NAME)
    edited["temperature"] = float(edited.get("temperature", 90)) + 1
    await pull(app)  # the machine's file is not what the app put there: a conflict
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    return row, {"content_hash": seen["machine"]["content_hash"]}


async def test_keeping_the_apps_side_of_a_pinned_files_conflict_works_as_for_any_row(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row, body = await _edited_on_the_machine(app, client, fake)
    recipe_versions = int(await app.state.db.fetch_value("SELECT COUNT(*) FROM recipe_versions"))

    kept = await client.post(
        f"/api/profile-board/{row['row']['id']}/conflict", json={"keep": "app", **body}
    )

    assert kept.status_code == 200, kept.text
    assert (
        int(await app.state.db.fetch_value("SELECT COUNT(*) FROM recipe_versions"))
        == recipe_versions
    )


async def test_keeping_the_machines_side_of_a_pinned_files_conflict_gives_the_recipe_a_new_version(
    adopted: Adopted,
) -> None:
    """The Recipe's profile gets its own version of the machine's brew, made active, so the Recipe
    has a new minor version, and the pin names that Recipe version. The copy keeps the file and the
    machine's version."""
    app, client, fake = adopted
    set_id = int(await app.state.db.fetch_value("SELECT MAX(id) FROM recipes") or 0)
    row, body = await _edited_on_the_machine(app, client, fake)
    row_id = row["row"]["id"]
    set_id = int(await app.state.db.fetch_value("SELECT id FROM recipes ORDER BY id DESC LIMIT 1"))
    own = int(
        await app.state.db.fetch_value("SELECT board_id FROM recipes WHERE id = ?", (set_id,))
    )
    before = int(
        await app.state.db.fetch_value(
            "SELECT current_version_id FROM profile_board WHERE id = ?", (own,)
        )
    )

    kept = await client.post(
        f"/api/profile-board/{row_id}/conflict", json={"keep": "machine", **body}
    )

    assert kept.status_code == 200, kept.text
    recipe_row = data(await client.get(f"/api/sets/{set_id}"))["set"]
    assert recipe_row["current_version_label"] == "v1.1"
    new_version = recipe_row["current_version_id"]
    pin = await app.state.db.fetch_one(
        "SELECT * FROM recipe_version_pins WHERE board_id = ?", (row_id,)
    )
    assert pin is not None and pin["set_version_id"] == new_version and pin["unpinned_at"] is None
    # ... and it names one version, the one the file holds now (the copy row's active version).
    assert (
        pin["profile_version_id"]
        == row_for(await get_board(client), NAME)["row"]["current_version_id"]
    )
    # The Recipe's profile now brews the machine's brew, under its own name.
    active = await app.state.db.fetch_one(
        "SELECT pv.label, pv.board_id, pv.json FROM profile_board b "
        "JOIN profile_versions pv ON pv.id = b.current_version_id WHERE b.id = ?",
        (own,),
    )
    assert active["board_id"] == own and active["label"] == BASE_LABEL
    assert json.loads(active["json"])["temperature"] == float(
        next(p for p in fake.profiles if p["label"] == NAME)["temperature"]
    )
    assert (
        await app.state.db.fetch_value(
            "SELECT current_version_id FROM profile_board WHERE id = ?", (own,)
        )
    ) != before
    # The copy keeps the file, and the version the file holds.
    held = row_for(await get_board(client), NAME)
    assert held["row"]["device_profile_id"] is not None and held["in_conflict"] is False
    assert held["row"]["current_version_id"] != row["row"]["current_version_id"]
    assert (
        await app.state.db.fetch_value(
            "SELECT board_id FROM profile_versions WHERE id = ?",
            (held["row"]["current_version_id"],),
        )
        == row_id
    )


async def test_a_shot_on_a_pinned_file_still_in_conflict_stays_in_the_inbox(
    adopted: Adopted,
) -> None:
    """The file was edited and nobody has chosen yet: its version is no pin's, so a shot brewed
    on it waits, as on any profile in conflict."""
    app, client, _fake = adopted
    row, _body = await _edited_on_the_machine(app, client, _fake)
    machine = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))["machine"]
    shot = await make_shot(app.state.db, "000778", profile_version_id=machine["version_id"])

    summary = await RecipesRepository(app.state.db).match_unfiled([shot])

    assert summary.matched == 0
    assert (
        await app.state.db.fetch_value("SELECT recipe_version_id FROM shots WHERE id = ?", (shot,))
        is None
    )


async def test_a_shot_brewed_on_the_kept_file_files_under_the_pins_new_recipe_version(
    adopted: Adopted,
) -> None:
    """The file holds the version the person kept; its shots belong to the pin, not the inbox."""
    app, client, fake = adopted
    row, body = await _edited_on_the_machine(app, client, fake)
    row_id = row["row"]["id"]
    await client.post(f"/api/profile-board/{row_id}/conflict", json={"keep": "machine", **body})
    held = row_for(await get_board(client), NAME)["row"]
    pin = await app.state.db.fetch_one(
        "SELECT * FROM recipe_version_pins WHERE board_id = ?", (row_id,)
    )
    assert pin is not None
    shot = await make_shot(app.state.db, "000777", profile_version_id=held["current_version_id"])

    summary = await RecipesRepository(app.state.db).match_unfiled([shot])

    assert summary.matched == 1
    assert (
        await app.state.db.fetch_value("SELECT recipe_version_id FROM shots WHERE id = ?", (shot,))
        == pin["set_version_id"]
    )


async def test_a_draft_that_would_continue_the_copy_is_refused_and_one_under_another_name_is_new(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    row = await _pinned_row(app, client)
    copy_version = row["row"]["current_version_id"]
    document = data(await client.get(f"/api/profile-versions/{copy_version}"))["profile"]
    changed = _copy.deepcopy(document)
    changed["phases"][0]["pump"] = {"target": "pressure", "pressure": 7, "flow": 0}
    drafts_before = int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_drafts"))

    same_name = await client.post(
        "/api/profile-drafts", json={"base_version_id": copy_version, "profile": changed}
    )

    assert same_name.status_code == 409, same_name.text
    assert (error(same_name)["code"], error(same_name)["message"]) == ("PIN_FROZEN", SENTENCE)
    assert (
        int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_drafts")) == drafts_before
    )
    elsewhere = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": copy_version, "profile": {**changed, "label": "Spin-off"}},
    )
    assert elsewhere.status_code < 300, elsewhere.text


async def test_the_landing_and_the_put_agree_for_a_draft_that_reached_the_copys_name(
    adopted: Adopted,
) -> None:
    """A draft that carries the pinned copy's name (stored by hand: drafting refuses to make one).

    The board's landing says it is refused and the put refuses it, with the same sentence."""
    app, client, _fake = adopted
    row = await _pinned_row(app, client)
    copy_version = row["row"]["current_version_id"]
    stored = await app.state.board.profiles.get_version(copy_version)
    assert stored is not None and stored.profile is not None
    changed = Profile.model_validate(stored.profile)
    changed = changed.model_copy(update={"description": "not the same"})
    version, _ = await app.state.board.profiles.ensure_version(changed, source="draft", owner=None)
    assert version.id != copy_version
    draft = await ProfileDraftsRepository(app.state.db).create(
        ProfileDraftWrite(base_version_id=copy_version, draft_version_id=version.id, made_by="edit")
    )

    board = await get_board(client)
    [landing] = [item for item in board["landings"] if item["draft_id"] == draft.id]
    put = await client.post("/api/profile-board", json={"draft_id": draft.id})
    check = data(
        await client.post(f"/api/profile-drafts/{draft.id}/name-check", json={"label": "Anything"})
    )

    assert landing["plain"]["refused"] == SENTENCE
    assert check["refused"] == SENTENCE, "the name check agrees with the landing and the put"
    assert put.status_code == 409
    assert (error(put)["code"], error(put)["message"]) == ("PIN_FROZEN", SENTENCE)


async def test_the_name_check_and_the_put_refuse_the_pinned_name_with_one_sentence(
    adopted: Adopted,
) -> None:
    """A waiting draft made from the copy under another name, then given the copy's own name."""
    app, client, _fake = adopted
    row = await _pinned_row(app, client)
    copy_version = row["row"]["current_version_id"]
    document = data(await client.get(f"/api/profile-versions/{copy_version}"))["profile"]
    changed = _copy.deepcopy(document)
    changed["phases"][0]["pump"] = {"target": "pressure", "pressure": 7, "flow": 0}
    draft = data(
        await client.post(
            "/api/profile-drafts",
            json={"base_version_id": copy_version, "profile": {**changed, "label": "Spin-off"}},
        )
    )

    check = data(
        await client.post(f"/api/profile-drafts/{draft['id']}/name-check", json={"label": NAME})
    )
    put = await client.post("/api/profile-board", json={"draft_id": draft["id"], "label": NAME})

    assert check["refused"] == SENTENCE
    assert put.status_code == 409
    assert (error(put)["code"], error(put)["message"]) == ("PIN_FROZEN", SENTENCE)
    # Its own, free name is fine in both.
    free = data(
        await client.post(
            f"/api/profile-drafts/{draft['id']}/name-check", json={"label": "Another spin-off"}
        )
    )
    assert free["refused"] is None


async def test_the_name_check_says_what_the_put_would_say_about_an_unpinned_copy(
    adopted: Adopted,
) -> None:
    """After the unpin no profile has the name, but the document is still the pin's copy."""
    app, client, _fake = adopted
    set_id, version_id = await _recipe_with_version(app, client)
    made = data(await _pin(client, set_id, version_id))["pin"]
    copy_version = made["profile_version_id"]
    document = data(await client.get(f"/api/profile-versions/{copy_version}"))["profile"]
    draft = data(
        await client.post(
            "/api/profile-drafts",
            json={"base_version_id": copy_version, "profile": {**document, "label": "Spin-off"}},
        )
    )
    data(await client.post(f"/api/pins/{made['id']}/unpin"))

    check = data(
        await client.post(f"/api/profile-drafts/{draft['id']}/name-check", json={"label": NAME})
    )
    put = await client.post("/api/profile-board", json={"draft_id": draft["id"], "label": NAME})

    person = f"{NAME} is the name of the copy pinned from Guji v1. Choose another name."
    assert check["refused"] == person
    assert put.status_code == 409
    assert error(put)["message"] == (
        f"{NAME} is the name of the copy pinned from Guji v1. "
        "Choose another name for a new profile."
    )


async def _a_stored_draft_equal_to_the_copy(app: FastAPI, client: httpx.AsyncClient) -> Any:
    """A pin, and a waiting draft made before it for a new profile with the copy's name and brew."""
    set_id, version_id = await _recipe_with_version(app, client)
    made = data(await _pin(client, set_id, version_id))["pin"]
    base = await base_version_id(app, BASE_LABEL)
    draft = await ProfileDraftsRepository(app.state.db).create(
        ProfileDraftWrite(
            base_version_id=base,
            draft_version_id=made["profile_version_id"],
            label=NAME,
            new_profile=True,
            made_by="edit",
        )
    )
    return made, draft


async def test_a_stored_draft_equal_to_a_live_pins_copy_is_not_put(adopted: Adopted) -> None:
    app, client, _fake = adopted
    made, draft = await _a_stored_draft_equal_to_the_copy(app, client)
    rows = int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board"))

    put = await client.post("/api/profile-board", json={"draft_id": draft.id})

    assert put.status_code == 409
    assert error(put)["message"] == "That profile version is already in the list."
    assert int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board")) == rows
    assert made["board_id"] == row_for(await get_board(client), NAME)["row"]["id"]


async def test_a_stored_draft_equal_to_an_unpinned_copy_is_refused_at_the_put(
    adopted: Adopted,
) -> None:
    """No pin and no live row any more: only the copy's claim on the profile version stands."""
    app, client, _fake = adopted
    made, draft = await _a_stored_draft_equal_to_the_copy(app, client)
    data(await client.post(f"/api/pins/{made['id']}/unpin"))
    rows = int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board"))

    board = await get_board(client)
    [landing] = [item for item in board["landings"] if item["draft_id"] == draft.id]
    check = data(
        await client.post(f"/api/profile-drafts/{draft.id}/name-check", json={"label": "Other"})
    )
    put = await client.post("/api/profile-board", json={"draft_id": draft.id})

    person = f"{NAME} is the name of the copy pinned from Guji v1. Choose another name."
    assert landing["plain"]["refused"] == person
    assert check["refused"] == person
    assert put.status_code == 409
    assert error(put)["message"] == (
        f"{NAME} is the name of the copy pinned from Guji v1. "
        "Choose another name for a new profile."
    )
    assert int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board")) == rows
    assert (
        int(
            await app.state.db.fetch_value(
                "SELECT COUNT(*) FROM profile_board "
                "WHERE deleted_at IS NULL AND current_version_id = ?",
                (made["profile_version_id"],),
            )
        )
        == 0
    )


async def test_keeping_the_machines_name_in_a_both_sides_name_conflict_touches_nothing_else(
    adopted: Adopted,
) -> None:
    """A conflict about the name alone adopts only the name: the Recipe's profile, its Recipe
    versions and the pin stay exactly as they were."""
    from gaggiclanker.db.repos.profile_board import BoardRowPatch

    app, client, fake = adopted
    row = await _pinned_row(app, client)
    await pull(app)  # the copy reaches the machine
    row_id = row["row"]["id"]
    await app.state.board.board.update(row_id, BoardRowPatch(label="Pinned renamed here"))
    next(p for p in fake.profiles if p["label"] == NAME)["label"] = "Pinned renamed there"
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row_id}/conflict"))
    assert (seen["app_name"], seen["machine_name"]) == (
        "Pinned renamed here",
        "Pinned renamed there",
    )
    db = app.state.db

    async def state() -> tuple[Any, ...]:
        return (
            [tuple(r) for r in await db.fetch_all("SELECT * FROM recipe_versions ORDER BY id")],
            [tuple(r) for r in await db.fetch_all("SELECT * FROM recipes ORDER BY id")],
            [tuple(r) for r in await db.fetch_all("SELECT * FROM recipe_version_pins ORDER BY id")],
            [
                tuple(r)
                for r in await db.fetch_all(
                    "SELECT id, current_version_id, previous_version_id, failed_version_id, "
                    "pending_draft_id, back_from_version_id FROM profile_board ORDER BY id"
                )
            ],
        )

    before = await state()

    kept = await client.post(
        f"/api/profile-board/{row_id}/conflict",
        json={"keep": "machine", "content_hash": seen["machine"]["content_hash"]},
    )

    assert kept.status_code == 200, kept.text
    body = data(kept)
    assert body["label"] == "Pinned renamed there" and body["device_label"] == body["label"]
    assert await state() == before
