"""Where a put of an approved draft would land, as the board read says it.

The same ``_lineage_row`` a put runs decides it; these scenarios are the ones a page got wrong
when it guessed (two variants of a profile of yours are two new profiles, not one overtaking
the other).
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import (
    BoardRowPatch,
    BoardRowWrite,
    ProfileBoardRepository,
)
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
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
from tests.drafts.test_replace import APP_LABEL, Live, draft_of, make_set_on
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
        assert (found["plain"]["row_id"], found["plain"]["holds_newer_draft"]) == (None, False)
        assert found["plain"]["revives_label"] is None and found["plain"]["taken_label"] is None
        assert found["for_set"] is None and found["already_on_board_label"] is None


async def test_two_variants_with_one_label_cannot_both_go_on_the_board(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    await pull(app)
    first = await draft_of(app, client, provider, BASE_LABEL, 7)
    second = await draft_of(app, client, provider, BASE_LABEL, 6)
    await approve(app, first)
    await approve(app, second)
    # Neither is refused while the other is not on the board: the landing is per draft.
    board = await get_board(client)
    assert landing(board, second)["plain"]["taken_label"] is None

    await put(client, first)

    found = landing(await get_board(client), second)["plain"]
    assert found["taken_label"] == APP_LABEL and found["row_id"] is None
    refused = await client.post("/api/profile-board", json={"draft_id": second["id"]})
    assert refused.status_code == 409
    assert error(refused)["message"] == (
        f"The board already has {APP_LABEL}; refine this draft from it, or discard it."
    )
    assert error(refused)["details"] == {"reason": "duplicate_label"}


async def test_an_older_draft_of_an_app_profile_is_told_its_row_holds_a_newer_one(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    older = await draft_from(app, client, provider, row, 7)
    newer = await draft_from(app, client, provider, row, 6)
    await approve(app, older)
    await approve(app, newer)

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
    await approve(app, older)
    await approve(app, newer)
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
    await approve(app, first)
    await approve(app, again)
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
    await approve(app, holder)
    await BoardRowRepoPatch(app, row["id"], current_version_id=holder["draft_version_id"])
    current = dict(row) | {"current_version_id": holder["draft_version_id"]}
    newer = await draft_from(app, client, provider, current, 5)
    restaged = await draft_from(app, client, provider, current, 6)  # the holder's document again
    assert restaged["draft_version_id"] == holder["draft_version_id"]
    await approve(app, newer)
    await approve(app, restaged)

    found = landing(await get_board(client), newer)["plain"]

    assert found["row_id"] == row["id"] and found["holds_newer_draft"] is False


async def test_a_row_made_by_a_newer_draft_holds_it_and_a_discarded_one_holds_nothing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    older = await draft_from(app, client, provider, row, 7)
    newer = await draft_from(app, client, provider, row, 6)
    await approve(app, older)
    await approve(app, newer)
    # The row now stands on the newer draft's document, with nothing pending.
    await BoardRowRepoPatch(app, row["id"], current_version_id=newer["draft_version_id"])
    # (its base file is still the row's file, so the older draft still finds the row)
    assert landing(await get_board(client), older)["plain"]["holds_newer_draft"] is True

    await client.post(f"/api/profile-drafts/{newer['id']}/discard")

    assert landing(await get_board(client), older)["plain"]["holds_newer_draft"] is False


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
        assert found["plain"]["row_id"] is None and found["plain"]["taken_label"] is None
    refused = await client.post("/api/profile-board", json={"draft_id": again["id"]})
    assert refused.status_code == 409
    assert "already on the board" in error(refused)["message"]


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


async def test_a_variant_beside_an_app_profile_with_the_same_label_is_refused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)  # the app's "9 Bar Espresso [AI]"
    variant = await draft_of(app, client, provider, BASE_LABEL, 7)  # of the person's own: [AI] too
    await approve(app, variant)

    found = landing(await get_board(client), variant)["plain"]

    assert found["row_id"] is None and found["revives_label"] is None
    assert found["taken_label"] == APP_LABEL
    rows = len((await get_board(client))["rows"])
    assert (
        await client.post("/api/profile-board", json={"draft_id": variant["id"]})
    ).status_code == 409
    assert len((await get_board(client))["rows"]) == rows


async def test_a_version_that_renames_its_profile_to_a_taken_label_is_refused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    first = await app_row(app, client, fake, provider, 8, name="First")
    await app_row(app, client, fake, provider, 7, name="Second")
    set_id = await make_set_on(client, "Rename set", first["current_version_id"])
    # A draft for the Set that continues "First [AI]" but is called "Second".
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
    await app.state.db.execute(
        "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, renamed["id"])
    )
    await approve(app, renamed)

    found = landing(await get_board(client), renamed)

    # By lineage it would continue First and rename it onto Second; as a new profile it would
    # be a second "Second". Both are refused.
    assert found["for_set"]["row_id"] is None and found["for_set"]["taken_label"] == "Second [AI]"
    assert found["plain"]["taken_label"] == "Second [AI]"
    refused = await client.post(
        "/api/profile-board", json={"draft_id": renamed["id"], "set_id": set_id}
    )
    assert refused.status_code == 409, refused.text
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

    assert found["taken_label"] is None and found["row_id"] == row["id"]
    assert (await put(client, newer))["id"] == row["id"]


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

    # Without the Set it is a variant of the person's own profile: a new profile. Recorded for
    # the Set it continues the app's profile the Set brews.
    assert found["plain"]["row_id"] is None
    assert found["for_set"]["row_id"] == row["id"]
    assert (await put(client, draft, set_id=set_id))["id"] == row["id"]
