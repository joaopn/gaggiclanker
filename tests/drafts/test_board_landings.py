"""Where a put of an approved draft would land, as the board read says it.

The same ``_lineage_row`` a put runs decides it; these scenarios are the ones a page got wrong
when it guessed (two variants of a profile of yours are two new profiles, not one overtaking
the other).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import BoardRowPatch, ProfileBoardRepository
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data
from tests.drafts.test_board import (
    adopted,
    app_row,
    approve,
    get_board,
    pull,
    put,
    row_for,
)
from tests.drafts.test_replace import APP_LABEL, Live, draft_of, make_set_on
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module


async def BoardRowRepoPatch(app: FastAPI, row_id: int, **fields: Any) -> None:
    await ProfileBoardRepository(app.state.db).update(row_id, BoardRowPatch(**fields))


async def draft_from(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider, row: dict[str, Any], bar: float
) -> dict[str, Any]:
    """A draft of the board row's own version (not of the newest version with its label)."""
    version = data(await client.get(f"/api/profile-versions/{row['current_version_id']}"))
    document = dict(version["profile"])
    document["phases"] = [dict(p) for p in document["phases"]]
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    provider.script = [json.dumps({"profile": document, "change_summary": f"{bar} bar."})]
    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": row["current_version_id"], "notes": "change it"},
    )
    return dict(data(response))


def landing(board: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    [found] = [x for x in board["landings"] if x["draft_id"] == draft["id"]]
    return dict(found)


async def test_two_variants_of_a_profile_of_yours_each_go_on_as_a_new_profile(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    await pull(app)
    first = await draft_of(app, client, provider, BASE_LABEL, 7)
    second = await draft_of(app, client, provider, BASE_LABEL, 6)
    await approve(client, first)
    await approve(client, second)

    board = await get_board(client)

    for draft in (first, second):
        found = landing(board, draft)
        assert (found["plain"]["row_id"], found["plain"]["holds_newer_draft"]) == (None, False)
        assert found["plain"]["revives_label"] is None
        assert found["for_set"] is None


async def test_an_older_draft_of_an_app_profile_is_told_its_row_holds_a_newer_one(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    older = await draft_from(app, client, provider, row, 7)
    newer = await draft_from(app, client, provider, row, 6)
    await approve(client, older)
    await approve(client, newer)

    # Both continue the app's profile, and neither is newer than the other yet.
    board = await get_board(client)
    first = landing(board, older)["plain"]
    assert (first["row_id"], first["row_label"], first["holds_newer_draft"]) == (
        row["id"],
        APP_LABEL,
        False,
    )
    assert landing(board, newer)["plain"]["holds_newer_draft"] is False

    # A row that waits on a newer draft: putting the older one would undo it.
    await BoardRowRepoPatch(app, row["id"], pending_draft_id=newer["id"])
    board = await get_board(client)
    assert landing(board, older)["plain"]["holds_newer_draft"] is True
    assert landing(board, older)["plain"]["row_id"] == row["id"]


async def test_once_the_newer_draft_is_on_the_board_the_older_one_says_it_would_undo_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """What a put does is what the read says, even when that is not what a page would guess."""
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    older = await draft_from(app, client, provider, row, 7)
    newer = await draft_from(app, client, provider, row, 6)
    await approve(client, older)
    await approve(client, newer)
    await client.post("/api/profile-board", json={"draft_id": newer["id"]})

    said = landing(await get_board(client), older)["plain"]

    assert said["row_id"] == row["id"] and said["holds_newer_draft"] is True
    # ...which is exactly what a put of it would do: undo the newer one on the same row.
    assert (await put(client, older))["id"] == row["id"]


async def test_a_restaged_copy_of_a_document_is_not_the_draft_that_holds_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    first = await draft_of(app, client, provider, BASE_LABEL, 7)
    again = await draft_of(app, client, provider, BASE_LABEL, 7)  # the same document, later
    assert first["draft_version_id"] == again["draft_version_id"]
    await approve(client, first)
    await approve(client, again)
    repo = ProfileDraftsRepository(app.state.db)
    version = first["draft_version_id"]

    assert await repo.first_draft_on_version(version) == first["id"]

    # A discarded draft holds nothing: the holder is then the next live one.
    await client.post(f"/api/profile-drafts/{first['id']}/discard")
    assert await repo.first_draft_on_version(version) == again["id"]
    await client.post(f"/api/profile-drafts/{again['id']}/discard")
    assert await repo.first_draft_on_version(version) is None


async def test_a_draft_that_restaged_the_rows_document_does_not_make_a_newer_draft_look_old(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    holder = await draft_from(app, client, provider, row, 6)
    await approve(client, holder)
    await BoardRowRepoPatch(app, row["id"], current_version_id=holder["draft_version_id"])
    current = dict(row) | {"current_version_id": holder["draft_version_id"]}
    newer = await draft_from(app, client, provider, current, 5)
    restaged = await draft_from(app, client, provider, current, 6)  # the holder's document again
    assert restaged["draft_version_id"] == holder["draft_version_id"]
    await approve(client, newer)
    await approve(client, restaged)

    found = landing(await get_board(client), newer)["plain"]

    assert found["row_id"] == row["id"] and found["holds_newer_draft"] is False


async def test_a_row_made_by_a_newer_draft_holds_it_and_a_discarded_one_holds_nothing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    older = await draft_from(app, client, provider, row, 7)
    newer = await draft_from(app, client, provider, row, 6)
    await approve(client, older)
    await approve(client, newer)
    # The row now stands on the newer draft's document, with nothing pending.
    await BoardRowRepoPatch(app, row["id"], current_version_id=newer["draft_version_id"])
    # (its base file is still the row's file, so the older draft still finds the row)
    assert landing(await get_board(client), older)["plain"]["holds_newer_draft"] is True

    await client.post(f"/api/profile-drafts/{newer['id']}/discard")

    assert landing(await get_board(client), older)["plain"]["holds_newer_draft"] is False


async def test_no_landing_for_a_draft_already_on_the_board(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 7)
    await put(client, draft)

    assert all(x["draft_id"] != draft["id"] for x in (await get_board(client))["landings"])


async def test_no_landings_before_the_board_is_adopted(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    await client.patch("/api/settings", json={"deviceWritesEnabled": False})
    await pull(app)  # the mirror, with no adoption
    draft = await draft_of(app, client, provider, BASE_LABEL, 7)
    await approve(client, draft)

    board = await get_board(client, live=False)

    assert board["adopted"] is False and board["landings"] == []


async def test_a_second_profile_beside_one_with_the_same_label_says_so(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)  # the app's "9 Bar Espresso [AI]"
    variant = await draft_of(app, client, provider, BASE_LABEL, 7)  # of the person's own: [AI] too
    await approve(client, variant)

    found = landing(await get_board(client), variant)["plain"]

    assert found["row_id"] is None
    assert found["beside_label"] == APP_LABEL and found["revives_label"] is None


async def _deleted_app_row_waiting(
    app: FastAPI,
    client: httpx.AsyncClient,
    fake: FakeDevice,
    provider: FakeProvider,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """An app row deleted on the board while its file is still on the machine."""
    row = await app_row(app, client, fake, provider, 8)
    assert (await client.delete(f"/api/profile-board/{row['id']}")).status_code == 200
    again = await draft_from(app, client, provider, row, 8)  # the very same document
    assert again["draft_version_id"] == row["current_version_id"]
    await approve(client, again)
    return row, again


async def test_a_put_that_brings_a_deleted_profile_back_says_so(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row, again = await _deleted_app_row_waiting(app, client, fake, provider)

    found = landing(await get_board(client), again)["plain"]

    assert found["revives_label"] == row["label"] and found["row_id"] is None
    assert (await put(client, again))["id"] == row["id"], "the same row, back"


async def test_a_revival_onto_a_file_a_live_row_holds_is_a_new_row_never_an_error(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row, again = await _deleted_app_row_waiting(app, client, fake, provider)
    other = row_for(await get_board(client), BASE_LABEL)["row"]
    # A live row now stands on the file the deleted row still names (the index allows it:
    # only live rows are unique).
    [waiting] = (await get_board(client))["pending_removals"]
    await BoardRowRepoPatch(app, other["id"], device_profile_id=waiting["device_profile_id"])

    found = landing(await get_board(client), again)["plain"]
    assert found["revives_label"] is None

    response = await client.post("/api/profile-board", json={"draft_id": again["id"]})
    assert response.status_code == 201, response.text
    assert data(response)["id"] not in (row["id"], other["id"])


async def test_the_landing_is_what_a_put_then_does(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    draft = await draft_of(app, client, provider, APP_LABEL, 7)
    await approve(client, draft)
    said = landing(await get_board(client), draft)["plain"]

    put_row = await put(client, draft)

    assert said["row_id"] == put_row["id"] == row["id"]


async def test_a_set_draft_lands_by_the_sets_current_version_when_recorded_for_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    set_id = await make_set_on(client, "Landing set", row["current_version_id"])
    draft = await draft_of(app, client, provider, BASE_LABEL, 7)
    await app.state.db.execute(
        "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, draft["id"])
    )
    await approve(client, draft)

    found = landing(await get_board(client), draft)

    # Without the Set it is a variant of the person's own profile: a new profile. Recorded for
    # the Set it continues the app's profile the Set brews.
    assert found["plain"]["row_id"] is None
    assert found["for_set"]["row_id"] == row["id"]
    assert (await put(client, draft, set_id=set_id))["id"] == row["id"]
