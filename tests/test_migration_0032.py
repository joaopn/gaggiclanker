"""0032 marks every push made before it as one that saved a profile.

The old code saved on every push, so a draft that already names a device profile put it
there. Left at the column's default of 0 it would read as "saved nothing": a rollback
would then discard the draft, forget the id and leave the profile on the machine.
"""

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


async def test_drafts_pushed_before_the_migration_read_as_having_saved(
    db: Database, tmp_path: Path
) -> None:
    directory = tmp_path / "below-0032"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "0032":
            shutil.copy(path, directory / path.name)
    await run_migrations(db, directory)
    await db.execute(
        "INSERT INTO profile_versions (id, content_hash, label, type, json) "
        "VALUES (1, 'h', 'P', 'standard', '{}')"
    )
    for draft_id, status, device_id in (
        (1, "pushed", "aaa"),
        (2, "failed", "bbb"),
        (3, "approved", None),
        (4, "discarded", None),
    ):
        await db.execute(
            "INSERT INTO profile_drafts (id, base_version_id, status, pushed_device_profile_id) "
            "VALUES (?, 1, ?, ?)",
            (draft_id, status, device_id),
        )

    assert "0032" in await run_migrations(db)

    rows = await db.fetch_all("SELECT id, pushed_saved FROM profile_drafts ORDER BY id")
    assert [(row["id"], row["pushed_saved"]) for row in rows] == [(1, 1), (2, 1), (3, 0), (4, 0)]
