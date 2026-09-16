#!/usr/bin/env python
"""Reproduce: a pull cut short by a machine-settings change is filed as `ok`.

    uv run python scripts/repro_pull_cut_by_reconfigure.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A change to the machine's connection settings is refused while a pull is under
way, and otherwise stores the settings and rebuilds the connection. Two holes
line up:

* **The window.** The busy check ran before the settings were written, and the
  write awaits the database. A pull asked for in between — `POST /api/sync/run`
  pokes the engine without asking the connection — passed nothing and started
  on the old machine, and the rebuild that followed cancelled it.
* **The ledger.** A shot pass closes its run in a ``finally``, and a
  cancellation counts no error, so the run cut off after reading nothing was
  recorded ``status = 'ok'``. The sync page then reports a healthy pull of an
  archive that did not fill. Shutdown cancels a pass the same way.

This script does it twice. First it holds the settings write open, asks for a
pull inside that window, lets the machine's index answer slowly so the pull is
still reading when the rebuild comes, and looks for a run filed ``ok`` that
archived nothing from a machine that holds shots. Then it starts a pull and
shuts the app down under it, and looks for the same thing.

The fix takes the pull request through the connection's lock, so it waits for
the change and goes to whatever connection results; and a pass that is
cancelled records itself as an error saying it was stopped.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import httpx

from gaggiclanker.device.fake import build_fake_device
from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


async def main() -> int:
    device = build_fake_device(FIXTURES)
    await device.start()
    # Slow enough that the pull is still reading the index when the rebuild
    # arrives, fast enough that the script finishes in a couple of seconds.
    device.history_delay_s = 1.0
    try:
        failures = 0
        for scenario in (reproduce, reproduce_shutdown):
            with tempfile.TemporaryDirectory() as tmp:
                env = EnvSettings(DATA_DIR=tmp, LOG_LEVEL="error", _env_file=None)  # type: ignore[call-arg]
                # The machine's address is a runtime setting, so it goes into
                # the archive before the app boots on it — which is the state a
                # configured box restarts in.
                await store_machine(env, device.address)
                app = create_app(env, web_dist=Path(tmp) / "no-web", dotenv={})
                failures += await scenario(app, device)
                device.requests.clear()
        return 1 if failures else 0
    finally:
        await device.stop()


async def store_machine(env: EnvSettings, address: str) -> None:
    """Write the machine's address into a fresh archive, through the settings service."""
    from gaggiclanker.db.connection import Database
    from gaggiclanker.db.migrations import run_migrations
    from gaggiclanker.db.settings_repo import SettingsRepository
    from gaggiclanker.settings_service import SettingsService

    db = Database(env.database_path)
    await db.connect()
    try:
        await run_migrations(db)
        await SettingsService(SettingsRepository(db)).apply(
            {"gaggimateHost": address, "gaggimateTimeoutSeconds": 5}
        )
    finally:
        await db.close()


async def ledger(db: object) -> tuple[list[object], int]:
    runs = await db.fetch_all(  # type: ignore[attr-defined]
        "SELECT id, kind, status, shots_inserted, error FROM sync_runs WHERE kind = 'backfill'"
    )
    shots = await db.fetch_value("SELECT COUNT(*) FROM shots")  # type: ignore[attr-defined]
    for row in runs:
        print(f"  run {row['id']}: {row['status']} inserted={row['shots_inserted']} {row['error']}")
    return runs, int(shots or 0)


def lies(runs: list[object], shots: int) -> bool:
    return not shots and any(
        row["status"] == "ok" and row["shots_inserted"] == 0  # type: ignore[index]
        for row in runs
    )


async def reproduce_shutdown(app: object, device: object) -> int:
    """A pull in flight when the app stops."""
    state = app.state  # type: ignore[attr-defined]
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        assert await state.connection.client.wait_connected(5.0), "the fake did not connect"
        state.connection.engine.request_shot_sync("manual")
        async with asyncio.timeout(5.0):
            while "/api/history/index.bin" not in device.requests:  # type: ignore[attr-defined]  # noqa: ASYNC110
                await asyncio.sleep(0.01)
        db = state.db
    # The database is closed with the app; reopen the file to read the ledger.
    from gaggiclanker.db.connection import Database

    reopened = Database(db.path)
    await reopened.connect()
    try:
        print("shutdown during a pull:")
        runs, shots = await ledger(reopened)
    finally:
        await reopened.close()
    if lies(runs, shots):
        print("BUG: a pull cut short by shutdown is recorded as ok")
        return 1
    print("fixed: the pull cut short by shutdown is not recorded as ok")
    return 0


async def reproduce(app: object, device: object) -> int:
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await reproduce_change(app, client, device)


async def reproduce_change(app: object, client: httpx.AsyncClient, device: object) -> int:
    """A pull asked for while a machine-settings change is being stored."""
    state = app.state  # type: ignore[attr-defined]
    assert await state.connection.client.wait_connected(5.0), "the fake did not connect"

    settings = state.settings_service
    real_write = settings.write
    writing = asyncio.Event()
    release = asyncio.Event()

    async def held_write(validated: dict[str, str | None]) -> object:
        writing.set()
        await release.wait()
        return await real_write(validated)

    settings.write = held_write

    change = asyncio.create_task(client.patch("/api/settings", json={"gaggimateHost": ""}))
    await asyncio.wait_for(writing.wait(), 5.0)

    pull = asyncio.create_task(client.post("/api/sync/run", json={"kind": "shots"}))

    async def index_requested() -> None:
        while "/api/history/index.bin" not in device.requests:  # type: ignore[attr-defined]  # noqa: ASYNC110
            await asyncio.sleep(0.01)

    # Old code: the pull starts at once and reaches the index. Fixed code: the
    # request waits for the change, so the index is never asked for; give it a
    # moment either way before letting the change through.
    await asyncio.wait({pull, asyncio.create_task(index_requested())}, timeout=0.5)
    release.set()
    changed = await change
    answered = await pull
    # Let any cut pass finish closing its row.
    await asyncio.sleep(0.2)

    print(f"settings change -> {changed.status_code}, pull -> {answered.status_code}")
    runs, shots = await ledger(state.db)
    if lies(runs, shots):
        print("BUG: a pull cut short by the settings change is recorded as ok")
        return 1
    print("fixed: no cut pull is recorded as ok")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
