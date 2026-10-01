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
from gaggiclanker.drafts.board_plan import PlanBuilder
from gaggiclanker.drafts.machine import place as real_place
from gaggiclanker.drafts.machine import remove_if_ours as real_remove
from gaggiclanker.infra.errors import Conflict
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.test_board import (
    adopted,
    app_row,
    approve,
    assert_every_delete_was_ours,
    draft_from,
    get_board,
    pull,
    put,
    row_for,
    summary_of,
    variant_draft,
    write_frames,
)
from tests.drafts.test_replace import (
    APP_LABEL,
    Live,
    audit,
    document_of,
    draft_of,
    kinds,
    make_set_on,
    set_device_ids,
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
    await assert_every_delete_was_ours(app)


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
    assert any("another board profile" in i["detail"] for i in summary_of(run)["left"])


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


async def test_a_profile_the_person_made_is_not_pushed_back_after_a_machine_reset(
    writes_on: Live, fake_device: FakeDevice
) -> None:
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
    assert write_frames(fake_device) == []
    assert "missing" in [i["reason"] for i in summary_of(run)["left"]]


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


async def test_an_adopted_profile_whose_copy_does_not_read_back_is_never_saved_at_all(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, _, fake = adopted
    fake.profiles = [p for p in fake.profiles if p["id"] == "9bar"]
    fake.mutate_on_save = lambda p: {**p, "temperature": float(p.get("temperature", 90)) + 1}
    fake.ws_requests.clear()

    for _ in range(3):
        await pull(app)

    assert "req:profiles:save" not in fake.ws_requests


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
    ok = await pull(app)
    assert ok.status == "ok", ok.error
    assert len(summary_of(ok)["pushed"]) >= 1


# ── a draft on the board is not pushed by hand ──────────────────────


async def test_the_staged_push_and_rollback_refuse_a_draft_that_is_on_the_board(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    vid = row_for(await get_board(client), APP_LABEL)["row"]["current_version_id"]
    set_id = await make_set_on(client, "Twice", vid)
    draft = await draft_of(app, client, provider, APP_LABEL, 7)
    await put(client, draft, set_id=set_id)

    pending = await client.post(f"/api/profile-drafts/{draft['id']}/push", json={"set_id": set_id})
    assert pending.status_code == 409 and "profile board" in error(pending)["message"]
    before = await set_device_ids(app, set_id)
    await pull(app)
    assert len(await set_device_ids(app, set_id)) == len(before) + 1

    # Pushed by the pull, it is still the board's: neither route touches it.
    again = await client.post(f"/api/profile-drafts/{draft['id']}/push", json={})
    assert again.status_code == 409
    rolled = await client.post(f"/api/profile-drafts/{draft['id']}/rollback", json={})
    assert rolled.status_code == 409 and "profile board" in error(rolled)["message"]


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


async def test_a_deleted_copy_edited_on_the_display_is_left_and_the_row_lets_go(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    file = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    edited = next(p for p in fake.profiles if p["id"] == file)
    edited["temperature"] = float(edited["temperature"]) + 2
    await client.delete(f"/api/profile-board/{row['id']}")

    run = await pull(app)

    assert file in ids(fake), "it no longer holds what the app saved"
    assert [(i["device_id"], i["detail"]) for i in summary_of(run)["left"]] == [
        (file, "changed on the display since")
    ]
    assert (await get_board(client))["pending_removals"] == []
    assert kinds(await audit(app), "profile_delete") == []


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
    await assert_every_delete_was_ours(app)


async def test_a_set_that_comes_to_brew_the_old_copy_during_the_pull_keeps_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    old = row_for(await get_board(client), APP_LABEL)
    await put(client, await draft_of(app, client, provider, APP_LABEL, 7))

    async def place_then_brew(*args: Any, **kwargs: Any) -> Any:
        placed = await real_place(*args, **kwargs)
        await make_set_on(client, "Late", old["row"]["current_version_id"])
        return placed

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(board_module, "place", place_then_brew)
    try:
        run = await pull(app)
    finally:
        monkeypatch.undo()

    assert old["machine"]["device_id"] in ids(fake)
    assert any("Late" in i["detail"] for i in summary_of(run)["left"])
    assert kinds(await audit(app), "profile_delete") == []


async def test_the_successor_of_a_deleted_selected_profile_is_never_a_utility_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    mine = row_for(await get_board(client), APP_LABEL)
    fake.selected_profile_id = mine["machine"]["device_id"]
    for r in (await get_board(client))["rows"]:
        utility = r["utility"]
        await client.put(
            f"/api/profile-board/{r['row']['id']}/home-screen",
            json={"on": utility or r["row"]["id"] == mine["row"]["id"]},
        )
    await pull(app)
    await client.delete(f"/api/profile-board/{mine['row']['id']}")

    await pull(app)

    chosen = next(p for p in fake.profiles if p["id"] == fake.selected_profile_id)
    assert not str(chosen["label"]).startswith("[Utility]"), chosen["label"]


# ── a machine that looks reset ──────────────────────────────────────


async def test_a_machine_that_lost_the_apps_profiles_pauses_until_a_person_resumes(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    keep = [p for p in fake.profiles if p["label"] != APP_LABEL]
    fake.profiles = keep
    fake.favorite_profile_ids.clear()
    fake.ws_requests.clear()

    run = await pull(app)

    assert "looks reset" in summary_of(run)["paused"]
    assert write_frames(fake) == [], "nothing is pushed or removed"
    board = await get_board(client)
    assert board["paused"] and board["pause_recorded"] and board["actions"] == []
    # Still paused on the next pull, even though nothing changed.
    assert summary_of(await pull(app))["paused"] and write_frames(fake) == []

    resumed = await client.post("/api/profile-board/resume")
    assert data(resumed) == {"resumed": True}
    after = await pull(app)

    assert after.status == "ok", after.error
    assert summary_of(after)["paused"] is None
    assert [i["reason"] for i in summary_of(after)["pushed"]] == ["missing"]
    assert APP_LABEL in [p["label"] for p in fake.profiles]
    # The person's own profiles were never pushed back by it.
    assert len(summary_of(after)["pushed"]) == 1


async def test_a_machine_with_some_of_the_apps_profiles_does_not_pause(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    await app_row(app, client, fake, provider, 7, name="Second")
    gone = next(p for p in fake.profiles if p["label"] == APP_LABEL)
    fake.profiles.remove(gone)

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

    monkeypatch.setattr(board_module, "remove_if_ours", edit_then_remove)

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


# ── the staged routes are off once the board is adopted ─────────────


async def test_once_the_board_is_adopted_the_staged_routes_refuse_every_draft(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    first = await draft_of(app, client, provider, APP_LABEL, 7)
    await put(client, first)
    await pull(app)
    row_file = row_for(await get_board(client), APP_LABEL)["machine"]["device_id"]
    second = await draft_of(app, client, provider, APP_LABEL, 6)
    await put(client, second)
    # A rollback of the earlier, superseded draft would take the board row's file.
    rolled = await client.post(f"/api/profile-drafts/{first['id']}/rollback", json={})
    assert rolled.status_code == 409 and "profile board" in error(rolled)["message"]
    # And a push of a draft made from a board file would replace that file behind the board.
    fresh = await draft_of(app, client, provider, APP_LABEL, 5)
    await approve(app, fresh)
    pushed = await client.post(f"/api/profile-drafts/{fresh['id']}/push", json={})
    assert pushed.status_code == 409 and "profile board" in error(pushed)["message"]

    await pull(app)

    assert row_file not in ids(fake), "the pull replaced it, the only way a file goes"
    assert len([p for p in fake.profiles if p["label"] == APP_LABEL]) == 1
    await assert_every_delete_was_ours(app)


# ── a file the app itself saved is the app's, wherever it was adopted ──


async def test_a_copy_the_app_saved_before_adoption_is_continued_by_a_later_draft(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    staged = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    staged.update(id="staged1", label=APP_LABEL)
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
    adopted_row = row_for(await get_board(client), APP_LABEL)
    assert adopted_row["row"]["origin"] == "draft"

    new = await put(client, await draft_of(app, client, provider, APP_LABEL, 7))
    run = await pull(app)

    assert new["id"] == adopted_row["row"]["id"], "a new version of the same profile"
    assert run.status == "ok", run.error
    assert "staged1" not in ids(fake_device), "its own earlier copy is replaced, not duplicated"
    assert len([p for p in fake_device.profiles if p["label"] == APP_LABEL]) == 1
    await assert_every_delete_was_ours(app)


async def test_a_profile_the_person_made_stays_theirs_even_when_it_ends_in_the_app_label(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    mine = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    mine.update(id="mine", label=APP_LABEL)
    fake_device.profiles.append(mine)
    await pull(app)
    mine_row = row_for(await get_board(client), APP_LABEL)
    assert mine_row["row"]["origin"] == "adopted"

    # A draft of it carries the same label: refused as a duplicate, plain or for a Set, so the
    # person's own profile is never continued, replaced or doubled.
    set_id = await make_set_on(client, "OnMine", mine_row["row"]["current_version_id"])
    plain = await draft_of(app, client, provider, APP_LABEL, 7)
    await approve(app, plain)
    for_set = await draft_of(app, client, provider, APP_LABEL, 6)
    await approve(app, for_set)
    for body in ({"draft_id": plain["id"]}, {"draft_id": for_set["id"], "set_id": set_id}):
        refused = await client.post("/api/profile-board", json=body)
        assert refused.status_code == 409, refused.text
    await pull(app)

    assert row_for(await get_board(client), APP_LABEL)["row"]["id"] == mine_row["row"]["id"]
    assert "mine" in ids(fake_device)
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_deleted_copy_the_app_saved_before_adoption_is_removed_under_the_guards(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    staged = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    staged.update(id="staged1", label=APP_LABEL)
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
    row = row_for(await get_board(client), APP_LABEL)
    await client.delete(f"/api/profile-board/{row['row']['id']}")

    run = await pull(app)

    assert run.status == "ok", run.error
    assert "staged1" not in ids(fake_device)
    assert [i["device_id"] for i in summary_of(run)["removed"]] == ["staged1"]
    await assert_every_delete_was_ours(app)


async def test_a_copy_the_app_saved_and_the_person_edited_before_adoption_stays_theirs(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    saved = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    saved.update(id="staged1", label=APP_LABEL)
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
    row = row_for(await get_board(client), APP_LABEL)
    assert row["row"]["origin"] == "adopted", "no longer what the app saved: the person's"

    await client.delete(f"/api/profile-board/{row['row']['id']}")
    run = await pull(app)

    assert "staged1" in ids(fake_device), "never removed"
    assert kinds(await audit(app), "profile_delete") == []
    assert [i["detail"] for i in summary_of(run)["left"]] == ["not created by this app"]


async def test_a_persons_row_is_not_removed_even_if_the_plan_wrongly_allows_it(
    writes_on: Live, fake_device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The execution-time check: it does not lean on what the plan predicted."""
    app, client = writes_on
    saved = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    saved.update(id="staged1", label=APP_LABEL)
    await DeviceWritesRepository(app.state.db).record(
        DeviceWriteWrite(
            kind="profile_save",
            host=app.state.connection.client.host,
            device_id="staged1",
            payload_hash=profile_content_hash(Profile.model_validate(saved)),
            result="ok",
        )
    )
    saved["temperature"] = float(saved["temperature"]) + 2
    fake_device.profiles.append(saved)
    await pull(app)
    row = row_for(await get_board(client), APP_LABEL)
    await client.delete(f"/api/profile-board/{row['row']['id']}")

    async def allow_everything(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(PlanBuilder, "refusal", allow_everything)
    await pull(app)

    assert "staged1" in ids(fake_device)
    assert kinds(await audit(app), "profile_delete") == []


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
    keep = [p for p in fake.profiles if p["label"] != APP_LABEL]
    fake.profiles = list(keep)
    await pull(app)  # paused
    await client.post("/api/profile-board/resume")
    # A pull that fails before the write phase does not use up the person's answer.
    fake.error_requests.add("req:profiles:list")
    assert (await pull(app)).status == "error"
    fake.error_requests.clear()
    pushed = await pull(app)
    assert [i["reason"] for i in summary_of(pushed)["pushed"]] == ["missing"]

    fake.profiles = [p for p in fake.profiles if p["label"] != APP_LABEL]
    fake.ws_requests.clear()
    again = await pull(app)

    assert "looks reset" in summary_of(again)["paused"]
    assert write_frames(fake) == []


async def test_one_app_file_deleted_on_the_display_pauses_when_it_was_the_only_one(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    file = await app_row(app, client, fake, provider, 8)
    fake.profiles = [p for p in fake.profiles if p["id"] != file["id"] and p["label"] != APP_LABEL]
    fake.ws_requests.clear()

    run = await pull(app)

    # Indistinguishable from a reset, so a person answers: nothing is written until then.
    assert "looks reset" in summary_of(run)["paused"] and write_frames(fake) == []
    await client.post("/api/profile-board/resume")
    back = await pull(app)
    assert [i["reason"] for i in summary_of(back)["pushed"]] == ["missing"]


# ── what keeps a row on its file, and what does not count as a device failure ──


async def test_a_deleted_copy_a_set_still_brews_stays_on_its_row_and_is_reported_each_pull(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    file = row["machine"]["device_id"]
    await make_set_on(client, "Brews it", row["row"]["current_version_id"])
    await client.delete(f"/api/profile-board/{row['row']['id']}")

    for _ in range(2):
        run = await pull(app)
        assert [i["device_id"] for i in summary_of(run)["left"]] == [file]
        assert file in ids(fake)
        assert [r["id"] for r in (await get_board(client))["pending_removals"]] == [
            row["row"]["id"]
        ]
    assert kinds(await audit(app), "profile_delete") == []


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
    fake.profiles = [p for p in fake.profiles if p["id"] != person["machine"]["device_id"]]

    board = await get_board(client)

    assert board["actions"] == []
    assert [(r["reason"], r["row_id"]) for r in board["reports"]] == [
        ("missing", person["row"]["id"])
    ]
    run = await pull(app)
    assert [i["reason"] for i in summary_of(run)["left"]] == ["missing"]


async def test_switching_writes_on_after_the_mirror_leaves_the_staged_push_working(
    writes_on: Live, provider: FakeProvider
) -> None:
    """The order the firmware simulator's staged-push tests rely on: mirror, then writes on.

    A pull with writes on adopts the board; a mirror taken before the switch does not, so the
    staged push is still there until the first such pull.
    """
    app, client = writes_on
    assert await app.state.board.board.adoption() is None

    pushed = await client.post(
        f"/api/profile-drafts/{(await _approved_draft(app, client, provider))['id']}/push",
        json={},
    )

    assert pushed.status_code == 200, pushed.text
    assert await app.state.board.board.adoption() is None


async def _approved_draft(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider
) -> dict[str, Any]:
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    await approve(app, draft)
    return draft


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


async def test_the_preview_never_offers_to_remove_a_persons_profile(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    saved = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    saved.update(id="staged1", label=APP_LABEL)
    await DeviceWritesRepository(app.state.db).record(
        DeviceWriteWrite(
            kind="profile_save",
            host=app.state.connection.client.host,
            device_id="staged1",
            payload_hash=profile_content_hash(Profile.model_validate(saved)),
            result="ok",
        )
    )
    saved["temperature"] = float(saved["temperature"]) + 2
    fake_device.profiles.append(saved)
    await pull(app)
    row = row_for(await get_board(client), APP_LABEL)
    await client.delete(f"/api/profile-board/{row['row']['id']}")

    board = await get_board(client)

    assert [(a["kind"], a["device_id"]) for a in board["actions"]] == [("leave", "staged1")]


async def test_an_ok_save_from_another_machine_does_not_make_a_file_the_apps(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    saved = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    saved.update(id="staged1", label=APP_LABEL)
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

    assert row_for(await get_board(client), APP_LABEL)["row"]["origin"] == "adopted"


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
