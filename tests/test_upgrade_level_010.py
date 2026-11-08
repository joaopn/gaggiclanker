"""Upgrade step 10 (level 9 to 10): an insight can name the person's message it came from.

The databases here are built from the frozen schema of level 9, which is what the previous
version wrote, with insights of every kind and a conversation they came from. The step adds one
trailing column and changes no row.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import upgrade_database_file
from gaggiclanker.db.upgrades import LATEST, STEPS, frozen_schema_sql, level_010
from tests.conftest import running_app
from tests.test_restore_apply import env_in, no_signal
from tests.test_restore_check import ROUTE, upload

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)

#: What this step must produce, now that a later step moved the schema file on.
LEVEL_TEN = schema.describe_sql(frozen_schema_sql(10))

TABLES = ("knowledge_insights", "chat_threads", "chat_messages", "beans", "recipes")


def level_nine(directory: Path) -> Path:
    path = directory / "gaggiclanker.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(frozen_schema_sql(9))
        conn.execute("INSERT INTO beans (name) VALUES ('Alturas')")
        conn.execute("INSERT INTO recipes (name, bean_id) VALUES ('Alturas', 1)")
        conn.execute("INSERT INTO chat_threads (title, recipe_id) VALUES ('Talk', 1)")
        conn.execute(
            "INSERT INTO chat_messages (thread_id, role, content) VALUES (1, 'user', 'hello')"
        )
        conn.execute(
            "INSERT INTO knowledge_insights (text, source, confirmed) "
            "VALUES ('Rinse the basket.', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO knowledge_insights (text, source, recipe_id, thread_id, dismissed) "
            "VALUES ('A Recipe lesson.', 'chat', 1, 1, 1)"
        )
    finally:
        conn.close()
    return path


def test_the_step_is_the_tenth_and_expects_the_frozen_level_9_schema() -> None:
    assert [s.level for s in STEPS] == list(range(1, LATEST + 1))
    assert LATEST >= 10
    assert STEPS[9].foreign_keys_off is False
    built = schema.describe_sql(frozen_schema_sql(9))
    assert schema.fingerprint_of(built) == STEPS[9].expects == level_010.EXPECTS


def test_every_row_is_carried_and_no_insight_names_a_message(tmp_path: Path) -> None:
    path = level_nine(tmp_path)
    before = sqlite3.connect(path)
    kept = {
        table: before.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
        for table in TABLES
    }
    before.close()

    report = upgrade_database_file(path, tmp_path, steps=STEPS[:10], expected=LEVEL_TEN, now=NOW)

    assert report is not None and (report.from_level, report.to_level) == (9, 10)
    assert report.steps[0].counts == {"columns_added": 1}
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # Keyed on the column: an added REFERENCES renumbers the table's foreign key ids.
        assert [
            row
            for row in conn.execute("PRAGMA foreign_key_check")
            if row[0] == "knowledge_insights"
        ] == []
        for table, rows in kept.items():
            if table == "knowledge_insights":
                continue
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == rows, table  # noqa: S608
        # The old columns are untouched and the new one is NULL on every row.
        old = conn.execute("SELECT * FROM knowledge_insights ORDER BY id").fetchall()
        assert [row[:-1] for row in old] == kept["knowledge_insights"]
        assert [row[-1] for row in old] == [None, None]
        # A message's deletion clears the column and leaves the insight.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("UPDATE knowledge_insights SET from_message_id = 1 WHERE id = 1")
        conn.execute("DELETE FROM chat_messages WHERE id = 1")
        assert conn.execute(
            "SELECT from_message_id FROM knowledge_insights WHERE id = 1"
        ).fetchone() == (None,)
    finally:
        conn.close()


def test_the_upgraded_database_is_the_one_a_new_database_is(tmp_path: Path) -> None:
    path = level_nine(tmp_path)
    assert upgrade_database_file(path, tmp_path, steps=STEPS, now=NOW) is not None
    conn = sqlite3.connect(path)
    try:
        assert schema.schema_differences(conn) == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    finally:
        conn.close()


async def test_a_backup_taken_at_level_9_restores_into_this_version(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    backup = level_nine(tmp_path / "source")
    target = env_in(tmp_path / "target")
    async with running_app(target) as (app, client):
        no_signal(app)
        response = await upload(client, backup)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["upgrade_from_level"] == 9
        applied = await client.post(f"{ROUTE}/{data['token']}/apply")
        assert applied.status_code == 202, applied.text

    async with running_app(target) as (app, _client):
        assert await app.state.db.fetch_value("PRAGMA user_version") == LATEST
        assert await app.state.db.fetch_value("SELECT COUNT(*) FROM knowledge_insights") == 2
        assert (
            await app.state.db.fetch_value(
                "SELECT COUNT(*) FROM knowledge_insights WHERE from_message_id IS NULL"
            )
            == 2
        )
