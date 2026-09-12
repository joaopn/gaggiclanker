"""`/api/device/*` and the lifespan wiring behind it.

The app is built the way every other test builds it — real SQLite file, real
migrations, ASGI in-process — with the device host pointed at the fake machine.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app
from tests.device.conftest import read_events, serving

# ── with no machine configured ───────────────────────────────────────


async def test_the_app_boots_with_no_host_configured(client: httpx.AsyncClient) -> None:
    """An archive with no machine attached is a supported configuration."""
    response = await client.get("/api/device/status")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"] == {
        "configured": False,
        "connected": False,
        "host": None,
        "identity": None,
        "last_status": None,
    }


async def test_the_live_stream_says_not_configured_rather_than_404(env: EnvSettings) -> None:
    """A 404 would put the browser's SSE helper into a reconnect loop."""
    async with serving(env) as (_app, base_url):
        async with httpx.AsyncClient(base_url=base_url) as http:
            async with http.stream("GET", "/api/device/live") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                events = await read_events(response, 1)
    assert events[0]["event"] == "device.connection"
    assert events[0]["data"] == {"connected": False, "configured": False}


# ── with the fake machine ────────────────────────────────────────────


@pytest.fixture
def device_env(
    data_dir: Path, fake_device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> EnvSettings:
    monkeypatch.setenv("GAGGIMATE_HOST", fake_device.address)
    monkeypatch.setenv("GAGGIMATE_TIMEOUT_S", "2")
    return EnvSettings(
        DATA_DIR=str(data_dir),
        LOG_LEVEL="warning",
        LOG_JSON=True,
        _env_file=None,  # type: ignore[call-arg]
    )


async def test_status_reports_the_machine_it_connected_to(
    device_env: EnvSettings, fake_device: FakeDevice
) -> None:
    async with running_app(device_env) as (app, client):
        assert app.state.device is not None
        assert await app.state.device.wait_connected(5.0)
        # The identity frame is answered asynchronously; wait for it rather
        # than racing the connect.
        for _ in range(100):
            if app.state.device.identity is not None:
                break
            await asyncio.sleep(0.02)

        body = (await client.get("/api/device/status")).json()["data"]

    assert body["configured"] is True
    assert body["connected"] is True
    assert body["host"] == fake_device.address
    assert body["identity"]["hardware"] == "GaggiMate Pro Rev 1.1"


async def test_the_live_stream_carries_the_merged_status(
    device_env: EnvSettings, fake_device: FakeDevice
) -> None:
    """One event per merged frame, so a tab that joins mid-shot is correct."""
    async with serving(device_env) as (app, base_url):
        assert await app.state.device.wait_connected(5.0)
        async with httpx.AsyncClient(base_url=base_url) as http:
            async with http.stream("GET", "/api/device/live") as response:
                pump = asyncio.create_task(_pump(fake_device))
                try:
                    events = await read_events(response, 3)
                finally:
                    pump.cancel()

    live = [e for e in events if e["event"] == "device.live"]
    assert live, events
    # The state frame the fake sends on connect is merged with the telemetry
    # we pumped, so a live event carries both halves.
    assert any(e["data"].get("ct") for e in live)


async def test_a_disconnect_is_announced_on_the_stream(
    device_env: EnvSettings, fake_device: FakeDevice
) -> None:
    """The pill has to go grey without polling for it."""
    async with serving(device_env) as (app, base_url):
        assert await app.state.device.wait_connected(5.0)
        async with httpx.AsyncClient(base_url=base_url) as http:
            async with http.stream("GET", "/api/device/live") as response:
                dropper = asyncio.create_task(_drop_soon(fake_device))
                try:
                    events = await read_events(response, 4, timeout=8.0)
                finally:
                    dropper.cancel()

    assert any(
        e["event"] == "device.connection" and e["data"]["connected"] is False for e in events
    ), events


async def test_sync_disabled_leaves_the_client_unstarted(
    data_dir: Path, fake_device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody working on the archive should not take one of three WS slots."""
    monkeypatch.setenv("GAGGIMATE_HOST", fake_device.address)
    monkeypatch.setenv("GAGGICLANKER_DEVICE_SYNC_ENABLED", "false")
    env = EnvSettings(
        DATA_DIR=str(data_dir),
        LOG_LEVEL="warning",
        _env_file=None,  # type: ignore[call-arg]
    )
    async with running_app(env) as (app, client):
        assert app.state.device is None
        assert (await client.get("/api/device/status")).json()["data"]["configured"] is False
    assert fake_device.client_count == 0


async def _pump(device: FakeDevice) -> None:
    for i in range(200):
        await device.emit_status(ct=90.0 + (i % 5), tt=93.0, pr=8.0)
        await asyncio.sleep(0.02)


async def _drop_soon(device: FakeDevice) -> None:
    await asyncio.sleep(0.2)
    await device.drop_connections()
