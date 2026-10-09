"""``POST /api/profile-drafts/{id}/name-check``: the Name field's answer as it is typed.

It asks the one placement rule the put asks, so the two must agree on every name: whatever the
check refuses the put refuses with the same sentence, and whatever it lets through the put
accepts. It writes nothing.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.lineage import taken_name_sentence
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import manual_draft, same_name_draft
from tests.drafts.test_board import adopted, put
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def check(client: httpx.AsyncClient, draft: dict[str, Any], label: str) -> httpx.Response:
    return await client.post(f"/api/profile-drafts/{draft['id']}/name-check", json={"label": label})


@pytest.mark.parametrize(
    "typed",
    [
        "Gentle Bloom",
        "  Gentle Bloom  ",
        "Other One",
        "other one",
        " OTHER ONE ",
        BASE_LABEL,
        BASE_LABEL.lower(),
        "Soft Bloom",
        "",
        "   ",
    ],
)
async def test_the_check_and_the_put_agree_on_every_name(adopted: Adopted, typed: str) -> None:
    app, client, _ = adopted
    await put(client, await manual_draft(app, client, BASE_LABEL, "Other One", 7))
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    db = app.state.db
    before = await db.fetch_value("SELECT total_changes()")

    checked = await check(client, draft, typed)

    assert checked.status_code == 200, checked.text
    answer = data(checked)
    assert answer["label"] == typed.strip()
    assert await db.fetch_value("SELECT total_changes()") == before, "the check writes nothing"

    refused = await client.post(
        "/api/profile-board", json={"draft_id": draft["id"], "label": typed}
    )
    if answer["refused"] is None:
        assert refused.status_code == 201, refused.text
    else:
        assert refused.status_code in (409, 422)
        assert error(refused)["message"] == answer["refused"]


async def test_the_sentences_are_the_ones_the_put_uses(adopted: Adopted) -> None:
    app, client, _ = adopted
    await put(client, await manual_draft(app, client, BASE_LABEL, "Other One", 7))
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)

    assert data(await check(client, draft, "Other One"))["refused"] == taken_name_sentence(
        "Other One"
    )
    assert data(await check(client, draft, "  "))["refused"] == "A profile needs a name"
    assert data(await check(client, draft, "Fresh"))["refused"] is None


async def test_a_change_to_a_profile_and_a_proposal_that_is_not_waiting_are_422(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    change = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    answered = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    await put(client, answered)

    for draft in (change, answered):
        refused = await check(client, draft, "Anything")
        assert refused.status_code == 422, refused.text
    assert (
        await client.post("/api/profile-drafts/987654/name-check", json={"label": "x"})
    ).status_code == 404
