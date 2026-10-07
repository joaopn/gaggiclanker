#!/usr/bin/env python
"""Reproduce: the machine's passwords are stored in the archive and served by the API.

    uv run python scripts/repro_machine_secrets_stored.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The identity pass stored the machine's ``GET /api/settings`` document verbatim
in ``machines.settings_json``. Firmware v1.9.0 puts ``wifiPassword`` (outside
access-point mode), ``apPassword`` and ``haPassword`` in that document, so the
archive held them in plain text, ``GET /api/machine`` served them to the web and
every backup file carried them. The identity frame (``identity_json``) is kept
whole as well, and so is the client's in-memory copy that
``GET /api/device/status`` serves.

This script starts the fake machine (whose settings carry the three keys with
sentinel values, and whose identity frame is given some too, since the row keeps
that frame whole), lets the app run its identity pass, and looks for the
sentinels in both stored columns, in the bodies of ``GET /api/machine`` and
``GET /api/device/status``, and in a backup file.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

import httpx

from gaggiclanker.device.fake import DEFAULT_DEVICE_SETTINGS, build_fake_device
from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

#: Not in the firmware's identity frame today; a later build may add anything.
IDENTITY_SENTINELS = {"apPassword": "fake-identity-ap-password", "authToken": "fake-identity-token"}


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


async def main() -> int:
    sentinels = [
        DEFAULT_DEVICE_SETTINGS[key] for key in ("wifiPassword", "apPassword", "haPassword")
    ] + list(IDENTITY_SENTINELS.values())
    device = build_fake_device(FIXTURES)
    device.identity.update(IDENTITY_SENTINELS)
    await device.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            env = EnvSettings(DATA_DIR=tmp, LOG_LEVEL="error")
            await store_machine(env, device.address)
            app = create_app(env, web_dist=Path(tmp) / "no-web")
            async with app.router.lifespan_context(app):
                connection = app.state.connection
                assert await connection.client.wait_connected(5.0), "the fake did not connect"
                run = await connection.engine.sync_identity(trigger="repro")
                assert run is not None and run.status == "ok", run
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                    machine_body = (await client.get("/api/machine")).text
                    status_body = (await client.get("/api/device/status")).text
                    backup_bytes = (
                        await client.get("/api/backup", params={"include_keys": "true"})
                    ).content

            row = (
                sqlite3.connect(env.database_path)
                .execute("SELECT settings_json, identity_json FROM machines")
                .fetchone()
            )
            places = {
                "machines.settings_json": row[0] or "",
                "machines.identity_json": row[1] or "",
                "GET /api/machine": machine_body,
                "GET /api/device/status": status_body,
            }
            leaks = [
                f"{where}: {value}"
                for where, text in places.items()
                for value in sentinels
                if value in text
            ]
            leaks += [f"backup file: {v}" for v in sentinels if v.encode() in backup_bytes]
    finally:
        await device.stop()

    for leak in leaks:
        print(f"LEAK {leak}")
    if leaks:
        print(f"BUG: {len(leaks)} secret(s) reached the archive or the API")
        return 1
    print("fixed: no machine secret is stored, served or backed up")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
