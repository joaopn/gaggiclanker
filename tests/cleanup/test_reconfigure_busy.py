"""A machine-settings change never cuts a write — or a pull — in half.

Each test starts the real operation through its real route, holds it on the
machine side, and then tries to move the connection. The change is a 409 naming
what is running, and nothing is stored; a change to any other setting goes
through. Then the operation is let go and finishes normally.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.cleanup.service import cleanup_task_name
from gaggiclanker.notes.writeback import writeback_task_name
from tests.cleanup.conftest import FIRST_ID, SMALL_COUNT, data, drain_tasks, error
from tests.cleanup.test_notes_writeback import _judge, _shot_id

ELSEWHERE = "127.0.0.1:9"


async def wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def assert_change_refused(app: FastAPI, client: httpx.AsyncClient, running: str) -> None:
    """The host change is a 409 naming ``running``; the setting and the client are untouched."""
    before_client = app.state.connection.client
    before = (await client.get("/api/settings")).json()["data"]["gaggimateHost"]

    response = await client.patch(
        "/api/settings", json={"gaggimateHost": ELSEWHERE, "modelDefault": "not-stored-either"}
    )

    assert response.status_code == 409, response.text
    body = error(response)
    assert running in body["message"]
    assert body["details"] == {"running": running}
    after = (await client.get("/api/settings")).json()["data"]
    assert after["gaggimateHost"] == before
    assert after["modelDefault"]["source"] == "default"
    assert app.state.connection.client is before_client

    # Only a change that would move the connection is refused.
    other = await client.patch("/api/settings", json={"modelDefault": "sonnet"})
    assert other.status_code == 200, other.text


async def test_a_cleanup_run_holds_off_a_connection_change(
    writes_on: tuple[FastAPI, httpx.AsyncClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = writes_on
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": SMALL_COUNT - 3}
    )
    machine = app.state.connection.client
    original = machine.delete_shot
    release = asyncio.Event()

    async def held(shot_id: Any) -> None:
        await release.wait()
        await original(shot_id)

    monkeypatch.setattr(machine, "delete_shot", held)
    plan = data(await client.get("/api/device/cleanup/plan"))
    response = await client.post(
        "/api/device/cleanup/run",
        json={"shot_ids": [item["shot_id"] for item in plan["planned"]]},
    )
    assert response.status_code == 202
    assert app.state.tasks.get(cleanup_task_name()) is not None

    try:
        await assert_change_refused(app, client, "a cleanup run")
    finally:
        release.set()
        await drain_tasks(app, cleanup_task_name())

    runs = data(await client.get("/api/device/cleanup/runs"))["items"]
    assert runs[0]["status"] == "ok"
    assert runs[0]["deleted"] == 2
    # Finished, so the change goes through now.
    assert (await client.patch("/api/settings", json={"gaggimateHost": ELSEWHERE})).status_code == (
        200
    )


async def test_a_notes_send_holds_off_a_connection_change(
    writes_on: tuple[FastAPI, httpx.AsyncClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = writes_on
    shot = await _shot_id(app, FIRST_ID)
    await _judge(app, shot)
    machine = app.state.connection.client
    original = machine.save_shot_notes
    release = asyncio.Event()

    async def held(shot_id: Any, notes: Any) -> Any:
        await release.wait()
        return await original(shot_id, notes)

    monkeypatch.setattr(machine, "save_shot_notes", held)
    response = await client.post("/api/device/notes/push", json={"shot_ids": [shot]})
    assert response.status_code == 202
    assert app.state.tasks.get(writeback_task_name()) is not None

    try:
        await assert_change_refused(app, client, "a notes send")
    finally:
        release.set()
        await drain_tasks(app, writeback_task_name())

    assert data(await client.get("/api/device/notes/pending"))["items"] == []


async def test_a_pull_holds_off_a_connection_change(
    live: tuple[FastAPI, httpx.AsyncClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = live
    engine = app.state.connection.engine
    machine = app.state.connection.client
    original = machine.fetch_index
    release = asyncio.Event()
    reached = asyncio.Event()

    async def held() -> Any:
        reached.set()
        await release.wait()
        return await original()

    monkeypatch.setattr(machine, "fetch_index", held)
    assert (await client.post("/api/sync/run", json={"kind": "shots"})).status_code == 202
    # Asked for and not yet picked up is already a pull somebody is waiting for.
    assert engine.busy() == "a pull"
    await asyncio.wait_for(reached.wait(), 5.0)

    try:
        await assert_change_refused(app, client, "a pull")
    finally:
        release.set()
        await wait_for(lambda: engine.busy() is None)

    last = (await client.get("/api/sync/status")).json()["data"]["last_runs"]
    assert last["backfill"]["status"] == "ok"


async def test_a_host_change_with_an_invalid_value_is_a_400_even_while_busy(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """Validation first: the person gets told what is wrong with the value, not to wait."""
    app, client = writes_on
    async with app.state.connection.operation("a profile push"):
        response = await client.patch(
            "/api/settings", json={"gaggimateHost": ELSEWHERE, "gaggimateTimeoutSeconds": "soon"}
        )
    assert response.status_code == 400
