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
from tests.drafts.test_board import adopted, app_row, approve, get_board, pull, put
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
        assert found["plain"] == {"row_id": None, "row_label": None, "holds_newer_draft": False}
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
    assert landing(board, older)["plain"] == {
        "row_id": row["id"],
        "row_label": APP_LABEL,
        "holds_newer_draft": False,
    }
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


async def test_a_draft_made_later_from_the_same_document_counts_as_newer(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    older = await draft_of(app, client, provider, BASE_LABEL, 7)
    await approve(client, older)
    repo = ProfileDraftsRepository(app.state.db)
    version = older["draft_version_id"]
    assert await repo.has_newer_on_version(version, after_id=older["id"] - 1) is True
    assert await repo.has_newer_on_version(version, after_id=older["id"]) is False


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
