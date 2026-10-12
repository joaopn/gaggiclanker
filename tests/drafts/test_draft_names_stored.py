"""A draft stores its own name, and the name it shows is the stored one."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, base_version_id, data
from tests.drafts.helpers import manual_draft
from tests.drafts.test_board import adopted, put

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _stored(app: FastAPI, draft_id: int) -> tuple[str, int | None]:
    row = await app.state.db.fetch_one(
        "SELECT label, destination_board_id FROM profile_drafts WHERE id = ?", (draft_id,)
    )
    return str(row["label"]), row["destination_board_id"]


async def test_a_draft_is_stored_with_the_name_it_was_made_under(adopted: Adopted) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    assert await _stored(app, draft["id"]) == ("Soft Bloom", None)
    assert draft["draft_label"] == "Soft Bloom"


async def test_the_name_a_person_types_when_approving_is_stored_on_the_draft(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    await put(client, draft, label="Gentle Bloom")
    assert (await _stored(app, draft["id"]))[0] == "Gentle Bloom"
    detail = data(await client.get(f"/api/profile-drafts/{draft['id']}"))
    assert detail["draft"]["draft_label"] == "Gentle Bloom"


async def test_a_draft_written_without_a_name_takes_its_documents_then_its_bases(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    drafts = ProfileDraftsRepository(app.state.db)
    base = await base_version_id(app, BASE_LABEL)
    drafted = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    with_document = await drafts.create(
        ProfileDraftWrite(base_version_id=base, draft_version_id=drafted["draft_version_id"])
    )
    without_document = await drafts.create(ProfileDraftWrite(base_version_id=base))
    named = await drafts.create(ProfileDraftWrite(base_version_id=base, label="Chosen"))
    assert with_document.draft_label == "Soft Bloom"
    assert without_document.draft_label == BASE_LABEL
    assert named.draft_label == "Chosen"


async def test_an_old_draft_with_no_name_at_all_is_served_without_one(adopted: Adopted) -> None:
    app, _client, _ = adopted
    drafts = ProfileDraftsRepository(app.state.db)
    base = await base_version_id(app, BASE_LABEL)
    draft = await drafts.create(ProfileDraftWrite(base_version_id=base))
    await app.state.db.execute("UPDATE profile_drafts SET label = '' WHERE id = ?", (draft.id,))
    again = await drafts.get(draft.id)
    assert again is not None and again.draft_label is None
