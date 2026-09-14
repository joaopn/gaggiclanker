"""Machine settings apply live: a PATCH rebuilds the connection, with no restart.

Two fake machines, A and B, and one app that boots pointed at A. Everything here
goes through `PATCH /api/settings` — the Settings page's own route — and then
asks the app, the fakes and the task registry what actually happened.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.fake import FakeDevice, build_fake_device
from gaggiclanker.settings import EnvSettings
from gaggiclanker.sync.engine import LOOP_TASK_NAMES
from tests.conftest import running_app
from tests.device.conftest import FIXTURES


async def wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    """Wait for ``condition`` to hold, polling; fail loudly if it never does."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.fixture
async def fake_b() -> AsyncIterator[FakeDevice]:
    """A second machine, with a board name of its own so a test can tell them apart."""
    device = build_fake_device(FIXTURES)
    device.identity.update({"hardware": "GaggiMate Standard Rev 2.0", "spiffsFree": 777_000})
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


@pytest.fixture
def env_a(data_dir: Path, fake_device: FakeDevice, monkeypatch: pytest.MonkeyPatch) -> EnvSettings:
    """Machine A as the environment's baseline, the way a compose file would set it."""
    monkeypatch.setenv("GAGGIMATE_HOST", fake_device.address)
    monkeypatch.setenv("GAGGIMATE_TIMEOUT_S", "2")
    return EnvSettings(
        DATA_DIR=str(data_dir),
        LOG_LEVEL="warning",
        LOG_JSON=True,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
async def on_a(env_a: EnvSettings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    async with running_app(env_a) as (app, client):
        assert app.state.connection.client is not None
        assert await app.state.connection.client.wait_connected(5.0)
        yield app, client


def current(app: FastAPI) -> GaggimateClient:
    client = app.state.connection.client
    assert client is not None, "no client"
    return client  # type: ignore[no-any-return]


async def patch(client: httpx.AsyncClient, body: dict[str, object]) -> httpx.Response:
    return await client.patch("/api/settings", json=body)


def sync_loops(app: FastAPI) -> list[str]:
    return sorted(name for name in app.state.tasks.names if name.startswith("sync-"))


def device_ws_tasks() -> int:
    return sum(1 for task in asyncio.all_tasks() if task.get_name() == "device-ws")


# ── the swap ─────────────────────────────────────────────────────────


async def test_changing_the_host_connects_to_the_new_machine_without_a_restart(
    on_a: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice, fake_b: FakeDevice
) -> None:
    app, client = on_a
    old = current(app)

    response = await patch(client, {"gaggimateHost": fake_b.address})
    assert response.status_code == 200, response.text

    new = current(app)
    assert new is not old
    assert new.host == fake_b.address
    assert await new.wait_connected(5.0)
    await wait_for(lambda: new.identity is not None)

    status = (await client.get("/api/device/status")).json()["data"]
    assert status["configured"] is True
    assert status["connected"] is True
    assert status["host"] == fake_b.address
    assert status["identity"]["hardware"] == "GaggiMate Standard Rev 2.0"

    # An identity pass through the new engine reads B, and the one machine row
    # moves with it rather than forking the archive.
    run = await app.state.connection.engine.sync_identity(trigger="test")
    assert run is not None and run.status == "ok"
    assert "/api/settings" in fake_b.requests
    machine = await app.state.db.fetch_one("SELECT host FROM machines WHERE id = 1")
    assert machine["host"] == fake_b.address
    sync_status = (await client.get("/api/sync/status")).json()["data"]
    assert sync_status["configured"] is True and sync_status["connected"] is True

    # The old client let go of A's socket.
    await wait_for(lambda: fake_device.client_count == 0)
    assert fake_b.client_count == 1


async def test_emptying_the_host_leaves_no_client_and_a_reset_brings_the_baseline_back(
    on_a: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = on_a

    assert (await patch(client, {"gaggimateHost": ""})).status_code == 200

    assert app.state.connection.client is None
    assert app.state.connection.engine is None
    assert sync_loops(app) == []
    assert (await client.get("/api/device/status")).json()["data"]["configured"] is False
    refused = await client.post("/api/sync/run", json={})
    assert refused.status_code == 503
    assert "No machine is configured" in refused.json()["error"]["message"]
    await wait_for(lambda: fake_device.client_count == 0)

    # `null` drops the stored override, so the environment's host applies again.
    assert (await patch(client, {"gaggimateHost": None})).status_code == 200
    back = current(app)
    assert back.host == fake_device.address
    assert await back.wait_connected(5.0)
    assert sync_loops(app) == sorted(LOOP_TASK_NAMES)


async def test_switching_sync_off_closes_the_connection_and_on_opens_it(
    on_a: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = on_a

    assert (await patch(client, {"deviceSyncEnabled": False})).status_code == 200
    assert app.state.connection.client is None
    assert sync_loops(app) == []
    await wait_for(lambda: fake_device.client_count == 0)

    assert (await patch(client, {"deviceSyncEnabled": True})).status_code == 200
    assert await current(app).wait_connected(5.0)
    await wait_for(lambda: fake_device.client_count == 1)
    assert sync_loops(app) == sorted(LOOP_TASK_NAMES)


async def test_protocol_and_timeout_changes_rebuild_the_client_with_the_new_values(
    on_a: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = on_a
    old = current(app)

    assert (await patch(client, {"gaggimateTimeoutSeconds": 3.5})).status_code == 200

    rebuilt = current(app)
    assert rebuilt is not old
    assert rebuilt.timeout == 3.5
    assert await rebuilt.wait_connected(5.0)


async def test_a_patch_that_changes_no_effective_value_leaves_the_connection_alone(
    on_a: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = on_a
    before = current(app)

    # The environment's value, now stored: the source changes, the value does not.
    response = await patch(client, {"gaggimateHost": fake_device.address, "modelDefault": "x"})
    assert response.status_code == 200
    assert response.json()["data"]["gaggimateHost"]["source"] == "database"

    assert current(app) is before
    assert before.connected


async def test_services_act_on_the_new_client_after_a_swap(
    on_a: tuple[FastAPI, httpx.AsyncClient], fake_b: FakeDevice
) -> None:
    app, client = on_a
    assert (await patch(client, {"gaggimateHost": fake_b.address})).status_code == 200
    new = current(app)
    assert await new.wait_connected(5.0)
    await wait_for(lambda: new.identity is not None)

    assert app.state.cleanup.client is new
    assert app.state.notes_writeback.client is new
    # The cleanup plan's free space is read off the machine it is talking to.
    plan = (await client.get("/api/device/cleanup/plan")).json()["data"]
    assert plan["free_bytes"] == 777_000
    # And a read the cleanup service makes goes to B's HTTP server.
    before = len(fake_b.requests)
    await app.state.cleanup._index_by_id()
    assert any(path.startswith("/api/history/index.bin") for path in fake_b.requests[before:])
    # A push would find it too: the draft service reads the connection it was given.
    async with app.state.drafts.connection.operation("a test") as held:
        assert held is new


# ── nothing left behind ──────────────────────────────────────────────


async def test_no_task_or_session_outlives_a_swap_or_the_shutdown(
    env_a: EnvSettings, fake_device: FakeDevice, fake_b: FakeDevice
) -> None:
    retired: list[GaggimateClient] = []
    async with running_app(env_a) as (app, client):
        assert await current(app).wait_connected(5.0)
        for host in (fake_b.address, fake_device.address, fake_b.address):
            retired.append(current(app))
            assert (await patch(client, {"gaggimateHost": host})).status_code == 200
            assert await current(app).wait_connected(5.0)
            # Exactly one engine's loops, and exactly one socket supervisor.
            assert sync_loops(app) == sorted(LOOP_TASK_NAMES)
            assert device_ws_tasks() == 1

        for old in retired:
            assert old._task is None
            assert old._session is None
            assert not old.connected
        last = current(app)

    assert last._task is None
    assert last._session is None
    assert device_ws_tasks() == 0
    assert app.state.connection.client is None
    await wait_for(lambda: fake_device.client_count == 0 and fake_b.client_count == 0)


async def test_an_identity_read_cut_by_a_swap_does_not_stay_running(
    on_a: tuple[FastAPI, httpx.AsyncClient],
    fake_b: FakeDevice,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An identity read is not a pull, so it does not block the change — and is closed."""
    app, client = on_a
    engine = app.state.connection.engine
    release = asyncio.Event()

    async def stuck() -> dict[str, object]:
        await release.wait()
        return {}

    monkeypatch.setattr(current(app), "get_settings", stuck)
    engine.request_identity_sync("test")
    await wait_for(lambda: engine._lock.locked())
    assert app.state.connection.busy() is None

    assert (await patch(client, {"gaggimateHost": fake_b.address})).status_code == 200

    running = await app.state.db.fetch_value(
        "SELECT COUNT(*) FROM sync_runs WHERE status = 'running'"
    )
    assert running == 0


# ── the ledger does not lie, and a failed build leaves nothing behind ─


async def test_a_pull_asked_for_during_a_settings_change_waits_for_it(
    on_a: tuple[FastAPI, httpx.AsyncClient],
    fake_device: FakeDevice,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression `scripts/repro_pull_cut_by_reconfigure.py` reproduces, first half.

    A pull poked between the busy check and the rebuild used to start on the old
    machine and be cut off; now it waits for the change and finds no machine.
    """
    app, client = on_a
    settings = app.state.settings_service
    real_write = settings.write
    writing, release = asyncio.Event(), asyncio.Event()

    async def held_write(validated: dict[str, str | None]) -> object:
        writing.set()
        await release.wait()
        return await real_write(validated)

    monkeypatch.setattr(settings, "write", held_write)
    change = asyncio.create_task(client.patch("/api/settings", json={"gaggimateHost": ""}))
    await asyncio.wait_for(writing.wait(), 5.0)
    pull = asyncio.create_task(client.post("/api/sync/run", json={"kind": "shots"}))
    await asyncio.sleep(0.2)
    assert not pull.done(), "the pull did not wait for the change"
    assert "/api/history/index.bin" not in fake_device.requests

    release.set()
    assert (await change).status_code == 200
    assert (await pull).status_code == 503
    runs = await app.state.db.fetch_value("SELECT COUNT(*) FROM sync_runs WHERE kind = 'backfill'")
    assert runs == 0


async def test_a_pull_cut_short_is_recorded_as_stopped_not_ok(
    env_a: EnvSettings, fake_device: FakeDevice
) -> None:
    """Second half: a cancellation counts no device error, and used to be filed `ok`."""
    fake_device.history_delay_s = 5.0
    async with running_app(env_a) as (app, _client):
        assert await current(app).wait_connected(5.0)
        app.state.connection.engine.request_shot_sync("manual")
        await wait_for(lambda: "/api/history/index.bin" in fake_device.requests)
        # The shutdown path stops the connection, which cancels the pass.
        await app.state.connection.stop()
        row = await app.state.db.fetch_one(
            "SELECT status, error FROM sync_runs WHERE kind = 'backfill' ORDER BY id DESC"
        )
    assert row["status"] == "error"
    assert "stopped before it finished" in row["error"]


async def test_a_build_that_fails_leaves_no_half_connection_and_is_retried(
    on_a: tuple[FastAPI, httpx.AsyncClient],
    fake_device: FakeDevice,
    fake_b: FakeDevice,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaggiclanker.sync.engine import SyncEngine

    app, client = on_a
    real_start = SyncEngine.start
    attempts = 0

    async def failing_once(self: SyncEngine, tasks: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("the engine would not start")
        await real_start(self, tasks)  # type: ignore[arg-type]

    monkeypatch.setattr(SyncEngine, "start", failing_once)

    failed = await patch(client, {"gaggimateHost": fake_b.address})
    assert failed.status_code == 500
    connection = app.state.connection
    assert connection.client is None
    assert connection.engine is None
    assert connection.config is None
    assert sync_loops(app) == []
    await wait_for(lambda: device_ws_tasks() == 0)
    await wait_for(lambda: fake_b.client_count == 0 and fake_device.client_count == 0)

    # The same values again: not taken for a no-op, built this time.
    retried = await patch(client, {"gaggimateHost": fake_b.address})
    assert retried.status_code == 200, retried.text
    assert current(app).host == fake_b.address
    assert await current(app).wait_connected(5.0)
    assert sync_loops(app) == sorted(LOOP_TASK_NAMES)


# ── every pass records being cut short ───────────────────────────────

#: Each pass, the client method it is held in, how it is asked for, and the
#: ledger kind its row carries.
PASSES: dict[str, tuple[str, str, str]] = {
    "identity": ("get_settings", "request_identity_sync", "identity"),
    "shots": ("fetch_index", "request_shot_sync", "backfill"),
    "notes": ("fetch_notes_json", "request_shot_sync", "notes"),
    "profiles": ("list_profiles", "request_profile_sync", "profiles"),
}


@pytest.fixture
async def archive_machine() -> AsyncIterator[FakeDevice]:
    """A machine with shots, profiles and one notes card, so every pass has work to do."""
    from tests.sync.conftest import SMALL_COUNT, build_archive_device

    device = build_archive_device(SMALL_COUNT, header_only=False)
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


@pytest.mark.parametrize(
    ("which", "cut"),
    [
        ("identity", "rebuild"),
        ("identity", "shutdown"),
        ("shots", "shutdown"),
        ("notes", "shutdown"),
        ("profiles", "shutdown"),
    ],
)
async def test_every_pass_cut_short_is_recorded_as_stopped(
    which: str,
    cut: str,
    data_dir: Path,
    archive_machine: FakeDevice,
    fake_b: FakeDevice,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation counts no device error; each pass must still say it was stopped.

    A pull cannot be cut by a rebuild — the settings change is refused while one
    runs — so shots, notes and profiles are cut by stopping the app. An identity
    read is not a pull, so a settings change cuts it, and so does stopping.
    The message is checked, not only the status: without a pass's own handler
    its row is left `running` and closed by the engine's backstop with a
    different sentence.
    """
    from gaggiclanker.db.connection import Database
    from gaggiclanker.sync.engine import STOPPED_MESSAGE

    method, request, kind = PASSES[which]
    monkeypatch.setenv("GAGGIMATE_HOST", archive_machine.address)
    monkeypatch.setenv("GAGGIMATE_TIMEOUT_S", "5")
    env = EnvSettings(DATA_DIR=str(data_dir), LOG_LEVEL="warning", _env_file=None)  # type: ignore[call-arg]

    async with running_app(env) as (app, client):
        machine = current(app)
        assert await machine.wait_connected(5.0)
        engine = app.state.connection.engine
        # Let the startup identity read finish first, so the held one is ours.
        await wait_for(lambda: not engine._lock.locked())
        reached = asyncio.Event()

        async def held(*_args: object, **_kwargs: object) -> object:
            reached.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(machine, method, held)
        getattr(engine, request)("test")
        await asyncio.wait_for(reached.wait(), 10.0)

        if cut == "rebuild":
            response = await patch(client, {"gaggimateHost": fake_b.address})
            assert response.status_code == 200, response.text
            row = await app.state.db.fetch_one(
                "SELECT status, error FROM sync_runs WHERE kind = ? ORDER BY id DESC", (kind,)
            )
        else:
            row = None

    if row is None:
        # Stopped with the app; read the ledger back from the file.
        db = Database(env.database_path)
        await db.connect()
        try:
            row = await db.fetch_one(
                "SELECT status, error FROM sync_runs WHERE kind = ? ORDER BY id DESC", (kind,)
            )
        finally:
            await db.close()

    assert row is not None
    assert row["status"] == "error", dict(row)
    assert row["error"] == STOPPED_MESSAGE
