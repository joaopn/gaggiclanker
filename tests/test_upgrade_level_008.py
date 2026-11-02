"""Upgrade step 8 (level 7 to 8): signature change requests, and the expectations a draft touches.

The databases here are built from the frozen schema of level 7, which is what the previous
version wrote, with rows in every table the step touches or sits beside. The step adds one table
and one trailing column and changes no row.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import upgrade_database_file
from gaggiclanker.db.upgrades import LATEST, STEPS, frozen_schema_sql, level_008
from tests.conftest import running_app
from tests.test_restore_apply import env_in, no_signal
from tests.test_restore_check import ROUTE, upload

NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=UTC)


def level_seven(directory: Path) -> Path:
    path = directory / "gaggiclanker.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(frozen_schema_sql(7))
        conn.execute(
            "INSERT INTO profile_versions (content_hash, label, type, json, source) "
            "VALUES ('h1', 'A profile', 'pro', '{}', 'import')"
        )
        conn.execute(
            "INSERT INTO profile_drafts (base_version_id, draft_version_id, change_summary, notes) "
            "VALUES (1, 1, 'A cooler profile', 'kept')"
        )
        conn.execute("INSERT INTO profile_signatures (profile_version_id) VALUES (1)")
        conn.execute(
            "INSERT INTO signature_expectations (signature_id, position, tier, kind, sentence, "
            "status) VALUES (1, 0, 'critical', 'reached', 'the ramp begins', 'confirmed')"
        )
        conn.execute("INSERT INTO beans (name) VALUES ('Alturas')")
    finally:
        conn.close()
    return path


def test_the_step_is_the_eighth_and_expects_the_frozen_level_7_schema() -> None:
    assert [s.level for s in STEPS] == list(range(1, LATEST + 1))
    assert LATEST >= 8
    assert STEPS[7].foreign_keys_off is False
    built = schema.describe_sql(frozen_schema_sql(7))
    assert schema.fingerprint_of(built) == STEPS[7].expects == level_008.EXPECTS


def test_the_upgrade_is_the_schema_file_and_every_row_is_carried_unchanged(tmp_path: Path) -> None:
    path = level_seven(tmp_path)
    before = sqlite3.connect(path)
    kept = {
        table: before.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
        for table in ("profile_versions", "profile_signatures", "signature_expectations", "beans")
    }
    draft_before = before.execute(
        "SELECT id, base_version_id, draft_version_id, change_summary, notes, status, created_at "
        "FROM profile_drafts"
    ).fetchall()
    before.close()

    report = upgrade_database_file(path, tmp_path, steps=STEPS[:8], now=NOW)

    assert report is not None and (report.from_level, report.to_level) == (7, 8)
    assert report.steps[0].counts == {"tables_added": 1, "columns_added": 1}
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert list(conn.execute("PRAGMA foreign_key_check")) == []
        for table, rows in kept.items():
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == rows, table  # noqa: S608
        assert (
            conn.execute(
                "SELECT id, base_version_id, draft_version_id, change_summary, notes, status, "
                "created_at FROM profile_drafts"
            ).fetchall()
            == draft_before
        )
        # The new table is empty and the new column reads "no flag was ever worked out".
        assert conn.execute("SELECT COUNT(*) FROM signature_change_requests").fetchone()[0] == 0
        assert conn.execute("SELECT affected_expectations_json FROM profile_drafts").fetchone() == (
            "[]",
        )
    finally:
        conn.close()


def test_the_upgraded_database_is_the_one_a_new_database_is(tmp_path: Path) -> None:
    path = level_seven(tmp_path)
    assert upgrade_database_file(path, tmp_path, steps=STEPS, now=NOW) is not None
    conn = sqlite3.connect(path)
    try:
        assert schema.schema_differences(conn) == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    finally:
        conn.close()


async def test_a_backup_taken_at_level_7_restores_into_this_version(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    backup = level_seven(tmp_path / "source")
    target = env_in(tmp_path / "target")
    async with running_app(target) as (app, client):
        no_signal(app)
        response = await upload(client, backup)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["upgrade_from_level"] == 7
        applied = await client.post(f"{ROUTE}/{data['token']}/apply")
        assert applied.status_code == 202, applied.text

    async with running_app(target) as (app, _client):
        assert await app.state.db.fetch_value("PRAGMA user_version") == LATEST
        assert await app.state.db.fetch_value("SELECT COUNT(*) FROM signature_expectations") == 1
        assert await app.state.db.fetch_value("SELECT COUNT(*) FROM signature_change_requests") == 0
        assert await app.state.db.fetch_value("SELECT notes FROM profile_drafts") == "kept"
