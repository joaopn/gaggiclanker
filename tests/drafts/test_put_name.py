"""Answering a proposal where it is shown: the put takes a name for a new profile, and approving
switches the profile on.

The name is stored with the approval, in the put's own transaction, through the constructor
every draft document goes through; ``place_draft`` decides everything about it (is this a new
profile, is the name taken). A refusal leaves the draft and the board exactly as they were.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.lineage import taken_name_sentence
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import manual_draft, same_name_draft, tombstone
from tests.drafts.test_board import (
    _carried_from,
    _confirmed_signature,
    adopted,
    get_board,
    pull,
    put,
    row_for,
    write_frames,
)
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _draft(client: httpx.AsyncClient, draft_id: int) -> dict[str, Any]:
    return dict(data(await client.get(f"/api/profile-drafts/{draft_id}")))


async def _version_count(app: FastAPI) -> int:
    return int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_versions"))


async def _snapshot(app: FastAPI, client: httpx.AsyncClient, draft_id: int) -> Any:
    """Everything a refused put must leave alone."""
    detail = await _draft(client, draft_id)
    board = await get_board(client)
    return (
        detail["draft"]["status"],
        detail["draft"]["draft_version_id"],
        detail["draft"]["draft_label"],
        [
            (r["row"]["id"], r["row"]["label"], r["row"]["current_version_id"])
            for r in board["rows"]
        ],
        await _version_count(app),
    )


async def test_a_new_profile_is_put_under_the_name_the_person_typed(adopted: Adopted) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    before = await _draft(client, draft["id"])

    row = await put(client, draft, label="  Gentle Bloom  ")

    assert row["label"] == "Gentle Bloom", "the surrounding whitespace is not stored"
    after = await _draft(client, draft["id"])
    assert after["draft"]["status"] == "approved"
    assert after["draft"]["draft_label"] == "Gentle Bloom"
    assert after["draft"]["draft_version_id"] == row["current_version_id"]
    assert after["draft"]["draft_version_id"] != before["draft"]["draft_version_id"]
    assert after["draft_profile"]["label"] == "Gentle Bloom"
    # Everything but the name is the document that was proposed.
    assert {k: v for k, v in after["draft_profile"].items() if k != "label"} == {
        k: v for k, v in before["draft_profile"].items() if k != "label"
    }
    assert row_for(await get_board(client), "Gentle Bloom")["row"]["id"] == row["id"]


async def test_a_name_that_is_the_one_it_was_proposed_under_changes_nothing(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    versions = await _version_count(app)

    row = await put(client, draft, label="Soft Bloom")

    assert row["label"] == "Soft Bloom"
    assert await _version_count(app) == versions, "the same document is the same version"


async def test_a_renamed_document_goes_through_the_clamp_like_any_draft_document(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    await app.state.settings_service.apply({"profilePolicyPressureMaxBar": 6})

    # The bounds tightened after drafting: the renamed document is built under today's bounds,
    # as a new draft would be, so it is not stored with a pressure the policy now forbids.
    row = await put(client, draft, label="Gentle Bloom")

    stored = data(await client.get(f"/api/profile-versions/{row['current_version_id']}"))
    pumps = [p["pump"] for p in stored["profile"]["phases"] if p["pump"]["target"] == "pressure"]
    assert pumps and all(p["pressure"] <= 6 for p in pumps)


async def test_an_empty_name_is_refused_and_nothing_changes(adopted: Adopted) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    before = await _snapshot(app, client, draft["id"])

    for typed in ("", "   "):
        refused = await client.post(
            "/api/profile-board", json={"draft_id": draft["id"], "label": typed}
        )
        assert refused.status_code == 422
        assert error(refused)["message"] == "A profile needs a name"

    assert await _snapshot(app, client, draft["id"]) == before


async def test_a_taken_name_is_refused_with_the_one_sentence_and_nothing_changes(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    other = await put(client, await manual_draft(app, client, BASE_LABEL, "Other One", 7))
    before = await _snapshot(app, client, draft["id"])

    for typed in ("Other One", "  other one "):
        refused = await client.post(
            "/api/profile-board", json={"draft_id": draft["id"], "label": typed}
        )
        assert refused.status_code == 409
        assert error(refused)["message"] == taken_name_sentence(typed.strip())
    assert other["label"] == "Other One"

    assert await _snapshot(app, client, draft["id"]) == before


async def test_a_change_to_an_existing_profile_keeps_its_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    change = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    before = await _snapshot(app, client, change["id"])

    refused = await client.post(
        "/api/profile-board", json={"draft_id": change["id"], "label": "Another name"}
    )

    assert refused.status_code == 422
    assert error(refused)["message"] == "A change to an existing profile keeps its name"
    assert await _snapshot(app, client, change["id"]) == before


async def test_a_rename_that_would_continue_a_profile_is_refused_as_taken(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    # A fork under another name is a profile of its own; typing its base's name would silently
    # make it a version of that profile, so the same refusal as a taken name applies.
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    before = await _snapshot(app, client, draft["id"])

    refused = await client.post(
        "/api/profile-board", json={"draft_id": draft["id"], "label": BASE_LABEL}
    )

    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence(BASE_LABEL)
    assert await _snapshot(app, client, draft["id"]) == before


async def test_two_puts_of_one_draft_under_different_names_one_wins(adopted: Adopted) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    rows = len((await get_board(client))["rows"])
    versions = await _version_count(app)

    first, second = await asyncio.gather(
        client.post("/api/profile-board", json={"draft_id": draft["id"], "label": "Name A"}),
        client.post("/api/profile-board", json={"draft_id": draft["id"], "label": "Name B"}),
    )

    assert sorted([first.status_code, second.status_code]) == [201, 409]
    loser = first if first.status_code == 409 else second
    winner = second if loser is first else first
    assert "already in the list" in error(loser)["message"]
    board = await get_board(client)
    assert len(board["rows"]) == rows + 1, "no orphan row"
    named = data(winner)["label"]
    assert named in ("Name A", "Name B")
    assert (await _draft(client, draft["id"]))["draft"]["draft_label"] == named
    # The loser's rename was undone with its transaction: one new version, not two.
    assert await _version_count(app) == versions + 1


async def test_approving_switches_a_profile_that_is_off_back_on_and_keeps_its_star(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    assert (
        await client.put(f"/api/profile-board/{row['id']}/on-machine", json={"on": False})
    ).is_success
    assert (
        await client.put(f"/api/profile-board/{row['id']}/starred", json={"starred": False})
    ).is_success
    change = await same_name_draft(app, client, provider, BASE_LABEL, 8)

    put_row = await put(client, change)

    assert put_row["id"] == row["id"]
    assert put_row["on_machine"] is True
    assert put_row["on_home_screen"] is False, "Starred keeps its value"


async def test_a_new_profile_is_on_and_approving_with_writes_off_only_stores_it(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    await client.put(f"/api/profile-board/{row['id']}/on-machine", json={"on": False})
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    change = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    new = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 7)
    fake.ws_requests.clear()

    switched = await put(client, change)
    fresh = await put(client, new, label="Gentle Bloom")

    assert switched["on_machine"] is True and fresh["on_machine"] is True
    run = await pull(app)
    assert run.status == "ok", run.error
    assert write_frames(fake) == [], "with Writes off the approval is only stored"


async def test_the_signature_in_force_follows_a_rename(adopted: Adopted) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    proposed = (await _draft(client, draft["id"]))["draft"]["draft_version_id"]
    expectation = await _confirmed_signature(app, proposed)

    row = await put(client, draft, label="Gentle Bloom")

    assert row["current_version_id"] != proposed
    assert await _carried_from(app, row["current_version_id"]) == [expectation]


async def test_the_signature_follows_a_rename_onto_a_version_another_draft_made_first(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    # Another draft of the very same document under the new name makes that version first.
    twin = await manual_draft(app, client, BASE_LABEL, "Gentle Bloom", 8)
    twin_version = (await _draft(client, twin["id"]))["draft"]["draft_version_id"]
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    proposed = (await _draft(client, draft["id"]))["draft"]["draft_version_id"]
    expectation = await _confirmed_signature(app, proposed)

    row = await put(client, draft, label="Gentle Bloom")

    assert row["current_version_id"] == twin_version, "the renamed version already existed"
    assert await _carried_from(app, twin_version) == [expectation]


async def test_a_deleted_profile_a_draft_brings_back_is_switched_on(adopted: Adopted) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    row = await put(client, draft)
    assert (await pull(app)).status == "ok"  # the row now stands on a file of its own
    assert (
        await client.put(f"/api/profile-board/{row['id']}/on-machine", json={"on": False})
    ).is_success
    await tombstone(client, row["id"])
    again = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)  # the same document

    back = await put(client, again)

    assert back["id"] == row["id"] and back["deleted_at"] is None, "the same row came back"
    assert back["on_machine"] is True
