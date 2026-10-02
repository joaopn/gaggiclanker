"""Putting a draft on the board is one action: approval, acknowledgement and the put together.

The staged box had an Approve and then a Push; the board has a single "Put on the board",
which carries what Approve carried (the stop-condition acknowledgement) and what the push
carried (the Set, and whether it is a major version). Every refusal here must leave the draft
as it was: a refused put that had half approved it would be a draft the card no longer
offers a click for.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, base_profile, base_version_id, data, error
from tests.drafts.helpers import APP_LABEL, draft_of, make_set_on, set_device_ids
from tests.drafts.test_board import (
    adopted,
    approve,
    get_board,
    pull,
    put,
    summary_of,
    variant_draft,
    write_frames,
)
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module


async def draft_status(client: httpx.AsyncClient, draft: dict[str, Any]) -> dict[str, Any]:
    return dict(data(await client.get(f"/api/profile-drafts/{draft['id']}"))["draft"])


async def stop_condition_draft(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider
) -> dict[str, Any]:
    """A draft that moves a stop condition: the pump now stops at another volume."""
    profile = await base_profile(app, BASE_LABEL)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["phases"][0]["targets"] = [{"type": "volumetric", "operator": "gte", "value": 44}]
    provider.script = [json.dumps({"profile": document, "change_summary": "Longer ratio."})]
    draft = dict(
        data(
            await client.post(
                "/api/profile-drafts",
                json={"base_version_id": await base_version_id(app, BASE_LABEL), "notes": "ratio"},
            )
        )
    )
    assert len(draft["stop_condition_changes"]) == 1
    return draft


async def test_one_request_approves_a_drafted_draft_and_puts_it_on_the_board(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    assert draft["status"] == "draft"
    fake.ws_requests.clear()

    row = await put(client, draft)

    assert row["pending_draft_id"] == draft["id"] and row["label"] == APP_LABEL
    stored = await draft_status(client, draft)
    assert stored["status"] == "approved" and stored["acknowledged_stop_changes"] is False
    assert write_frames(fake) == [], "nothing is sent until the sync"
    run = await pull(app)
    assert run.status == "ok" and len(summary_of(run)["pushed"]) == 1
    assert (await draft_status(client, draft))["status"] == "pushed"


async def test_a_stop_condition_change_is_refused_until_it_is_acknowledged_and_nothing_moves(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await stop_condition_draft(app, client, provider)
    rows = len((await get_board(client))["rows"])

    refused = await client.post("/api/profile-board", json={"draft_id": draft["id"]})

    assert refused.status_code == 409
    body = error(refused)
    assert "how much coffee ends up in the cup" in body["message"]
    assert body["details"]["field"] == "acknowledge_stop_changes"
    assert body["details"]["stop_condition_changes"][0]["after"]["value"] == 44
    assert (await draft_status(client, draft))["status"] == "draft", "not half approved"
    assert len((await get_board(client))["rows"]) == rows
    # A falsy acknowledgement is not one.
    again = await client.post(
        "/api/profile-board", json={"draft_id": draft["id"], "acknowledge_stop_changes": False}
    )
    assert again.status_code == 409

    row = await put(client, draft, acknowledge_stop_changes=True)

    assert row["pending_draft_id"] == draft["id"]
    stored = await draft_status(client, draft)
    assert stored["status"] == "approved" and stored["acknowledged_stop_changes"] is True


async def test_a_draft_that_moves_nothing_needs_no_acknowledgement(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    assert draft["stop_condition_changes"] == []

    await put(client, draft)

    assert (await draft_status(client, draft))["acknowledged_stop_changes"] is False


async def test_an_acknowledgement_on_a_draft_that_moves_nothing_is_not_recorded(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)

    await put(client, draft, acknowledge_stop_changes=True)

    assert (await draft_status(client, draft))["acknowledged_stop_changes"] is False


async def test_a_draft_approved_before_the_one_click_put_is_put_as_it_is(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await stop_condition_draft(app, client, provider)
    await app.state.drafts.drafts.set_status(draft["id"], "approved", acknowledged=True)

    row = await put(client, draft)  # its acknowledgement is already on record

    assert row["pending_draft_id"] == draft["id"]


async def test_a_refused_put_leaves_the_draft_drafted(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    first = await draft_of(app, client, provider, BASE_LABEL, 8)
    await put(client, first)
    second = await draft_of(app, client, provider, BASE_LABEL, 7)  # the same label

    refused = await client.post("/api/profile-board", json={"draft_id": second["id"]})

    assert refused.status_code == 409
    assert (await draft_status(client, second))["status"] == "draft", "not approved by a refusal"


async def test_only_a_drafted_or_approved_draft_can_be_put(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    repo = app.state.drafts.drafts
    for status in ("pushed", "failed", "discarded", "superseded"):
        draft = await draft_of(app, client, provider, BASE_LABEL, 8)
        await repo.set_status(draft["id"], status)

        refused = await client.post("/api/profile-board", json={"draft_id": draft["id"]})

        assert refused.status_code == 409, status
        assert f"A {status} proposal cannot be made active" in error(refused)["message"]


async def test_the_sets_version_and_the_major_choice_ride_on_the_same_request(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = await variant_draft(app, client, "For a set", 8)
    first = await put(client, row)
    set_id = await make_set_on(client, "One click set", first["current_version_id"])
    newer = await draft_of(app, client, provider, "For a set [AI]", 7)
    await app.state.db.execute(
        "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, newer["id"])
    )
    await pull(app)
    before = await set_device_ids(app, set_id)

    put_row = await put(client, newer, set_id=set_id, major=True)
    assert put_row["pending_set_id"] == set_id and put_row["pending_major"] is True
    run = await pull(app)

    assert run.status == "ok", run.error
    assert len(await set_device_ids(app, set_id)) == len(before) + 1
    assert (await draft_status(client, newer))["status"] == "pushed"


async def test_the_board_read_offers_a_landing_for_a_drafted_draft(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    drafted = await draft_of(app, client, provider, BASE_LABEL, 8)
    legacy = await draft_of(app, client, provider, BASE_LABEL, 7)
    await approve(app, legacy)

    landings = {x["draft_id"] for x in (await get_board(client))["landings"]}

    assert landings == {drafted["id"], legacy["id"]}
