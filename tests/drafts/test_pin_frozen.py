"""A pinned copy is frozen: it changes only by unpinning it.

The refusal is server side (409 `PIN_FROZEN`, a sentence naming the pin): the switch, the active
version, keeping the machine's side of a conflict, and any draft that would continue the copy.
The star still works, and so does keeping the app's side. The draft refusal lives in the one
function that decides where a draft goes, so drafting, the put and the landing the page shows
cannot disagree.
"""

from __future__ import annotations

import copy as _copy
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import BoardRowWrite
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from tests.drafts.conftest import BASE_LABEL, base_version_id, data, error
from tests.drafts.helpers import make_set_on
from tests.drafts.test_board import adopted, get_board, pull, row_for
from tests.drafts.test_pins import _pin, _set_with_version

__all__ = ["adopted"]  # the fixture, re-exported for this module

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]
NAME = f"{BASE_LABEL} v1"
SENTENCE = f"{NAME} is pinned to Guji v1: unpin it to change it."


async def _pinned_row(app: FastAPI, client: httpx.AsyncClient) -> dict[str, Any]:
    set_id, version_id = await _set_with_version(app, client)
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


async def test_keeping_the_machines_side_of_a_conflict_is_refused_and_keeping_the_apps_works(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = await _pinned_row(app, client)
    row_id = row["row"]["id"]
    await pull(app)  # the copy reaches the machine
    edited = next(p for p in fake.profiles if p["label"] == NAME)
    edited["temperature"] = float(edited.get("temperature", 90)) + 1
    await pull(app)  # the machine's file is not what the app put there: a conflict
    seen = data(await client.get(f"/api/profile-board/{row_id}/conflict"))
    body = {"content_hash": seen["machine"]["content_hash"]}

    machine = await client.post(
        f"/api/profile-board/{row_id}/conflict", json={"keep": "machine", **body}
    )
    app_side = await client.post(
        f"/api/profile-board/{row_id}/conflict", json={"keep": "app", **body}
    )

    assert machine.status_code == 409
    assert (error(machine)["code"], error(machine)["message"]) == ("PIN_FROZEN", SENTENCE)
    assert app_side.status_code == 200, app_side.text


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
    set_id, version_id = await _set_with_version(app, client)
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


async def test_the_name_check_refuses_a_case_variant_the_sets_profile_makes_frozen(
    adopted: Adopted,
) -> None:
    """The sync matches labels exactly, so "x v1" can sit on the list beside the pinned "X v1".

    A Set that brews a version the pinned row lists (the machine's edit of the file, kept) has the
    pinned row as its profile: a waiting draft of that Set, made from the variant, is first placed
    on the variant (not frozen) and then, with the Set, on the pinned row. The put refuses it and
    the name check has to say so too.
    """
    app, client, _fake = adopted
    board = app.state.board
    set_id, version_id = await _set_with_version(app, client)
    data(await _pin(client, set_id, version_id))
    pinned = row_for(await get_board(client), NAME)["row"]
    base = await board.proposals.base_profile(await base_version_id(app, BASE_LABEL))
    lower = NAME.lower()
    variant, _ = await board.profiles.ensure_version(
        base.model_copy(update={"label": lower, "temperature": 91.0}), source="draft", owner=None
    )
    await board.board.insert(
        BoardRowWrite(label=lower, current_version_id=variant.id, origin="adopted")
    )
    edited, _ = await board.profiles.ensure_version(
        base.model_copy(update={"label": NAME, "temperature": 92.0}), source="draft", owner=None
    )
    await board.board.add_version(pinned["id"], edited.id, "edited_on_machine")
    brewing = await make_set_on(client, "Brews the edit", edited.id)
    spin, _ = await board.profiles.ensure_version(
        base.model_copy(update={"label": "Spin-off", "temperature": 89.0}),
        source="draft",
        owner=None,
    )
    draft = await ProfileDraftsRepository(app.state.db).create(
        ProfileDraftWrite(
            base_version_id=variant.id, draft_version_id=spin.id, made_by="edit", set_id=brewing
        )
    )

    check = data(
        await client.post(f"/api/profile-drafts/{draft.id}/name-check", json={"label": lower})
    )
    put = await client.post(
        "/api/profile-board", json={"draft_id": draft.id, "set_id": brewing, "label": lower}
    )

    assert put.status_code == 409
    assert (error(put)["code"], error(put)["message"]) == ("PIN_FROZEN", SENTENCE)
    assert check["refused"] == SENTENCE


async def _a_stored_draft_equal_to_the_copy(app: FastAPI, client: httpx.AsyncClient) -> Any:
    """A pin, and a waiting draft made before it whose document is exactly the pin's copy."""
    set_id, version_id = await _set_with_version(app, client)
    made = data(await _pin(client, set_id, version_id))["pin"]
    base = await base_version_id(app, BASE_LABEL)
    draft = await ProfileDraftsRepository(app.state.db).create(
        ProfileDraftWrite(
            base_version_id=base, draft_version_id=made["profile_version_id"], made_by="edit"
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
