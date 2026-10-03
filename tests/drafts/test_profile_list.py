"""The profile list's rules, one per decision: on or off the machine, starred, the selected
profile, making a version active, the versions a profile has and the proposals waiting for it."""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from gaggiclanker.drafts.board import BoardService
from gaggiclanker.drafts.machine import read_machine
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import APP_LABEL, draft_of, ids_labelled, make_set_on, tombstone
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
    variant_draft,
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
    # The row carries the active version's document, so the list can describe it without a
    # request per profile.
    document = default["active_version"]["profile"]
    assert document["label"] == BASE_LABEL and document["phases"]

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


async def two_versions(
    app: FastAPI, client: httpx.AsyncClient, fake: FakeDevice, provider: FakeProvider
) -> tuple[dict[str, object], dict[str, object], str]:
    """An app profile at 8 bar then 7 bar on the machine: (row, the 8 bar document, the file)."""
    first = await app_row(app, client, fake, provider, 8)
    old_file = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    old_doc = copy.deepcopy(next(p for p in fake.profiles if p["id"] == old_file))
    await put(client, await draft_of(app, client, provider, APP_LABEL, 7))
    await pull(app)
    new_file = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    return first, old_doc, new_file


async def test_a_file_edited_to_an_older_version_is_recorded_and_replaced_when_on(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    _, old_doc, file = await two_versions(app, client, fake, provider)
    index = next(i for i, p in enumerate(fake.profiles) if p["id"] == file)
    fake.profiles[index] = {**old_doc, "id": file}

    run = await pull(app)

    summary = summary_of(run)
    assert summary["conflicts"] == [], "an older version put back is no conflict"
    assert [i["device_id"] for i in summary["recorded"]] == [file]
    assert [i["reason"] for i in summary["pushed"]] == ["superseded"]
    assert [i["device_id"] for i in summary["removed"]] == [file]
    [only] = ids_labelled(fake, APP_LABEL)
    assert only != file


async def test_a_file_edited_to_an_older_version_is_recorded_and_removed_when_off(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    first, old_doc, file = await two_versions(app, client, fake, provider)
    await on_machine(client, first["id"], False)  # type: ignore[arg-type]
    index = next(i for i, p in enumerate(fake.profiles) if p["id"] == file)
    fake.profiles[index] = {**old_doc, "id": file}

    run = await pull(app)

    summary = summary_of(run)
    assert summary["conflicts"] == []
    assert [i["device_id"] for i in summary["recorded"]] == [file]
    assert [(i["device_id"], i["reason"]) for i in summary["removed"]] == [(file, "off")]
    assert ids_labelled(fake, APP_LABEL) == []


async def test_a_file_attached_in_a_sync_is_left_alone_in_that_sync_when_on(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    _, old_doc, file = await two_versions(app, client, fake, provider)
    fake.profiles = [p for p in fake.profiles if p["id"] != file] + [{**old_doc, "id": "older1"}]
    fake.ws_requests.clear()

    found = await pull(app)

    assert found.status == "ok", found.error
    assert [i["reason"] for i in summary_of(found)["adopted"]] == ["attached"]
    assert summary_of(found)["failures"] == []
    assert write_frames(fake) == [], "the sync that finds a file does nothing else to the profile"
    assert "older1" in ids(fake)
    # The next sync treats it like any file: the active version is pushed, this one removed.
    nxt = await pull(app)
    assert [i["reason"] for i in summary_of(nxt)["pushed"]] == ["superseded"]
    assert [i["device_id"] for i in summary_of(nxt)["removed"]] == ["older1"]


async def test_a_file_attached_in_a_sync_is_left_alone_in_that_sync_when_off(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    await on_machine(client, row["row"]["id"], False)
    await pull(app)  # removed
    doc = copy.deepcopy(
        data(await client.get(f"/api/profile-versions/{row['row']['current_version_id']}"))[
            "profile"
        ]
    )
    fake.profiles.append({**doc, "id": "relocated"})
    fake.ws_requests.clear()

    found = await pull(app)

    assert found.status == "ok", found.error
    assert [i["reason"] for i in summary_of(found)["adopted"]] == ["attached"]
    assert write_frames(fake) == [] and "relocated" in ids(fake)
    nxt = await pull(app)
    assert [(i["device_id"], i["reason"]) for i in summary_of(nxt)["removed"]] == [
        ("relocated", "off")
    ]


async def test_making_a_version_active_is_refused_when_its_name_is_taken(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    mine = await app_row(app, client, fake, provider, 8)
    # The profile once had another name (an older version carries it), and another profile has
    # taken that name since: the older version cannot come back without two sharing one.
    other = await put(client, await variant_draft(app, client, "Old name", 6))
    taken = data(await client.get(f"/api/profile-versions/{other['current_version_id']}"))
    document = {**taken["profile"], "phases": taken["profile"]["phases"]}
    version, _ = await ProfilesRepository(app.state.db).ensure_version(
        Profile.model_validate({**document, "label": "Old name [AI]", "id": None})
    )
    await ProfileBoardRepository(app.state.db).add_version(mine["id"], version.id, "edit")

    refused = await client.put(
        f"/api/profile-board/{mine['id']}/active-version", json={"version_id": version.id}
    )

    assert refused.status_code == 409
    assert error(refused)["details"]["reason"] == "duplicate_label"


async def test_keeping_the_machines_side_is_refused_when_its_name_is_taken(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    file = row["machine"]["device_id"]
    # Edited on the display, and renamed onto the name of another profile.
    other = row_for(await get_board(client), BASE_LABEL)
    index = next(i for i, p in enumerate(fake.profiles) if p["id"] == file)
    fake.profiles[index] = {**fake.profiles[index], "label": BASE_LABEL, "temperature": 70.0}
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))

    refused = await client.post(
        f"/api/profile-board/{row['row']['id']}/conflict",
        json={"keep": "machine", "content_hash": seen["machine"]["content_hash"]},
    )

    assert refused.status_code == 409 and error(refused)["details"]["reason"] == "duplicate_label"
    assert other["row"]["id"] != row["row"]["id"]


async def test_a_profile_switched_back_on_since_the_plan_keeps_its_file(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    file = row["machine"]["device_id"]
    await on_machine(client, row["row"]["id"], False)
    real = BoardService._apply_off

    async def switch_on_then_apply(self: BoardService, phase: Any, plan: Any) -> None:
        await self.set_on_machine(row["row"]["id"], True)
        await real(self, phase, plan)

    monkeypatch.setattr(BoardService, "_apply_off", switch_on_then_apply)

    run = await pull(app)

    assert file in ids(fake) and summary_of(run)["removed"] == []
    assert any("switched back on" in i["detail"] for i in summary_of(run)["left"])


async def test_the_active_version_must_be_a_whole_number(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    _, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    url = f"/api/profile-board/{row['row']['id']}/active-version"
    for bad in ("5", True, 5.5):
        assert (await client.put(url, json={"version_id": bad})).status_code in (400, 422)


async def test_the_plan_says_what_resuming_would_do_only_while_paused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, _, fake = adopted
    board = app.state.board
    machine = await read_machine(app.state.connection.client)
    quiet = await board.plan(machine, host="h")
    assert quiet.paused is None and quiet.would_do == []

    fresh = copy.deepcopy(fake.profiles[0])
    fresh.update(id="reborn", label="Factory default")
    fake.profiles = [fresh]
    paused = await board.plan(await read_machine(app.state.connection.client), host="h")

    assert paused.paused and paused.actions == []
    assert {a.kind for a in paused.would_do} >= {"push", "adopt"}


async def test_the_resume_preview_counts_files_that_join_a_profile_that_is_off(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    rows = (await get_board(client))["rows"]
    off = next(r for r in rows if not r["utility"])
    await on_machine(client, off["row"]["id"], False)
    off_doc = copy.deepcopy(
        next(p for p in fake.profiles if p["id"] == off["machine"]["device_id"])
    )
    await pull(app)  # its file goes
    fresh = copy.deepcopy(fake.profiles[0])
    fresh.update(id="reborn", label="Factory default")
    fake.profiles = [fresh, {**off_doc, "id": "offcopy"}]
    await pull(app)  # paused
    preview = (await get_board(client))["resume_preview"]
    assert preview["join"] == 2 and preview["remove"] == 1

    await client.post("/api/profile-board/resume")
    first = summary_of(await pull(app))
    second = summary_of(await pull(app))

    assert first["removed"] == [], "the sync that finds a file never removes it"
    assert len(first["pushed"]) == preview["push"]
    assert len(first["removed"]) + len(second["removed"]) == preview["remove"]


async def test_an_off_profile_whose_file_vanished_keeps_the_file_attached_next_and_loses_it_next(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """The stale file id is still on the row when an identical file turns up under another id:
    the sync that attaches it must leave the row standing on it, so the next sync removes it."""
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    file = row["machine"]["device_id"]
    document = copy.deepcopy(next(p for p in fake.profiles if p["id"] == file))
    await on_machine(client, row["row"]["id"], False)
    fake.profiles = [p for p in fake.profiles if p["id"] != file] + [{**document, "id": "moved"}]

    first = await pull(app)

    assert [i["reason"] for i in summary_of(first)["adopted"]] == ["attached"]
    standing = row_for(await get_board(client), BASE_LABEL)["row"]["device_profile_id"]
    assert standing == "moved", "the attach is not undone by the profile's stale file id"
    second = await pull(app)
    assert [(i["device_id"], i["reason"]) for i in summary_of(second)["removed"]] == [
        ("moved", "off")
    ]


async def test_a_profile_switched_off_keeps_its_star_and_nothing_is_sent_for_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    rid, file = row["row"]["id"], row["machine"]["device_id"]
    fake.favorite_profile_ids.add(file)
    await client.put(f"/api/profile-board/{rid}/starred", json={"starred": False})
    await on_machine(client, rid, False)
    plan = row_for(await get_board(client), BASE_LABEL)["planned"]
    assert [a["kind"] for a in plan] == ["remove"], "no star change is planned for it"
    fake.ws_requests.clear()

    await pull(app)

    frames = write_frames(fake)
    assert "req:profiles:favorite" not in frames and "req:profiles:unfavorite" not in frames
    assert row_for(await get_board(client), BASE_LABEL)["starred"] is False


async def test_an_upgrade_never_turns_a_profile_made_on_the_display_into_one_the_sync_removes(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """The upgrade probe: a board-era archive whose mirror holds a profile made on the display
    after adoption, with no board row. The fill makes it a profile that is on, the next syncs
    attach it and remove nothing."""
    from gaggiclanker.db.repos.profile_list import ProfileListBuilder

    app, client, fake = adopted
    made = copy.deepcopy(fake.profiles[0])
    made.update(id="disp9", label="Made on the display", temperature=91.0)
    fake.profiles.append(made)
    fake.favorite_profile_ids.add("disp9")
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    await pull(app)  # mirrored, and with the switch off no row is made for it
    assert all(
        r["row"]["label"] != "Made on the display" for r in (await get_board(client))["rows"]
    )
    await app.state.db.execute("DELETE FROM profile_list_build")
    await ProfileListBuilder(app.state.db).build()
    await app.state.settings_service.apply({"deviceWritesEnabled": True})

    row = row_for(await get_board(client), "Made on the display")
    assert row["on_machine"] is True and row["starred"] is True
    for _ in range(3):
        run = await pull(app)
        assert run.status == "ok", run.error
        assert summary_of(run)["removed"] == []
    assert "disp9" in [str(p["id"]) for p in fake.profiles]


async def test_a_file_that_appears_later_for_a_profile_that_is_off_is_announced_as_removed(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """The steady state: the exact-enabled-set rule. The page says "will be removed" on the row
    once the file is matched, and the sync that matches it names the match in its summary."""
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    assert (await on_machine(client, row["id"], False)).status_code == 200
    await pull(app)  # the file goes
    assert BASE_LABEL not in [p["label"] for p in fake.profiles]
    version = await ProfilesRepository(app.state.db).get_version(row["current_version_id"])
    assert version is not None and version.profile is not None
    again = copy.deepcopy(dict(version.profile))
    again["id"] = "back1"
    fake.profiles.append(again)

    attach = await pull(app)  # matches the file to the off profile
    [matched] = summary_of(attach)["adopted"]
    assert matched["reason"] == "attached"
    assert "switched off, so the next sync removes it" in matched["detail"]
    shown = row_for(await get_board(client), BASE_LABEL)
    assert [a["kind"] for a in shown["planned"]] == ["remove"]

    removal = await pull(app)
    assert [i["device_id"] for i in summary_of(removal)["removed"]] == ["back1"]


async def test_an_upgrade_keeps_a_profile_the_person_deleted_from_the_old_board(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """Case 4 of the upgrade: deleted on the old page, its file stayed on the machine. After the
    fill it is on, and three syncs remove nothing."""
    from gaggiclanker.db.repos.profile_list import ProfileListBuilder

    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    await tombstone(client, row["id"])
    await app.state.db.execute("DELETE FROM profile_list_build")
    await ProfileListBuilder(app.state.db).build()

    revived = row_for(await get_board(client), BASE_LABEL)
    assert revived["on_machine"] is True
    for _ in range(3):
        run = await pull(app)
        assert run.status == "ok", run.error
        assert summary_of(run)["removed"] == []
    assert BASE_LABEL in [p["label"] for p in fake.profiles]


async def test_an_upgrade_never_undoes_a_display_edit_made_after_the_old_delete(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """Case 7: the profile was deleted on the old page and then edited on the display. Three
    syncs after the fill write nothing and the machine's file and content are unchanged."""
    from gaggiclanker.db.repos.profile_list import ProfileListBuilder

    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    await tombstone(client, row["id"])
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    index = next(i for i, p in enumerate(fake.profiles) if p["label"] == BASE_LABEL)
    fake.profiles[index] = {**fake.profiles[index], "temperature": 77.0}
    before = copy.deepcopy(fake.profiles[index])
    await pull(app)  # the edit is mirrored
    await app.state.db.execute("DELETE FROM profile_list_build")
    await ProfileListBuilder(app.state.db).build()
    await app.state.settings_service.apply({"deviceWritesEnabled": True})

    revived = row_for(await get_board(client), BASE_LABEL)
    assert revived["on_machine"] is True and revived["in_conflict"] is False
    version = await ProfilesRepository(app.state.db).get_version(
        revived["row"]["current_version_id"]
    )
    assert version is not None and version.profile is not None
    assert version.profile["temperature"] == 77.0
    for _ in range(3):
        run = await pull(app)
        assert run.status == "ok", run.error
        assert summary_of(run)["writes"] == 0
    assert fake.profiles[index] == before
