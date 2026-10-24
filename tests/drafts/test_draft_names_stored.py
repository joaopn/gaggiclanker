"""A draft stores its own name, and the name it shows is the stored one."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, base_version_id
from tests.drafts.helpers import manual_draft
from tests.drafts.test_board import adopted

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


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
