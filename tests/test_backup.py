"""``POST /api/backup`` and the ``VACUUM INTO`` path underneath it."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.backup import create_backup
from gaggiclanker.settings import EnvSettings


async def test_backup_writes_a_readable_copy(client: httpx.AsyncClient, data_dir: Path) -> None:
    await client.patch("/api/settings", json={"gaggimateHost": "10.0.0.7"})

    response = await client.post("/api/backup")
    assert response.status_code == 201
    data = response.json()["data"]

    backup_path = Path(data["path"])
    assert backup_path.is_file()
    assert backup_path.parent == data_dir / "backups"
    assert backup_path.name == data["filename"]
    assert data["size_bytes"] == backup_path.stat().st_size > 0

    # The copy must be a usable database carrying the row we just wrote — the
    # point of VACUUM INTO over `cp` is that this holds while the app is live.
    with sqlite3.connect(backup_path) as conn:
        stored = conn.execute("SELECT value FROM settings WHERE key = 'gaggimateHost'").fetchone()
        assert stored == ("10.0.0.7",)
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)


async def test_backup_filename_is_timestamped(client: httpx.AsyncClient) -> None:
    data = (await client.post("/api/backup")).json()["data"]
    assert data["filename"].startswith("gaggiclanker-")
    assert data["filename"].endswith(".db")


async def test_two_backups_in_the_same_second_do_not_collide(app: FastAPI, data_dir: Path) -> None:
    """VACUUM INTO refuses to overwrite, so the name has to give way, not the call."""
    backups = data_dir / "backups"
    first = await create_backup(app.state.db, backups)
    second = await create_backup(app.state.db, backups)
    assert first.path != second.path
    assert first.path.is_file() and second.path.is_file()


async def test_backups_directory_is_created_on_demand(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    """A fresh bind mount has no backups/ until the first backup is taken."""
    assert not (data_dir / "backups").exists()
    assert (await client.post("/api/backup")).status_code == 201
    assert (data_dir / "backups").is_dir()


async def test_backup_lands_under_the_data_dir(env: EnvSettings, data_dir: Path) -> None:
    """It has to be inside the bind mount or a container recreate loses it."""
    assert env.backups_dir == data_dir / "backups"
    assert env.backups_dir.is_relative_to(env.data_dir)


async def test_a_backup_works_while_the_sync_engine_is_writing(
    app: FastAPI, data_dir: Path
) -> None:
    """The regression `scripts/repro_backup_during_sync.py` reproduces.

    The whole app shares one SQLite connection, and once sync is running that connection
    spends its time inside ``BEGIN IMMEDIATE`` — the sync engine wraps each shot
    and its samples so they land together. ``VACUUM`` cannot run inside a
    transaction, so a backup taken during a backfill (exactly when somebody
    reaches for one) used to answer 500 about one time in four. The backup now
    runs on its own connection.
    """
    db = app.state.db
    async with db.transaction():
        await db.execute("INSERT INTO beans (name) VALUES (?)", ("Backup bean",))
        result = await create_backup(db, data_dir / "backups")

    assert result.path.is_file()
    assert result.size_bytes > 0
    with sqlite3.connect(result.path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        # The row was still uncommitted when the snapshot was taken, so it is
        # correctly absent: a backup is a consistent copy, not a peek at
        # somebody else's open transaction.
        assert conn.execute("SELECT COUNT(*) FROM beans").fetchone() == (0,)


async def test_backup_failure_surfaces_as_an_envelope_error(app: FastAPI, tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory"
    target.write_text("in the way", encoding="utf-8")
    with pytest.raises(Exception, match=r"not-a-directory|Backup failed|exists"):
        await create_backup(app.state.db, target)
