"""0033 adds the profile board and a summary on every sync run, and loses nothing."""

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


async def test_existing_runs_survive_and_read_as_having_no_summary(
    db: Database, tmp_path: Path
) -> None:
    directory = tmp_path / "below-0033"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "0033":
            shutil.copy(path, directory / path.name)
    await run_migrations(db, directory)
    await db.execute(
        "INSERT INTO sync_runs (kind, status, trigger, started_at) "
        "VALUES ('profiles', 'ok', 'manual', '2026-09-30T10:00:00.000Z')"
    )

    assert "0033" in await run_migrations(db)

    row = await db.fetch_one("SELECT status, summary_json FROM sync_runs")
    assert row is not None and row["status"] == "ok" and row["summary_json"] is None
    # The board starts empty and un-adopted: the first pull with the switch on takes
    # the machine's profiles, and nothing before that does.
    assert await db.fetch_value("SELECT COUNT(*) FROM profile_board") == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM profile_board_adoption") == 0
    await db.execute(
        "INSERT INTO profile_versions (id, content_hash, label, type, json) "
        "VALUES (1, 'h', 'P', 'pro', '{}')"
    )
    await db.execute(
        "INSERT INTO profile_board (label, current_version_id, origin) VALUES ('P', 1, 'adopted')"
    )
    foreign_keys = await db.fetch_all("PRAGMA foreign_key_check")
    assert foreign_keys == []
