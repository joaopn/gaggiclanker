"""The migration runner and its ledger."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import (
    MigrationError,
    load_migrations,
    run_migrations,
)
from gaggiclanker.settings import EnvSettings


@pytest.fixture
async def db(data_dir: Path) -> AsyncIterator[Database]:
    database = Database(data_dir / "test.db")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


async def test_shipped_migrations_apply(db: Database) -> None:
    applied = await run_migrations(db)
    assert applied == [m.version for m in load_migrations()]

    rows = await db.fetch_all("SELECT version, name, checksum FROM schema_migrations ORDER BY 1")
    assert [str(row["version"]) for row in rows] == applied
    assert all(len(str(row["checksum"])) == 64 for row in rows)


async def test_init_creates_the_settings_table(db: Database) -> None:
    await run_migrations(db)
    tables = {
        str(row["name"])
        for row in await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"settings", "schema_migrations"} <= tables


async def test_running_twice_is_a_no_op(db: Database) -> None:
    first = await run_migrations(db)
    assert first
    assert await run_migrations(db) == []

    count = await db.fetch_value("SELECT count(*) FROM schema_migrations")
    assert count == len(first)


async def test_only_pending_migrations_run(db: Database, tmp_path: Path) -> None:
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_first.sql").write_text("CREATE TABLE a (id INTEGER);", encoding="utf-8")
    assert await run_migrations(db, directory) == ["0001"]

    (directory / "0002_second.sql").write_text("CREATE TABLE b (id INTEGER);", encoding="utf-8")
    assert await run_migrations(db, directory) == ["0002"]


async def test_editing_an_applied_migration_is_refused(db: Database, tmp_path: Path) -> None:
    """A shipped migration is immutable; changing it would fork the schema silently."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    migration = directory / "0001_first.sql"
    migration.write_text("CREATE TABLE a (id INTEGER);", encoding="utf-8")
    await run_migrations(db, directory)

    migration.write_text("CREATE TABLE a (id INTEGER, extra TEXT);", encoding="utf-8")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        await run_migrations(db, directory)


async def test_missing_applied_migration_is_refused(db: Database, tmp_path: Path) -> None:
    """A database from a newer build must not be silently downgraded."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_first.sql").write_text("CREATE TABLE a (id INTEGER);", encoding="utf-8")
    (directory / "0002_second.sql").write_text("CREATE TABLE b (id INTEGER);", encoding="utf-8")
    await run_migrations(db, directory)

    (directory / "0002_second.sql").unlink()
    with pytest.raises(MigrationError, match="newer version"):
        await run_migrations(db, directory)


async def test_a_failing_migration_is_not_recorded(db: Database, tmp_path: Path) -> None:
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_broken.sql").write_text("CREATE TABLE ;", encoding="utf-8")

    with pytest.raises(MigrationError, match="0001_broken"):
        await run_migrations(db, directory)

    assert await db.fetch_value("SELECT count(*) FROM schema_migrations") == 0


def test_badly_named_file_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "init.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match=re.escape("NNNN_name.sql")):
        load_migrations(directory)


def test_duplicate_version_is_refused(tmp_path: Path) -> None:
    """Two people adding 0007 on different branches must not both apply."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0007_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (directory / "0007_b.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="duplicate migration version"):
        load_migrations(directory)


async def test_connection_pragmas(db: Database) -> None:
    """WAL and foreign keys are load-bearing, not hygiene: assert them."""
    assert str(await db.fetch_value("PRAGMA journal_mode")).lower() == "wal"
    assert await db.fetch_value("PRAGMA foreign_keys") == 1


async def test_foreign_keys_are_enforced(db: Database) -> None:
    await db.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    await db.execute(
        "CREATE TABLE child (id INTEGER PRIMARY KEY, "
        "parent_id INTEGER REFERENCES parent(id) ON DELETE CASCADE)"
    )
    with pytest.raises(Exception, match="FOREIGN KEY"):
        await db.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")


async def test_transaction_rolls_back(db: Database) -> None:
    await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError):
        async with db.transaction() as conn:
            await conn.execute("INSERT INTO t (id) VALUES (1)")
            raise RuntimeError("boom")
    assert await db.fetch_value("SELECT count(*) FROM t") == 0


async def test_app_runs_migrations_on_startup(app: FastAPI, env: EnvSettings) -> None:
    """The lifespan, not a separate command, is what migrates the database."""
    assert env.database_path.is_file()
    versions = await app.state.db.fetch_all("SELECT version FROM schema_migrations")
    assert [str(row["version"]) for row in versions] == [m.version for m in load_migrations()]


async def test_a_partly_failing_migration_leaves_nothing_behind(
    db: Database, tmp_path: Path
) -> None:
    """The schema change and its ledger row move together, or not at all.

    Without one transaction around both, the first statement would be committed
    and the ledger would have no row — so the next boot would retry the file and
    fail on 'table already exists', wedging the container on every start.
    """
    directory = tmp_path / "migrations"
    directory.mkdir()
    broken = directory / "0001_two_statements.sql"
    broken.write_text(
        "CREATE TABLE good (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE bad (id INTEGER PRIMARY KEY, ;\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match="0001_two_statements"):
        await run_migrations(db, directory)

    tables = {
        str(row["name"])
        for row in await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "good" not in tables
    assert "bad" not in tables
    assert await db.fetch_value("SELECT count(*) FROM schema_migrations") == 0

    # And the corrected file applies cleanly on the retry, which is only true
    # because the failed attempt left no half-built schema in the way.
    broken.write_text(
        "CREATE TABLE good (id INTEGER PRIMARY KEY);\nCREATE TABLE bad (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    assert await run_migrations(db, directory) == ["0001"]
    tables = {
        str(row["name"])
        for row in await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"good", "bad"} <= tables
    assert await db.fetch_value("SELECT count(*) FROM schema_migrations") == 1


async def test_ledger_row_is_not_written_without_the_schema(db: Database, tmp_path: Path) -> None:
    """The inverse of the above: a rolled-back migration is genuinely pending."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_ok.sql").write_text("CREATE TABLE a (id INTEGER);", encoding="utf-8")
    (directory / "0002_bad.sql").write_text("CREATE TABLE b (;", encoding="utf-8")

    with pytest.raises(MigrationError):
        await run_migrations(db, directory)

    rows = await db.fetch_all("SELECT version FROM schema_migrations")
    applied = [str(row["version"]) for row in rows]
    assert applied == ["0001"]
