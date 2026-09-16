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
from tests.conftest import running_app, seed_settings

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


async def test_the_live_stream_is_gone(env: EnvSettings) -> None:
    """The 2 Hz telemetry stream was removed, and its absence is the envelope's.

    A route that vanished has to answer like every other unknown path — the
    error envelope, not a bare Starlette 404 — because the front end's fetch
    wrapper reads `error.code` before it reads the status.
    """
    async with running_app(env) as (_app, client):
        response = await client.get("/api/device/live")
    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"


# ── with the fake machine ────────────────────────────────────────────


@pytest.fixture
async def device_env(data_dir: Path, fake_device: FakeDevice) -> EnvSettings:
    """An archive that already holds the machine's address, as a restarted box does."""
    env = EnvSettings(
        DATA_DIR=str(data_dir),
        LOG_LEVEL="warning",
        LOG_JSON=True,
    )  # type: ignore[call-arg]
    await seed_settings(env, gaggimateHost=fake_device.address, gaggimateTimeoutSeconds=2)
    return env


async def test_status_reports_the_machine_it_connected_to(
    device_env: EnvSettings, fake_device: FakeDevice
) -> None:
    async with running_app(device_env) as (app, client):
        assert app.state.connection.client is not None
        assert await app.state.connection.client.wait_connected(5.0)
        # The identity frame is answered asynchronously; wait for it rather
        # than racing the connect.
        for _ in range(100):
            if app.state.connection.client.identity is not None:
                break
            await asyncio.sleep(0.02)

        body = (await client.get("/api/device/status")).json()["data"]

    assert body["configured"] is True
    assert body["connected"] is True
    assert body["host"] == fake_device.address
    assert body["identity"]["hardware"] == "GaggiMate Pro Rev 1.1"


async def test_sync_disabled_leaves_the_client_unstarted(
    data_dir: Path, fake_device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody working on the archive should not take one of three WS slots."""
    env = EnvSettings(
        DATA_DIR=str(data_dir),
        LOG_LEVEL="warning",
    )  # type: ignore[call-arg]
    await seed_settings(env, gaggimateHost=fake_device.address, deviceSyncEnabled=False)
    async with running_app(env) as (app, client):
        assert app.state.connection.client is None
        assert (await client.get("/api/device/status")).json()["data"]["configured"] is False
    assert fake_device.client_count == 0
