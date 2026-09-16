"""`/api/device/cleanup/*` and `/api/device/notes/*` over the real app."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.cleanup.service import cleanup_task_name
from gaggiclanker.domain.ids import pad6
from tests.cleanup.conftest import FIRST_ID, SMALL_COUNT, data, error, service
from tests.conftest import machine_tasks


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
    plan = data(await client.get("/api/device/cleanup/plan"))
    shot_ids = [item["shot_id"] for item in plan["planned"]]
    response = await client.post("/api/device/cleanup/run", json={"shot_ids": shot_ids})
    assert response.status_code == 202
    accepted = data(response)
    assert accepted["planned"] == 2

    task = machine_tasks(app).get(accepted["task"])
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
    plan = await service(app).plan()
    assert service(app).spawn(machine_tasks(app), plan) is True
    try:
        response = await client.post(
            "/api/device/cleanup/run",
            json={"shot_ids": [item.shot_id for item in plan.planned]},
        )
        assert response.status_code == 409
        assert "already running" in error(response)["message"]
    finally:
        task = machine_tasks(app).get(cleanup_task_name())
        assert task is not None
        await asyncio.shield(task)


async def test_the_run_route_refuses_a_plan_that_changed_and_queues_nothing(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = writes_on
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": SMALL_COUNT - 3}
    )
    shown = [
        item["shot_id"] for item in data(await client.get("/api/device/cleanup/plan"))["planned"]
    ]
    # A pull, a settings change: the plan grows between the preview and the confirm.
    await app.state.settings_service.apply({"deviceCleanupKeepNewest": SMALL_COUNT - 5})

    response = await client.post("/api/device/cleanup/run", json={"shot_ids": shown})

    assert response.status_code == 409
    body = error(response)
    assert "changed since it was previewed" in body["message"]
    assert body["details"] == {"field": "shot_ids", "planned": 4}
    assert machine_tasks(app).get(cleanup_task_name()) is None
    assert data(await client.get("/api/device/cleanup/runs"))["items"] == []


async def test_the_run_route_needs_the_approved_ids(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """No body is not "run whatever the policy says now"; it is a malformed request."""
    _, client = writes_on
    response = await client.post("/api/device/cleanup/run")
    assert response.status_code == 400
    assert data(await client.get("/api/device/cleanup/runs"))["items"] == []


async def test_confirming_an_empty_plan_is_a_bad_request_and_writes_no_run(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """Explicit means something to delete: an empty approval is not an `ok 0/0` run."""
    app, client = writes_on
    plan = data(await client.get("/api/device/cleanup/plan"))
    assert plan["planned"] == [], "the default policy is off, so the plan is empty"

    response = await client.post("/api/device/cleanup/run", json={"shot_ids": []})

    assert response.status_code == 400
    details = error(response)["details"]
    assert [(item["field"], item["type"]) for item in details] == [("body.shot_ids", "too_short")]
    assert machine_tasks(app).get(cleanup_task_name()) is None
    assert data(await client.get("/api/device/cleanup/runs"))["items"] == []


async def test_the_service_refuses_an_empty_approval_too(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The route's schema is not the only caller: the service holds the rule itself."""
    from gaggiclanker.infra.errors import BadRequest

    app, _ = writes_on
    with pytest.raises(BadRequest, match="at least one shot"):
        await service(app).approve([])


async def test_the_run_route_with_writes_off_is_a_403_naming_the_switch(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = live
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": 5}
    )
    shown = [
        item["shot_id"] for item in data(await client.get("/api/device/cleanup/plan"))["planned"]
    ]

    response = await client.post("/api/device/cleanup/run", json={"shot_ids": shown})

    assert response.status_code == 403
    assert "Device writes enabled" in error(response)["message"]
    assert machine_tasks(app).get(cleanup_task_name()) is None


async def test_the_read_routes_answer_with_an_empty_plan_when_there_is_no_machine(
    client: httpx.AsyncClient,
) -> None:
    """A box with no `gaggimateHost` is a supported configuration, not a broken one.

    The archive browser works with the machine unplugged, so the *reads* answer
    normally and say why they are empty. Only the run refuses, because there is
    genuinely nothing to run against.
    """
    plan = data(await client.get("/api/device/cleanup/plan"))
    assert plan["planned"] == []
    assert plan["on_device_count"] == 0

    pending = data(await client.get("/api/device/notes/pending"))
    assert pending["items"] == []
    response = await client.post("/api/device/notes/push", json={"shot_ids": [1]})
    assert response.status_code == 503

    response = await client.post("/api/device/cleanup/run", json={"shot_ids": [1]})
    assert response.status_code == 503
    assert "no machine" in error(response)["message"].lower()


async def test_pending_notes_reports_the_write_switch(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    _, client = live
    body = data(await client.get("/api/device/notes/pending"))
    assert "enabled" not in body
    assert body["writes_enabled"] is False
    assert "rating" in body["fields"]
    assert body["items"] == []
