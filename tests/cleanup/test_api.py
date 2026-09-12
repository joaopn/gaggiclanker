"""`/api/device/cleanup/*` and `/api/device/notes/*` over the real app."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI

from gaggiclanker.cleanup.service import cleanup_task_name
from gaggiclanker.domain.ids import pad6
from tests.cleanup.conftest import FIRST_ID, SMALL_COUNT, data, error, machine_id, service


async def test_the_plan_route_is_a_dry_run(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = live
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": SMALL_COUNT - 4}
    )
    body = data(await client.get("/api/device/cleanup/plan"))
    assert body["policy"]["mode"] == "keep_newest"
    assert len(body["planned"]) == 3
    assert body["planned"][0]["device_id"] == pad6(FIRST_ID)
    assert body["skipped"]
    # Nothing has been deleted: the same plan comes back a second time.
    again = data(await client.get("/api/device/cleanup/plan"))
    assert len(again["planned"]) == 3


async def test_the_run_route_answers_202_and_deletes_in_the_background(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = writes_on
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": SMALL_COUNT - 3}
    )
    response = await client.post("/api/device/cleanup/run")
    assert response.status_code == 202
    accepted = data(response)
    assert accepted["planned"] == 2

    task = app.state.tasks.get(accepted["task"])
    assert task is not None
    await asyncio.shield(task)

    runs = data(await client.get("/api/device/cleanup/runs"))["items"]
    assert len(runs) == 1
    assert runs[0]["deleted"] == 2
    assert runs[0]["status"] == "ok"


async def test_a_second_run_while_one_is_going_is_a_conflict(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = writes_on
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": 5}
    )
    identifier = await machine_id(app)
    assert service(app).spawn(app.state.tasks, identifier) is True
    try:
        response = await client.post("/api/device/cleanup/run")
        assert response.status_code == 409
        assert "already running" in error(response)["message"]
    finally:
        task = app.state.tasks.get(cleanup_task_name(identifier))
        assert task is not None
        await asyncio.shield(task)


async def test_the_read_routes_answer_with_an_empty_plan_when_there_is_no_machine(
    client: httpx.AsyncClient,
) -> None:
    """A box with no `gaggimateHost` is a supported configuration, not a broken one.

    The archive browser works with the machine unplugged, so the *reads* answer
    normally and say why they are empty. Only the run refuses, because there is
    genuinely nothing to run against.
    """
    plan = data(await client.get("/api/device/cleanup/plan"))
    assert plan["machine_id"] is None
    assert plan["planned"] == []
    assert plan["blocked"] is not None

    pending = data(await client.get("/api/device/notes/pending"))
    assert pending["shot_ids"] == []

    response = await client.post("/api/device/cleanup/run")
    assert response.status_code == 503
    assert "no machine" in error(response)["message"].lower()


async def test_pending_notes_reports_both_switches(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    _, client = live
    body = data(await client.get("/api/device/notes/pending"))
    assert body["enabled"] is False
    assert body["writes_enabled"] is False
    assert "rating" in body["fields"]
    assert body["shot_ids"] == []
