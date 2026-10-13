"""A Set follows its profile: every change of a profile's active version is a version of its Set.

One function does it (``SetsRepository.follow_profile``), in the same transaction as the change.
These tests walk every path that changes a profile's active version, and the Set's own revert,
and after each one the database must satisfy what a Set and its profile owe each other
(``assert_sets_follow_their_profiles``).
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import (
    assert_sets_follow_their_profiles,
    assert_versions_owned,
    make_set_on,
    same_name_draft,
    set_version_ids,
)
from tests.drafts.test_board import adopted, edit_on_display, get_board, pull, put, resolve, row_for
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _set_on_the_base(app: FastAPI, client: httpx.AsyncClient) -> tuple[int, dict[str, Any]]:
    """A Set with a grind, dose and yield on the base profile, and the profile's row."""
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    set_id = await make_set_on(client, "Followed", row["current_version_id"])
    data(
        await client.post(
            f"/api/sets/{set_id}/versions",
            json={"grind_setting": "21", "grind_value": 21, "intent": "a finer grind"},
        )
    )
    return set_id, row


async def _versions(client: httpx.AsyncClient, set_id: int) -> list[dict[str, Any]]:
    """The Set's versions, oldest first."""
    listed = data(await client.get(f"/api/sets/{set_id}"))["versions"]
    return [entry["version"] for entry in reversed(listed)]


async def _current(client: httpx.AsyncClient, set_id: int) -> dict[str, Any]:
    return next(v for v in await _versions(client, set_id) if v["is_current"])


async def _older_version_of(client: httpx.AsyncClient, row_id: int) -> int:
    listed = data(await client.get(f"/api/profile-board/{row_id}/versions"))
    active = listed["active_version_id"]
    return int(next(v["version_id"] for v in listed["versions"] if v["version_id"] != active))


async def test_making_a_version_active_gives_the_set_a_minor_version_naming_it(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    set_id, row = await _set_on_the_base(app, client)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    before = await _versions(client, set_id)
    older = await _older_version_of(client, row["id"])

    response = await client.put(
        f"/api/profile-board/{row['id']}/active-version", json={"version_id": older}
    )

    assert response.status_code == 200, response.text
    after = await _versions(client, set_id)
    assert len(after) == len(before) + 1
    made = await _current(client, set_id)
    assert made["profile_version_id"] == older and made["version_label"] == "v1.3"
    assert made["parent_version_id"] == before[-1]["id"] and made["origin"] == "manual"
    # Grind, dose, yield and intent are carried from the version the Set was on.
    parent = before[-1]
    assert (made["grind_setting"], made["dose_g"], made["target_yield_g"], made["intent"]) == (
        parent["grind_setting"],
        parent["dose_g"],
        parent["target_yield_g"],
        parent["intent"],
    )
    assert made["prediction"] == "" and made["compares_to_version_id"] is None
    await assert_versions_owned(app.state.db)


async def test_making_the_active_version_active_again_adds_nothing(adopted: Adopted) -> None:
    app, client, _ = adopted
    set_id, row = await _set_on_the_base(app, client)
    before = await set_version_ids(app, set_id)

    response = await client.put(
        f"/api/profile-board/{row['id']}/active-version",
        json={"version_id": row["current_version_id"]},
    )

    assert response.status_code == 200
    assert await set_version_ids(app, set_id) == before


async def test_a_profile_with_no_set_changes_without_one(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    older = await _older_version_of(client, row["id"])

    response = await client.put(
        f"/api/profile-board/{row['id']}/active-version", json={"version_id": older}
    )

    assert response.status_code == 200
    assert int(await app.state.db.fetch_value("SELECT COUNT(*) FROM sets")) == 0


async def test_keeping_the_machines_side_of_a_conflict_gives_the_set_a_version(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    set_id, row = await _set_on_the_base(app, client)
    await pull(app)
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    await edit_on_display(fake, file)
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['id']}/conflict"))
    before = await set_version_ids(app, set_id)

    response = await resolve(client, row["id"], "machine", seen["machine"]["content_hash"])

    assert response.status_code == 200, response.text
    assert len(await set_version_ids(app, set_id)) == len(before) + 1
    made = await _current(client, set_id)
    after = row_for(await get_board(client), BASE_LABEL)["row"]
    assert made["profile_version_id"] == after["current_version_id"]
    await assert_versions_owned(app.state.db)


async def test_keeping_the_apps_side_of_a_conflict_adds_no_version(adopted: Adopted) -> None:
    app, client, fake = adopted
    set_id, row = await _set_on_the_base(app, client)
    await pull(app)
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    await edit_on_display(fake, file)
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['id']}/conflict"))
    before = await set_version_ids(app, set_id)

    await resolve(client, row["id"], "app", seen["machine"]["content_hash"])

    assert await set_version_ids(app, set_id) == before
    await assert_versions_owned(app.state.db)


async def test_an_edit_the_sync_records_changes_no_active_version_and_so_no_set_version(
    adopted: Adopted,
) -> None:
    """The sync lists what the machine's file holds as a version of the profile; the active
    version stays the person's choice, so the Set has nothing to follow."""
    app, client, fake = adopted
    set_id, _ = await _set_on_the_base(app, client)
    await pull(app)
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    before = await set_version_ids(app, set_id)
    await edit_on_display(fake, file)

    await pull(app)

    assert await set_version_ids(app, set_id) == before
    await assert_versions_owned(app.state.db)


async def test_a_put_the_sync_then_sends_adds_nothing_to_the_set(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    set_id, _ = await _set_on_the_base(app, client)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    recorded = await set_version_ids(app, set_id)

    run = await pull(app)

    assert run.status == "ok", run.error
    assert await set_version_ids(app, set_id) == recorded
    await assert_versions_owned(app.state.db)


async def test_an_archived_set_still_follows_its_profile(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """Archiving changes nothing about the profile: it stays the Set's, and the Set keeps saying
    what it brews."""
    app, client, _ = adopted
    set_id, row = await _set_on_the_base(app, client)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    data(await client.post(f"/api/sets/{set_id}/archive"))
    older = await _older_version_of(client, row["id"])

    await client.put(f"/api/profile-board/{row['id']}/active-version", json={"version_id": older})

    assert (await _current(client, set_id))["profile_version_id"] == older
    await assert_versions_owned(app.state.db)


# ── the Set's own revert ─────────────────────────────────────────────────


async def test_a_revert_makes_the_profile_follow_the_set_and_writes_no_version(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    set_id, row = await _set_on_the_base(app, client)
    first = (await _versions(client, set_id))[0]
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    before = await set_version_ids(app, set_id)
    assert first["profile_version_id"] != row["current_version_id"] or len(before) == 3

    response = await client.post(
        f"/api/sets/{set_id}/rollback", json={"to_version_id": first["id"], "note": "too sharp"}
    )

    assert response.status_code == 200, response.text
    assert await set_version_ids(app, set_id) == before, "going back writes no version"
    current = await _current(client, set_id)
    assert current["id"] == first["id"]
    now = row_for(await get_board(client), BASE_LABEL)["row"]
    assert now["current_version_id"] == first["profile_version_id"]
    assert now["previous_version_id"] is not None
    await assert_versions_owned(app.state.db)


async def test_a_revert_that_the_profile_cannot_follow_moves_nothing(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """Everything that can refuse making a version active refuses the revert, as one change."""
    app, client, _ = adopted
    set_id, _row = await _set_on_the_base(app, client)
    first = (await _versions(client, set_id))[0]
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    before = await set_version_ids(app, set_id)
    # The bounds are tightened since: the older version is outside them.
    await app.state.settings_service.apply({"profilePolicyPressureMaxBar": 6.0})
    current_before = await _current(client, set_id)

    response = await client.post(
        f"/api/sets/{set_id}/rollback", json={"to_version_id": first["id"], "note": "x"}
    )

    assert response.status_code == 409, response.text
    assert error(response)["message"].startswith("That version cannot go on the machine")
    assert await _current(client, set_id) == current_before
    assert await set_version_ids(app, set_id) == before
    assert int(await app.state.db.fetch_value("SELECT COUNT(*) FROM set_version_reverts")) == 0
    await assert_sets_follow_their_profiles(app.state.db)


async def test_a_revert_to_the_version_the_profile_already_brews_only_moves_the_pointer(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    set_id, row = await _set_on_the_base(app, client)
    versions = await _versions(client, set_id)
    # Both versions brew the profile's active version: only the Set's grind differs.
    assert versions[0]["profile_version_id"] == versions[1]["profile_version_id"]

    response = await client.post(
        f"/api/sets/{set_id}/rollback", json={"to_version_id": versions[0]["id"], "note": ""}
    )

    assert response.status_code == 200, response.text
    now = row_for(await get_board(client), BASE_LABEL)["row"]
    assert now["current_version_id"] == row["current_version_id"]
    await assert_versions_owned(app.state.db)


async def _waiting_proposal(app: FastAPI, set_id: int, dose: float) -> None:
    from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
    from gaggiclanker.db.repos.sets import SetVersionPatch

    made = await SetProposalsRepository(app.state.db).create(
        set_id,
        ProposalWrite(
            patch=SetVersionPatch(dose_g=dose),
            reason="A little more.",
            prediction="Compared to the current version: a touch more body, and no slower.",
        ),
    )
    assert made.proposal is not None, made.refused


async def _nothing_waits(app: FastAPI, set_id: int) -> bool:
    from gaggiclanker.db.repos.set_proposals import SetProposalsRepository

    return await SetProposalsRepository(app.state.db).waiting(set_id) is None


async def test_a_put_leaves_a_proposal_waiting_on_the_set_stale(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """A change argued against the version the Set was on is overtaken the moment it moves."""
    app, client, _ = adopted
    set_id, _row = await _set_on_the_base(app, client)
    await _waiting_proposal(app, set_id, 18.5)

    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))

    assert await _nothing_waits(app, set_id)


async def test_making_a_version_active_leaves_a_proposal_waiting_on_the_set_stale(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    set_id, row = await _set_on_the_base(app, client)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    older = await _older_version_of(client, row["id"])
    await _waiting_proposal(app, set_id, 18.7)

    await client.put(f"/api/profile-board/{row['id']}/active-version", json={"version_id": older})

    assert await _nothing_waits(app, set_id)


async def test_keeping_the_machines_side_leaves_a_proposal_waiting_on_the_set_stale(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    set_id, row = await _set_on_the_base(app, client)
    await pull(app)
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    await edit_on_display(fake, file)
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['id']}/conflict"))
    await _waiting_proposal(app, set_id, 18.9)

    await resolve(client, row["id"], "machine", seen["machine"]["content_hash"])

    assert await _nothing_waits(app, set_id)
