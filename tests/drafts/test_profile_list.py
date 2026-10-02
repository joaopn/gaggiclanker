"""The profile list's rules, one per decision: on or off the machine, starred, the selected
profile, making a version active, the versions a profile has and the proposals waiting for it."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import APP_LABEL, draft_of, ids_labelled, make_set_on
from tests.drafts.test_board import (
    adopted,
    app_row,
    approve,
    draft_from,
    get_board,
    pull,
    put,
    row_for,
    summary_of,
    write_frames,
)
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]


async def on_machine(client: httpx.AsyncClient, row_id: int, on: bool) -> httpx.Response:
    return await client.put(f"/api/profile-board/{row_id}/on-machine", json={"on": on})


def ids(fake: FakeDevice) -> list[str]:
    return [str(p["id"]) for p in fake.profiles]


async def test_switching_a_firmware_default_off_removes_it_and_back_on_pushes_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    default = row_for(await get_board(client), BASE_LABEL)
    file = default["machine"]["device_id"]
    assert default["row"]["origin"] == "adopted" and default["on_machine"] is True

    assert (await on_machine(client, default["row"]["id"], False)).status_code == 200
    preview = row_for(await get_board(client), BASE_LABEL)["planned"]
    assert [(a["kind"], a["reason"]) for a in preview] == [("remove", "off")]
    run = await pull(app)

    assert [i["device_id"] for i in summary_of(run)["removed"]] == [file]
    assert file not in ids(fake)
    # The profile stays in the list, off, with its versions.
    off = row_for(await get_board(client), BASE_LABEL)
    assert off["on_machine"] is False and off["machine"]["present"] is False
    fake.ws_requests.clear()
    assert (await pull(app)).status == "ok" and write_frames(fake) == []

    await on_machine(client, default["row"]["id"], True)
    back = await pull(app)
    assert [i["reason"] for i in summary_of(back)["pushed"]] == ["missing"]
    assert len([p for p in fake.profiles if p["label"] == BASE_LABEL]) == 1


async def test_with_writes_off_a_toggle_is_stored_and_nothing_is_sent(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = live
    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    await pull(app)
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    fake_device.ws_requests.clear()

    assert (await on_machine(client, row["id"], False)).status_code == 200
    run = await pull(app)

    assert write_frames(fake_device) == [] and run.status == "ok"
    assert row_for(await get_board(client), BASE_LABEL)["on_machine"] is False


async def test_starred_is_applied_only_while_on_the_machine_and_remembered_while_off(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    file = row["machine"]["device_id"]
    rid = row["row"]["id"]
    await on_machine(client, rid, False)
    # Starring a profile that is off is stored, and sends nothing for it.
    assert (
        await client.put(f"/api/profile-board/{rid}/starred", json={"starred": True})
    ).status_code == 200
    await pull(app)
    assert file not in ids(fake)
    assert row_for(await get_board(client), BASE_LABEL)["starred"] is True

    # On again: pushed, and its star is the remembered one (the firmware stars every save).
    await on_machine(client, rid, True)
    await pull(app)
    [new] = ids_labelled(fake, BASE_LABEL)
    assert new in fake.favorite_profile_ids
    # Unstarred while on: applied.
    await client.put(f"/api/profile-board/{rid}/starred", json={"starred": False})
    await pull(app)
    assert new not in fake.favorite_profile_ids and new in ids(fake)


async def test_switching_off_the_selected_profile_selects_the_first_that_is_on_first(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    rows = (await get_board(client))["rows"]
    selected = next(r for r in rows if not r["utility"])
    fake.selected_profile_id = selected["machine"]["device_id"]
    await on_machine(client, selected["row"]["id"], False)
    first_on = next(r for r in rows if r["row"]["id"] != selected["row"]["id"] and not r["utility"])

    await pull(app)

    assert selected["machine"]["device_id"] not in ids(fake)
    assert fake.selected_profile_id == first_on["machine"]["device_id"]


async def test_the_selected_profile_stays_when_nothing_else_is_on(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    rows = (await get_board(client))["rows"]
    keep = rows[0]
    fake.selected_profile_id = keep["machine"]["device_id"]
    for r in rows:
        await on_machine(client, r["row"]["id"], False)

    board = await get_board(client)
    leave = [a for a in board["actions"] if a["kind"] == "leave"]
    assert [a["device_id"] for a in leave] == [keep["machine"]["device_id"]]
    assert "no other profile" in leave[0]["detail"]
    await pull(app)

    assert ids(fake) == [keep["machine"]["device_id"]]


async def test_a_set_brewing_a_profile_is_served_and_does_not_block_switching_it_off(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    await make_set_on(client, "Brews it", row["row"]["current_version_id"])
    assert [s["name"] for s in row_for(await get_board(client), BASE_LABEL)["sets_brewing"]] == [
        "Brews it"
    ]
    await on_machine(client, row["row"]["id"], False)

    run = await pull(app)

    assert [i["device_id"] for i in summary_of(run)["removed"]] == [row["machine"]["device_id"]]


async def test_an_older_version_made_active_replaces_the_newer_file(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1 = await app_row(app, client, fake, provider, 8)
    second = await draft_from(app, client, provider, v1, 7)
    await put(client, second)
    await pull(app)
    versions = data(await client.get(f"/api/profile-board/{v1['id']}/versions"))
    assert versions["versions"][0]["is_active"] is True
    older = versions["versions"][1]
    assert older["previous_version_id"] is None, "the first version is new, never a diff"
    assert versions["versions"][0]["previous_version_id"] == older["version_id"]

    done = await client.put(
        f"/api/profile-board/{v1['id']}/active-version", json={"version_id": older["version_id"]}
    )

    assert done.status_code == 200, done.text
    assert data(done)["current_version_id"] == older["version_id"]
    run = await pull(app)
    assert [i["reason"] for i in summary_of(run)["pushed"]] == ["superseded"]
    [only] = ids_labelled(fake, APP_LABEL)
    found = next(p for p in fake.profiles if p["id"] == only)
    assert float(found["phases"][0]["pump"]["pressure"]) == 8


async def test_making_a_version_active_is_refused_with_its_own_sentence(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    mine = await app_row(app, client, fake, provider, 8)
    other = row_for(await get_board(client), BASE_LABEL)
    url = f"/api/profile-board/{mine['id']}/active-version"

    foreign = await client.put(url, json={"version_id": other["row"]["current_version_id"]})
    assert foreign.status_code == 409 and error(foreign)["details"]["reason"] == "other_profile"

    base = await app.state.db.fetch_value(
        "SELECT id FROM profile_versions WHERE label = 'Empty baseline'"
    )
    if base is None:
        base = await app.state.draft_proposals.profiles.empty_base()
    synthetic = await client.put(url, json={"version_id": base})
    assert synthetic.status_code == 409
    assert error(synthetic)["details"]["reason"] == "synthetic_base"

    missing = await client.put(url, json={"version_id": 99999})
    assert missing.status_code == 404


async def test_a_version_outside_the_bounds_cannot_be_made_active(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1 = await app_row(app, client, fake, provider, 8)
    await put(client, await draft_from(app, client, provider, v1, 7))
    older = data(await client.get(f"/api/profile-board/{v1['id']}/versions"))["versions"][1]
    await app.state.settings_service.apply({"profilePolicyPressureMaxBar": 7.5})

    refused = await client.put(
        f"/api/profile-board/{v1['id']}/active-version", json={"version_id": older["version_id"]}
    )

    assert refused.status_code == 409 and error(refused)["details"]["reason"] == "policy"


async def test_a_proposal_is_a_version_waiting_inside_its_profile_until_made_active(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1 = await app_row(app, client, fake, provider, 8)
    draft = await draft_from(app, client, provider, v1, 6)
    await approve(app, draft)

    board = await get_board(client)
    assert row_for(board, APP_LABEL)["proposed_versions"] == 1
    assert [p["row_id"] for p in board["proposals"]] == [v1["id"]]
    versions = data(await client.get(f"/api/profile-board/{v1['id']}/versions"))
    [proposed] = versions["proposed"]
    assert proposed["draft"]["id"] == draft["id"]
    assert proposed["compared_to_version_id"] == versions["active_version_id"]
    assert len(versions["versions"]) == 1, "it is not a version until a person makes it active"

    await put(client, draft)  # making it active is a put
    after = data(await client.get(f"/api/profile-board/{v1['id']}/versions"))
    assert after["proposed"] == [] and len(after["versions"]) == 2
    assert after["versions"][0]["source"] in ("agent", "edit")


async def test_a_draft_that_lands_on_no_profile_is_a_proposed_new_one(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)

    board = await get_board(client)

    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]
    assert proposal["row_id"] is None
