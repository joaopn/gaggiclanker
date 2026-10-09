"""`POST /api/device/mode`: brew or standby, from the top bar.

It behaves as the mode buttons on the machine's web UI: one `req:change-mode`,
which the firmware never answers, confirmed by the state frame it publishes when
the mode changes. It needs the Writes switch, is refused while a shot or a flush
runs (the firmware's handler would stop it) and while the machine is not ready
(the firmware would ignore it), and leaves no audit row.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app, seed_settings
from tests.device.test_flush import no_audit, settle


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


async def writes_on(app: FastAPI) -> None:
    await app.state.settings_service.apply({"deviceWritesEnabled": True})


async def test_from_brew_one_frame_puts_it_in_standby_and_nothing_is_recorded(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = live
    await writes_on(app)

    response = await client.post("/api/device/mode", json={"mode": "standby"})

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"mode": "standby"}
    assert fake_device.mode_changes == [0]
    assert fake_device.ws_requests.count("req:change-mode") == 1
    # Answered only once the machine's own state frame said so.
    assert app.state.connection.client.last_status.m == 0
    assert await no_audit(app)


@pytest.mark.parametrize("mode", [0, 2, 3, 4])
async def test_from_any_other_mode_it_goes_to_brew(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice, mode: int
) -> None:
    app, client = live
    await writes_on(app)
    fake_device.mode = mode
    await fake_device.emit_status(m=mode)
    await settle(app.state.connection.client, lambda c: c.last_status.m == mode)

    response = await client.post("/api/device/mode", json={"mode": "brew"})

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"mode": "brew"}
    assert fake_device.mode_changes == [1]
    assert app.state.connection.client.last_status.m == 1
    assert await no_audit(app)


async def test_with_writes_off_nothing_is_sent_and_nothing_recorded(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = live

    response = await client.post("/api/device/mode", json={"mode": "standby"})

    assert response.status_code == 403
    assert "Writes switch" in response.json()["error"]["message"]
    assert "req:change-mode" not in fake_device.ws_requests
    assert await no_audit(app)


async def test_with_writes_off_it_refuses_even_when_already_in_that_mode(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The no-op shortcut must not become a way round the switch."""
    _app, client = live

    response = await client.post("/api/device/mode", json={"mode": "brew"})

    assert response.status_code == 403
    assert "req:change-mode" not in fake_device.ws_requests


async def test_already_in_that_mode_nothing_is_sent(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = live
    await writes_on(app)

    response = await client.post("/api/device/mode", json={"mode": "brew"})

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"mode": "brew"}
    assert "req:change-mode" not in fake_device.ws_requests


async def test_while_a_process_runs_nothing_is_sent_and_after_it_one_is(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The firmware deactivates the process before changing mode: a shot would be cut short."""
    app, client = live
    await writes_on(app)
    await fake_device.emit_status(process={"a": 1, "s": "brew", "l": "Infusion", "e": 0})
    await settle(
        app.state.connection.client,
        lambda c: c.last_status.process is not None and c.last_status.process.a == 1,
    )

    response = await client.post("/api/device/mode", json={"mode": "standby"})
    assert response.status_code == 403
    assert "would stop it" in response.json()["error"]["message"]
    assert "req:change-mode" not in fake_device.ws_requests

    await fake_device.emit_status(process={"a": 0, "s": "brew", "l": "Finished", "e": 28_000})
    await settle(app.state.connection.client, lambda c: c.last_status.process.a == 0)
    response = await client.post("/api/device/mode", json={"mode": "standby"})
    assert response.status_code == 200, response.text
    assert fake_device.mode_changes == [0]
    assert await no_audit(app)


async def test_while_the_machine_is_not_ready_nothing_is_sent(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The firmware ignores the frame then, so sending it would only wait for nothing."""
    app, client = live
    await writes_on(app)
    await fake_device.emit_status(sys={"s": "starting", "m": "", "c": 0})
    await settle(app.state.connection.client, lambda c: c.last_status.sys.s == "starting")

    response = await client.post("/api/device/mode", json={"mode": "standby"})

    assert response.status_code == 403
    assert "not ready" in response.json()["error"]["message"]
    assert "req:change-mode" not in fake_device.ws_requests


async def test_a_machine_that_never_reports_the_new_mode_is_a_timeout(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """Sent, but the state frame never came: say so rather than claim it switched."""
    app, client = live
    await writes_on(app)
    # The fake drops the request the way the firmware does when it is not ready,
    # while the app's last frame still says ready.
    fake_device.system_ready = False

    response = await client.post("/api/device/mode", json={"mode": "standby"})

    assert response.status_code == 504, response.text
    assert "did not report standby" in response.json()["error"]["message"]
    assert fake_device.mode_changes == [0]
    assert await no_audit(app)


@pytest.mark.parametrize("mode", ["steam", "water", "grind", 0, 1, ""])
async def test_only_brew_and_standby_are_accepted(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice, mode: object
) -> None:
    app, client = live
    await writes_on(app)

    response = await client.post("/api/device/mode", json={"mode": mode})

    assert response.status_code == 400
    assert "req:change-mode" not in fake_device.ws_requests


async def test_with_no_machine_configured_it_says_so(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/device/mode", json={"mode": "brew"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEVICE_UNAVAILABLE"


async def test_a_client_without_the_app_gate_cannot_change_mode(
    device_client: GaggimateClient, fake_device: FakeDevice
) -> None:
    """Deny-all by default holds for the mode switch too."""
    from gaggiclanker.device.writes import DeviceWriteRefused

    with pytest.raises(DeviceWriteRefused):
        await device_client.change_mode("standby")
    assert "req:change-mode" not in fake_device.ws_requests
