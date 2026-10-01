"""The profile board and the write phase of a pull, against the fake machine.

What is asserted is the state the *display* ends up in (which profiles it holds, which are
starred, what was sent in which order) and what the archive says about it afterwards. Every
test runs the real pull (`engine.sync_profiles`), because the write phase is a step of that
pass and has no entry point of its own.

The one rule every scenario here also proves, through :func:`assert_every_delete_was_ours`:
**no profile is ever deleted from the machine without an ``ok`` save of that id by this app
earlier in the audit.**
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository, DeviceWriteWrite
from gaggiclanker.db.repos.sync import SyncRepository, SyncRunRow
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile, profile_content_hash
from gaggiclanker.drafts import board as board_module
from gaggiclanker.drafts.board import _is_settled
from gaggiclanker.drafts.board_plan import ANOTHER_ROW
from gaggiclanker.drafts.machine import (
    CHANGED_SINCE,
    GONE,
    NOT_OURS,
    MachineState,
    Removal,
    read_machine,
)
from gaggiclanker.drafts.machine import place as real_place
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.test_replace import (
    APP_LABEL,
    Live,
    audit,
    draft_of,
    grind_change,
    kinds,
    make_set_on,
    manual_draft,
    set_device_ids,
)
from tests.llm.conftest import FakeProvider

WRITE_FRAMES = {
    "req:profiles:save",
    "req:profiles:delete",
    "req:profiles:select",
    "req:profiles:favorite",
    "req:profiles:unfavorite",
}


async def pull(app: FastAPI) -> SyncRunRow:
    run: SyncRunRow = await app.state.connection.engine.sync_profiles(trigger="test")
    return run


def summary_of(run: SyncRunRow) -> dict[str, Any]:
    assert run.summary is not None and "writes" in run.summary, "the write phase did not run"
    return run.summary


def write_frames(fake: FakeDevice) -> list[str]:
    return [t for t in fake.ws_requests if t in WRITE_FRAMES]


async def get_board(client: httpx.AsyncClient, *, live: bool = True) -> dict[str, Any]:
    return dict(data(await client.get(f"/api/profile-board?live={str(live).lower()}")))


async def approve(client: httpx.AsyncClient, draft: dict[str, Any]) -> None:
    response = await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    assert response.status_code == 200, response.text


async def put(client: httpx.AsyncClient, draft: dict[str, Any], **body: Any) -> dict[str, Any]:
    await approve(client, draft)
    response = await client.post("/api/profile-board", json={"draft_id": draft["id"], **body})
    return dict(data(response))


def _app_ids(fake: FakeDevice) -> set[str]:
    return {str(p["id"]) for p in fake.profiles if p["label"] == APP_LABEL}


def row_for(board: dict[str, Any], label: str) -> dict[str, Any]:
    found = [r for r in board["rows"] if r["row"]["label"] == label]
    assert len(found) == 1, [r["row"]["label"] for r in board["rows"]]
    return dict(found[0])


async def events(app: FastAPI, run_id: int) -> list[Any]:
    return [e for e in await SyncRepository(app.state.db).recent_events(200) if e.run_id == run_id]


async def assert_every_delete_was_ours(app: FastAPI) -> None:
    rows = await audit(app)
    for position, row in enumerate(rows):
        if row.kind == "profile_delete" and row.result == "ok":
            saved_before = [
                r
                for r in rows[:position]
                if r.kind == "profile_save" and r.result == "ok" and r.device_id == row.device_id
            ]
            assert saved_before, f"{row.device_id} was deleted without an ok app save of it"


@pytest.fixture
async def adopted(
    writes_on: Live, fake_device: FakeDevice
) -> tuple[FastAPI, httpx.AsyncClient, FakeDevice]:
    """Writes on, and the first pull done: the machine's profiles are on the board."""
    app, client = writes_on
    run = await pull(app)
    assert run.status == "ok", run.error
    return app, client, fake_device


async def variant_draft(
    app: FastAPI, client: httpx.AsyncClient, name: str, bar: float
) -> dict[str, Any]:
    """A draft of the person's profile carrying a label of its own (so a profile of its own).

    Two live board profiles never share a label, so a test that wants several app profiles
    off the one person's profile names each.
    """
    return await manual_draft(app, client, BASE_LABEL, name, bar)


def app_label_of(name: str) -> str:
    """What the suffix makes of a variant's label."""
    return f"{name} [AI]"


async def draft_from(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider, row: dict[str, Any], bar: float
) -> dict[str, Any]:
    """A draft of the board row's own version (not of the newest version with its label)."""
    version = data(await client.get(f"/api/profile-versions/{row['current_version_id']}"))
    document = dict(version["profile"])
    document["phases"] = [dict(p) for p in document["phases"]]
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    provider.script = [json.dumps({"profile": document, "change_summary": f"{bar} bar."})]
    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": row["current_version_id"], "notes": "change it"},
    )
    return dict(data(response))


async def app_row(
    app: FastAPI,
    client: httpx.AsyncClient,
    fake: FakeDevice,
    provider: FakeProvider,
    bar: float = 8,
    name: str | None = None,
) -> dict[str, Any]:
    """Put a draft of the person's profile on the board and pull: the machine holds a copy.

    ``name`` gives it a label of its own, for a test that wants more than one app profile.
    """
    draft = (
        await draft_of(app, client, provider, BASE_LABEL, bar)
        if name is None
        else await variant_draft(app, client, name, bar)
    )
    row = await put(client, draft)
    run = await pull(app)
    assert run.status == "ok", run.error
    assert len(summary_of(run)["pushed"]) == 1
    return row


# ── the switch ───────────────────────────────────────────────────────


async def test_with_the_switch_off_a_pull_reads_nothing_for_writing_and_writes_nothing(
    live: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = live
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    await put(client, draft)  # editing the board needs no machine and no switch
    fake_device.ws_requests.clear()

    run = await pull(app)

    # Only the count of profiles read: no board keys, because no write phase ran.
    assert run.status == "ok" and run.summary == {"profiles_read": len(fake_device.profiles)}
    assert "req:profiles:load" not in fake_device.ws_requests
    assert write_frames(fake_device) == []
    assert await audit(app) == []
    board = await get_board(client)
    assert board["writes_enabled"] is False and board["adopted"] is False


# ── adoption ─────────────────────────────────────────────────────────


async def test_the_first_pull_with_the_switch_on_adopts_the_machine_and_writes_nothing(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    fake_device.ws_requests.clear()

    run = await pull(app)

    summary = summary_of(run)
    assert run.status == "ok" and summary["writes"] == 0
    assert len(summary["adopted"]) == len(fake_device.profiles)
    assert write_frames(fake_device) == []
    assert await audit(app) == []
    board = await get_board(client)
    assert board["adopted"] is True
    stars = {r["machine"]["device_id"]: r["row"]["on_home_screen"] for r in board["rows"]}
    assert stars == {
        str(p["id"]): str(p["id"]) in fake_device.favorite_profile_ids for p in fake_device.profiles
    }
    assert {r["row"]["origin"] for r in board["rows"]} == {"adopted"}


async def test_adoption_runs_once_and_an_unchanged_board_on_an_in_sync_machine_plans_nothing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    rows_before = len((await get_board(client))["rows"])
    fake.ws_requests.clear()

    run = await pull(app)

    summary = summary_of(run)
    assert summary["adopted"] == [] and summary["writes"] == 0
    # The count of profiles read sits beside the board's keys, not instead of them.
    assert summary["profiles_read"] == len(fake.profiles)
    assert write_frames(fake) == []
    board = await get_board(client)
    assert len(board["rows"]) == rows_before
    assert board["actions"] == []
    assert all(r["planned"] == [] for r in board["rows"])
    # A profile somebody adds on the display afterwards is not adopted by a later pull.
    extra = copy.deepcopy(fake.profiles[0])
    extra.update(id="zzzzzz", label="Added later")
    fake.profiles.append(extra)
    await pull(app)
    assert len((await get_board(client))["rows"]) == rows_before


async def test_adoption_with_an_empty_machine_list_adopts_nothing_and_says_so(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    fake_device.profiles.clear()

    run = await pull(app)

    assert run.status == "error"
    assert (await get_board(client))["adopted"] is False


# ── pushing and replacing ────────────────────────────────────────────


async def test_a_missing_current_version_is_pushed_and_recorded_on_the_draft(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    row = await put(client, draft)
    # Nothing is sent by editing the board, and the preview names what the pull will do.
    assert write_frames(fake) == []
    view = row_for(await get_board(client), APP_LABEL)
    assert [a["kind"] for a in view["planned"]] == ["push"]
    assert view["planned"][0]["reason"] == "missing"

    run = await pull(app)

    assert run.status == "ok", run.error
    [pushed] = summary_of(run)["pushed"]
    assert pushed["reused"] is False, "a push that saved a file is a write"
    device_id = pushed["device_id"]
    assert [str(p["label"]) for p in fake.profiles if p["id"] == device_id] == [APP_LABEL]
    saved = await DeviceWritesRepository(app.state.db).created_by_us(device_id)
    assert saved
    stored = data(await client.get(f"/api/profile-drafts/{draft['id']}"))["draft"]
    assert stored["status"] == "pushed" and stored["pushed_device_profile_id"] == device_id
    view = row_for(await get_board(client), APP_LABEL)
    assert view["row"]["id"] == row["id"] and view["row"]["device_profile_id"] == device_id
    assert view["row"]["pending_draft_id"] is None
    assert view["machine"]["holds_current"] is True
    assert (await get_board(client))["actions"] == []
    # And it is the one write the phase made.
    assert kinds(await audit(app), "profile_save") == [("profile_save", device_id, "ok")]
    kinds_seen = {e.kind for e in await events(app, run.id)}
    assert "board_pushed" in kinds_seen
    status = data(await client.get("/api/sync/status"))
    assert status["last_runs"]["profiles"]["summary"]["pushed"][0]["device_id"] == device_id
    await assert_every_delete_was_ours(app)


async def test_a_new_version_replaces_only_the_copy_the_app_wrote(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    first = await app_row(app, client, fake, provider, 8)
    old_id = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    person_ids = [str(p["id"]) for p in fake.profiles if p["label"] == BASE_LABEL]
    assert old_id in fake.favorite_profile_ids

    second = await put(client, await draft_of(app, client, provider, APP_LABEL, 7))
    assert second["id"] == first["id"], "a new version of the same profile is the same row"
    run = await pull(app)

    assert run.status == "ok", run.error
    summary = summary_of(run)
    [removed] = summary["removed"]
    assert removed["device_id"] == old_id and removed["reason"] == "superseded"
    ids = [str(p["id"]) for p in fake.profiles]
    assert old_id not in ids
    [new_id] = [str(p["id"]) for p in fake.profiles if p["label"] == APP_LABEL]
    assert new_id in fake.favorite_profile_ids
    assert all(pid in ids for pid in person_ids), "the person's own profile stays"
    board = await get_board(client)
    assert row_for(board, APP_LABEL)["row"]["device_profile_id"] == new_id
    assert board["actions"] == []
    await assert_every_delete_was_ours(app)


async def test_a_draft_with_the_label_of_a_profile_the_person_made_waits_until_theirs_is_gone(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    # Carries the app label but was not saved by this app, as a profile made by another
    # tool does: the board treats it as the person's, never pushes or replaces it.
    legacy = copy.deepcopy(fake_device.profiles[0])
    legacy.update(id="legacy", label="Legacy [AI]")
    fake_device.profiles.append(legacy)
    fake_device.favorite_profile_ids.add("legacy")
    await pull(app)  # adoption (and the mirror)
    adopted_row = row_for(await get_board(client), "Legacy [AI]")
    draft = await draft_of(app, client, provider, "Legacy [AI]", 7)
    await approve(client, draft)

    # A draft of it carries the same label, and two live profiles never share one.
    refused = await client.post("/api/profile-board", json={"draft_id": draft["id"]})
    assert refused.status_code == 409
    assert "The board already has Legacy [AI]" in error(refused)["message"]
    assert len((await get_board(client))["rows"]) == len(fake_device.profiles)

    # Taking the person's profile off the board (it stays on the machine) frees the label.
    assert (
        await client.delete(f"/api/profile-board/{adopted_row['row']['id']}")
    ).status_code == 200
    new = dict(data(await client.post("/api/profile-board", json={"draft_id": draft["id"]})))
    assert new["id"] != adopted_row["row"]["id"]
    board = await get_board(client)
    assert [a["kind"] for a in board["actions"]] == ["push", "leave"]

    run = await pull(app)

    summary = summary_of(run)
    assert [i["label"] for i in summary["left"]] == ["Legacy [AI]"], "the person's file is left"
    assert "legacy" in [str(p["id"]) for p in fake_device.profiles]
    assert kinds(await audit(app), "profile_delete") == []


# ── deleting ─────────────────────────────────────────────────────────


async def test_a_deleted_row_is_removed_only_when_the_app_wrote_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    mine = await app_row(app, client, fake, provider)
    board = await get_board(client)
    person = row_for(board, BASE_LABEL)
    mine_id = row_for(board, APP_LABEL)["machine"]["device_id"]
    person_id = person["machine"]["device_id"]
    for row_id in (mine["id"], person["row"]["id"]):
        assert (await client.delete(f"/api/profile-board/{row_id}")).status_code == 200
    preview = (await get_board(client))["actions"]
    assert {(a["kind"], a["device_id"]) for a in preview} == {
        ("remove", mine_id),
        ("leave", person_id),
    }

    run = await pull(app)

    summary = summary_of(run)
    assert [i["device_id"] for i in summary["removed"]] == [mine_id]
    assert [i["device_id"] for i in summary["left"]] == [person_id]
    ids = [str(p["id"]) for p in fake.profiles]
    assert mine_id not in ids and person_id in ids
    assert (await get_board(client))["pending_removals"] == []
    assert summary_of(await pull(app))["writes"] == 0
    await assert_every_delete_was_ours(app)


async def test_deleting_the_selected_profile_selects_another_board_profile_first(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    mine = await app_row(app, client, fake, provider)
    mine_id = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    fake.selected_profile_id = mine_id
    await client.delete(f"/api/profile-board/{mine['id']}")
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    assert fake.selected_profile_id != mine_id
    assert fake.selected_profile_id in [str(p["id"]) for p in fake.profiles]
    order = [t for t in fake.ws_requests if t in WRITE_FRAMES]
    assert order.index("req:profiles:select") < order.index("req:profiles:delete")


# ── the home screen ──────────────────────────────────────────────────


async def test_the_home_screen_flag_sets_and_clears_the_favourite(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    board = await get_board(client)
    starred = row_for(board, BASE_LABEL)
    device_id = starred["machine"]["device_id"]
    # The fake machine starts with nothing on its home screen, so adoption took it as off.
    assert starred["row"]["on_home_screen"] is False and not fake.favorite_profile_ids
    row_url = f"/api/profile-board/{starred['row']['id']}/home-screen"

    on = await client.put(row_url, json={"on": True})
    assert data(on)["on_home_screen"] is True
    assert write_frames(fake) == [], "editing the board sends nothing"
    run = await pull(app)
    assert run.status == "ok", run.error
    assert device_id in fake.favorite_profile_ids and "req:profiles:favorite" in fake.ws_requests

    off = await client.put(row_url, json={"on": False})
    assert data(off)["on_home_screen"] is False
    fake.ws_requests.clear()
    assert [a["kind"] for a in (await get_board(client))["actions"]] == ["home_screen"]
    run = await pull(app)
    assert run.status == "ok", run.error
    assert device_id not in fake.favorite_profile_ids
    assert "req:profiles:unfavorite" in fake.ws_requests
    assert [i["device_id"] for i in summary_of(run)["home_screen"]] == [device_id]
    assert (await get_board(client))["actions"] == []


async def test_a_profile_pushed_off_the_home_screen_is_unstarred_after_the_firmware_stars_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await put(client, await draft_of(app, client, provider, BASE_LABEL, 8))
    await client.put(f"/api/profile-board/{row['id']}/home-screen", json={"on": False})

    run = await pull(app)

    assert run.status == "ok", run.error
    [pushed] = summary_of(run)["pushed"]
    assert pushed["device_id"] in [str(p["id"]) for p in fake.profiles]
    assert pushed["device_id"] not in fake.favorite_profile_ids
    assert (await get_board(client))["actions"] == []


# ── edited on the machine anyway ─────────────────────────────────────


async def test_an_app_profile_edited_on_the_machine_gets_the_boards_version_beside_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    edited_id = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    edited = next(p for p in fake.profiles if p["id"] == edited_id)
    edited["temperature"] = float(edited["temperature"]) + 2
    plan = row_for(await get_board(client), APP_LABEL)["planned"]
    assert [(a["kind"], a["reason"]) for a in plan] == [
        ("push", "edited_on_machine"),
        ("leave", "superseded"),
    ]

    run = await pull(app)

    assert run.status == "ok", run.error
    summary = summary_of(run)
    assert [i["label"] for i in summary["overwritten"]] == [APP_LABEL]
    assert any(
        e.kind == "board_overwrote" and APP_LABEL in e.message for e in await events(app, run.id)
    )
    # Removal expects exactly what the archive recorded for the app's save: an edited copy
    # is the person's now, so it stays, and the pull says so.
    assert [(i["device_id"], i["detail"]) for i in summary["left"]] == [
        (edited_id, "changed on the display since")
    ]
    ids = [str(p["id"]) for p in fake.profiles]
    assert edited_id in ids
    [new_id] = [i for i in ids if i != edited_id and i in _app_ids(fake)]
    assert row_for(await get_board(client), APP_LABEL)["row"]["device_profile_id"] == new_id
    assert (await get_board(client))["actions"] == []
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_profile_the_person_made_is_never_pushed_back_only_reported(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    person = row_for(await get_board(client), BASE_LABEL)
    person_id = person["machine"]["device_id"]
    edited = next(p for p in fake.profiles if p["id"] == person_id)
    edited["temperature"] = float(edited["temperature"]) + 2
    before = len(fake.profiles)
    fake.ws_requests.clear()

    run = await pull(app)

    summary = summary_of(run)
    assert summary["pushed"] == [] and summary["writes"] == 0
    assert [(i["device_id"], i["reason"]) for i in summary["left"]] == [
        (person_id, "edited_on_machine")
    ]
    assert len(fake.profiles) == before and write_frames(fake) == []
    # And the same when the file is gone altogether.
    fake.profiles.remove(edited)
    gone = await pull(app)
    assert [i["reason"] for i in summary_of(gone)["left"]] == ["missing"]
    assert write_frames(fake) == [] and await audit(app) == []


# ── the round trip ───────────────────────────────────────────────────


async def test_a_round_trip_mismatch_removes_the_new_copy_and_keeps_the_previous_version(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    old_id = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    second = await put(client, await draft_of(app, client, provider, APP_LABEL, 7))
    count = len(fake.profiles)
    fake.mutate_on_save = lambda p: {**p, "temperature": float(p.get("temperature", 90)) + 1}

    run = await pull(app)

    assert run.status == "error"
    [failure] = summary_of(run)["failures"]
    assert failure["reason"] == "round_trip" and "removed again" in failure["detail"]
    assert len(fake.profiles) == count, "the copy that did not verify is gone again"
    assert old_id in [str(p["id"]) for p in fake.profiles], "the previous version stays"
    row = row_for(await get_board(client), APP_LABEL)["row"]
    assert (
        row["device_profile_id"] == old_id and row["pending_draft_id"] == second["pending_draft_id"]
    )
    drafts = data(await client.get(f"/api/profile-drafts/{second['pending_draft_id']}"))["draft"]
    assert drafts["status"] == "approved"

    fake.mutate_on_save = None
    ok = await pull(app)
    assert ok.status == "ok", ok.error
    assert old_id not in [str(p["id"]) for p in fake.profiles]
    await assert_every_delete_was_ours(app)


async def test_a_pull_that_fails_halfway_leaves_old_and_new_and_the_next_pull_finishes(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    old_id = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    await put(client, await draft_of(app, client, provider, APP_LABEL, 7))
    fake.error_requests.add("req:profiles:delete")

    run = await pull(app)

    assert run.status == "error"
    labelled = [str(p["id"]) for p in fake.profiles if p["label"] == APP_LABEL]
    assert old_id in labelled and len(labelled) == 2, "both are on the machine, none is lost"
    row = row_for(await get_board(client), APP_LABEL)["row"]
    assert row["device_profile_id"] == old_id, "the row still stands on what it can remove"

    fake.error_requests.clear()
    finished = await pull(app)

    assert finished.status == "ok", finished.error
    [new_id] = [str(p["id"]) for p in fake.profiles if p["label"] == APP_LABEL]
    assert new_id != old_id
    board = await get_board(client)
    assert row_for(board, APP_LABEL)["row"]["device_profile_id"] == new_id
    assert board["actions"] == []
    await assert_every_delete_was_ours(app)


async def test_three_failures_in_a_row_stop_the_phase(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    for bar in (6, 7, 8, 9):
        await put(client, await variant_draft(app, client, f"Variant {bar}", bar))
    fake.error_requests.add("req:profiles:save")

    run = await pull(app)

    assert run.status == "error"
    assert len(summary_of(run)["failures"]) == 3
    assert fake.ws_requests.count("req:profiles:save") == 3


# ── Sets ─────────────────────────────────────────────────────────────


async def test_a_set_version_is_recorded_when_the_pull_puts_the_profile_on_the_machine(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    base = row_for(await get_board(client), BASE_LABEL)
    set_id = await make_set_on(client, "Board set", base["row"]["current_version_id"])
    await grind_change(client, set_id)
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    row = await put(client, draft, set_id=set_id, major=True)
    # The Set brews the person's own profile: it stays on the board as it was, and the
    # draft is a profile of its own.
    assert row["id"] != base["row"]["id"] and row["pending_set_id"] == set_id
    before = await set_device_ids(app, set_id)

    run = await pull(app)

    assert run.status == "ok", run.error
    [pushed] = summary_of(run)["pushed"]
    after = await set_device_ids(app, set_id)
    assert len(after) == len(before) + 1 and after[-1] == pushed["device_id"]
    majors = await app.state.db.fetch_all(
        "SELECT version_major FROM set_versions WHERE set_id = ? ORDER BY version_no", (set_id,)
    )
    assert majors[-1]["version_major"] > majors[-2]["version_major"], "the 'major' answer was kept"
    again = (await get_board(client))["rows"]
    assert all(r["row"]["pending_set_id"] is None for r in again)
    assert summary_of(run)["left"] == []
    assert row_for(await get_board(client), BASE_LABEL)["row"]["id"] == base["row"]["id"]


# ── the plan ─────────────────────────────────────────────────────────


async def test_the_plan_is_the_same_for_the_same_inputs_in_any_order(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    await put(client, await draft_of(app, client, provider, APP_LABEL, 7))
    first = await client.delete(
        f"/api/profile-board/{row_for(await get_board(client), BASE_LABEL)['row']['id']}"
    )
    assert first.status_code == 200
    board = app.state.board
    machine = await read_machine(app.state.connection.client)
    host = app.state.connection.client.host
    reversed_machine = MachineState(profiles=dict(reversed(list(machine.profiles.items()))))

    one = await board.plan(machine, host=host)
    two = await board.plan(machine, host=host)
    three = await board.plan(reversed_machine, host=host)

    assert one.actions, "the scenario has something to plan"
    assert one == two == three
    assert [a.kind for a in one.actions][:2] == ["push", "remove"]


# ── routes ───────────────────────────────────────────────────────────


async def test_the_board_routes_refuse_what_cannot_go_on_the_board(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    unapproved = await client.post("/api/profile-board", json={"draft_id": draft["id"]})
    assert unapproved.status_code == 409 and "approve" in error(unapproved)["message"]

    await put(client, draft)
    again = await client.post("/api/profile-board", json={"draft_id": draft["id"]})
    assert again.status_code == 409
    missing = await client.post("/api/profile-board", json={"draft_id": 9999})
    assert missing.status_code == 404
    assert (await client.delete("/api/profile-board/9999")).status_code == 404
    bad = await client.put("/api/profile-board/1/home-screen", json={"on": "yes"})
    assert bad.status_code == 400
    row_id = row_for(await get_board(client), BASE_LABEL)["row"]["id"]
    await client.delete(f"/api/profile-board/{row_id}")
    assert (await client.delete(f"/api/profile-board/{row_id}")).status_code == 404
    gone = await client.put(f"/api/profile-board/{row_id}/home-screen", json={"on": False})
    assert gone.status_code == 404


async def test_the_preview_before_adoption_lists_every_machine_profile_and_writes_nothing(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    _, client = writes_on
    fake_device.ws_requests.clear()

    board = await get_board(client)

    assert board["adopted"] is False and board["machine_source"] == "machine"
    assert {a["device_id"] for a in board["actions"]} == {
        str(p["id"]) for p in fake_device.profiles
    }
    assert {a["kind"] for a in board["actions"]} == {"adopt"}
    assert write_frames(fake_device) == []


async def test_a_copy_another_set_still_brews_is_left_and_the_row_moves_on(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    mine = row_for(await get_board(client), APP_LABEL)
    old_id = mine["machine"]["device_id"]
    await make_set_on(client, "Stays", mine["row"]["current_version_id"])
    await put(client, await draft_of(app, client, provider, APP_LABEL, 7))
    plan = row_for(await get_board(client), APP_LABEL)["planned"]
    assert [(a["kind"], a["device_id"]) for a in plan] == [("push", None), ("leave", old_id)]

    run = await pull(app)

    assert run.status == "ok", run.error
    [left] = summary_of(run)["left"]
    assert left["device_id"] == old_id and "Stays" in left["detail"]
    assert old_id in [str(p["id"]) for p in fake.profiles]
    assert len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 2
    # Not final: the row keeps standing on the old file and asks again next pull, without
    # adding another copy.
    board = await get_board(client)
    assert [(a["kind"], a["device_id"]) for a in board["actions"]] == [("leave", old_id)]
    assert row_for(board, APP_LABEL)["row"]["device_profile_id"] == old_id
    again = await pull(app)
    assert [i["device_id"] for i in summary_of(again)["left"]] == [old_id]
    assert len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 2
    await assert_every_delete_was_ours(app)


async def test_the_set_the_new_version_is_recorded_on_does_not_keep_its_old_copy(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    mine = row_for(await get_board(client), APP_LABEL)
    old_id = mine["machine"]["device_id"]
    set_id = await make_set_on(client, "Moves", mine["row"]["current_version_id"])
    await put(client, await draft_of(app, client, provider, APP_LABEL, 7), set_id=set_id)

    run = await pull(app)

    assert run.status == "ok", run.error
    assert [i["device_id"] for i in summary_of(run)["removed"]] == [old_id]
    assert summary_of(run)["left"] == []
    assert old_id not in [str(p["id"]) for p in fake.profiles]
    ids = await set_device_ids(app, set_id)
    assert ids[-1] is not None and old_id not in ids
    await assert_every_delete_was_ours(app)


@pytest.mark.parametrize(
    ("removal", "settled"),
    [
        (Removal(removed=True), True),
        (Removal(gone=True, reason=GONE), True),
        (Removal(reason=NOT_OURS), True),
        (Removal(reason=CHANGED_SINCE), True),
        (Removal(reason="still the current version of the Set 'A'"), False),
        (Removal(reason=ANOTHER_ROW), False),
        (Removal(reason="could not be read: timed out"), False),
        (Removal(reason="Delete failed"), False),
    ],
)
def test_a_row_lets_go_of_a_file_only_when_trying_again_cannot_change_the_answer(
    removal: Removal, settled: bool
) -> None:
    assert _is_settled(removal) is settled


async def test_a_push_that_found_the_identical_file_there_is_marked_reused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No save was sent, so the notification must not count it as a write."""
    app, client, _fake = adopted
    await put(client, await draft_of(app, client, provider, BASE_LABEL, 8))

    async def found_there(*args: Any, **kwargs: Any) -> Any:
        placed = await real_place(*args, **kwargs)
        return dataclasses.replace(placed, reused=True)

    monkeypatch.setattr(board_module, "place", found_there)
    run = await pull(app)

    [pushed] = summary_of(run)["pushed"]
    assert pushed["reused"] is True


async def test_a_copy_edited_between_the_read_and_the_delete_is_not_destroyed(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    old_id = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    await put(client, await draft_of(app, client, provider, APP_LABEL, 7))

    async def place_then_edit(*args: Any, **kwargs: Any) -> Any:
        placed = await real_place(*args, **kwargs)
        # Somebody edits the old file on the display while the pull is working.
        old = next(p for p in fake.profiles if p["id"] == old_id)
        old["temperature"] = float(old["temperature"]) + 3
        return placed

    monkeypatch.setattr(board_module, "place", place_then_edit)

    run = await pull(app)

    assert run.status == "ok", run.error
    assert old_id in [str(p["id"]) for p in fake.profiles], "the edit was not destroyed"
    [left] = summary_of(run)["left"]
    assert left["device_id"] == old_id and left["detail"] == "changed on the display since"
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_copy_that_another_board_profile_already_stands_on_is_never_shared(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    host = app.state.connection.client.host
    # The app's own files (an ok save each, the app label), two with identical content and
    # a third that keeps the machine from looking reset.
    for name, label in (("dup0", "Dup [AI]"), ("dup1", "Dup [AI]"), ("keep", "Keep [AI]")):
        twin = copy.deepcopy(fake_device.profiles[0])
        twin.update(id=name, label=label)
        fake_device.profiles.append(twin)
        await DeviceWritesRepository(app.state.db).record(
            DeviceWriteWrite(
                kind="profile_save",
                host=host,
                device_id=name,
                payload_hash=profile_content_hash(Profile.model_validate(twin)),
                result="ok",
            )
        )
    await pull(app)  # adoption: three app rows
    survivor = copy.deepcopy(fake_device.profiles[-3])
    survivor["id"] = "survivor"
    fake_device.profiles = [p for p in fake_device.profiles if p["id"] not in ("dup0", "dup1")]
    fake_device.profiles.append(survivor)

    run = await pull(app)

    assert run.status == "ok", run.error
    rows = [r for r in (await get_board(client))["rows"] if r["row"]["label"] == "Dup [AI]"]
    standing = [r["row"]["device_profile_id"] for r in rows]
    assert "survivor" in standing and len(set(standing)) == 2, "one file, never two rows"
    assert all(r["machine"]["present"] for r in rows)
    assert [i["reason"] for i in summary_of(run)["pushed"]] == ["missing"]


# ── no two live profiles share a label ──────────────────────────────


async def test_two_puts_of_one_label_at_once_make_one_row_and_one_refusal(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    first = await draft_of(app, client, provider, BASE_LABEL, 7)
    second = await draft_of(app, client, provider, BASE_LABEL, 6)
    await approve(client, first)
    await approve(client, second)

    responses = await asyncio.gather(
        *(client.post("/api/profile-board", json={"draft_id": d["id"]}) for d in (first, second))
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    labels = [r["row"]["label"] for r in (await get_board(client, live=False))["rows"]]
    assert labels.count(APP_LABEL) == 1


async def test_a_put_and_a_take_of_one_label_at_once_leave_one_profile_with_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 7)
    await approve(client, draft)
    # A profile made on the display that carries the very label the draft would give.
    made = copy.deepcopy(fake.profiles[0])
    made.update(id="made", label=APP_LABEL)
    made["temperature"] = float(made["temperature"]) + 3
    fake.profiles.append(made)
    await pull(app)  # the mirror learns of it

    responses = await asyncio.gather(
        client.post("/api/profile-board", json={"draft_id": draft["id"]}),
        client.post("/api/profile-board/take", json={"device_profile_id": "made"}),
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    labels = [r["row"]["label"] for r in (await get_board(client, live=False))["rows"]]
    assert labels.count(APP_LABEL) == 1


async def test_adoption_takes_two_profiles_with_one_label_and_reports_the_pair(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    original = fake_device.profiles[0]
    twin = copy.deepcopy(original)
    twin.update(id="twin", temperature=float(twin["temperature"]) + 1)  # same label, other file
    fake_device.profiles.append(twin)
    fake_device.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    assert len(summary_of(run)["adopted"]) == len(fake_device.profiles), "nothing is refused"
    assert write_frames(fake_device) == []
    board = await get_board(client)
    shared = [r for r in board["reports"] if r["reason"] == "duplicate_label"]
    assert len(shared) == 2 and {r["label"] for r in shared} == {original["label"]}
    assert {r["device_id"] for r in shared} == {original["id"], "twin"}
    assert board["actions"] == [], "a pair is said, never acted on"
    # A later sync writes nothing for it either.
    assert write_frames(fake_device) == []
    again = await pull(app)
    assert summary_of(again)["pushed"] == [] and summary_of(again)["removed"] == []
