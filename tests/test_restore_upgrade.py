"""Restoring a backup an earlier version made: upgraded on the staged copy, by the same steps.

One level past the latest the code ships is injected, so the tests mean the same with any
number of real steps: today's schema at another level, reached by a step that edits a column.
The backup is a real download taken at the latest shipped level.
"""

from __future__ import annotations

import sqlite3
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker import main as main_module
from gaggiclanker.api import backup as backup_api
from gaggiclanker.db import restore, schema
from gaggiclanker.db.upgrade import upgrade_database_file
from gaggiclanker.db.upgrades import LATEST, STEPS, Step, frozen_schema_sql
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app
from tests.test_restore_apply import env_in, no_signal
from tests.test_restore_check import ROUTE, digest, make_file, staged_files, upload

NEXT = LATEST + 1
SQL_NEXT = schema.schema_sql().replace(
    f"PRAGMA user_version = {LATEST};", f"PRAGMA user_version = {NEXT};"
)
EXPECTED_NEXT = schema.describe_sql(SQL_NEXT)
UPGRADED = "upgraded From the backup"


def _tag_beans(conn: sqlite3.Connection) -> dict[str, int]:
    return {"beans": conn.execute("UPDATE beans SET notes = 'upgraded ' || name").rowcount}


def _breaks(conn: sqlite3.Connection) -> dict[str, int]:
    conn.execute("UPDATE beans SET notes = 'half done'")
    raise RuntimeError("the fill went wrong")


def steps_to_next(run: Any = _tag_beans) -> list[Step]:
    """The steps the code ships, then the injected one that makes NEXT the latest level."""
    return [
        *STEPS,
        Step(level=NEXT, expects=schema.schema_fingerprint(), foreign_keys_off=False, run=run),
    ]


def frozen_sql(level: int) -> str:
    return schema.schema_sql() if level == LATEST else frozen_schema_sql(level)


def aim_at_next_level(monkeypatch: pytest.MonkeyPatch, steps: list[Step]) -> None:
    """The app's start and the restore's check, both with NEXT as the latest level."""
    monkeypatch.setattr(
        main_module,
        "upgrade_database_file",
        partial(
            upgrade_database_file,
            steps=steps,
            expected=EXPECTED_NEXT,
            frozen_sql=frozen_sql,
        ),
    )
    monkeypatch.setattr(
        main_module,
        "check_database_file",
        partial(schema.check_database_file, steps=steps, expected=EXPECTED_NEXT),
    )
    monkeypatch.setattr(
        backup_api,
        "validate_staged",
        partial(restore.validate_staged, steps=steps, expected=EXPECTED_NEXT),
    )


async def a_latest_level_backup(tmp_path: Path) -> Path:
    """A real download from an app at level 0, holding one bean."""
    source = env_in(tmp_path / "source")
    async with running_app(source) as (app, _client):
        await app.state.db.execute("INSERT INTO beans (name) VALUES ('From the backup')")
        return await make_file(app, tmp_path, name="level0.db")


async def test_a_backup_from_an_earlier_version_is_upgraded_on_the_staged_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = await a_latest_level_backup(tmp_path)
    uploaded = digest(backup)
    aim_at_next_level(monkeypatch, steps_to_next())
    target = env_in(tmp_path / "target")

    async with running_app(target) as (app, client):
        no_signal(app)
        live_before = digest(target.database_path)
        response = await upload(client, backup)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["upgrade_from_level"] == LATEST
        assert data["in_file"]["beans"] == 1
        # The staged copy is the one that was upgraded; the live database is untouched.
        (staged,) = staged_files(target.data_dir)
        conn = sqlite3.connect(staged)
        try:
            assert conn.execute("PRAGMA user_version").fetchone() == (NEXT,)
            assert conn.execute("SELECT notes FROM beans").fetchone() == (UPGRADED,)
        finally:
            conn.close()
        assert digest(backup) == uploaded
        assert not list(target.data_dir.glob("upgrade-backup-*"))  # the upload is the copy
        assert digest(target.database_path) == live_before

        applied = await client.post(f"{ROUTE}/{data['token']}/apply")
        assert applied.status_code == 202, applied.text

    # Restorable: the next start is on the upgraded copy, with nothing left to upgrade.
    async with running_app(target) as (app, _client):
        assert await app.state.db.fetch_value("PRAGMA user_version") == NEXT
        assert await app.state.db.fetch_value("SELECT notes FROM beans") == UPGRADED
    # No copy anywhere: the restore's upload was the copy, and the boot had nothing to upgrade.
    assert not list(target.data_dir.glob("upgrade-backup-*"))


async def test_a_backup_at_the_current_level_says_nothing_about_an_upgrade(
    env: EnvSettings, app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    path = await make_file(app, tmp_path)
    response = await upload(client, path)
    assert response.status_code == 200
    assert response.json()["data"]["upgrade_from_level"] is None


async def test_a_step_that_fails_on_the_staged_copy_refuses_and_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = await a_latest_level_backup(tmp_path)
    target = env_in(tmp_path / "target")
    async with running_app(target) as (_app, client):
        aim_at_next_level(monkeypatch, steps_to_next(_breaks))
        live_before = digest(target.database_path)
        response = await upload(client, backup)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RESTORE_SCHEMA_DIFFERS"
        assert "could not be brought up to date" in response.json()["error"]["message"]
        assert staged_files(target.data_dir) == []
        assert digest(target.database_path) == live_before


async def test_a_backup_that_is_not_in_the_state_its_level_left_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = await a_latest_level_backup(tmp_path)
    conn = sqlite3.connect(backup)
    conn.execute("ALTER TABLE recipes ADD COLUMN surprise TEXT")
    conn.commit()
    conn.close()
    target = env_in(tmp_path / "target")
    async with running_app(target) as (_app, client):
        aim_at_next_level(monkeypatch, steps_to_next())
        response = await upload(client, backup)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RESTORE_SCHEMA_DIFFERS"
        assert staged_files(target.data_dir) == []


async def test_a_backup_from_a_newer_version_is_refused(
    tmp_path: Path,
) -> None:
    backup = await a_latest_level_backup(tmp_path)
    conn = sqlite3.connect(backup)
    conn.execute(f"PRAGMA user_version = {LATEST + 4}")
    conn.close()
    target = env_in(tmp_path / "target")
    async with running_app(target) as (_app, client):
        response = await upload(client, backup)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RESTORE_SCHEMA_DIFFERS"
        assert "newer version of gaggiclanker" in response.json()["error"]["message"]
        assert staged_files(target.data_dir) == []


def a_backup_at_level(tmp_path: Path, level: int) -> Path:
    """A database as the version at ``level`` left it: its frozen schema, a bean and a Set."""
    path = tmp_path / f"level{level}-backup.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(frozen_schema_sql(level))
        conn.execute("INSERT INTO beans (name) VALUES ('From an older version')")
        conn.execute("INSERT INTO sets (name, bean_id) VALUES ('Older Set', 1)")
        assert conn.execute("PRAGMA user_version").fetchone() == (level,)
    finally:
        conn.close()
    return path


@pytest.mark.parametrize("level", [0, 1, 2, 3])
async def test_a_backup_from_before_the_rename_is_accepted_and_upgraded_with_its_rows(
    tmp_path: Path, level: int
) -> None:
    """The gate asks for tables a gaggiclanker database has at any level it can upgrade."""
    backup = a_backup_at_level(tmp_path, level)
    target = env_in(tmp_path / "target")

    async with running_app(target) as (app, client):
        no_signal(app)
        response = await upload(client, backup)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["upgrade_from_level"] == level
        assert data["in_file"]["beans"] == 1
        assert data["in_file"]["sets"] == 1
        applied = await client.post(f"{ROUTE}/{data['token']}/apply")
        assert applied.status_code == 202, applied.text

    async with running_app(target) as (app, _client):
        assert await app.state.db.fetch_value("PRAGMA user_version") == LATEST
        assert await app.state.db.fetch_value("SELECT name FROM recipes") == "Older Set"
        assert await app.state.db.fetch_value("SELECT name FROM beans") == "From an older version"


async def test_a_file_with_neither_the_old_nor_the_new_recipe_table_is_not_ours(
    tmp_path: Path,
) -> None:
    path = tmp_path / "neither.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE settings (key TEXT); CREATE TABLE shots (id INTEGER);"
        "CREATE TABLE beans (id INTEGER);"
    )
    conn.close()
    target = env_in(tmp_path / "target")
    async with running_app(target) as (app, client):
        no_signal(app)
        response = await upload(client, path)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RESTORE_NOT_A_DATABASE"
