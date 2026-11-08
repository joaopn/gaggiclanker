"""Upgrade step 11 (level 10 to 11): a deletion proposal may belong to no Recipe.

The databases here are built from the frozen schema of level 10, with a deletion proposal in
every status. The step rebuilds the table, so every row, every id and the counter are checked.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import upgrade_database_file
from gaggiclanker.db.upgrades import LATEST, STEPS, frozen_schema_sql, level_011
from tests.conftest import running_app
from tests.test_restore_apply import env_in, no_signal
from tests.test_restore_check import ROUTE, upload

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)

STATUSES = ("proposed", "deleted", "kept", "stale", "superseded")
REASON = "The shots no longer support it at all."


def level_ten(directory: Path) -> Path:
    path = directory / "gaggiclanker.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(frozen_schema_sql(10))
        conn.execute("INSERT INTO beans (name) VALUES ('Alturas')")
        conn.execute("INSERT INTO recipes (name, bean_id) VALUES ('Alturas', 1)")
        conn.execute("INSERT INTO chat_threads (title, recipe_id) VALUES ('Talk', 1)")
        for number, status in enumerate(STATUSES, start=1):
            conn.execute(
                "INSERT INTO knowledge_insights (text, source, recipe_id, confirmed) "
                "VALUES (?, 'chat', 1, 1)",
                (f"Lesson {number}.",),
            )
            conn.execute(
                "INSERT INTO recipe_insight_deletions "
                "(recipe_id, thread_id, insight_id, insight_text, reason, status, created_at, "
                "decided_at) VALUES (1, 1, ?, ?, ?, ?, ?, ?)",
                (
                    number if status == "proposed" or number % 2 else None,
                    f"Lesson {number}.",
                    REASON,
                    status,
                    f"2026-11-0{number}T10:00:00.000Z",
                    None if status == "proposed" else f"2026-11-0{number}T11:00:00.000Z",
                ),
            )
        # The newest row is gone: the counter must not hand its id out again.
        conn.execute("DELETE FROM recipe_insight_deletions WHERE id = 5")
    finally:
        conn.close()
    return path


def test_the_step_is_the_eleventh_and_expects_the_frozen_level_10_schema() -> None:
    assert [s.level for s in STEPS] == list(range(1, LATEST + 1))
    assert LATEST >= 11
    assert STEPS[10].foreign_keys_off is True
    built = schema.describe_sql(frozen_schema_sql(10))
    assert schema.fingerprint_of(built) == STEPS[10].expects == level_011.EXPECTS


def test_every_row_is_carried_with_its_id_and_the_column_now_takes_null(tmp_path: Path) -> None:
    path = level_ten(tmp_path)
    before = sqlite3.connect(path)
    kept = before.execute("SELECT * FROM recipe_insight_deletions ORDER BY id").fetchall()
    sequence = before.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'recipe_insight_deletions'"
    ).fetchone()
    before.close()
    assert [row[6] for row in kept] == list(STATUSES[:4])

    report = upgrade_database_file(path, tmp_path, steps=STEPS[:11], now=NOW)

    assert report is not None and (report.from_level, report.to_level) == (10, 11)
    assert report.steps[0].counts == {"recipe_insight_deletions": 4}
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert [
            row
            for row in conn.execute("PRAGMA foreign_key_check")
            if row[0] == "recipe_insight_deletions"
        ] == []
        assert conn.execute("SELECT * FROM recipe_insight_deletions ORDER BY id").fetchall() == kept
        assert (
            conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'recipe_insight_deletions'"
            ).fetchone()
            == sequence
        )
        # The point of the step: a proposal with no Recipe, and the indexes still guard.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO knowledge_insights (text, source) VALUES ('General.', 'user')")
        general = conn.execute("SELECT MAX(id) FROM knowledge_insights").fetchone()[0]
        conn.execute(
            "INSERT INTO recipe_insight_deletions (recipe_id, thread_id, insight_id, "
            "insight_text, reason) VALUES (NULL, 1, ?, 'General.', ?)",
            (general, REASON),
        )
        assert conn.execute("SELECT MAX(id) FROM recipe_insight_deletions").fetchone()[0] == 6
        try:
            conn.execute(
                "INSERT INTO recipe_insight_deletions (recipe_id, thread_id, insight_id, "
                "insight_text, reason) VALUES (NULL, 1, ?, 'General.', ?)",
                (general, REASON),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("the one-waiting-per-insight index was not recreated")
    finally:
        conn.close()


def test_the_upgraded_database_is_the_one_a_new_database_is(tmp_path: Path) -> None:
    path = level_ten(tmp_path)
    assert upgrade_database_file(path, tmp_path, steps=STEPS, now=NOW) is not None
    conn = sqlite3.connect(path)
    try:
        assert schema.schema_differences(conn) == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    finally:
        conn.close()


async def test_a_backup_taken_at_level_10_restores_into_this_version(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    backup = level_ten(tmp_path / "source")
    target = env_in(tmp_path / "target")
    async with running_app(target) as (app, client):
        no_signal(app)
        response = await upload(client, backup)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["upgrade_from_level"] == 10
        applied = await client.post(f"{ROUTE}/{data['token']}/apply")
        assert applied.status_code == 202, applied.text

    async with running_app(target) as (app, _client):
        assert await app.state.db.fetch_value("PRAGMA user_version") == LATEST
        assert await app.state.db.fetch_value("SELECT COUNT(*) FROM recipe_insight_deletions") == 4
