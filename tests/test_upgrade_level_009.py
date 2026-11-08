"""Upgrade step 9 (level 8 to 9): a numbered grinder says which way its numbers run.

The databases here are built from the frozen schema of level 8, which is what the previous
version wrote, with a grinder of every step unit, a Recipe and a starting-point run that refer
to them, and a counter that a deleted grinder left ahead of the highest id.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import upgrade_database_file
from gaggiclanker.db.upgrades import LATEST, STEPS, frozen_schema_sql, level_009
from tests.conftest import running_app
from tests.test_restore_apply import env_in, no_signal
from tests.test_restore_check import ROUTE, upload

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)

#: What this step must produce, now that a later step moved the schema file on.
LEVEL_NINE = schema.describe_sql(frozen_schema_sql(9))

#: (id, name, model, burr_type, step_unit, notes, created_at) as level 8 stores them.
GRINDERS = [
    (1, "Niche Zero", "NZ", "conical", "numbers", "Lower grind number means finer.", "t1"),
    (2, "Comandante", None, "conical", "clicks", "", "t2"),
    (3, "EK43", None, "flat", "microns", "", "t3"),
    (4, "Hand mill", None, "unknown", "free", "", "t4"),
    (6, "Mazzer Philos", None, "flat", "numbers", "", "t6"),
]


def level_eight(directory: Path) -> Path:
    path = directory / "gaggiclanker.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(frozen_schema_sql(8))
        conn.executemany(
            "INSERT INTO grinders (id, name, model, burr_type, step_unit, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            GRINDERS,
        )
        # A grinder that was deleted after the last one was made: the counter is ahead of the ids.
        conn.execute("UPDATE sqlite_sequence SET seq = 7 WHERE name = 'grinders'")
        conn.execute("INSERT INTO beans (name) VALUES ('Alturas')")
        conn.execute("INSERT INTO recipes (name, bean_id, grinder_id) VALUES ('Alturas', 1, 6)")
        conn.execute("INSERT INTO starting_point_runs (bean_id, grinder_id) VALUES (1, 1)")
    finally:
        conn.close()
    return path


def test_the_step_is_the_ninth_and_expects_the_frozen_level_8_schema() -> None:
    assert [s.level for s in STEPS] == list(range(1, LATEST + 1))
    assert LATEST >= 9
    # Two tables refer to grinders, so the rebuild runs with foreign keys off.
    assert STEPS[8].foreign_keys_off is True
    built = schema.describe_sql(frozen_schema_sql(8))
    assert schema.fingerprint_of(built) == STEPS[8].expects == level_009.EXPECTS


def test_numbers_becomes_lower_is_finer_and_everything_else_is_carried(tmp_path: Path) -> None:
    path = level_eight(tmp_path)

    report = upgrade_database_file(path, tmp_path, steps=STEPS[:9], expected=LEVEL_NINE, now=NOW)

    assert report is not None and (report.from_level, report.to_level) == (8, 9)
    assert report.steps[0].counts == {"grinders": 5, "numbers_read_as_lower_finer": 2}
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert list(conn.execute("PRAGMA foreign_key_check")) == []
        expected = [
            (*row[:4], "numbers_lower_finer" if row[4] == "numbers" else row[4], *row[5:])
            for row in GRINDERS
        ]
        assert (
            conn.execute(
                "SELECT id, name, model, burr_type, step_unit, notes, created_at "
                "FROM grinders ORDER BY id"
            ).fetchall()
            == expected
        )
        # What refers to a grinder still does, and the views that read it are back.
        assert conn.execute("SELECT grinder_id FROM recipes").fetchone() == (6,)
        assert conn.execute("SELECT grinder_id FROM starting_point_runs").fetchone() == (1,)
        assert conn.execute("SELECT step_unit FROM v_grinders WHERE grinder_id = 1").fetchone() == (
            "numbers_lower_finer",
        )
        # The counter is the old one: a deleted grinder's id is not handed out again.
        conn.execute("INSERT INTO grinders (name) VALUES ('Next')")
        assert conn.execute("SELECT MAX(id) FROM grinders").fetchone() == (8,)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO grinders (name, step_unit) VALUES ('Old', 'numbers')")
        conn.execute("INSERT INTO grinders (name, step_unit) VALUES ('K', 'numbers_lower_coarser')")
    finally:
        conn.close()


def test_the_upgraded_database_is_the_one_a_new_database_is(tmp_path: Path) -> None:
    path = level_eight(tmp_path)
    assert upgrade_database_file(path, tmp_path, steps=STEPS, now=NOW) is not None
    conn = sqlite3.connect(path)
    try:
        assert schema.schema_differences(conn) == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    finally:
        conn.close()


async def test_a_backup_taken_at_level_8_restores_into_this_version(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    backup = level_eight(tmp_path / "source")
    target = env_in(tmp_path / "target")
    async with running_app(target) as (app, client):
        no_signal(app)
        response = await upload(client, backup)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["upgrade_from_level"] == 8
        applied = await client.post(f"{ROUTE}/{data['token']}/apply")
        assert applied.status_code == 202, applied.text

    async with running_app(target) as (app, _client):
        assert await app.state.db.fetch_value("PRAGMA user_version") == LATEST
        assert (
            await app.state.db.fetch_value("SELECT step_unit FROM grinders WHERE id = 6")
            == "numbers_lower_finer"
        )
        assert await app.state.db.fetch_value("SELECT grinder_id FROM recipes") == 6
