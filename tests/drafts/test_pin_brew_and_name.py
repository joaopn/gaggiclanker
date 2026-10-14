"""A pin's copy is found by its brew and its name, and a pinned profile is frozen by its pin.

A pin's copy is the pinned version's profile under the pin's name. With the name out of a
version's identity the copy is the original's own brew, so "this document is the copy" is a
question about the brew **and** the name the copy's profile carries, and "this profile is frozen"
is a question about the pin that holds it, never about what a version happens to be called.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.pins import PinsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository, document_for
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.test_board import adopted, get_board, row_for
from tests.drafts.test_pin_frozen import NAME, _pinned_row

__all__ = ["adopted"]

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def test_the_copy_is_found_by_its_brew_and_its_pin_rows_name(adopted: Adopted) -> None:
    app, client, _fake = adopted
    row = await _pinned_row(app, client)
    profiles = ProfilesRepository(app.state.db)
    copy = await profiles.get_version(row["row"]["current_version_id"])
    assert copy is not None
    pins = PinsRepository(app.state.db)

    assert await pins.by_copy_content(copy.content_hash, NAME) is not None
    assert await pins.by_copy_content(copy.content_hash, BASE_LABEL) is None
    assert await pins.by_copy_content(copy.content_hash, "Another name") is None
    assert await pins.set_version_naming_content(copy.content_hash, BASE_LABEL) is not None
    assert await pins.set_version_naming_content(copy.content_hash, NAME) is None


async def test_a_change_of_the_original_back_to_the_pinned_brew_is_not_the_pins_copy(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    row = await _pinned_row(app, client)
    profiles = ProfilesRepository(app.state.db)
    copy = await profiles.get_version(row["row"]["current_version_id"])
    assert copy is not None

    as_the_original = document_for(copy, BASE_LABEL)
    as_the_copy = document_for(copy, NAME)

    # The same brew: under the original's name it is a change to the original, under the copy's
    # name it is the copy.
    assert await app.state.draft_proposals.pinned_copy_of(as_the_original) is None
    assert await app.state.draft_proposals.pinned_copy_of(as_the_copy) is not None


async def test_a_pinned_profile_stays_frozen_whatever_it_is_called(adopted: Adopted) -> None:
    app, client, _fake = adopted
    row = await _pinned_row(app, client)
    # Whatever the profile is called (or its versions' documents were), the pin holds it.
    await app.state.db.execute(
        "UPDATE profile_board SET label = ? WHERE id = ?",
        ("A name nobody pinned", row["row"]["id"]),
    )

    refused = await client.put(
        f"/api/profile-board/{row['row']['id']}/active-version",
        json={"version_id": row["row"]["current_version_id"]},
    )

    assert refused.status_code == 409 and error(refused)["code"] == "PIN_FROZEN"
    assert row_for(await get_board(client), "A name nobody pinned")["row"]["id"] == row["row"]["id"]
    assert data(await client.get(f"/api/profile-board/{row['row']['id']}/versions"))
