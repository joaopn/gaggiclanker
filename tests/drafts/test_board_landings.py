"""Where a put of an approved draft would land, as the board read says it.

The same ``place_draft`` a put runs decides it; these scenarios are the ones a page got wrong
when it guessed (two variants of a profile of yours are two new profiles, not one overtaking
the other).
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.lineage import person_taken_sentence, taken_name_sentence
from gaggiclanker.db.repos.profile_board import (
    BoardRowPatch,
    BoardRowWrite,
    ProfileBoardRepository,
)
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import APP_LABEL, Live, draft_of, make_set_on, same_name_draft, tombstone
from tests.drafts.test_board import (
    adopted,
    app_row,
    approve,
    draft_from,
    get_board,
    pull,
    put,
    row_for,
    variant_draft,
)
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module


async def BoardRowRepoPatch(app: FastAPI, row_id: int, **fields: Any) -> None:
    await ProfileBoardRepository(app.state.db).update(row_id, BoardRowPatch(**fields))


def landing(board: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    [found] = [x for x in board["landings"] if x["draft_id"] == draft["id"]]
    return dict(found)


async def test_two_variants_of_a_profile_of_yours_with_labels_of_their_own_each_go_on_as_new(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    await pull(app)
    first = await variant_draft(app, client, "Variant one", 7)
    second = await variant_draft(app, client, "Variant two", 6)
    await approve(app, first)
    await approve(app, second)

    board = await get_board(client)

    for draft in (first, second):
        found = landing(board, draft)
        assert found["plain"]["row_id"] is None
        assert found["plain"]["revives_label"] is None
        assert found["for_set"] is None and found["already_on_board_label"] is None


async def test_the_second_of_two_new_drafts_with_one_name_is_refused_once_the_first_is_a_profile(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    """Both drafts were made while the name was free. Once the first is put, the name is a
    profile: the second would land on it (a version nobody asked for), so its put is refused in
    the creation guard's sentence and the list keeps one profile of the name."""
    app, client = writes_on
    await pull(app)
    first = await draft_of(app, client, provider, BASE_LABEL, 7)
    second = await draft_of(app, client, provider, BASE_LABEL, 6)
    await approve(app, first)
    await approve(app, second)
    assert landing(await get_board(client), second)["plain"]["row_id"] is None

    await put(client, first)
    refused = await client.post("/api/profile-board", json={"draft_id": second["id"]})

    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence(APP_LABEL)
    labels = [r["row"]["label"] for r in (await get_board(client))["rows"]]
    assert labels.count(APP_LABEL) == 1


async def test_two_changes_of_a_persons_profile_that_keep_its_name_are_both_versions_of_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    first = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    second = await same_name_draft(app, client, provider, BASE_LABEL, 6)
    await approve(app, first)
    await approve(app, second)

    board = await get_board(client)

    for draft in (first, second):
        found = landing(board, draft)["plain"]
        assert found["row_id"] == row["id"]
    await put(client, first)
    assert (await put(client, second))["id"] == row["id"]


async def test_a_draft_whose_document_is_already_on_the_board_says_so_and_a_put_is_refused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    on_board = await draft_of(app, client, provider, BASE_LABEL, 7)
    again = await draft_of(app, client, provider, BASE_LABEL, 7)  # the same document, later
    assert on_board["draft_version_id"] == again["draft_version_id"]
    await approve(app, again)
    await put(client, on_board)

    board = await get_board(client)

    # The draft the row stands on, and a later draft of the same document, both: nothing to put.
    for draft in (on_board, again):
        found = landing(board, draft)
        assert found["already_on_board_label"] == APP_LABEL
        assert found["plain"]["row_id"] is None
    refused = await client.post("/api/profile-board", json={"draft_id": again["id"]})
    assert refused.status_code == 409
    assert "already in the list" in error(refused)["message"]


async def test_no_landings_before_the_board_is_adopted(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    await client.patch("/api/settings", json={"deviceWritesEnabled": False})
    await pull(app)  # the mirror, with no adoption
    draft = await draft_of(app, client, provider, BASE_LABEL, 7)
    await approve(app, draft)

    board = await get_board(client, live=False)

    assert board["adopted"] is False and board["landings"] == []


async def test_a_fork_named_like_an_app_profile_continues_that_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)  # the app's "9 Bar Espresso [AI]"
    variant = await draft_of(app, client, provider, BASE_LABEL, 7)  # a fork named like it
    await approve(app, variant)

    found = landing(await get_board(client), variant)["plain"]

    assert found["row_id"] == row["id"]
    rows = len((await get_board(client))["rows"])
    assert (await put(client, variant))["id"] == row["id"]
    assert len((await get_board(client))["rows"]) == rows


async def test_a_set_draft_renamed_onto_a_name_that_became_a_profile_is_refused_by_landing_and_put(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The refusal is first at creation. A name that becomes a profile after the draft was made
    (the race) is refused the same way when the page's landing is read and when the put runs: a
    rename never lands on a profile it is not a version of, for the Set or without it."""
    app, client, fake = adopted
    first = await app_row(app, client, fake, provider, 8, name="First")
    set_id = await make_set_on(client, "Rename set", first["current_version_id"])
    version = data(await client.get(f"/api/profile-versions/{first['current_version_id']}"))
    document = dict(version["profile"])
    document["label"] = "Second"
    renamed = dict(
        data(
            await client.post(
                "/api/profile-drafts",
                json={
                    "base_version_id": first["current_version_id"],
                    "profile": document,
                    "change_summary": "rename",
                },
            )
        )
    )
    second = await app_row(app, client, fake, provider, 7, name="Second")
    await app.state.db.execute(
        "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, renamed["id"])
    )
    await approve(app, renamed)

    found = landing(await get_board(client), renamed)

    sentence = taken_name_sentence("Second")
    assert found["plain"]["refused"] == found["for_set"]["refused"]
    assert found["plain"]["refused"] == person_taken_sentence("Second")
    assert found["plain"]["row_id"] is None and found["for_set"]["row_id"] is None
    for body in ({}, {"set_id": set_id}):
        refused = await client.post("/api/profile-board", json={"draft_id": renamed["id"], **body})
        assert refused.status_code == 409 and error(refused)["message"] == sentence
    assert second["id"] in [r["row"]["id"] for r in (await get_board(client))["rows"]]
    labels = [r["row"]["label"] for r in (await get_board(client))["rows"]]
    assert len(labels) == len(set(labels))


async def test_a_put_that_keeps_its_rows_label_is_never_refused_for_a_pair_that_already_exists(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    # An older pair sharing a label (the machine held two when the board took it).
    await app.state.board.board.insert(
        BoardRowWrite(
            label=row["label"], current_version_id=row["current_version_id"], origin="adopted"
        )
    )
    newer = await draft_from(app, client, provider, row, 6)
    await approve(app, newer)

    found = landing(await get_board(client), newer)["plain"]
    assert found["row_id"] == row["id"]
    assert (await put(client, newer))["id"] == row["id"]


async def _deleted_app_row_waiting(
    app: FastAPI,
    client: httpx.AsyncClient,
    fake: FakeDevice,
    provider: FakeProvider,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """An app row deleted on the board while its file is still on the machine."""
    row = await app_row(app, client, fake, provider, 8)
    assert (await tombstone(client, row["id"])).status_code == 200
    again = await draft_from(app, client, provider, row, 8)  # the very same document
    assert again["draft_version_id"] == row["current_version_id"]
    await approve(app, again)
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
    await approve(app, draft)
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
    await approve(app, draft)

    found = landing(await get_board(client), draft)

    # A fork named like the app's profile the Set brews: it continues that profile, for the Set
    # or not, because that is the profile with exactly its name.
    assert found["plain"]["row_id"] == row["id"]
    assert found["for_set"]["row_id"] == row["id"]
    assert (await put(client, draft, set_id=set_id))["id"] == row["id"]
