"""0031 drops the cleanup ledger and the removed settings' rows, and keeps the write audit."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations


@pytest.fixture
async def db(data_dir: Path) -> AsyncIterator[Database]:
    database = Database(data_dir / "test.db")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


async def _migrate_below_0031(db: Database, tmp_path: Path) -> None:
    directory = tmp_path / "below-0031"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "0031":
            shutil.copy(path, directory / path.name)
    await run_migrations(db, directory)


async def test_the_cleanup_ledger_and_the_removed_settings_go_and_the_audit_stays(
    db: Database, tmp_path: Path
) -> None:
    await _migrate_below_0031(db, tmp_path)
    await db.execute("INSERT INTO cleanup_runs (mode) VALUES ('keep_newest')")
    for key in (
        "deviceCleanupMode",
        "deviceCleanupKeepNewest",
        "deviceCleanupMinFreeKb",
        "notesWritebackFields",
    ):
        await db.execute("INSERT INTO settings (key, value) VALUES (?, 'x')", (key,))
    await db.execute("INSERT INTO settings (key, value) VALUES ('gaggimateHost', '10.0.0.5')")
    for kind in ("profile_save", "shot_delete", "notes_save"):
        await db.execute(
            "INSERT INTO device_writes (kind, host, device_id, result) VALUES (?, 'm', '1', 'ok')",
            (kind,),
        )

    assert "0031" in await run_migrations(db)

    tables = {
        row["name"]
        for row in await db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "cleanup_runs" not in tables
    indexes = {
        row["name"]
        for row in await db.fetch_all("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert not {name for name in indexes if "cleanup_runs" in name}
    kept = await db.fetch_all("SELECT key FROM settings")
    assert [row["key"] for row in kept] == ["gaggimateHost"]
    kinds = await db.fetch_all("SELECT kind FROM device_writes ORDER BY id")
    assert [row["kind"] for row in kinds] == ["profile_save", "shot_delete", "notes_save"]
