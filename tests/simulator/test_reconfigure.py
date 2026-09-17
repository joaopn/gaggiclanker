"""Pointing a running app at the real firmware, from Settings, with no restart.

Opt-in, like the rest of this directory: ``scripts/sim.sh test``, or
``uv run pytest -m simulator`` against a simulator already on :8080.

The app boots with no machine at all — the state a fresh `docker compose up`
leaves it in — and the host is then set through `PATCH /api/settings`, the route
the Settings page uses. What the fake device cannot prove is that the rebuilt
client holds a socket the real firmware accepts and answers an identity read
on; that is the subject. Nothing is written to the machine.
"""

from __future__ import annotations

import pytest

from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app
from tests.simulator.test_e2e import SIM_HOST, _until, data, require_simulator

pytestmark = pytest.mark.simulator


async def test_setting_the_host_at_runtime_connects_and_reads_identity(env: EnvSettings) -> None:
    await require_simulator()

    async with running_app(env) as (app, client):
        assert data(await client.get("/api/device/status"))["configured"] is False

        response = await client.patch(
            "/api/settings", json={"gaggimateHost": SIM_HOST, "gaggimateTimeoutSeconds": 15}
        )
        data(response)

        machine = app.state.connection.client
        assert machine is not None
        assert machine.host == SIM_HOST
        assert await machine.wait_connected(20.0), "the simulator did not accept a connection"

        run = await app.state.connection.engine.sync_identity(trigger="test")
        assert run is not None
        assert run.status == "ok", run.error

        async def identified() -> dict[str, object] | None:
            status = data(await client.get("/api/device/status"))
            return status if status["connected"] and status["identity"] else None

        status = await _until(identified, 30.0, "the device status to report the simulator")
        assert status["host"] == SIM_HOST
        row = await app.state.db.fetch_one(
            "SELECT host, display_version FROM machines WHERE id = 1"
        )
        assert row["host"] == SIM_HOST
        assert row["display_version"]

    # The connection closed with the app: the simulator keeps its WebSocket slots.
    assert app.state.connection.client is None
