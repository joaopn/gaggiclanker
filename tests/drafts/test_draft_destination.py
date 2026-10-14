"""A draft stores where it goes and what it is called, decided once when it is made.

A change to a profile carries that profile's name and is a version of it; a new profile carries
the name it was given and belongs to no profile until it is put. Nothing works either out from a
document's label afterwards: not a refinement, not the put, not the landing.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, base_profile, base_version_id, data, error
from tests.drafts.helpers import (
    assert_versions_owned,
    draft_of,
    manual_draft,
    same_name_draft,
    tombstone,
)
from tests.drafts.test_board import adopted, get_board, put, row_for
from tests.drafts.test_pins import _pin, _set_with_version
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _stored(app: FastAPI, draft_id: int) -> tuple[str, int | None, int | None]:
    """The draft's name and destination as stored, and the owner of its version."""
    row = await app.state.db.fetch_one(
        "SELECT d.label, d.destination_board_id, v.board_id AS owner FROM profile_drafts d "
        "JOIN profile_versions v ON v.id = d.draft_version_id WHERE d.id = ?",
        (draft_id,),
    )
    return str(row["label"]), row["destination_board_id"], row["owner"]


async def _editor_document(client: httpx.AsyncClient, row: dict[str, Any], label: str) -> Any:
    version = data(await client.get(f"/api/profile-versions/{row['current_version_id']}"))
    document = copy.deepcopy(version["profile"])
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": 7, "flow": 0}
    document["label"] = label
    return document


async def test_a_hand_edit_with_the_profiles_name_is_a_change_to_it_whatever_the_case(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    document = await _editor_document(client, row, f"  {BASE_LABEL.upper()} ")

    made = data(
        await client.post(
            "/api/profile-drafts",
            json={"base_version_id": row["current_version_id"], "profile": document},
        )
    )

    assert await _stored(app, made["id"]) == (BASE_LABEL, row["id"], row["id"])
    assert made["destination_board_id"] == row["id"] and made["draft_label"] == BASE_LABEL
    await assert_versions_owned(app.state.db)


async def test_a_hand_edit_under_any_other_name_is_a_new_profile_with_that_name(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    document = await _editor_document(client, row, "  Soft Bloom ")

    made = data(
        await client.post(
            "/api/profile-drafts",
            json={"base_version_id": row["current_version_id"], "profile": document},
        )
    )

    assert await _stored(app, made["id"]) == ("Soft Bloom", None, None)
    taken = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": row["current_version_id"],
            "profile": {**document, "label": BASE_LABEL.lower() + " v2"},
        },
    )
    assert taken.status_code < 300
    again = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": row["current_version_id"],
            "profile": {**document, "label": BASE_LABEL},
        },
    )
    assert again.status_code < 300 and data(again)["destination_board_id"] == row["id"]


async def test_the_label_in_a_document_never_places_a_draft_made_as_a_change(
    adopted: Adopted,
) -> None:
    """The constructor takes the destination's name over whatever the document says."""
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    base = await base_version_id(app, BASE_LABEL)
    document = await _editor_document(client, row, "Whatever the model wrote")

    made = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=document, new_profile_name=None
    )

    assert (made.draft_label, made.destination_board_id) == (BASE_LABEL, row["id"])
    version = data(await client.get(f"/api/profile-versions/{made.draft_version_id}"))
    assert version["profile"]["label"] == BASE_LABEL


async def test_a_refinement_goes_where_its_parent_went_and_keeps_its_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    change = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    fork = await draft_of(app, client, provider, BASE_LABEL, 8.5)
    assert (change["destination_board_id"], fork["destination_board_id"]) == (row["id"], None)

    async def refine(parent: dict[str, Any], label: str) -> dict[str, Any]:
        version = data(await client.get(f"/api/profile-versions/{parent['draft_version_id']}"))
        document = {**version["profile"], "label": label}
        document["phases"] = copy.deepcopy(document["phases"])
        document["phases"][0]["pump"] = {"target": "pressure", "pressure": 6.5, "flow": 0}
        provider.script = [json.dumps({"profile": document, "change_summary": "again"})]
        return dict(
            data(
                await client.post(
                    f"/api/profile-drafts/{parent['id']}/refine", json={"notes": "more"}
                )
            )
        )

    refined_change = await refine(change, "A name the model liked better")
    refined_fork = await refine(fork, "Another name the model liked")

    assert (refined_change["destination_board_id"], refined_change["draft_label"]) == (
        row["id"],
        BASE_LABEL,
    )
    assert (refined_fork["destination_board_id"], refined_fork["draft_label"]) == (
        None,
        fork["draft_label"],
    )
    assert await _stored(app, refined_fork["id"]) == (fork["draft_label"], None, None)
    await assert_versions_owned(app.state.db)


async def test_the_put_goes_to_the_stored_destination_whatever_the_profile_is_called_now(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    change = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    await app.state.db.execute(
        "UPDATE profile_board SET label = 'Renamed since' WHERE id = ?", (row["id"],)
    )

    landed = await put(client, change)

    assert landed["id"] == row["id"]
    await assert_versions_owned(app.state.db)


async def test_a_change_whose_profile_was_deleted_since_becomes_a_new_profile_under_its_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    change = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    await tombstone(client, row["id"])

    made = await put(client, change)

    assert made["id"] != row["id"] and made["label"] == BASE_LABEL
    assert made["deleted_at"] is None
    await assert_versions_owned(app.state.db)


async def test_the_name_typed_when_approving_changes_only_the_drafts_name(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    draft = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)

    row = await put(client, draft, label="  Gentle Bloom ")

    assert await _stored(app, draft["id"]) == ("Gentle Bloom", None, row["id"])
    assert row["label"] == "Gentle Bloom"
    version = data(await client.get(f"/api/profile-versions/{row['current_version_id']}"))
    assert version["profile"]["label"] == "Gentle Bloom"
    await assert_versions_owned(app.state.db)


async def test_a_name_for_a_change_is_refused_and_a_taken_name_for_a_new_profile_too(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    change = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    refused = await client.post(
        "/api/profile-board", json={"draft_id": change["id"], "label": "Another"}
    )
    assert refused.status_code == 422
    assert error(refused)["message"] == "A change to an existing profile keeps its name"
    new = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    taken = await client.post(
        "/api/profile-board", json={"draft_id": new["id"], "label": BASE_LABEL.upper()}
    )
    assert taken.status_code == 409


async def test_a_copy_is_a_draft_for_a_new_profile_and_its_version_belongs_to_the_new_row(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    set_id, version_id = await _set_with_version(app, client)
    made = data(await _pin(client, set_id, version_id))
    row_id, copy_version = made["board_id"], made["pin"]["profile_version_id"]

    drafts = await app.state.db.fetch_all(
        "SELECT label, destination_board_id FROM profile_drafts WHERE draft_version_id = ?",
        (copy_version,),
    )

    assert [(d["label"], d["destination_board_id"]) for d in drafts] == [(f"{BASE_LABEL} v1", None)]
    owner = await app.state.db.fetch_value(
        "SELECT board_id FROM profile_versions WHERE id = ?", (copy_version,)
    )
    assert owner == row_id
    await assert_versions_owned(app.state.db)


async def _older_version_made_active(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A change drafted, then an older version of its profile, whose document was first stored
    under another name, made the active one. Returns the profile row and the draft."""
    from gaggiclanker.db.repos.profile_board import ProfileBoardRepository
    from gaggiclanker.db.repos.profiles import ProfilesRepository
    from gaggiclanker.domain.models import Profile

    row = row_for(await get_board(client), BASE_LABEL)["row"]
    profiles = ProfilesRepository(app.state.db)
    current = await profiles.get_version(row["current_version_id"])
    assert current is not None
    older, _ = await profiles.ensure_version(
        Profile.model_validate({**(current.profile or {}), "label": "Old name", "temperature": 90}),
        owner=None,
    )
    await ProfileBoardRepository(app.state.db).add_version(row["id"], older.id, "edit")
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 7.4)
    made = await client.put(
        f"/api/profile-board/{row['id']}/active-version", json={"version_id": older.id}
    )
    assert made.status_code == 200, made.text
    return row, draft


async def test_making_an_older_version_active_keeps_the_profiles_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row, _ = await _older_version_made_active(app, client, provider)

    after = row_for(await get_board(client), BASE_LABEL)["row"]

    assert after["id"] == row["id"] and after["label"] == BASE_LABEL
    assert after["current_version_id"] != row["current_version_id"]
    # What is shown of that version is the profile's name, not the one it was first stored with.
    version = data(await client.get(f"/api/profile-versions/{after['current_version_id']}"))
    assert version["label"] == BASE_LABEL and version["profile"]["label"] == BASE_LABEL


async def test_a_put_never_renames_the_profile_it_changes(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row, draft = await _older_version_made_active(app, client, provider)
    rows_before = len(data(await client.get("/api/profile-board"))["rows"])

    landed = await put(client, draft)

    assert landed["id"] == row["id"] and landed["label"] == BASE_LABEL
    assert len(data(await client.get("/api/profile-board"))["rows"]) == rows_before
    # The document is the profile's name over the draft's brew; nothing was stored for a name.
    version = data(await client.get(f"/api/profile-versions/{landed['current_version_id']}"))
    assert version["profile"]["label"] == BASE_LABEL
    stored = data(await client.get(f"/api/profile-drafts/{draft['id']}"))["draft"]
    assert (stored["draft_version_id"], stored["draft_label"]) == (
        draft["draft_version_id"],
        BASE_LABEL,
    )
    assert landed["current_version_id"] == draft["draft_version_id"]
    await assert_versions_owned(app.state.db)


async def test_the_profile_keeps_its_name_so_no_other_profile_can_take_it(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    await _older_version_made_active(app, client, provider)

    taken = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": await base_version_id(app, "Adaptive v2"),
            "profile": {
                **(await base_profile(app, "Adaptive v2")).model_dump(
                    mode="json", exclude={"annotations", "id"}
                ),
                "label": BASE_LABEL,
            },
            "change_summary": "manual",
        },
    )

    assert taken.status_code == 409, taken.text


async def test_the_signature_in_force_stays_with_the_version_a_change_lands_on(
    adopted: Adopted, provider: FakeProvider
) -> None:
    from gaggiclanker.domain.signature import ExpectationInput
    from gaggiclanker.signatures.service import SignatureService

    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    version = data(await client.get(f"/api/profile-versions/{row['current_version_id']}"))
    first_phase = version["profile"]["phases"][0]["name"]
    signatures = SignatureService(app.state.db)
    await signatures.propose(
        row["current_version_id"],
        [ExpectationInput(tier="critical", kind="reached", phase=first_phase)],
        reason="why",
    )
    row, draft = await _older_version_made_active(app, client, provider)
    assert await signatures.count_in_force(draft["draft_version_id"]) == 1

    landed = await put(client, draft)

    assert landed["current_version_id"] == draft["draft_version_id"]
    assert await signatures.count_in_force(landed["current_version_id"]) == 1


async def test_a_stored_destination_decides_even_when_the_base_belongs_to_another_profile_now(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    other = row_for(await get_board(client), "Adaptive v2")["row"]
    change = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    assert change["destination_board_id"] == row["id"]
    await app.state.db.execute(
        "UPDATE profile_versions SET board_id = ? WHERE id = ?",
        (other["id"], row["current_version_id"]),
    )

    board = await get_board(client)
    [landing] = [item for item in board["landings"] if item["draft_id"] == change["id"]]

    assert landing["plain"]["row_id"] == row["id"]
