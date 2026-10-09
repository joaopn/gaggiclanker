"""The profile board and the write phase of a pull, against the fake machine.

What is asserted is the state the *display* ends up in (which profiles it holds, which are
starred, what was sent in which order) and what the archive says about it afterwards. Every
test runs the real pull (`engine.sync_profiles`), because the write phase is a step of that
pass and has no entry point of its own.

Every profile the app has synced is the app's to manage, so what is proved about deletes is
the one guard (a fresh load must hold what the archive recorded), in ``test_board_guards``.
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

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.lineage import person_taken_sentence, taken_name_sentence
from gaggiclanker.db.repos.profile_board import BoardRowWrite, ProfileBoardRepository
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sync import SyncRepository, SyncRunRow
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from gaggiclanker.drafts import board as board_module
from gaggiclanker.drafts.board import _is_settled
from gaggiclanker.drafts.board_plan import ANOTHER_ROW
from gaggiclanker.drafts.machine import (
    CHANGED_SINCE,
    GONE,
    NO_SUCCESSOR,
    MachineState,
    Removal,
    read_machine,
)
from gaggiclanker.drafts.machine import place as real_place
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import (
    APP_LABEL,
    Live,
    audit,
    draft_of,
    grind_change,
    kinds,
    make_set_on,
    manual_draft,
    same_name_draft,
    set_device_ids,
    tombstone,
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


async def approve(app: FastAPI, draft: dict[str, Any]) -> None:
    """Leave a draft ``approved`` without putting it on the board.

    Putting a draft on the board approves it, so a draft only waits in this state when it was
    approved before that was one action (a database can still hold one). The board treats it
    exactly as a drafted one; tests that care set it directly.
    """
    await ProfileDraftsRepository(app.state.db).set_status(draft["id"], "approved")


async def put(client: httpx.AsyncClient, draft: dict[str, Any], **body: Any) -> dict[str, Any]:
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
    # A profile somebody adds on the display afterwards joins the list on the next pull: on,
    # starred as the machine has it, and never removed or changed by the pull that finds it.
    extra = copy.deepcopy(fake.profiles[0])
    extra.update(id="zzzzzz", label="Added later")
    fake.profiles.append(extra)
    fake.ws_requests.clear()
    found = await pull(app)
    assert [(i["reason"], i["label"]) for i in summary_of(found)["adopted"]] == [
        ("unseen", "Added later")
    ]
    assert write_frames(fake) == [] and "zzzzzz" in [str(p["id"]) for p in fake.profiles]
    joined = row_for(await get_board(client), "Added later")
    assert joined["row"]["on_machine"] is True and joined["machine"]["device_id"] == "zzzzzz"
    assert len((await get_board(client))["rows"]) == rows_before + 1


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


async def test_a_draft_named_like_a_profile_that_appeared_is_refused_until_that_one_is_deleted(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    # A fork of another profile, made while no profile has its name (a name already in the list
    # is refused at creation).
    draft = await manual_draft(app, client, BASE_LABEL, "Legacy [AI]", 7)
    # Then a profile made by another tool takes that name (the app did not save it).
    legacy = copy.deepcopy(fake_device.profiles[0])
    legacy.update(id="legacy", label="Legacy [AI]")
    fake_device.profiles.append(legacy)
    fake_device.favorite_profile_ids.add("legacy")
    await pull(app)  # adoption (and the mirror)
    adopted_row = row_for(await get_board(client), "Legacy [AI]")
    # The draft is a fork under that name, not a change to Legacy, so a put would land it on a
    # profile it is not a version of: the landing says it is refused, and the put is.
    await approve(app, draft)
    [found] = [x for x in (await get_board(client))["landings"] if x["draft_id"] == draft["id"]]
    assert found["plain"]["refused"] == person_taken_sentence("Legacy [AI]")
    assert found["plain"]["row_id"] is None
    refused = await client.post("/api/profile-board", json={"draft_id": draft["id"]})
    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence("Legacy [AI]")
    assert len((await get_board(client))["rows"]) == len(fake_device.profiles)

    # Taking the person's profile off the board (it stays on the machine) frees the label.
    assert (await tombstone(client, adopted_row["row"]["id"])).status_code == 200
    new = dict(data(await client.post("/api/profile-board", json={"draft_id": draft["id"]})))
    assert new["id"] != adopted_row["row"]["id"]
    board = await get_board(client)
    assert [a["kind"] for a in board["actions"]] == ["push", "remove"]

    run = await pull(app)

    # Who made the old file decides nothing: it goes like any deleted profile's.
    summary = summary_of(run)
    assert [i["device_id"] for i in summary["removed"]] == ["legacy"]
    assert summary["left"] == []
    assert "legacy" not in [str(p["id"]) for p in fake_device.profiles]


# ── deleting ─────────────────────────────────────────────────────────


async def test_a_deleted_rows_file_is_removed_whoever_wrote_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    mine = await app_row(app, client, fake, provider)
    board = await get_board(client)
    person = row_for(board, BASE_LABEL)
    mine_id = row_for(board, APP_LABEL)["machine"]["device_id"]
    person_id = person["machine"]["device_id"]
    for row_id in (mine["id"], person["row"]["id"]):
        assert (await tombstone(client, row_id)).status_code == 200
    preview = (await get_board(client))["actions"]
    assert {(a["kind"], a["device_id"]) for a in preview} == {
        ("remove", mine_id),
        ("remove", person_id),
    }

    run = await pull(app)

    summary = summary_of(run)
    assert {i["device_id"] for i in summary["removed"]} == {mine_id, person_id}
    assert summary["left"] == []
    ids = [str(p["id"]) for p in fake.profiles]
    assert mine_id not in ids and person_id not in ids
    assert (await get_board(client))["pending_removals"] == []
    assert summary_of(await pull(app))["writes"] == 0


async def test_deleting_the_selected_profile_selects_another_board_profile_first(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    mine = await app_row(app, client, fake, provider)
    mine_id = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    fake.selected_profile_id = mine_id
    await tombstone(client, mine["id"])
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
    row_url = f"/api/profile-board/{starred['row']['id']}/starred"

    on = await client.put(row_url, json={"starred": True})
    assert data(on)["on_home_screen"] is True
    assert write_frames(fake) == [], "editing the board sends nothing"
    run = await pull(app)
    assert run.status == "ok", run.error
    assert device_id in fake.favorite_profile_ids and "req:profiles:favorite" in fake.ws_requests

    off = await client.put(row_url, json={"starred": False})
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
    await client.put(f"/api/profile-board/{row['id']}/starred", json={"starred": False})

    run = await pull(app)

    assert run.status == "ok", run.error
    [pushed] = summary_of(run)["pushed"]
    assert pushed["device_id"] in [str(p["id"]) for p in fake.profiles]
    assert pushed["device_id"] not in fake.favorite_profile_ids
    assert (await get_board(client))["actions"] == []


# ── edited on the machine anyway ─────────────────────────────────────


async def edit_on_display(fake: FakeDevice, device_id: str, bump: float = 2) -> None:
    edited = next(p for p in fake.profiles if p["id"] == device_id)
    edited["temperature"] = float(edited["temperature"]) + bump


async def resolve(
    client: httpx.AsyncClient, row_id: int, keep: str, content_hash: str
) -> httpx.Response:
    return await client.post(
        f"/api/profile-board/{row_id}/conflict", json={"keep": keep, "content_hash": content_hash}
    )


async def test_a_profile_edited_on_the_machine_is_a_conflict_and_nothing_is_written_for_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    other = row_for(await get_board(client), BASE_LABEL)
    edited_id = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    await edit_on_display(fake, edited_id)
    fake.ws_requests.clear()
    view = row_for(await get_board(client), APP_LABEL)
    assert view["in_conflict"] and view["conflict"]["device_id"] == edited_id
    assert view["planned"] == []

    run = await pull(app)

    # Detected by content (the id is the same), nothing is pushed, removed, starred or selected
    # for it, the others sync as usual, and the machine's content is kept as a version.
    assert run.status == "ok", run.error
    summary = summary_of(run)
    assert [i["label"] for i in summary["conflicts"]] == [APP_LABEL]
    assert summary["pushed"] == [] and summary["removed"] == []
    assert write_frames(fake) == []
    assert edited_id in [str(p["id"]) for p in fake.profiles]
    assert any(e.kind == "board_conflict" for e in await events(app, run.id))
    row = row_for(await get_board(client), APP_LABEL)
    listed = await ProfileBoardRepository(app.state.db).list_versions(row["row"]["id"])
    assert (
        listed[0].source == "edited_on_machine"
        and listed[0].version_id != row["row"]["current_version_id"]
    )
    # Still a conflict on the next sync: recording the content did not settle it.
    again = await pull(app)
    assert [i["label"] for i in summary_of(again)["conflicts"]] == [APP_LABEL]
    assert row_for(await get_board(client), BASE_LABEL)["in_conflict"] is False
    assert other["row"]["id"] != row["row"]["id"]


async def test_a_conflict_does_not_stop_the_other_profiles_syncing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    edited_id = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    await edit_on_display(fake, edited_id)
    await put(client, await variant_draft(app, client, "Other", 7))

    run = await pull(app)

    assert [i["label"] for i in summary_of(run)["pushed"]] == ["Other"]
    assert [i["label"] for i in summary_of(run)["conflicts"]] == [APP_LABEL]


async def test_keeping_the_apps_side_replaces_the_file_on_the_next_sync(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    edited_id = row["machine"]["device_id"]
    await edit_on_display(fake, edited_id)
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    assert seen["machine"]["device_id"] == edited_id
    assert seen["machine_profile"]["temperature"] != seen["app_profile"]["temperature"]

    stale = await resolve(client, row["row"]["id"], "app", "not-what-you-saw")
    assert stale.status_code == 409 and error(stale)["details"]["reason"] == "stale_conflict"
    assert (
        await resolve(client, row["row"]["id"], "app", seen["machine"]["content_hash"])
    ).status_code == 200
    run = await pull(app)

    assert run.status == "ok", run.error
    assert summary_of(run)["conflicts"] == []
    assert [i["reason"] for i in summary_of(run)["pushed"]] == ["superseded"]
    assert [i["device_id"] for i in summary_of(run)["removed"]] == [edited_id]
    ids = [str(p["id"]) for p in fake.profiles]
    assert edited_id not in ids and len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 1
    # The edited content is still a version of the profile, so nothing was lost.
    versions = data(await client.get(f"/api/profile-board/{row['row']['id']}/versions"))
    assert "edited_on_machine" in [v["source"] for v in versions["versions"]]
    # A further edit on the display is a new conflict.
    [new_id] = [i for i in ids if i in _app_ids(fake)]
    await edit_on_display(fake, new_id, bump=3)
    assert row_for(await get_board(client), APP_LABEL)["in_conflict"]
    # And with nothing in conflict the route says so.
    other = row_for(await get_board(client), BASE_LABEL)["row"]["id"]
    refused = await resolve(client, other, "app", "x")
    assert refused.status_code == 409 and error(refused)["details"]["reason"] == "no_conflict"


async def test_keeping_the_machines_side_makes_it_the_active_version_and_pushes_nothing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    edited_id = row["machine"]["device_id"]
    await edit_on_display(fake, edited_id)
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))

    kept = await resolve(client, row["row"]["id"], "machine", seen["machine"]["content_hash"])

    assert kept.status_code == 200, kept.text
    assert data(kept)["current_version_id"] == seen["machine"]["version_id"]
    fake.ws_requests.clear()
    run = await pull(app)
    assert run.status == "ok" and summary_of(run)["conflicts"] == []
    assert write_frames(fake) == [] and edited_id in [str(p["id"]) for p in fake.profiles]
    assert row_for(await get_board(client), APP_LABEL)["in_conflict"] is False


async def test_a_new_file_with_a_profiles_name_and_other_content_is_a_conflict(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    person = row_for(await get_board(client), BASE_LABEL)
    # The profile's file is gone and one made outside the app carries its name.
    gone = person["machine"]["device_id"]
    made = copy.deepcopy(next(p for p in fake.profiles if p["id"] == gone))
    made.update(id="outside", temperature=float(made["temperature"]) + 2)
    fake.profiles = [p for p in fake.profiles if p["id"] != gone] + [made]
    fake.ws_requests.clear()
    assert any(a["reason"] == "conflict" for a in (await get_board(client))["actions"])

    await pull(app)  # attached as the machine's side
    run = await pull(app)

    assert [i["label"] for i in summary_of(run)["conflicts"]] == [BASE_LABEL]
    assert write_frames(fake) == []
    assert "outside" in [str(p["id"]) for p in fake.profiles]


async def test_a_relocated_file_with_the_active_content_is_not_a_conflict(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    person = row_for(await get_board(client), BASE_LABEL)
    gone = person["machine"]["device_id"]
    same = copy.deepcopy(next(p for p in fake.profiles if p["id"] == gone))
    same["id"] = "relocated"
    fake.profiles = [p for p in fake.profiles if p["id"] != gone] + [same]

    run = await pull(app)

    # The row holds a file with exactly its active content, under whatever id.
    assert summary_of(run)["conflicts"] == [] and summary_of(run)["pushed"] == []
    again = await pull(app)
    assert summary_of(again)["conflicts"] == [] and summary_of(again)["writes"] == 0


async def test_a_switched_off_profile_whose_file_holds_what_was_recorded_is_removed_not_a_conflict(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    person = row_for(await get_board(client), BASE_LABEL)
    await client.put(f"/api/profile-board/{person['row']['id']}/on-machine", json={"on": False})

    run = await pull(app)

    assert summary_of(run)["conflicts"] == []
    assert [i["reason"] for i in summary_of(run)["removed"]] == ["off"]


async def test_a_profile_switched_off_and_edited_on_the_display_is_a_conflict_not_removed(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    person = row_for(await get_board(client), BASE_LABEL)
    await client.put(f"/api/profile-board/{person['row']['id']}/on-machine", json={"on": False})
    await edit_on_display(fake, person["machine"]["device_id"])

    run = await pull(app)

    assert [i["label"] for i in summary_of(run)["conflicts"]] == [BASE_LABEL]
    assert summary_of(run)["removed"] == []
    assert person["machine"]["device_id"] in [str(p["id"]) for p in fake.profiles]


async def test_a_profile_the_person_made_is_pushed_back_when_its_file_is_gone(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    person = row_for(await get_board(client), BASE_LABEL)
    gone = person["machine"]["device_id"]
    fake.profiles[:] = [p for p in fake.profiles if str(p["id"]) != gone]

    run = await pull(app)

    assert [i["reason"] for i in summary_of(run)["pushed"]] == ["missing"]
    assert [p["label"] for p in fake.profiles].count(BASE_LABEL) == 1


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

    # Not tried again: no save is sent, and the plan says why.
    fake.mutate_on_save = None
    fake.ws_requests.clear()
    quiet = await pull(app)
    assert write_frames(fake) == [] and old_id in [str(p["id"]) for p in fake.profiles]
    assert [i["reason"] for i in summary_of(quiet)["left"]] == ["did_not_verify"]
    assert row_for(await get_board(client), APP_LABEL)["row"]["failed_version_id"] is not None
    # Making the same version active again asks for exactly one more try.
    row_id = row["id"]
    again = await client.put(
        f"/api/profile-board/{row_id}/active-version",
        json={"version_id": row["current_version_id"]},
    )
    assert again.status_code == 200 and data(again)["failed_version_id"] is None
    ok = await pull(app)
    assert ok.status == "ok", ok.error
    assert old_id not in [str(p["id"]) for p in fake.profiles]
    assert fake.ws_requests.count("req:profiles:save") == 1
    final = await pull(app)
    assert summary_of(final)["pushed"] == [] and summary_of(final)["left"] == []


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
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    row = await put(client, draft, set_id=set_id, major=True)
    # The Set brews the person's own profile, and the draft keeps its name: a new version of it.
    assert row["id"] == base["row"]["id"] and row["pending_set_id"] == set_id
    before = await set_device_ids(app, set_id)

    run = await pull(app)

    assert run.status == "ok", run.error
    [pushed] = summary_of(run)["pushed"]
    after = await set_device_ids(app, set_id)
    assert len(after) == len(before) + 1 and after[-1] == pushed["device_id"]
    majors = await app.state.db.fetch_all(
        "SELECT version_major FROM set_versions WHERE set_id = ? ORDER BY created_at, id", (set_id,)
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
    first = await tombstone(client, row_for(await get_board(client), BASE_LABEL)["row"]["id"])
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
    discarded = dict(data(await client.post(f"/api/profile-drafts/{draft['id']}/discard")))
    refused = await client.post("/api/profile-board", json={"draft_id": discarded["id"]})
    assert (
        refused.status_code == 409
        and "discarded proposal cannot be made active" in error(refused)["message"]
    )
    other = await draft_of(app, client, provider, BASE_LABEL, 7)

    await put(client, other)
    draft = other
    again = await client.post("/api/profile-board", json={"draft_id": draft["id"]})
    assert again.status_code == 409
    missing = await client.post("/api/profile-board", json={"draft_id": 9999})
    assert missing.status_code == 404
    bad = await client.put("/api/profile-board/1/starred", json={"starred": "yes"})
    assert bad.status_code == 400
    row_id = row_for(await get_board(client), BASE_LABEL)["row"]["id"]
    await tombstone(client, row_id)
    gone = await client.put(f"/api/profile-board/{row_id}/starred", json={"starred": False})
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


async def test_a_copy_another_set_brews_is_replaced_all_the_same(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """Changing the active version is the person's explicit choice: a Set brewing the old copy
    does not hold it back (the page warns beforehand)."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    mine = row_for(await get_board(client), APP_LABEL)
    old_id = mine["machine"]["device_id"]
    set_id = await make_set_on(client, "Stays", mine["row"]["current_version_id"])
    await put(client, await draft_of(app, client, provider, APP_LABEL, 7))
    plan = row_for(await get_board(client), APP_LABEL)["planned"]
    assert [(a["kind"], a["device_id"]) for a in plan] == [("push", None), ("remove", old_id)]

    run = await pull(app)

    assert run.status == "ok", run.error
    assert [i["device_id"] for i in summary_of(run)["removed"]] == [old_id]
    assert summary_of(run)["left"] == []
    assert old_id not in [str(p["id"]) for p in fake.profiles]
    assert len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 1
    # The Set keeps what it brewed (its profile version); it just stops naming a file that is gone.
    assert old_id not in await set_device_ids(app, set_id)
    assert (await get_board(client))["actions"] == []


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


@pytest.mark.parametrize(
    ("removal", "settled"),
    [
        (Removal(removed=True), True),
        (Removal(gone=True, reason=GONE), True),
        # A file changed since it was recorded is recorded by the next sync, which then decides;
        # nothing is final any more.
        (Removal(reason=CHANGED_SINCE), False),
        (Removal(reason=NO_SUCCESSOR), False),
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
    await pull(app)  # adoption
    # Two profiles with one label and identical content, as a database from before the list
    # refused such a pair can hold (a pull now attaches a second file to the first profile
    # instead), and a third that keeps the machine from looking reset.
    profiles = ProfilesRepository(app.state.db)
    board = ProfileBoardRepository(app.state.db)
    for name, label in (("dup0", "Dup [AI]"), ("dup1", "Dup [AI]"), ("keep", "Keep [AI]")):
        twin = copy.deepcopy(fake_device.profiles[0])
        twin.update(id=name, label=label)
        fake_device.profiles.append(twin)
        version, _ = await profiles.ensure_version(Profile.model_validate(twin))
        await board.insert(
            BoardRowWrite(
                label=label,
                current_version_id=version.id,
                device_profile_id=name,
                device_version_id=version.id,
                origin="draft",
            )
        )
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


async def test_two_puts_of_one_new_name_at_once_make_one_row_and_refuse_the_other(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """Two forks given the same new name: serialised, the first makes the profile and the second
    is refused, because the name is a profile by then."""
    app, client, _ = adopted
    first = await draft_of(app, client, provider, BASE_LABEL, 7)
    second = await draft_of(app, client, provider, BASE_LABEL, 6)
    await approve(app, first)
    await approve(app, second)

    responses = await asyncio.gather(
        *(client.post("/api/profile-board", json={"draft_id": d["id"]}) for d in (first, second))
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    labels = [r["row"]["label"] for r in (await get_board(client, live=False))["rows"]]
    assert labels.count(APP_LABEL) == 1


async def test_two_puts_of_one_profile_at_once_make_both_versions_of_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """Two changes that keep the profile's name are versions of it, in either order."""
    app, client, _ = adopted
    first = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    second = await same_name_draft(app, client, provider, BASE_LABEL, 6)
    await approve(app, first)
    await approve(app, second)

    responses = await asyncio.gather(
        *(client.post("/api/profile-board", json={"draft_id": d["id"]}) for d in (first, second))
    )

    assert sorted(r.status_code for r in responses) == [201, 201]
    rows = (await get_board(client, live=False))["rows"]
    assert [r["row"]["label"] for r in rows].count(BASE_LABEL) == 1
    made = row_for(await get_board(client, live=False), BASE_LABEL)
    versions = data(await client.get(f"/api/profile-board/{made['row']['id']}/versions"))
    assert len(versions["versions"]) == 3


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
    # Two live profiles never share a label: the second file is said, not made a profile.
    assert len(summary_of(run)["adopted"]) == len(fake_device.profiles) - 1
    assert write_frames(fake_device) == []
    board = await get_board(client)
    extra = [r for r in board["reports"] if r["reason"] == "extra_copy"]
    assert [r["device_id"] for r in extra] == ["twin"]
    assert len([r for r in board["rows"] if r["row"]["label"] == original["label"]]) == 1
    assert board["actions"] == [], "a second file is said, never acted on"
    # A later sync writes nothing for it either, and never removes it.
    assert write_frames(fake_device) == []
    again = await pull(app)
    assert summary_of(again)["pushed"] == [] and summary_of(again)["removed"] == []
    assert "twin" in [str(p["id"]) for p in fake_device.profiles]


# ── a signature follows a profile through the board ─────────────────


async def _confirmed_signature(app: FastAPI, version_id: int) -> int:
    """One confirmed expectation on a profile version: its first phase must begin."""
    from gaggiclanker.domain.signature import ExpectationInput
    from gaggiclanker.signatures.service import SignatureService

    version = await ProfilesRepository(app.state.db).get_version(version_id)
    assert version is not None
    first = version.profile["phases"][0]["name"]  # type: ignore[index]
    service = SignatureService(app.state.db)
    (row,) = await service.propose(
        version_id, [ExpectationInput(tier="critical", kind="reached", phase=first)], reason="r"
    )
    assert row.status == "confirmed"
    return row.id


async def _carried_from(app: FastAPI, version_id: int) -> list[int | None]:
    from gaggiclanker.db.repos.signatures import SignatureRepository

    rows = await SignatureRepository(app.state.db).for_version(version_id)
    assert {r.status for r in rows} <= {"confirmed"}
    return [r.carried_from_id for r in rows]


async def test_the_content_kept_from_a_conflict_carries_the_files_signature_in_force(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    expectation = await _confirmed_signature(app, row["row"]["current_version_id"])
    await edit_on_display(fake, row["machine"]["device_id"])

    await pull(app)

    listed = await ProfileBoardRepository(app.state.db).list_versions(row["row"]["id"])
    assert listed[0].source == "edited_on_machine"
    assert await _carried_from(app, listed[0].version_id) == [expectation]


async def test_a_file_new_to_the_machine_under_a_profiles_label_carries_its_signature_in_force(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    person = row_for(await get_board(client), BASE_LABEL)
    expectation = await _confirmed_signature(app, person["row"]["current_version_id"])
    gone = person["machine"]["device_id"]
    made = copy.deepcopy(next(p for p in fake.profiles if p["id"] == gone))
    made.update(id="outside", temperature=float(made["temperature"]) + 2)
    fake.profiles = [p for p in fake.profiles if p["id"] != gone] + [made]

    await pull(app)  # attached as the machine's side
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{person['row']['id']}/conflict"))
    kept = await resolve(client, person["row"]["id"], "machine", seen["machine"]["content_hash"])

    assert kept.status_code == 200, kept.text
    machine_version = data(kept)["current_version_id"]
    assert machine_version != person["row"]["current_version_id"]
    assert await _carried_from(app, machine_version) == [expectation]


async def test_a_profile_made_on_the_display_carries_nothing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    person = row_for(await get_board(client), BASE_LABEL)
    await _confirmed_signature(app, person["row"]["current_version_id"])
    made = copy.deepcopy(next(p for p in fake.profiles if p["label"] == BASE_LABEL))
    made.update(id="display-made", label="Made on the display")
    fake.profiles = [*fake.profiles, made]

    await pull(app)

    new = row_for(await get_board(client), "Made on the display")
    assert await _carried_from(app, new["row"]["current_version_id"]) == []
    from gaggiclanker.db.repos.signatures import SignatureRepository

    rows = await SignatureRepository(app.state.db).for_version(new["row"]["current_version_id"])
    assert rows == []


async def test_a_file_attached_with_an_older_versions_content_is_not_carried_onto_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The signature runs forwards: it is never carried again onto a version the profile had."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider, bar=8)
    first = row_for(await get_board(client), APP_LABEL)
    older_file = copy.deepcopy(next(p for p in fake.profiles if p["label"] == APP_LABEL))
    await app_row(app, client, fake, provider, bar=7)
    current = row_for(await get_board(client), APP_LABEL)
    assert current["row"]["current_version_id"] != first["row"]["current_version_id"]
    await _confirmed_signature(app, current["row"]["current_version_id"])
    # The reset: the profile's file is gone and one with the content of its older version stands
    # under its label.
    older_file["id"] = "after-a-reset"
    fake.profiles = [p for p in fake.profiles if p["id"] != current["machine"]["device_id"]] + [
        older_file
    ]

    run = await pull(app)

    assert [a["reason"] for a in summary_of(run)["adopted"]] == ["attached"]
    row_id = current["row"]["id"]
    versions = await ProfileBoardRepository(app.state.db).list_versions(row_id)
    assert first["row"]["current_version_id"] in [v.version_id for v in versions]
    assert await _carried_from(app, first["row"]["current_version_id"]) == []
