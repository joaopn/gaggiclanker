"""Archiving a Recipe takes its profile off the machine at the next sync; Unarchive puts it back.

Walks the routes against the fake machine: what Archive switches off, what the board plans for it
(the switch's own removal, the selected profile moving first), what the Recipe's detail says
Archive would take off, what the Profiles page says about the archived owner, and the way back.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data
from tests.drafts.helpers import grind_change
from tests.drafts.test_board import (
    adopted,
    edit_on_display,
    get_board,
    pull,
    resolve,
    summary_of,
    write_frames,
)
from tests.drafts.test_pins import _recipe_with_version
from tests.drafts.test_rename import own_row

__all__ = ["adopted"]  # the fixture, re-exported for this module

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _on_the_machine(app: FastAPI, client: httpx.AsyncClient) -> tuple[int, str]:
    """A Recipe on the base profile, synced: (recipe id, the file's id)."""
    recipe_id, _version = await _recipe_with_version(app, client)
    run = await pull(app)
    assert run.status == "ok", run.error
    return recipe_id, str((await own_row(client))["machine"]["device_id"])


def _files(fake: FakeDevice) -> list[str]:
    return [str(p["id"]) for p in fake.profiles]


async def test_archive_switches_the_profile_off_and_the_next_sync_removes_its_file(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, file = await _on_the_machine(app, client)
    before = await own_row(client)
    assert before["on_machine"] is True and before["archived_recipe"] is None
    assert before["recipe"]["name"] == "Guji"
    assert file in _files(fake), "the precondition: the file is on the machine"

    data(await client.post(f"/api/recipes/{recipe_id}/archive"))

    row = await own_row(client)
    assert row["on_machine"] is False
    assert row["starred"] is before["starred"], "the star is kept"
    # Archived Recipes do not count for `recipe`; the owner is named beside it.
    assert row["recipe"] is None
    assert row["archived_recipe"] == {"id": recipe_id, "name": "Guji"}
    assert [(a["kind"], a["reason"], a["device_id"]) for a in row["planned"]] == [
        ("remove", "off", file)
    ]
    run = await pull(app)
    assert run.status == "ok", run.error
    assert [i["device_id"] for i in summary_of(run)["removed"]] == [file]
    assert file not in _files(fake)


async def test_archive_of_the_selected_profile_selects_another_first_and_unarchive_brings_it_back(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, file = await _on_the_machine(app, client)
    fake.selected_profile_id = file
    await pull(app)  # the mirror learns which one is selected
    others = [r for r in (await get_board(client))["rows"] if r["machine"]["device_id"] != file]
    assert others

    data(await client.post(f"/api/recipes/{recipe_id}/archive"))
    await pull(app)

    assert file not in _files(fake)
    assert fake.selected_profile_id in _files(fake), "another profile was selected first"

    back = data(await client.post(f"/api/recipes/{recipe_id}/unarchive"))

    assert back["archived"] is False and back["profile_switched_on"] is True
    row = await own_row(client)
    assert row["on_machine"] is True and row["archived_recipe"] is None
    assert row["recipe"]["name"] == "Guji"
    run = await pull(app)
    assert run.status == "ok", run.error
    # The profile's own row stands on a file again, and that file is on the machine.
    after = await own_row(client)
    assert after["machine"]["present"] is True
    assert after["machine"]["device_id"] in _files(fake), "pushed back"
    assert [i["device_id"] for i in summary_of(run)["pushed"]] == [after["machine"]["device_id"]]


async def test_the_detail_says_what_archive_would_take_off_the_machine(adopted: Adopted) -> None:
    app, client, fake = adopted
    recipe_id, file = await _on_the_machine(app, client)
    await grind_change(client, recipe_id)

    detail = data(await client.get(f"/api/recipes/{recipe_id}"))
    assert detail["profile"] == {
        "machine_name": f"{BASE_LABEL} v1.1",
        "on_machine": True,
        "selected": False,
    }

    fake.selected_profile_id = file
    await pull(app)
    detail = data(await client.get(f"/api/recipes/{recipe_id}"))
    assert detail["profile"]["selected"] is True

    off = await own_row(client)
    data(await client.put(f"/api/profile-board/{off['row']['id']}/on-machine", json={"on": False}))
    detail = data(await client.get(f"/api/recipes/{recipe_id}"))
    assert detail["profile"]["on_machine"] is False


async def test_a_recipe_with_no_profile_has_none_in_its_detail_and_archives_at_once(
    adopted: Adopted,
) -> None:
    _app, client, _fake = adopted
    bean = data(await client.post("/api/beans", json={"name": "Paper", "roaster": "nobody"}))
    grinder = data(
        await client.post(
            "/api/grinders", json={"name": "Niche Zero", "step_unit": "numbers_lower_finer"}
        )
    )
    designed = data(
        await client.post(
            "/api/recipes/design",
            json={"bean_id": bean["id"], "grinder_id": grinder["id"], "drink": "espresso"},
        )
    )
    made = designed["recipe"]
    assert made["board_id"] is None

    assert data(await client.get(f"/api/recipes/{made['id']}"))["profile"] is None
    back = data(await client.post(f"/api/recipes/{made['id']}/archive"))
    assert back["archived"] is True
    again = data(await client.post(f"/api/recipes/{made['id']}/unarchive"))
    assert again["profile_switched_on"] is False


async def test_with_writes_off_archive_plans_the_removal_and_sends_nothing(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, file = await _on_the_machine(app, client)
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    fake.ws_requests.clear()

    data(await client.post(f"/api/recipes/{recipe_id}/archive"))

    row = await own_row(client)
    assert row["on_machine"] is False
    assert [(a["kind"], a["reason"], a["device_id"]) for a in row["planned"]] == [
        ("remove", "off", file)
    ]
    run = await pull(app)
    assert run.status == "ok", run.error
    assert write_frames(fake) == []
    assert file in _files(fake), "with Writes off the file stays until Writes are on"

    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    await pull(app)
    assert file not in _files(fake)


async def test_archiving_a_profile_in_conflict_removes_nothing_until_a_person_chooses(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, file = await _on_the_machine(app, client)
    await edit_on_display(fake, file)
    before = await own_row(client)
    assert before["in_conflict"], "the precondition: the machine's file differs"

    data(await client.post(f"/api/recipes/{recipe_id}/archive"))

    row = await own_row(client)
    assert row["on_machine"] is False and row["in_conflict"]
    assert row["planned"] == [], "the sync does nothing for a profile in conflict"
    fake.ws_requests.clear()
    run = await pull(app)
    assert run.status == "ok", run.error
    assert summary_of(run)["removed"] == [] and write_frames(fake) == []
    assert file in _files(fake)

    # A person keeps the app's side: the profile is off, so the removal is planned and done.
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    kept = await resolve(client, row["row"]["id"], "app", seen["machine"]["content_hash"])
    assert kept.status_code == 200, kept.text
    run = await pull(app)
    assert run.status == "ok", run.error
    assert file not in _files(fake)
