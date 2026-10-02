"""What the board's write phase must never do, each from a scenario that once did it.

The scenarios are the ones an adversarial read of the first version found: a deleted row's
removal taking a file a live row stands on, a pulled profile that never met the safety policy,
an unreadable file read as a gone one, a second path to the machine for the same draft, a
machine that was reset. Each asserts the safe outcome through the fake machine and the audit.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository, DeviceWriteWrite
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile, profile_content_hash
from gaggiclanker.drafts import board as board_module
from gaggiclanker.drafts.board_plan import EDITED
from gaggiclanker.drafts.machine import place as real_place
from gaggiclanker.drafts.machine import remove_profile as real_remove
from gaggiclanker.infra.errors import Conflict
from tests.drafts.conftest import BASE_LABEL, data
from tests.drafts.helpers import (
    APP_LABEL,
    Live,
    audit,
    document_of,
    draft_of,
    kinds,
    make_set_on,
    set_device_ids,
)
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

__all__ = ["adopted"]  # the fixture, re-exported for this module


def ids(fake: FakeDevice) -> list[str]:
    return [str(p["id"]) for p in fake.profiles]


def fail_loads(fake: FakeDevice, failing: set[str]) -> None:
    """Make the fake answer a load of these ids with an error, as a flaky display does."""
    original = fake._handle_request

    async def wrapped(socket: Any, raw: str) -> None:
        message = json.loads(raw)
        if message.get("tp") == "req:profiles:load" and message.get("id") in failing:
            fake.ws_requests.append("req:profiles:load")
            await fake._reply(socket, "req:profiles:load", message.get("rid"), error="not found")
            return
        await original(socket, raw)

    fake._handle_request = wrapped  # type: ignore[method-assign]


async def staged_copy_of(
    app: FastAPI, client: httpx.AsyncClient, fake: FakeDevice, row: dict[str, Any], file: str
) -> dict[str, Any]:
    """A draft carrying exactly the document a file on the machine holds (a stage-as-is)."""
    response = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": row["current_version_id"],
            "profile": document_of(fake, file),
            "change_summary": "as is",
        },
    )
    assert response.status_code == 201, response.text
    return dict(data(response))


# ── a deleted row's file is not another row's ───────────────────────


async def test_a_deleted_rows_removal_never_takes_the_file_a_live_row_would_hold(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """A live row whose content a deleted row's file also holds does not take that file."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    first = row_for(await get_board(client), APP_LABEL)
    file = first["machine"]["device_id"]
    version = first["row"]["current_version_id"]
    # The live row has let go of its file (a refusal once released it), and a deleted row
    # stands on that file with the same content: the live row must not take it back.
    await app.state.db.execute(
        "UPDATE profile_board SET device_profile_id = NULL, device_version_id = NULL WHERE id = ?",
        (first["row"]["id"],),
    )
    await app.state.db.execute(
        "INSERT INTO profile_board (label, current_version_id, device_profile_id, "
        "device_version_id, origin, deleted_at) VALUES ('Old', ?, ?, ?, 'draft', "
        "'2026-10-01T00:00:00.000Z')",
        (version, file, version),
    )

    run = await pull(app)

    assert run.status == "ok", run.error
    board = await get_board(client)
    standing = row_for(board, APP_LABEL)["row"]["device_profile_id"]
    assert standing is not None and standing in ids(fake), "the live row stands on a real file"
    assert standing != file, "and not on one a deleted row's removal had to take"
    assert file not in ids(fake)


async def test_putting_a_deleted_profile_back_revives_its_row_instead_of_pushing_a_copy(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    first = row_for(await get_board(client), APP_LABEL)
    file = first["machine"]["device_id"]
    await make_set_on(client, "Holds", first["row"]["current_version_id"])
    await client.delete(f"/api/profile-board/{first['row']['id']}")
    again = await put(client, await staged_copy_of(app, client, fake, first["row"], file))
    assert again["id"] == first["row"]["id"], "the same profile is the same row"

    for _ in range(3):
        run = await pull(app)

    assert run.status == "ok", run.error
    assert len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 1
    assert row_for(await get_board(client), APP_LABEL)["row"]["device_profile_id"] == file
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_removal_is_refused_while_a_live_row_stands_on_the_file(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The execution-time check: the plan is stale by the time the delete is asked for."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    old = row_for(await get_board(client), APP_LABEL)
    old_id = old["machine"]["device_id"]
    await put(client, await draft_of(app, client, provider, APP_LABEL, 7))
    claimed: list[int] = []

    async def place_then_claim(*args: Any, **kwargs: Any) -> Any:
        placed = await real_place(*args, **kwargs)
        # Another board profile comes to stand on the old file while the pull works (the
        # schema forbids it now, so the test lifts that to prove the delete guard itself).
        await app.state.db.execute("DROP INDEX IF EXISTS idx_profile_board_live_device")
        await app.state.db.execute(
            "INSERT INTO profile_board (label, current_version_id, device_profile_id, "
            "device_version_id, origin) VALUES ('Squatter', ?, ?, ?, 'adopted')",
            (old["row"]["current_version_id"], old_id, old["row"]["current_version_id"]),
        )
        claimed.append(1)
        return placed

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(board_module, "place", place_then_claim)
    try:
        run = await pull(app)
    finally:
        monkeypatch.undo()

    assert claimed and run.status == "ok", run.error
    assert old_id in ids(fake), "the file a live row stands on stays"
    assert kinds(await audit(app), "profile_delete") == []
    assert any("another profile" in i["detail"] for i in summary_of(run)["left"])


# ── an unreadable load is not a gone file ───────────────────────────


async def test_an_unreadable_file_is_neither_pushed_again_nor_forgotten(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    x = await app_row(app, client, fake, provider, 8)
    xfile = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    await put(client, await variant_draft(app, client, "Second", 7))
    await pull(app)
    yrow = next(
        r
        for r in (await get_board(client))["rows"]
        if r["row"]["id"] != x["id"] and r["row"]["origin"] == "draft"
    )
    yfile = yrow["machine"]["device_id"]
    await client.delete(f"/api/profile-board/{yrow['row']['id']}")
    count = len([p for p in fake.profiles if str(p["label"]).endswith("[AI]")])
    fail_loads(fake, {xfile, yfile})
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "error"
    assert {f["device_id"] for f in summary_of(run)["failures"]} == {xfile, yfile}
    assert write_frames(fake) == [], "nothing is pushed or removed for what could not be read"
    assert len([p for p in fake.profiles if str(p["label"]).endswith("[AI]")]) == count
    board = await get_board(client)
    assert [r["id"] for r in board["pending_removals"]] == [yrow["row"]["id"]]
    assert row_for(board, APP_LABEL)["row"]["device_profile_id"] == xfile


# ── only the app's own versions are pushed, and through the policy ──


async def test_a_profile_the_policy_refuses_is_not_pushed_back_after_a_machine_reset(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    """Every profile is pushed back, whoever made it; one outside the bounds says why and waits."""
    app, _ = writes_on
    long_soak = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    long_soak.update(id="hot", label="My long soak")
    long_soak["phases"][0]["duration"] = 200  # beyond the policy's 120 s
    fake_device.profiles.append(long_soak)
    await pull(app)
    fake_device.profiles = [p for p in fake_device.profiles if p["id"] == "9bar"]
    fake_device.ws_requests.clear()

    run = await pull(app)

    assert not [p for p in fake_device.profiles if p["label"] == "My long soak"]
    failures = summary_of(run)["failures"]
    assert [(f["label"], f["reason"]) for f in failures] == [("My long soak", "policy")]
    assert "phase" in failures[0]["detail"] or "duration" in failures[0]["detail"]
    pushed = {i["label"] for i in summary_of(run)["pushed"]}
    assert "Adaptive v2" in pushed and "My long soak" not in pushed, "the rest is not blocked"


async def test_a_version_that_breaks_the_bounds_as_they_are_now_is_not_pushed(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await put(client, await draft_of(app, client, provider, BASE_LABEL, 8))
    # The temperature was valid when it was drafted; the person has tightened the bound since.
    await app.state.settings_service.apply({"profilePolicyTemperatureMaxC": 90})
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "error"
    [failure] = summary_of(run)["failures"]
    assert failure["reason"] == "policy" and "temperature" in failure["detail"]
    assert APP_LABEL not in [p["label"] for p in fake.profiles]
    assert write_frames(fake) == []
    # Back inside the bounds, the next pull pushes it.
    await app.state.settings_service.apply({"profilePolicyTemperatureMaxC": 100})
    ok = await pull(app)
    assert ok.status == "ok" and len(summary_of(ok)["pushed"]) == 1


async def test_a_machine_that_cannot_round_trip_costs_three_saves_a_sync_not_a_flood(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """Three failed profiles in a row stop the pass, so a firmware that mangles every save is
    asked for three saves (each taken off again) per sync, not for every profile."""
    app, _, fake = adopted
    fake.profiles = [p for p in fake.profiles if p["id"] == "9bar"]
    fake.mutate_on_save = lambda p: {**p, "temperature": float(p.get("temperature", 90)) + 1}
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "error"
    assert fake.ws_requests.count("req:profiles:save") == 3
    assert fake.ws_requests.count("req:profiles:delete") == 3
    assert [p["id"] for p in fake.profiles] == ["9bar"], "every bad copy was taken off again"


async def test_a_copy_that_does_not_verify_and_cannot_be_removed_is_not_retried_each_pull(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await put(client, await draft_of(app, client, provider, BASE_LABEL, 8))
    fake.mutate_on_save = lambda p: {**p, "temperature": float(p.get("temperature", 90)) + 1}
    fake.error_requests.add("req:profiles:delete")
    first = await pull(app)
    assert first.status == "error"
    saves = fake.ws_requests.count("req:profiles:save")
    assert saves == 1

    for _ in range(2):
        again = await pull(app)
        assert [i["reason"] for i in summary_of(again)["left"]] == ["did_not_verify"]
    assert fake.ws_requests.count("req:profiles:save") == saves

    # A changed profile is a new try.
    fake.mutate_on_save = None
    fake.error_requests.clear()
    newer = await put(client, await draft_from(app, client, provider, row, 7))
    assert newer["id"] == row["id"], "the new version continues the same profile"
    # The bad copy that could not be taken off is a file with this profile's name and content it
    # never had: a conflict, which a person settles by keeping the app's side.
    await pull(app)  # attached as the machine's side
    held = await pull(app)
    assert [i["label"] for i in summary_of(held)["conflicts"]] == [APP_LABEL]
    seen = data(await client.get(f"/api/profile-board/{row['id']}/conflict"))
    await client.post(
        f"/api/profile-board/{row['id']}/conflict",
        json={"keep": "app", "content_hash": seen["machine"]["content_hash"]},
    )
    ok = await pull(app)
    assert ok.status == "ok", ok.error
    assert len(summary_of(ok)["pushed"]) >= 1


# ── a draft on the board is not pushed by hand ──────────────────────


async def test_a_draft_turned_down_after_it_went_on_the_board_is_pushed_but_not_recorded(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    base = row_for(await get_board(client), BASE_LABEL)
    set_id = await make_set_on(client, "Declined", base["row"]["current_version_id"])
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    await put(client, draft, set_id=set_id)
    discarded = await client.post(f"/api/profile-drafts/{draft['id']}/discard", json={})
    assert discarded.status_code == 200
    before = await set_device_ids(app, set_id)

    run = await pull(app)

    assert run.status == "ok", run.error
    assert await set_device_ids(app, set_id) == before, "no second Set version"
    row = row_for(await get_board(client), APP_LABEL)["row"]
    assert row["pending_draft_id"] is None and row["pending_set_id"] is None


async def test_the_draft_row_says_the_pull_saved_the_file(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    await put(client, draft)
    await pull(app)
    stored = data(await client.get(f"/api/profile-drafts/{draft['id']}"))["draft"]
    assert stored["pushed_saved"] is True


async def test_a_pull_cut_off_after_the_save_still_records_that_it_saved(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client, fake = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    await put(client, draft)
    real_record = board_module.BoardService._record
    calls = {"n": 0}

    async def cut_off(self: Any, *args: Any, **kwargs: Any) -> None:
        if args[1].row.pending_draft_id is not None:
            calls["n"] += 1
        if args[1].row.pending_draft_id is not None and calls["n"] == 1:
            raise RuntimeError("the process stopped here")
        await real_record(self, *args, **kwargs)

    monkeypatch.setattr(board_module.BoardService, "_record", cut_off)
    first = await pull(app)
    assert first.status == "error"
    assert len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 1

    second = await pull(app)

    assert second.status == "ok", second.error
    assert len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 1
    stored = data(await client.get(f"/api/profile-drafts/{draft['id']}"))["draft"]
    assert stored["status"] == "pushed" and stored["pushed_saved"] is True


async def test_a_draft_put_on_the_profile_during_a_pull_keeps_its_pending_record(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    await put(client, await draft_of(app, client, provider, APP_LABEL, 7))
    newer = await draft_of(app, client, provider, APP_LABEL, 6)
    await approve(app, newer)

    async def place_then_put_newer(*args: Any, **kwargs: Any) -> Any:
        placed = await real_place(*args, **kwargs)
        response = await client.post("/api/profile-board", json={"draft_id": newer["id"]})
        assert response.status_code == 201, response.text
        return placed

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(board_module, "place", place_then_put_newer)
    try:
        run = await pull(app)
    finally:
        monkeypatch.undo()

    assert run.status == "ok", run.error
    row = row_for(await get_board(client), APP_LABEL)["row"]
    assert row["pending_draft_id"] == newer["id"], "the newer draft is still waiting"


# ── the order of work, and what a failure keeps ─────────────────────


async def test_new_profiles_are_saved_before_any_deleted_profile_is_removed(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    old = await app_row(app, client, fake, provider, 8)
    await client.delete(f"/api/profile-board/{old['id']}")
    await put(client, await draft_of(app, client, provider, BASE_LABEL, 6))
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    frames = write_frames(fake)
    assert "req:profiles:save" in frames and "req:profiles:delete" in frames
    assert frames.index("req:profiles:save") < frames.index("req:profiles:delete")


async def test_a_deleted_copy_edited_on_the_display_is_recorded_first_and_removed_by_the_next_sync(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    file = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    edited = next(p for p in fake.profiles if p["id"] == file)
    edited["temperature"] = float(edited["temperature"]) + 2
    await client.delete(f"/api/profile-board/{row['id']}")

    run = await pull(app)

    # A file edited since the last read is never deleted unseen: this sync records what it
    # holds, and the next one decides.
    assert file in ids(fake)
    assert [(i["device_id"], i["detail"]) for i in summary_of(run)["left"]] == [(file, EDITED)]
    assert [i["device_id"] for i in summary_of(run)["recorded"]] == [file]
    assert kinds(await audit(app), "profile_delete") == []
    assert [r["id"] for r in (await get_board(client))["pending_removals"]] == [row["id"]]

    again = await pull(app)

    assert [i["device_id"] for i in summary_of(again)["removed"]] == [file]
    assert file not in ids(fake)
    assert (await get_board(client))["pending_removals"] == []


async def test_a_deleted_copy_is_kept_on_the_row_when_the_machine_could_not_delete_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    file = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    await client.delete(f"/api/profile-board/{row['id']}")
    fake.error_requests.add("req:profiles:delete")

    failed = await pull(app)

    assert failed.status == "error" and file in ids(fake)
    assert [r["id"] for r in (await get_board(client))["pending_removals"]] == [row["id"]]
    fake.error_requests.clear()
    assert (await pull(app)).status == "ok"
    assert file not in ids(fake) and (await get_board(client))["pending_removals"] == []


async def test_the_successor_of_a_deleted_selected_profile_is_never_a_utility_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """With only a utility profile left on, the selection has nowhere to go: the file stays."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    mine = row_for(await get_board(client), APP_LABEL)
    file = mine["machine"]["device_id"]
    fake.selected_profile_id = file
    for r in (await get_board(client))["rows"]:
        if not r["utility"] and r["row"]["id"] != mine["row"]["id"]:
            await client.put(f"/api/profile-board/{r['row']['id']}/on-machine", json={"on": False})
    await client.delete(f"/api/profile-board/{mine['row']['id']}")
    plan = (await get_board(client))["actions"]
    assert [(a["kind"], a["device_id"]) for a in plan if a["device_id"] == file] == [
        ("leave", file)
    ]

    run = await pull(app)

    assert file in ids(fake) and fake.selected_profile_id == file
    assert any(
        i["device_id"] == file and "no other profile" in i["detail"]
        for i in summary_of(run)["left"]
    )


# ── a machine that looks reset ──────────────────────────────────────


def reset_machine(fake: FakeDevice, name: str = "reborn") -> None:
    """What a firmware update that wiped the profile memory leaves: files under new ids."""
    fresh = copy.deepcopy(fake.profiles[0])
    fresh.update(id=name, label="Factory default")
    fake.profiles = [fresh]
    fake.favorite_profile_ids.clear()


async def test_a_machine_that_lost_every_file_pauses_until_a_person_resumes(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    rows = len((await get_board(client))["rows"])
    reset_machine(fake)
    fake.ws_requests.clear()

    run = await pull(app)

    assert "looks reset" in summary_of(run)["paused"]
    assert write_frames(fake) == [], "nothing is pushed or removed"
    board = await get_board(client)
    assert board["paused"] and board["pause_recorded"] and board["actions"] == []
    # What resuming would do is served while paused: every profile is put back, and the one file
    # the machine came back with joins the list.
    preview = board["resume_preview"]
    assert (preview["push"], preview["remove"], preview["join"]) == (rows, 0, 1)
    assert {"push", "adopt"} <= {a["kind"] for a in preview["lines"]}
    # Still paused on the next pull, even though nothing changed.
    assert summary_of(await pull(app))["paused"] and write_frames(fake) == []

    resumed = await client.post("/api/profile-board/resume")
    assert data(resumed) == {"resumed": True}
    after = await pull(app)

    assert after.status == "ok", after.error
    assert summary_of(after)["paused"] is None
    assert len(summary_of(after)["pushed"]) == preview["push"], "resuming does what was served"
    assert APP_LABEL in [p["label"] for p in fake.profiles]


async def test_an_empty_list_is_not_a_reset(writes_on: Live, fake_device: FakeDevice) -> None:
    """Nothing synced yet: the first sync adopts, it does not pause."""
    app, _ = writes_on
    run = await pull(app)
    assert summary_of(run)["paused"] is None and run.status == "ok", run.error


async def test_a_machine_with_some_of_the_apps_profiles_does_not_pause(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    await app_row(app, client, fake, provider, 7, name="Second")
    gone = next(p for p in fake.profiles if p["label"] == APP_LABEL)
    fake.profiles.remove(gone)  # one file gone, the rest of the machine as it was

    run = await pull(app)

    assert summary_of(run)["paused"] is None and run.status == "ok", run.error
    assert len(summary_of(run)["pushed"]) == 1


# ── reading the board is cheap by default ───────────────────────────


async def test_reading_the_board_does_not_touch_the_machine_unless_asked(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    _, client, fake = adopted
    fake.ws_requests.clear()

    cheap = await get_board(client, live=False)

    assert cheap["machine_source"] == "mirror" and fake.ws_requests == []
    live = await get_board(client, live=True)
    assert live["machine_source"] == "machine" and "req:profiles:load" in fake.ws_requests


async def test_a_deleted_copy_edited_between_the_read_and_the_delete_is_not_destroyed(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    file = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    await client.delete(f"/api/profile-board/{row['id']}")
    assert [a["kind"] for a in (await get_board(client))["actions"]] == ["remove"]

    async def edit_then_remove(*args: Any, **kwargs: Any) -> Any:
        # The person edits the file on the display after the plan was made.
        found = next(p for p in fake.profiles if p["id"] == file)
        found["temperature"] = float(found["temperature"]) + 2
        return await real_remove(*args, **kwargs)

    monkeypatch.setattr(board_module, "remove_profile", edit_then_remove)

    run = await pull(app)

    assert file in ids(fake), "the delete expects what the archive recorded, not what the plan read"
    assert kinds(await audit(app), "profile_delete") == []
    assert [i["detail"] for i in summary_of(run)["left"]] == ["changed on the display since"]


async def test_a_draft_already_pushed_by_another_path_is_not_recorded_on_the_set_again(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    base = row_for(await get_board(client), BASE_LABEL)
    set_id = await make_set_on(client, "Once", base["row"]["current_version_id"])
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    await put(client, draft, set_id=set_id)
    # Something else has already pushed it (a second process, an older client).
    await ProfileDraftsRepository(app.state.db).set_status(
        draft["id"], "pushed", pushed_device_profile_id="elsewhere"
    )
    before = await set_device_ids(app, set_id)

    run = await pull(app)

    assert run.status == "ok", run.error
    assert await set_device_ids(app, set_id) == before


# ── a later put replaces the file an earlier one made ───────────────


async def test_each_put_replaces_the_file_the_one_before_it_made_and_only_that_one(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    first = await app_row(app, client, fake, provider, 8)
    row_file = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    row = await put(client, await draft_from(app, client, provider, first, 7))
    await pull(app)
    second_file = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    # Two more, put before the sync reaches the machine: only the newest is pushed.
    row = await put(client, await draft_from(app, client, provider, row, 6))
    await put(client, await draft_from(app, client, provider, row, 5))

    await pull(app)

    assert row_file not in ids(fake) and second_file not in ids(fake)
    assert len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 1


# ── a file the app itself saved is the app's, wherever it was adopted ──


STAGED = "Staged copy [AI]"


async def test_a_copy_the_app_saved_before_adoption_is_continued_by_a_later_draft(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    staged = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    staged.update(id="staged1", label=STAGED)
    fake_device.profiles.append(staged)
    await DeviceWritesRepository(app.state.db).record(
        DeviceWriteWrite(
            kind="profile_save",
            host=app.state.connection.client.host,
            device_id="staged1",
            payload_hash=profile_content_hash(Profile.model_validate(staged)),
            result="ok",
        )
    )
    await pull(app)  # adoption: the app's own earlier copy is an app row
    adopted_row = row_for(await get_board(client), STAGED)
    assert adopted_row["row"]["origin"] == "draft"

    new = await put(client, await draft_of(app, client, provider, STAGED, 7))
    run = await pull(app)

    assert new["id"] == adopted_row["row"]["id"], "a new version of the same profile"
    assert run.status == "ok", run.error
    assert "staged1" not in ids(fake_device), "its own earlier copy is replaced, not duplicated"
    assert len([p for p in fake_device.profiles if p["label"] == STAGED]) == 1


async def test_a_profile_the_person_made_stays_theirs_even_when_it_ends_in_the_app_label(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    mine = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    mine.update(id="mine", label=STAGED)
    fake_device.profiles.append(mine)
    await pull(app)
    mine_row = row_for(await get_board(client), STAGED)
    assert mine_row["row"]["origin"] == "adopted"

    # A draft of it carries the same label: refused as a duplicate, plain or for a Set, so the
    # person's own profile is never continued, replaced or doubled.
    set_id = await make_set_on(client, "OnMine", mine_row["row"]["current_version_id"])
    plain = await draft_of(app, client, provider, STAGED, 7)
    await approve(app, plain)
    for_set = await draft_of(app, client, provider, STAGED, 6)
    await approve(app, for_set)
    for body in ({"draft_id": plain["id"]}, {"draft_id": for_set["id"], "set_id": set_id}):
        refused = await client.post("/api/profile-board", json=body)
        assert refused.status_code == 409, refused.text
    await pull(app)

    assert row_for(await get_board(client), STAGED)["row"]["id"] == mine_row["row"]["id"]
    assert "mine" in ids(fake_device)
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_deleted_copy_the_app_saved_before_adoption_is_removed_under_the_guards(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    staged = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    staged.update(id="staged1", label=STAGED)
    fake_device.profiles.append(staged)
    await DeviceWritesRepository(app.state.db).record(
        DeviceWriteWrite(
            kind="profile_save",
            host=app.state.connection.client.host,
            device_id="staged1",
            payload_hash=profile_content_hash(Profile.model_validate(staged)),
            result="ok",
        )
    )
    await pull(app)
    row = row_for(await get_board(client), STAGED)
    await client.delete(f"/api/profile-board/{row['row']['id']}")

    run = await pull(app)

    assert run.status == "ok", run.error
    assert "staged1" not in ids(fake_device)
    assert [i["device_id"] for i in summary_of(run)["removed"]] == ["staged1"]


async def test_a_copy_the_app_saved_and_the_person_edited_before_adoption_is_the_persons_by_origin(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    """Origin is information only: deleting it removes the file like any other profile's."""
    app, client = writes_on
    saved = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    saved.update(id="staged1", label=STAGED)
    await DeviceWritesRepository(app.state.db).record(
        DeviceWriteWrite(
            kind="profile_save",
            host=app.state.connection.client.host,
            device_id="staged1",
            payload_hash=profile_content_hash(Profile.model_validate(saved)),
            result="ok",
        )
    )
    # Edited on the display after the app saved it, before the board adopted the machine.
    edited = copy.deepcopy(saved)
    edited["temperature"] = float(edited["temperature"]) + 2
    fake_device.profiles.append(edited)
    await pull(app)
    row = row_for(await get_board(client), STAGED)
    assert row["row"]["origin"] == "adopted", "no longer what the app saved"

    await client.delete(f"/api/profile-board/{row['row']['id']}")
    run = await pull(app)

    assert "staged1" not in ids(fake_device)
    assert [i["device_id"] for i in summary_of(run)["removed"]] == ["staged1"]


# ── a fork keeps its source ─────────────────────────────────────────


async def test_a_fork_under_a_new_label_never_takes_over_its_source_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    source = row_for(await get_board(client), APP_LABEL)
    file = source["machine"]["device_id"]
    document = document_of(fake, file)
    document["label"] = "Brand new [AI]"
    draft = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": source["row"]["current_version_id"],
            "profile": document,
            "change_summary": "fork",
        },
    )
    forked = await put(client, dict(data(draft)))

    run = await pull(app)

    assert forked["id"] != source["row"]["id"], "a new profile, not a new version of the source"
    assert run.status == "ok", run.error
    assert file in ids(fake) and summary_of(run)["removed"] == []
    assert len([p for p in fake.profiles if p["label"] in (APP_LABEL, "Brand new [AI]")]) == 2


# ── the pause and what keeps it honest ──────────────────────────────


async def test_a_second_reset_after_a_resume_pauses_again(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    reset_machine(fake)
    await pull(app)  # paused
    await client.post("/api/profile-board/resume")
    # A pull that fails before the write phase does not use up the person's answer.
    fake.error_requests.add("req:profiles:list")
    assert (await pull(app)).status == "error"
    fake.error_requests.clear()
    pushed = await pull(app)
    assert summary_of(pushed)["pushed"], "the profiles are put back"

    reset_machine(fake, "reborn-again")
    fake.ws_requests.clear()
    again = await pull(app)

    assert "looks reset" in summary_of(again)["paused"]
    assert write_frames(fake) == []


async def test_one_file_deleted_on_the_display_pauses_when_it_was_the_only_one(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    only = copy.deepcopy(fake_device.profiles[0])
    fake_device.profiles = [only]
    await pull(app)  # adoption: the one profile
    # It is deleted on the display and another one is made: none of the files the last sync
    # left is there, which is indistinguishable from a reset.
    fake_device.profiles = [{**copy.deepcopy(only), "id": "another", "label": "New"}]
    fake_device.ws_requests.clear()

    run = await pull(app)

    # So a person answers: nothing is written until then.
    assert "looks reset" in summary_of(run)["paused"] and write_frames(fake_device) == []
    await client.post("/api/profile-board/resume")
    back = await pull(app)
    assert [i["reason"] for i in summary_of(back)["pushed"]] == ["missing"]


async def test_a_deleted_copy_a_set_brews_is_removed_all_the_same(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """Deleting is the person's explicit choice; a Set brewing it does not hold the file back."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    file = row["machine"]["device_id"]
    set_id = await make_set_on(client, "Brews it", row["row"]["current_version_id"])
    await client.delete(f"/api/profile-board/{row['row']['id']}")

    run = await pull(app)

    assert [i["device_id"] for i in summary_of(run)["removed"]] == [file]
    assert file not in ids(fake)
    assert (await get_board(client))["pending_removals"] == []
    assert file not in await set_device_ids(app, set_id)


async def test_versions_the_policy_refuses_do_not_use_up_the_three_failure_stop(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    for bar in (9, 10, 11):
        await put(client, await variant_draft(app, client, f"High {bar}", bar))
    await put(client, await variant_draft(app, client, "Low", 7))
    await app.state.settings_service.apply({"profilePolicyPressureMaxBar": 8.5})

    run = await pull(app)

    summary = summary_of(run)
    assert [f["reason"] for f in summary["failures"]] == ["policy"] * 3
    assert len(summary["pushed"]) == 1, "the valid one behind them still goes"


# ── reports are not writes ──────────────────────────────────────────


async def test_a_machine_in_sync_plans_no_actions_even_while_a_report_stands(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    person = row_for(await get_board(client), BASE_LABEL)
    twin = copy.deepcopy(
        next(p for p in fake.profiles if p["id"] == person["machine"]["device_id"])
    )
    twin.update(id="twin", temperature=float(twin["temperature"]) + 1)  # a second file, same name
    fake.profiles.append(twin)

    board = await get_board(client)

    assert board["actions"] == [] or {a["kind"] for a in board["actions"]} == {"adopt"}
    assert [(r["reason"], r["device_id"]) for r in board["reports"]] == [("extra_copy", "twin")]
    run = await pull(app)
    assert write_frames(fake) == [] or run.status == "ok"
    assert summary_of(run)["pushed"] == [] and summary_of(run)["removed"] == []


# ── races and small guards found late ───────────────────────────────


async def test_a_row_revived_between_the_plan_and_its_removal_keeps_its_file(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 9)  # another app row: the machine is not "reset"
    await app_row(app, client, fake, provider, 8, name="Second")
    rows = [r for r in (await get_board(client))["rows"] if r["row"]["label"] == "Second [AI]"]
    victim = rows[-1]
    file = victim["machine"]["device_id"]
    await client.delete(f"/api/profile-board/{victim['row']['id']}")
    draft = dict(
        data(
            await client.post(
                "/api/profile-drafts",
                json={
                    "base_version_id": victim["row"]["current_version_id"],
                    "profile": document_of(fake, file),
                    "change_summary": "as is",
                },
            )
        )
    )
    await approve(app, draft)
    real_apply = board_module.BoardService._apply_deleted
    revived: list[int] = []

    async def revive_then_apply(self: Any, phase: Any, deleted: Any) -> Any:
        # Put back on the board after the plan was made, before the pull reaches its removal.
        if not revived:
            back = await app.state.board.put_draft(draft["id"])
            revived.append(back.id)
        return await real_apply(self, phase, deleted)

    monkeypatch.setattr(board_module.BoardService, "_apply_deleted", revive_then_apply)

    await pull(app)

    assert revived == [victim["row"]["id"]], "the same row came back"
    assert file in ids(fake), "a live profile's file is not removed"
    assert kinds(await audit(app), "profile_delete") == []


async def test_putting_a_failed_version_back_tries_it_again(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    second = await draft_of(app, client, provider, APP_LABEL, 7)
    await put(client, second)
    fake.mutate_on_save = lambda p: {**p, "temperature": float(p.get("temperature", 90)) + 1}
    fake.error_requests.add("req:profiles:delete")
    await pull(app)  # does not verify, and the copy cannot be removed
    row = row_for(await get_board(client), APP_LABEL)["row"]
    assert row["failed_version_id"] == row["current_version_id"]
    await client.delete(f"/api/profile-board/{row['id']}")
    fake.mutate_on_save = None
    fake.error_requests.clear()

    back = await client.post("/api/profile-board", json={"draft_id": second["id"]})

    assert back.status_code == 201, back.text
    assert data(back)["id"] == row["id"] and data(back)["failed_version_id"] is None
    run = await pull(app)
    assert run.status == "ok", run.error
    assert len(summary_of(run)["pushed"]) == 1


async def test_an_ok_save_from_another_machine_does_not_make_a_file_the_apps(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    saved = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    saved.update(id="staged1", label=STAGED)
    fake_device.profiles.append(saved)
    await DeviceWritesRepository(app.state.db).record(
        DeviceWriteWrite(
            kind="profile_save",
            host="192.168.9.9",
            device_id="staged1",
            payload_hash=profile_content_hash(Profile.model_validate(saved)),
            result="ok",
        )
    )

    await pull(app)

    assert row_for(await get_board(client), STAGED)["row"]["origin"] == "adopted"


# ── a double click ──────────────────────────────────────────────────


async def test_two_puts_of_one_draft_at_once_make_one_row_and_one_copy_on_the_machine(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    await approve(app, draft)

    results = await asyncio.gather(
        app.state.board.put_draft(draft["id"]),
        app.state.board.put_draft(draft["id"]),
        return_exceptions=True,
    )
    rows = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, Conflict)]

    assert len(rows) == 1 and len(refused) == 1, results
    run = await pull(app)
    assert run.status == "ok", run.error
    assert len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 1
    board = await get_board(client)
    assert len([r for r in board["rows"] if r["row"]["label"] == APP_LABEL]) == 1


async def test_two_posts_of_one_draft_at_once_answer_one_created_and_one_conflict(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    await approve(app, draft)

    responses = await asyncio.gather(
        client.post("/api/profile-board", json={"draft_id": draft["id"]}),
        client.post("/api/profile-board", json={"draft_id": draft["id"]}),
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    await pull(app)
    assert len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 1


async def test_the_schema_refuses_two_live_rows_on_one_file_or_one_pending_draft(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    await put(client, await draft_of(app, client, provider, BASE_LABEL, 8))
    pending = await app.state.db.fetch_one(
        "SELECT current_version_id, pending_draft_id FROM profile_board "
        "WHERE pending_draft_id IS NOT NULL"
    )
    person = row_for(await get_board(client), BASE_LABEL)
    insert = (
        "INSERT INTO profile_board (label, current_version_id, device_profile_id, origin, "
        "pending_draft_id) VALUES ('Twin', ?, ?, 'adopted', ?)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        await app.state.db.execute(
            insert, (pending["current_version_id"], person["machine"]["device_id"], None)
        )
    with pytest.raises(sqlite3.IntegrityError):
        await app.state.db.execute(
            insert, (pending["current_version_id"], None, pending["pending_draft_id"])
        )
