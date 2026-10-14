"""What the board read says about names: the note after a sync took the machine's name, and the name
fields of a conflict that the Profiles page words (the profile's name beside the machine's)."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data
from tests.drafts.helpers import APP_LABEL
from tests.drafts.test_board import (
    adopted,
    app_row,
    edit_on_display,
    get_board,
    pull,
    row_for,
)
from tests.drafts.test_names_per_file import rename_at_the_machine
from tests.drafts.test_rename import own_row, rename
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def test_a_name_the_machine_gave_is_noted_on_the_row_until_the_next_sync(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = await own_row(client)
    assert row["renamed_on_machine"] is None
    rename_at_the_machine(fake, row["machine"]["device_id"], "Renamed at the machine")

    run = await pull(app)

    noted = await own_row(client, "Renamed at the machine")
    assert noted["renamed_on_machine"]["from_name"] == BASE_LABEL
    assert noted["renamed_on_machine"]["at"], "the time of the sync that took it"
    rows = (await get_board(client))["rows"]
    assert [r["row"]["label"] for r in rows if r["renamed_on_machine"]] == [
        "Renamed at the machine"
    ], "no other profile carries it"
    assert run.status == "ok"
    await pull(app)
    assert (await own_row(client, "Renamed at the machine"))["renamed_on_machine"] is None


async def test_the_note_is_there_with_writes_off_too(adopted: Adopted) -> None:
    app, client, fake = adopted
    row = await own_row(client)
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    rename_at_the_machine(fake, row["machine"]["device_id"], "Renamed at the machine")

    await pull(app)

    noted = await own_row(client, "Renamed at the machine")
    assert noted["renamed_on_machine"]["from_name"] == BASE_LABEL


async def test_the_note_goes_when_the_profile_is_renamed_again(adopted: Adopted) -> None:
    app, client, fake = adopted
    row = await own_row(client)
    rename_at_the_machine(fake, row["machine"]["device_id"], "Renamed at the machine")
    await pull(app)
    taken = await own_row(client, "Renamed at the machine")

    await rename(client, taken["row"]["id"], "Renamed again in the app")

    again = await own_row(client, "Renamed again in the app")
    assert again["renamed_on_machine"] is None, "a note about a name it no longer has would lie"


async def test_a_conflict_about_a_taken_name_carries_both_names_and_the_holder(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    mine = await own_row(client, APP_LABEL)
    other = await own_row(client)
    rename_at_the_machine(fake, mine["machine"]["device_id"], other["row"]["label"])
    await pull(app)

    held = await own_row(client, APP_LABEL)

    assert held["in_conflict"]
    assert held["conflict"]["machine_name"] == other["row"]["label"]
    assert held["conflict"]["machine_name_held_by"] == other["row"]["label"]
    assert held["conflict"]["name_only"] is True
    seen = data(await client.get(f"/api/profile-board/{mine['row']['id']}/conflict"))
    assert seen["machine"]["name_only"] is True
    assert seen["machine"]["machine_name_held_by"] == other["row"]["label"]
    assert seen["machine"]["content_hash"] == held["conflict"]["content_hash"]


async def test_a_conflict_about_a_name_both_sides_changed_has_no_holder(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = await own_row(client)
    await rename(client, row["row"]["id"], "Renamed here")
    rename_at_the_machine(fake, row["machine"]["device_id"], "Renamed there")
    await pull(app)

    held = await own_row(client, "Renamed here")

    assert held["in_conflict"]
    assert held["conflict"]["machine_name"] == "Renamed there"
    assert held["conflict"]["machine_name_held_by"] is None
    assert held["conflict"]["name_only"] is True


async def test_a_conflict_about_the_brew_is_not_about_the_name_alone(adopted: Adopted) -> None:
    app, client, fake = adopted
    row = await own_row(client)
    rename_at_the_machine(fake, row["machine"]["device_id"], "Edited and renamed")
    await edit_on_display(fake, row["machine"]["device_id"], 3)
    await pull(app)

    held = row_for(await get_board(client), BASE_LABEL)

    assert held["in_conflict"]
    assert held["conflict"]["name_only"] is False
    assert held["conflict"]["machine_name"] == "Edited and renamed"
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    assert seen["machine"]["name_only"] is False


async def test_the_name_conflict_of_an_older_brew_is_served_as_one(
    adopted: Adopted, provider: FakeProvider
) -> None:
    from tests.drafts.test_names_per_file import older_brew_on_the_display

    app, client, fake = adopted
    mine, file = await older_brew_on_the_display(adopted, provider)
    other = await own_row(client)
    rename_at_the_machine(fake, file, other["row"]["label"])
    await pull(app)

    held = await own_row(client, APP_LABEL)

    assert held["in_conflict"]
    assert held["conflict"]["name_only"] is True
    assert held["conflict"]["machine_name_held_by"] == other["row"]["label"]
    seen = data(await client.get(f"/api/profile-board/{mine['row']['id']}/conflict"))
    assert seen["machine"]["name_only"] is True
