"""0041 adds the pattern tables and one nullable column, and loses nothing.

Upgraded from the previous tip's schema with insights in it: every row survives with no
run behind it, and the new tables enforce their statuses and the one-running-run guard.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations


async def test_existing_insights_survive_with_no_run_behind_them(
    data_dir: Path, tmp_path: Path
) -> None:
    database = Database(data_dir / "old.db")
    await database.connect()
    try:
        directory = tmp_path / "below-0041"
        directory.mkdir()
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name < "0041":
                shutil.copy(path, directory / path.name)
        await run_migrations(database, directory)
        await database.execute(
            "INSERT INTO knowledge_insights "
            "(scope_json, text, source, confirmed, confirmed_at, evidence_shot_ids_json) "
            "VALUES ('{}', 'Kept.', 'chat', 1, '2026-01-01T00:00:00Z', '[1, 2]'), "
            "('{\"bean_id\": 1}', 'Waiting.', 'user', 0, NULL, '[]')"
        )

        assert "0041" in await run_migrations(database)

        rows = await database.fetch_all(
            "SELECT text, evidence_shot_ids_json, confirmed, pattern_run_id "
            "FROM knowledge_insights ORDER BY id"
        )
        assert [tuple(row) for row in rows] == [
            ("Kept.", "[1, 2]", 1, None),
            ("Waiting.", "[]", 0, None),
        ]
        assert await database.fetch_all("PRAGMA foreign_key_check") == []
        assert await database.fetch_value("SELECT COUNT(*) FROM pattern_runs") == 0
        assert await database.fetch_value("SELECT COUNT(*) FROM pattern_proposals") == 0
    finally:
        await database.close()


@pytest.fixture
async def db(data_dir: Path) -> AsyncIterator[Database]:
    database = Database(data_dir / "new.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.parametrize(
    "bad",
    [
        "INSERT INTO pattern_runs (status) VALUES ('paused')",
        "INSERT INTO pattern_proposals (run_id, text, status) VALUES (1, 'x', 'approved-ish')",
        "INSERT INTO pattern_runs (status) VALUES ('running')",
    ],
)
async def test_the_constraints_refuse_what_the_rules_forbid(db: Database, bad: str) -> None:
    await db.execute("INSERT INTO pattern_runs (status) VALUES ('running')")
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(bad)


async def test_a_deleted_run_takes_its_proposals_and_leaves_the_insight_it_wrote(
    db: Database,
) -> None:
    await db.execute("INSERT INTO pattern_runs (id, status) VALUES (1, 'done')")
    await db.execute(
        "INSERT INTO knowledge_insights (id, scope_json, text, pattern_run_id) "
        "VALUES (1, '{}', 'General.', 1)"
    )
    await db.execute("INSERT INTO pattern_proposals (run_id, text, insight_id) VALUES (1, 'x', 1)")

    await db.execute("DELETE FROM pattern_runs WHERE id = 1")

    assert await db.fetch_value("SELECT COUNT(*) FROM pattern_proposals") == 0
    assert (
        await db.fetch_value("SELECT pattern_run_id FROM knowledge_insights WHERE id = 1") is None
    )
