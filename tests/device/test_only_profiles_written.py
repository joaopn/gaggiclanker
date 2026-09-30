"""The machine is written with profiles and nothing else, and the old history writes are gone.

Two history writes used to exist: deleting shots from the machine (the Sync page's
storage cleanup) and writing a judgement to a shot's notes card. Both were removed;
the machine's own rotation deletes its oldest shots when storage runs low, and the
archive pulls before it does. What these tests pin from the outside, with the real
app connected to the fake machine and every old switch still on in the database:

* a pull and a judgement save send no frame that changes anything;
* the routes that started the two writes are gone;
* a settings row or an environment variable from before the removal resurrects
  nothing;
* audit rows of the two removed kinds are still history the Sync page can list.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.device.client import GATED_WRITE_METHODS
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.ids import pad6
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app, seed_settings
from tests.sync.conftest import FIRST_ID, SMALL_COUNT, build_archive_device

#: Every request type the client may send that changes something on the machine.
PROFILE_WRITES = frozenset(
    {
        "req:profiles:save",
        "req:profiles:delete",
        "req:profiles:select",
        "req:profiles:favorite",
        "req:profiles:unfavorite",
    }
)
REMOVED_WRITES = frozenset({"req:history:delete", "req:history:notes:save"})

RETIRED_ENV = (
    "GAGGICLANKER_DEVICE_CLEANUP_AUTO",
    "GAGGICLANKER_DEVICE_CLEANUP_MODE",
    "GAGGICLANKER_NOTES_WRITEBACK_ENABLED",
    "GAGGICLANKER_NOTES_WRITEBACK_FIELDS",
    "GAGGICLANKER_MCP_DEVICE_WRITES",
)
RETIRED_KEYS = (
    "deviceCleanupAuto",
    "deviceCleanupMode",
    "deviceCleanupKeepNewest",
    "deviceCleanupMinFreeKb",
    "notesWritebackEnabled",
    "notesWritebackFields",
    "mcpDeviceWrites",
)


@pytest.fixture
async def fake_device() -> AsyncIterator[FakeDevice]:
    device = build_archive_device(SMALL_COUNT, header_only=False)
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


@pytest.fixture
async def live(
    env: EnvSettings, fake_device: FakeDevice
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    await seed_settings(env, gaggimateHost=fake_device.address, gaggimateTimeoutSeconds=5)
    async with running_app(env) as (app, client):
        assert await app.state.connection.client.wait_connected(5.0)
        await app.state.connection.engine.sync_identity(trigger="test")
        await app.state.connection.engine.sync_shots(trigger="test")
        yield app, client


def test_the_client_writes_five_profile_operations_and_nothing_else() -> None:
    assert GATED_WRITE_METHODS == {
        "save_profile",
        "delete_profile",
        "select_profile",
        "favorite_profile",
        "unfavorite_profile",
    }


async def test_a_pull_and_a_judgement_save_change_nothing_on_the_machine(
    live: tuple[FastAPI, httpx.AsyncClient],
    fake_device: FakeDevice,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writes on, every retired switch still set: only reads go out, and no audit row appears."""
    app, client = live
    for name in RETIRED_ENV:
        monkeypatch.setenv(name, "true")
    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    for key in RETIRED_KEYS:
        await app.state.db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, 'true')", (key,)
        )
    fake_device.ws_requests.clear()

    assert (await app.state.connection.engine.sync_shots(trigger="test")).status == "ok"
    shot = await ShotsRepository(app.state.db).get_by_device_id(pad6(FIRST_ID))
    assert shot is not None
    response = await client.put(f"/api/shots/{shot.id}/judgement", json={"rating": 5})
    assert response.status_code == 200
    await asyncio.sleep(0.05)

    assert not set(fake_device.ws_requests) & (PROFILE_WRITES | REMOVED_WRITES)
    assert await DeviceWritesRepository(app.state.db).list_writes() == []
    # And the retired keys are not settings any more.
    keys = set((await client.get("/api/settings")).json()["data"])
    assert not keys & set(RETIRED_KEYS)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/device/cleanup/plan"),
        ("POST", "/api/device/cleanup/run"),
        ("GET", "/api/device/cleanup/runs"),
        ("GET", "/api/device/notes/pending"),
        ("POST", "/api/device/notes/push"),
        ("POST", "/api/shots/1/notes-writeback"),
    ],
)
async def test_the_routes_that_started_a_history_write_are_gone(
    live: tuple[FastAPI, httpx.AsyncClient], method: str, path: str
) -> None:
    _, client = live
    response = await client.request(method, path, json={"shot_ids": [1]})
    assert response.status_code in (404, 405), response.text


async def test_audit_rows_of_the_removed_kinds_still_list_as_history(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The table's CHECK still admits the two old kinds, so an old archive's rows stay readable."""
    app, client = live
    for kind in ("shot_delete", "notes_save"):
        await app.state.db.execute(
            "INSERT INTO device_writes (kind, host, device_id, result) "
            "VALUES (?, 'm', '000001', 'ok')",
            (kind,),
        )

    body = (await client.get("/api/device/writes")).json()
    assert body["ok"] is True
    assert {row["kind"] for row in body["data"]["items"]} == {"shot_delete", "notes_save"}
