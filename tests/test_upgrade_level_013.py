"""Upgrade step 13 (level 12 to 13): the matching switch goes; a live Recipe always files.

The databases here are built from the frozen schema of level 12, with one Recipe in each state
the switch could leave: live and filing, live with filing off, archived. After the step every
row is there with its other columns as they were, the column is gone, and the matcher files a
shot under the Recipe that had filing off.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import upgrade_database_file
from gaggiclanker.db.upgrades import LATEST, STEPS, frozen_schema_sql, level_013
from tests.conftest import running_app
from tests.test_restore_apply import env_in, no_signal
from tests.test_restore_check import ROUTE, upload

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)

#: What this step must produce, now that a later step may move the schema file on.
LEVEL_THIRTEEN = schema.describe_sql(frozen_schema_sql(13))

FILING = 1
FILING_OFF = 2
ARCHIVED = 3

#: Every column but the one the step drops, read before and after.
KEPT = (
    "SELECT id, name, bean_id, grinder_id, created_at, archived, designing, design_brief, "
    "current_version_id, copied_from_version_id, board_id FROM recipes ORDER BY id"
)


def level_twelve(directory: Path) -> Path:
    path = directory / "gaggiclanker.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(frozen_schema_sql(12))
        conn.execute("INSERT INTO beans (name) VALUES ('Alturas')")
        for name, automatch, archived in (
            ("Filing", 1, 0),
            ("Filing off", 0, 0),
            ("Archived", 0, 1),
        ):
            conn.execute(
                "INSERT INTO recipes (name, bean_id, automatch, archived, created_at) "
                "VALUES (?, 1, ?, ?, '2026-11-01T10:00:00.000Z')",
                (name, automatch, archived),
            )
            recipe_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO recipe_versions (recipe_id, version_major) VALUES (?, 1)",
                (recipe_id,),
            )
            version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "UPDATE recipes SET current_version_id = ? WHERE id = ?", (version_id, recipe_id)
            )
    finally:
        conn.close()
    return path


def _rows(path: Path) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(KEPT).fetchall()
    finally:
        conn.close()


def test_the_step_is_the_thirteenth_and_expects_the_frozen_level_12_schema() -> None:
    assert [s.level for s in STEPS] == list(range(1, LATEST + 1))
    assert LATEST >= 13
    built = schema.describe_sql(frozen_schema_sql(12))
    assert schema.fingerprint_of(built) == STEPS[12].expects == level_013.EXPECTS


def test_the_column_goes_and_every_row_keeps_everything_else(tmp_path: Path) -> None:
    path = level_twelve(tmp_path)
    kept = _rows(path)

    report = upgrade_database_file(
        path, tmp_path, steps=STEPS[:13], expected=LEVEL_THIRTEEN, now=NOW
    )

    assert report is not None and (report.from_level, report.to_level) == (12, 13)
    # Only the live Recipe with filing off changes what it does; the archived one stays out.
    assert report.steps[0].counts == {"columns_dropped": 1, "live_recipes_now_filing": 1}
    assert _rows(path) == kept
    conn = sqlite3.connect(path)
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(recipes)")]
        assert "automatch" not in columns
        view = [row[1] for row in conn.execute("PRAGMA table_info(v_recipes)")]
        assert "automatch" not in view and "archived" in view
        assert conn.execute("SELECT COUNT(*) FROM v_recipes").fetchone()[0] == 3
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_the_upgraded_database_is_the_one_a_new_database_is(tmp_path: Path) -> None:
    path = level_twelve(tmp_path)
    assert upgrade_database_file(path, tmp_path, steps=STEPS, now=NOW) is not None
    conn = sqlite3.connect(path)
    try:
        assert schema.schema_differences(conn) == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    finally:
        conn.close()


async def test_a_backup_taken_at_level_12_restores_into_this_version(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    backup = level_twelve(tmp_path / "source")
    target = env_in(tmp_path / "target")
    async with running_app(target) as (app, client):
        no_signal(app)
        response = await upload(client, backup)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["upgrade_from_level"] == 12
        applied = await client.post(f"{ROUTE}/{data['token']}/apply")
        assert applied.status_code == 202, applied.text

    async with running_app(target) as (app, client):
        assert await app.state.db.fetch_value("PRAGMA user_version") == LATEST
        listed = (await client.get("/api/recipes?include_archived=true")).json()["data"]["items"]
        assert {row["name"]: row["archived"] for row in listed} == {
            "Filing": False,
            "Filing off": False,
            "Archived": True,
        }
