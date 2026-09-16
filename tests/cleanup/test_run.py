"""Running a cleanup: what leaves the machine, in what order, and what stops it."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.cleanup.service import (
    MIN_DELETE_INTERVAL_S,
    CleanupService,
    cleanup_task_name,
)
from gaggiclanker.db.repos.cleanup import CleanupRepository
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.device.writes import DeviceWriteRefused
from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.models import SHOT_FLAG_DELETED
from gaggiclanker.infra.errors import Conflict
from tests.cleanup.conftest import (
    FIRST_ID,
    NOTES_ID,
    SMALL_COUNT,
    service,
)
from tests.conftest import machine_tasks

#: The wording somebody types on the touchscreen after the last notes pull.
EDITED = "reworded on the machine"


async def _shot_id(app: FastAPI, device_id: int) -> int:
    row = await ShotsRepository(app.state.db).get_by_device_id(pad6(device_id))
    assert row is not None
    return row.id


async def _keep(app: FastAPI, count: int) -> None:
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": count}
    )


async def test_a_run_deletes_the_oldest_shots_and_leaves_the_index_row_flagged(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The firmware never removes an index row; it flags it and deletes the files."""
    app, _ = writes_on
    await _keep(app, SMALL_COUNT - 4)
    plan = await service(app).plan()
    wanted = [item.device_id for item in plan.planned]
    assert len(wanted) == 3
    assert wanted[0] == pad6(FIRST_ID)

    run = await service(app).run(await service(app).plan())
    assert run.status == "ok"
    assert run.planned == 3
    assert run.deleted == 3
    assert run.mode == "keep_newest"
    assert run.target == SMALL_COUNT - 4

    for device_id in wanted:
        entry = next(
            shot.entry for shot in fake_device.shots.values() if pad6(shot.entry.id) == device_id
        )
        assert entry.flags & SHOT_FLAG_DELETED
        assert entry.id in fake_device.missing_files


async def test_a_run_marks_the_archive_locally_rather_than_waiting_for_the_next_diff(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = writes_on
    await _keep(app, SMALL_COUNT - 3)
    plan = await service(app).plan()
    await service(app).run(await service(app).plan())

    states = await ShotsRepository(app.state.db).known_states()
    for item in plan.planned:
        assert states[item.device_id].deleted_on_device is True


async def test_every_delete_leaves_an_audit_row(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = writes_on
    await _keep(app, SMALL_COUNT - 3)
    await service(app).run(await service(app).plan())

    rows = await DeviceWritesRepository(app.state.db).list_writes()
    deletes = [row for row in rows if row.kind == "shot_delete"]
    assert len(deletes) == 2
    assert all(row.result == "ok" for row in deletes)


async def test_a_run_stops_at_the_first_thing_the_machine_refuses(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
    fake_device: FakeDevice,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device error means the machine is unhappy now; forty more frames will not help."""
    app, _ = writes_on
    await _keep(app, 5)
    plan = await service(app).plan()
    assert len(plan.planned) > 3

    original = app.state.connection.client.delete_shot
    calls = 0

    async def delete(shot_id: Any) -> None:
        nonlocal calls
        calls += 1
        # The firmware answers every `req:history*` with "Update in progress"
        # while an OTA runs. Switched on just before the third delete.
        if calls == 3:
            fake_device.ota_in_progress = True
        await original(shot_id)

    monkeypatch.setattr(app.state.connection.client, "delete_shot", delete)
    run = await service(app).run(await service(app).plan())

    assert run.status == "error"
    assert run.deleted == 2
    assert run.planned == len(plan.planned)
    assert run.error
    assert calls == 3


async def test_a_run_with_writes_off_deletes_nothing_and_says_why(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The plan lists shots happily; the gate is what refuses, and it is audited."""
    app, _ = live
    await _keep(app, 5)
    run = await service(app).run(await service(app).plan())
    assert run.deleted == 0
    assert run.status == "error"
    assert run.error is not None
    assert "switched off" in run.error

    rows = await DeviceWritesRepository(app.state.db).list_writes()
    assert any(row.kind == "shot_delete" and row.result == "refused" for row in rows)


async def test_deletes_are_paced_to_two_a_second(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The display's web server is pumped from its main loop; a tight loop stalls it.

    The one test that runs at the shipped pace, because the constant is the
    subject. Two deletes, so it costs one interval rather than forty.
    """
    app, _ = writes_on
    await _keep(app, SMALL_COUNT - 3)
    paced = CleanupService(
        app.state.db,
        app.state.settings_service,
        connection=app.state.connection,
        bus=app.state.events,
    )
    assert paced.pace_seconds == MIN_DELETE_INTERVAL_S

    started = time.monotonic()
    run = await paced.run(await paced.plan())
    assert run.deleted == 2
    assert time.monotonic() - started >= MIN_DELETE_INTERVAL_S


async def test_only_one_run_at_a_time(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The registry name is claimed synchronously, so two tabs get one run."""
    app, _ = writes_on
    await _keep(app, 5)
    tasks = machine_tasks(app)

    assert service(app).spawn(tasks, await service(app).plan()) is True
    assert service(app).spawn(tasks, await service(app).plan()) is False

    await _await_task(app, cleanup_task_name())
    runs = await CleanupRepository(app.state.db).list_runs()
    assert len(runs) == 1


async def test_the_run_appears_in_the_ledger_with_both_figures(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = writes_on
    await _keep(app, SMALL_COUNT - 2)
    await service(app).run(await service(app).plan(), trigger="test")

    runs = await CleanupRepository(app.state.db).list_runs()
    assert len(runs) == 1
    assert runs[0].trigger == "test"
    assert runs[0].planned == 1
    assert runs[0].deleted == 1
    assert runs[0].finished_at is not None


async def test_a_run_cut_off_by_a_restart_is_closed_at_the_next_boot(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """A `running` row nothing closes would show a deletion in progress for ever."""
    app, _ = writes_on
    repo = CleanupRepository(app.state.db)
    run_id = await repo.start_run(
        mode="keep_newest",
        target=5,
        trigger="test",
        planned=3,
        free_before=None,
    )
    assert await repo.reconcile_running() == 1
    row = await repo.get_run(run_id)
    assert row is not None
    assert row.status == "error"
    assert row.error == "interrupted by a restart"


# ── nothing starts a cleanup but a person ────────────────────────────


async def test_a_clean_index_sync_never_starts_a_cleanup(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """Even with writes on and a policy that wants shots gone, a pull only reads.

    The engine used to poke an automatic cleanup after every clean index diff.
    There is no such hook now; this pins it from the outside — no task, no run
    row, no delete in the audit, and every planned shot still on the machine.
    """
    app, _ = writes_on
    await _keep(app, SMALL_COUNT - 3)
    wanted = [item.device_id for item in (await service(app).plan()).planned]
    assert wanted, "the policy must want something deleted for this to mean anything"

    run = await app.state.connection.engine.sync_shots(trigger="test")
    assert run.status == "ok"
    await asyncio.sleep(0)

    assert not [name for name in machine_tasks(app).names if name.startswith("cleanup")]
    assert await CleanupRepository(app.state.db).list_runs() == []
    rows = await DeviceWritesRepository(app.state.db).list_writes()
    assert not [row for row in rows if row.kind == "shot_delete"]
    assert [item.device_id for item in (await service(app).plan()).planned] == wanted


@pytest.fixture
def retired_switches_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retired automatic-write switches, set the way an old compose file sets them."""
    monkeypatch.setenv("GAGGICLANKER_DEVICE_CLEANUP_AUTO", "true")
    monkeypatch.setenv("GAGGICLANKER_NOTES_WRITEBACK_ENABLED", "true")
    monkeypatch.setenv("GAGGICLANKER_MCP_DEVICE_WRITES", "true")


async def test_retired_switches_in_the_environment_and_the_database_resurrect_nothing(
    retired_switches_in_env: None,
    writes_on: tuple[FastAPI, httpx.AsyncClient],
    fake_device: FakeDevice,
) -> None:
    """An upgraded install with every old switch still on writes nothing by itself.

    The variables are set before boot and the rows are written afterwards, as a
    backup restored by hand would leave them. A clean pull and a judgement save
    — the two moments the old switches acted on — must send no frame at all.
    """
    app, client = writes_on
    for key in ("deviceCleanupAuto", "notesWritebackEnabled", "mcpDeviceWrites"):
        await app.state.db.execute("INSERT INTO settings (key, value) VALUES (?, 'true')", (key,))
    await _keep(app, SMALL_COUNT - 3)
    assert (await service(app).plan()).planned
    fake_device.ws_requests.clear()

    assert (await app.state.connection.engine.sync_shots(trigger="test")).status == "ok"
    shot_id = await _shot_id(app, FIRST_ID)
    response = await client.put(f"/api/shots/{shot_id}/judgement", json={"rating": 5})
    assert response.status_code == 200
    await asyncio.sleep(0.05)

    assert "req:history:delete" not in fake_device.ws_requests
    assert "req:history:notes:save" not in fake_device.ws_requests
    assert not [
        name for name in machine_tasks(app).names if name.startswith(("cleanup", "notes-writeback"))
    ]
    assert await DeviceWritesRepository(app.state.db).list_writes() == []
    assert await CleanupRepository(app.state.db).list_runs() == []


async def test_approval_returns_the_plan_that_was_shown(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = writes_on
    await _keep(app, SMALL_COUNT - 3)
    shown = await service(app).plan()

    approved = await service(app).approve([item.shot_id for item in reversed(shown.planned)])

    assert [item.shot_id for item in approved.planned] == [item.shot_id for item in shown.planned]


async def test_approval_is_refused_when_the_plan_changed_since_the_preview(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """What runs is what was approved: a policy moved, so nothing is queued."""
    app, _ = writes_on
    await _keep(app, SMALL_COUNT - 3)
    shown = [item.shot_id for item in (await service(app).plan()).planned]
    await _keep(app, SMALL_COUNT - 5)

    with pytest.raises(Conflict, match="changed since it was previewed"):
        await service(app).approve(shown)
    # A subset of the current plan is not the current plan either.
    current = [item.shot_id for item in (await service(app).plan()).planned]
    with pytest.raises(Conflict):
        await service(app).approve(current[:-1])
    assert await CleanupRepository(app.state.db).list_runs() == []


async def test_approval_with_writes_off_is_refused_and_audited(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = live
    await _keep(app, 5)
    shown = [item.shot_id for item in (await service(app).plan()).planned]

    with pytest.raises(DeviceWriteRefused, match="switched off"):
        await service(app).approve(shown)

    rows = await DeviceWritesRepository(app.state.db).list_writes()
    assert [(row.kind, row.result, row.device_id) for row in rows] == [
        ("shot_delete", "refused", None)
    ]
    assert await CleanupRepository(app.state.db).list_runs() == []


async def test_a_run_deletes_only_the_approved_plan_even_if_the_policy_moves(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The run takes the approved plan rather than re-planning when its task starts."""
    app, _ = writes_on
    await _keep(app, SMALL_COUNT - 3)
    approved = await service(app).approve(
        [item.shot_id for item in (await service(app).plan()).planned]
    )
    await _keep(app, 5)

    run = await service(app).run(approved)

    assert run.planned == len(approved.planned) == 2
    assert run.deleted == 2
    rows = await DeviceWritesRepository(app.state.db).list_writes()
    assert sorted(row.device_id or "" for row in rows if row.kind == "shot_delete") == sorted(
        item.device_id for item in approved.planned
    )


async def _await_task(app: FastAPI, name: str) -> None:
    task = machine_tasks(app).get(name)
    assert task is not None, f"no background task named {name!r}"
    await asyncio.shield(task)


# ── the notes card goes with the shot, so it is pulled first ─────────


async def test_a_note_edited_since_the_last_pull_is_mirrored_before_the_delete(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The gap this closes: notes are re-pulled only when the *index* moves.

    `_sync_notes` compares the index entry's rating and volume with what it held
    at the last pull, because those are the only two fields the firmware writes
    back into the index when a card is saved. Reword the note on the touchscreen
    and the index does not move at all — so the mirror keeps the old text, and a
    cleanup would delete `/h/<id>.json` with the only copy of the new one.
    """
    app, _ = writes_on
    shot_id = await _shot_id(app, NOTES_ID)
    before = await NotesRepository(app.state.db).get(shot_id)
    assert before is not None, "the fixture's sync pass should have mirrored this card"
    assert before.notes != EDITED

    # Typed on the machine: the text changes, the index entry does not.
    shot = fake_device.shots[NOTES_ID]
    entry = shot.entry
    rating_before, volume_before = entry.rating, entry.volume_g
    assert shot.notes is not None
    shot.notes = {**shot.notes, "notes": EDITED}
    assert (entry.rating, entry.volume_g) == (rating_before, volume_before)

    # A pass that would *not* re-pull it: proof the index says nothing changed.
    await app.state.connection.engine.sync_shots(trigger="test")
    unchanged = await NotesRepository(app.state.db).get(shot_id)
    assert unchanged is not None and unchanged.notes != EDITED

    await _keep(app, SMALL_COUNT - 5)
    plan = await service(app).plan()
    assert pad6(NOTES_ID) in {item.device_id for item in plan.planned}

    run = await service(app).run(await service(app).plan())
    assert run.status == "ok"

    saved = await NotesRepository(app.state.db).get(shot_id)
    assert saved is not None
    assert saved.notes == EDITED, "the card was deleted without being read first"
    states = await ShotsRepository(app.state.db).known_states()
    assert states[pad6(NOTES_ID)].deleted_on_device is True


async def test_a_shot_whose_notes_cannot_be_read_is_skipped_rather_than_deleted(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """A skip, with a reason and an audit row — not a stop, and not a delete.

    The machine is answering; one file is not. The other shots in the plan are
    unaffected, and the one that could not be read keeps its card.
    """
    app, _ = writes_on
    # The firmware serves its own SPA for a path it cannot satisfy, which the
    # client refuses as a protocol error rather than reading as a notes file.
    fake_device.html_paths.add(f"/api/history/{pad6(NOTES_ID)}.json")

    await _keep(app, SMALL_COUNT - 5)
    plan = await service(app).plan()
    planned = [item.device_id for item in plan.planned]
    assert pad6(NOTES_ID) in planned

    run = await service(app).run(await service(app).plan())
    assert run.deleted == len(planned) - 1
    assert run.errors == 1
    assert run.error is not None and "notes card could not be read" in run.error

    states = await ShotsRepository(app.state.db).known_states()
    assert states[pad6(NOTES_ID)].deleted_on_device is False
    assert fake_device.shots[NOTES_ID].notes is not None

    rows = await DeviceWritesRepository(app.state.db).list_writes()
    refusal = next(
        row for row in rows if row.kind == "shot_delete" and row.device_id == pad6(NOTES_ID)
    )
    assert refusal.result == "refused"
    assert "notes card could not be read" in refusal.error


async def test_a_shot_the_index_says_has_no_notes_costs_no_extra_request(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The flag is what makes this affordable: two HTTP slots, hundreds of shots."""
    app, _ = writes_on
    await _keep(app, SMALL_COUNT - 3)
    plan = await service(app).plan()
    assert pad6(NOTES_ID) not in {item.device_id for item in plan.planned}

    fake_device.requests.clear()
    await service(app).run(await service(app).plan())

    assert not [path for path in fake_device.requests if path.endswith(".json")]
