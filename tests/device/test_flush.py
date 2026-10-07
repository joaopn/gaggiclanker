"""`POST /api/device/flush`: the machine's own flush, once, from the top bar.

It behaves as the button on the machine's web UI: one `req:flush:start`, run for
the duration set on the machine. It needs the Writes switch, is offered only in
brew mode with nothing running (the firmware itself only refuses the second),
and leaves no audit row.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app, seed_settings


@pytest.fixture
async def live(
    data_dir: Path, fake_device: FakeDevice
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    env = EnvSettings(DATA_DIR=str(data_dir), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
    await seed_settings(env, gaggimateHost=fake_device.address, gaggimateTimeoutSeconds=2)
    async with running_app(env) as (app, client):
        assert await app.state.connection.client.wait_connected(5.0)
        await settle(app.state.connection.client, lambda c: c.last_status is not None)
        yield app, client


async def settle(client: GaggimateClient, done: object) -> None:
    """Wait until a status frame the fake sent has been merged."""
    for _ in range(100):
        if done(client):  # type: ignore[operator]
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the status frame never arrived")


async def no_audit(app: FastAPI) -> bool:
    return await DeviceWritesRepository(app.state.db).list_writes() == []


async def test_with_writes_on_in_brew_mode_one_flush_is_sent_and_nothing_recorded(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = live
    await app.state.settings_service.apply({"deviceWritesEnabled": True})

    response = await client.post("/api/device/flush")

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"started": True}
    assert fake_device.flushes == 1
    assert fake_device.ws_requests.count("req:flush:start") == 1
    assert "req:flush:stop" not in fake_device.ws_requests
    assert await no_audit(app)


async def test_with_writes_off_nothing_is_sent_and_nothing_recorded(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = live

    response = await client.post("/api/device/flush")

    assert response.status_code == 403
    assert "Writes switch" in response.json()["error"]["message"]
    assert fake_device.flushes == 0
    assert await no_audit(app)


@pytest.mark.parametrize("mode", [0, 2, 3, 4])
async def test_outside_brew_mode_nothing_is_sent(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice, mode: int
) -> None:
    """The firmware would run it in any mode; the machine's web UI offers it only in brew."""
    app, client = live
    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    await fake_device.emit_status(m=mode)
    await settle(app.state.connection.client, lambda c: c.last_status.m == mode)

    response = await client.post("/api/device/flush")

    assert response.status_code == 403
    assert "brew mode" in response.json()["error"]["message"]
    assert fake_device.flushes == 0
    assert await no_audit(app)


async def test_while_a_process_runs_nothing_is_sent_and_after_it_one_is(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = live
    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    await fake_device.emit_status(process={"a": 1, "s": "brew", "l": "Infusion", "e": 0})
    await settle(
        app.state.connection.client,
        lambda c: c.last_status.process is not None and c.last_status.process.a == 1,
    )

    response = await client.post("/api/device/flush")
    assert response.status_code == 403
    assert "already running" in response.json()["error"]["message"]
    assert fake_device.flushes == 0

    # A finished shot still shown on the machine does not block it: the
    # firmware's flush clears it, as on the display.
    await fake_device.emit_status(process={"a": 0, "s": "brew", "l": "Finished", "e": 28_000})
    await settle(app.state.connection.client, lambda c: c.last_status.process.a == 0)
    response = await client.post("/api/device/flush")
    assert response.status_code == 200, response.text
    assert fake_device.flushes == 1
    assert await no_audit(app)


async def test_with_no_machine_configured_it_says_so(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/device/flush")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEVICE_UNAVAILABLE"


async def test_a_client_without_the_app_gate_cannot_flush(
    device_client: GaggimateClient, fake_device: FakeDevice
) -> None:
    """Deny-all by default holds for the flush too."""
    from gaggiclanker.device.writes import DeviceWriteRefused

    with pytest.raises(DeviceWriteRefused):
        await device_client.start_flush()
    assert fake_device.flushes == 0
