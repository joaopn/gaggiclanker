"""A draft stores what it is: a new profile with a name, or a change.

A change goes to the profile stored on it; with none stored (its profile was not on the list when
the draft was made, as when Writes has never been on) it goes to whichever profile owns its base
version when it is read. A new profile is only ever asked for.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, base_version_id, data
from tests.drafts.helpers import assert_versions_owned, draft_of, manual_draft, same_name_draft
from tests.drafts.test_board import adopted, get_board, pull, put, row_for
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _stored(app: FastAPI, draft_id: int) -> tuple[int, int | None]:
    row = await app.state.db.fetch_one(
        "SELECT new_profile, destination_board_id FROM profile_drafts WHERE id = ?", (draft_id,)
    )
    return int(row["new_profile"]), row["destination_board_id"]


async def test_a_change_drafted_before_the_list_has_its_profile_is_a_version_of_it_later(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """Writes off, the shipped default: no profile row exists yet when the draft is made."""
    app, client = live
    assert data(await client.get("/api/profile-board"))["rows"] == []
    draft = await manual_draft(app, client, BASE_LABEL, BASE_LABEL, 7)
    assert await _stored(app, draft["id"]) == (0, None)

    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    await pull(app)  # the list adopts the machine
    board = data(await client.get("/api/profile-board"))
    [landing] = [item for item in board["landings"] if item["draft_id"] == draft["id"]]
    row = row_for(board, BASE_LABEL)["row"]
    assert landing["plain"]["refused"] is None and landing["plain"]["row_id"] == row["id"]

    put_row = await put(client, draft)

    assert put_row["id"] == row["id"] and put_row["label"] == BASE_LABEL
    assert len(data(await client.get("/api/profile-board"))["rows"]) == len(board["rows"])
    await assert_versions_owned(app.state.db)


async def test_a_new_profile_is_only_ever_asked_for(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    base = await base_version_id(app, BASE_LABEL)
    version = data(await client.get(f"/api/profile-versions/{row['current_version_id']}"))
    document = copy.deepcopy(version["profile"])
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": 7, "flow": 0}

    change = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    fork = await draft_of(app, client, provider, BASE_LABEL, 8)
    renamed = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    asked = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=document, new_profile_name="Asked for"
    )
    scratch = await app.state.draft_proposals.create_manual(
        base_version_id=base, document={**document, "label": "From zero"}, is_new=True
    )
    editor_change = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": base, "profile": {**document, "label": BASE_LABEL.upper()}},
    )

    assert await _stored(app, change["id"]) == (0, row["id"])
    assert await _stored(app, fork["id"]) == (1, None)
    assert await _stored(app, renamed["id"]) == (1, None)
    assert await _stored(app, asked.id) == (1, None)
    assert await _stored(app, scratch.id) == (1, None)
    assert await _stored(app, data(editor_change)["id"]) == (0, row["id"])


async def test_a_refinement_keeps_what_its_parent_was(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    fork = await draft_of(app, client, provider, BASE_LABEL, 8.5)
    refined = data(
        await client.post(f"/api/profile-drafts/{fork['id']}/refine", json={"notes": "more"})
    )
    assert await _stored(app, refined["id"]) == (1, None)


@pytest.mark.parametrize("seen_by", ["landing", "put"])
async def test_a_change_with_no_profile_stored_follows_its_base_to_the_profile_that_owns_it(
    adopted: Adopted, provider: FakeProvider, seen_by: str
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    change = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    await app.state.db.execute(
        "UPDATE profile_drafts SET destination_board_id = NULL WHERE id = ?", (change["id"],)
    )

    if seen_by == "landing":
        board = await get_board(client)
        [landing] = [i for i in board["landings"] if i["draft_id"] == change["id"]]
        assert landing["plain"]["row_id"] == row["id"]
    else:
        assert (await put(client, change))["id"] == row["id"]


async def test_the_standing_and_the_put_agree_on_where_a_change_goes(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    change = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    standing = data(await client.get(f"/api/profile-drafts/{change['id']}/standing"))
    assert standing["landing"]["plain"]["row_id"] == row["id"]
    await put(client, change)
    after: Any = data(await client.get(f"/api/profile-drafts/{change['id']}/standing"))
    assert after["row_id"] == row["id"]


async def test_an_imported_shot_is_linked_by_its_profile_name_with_writes_off(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The list has never adopted the machine, so no profile row exists: the name finds the
    mirrored version that carries it."""
    import json
    from pathlib import Path

    app, client = live
    export = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "exports" / "shot-129.json").read_text()
    )
    export["profile"] = BASE_LABEL
    export["profileId"] = "goneGone12"

    response = await client.post(
        "/api/import",
        files=[("files", ("shot.json", json.dumps(export).encode(), "application/json"))],
    )

    assert response.status_code < 300, response.text
    linked = await app.state.db.fetch_value(
        "SELECT pv.label FROM shots s JOIN profile_versions pv ON pv.id = s.profile_version_id"
    )
    assert linked == BASE_LABEL
    assert data(await client.get("/api/profile-board"))["rows"] == []


async def test_a_database_from_before_the_list_boots_with_its_changes_still_changes(
    env: Any,
) -> None:
    """The list is filled at the first start; a change drafted before it goes to the profile the
    fill makes of its base, with nothing stored on it to say so."""
    from gaggiclanker.db.connection import Database
    from gaggiclanker.db.schema import create_schema
    from tests.conftest import running_app

    env.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(env.database_path)
    await db.connect()
    await create_schema(db)
    from gaggiclanker.domain.models import Profile, canonical_document_json, document_hash
    from tests.drafts.conftest import profile_fixture

    for vid, source, temperature in ((1, "device", 93), (2, "draft", 91)):
        profile = Profile.model_validate(
            {**profile_fixture("docs-medium-18g"), "label": "Espresso", "temperature": temperature}
        )
        await db.execute(
            "INSERT INTO profile_versions (id, content_hash, label, type, json, source) "
            "VALUES (?, ?, 'Espresso', 'pro', ?, ?)",
            (vid, document_hash(profile), canonical_document_json(profile), source),
        )
    await db.execute(
        "INSERT INTO profile_board (id, label, current_version_id, device_profile_id, "
        "device_version_id, origin) VALUES (1, 'Espresso', 1, 'f1', 1, 'adopted')"
    )
    await db.execute(
        "INSERT INTO profile_drafts (id, base_version_id, draft_version_id, label, status) "
        "VALUES (1, 1, 2, 'Espresso', 'draft')"
    )
    await db.close()

    async with running_app(env) as (app, client):
        standing = data(await client.get("/api/profile-drafts/1/standing"))
        assert standing["landing"]["plain"]["row_id"] == 1
        assert standing["landing"]["plain"]["refused"] is None
        await assert_versions_owned(app.state.db)
