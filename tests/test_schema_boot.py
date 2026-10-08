"""Starting the app on a database: a new one is made, a matching one is kept, another is refused."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from gaggiclanker.db import schema
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def test_an_empty_data_directory_boots_on_a_fresh_schema(env: EnvSettings) -> None:
    async with running_app(env) as (app, _client):
        assert await app.state.db.fetch_value("SELECT COUNT(*) FROM machines") == 1
        assert await app.state.db.fetch_value("SELECT COUNT(*) FROM flavor_picks") == 22
        assert (
            await app.state.db.fetch_value(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = 'schema_migrations'"
            )
            == 0
        )


async def test_a_matching_database_is_kept_with_its_rows(env: EnvSettings) -> None:
    async with running_app(env) as (app, _client):
        await app.state.db.execute("INSERT INTO beans (name) VALUES ('Kept')")
    async with running_app(env) as (app, _client):
        assert await app.state.db.fetch_value("SELECT name FROM beans") == "Kept"


async def test_the_ledger_of_the_previous_version_is_dropped_at_boot(env: EnvSettings) -> None:
    async with running_app(env) as (app, _client):
        await app.state.db.execute("INSERT INTO beans (name) VALUES ('Kept')")
    conn = sqlite3.connect(env.database_path)
    conn.execute(
        "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, name TEXT, checksum TEXT)"
    )
    conn.execute("INSERT INTO schema_migrations VALUES ('0050', 'x', 'y')")
    conn.commit()
    conn.close()

    async with running_app(env) as (app, _client):
        assert await app.state.db.fetch_value("SELECT name FROM beans") == "Kept"
        assert (
            await app.state.db.fetch_value(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = 'schema_migrations'"
            )
            == 0
        )


async def test_a_database_with_another_schema_is_refused_and_not_touched(
    env: EnvSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    async with running_app(env):
        pass
    conn = sqlite3.connect(env.database_path)
    conn.execute("ALTER TABLE sets ADD COLUMN surprise TEXT")
    conn.execute("INSERT INTO beans (name) VALUES ('Precious')")
    conn.commit()
    conn.close()
    path = env.database_path
    before = _digest(path)
    names_before = sorted(p.name for p in path.parent.iterdir())
    capsys.readouterr()

    with pytest.raises(schema.SchemaMismatch) as refused:
        async with running_app(env):
            pass

    assert refused.value.differences[0] == "unexpected column sets.surprise"
    err = capsys.readouterr().err
    assert err.strip() == (
        f"The database at {path} was made by a different version of gaggiclanker. "
        "Delete it to start fresh (a backup can be restored only into the version "
        "that made it)."
    )
    assert _digest(path) == before
    assert sorted(p.name for p in path.parent.iterdir()) == names_before


async def test_the_refusal_is_logged_with_what_differs(
    env: EnvSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    from gaggiclanker.infra.logging import configure_logging

    async with running_app(env):
        pass
    conn = sqlite3.connect(env.database_path)
    conn.execute("DROP VIEW v_judgements")
    conn.commit()
    conn.close()
    configure_logging("info", json_output=True)
    try:
        with pytest.raises(schema.SchemaMismatch):
            async with running_app(env):
                pass
        logged = capsys.readouterr().out
    finally:
        configure_logging("warning", json_output=True)
    assert "database_schema_differs" in logged
    assert "missing view v_judgements" in logged


def test_serving_on_another_versions_database_exits_non_zero_with_one_line(
    env: EnvSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    from gaggiclanker.__main__ import _refuse_foreign_database

    conn = sqlite3.connect(env.database_path)
    conn.executescript(schema.schema_sql() + "\nALTER TABLE sets ADD COLUMN surprise TEXT;")
    conn.close()
    before = _digest(env.database_path)

    with pytest.raises(SystemExit) as exit_:
        _refuse_foreign_database(env)

    assert exit_.value.code == 1
    assert "was made by a different version of gaggiclanker" in capsys.readouterr().err
    assert _digest(env.database_path) == before


def test_a_waiting_reset_is_not_refused(env: EnvSettings) -> None:
    from gaggiclanker.__main__ import _refuse_foreign_database
    from gaggiclanker.db.reset import RESET_MARKER

    sqlite3.connect(env.database_path).executescript("CREATE TABLE unrelated (a);")
    (env.data_dir / RESET_MARKER).write_text("", encoding="utf-8")

    _refuse_foreign_database(env)  # the reset deletes that database before it is opened
