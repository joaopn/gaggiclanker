"""0040 adds what an insight rests on and the deletion proposals, and loses nothing.

Upgraded from the previous tip's schema, with a Set insight in each state in it: every
row survives with an empty "rests on", no replacement and no outcome of one, and the new
table exists and enforces its rules.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations


async def test_existing_insights_survive_with_nothing_resting_on_anything(
    data_dir: Path, tmp_path: Path
) -> None:
    database = Database(data_dir / "old.db")
    await database.connect()
    try:
        directory = tmp_path / "below-0040"
        directory.mkdir()
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name < "0040":
                shutil.copy(path, directory / path.name)
        await run_migrations(database, directory)
        await database.execute(
            "INSERT INTO knowledge_insights "
            "(scope_json, text, source, confirmed, confirmed_at, evidence_shot_ids_json) "
            "VALUES ('{}', 'Kept.', 'chat', 1, '2026-01-01T00:00:00Z', '[1, 2]'), "
            "('{\"bean_id\": 1}', 'Waiting.', 'user', 0, NULL, '[]')"
        )

        assert await run_migrations(database) == ["0040"]

        rows = await database.fetch_all(
            "SELECT text, evidence_shot_ids_json, rests_on_json, replaces_id, replaces_text, "
            "replaced FROM knowledge_insights ORDER BY id"
        )
        assert [tuple(row) for row in rows] == [
            ("Kept.", "[1, 2]", "[]", None, "", None),
            ("Waiting.", "[]", "[]", None, "", None),
        ]
        assert await database.fetch_all("PRAGMA foreign_key_check") == []
        assert await database.fetch_value("SELECT COUNT(*) FROM set_insight_deletions") == 0
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
        "INSERT INTO set_insight_deletions (set_id, thread_id, insight_text, reason, status) "
        "VALUES (1, 1, 'x', 'too short', 'proposed')",
        "INSERT INTO set_insight_deletions (set_id, thread_id, insight_text, reason, status) "
        "VALUES (1, 1, 'x', 'a reason that is long enough to count', 'retired')",
        "UPDATE knowledge_insights SET replaced = 'maybe'",
    ],
)
async def test_the_constraints_refuse_what_the_rules_forbid(db: Database, bad: str) -> None:
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute("INSERT INTO knowledge_insights (scope_json, text) VALUES ('{}', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(bad)
