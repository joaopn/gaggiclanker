"""The migration runner and its ledger."""

from __future__ import annotations

import re
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import (
    MIGRATIONS_DIR,
    MigrationError,
    load_migrations,
    run_migrations,
)
from gaggiclanker.settings import EnvSettings
from gaggiclanker.tools.sql import SqlRefused, run_query


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


async def _migrate_below(db: Database, tmp_path: Path, version: str) -> None:
    """Bring a database up to just below `version`, using the shipped files.

    Copies rather than a slice of `load_migrations()`, because the runner takes
    a *directory*: this is the only way to stop at a version without teaching it
    a concept it does not otherwise need.
    """
    directory = tmp_path / f"below-{version}"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < version:
            shutil.copy(path, directory / path.name)
    await run_migrations(db, directory)


async def _migrate_to_0013(db: Database, tmp_path: Path) -> None:
    await _migrate_below(db, tmp_path, "0014")


async def test_0014_upgrades_a_populated_database(db: Database, tmp_path: Path) -> None:
    """The rebuild of `set_versions` has to survive rows that point at it.

    This is a regression test with a scar. 0014 widens a CHECK on a STRICT
    table, which means dropping and re-creating it — and two columns reference
    it, `set_versions.parent_version_id` and `suggestions.resulting_set_version_id`.
    The first version of the migration relied on `PRAGMA defer_foreign_keys`
    alone, which postpones the *check* but not the counting: the implicit DELETE
    inside `DROP TABLE` incremented the deferred-violation counter once per
    surviving child row, nothing decremented it, and COMMIT failed with
    "FOREIGN KEY constraint failed".

    It passed on an empty database, which is every test that had run until this
    one. So this test populates the exact three shapes that trip it — a version
    with a parent, an accepted suggestion pointing at a version, and a shot
    attached to one — and then boots to HEAD.
    """
    await _migrate_to_0013(db, tmp_path)

    await db.execute("INSERT INTO machines (host, created_at) VALUES ('kitchen.local', 'x')")
    await db.execute("INSERT INTO beans (name, created_at) VALUES ('Guji', 'x')")
    await db.execute(
        "INSERT INTO sets (name, bean_id, machine_id, created_at) VALUES ('S', 1, 1, 'x')"
    )
    await db.execute(
        "INSERT INTO set_versions (set_id, version_no, intent, created_at) "
        "VALUES (1, 1, 'baseline', 'x')"
    )
    await db.execute(
        "INSERT INTO set_versions (set_id, version_no, parent_version_id, intent, created_at) "
        "VALUES (1, 2, 1, 'two finer', 'x')"
    )
    await db.execute(
        "INSERT INTO shots (device_id, machine_id, raw_slog, set_version_id, synced_at, "
        "updated_at) VALUES ('000001', 1, x'00', 2, 'x', 'x')"
    )
    await db.execute("INSERT INTO shot_analyses (shot_id, status) VALUES (1, 'ok')")
    await db.execute(
        "INSERT INTO suggestions (analysis_id, variable, direction, reason, "
        "resulting_set_version_id) VALUES (1, 'grind', 'finer', 'sour', 2)"
    )
    await db.execute(
        "INSERT INTO profile_versions (content_hash, label, type, json, created_at) "
        "VALUES ('abc', '9 Bar', 'pro', '{}', 'x')"
    )
    await db.execute(
        "INSERT INTO profile_drafts (base_version_id, change_summary, created_at, updated_at) "
        "VALUES (1, 'by hand', 'x', 'x')"
    )

    # Everything from 0014 up, so the assertions below are made against HEAD
    # rather than against a version nobody ships.
    applied = await run_migrations(db)
    assert applied[0] == "0014"

    # Every row survived, and so did every link between them.
    versions = await db.fetch_all(
        "SELECT id, parent_version_id, intent FROM set_versions ORDER BY 1"
    )
    assert [(int(r["id"]), r["parent_version_id"], r["intent"]) for r in versions] == [
        (1, None, "baseline"),
        (2, 1, "two finer"),
    ]
    resulting = await db.fetch_value(
        "SELECT resulting_set_version_id FROM suggestions WHERE id = 1"
    )
    assert resulting == 2
    assert await db.fetch_value("SELECT set_version_id FROM shots WHERE id = 1") == 2
    assert await db.fetch_value("SELECT count(*) FROM profile_drafts") == 1

    # Nothing dangling, which is the assertion the broken version failed at
    # COMMIT rather than here.
    assert await db.fetch_all("PRAGMA foreign_key_check") == []

    # The temp stashes are gone rather than left on the connection.
    temp = await db.fetch_all("SELECT name FROM temp.sqlite_master WHERE type = 'table'")
    assert [str(row["name"]) for row in temp] == []

    # The views the swap had to drop are back, and they still read the table.
    view = await db.fetch_all(
        "SELECT set_version_id, version_no, shot_count FROM v_set_versions ORDER BY 1"
    )
    assert [
        (int(r["set_version_id"]), int(r["version_no"]), int(r["shot_count"])) for r in view
    ] == [(1, 1, 0), (2, 2, 1)]
    assert await db.fetch_value("SELECT count(*) FROM v_shots") == 1
    assert await db.fetch_value("SELECT count(*) FROM v_sets") == 1

    # And the widened CHECK is what the whole rebuild was for.
    await db.execute(
        "INSERT INTO set_versions (set_id, version_no, origin, created_at) "
        "VALUES (1, 3, 'starting_point', 'x')"
    )


async def test_0014_leaves_a_starting_point_version_readable_through_the_sql_tool(
    db: Database, tmp_path: Path
) -> None:
    """`query_shots` reads `v_set_versions`, and the swap re-created it by hand.

    A view re-created from stale text would be the kind of bug nobody notices
    until the chat answers a question with a column that is no longer there.
    """
    await _migrate_to_0013(db, tmp_path)
    await run_migrations(db)

    await db.execute("INSERT INTO machines (host, created_at) VALUES ('kitchen.local', 'x')")
    await db.execute("INSERT INTO beans (name, created_at) VALUES ('Guji', 'x')")
    await db.execute(
        "INSERT INTO sets (name, bean_id, machine_id, created_at) VALUES ('S', 1, 1, 'x')"
    )
    await db.execute(
        "INSERT INTO set_versions (set_id, version_no, origin, intent, created_at) "
        "VALUES (1, 1, 'starting_point', 'from the wizard', 'x')"
    )

    # `run_query` opens its own read-only connection on the file, so the rows
    # have to be committed — which they are: the connection is in autocommit
    # outside an explicit transaction.
    result = await run_query(db.path, "SELECT origin, intent FROM v_set_versions")
    assert result.columns == ["origin", "intent"]
    assert result.rows == [["starting_point", "from the wizard"]]


async def test_0015_upgrades_a_populated_database(db: Database, tmp_path: Path) -> None:
    """Dropping `beans.roast_date` has to survive beans that had one.

    The column is read by two curated views, and SQLite re-parses every view in
    the schema when a table is altered — so `ALTER TABLE ... DROP COLUMN` fails
    outright while `v_beans` and `v_sets` still name it. The migration drops
    them, drops the column and re-creates them underneath; this test is the
    check that the whole sequence works on a database that has rows in it,
    which an empty one would not show.
    """
    await _migrate_below(db, tmp_path, "0015")

    await db.execute("INSERT INTO machines (host, created_at) VALUES ('kitchen.local', 'x')")
    await db.execute(
        "INSERT INTO beans (name, roaster, roast_level, roast_date, created_at) "
        "VALUES ('Guji', 'Square Mile', 'light', '2026-02-20', 'x')"
    )
    await db.execute("INSERT INTO beans (name, roast_date, created_at) VALUES ('Decaf', NULL, 'x')")
    await db.execute(
        "INSERT INTO sets (name, bean_id, machine_id, created_at) VALUES ('S', 1, 1, 'x')"
    )
    await db.execute(
        "INSERT INTO set_versions (set_id, version_no, intent, created_at) "
        "VALUES (1, 1, 'baseline', 'x')"
    )
    await db.execute(
        "INSERT INTO shots (device_id, machine_id, raw_slog, set_version_id, synced_at, "
        "updated_at) VALUES ('000001', 1, x'00', 1, 'x', 'x')"
    )

    assert await run_migrations(db) == ["0015"]

    columns = {str(row["name"]) for row in await db.fetch_all("PRAGMA table_info(beans)")}
    assert "roast_date" not in columns
    # Everything else about the bean survived: this drops one column, not a row.
    assert await db.fetch_value("SELECT roaster FROM beans WHERE id = 1") == "Square Mile"
    assert await db.fetch_value("SELECT count(*) FROM beans") == 2

    # The two views the drop had to remove are back and still read the table.
    assert await db.fetch_value("SELECT count(*) FROM v_beans") == 2
    assert await db.fetch_value("SELECT bean_name FROM v_sets WHERE set_id = 1") == "Guji"
    assert await db.fetch_value("SELECT count(*) FROM v_shots") == 1
    assert await db.fetch_all("PRAGMA foreign_key_check") == []


async def test_0015_leaves_the_bean_views_readable_through_the_sql_tool(
    db: Database, tmp_path: Path
) -> None:
    """`query_shots` reads these views, and the migration re-typed them by hand.

    A view re-created from stale text is the kind of bug nobody notices until
    the chat answers a question with a column that is no longer there — or
    fails on one that is.
    """
    await _migrate_below(db, tmp_path, "0015")
    await run_migrations(db)

    await db.execute("INSERT INTO machines (host, created_at) VALUES ('kitchen.local', 'x')")
    await db.execute(
        "INSERT INTO beans (name, roast_level, created_at) VALUES ('Guji', 'light', 'x')"
    )
    await db.execute(
        "INSERT INTO sets (name, bean_id, machine_id, created_at) VALUES ('S', 1, 1, 'x')"
    )

    beans = await run_query(db.path, "SELECT name, roast_level FROM v_beans")
    assert beans.rows == [["Guji", "light"]]

    sets = await run_query(db.path, "SELECT name, bean_name, roast_level FROM v_sets")
    assert sets.rows == [["S", "Guji", "light"]]

    # And the column is gone from the view, rather than merely unselected.
    with pytest.raises(SqlRefused, match="no such column"):
        await run_query(db.path, "SELECT roast_date FROM v_beans")
