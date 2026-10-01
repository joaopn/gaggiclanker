"""0034 lets a board profile remember its previous version, and loses nothing."""

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


async def test_existing_board_rows_survive_and_cannot_go_back_yet(
    db: Database, tmp_path: Path
) -> None:
    directory = tmp_path / "below-0034"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "0034":
            shutil.copy(path, directory / path.name)
    await run_migrations(db, directory)
    await db.execute(
        "INSERT INTO profile_versions (id, content_hash, label, type, json) "
        "VALUES (1, 'h', 'P', 'pro', '{}')"
    )
    await db.execute(
        "INSERT INTO profile_board (label, current_version_id, origin) VALUES ('P', 1, 'draft')"
    )

    assert "0034" in await run_migrations(db)

    row = await db.fetch_one("SELECT * FROM profile_board")
    assert row is not None and row["label"] == "P" and row["current_version_id"] == 1
    assert row["previous_version_id"] is None and row["back_from_set_version_id"] is None
    assert await db.fetch_all("PRAGMA foreign_key_check") == []
