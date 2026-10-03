"""The migration runner and its ledger."""

from __future__ import annotations

import json
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
from gaggiclanker.db.repos.knowledge_insights import scope_matches
from gaggiclanker.settings import EnvSettings
from gaggiclanker.tools.sql import SqlRefused, run_query
from tests.conftest import running_app


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


async def test_a_comment_edited_after_it_was_applied_still_boots(
    db: Database, tmp_path: Path
) -> None:
    """Only what runs is frozen: a refusal costs the database, a comment must not."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    migration = directory / "0001_first.sql"
    migration.write_text("CREATE TABLE a (id INTEGER);", encoding="utf-8")
    await run_migrations(db, directory)

    migration.write_text(
        "-- Why a exists.\nCREATE TABLE a (\n    id INTEGER\n);\n", encoding="utf-8"
    )
    assert await run_migrations(db, directory) == []


async def test_a_byte_checksum_ledger_is_accepted_and_rewritten(
    db: Database, tmp_path: Path
) -> None:
    """A database made before the statement checksum keeps booting, and is upgraded once."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    migration = directory / "0001_first.sql"
    migration.write_text("CREATE TABLE a (id INTEGER);", encoding="utf-8")
    await run_migrations(db, directory)
    [loaded] = load_migrations(directory)
    await db.execute("UPDATE schema_migrations SET checksum = ?", (loaded.byte_checksum,))

    assert await run_migrations(db, directory) == []
    assert await db.fetch_value("SELECT checksum FROM schema_migrations") == loaded.checksum


async def test_every_shipped_file_recognises_its_byte_checksum_ledger(db: Database) -> None:
    """The archive a person runs today has byte checksums; the next boot must accept them all."""
    await run_migrations(db)
    for migration in load_migrations():
        await db.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE version = ?",
            (migration.byte_checksum, migration.version),
        )
    assert await run_migrations(db) == []
    rows = await db.fetch_all("SELECT version, checksum FROM schema_migrations ORDER BY 1")
    assert [str(row["checksum"]) for row in rows] == [m.checksum for m in load_migrations()]


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


async def _migrate_below(db: Database, tmp_path: Path, version: str) -> list[str]:
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
    return await run_migrations(db, directory)


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

    # Everything from 0014 up to the last migration that still has the
    # analysis tables these assertions read (0026 retires them).
    applied = await _migrate_below(db, tmp_path, "0026")
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

    await db.execute("INSERT INTO beans (name, created_at) VALUES ('Guji', 'x')")
    await db.execute("INSERT INTO sets (name, bean_id, created_at) VALUES ('S', 1, 'x')")
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

    # Everything from 0015 up, so the assertions below are made against HEAD.
    assert (await run_migrations(db))[0] == "0015"

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

    await db.execute(
        "INSERT INTO beans (name, roast_level, created_at) VALUES ('Guji', 'light', 'x')"
    )
    await db.execute("INSERT INTO sets (name, bean_id, created_at) VALUES ('S', 1, 'x')")

    beans = await run_query(db.path, "SELECT name, roast_level FROM v_beans")
    assert beans.rows == [["Guji", "light"]]

    sets = await run_query(db.path, "SELECT name, bean_name, roast_level FROM v_sets")
    assert sets.rows == [["S", "Guji", "light"]]

    # And the column is gone from the view, rather than merely unselected.
    with pytest.raises(SqlRefused, match="no such column"):
        await run_query(db.path, "SELECT roast_date FROM v_beans")


async def test_0016_merges_the_import_placeholder_into_the_real_machine(
    db: Database, tmp_path: Path
) -> None:
    """The shape a new install actually produces, and the one that used to rot.

    Import the archive first and connect the machine later — the natural order —
    and 0015's schema held two machines for ever: the importer's synthetic
    `import:default` and the real host. The same shot then existed twice,
    because the unique key was `(machine_id, device_id)` and the two copies were
    under different machines.

    Upgrading has to collapse that into one machine and one copy of the shot,
    and the copy that survives has to carry what the other one was holding: the
    samples, the verdict, the notes mirror, the analysis and the Set it was
    assigned to. Losing a typed verdict to a merge would be unforgivable.
    """
    await _migrate_below(db, tmp_path, "0016")

    await db.execute(
        "INSERT INTO machines (id, host, last_seen_at) "
        "VALUES (1, 'import:default', '2026-01-01T00:00:00.000Z')"
    )
    await db.execute(
        "INSERT INTO machines (id, host, name, last_seen_at) "
        "VALUES (2, 'kitchen.local', 'the kitchen one', '2026-03-01T00:00:00.000Z')"
    )
    await db.execute("INSERT INTO beans (name, created_at) VALUES ('Guji', 'x')")
    await db.execute(
        "INSERT INTO sets (name, bean_id, machine_id, active, created_at) "
        "VALUES ('Guji v1', 1, 2, 1, 'x')"
    )
    await db.execute(
        "INSERT INTO set_versions (set_id, version_no, intent, created_at) "
        "VALUES (1, 1, 'baseline', 'x')"
    )
    # The imported copy carries everything a person or an analysis added; the
    # copy pulled off the device carries only the bytes.
    await db.execute(
        "INSERT INTO shots (id, device_id, machine_id, raw_slog, source, set_version_id, "
        "synced_at, updated_at) VALUES (1, '000129', 1, x'00', 'import', 1, 'x', 'x')"
    )
    await db.execute(
        "INSERT INTO shots (id, device_id, machine_id, raw_slog, source, synced_at, updated_at) "
        "VALUES (2, '000129', 2, x'01', 'device', 'x', 'x')"
    )
    await db.execute("INSERT INTO shot_samples (shot_id, t_ms, ct) VALUES (1, 0, 93.0)")
    await db.execute("INSERT INTO shot_samples (shot_id, t_ms, ct) VALUES (1, 100, 93.5)")
    await db.execute("INSERT INTO shot_judgements (shot_id, rating) VALUES (1, 4)")
    await db.execute("INSERT INTO device_shot_notes (shot_id, raw_json) VALUES (1, '{}')")
    await db.execute("INSERT INTO shot_analyses (shot_id, status) VALUES (1, 'ok')")
    await db.execute(
        "INSERT INTO suggestions (analysis_id, variable, direction, reason) "
        "VALUES (1, 'grind', 'finer', 'sour')"
    )
    await db.execute(
        "INSERT INTO knowledge_insights (scope_json, text, analysis_id, confirmed) "
        "VALUES ('{\"bean_id\": 1, \"machine_id\": 2}', 'this bag likes it finer', 1, 1)"
    )
    await db.execute("INSERT INTO sync_events (kind, shot_id) VALUES ('shot_ingested', 1)")

    # Up to the last migration that still has the analysis tables the
    # assertions below read (0026 retires them).
    assert (await _migrate_below(db, tmp_path, "0026"))[0] == "0016"

    # One machine, and it is the real one, renumbered to the singleton's id.
    rows = await db.fetch_all("SELECT id, host, name FROM machines")
    assert [(int(r["id"]), r["host"], r["name"]) for r in rows] == [
        (1, "kitchen.local", "the kitchen one")
    ]

    # One shot, and it is the one whose bytes came off the machine.
    shots = await db.fetch_all("SELECT id, device_id, source, set_version_id FROM shots")
    assert [(int(r["id"]), r["device_id"], r["source"], r["set_version_id"]) for r in shots] == [
        (2, "000129", "device", 1)
    ]

    # Everything the loser was holding moved across rather than cascading away.
    samples = await db.fetch_all("SELECT shot_id, t_ms FROM shot_samples ORDER BY t_ms")
    assert [(int(r["shot_id"]), int(r["t_ms"])) for r in samples] == [(2, 0), (2, 100)]
    assert await db.fetch_value("SELECT shot_id FROM shot_judgements") == 2
    assert await db.fetch_value("SELECT rating FROM shot_judgements") == 4
    assert await db.fetch_value("SELECT shot_id FROM device_shot_notes") == 2
    assert await db.fetch_value("SELECT shot_id FROM shot_analyses") == 2
    # The analysis moved, so its suggestion came with it and the insight that
    # names it is still linked — `analysis_id` is ON DELETE SET NULL, so a
    # rebuild that let the analysis go would have unlinked it silently.
    assert await db.fetch_value("SELECT count(*) FROM suggestions") == 1
    assert await db.fetch_value("SELECT analysis_id FROM knowledge_insights") == 1

    # An insight's scope is JSON, so it is the one place a machine id can
    # outlive its column. The key is gone from the stored document and every
    # other stated key is untouched, so the insight still applies to the bag it
    # was confirmed about.
    scope = json.loads(str(await db.fetch_value("SELECT scope_json FROM knowledge_insights")))
    assert scope == {"bean_id": 1}
    # The matching rule itself, on the stored document: the repository's reads
    # are written for the latest schema, and this database stops at 0016.
    assert scope_matches(scope, {"bean_id": 1, "grinder_id": None})
    # The sync feed's shot id carries no foreign key, so nothing else would have
    # caught it pointing at a row that is gone.
    assert await db.fetch_value("SELECT shot_id FROM sync_events") == 2

    assert await db.fetch_all("PRAGMA foreign_key_check") == []
    temp = await db.fetch_all("SELECT name FROM temp.sqlite_master WHERE type = 'table'")
    assert [str(row["name"]) for row in temp] == []

    columns = {str(r["name"]) for r in await db.fetch_all("PRAGMA table_info(shots)")}
    assert "machine_id" not in columns

    # The curated views answer, which is what the chat reads.
    assert await db.fetch_value("SELECT count(*) FROM v_shots") == 1
    assert await db.fetch_value("SELECT set_name FROM v_shots") == "Guji v1"
    assert await db.fetch_value("SELECT shot_count FROM v_sets") == 1


async def test_0016_keeps_the_most_recently_seen_of_two_real_machines(
    db: Database, tmp_path: Path
) -> None:
    """A display board that changed address used to become a second machine.

    Its shots, profiles and Sets split off from the old ones and nothing merged
    them back. The upgrade keeps the machine the container is pointed at *now* —
    the most recently seen — and re-attaches everything the other one owned.
    """
    await _migrate_below(db, tmp_path, "0016")

    await db.execute(
        "INSERT INTO machines (id, host, last_seen_at) "
        "VALUES (1, '192.168.1.40', '2026-01-01T00:00:00.000Z')"
    )
    await db.execute(
        "INSERT INTO machines (id, host, last_seen_at) "
        "VALUES (2, '192.168.1.77', '2026-03-01T00:00:00.000Z')"
    )
    await db.execute("INSERT INTO beans (name, created_at) VALUES ('Guji', 'x')")
    # An active Set on each: "active" is about to stop being per-machine, and
    # the partial unique index that replaces it would refuse two.
    await db.execute(
        "INSERT INTO sets (name, bean_id, machine_id, active, created_at) "
        "VALUES ('old address', 1, 1, 1, '2026-01-01')"
    )
    await db.execute(
        "INSERT INTO sets (name, bean_id, machine_id, active, created_at) "
        "VALUES ('new address', 1, 2, 1, '2026-03-01')"
    )
    await db.execute(
        "INSERT INTO profile_versions (content_hash, label, type, json, created_at) "
        "VALUES ('abc', '9 Bar', 'pro', '{}', 'x')"
    )
    # The same slot mirrored from both addresses: one row after the upgrade.
    await db.execute(
        "INSERT INTO device_profiles (device_id, machine_id, current_version_id, last_seen_at) "
        "VALUES ('1', 1, 1, '2026-01-01T00:00:00.000Z')"
    )
    await db.execute(
        "INSERT INTO device_profiles (device_id, machine_id, current_version_id, last_seen_at) "
        "VALUES ('1', 2, 1, '2026-03-01T00:00:00.000Z')"
    )
    # Different shots, so nothing merges: both survive, both on the one machine.
    await db.execute(
        "INSERT INTO shots (device_id, machine_id, raw_slog, synced_at, updated_at) "
        "VALUES ('000001', 1, x'00', 'x', 'x')"
    )
    await db.execute(
        "INSERT INTO shots (device_id, machine_id, raw_slog, synced_at, updated_at) "
        "VALUES ('000002', 2, x'01', 'x', 'x')"
    )
    await db.execute("INSERT INTO cleanup_runs (machine_id, mode) VALUES (1, 'keep_newest')")
    await db.execute("INSERT INTO starting_point_runs (bean_id, machine_id) VALUES (1, 1)")

    assert (await run_migrations(db))[0] == "0016"

    hosts = await db.fetch_all("SELECT id, host FROM machines")
    assert [(int(r["id"]), r["host"]) for r in hosts] == [(1, "192.168.1.77")]

    # Nothing was dropped on the floor: both shots, the starting-point run, one
    # profile row. (The cleanup ledger this test also seeds is dropped by 0031,
    # which `run_migrations` goes on to apply.)
    assert await db.fetch_value("SELECT count(*) FROM shots") == 2
    assert await db.fetch_value("SELECT count(*) FROM starting_point_runs") == 1
    profiles = await db.fetch_all("SELECT device_id, last_seen_at FROM device_profiles")
    assert [(r["device_id"], r["last_seen_at"]) for r in profiles] == [
        ("1", "2026-03-01T00:00:00.000Z")
    ]

    # 0016 left one active Set — the survivor machine's — and 0022 then retired
    # that idea entirely, so what is asserted here is only that both Sets came
    # through the collapse: neither was archived and neither was dropped.
    names = await db.fetch_all("SELECT name FROM sets ORDER BY name")
    assert [r["name"] for r in names] == ["new address", "old address"]

    for table in ("sets", "starting_point_runs", "device_profiles"):
        columns = {str(r["name"]) for r in await db.fetch_all(f"PRAGMA table_info({table})")}
        assert "machine_id" not in columns, table
    assert await db.fetch_all("PRAGMA foreign_key_check") == []


async def test_0016_gives_an_archive_with_no_machine_the_placeholder_row(
    db: Database, tmp_path: Path
) -> None:
    """A fresh install, and an import-only one, both have "the machine" after this.

    The row is a singleton describing whatever host is configured, so it has to
    exist before the first pull — otherwise every consumer would have to model
    the absence of a thing that is meant to be always there. An empty host is
    the honest statement of "nothing is configured yet".
    """
    await _migrate_below(db, tmp_path, "0016")
    assert await db.fetch_value("SELECT count(*) FROM machines") == 0

    assert (await run_migrations(db))[0] == "0016"

    rows = await db.fetch_all("SELECT id, host, name FROM machines")
    assert [(int(r["id"]), r["host"], r["name"]) for r in rows] == [(1, "", "")]

    # And it stays a singleton: a second row is refused by the schema rather
    # than by whoever remembers.
    with pytest.raises(Exception, match="CHECK"):
        await db.execute("INSERT INTO machines (id, host) VALUES (2, 'other.local')")


async def test_0016_leaves_an_import_only_archive_with_an_unconfigured_machine(
    db: Database, tmp_path: Path
) -> None:
    """The placeholder survives as *the* machine, but not as an address.

    `import:default` was the importer's way of giving a shot an owner when
    `shots.machine_id` was NOT NULL. It was never something anything could
    reach, so carrying it into a column the Hardware page renders as the host
    would be showing a person a made-up address.
    """
    await _migrate_below(db, tmp_path, "0016")

    await db.execute(
        "INSERT INTO machines (id, host, name) VALUES (1, 'import:default', 'Imported shots')"
    )
    await db.execute(
        "INSERT INTO shots (device_id, machine_id, raw_slog, source, synced_at, updated_at) "
        "VALUES ('000001', 1, x'00', 'import', 'x', 'x')"
    )

    assert (await run_migrations(db))[0] == "0016"

    rows = await db.fetch_all("SELECT id, host, name FROM machines")
    assert [(int(r["id"]), r["host"], r["name"]) for r in rows] == [(1, "", "Imported shots")]
    # The shot is still there, filed against the machine the sync engine will
    # later write its real host onto.
    assert await db.fetch_value("SELECT count(*) FROM shots") == 1


async def test_0016_leaves_the_shot_views_readable_through_the_sql_tool(
    db: Database, tmp_path: Path
) -> None:
    """`query_shots` reads these views, and the swap re-typed five of them by hand.

    A view re-created from stale text is the kind of bug nobody notices until
    the chat answers a question with a column that is no longer there.
    """
    await _migrate_below(db, tmp_path, "0016")
    await run_migrations(db)

    await db.execute("INSERT INTO beans (name, created_at) VALUES ('Guji', 'x')")
    await db.execute("INSERT INTO sets (name, bean_id, created_at) VALUES ('S', 1, 'x')")
    await db.execute(
        "INSERT INTO set_versions (set_id, version_no, intent, created_at) "
        "VALUES (1, 1, 'baseline', 'x')"
    )
    await db.execute(
        "INSERT INTO shots (device_id, raw_slog, set_version_id, synced_at, updated_at) "
        "VALUES ('000001', x'00', 1, 'x', 'x')"
    )
    await db.execute("INSERT INTO shot_judgements (shot_id, rating) VALUES (1, 5)")

    shots = await run_query(db.path, "SELECT device_id, set_name, rating FROM v_shots")
    assert shots.rows == [["000001", "S", 5]]
    sets = await run_query(db.path, "SELECT name, shot_count FROM v_sets")
    assert sets.rows == [["S", 1]]
    versions = await run_query(db.path, "SELECT version_no, shot_count FROM v_set_versions")
    assert versions.rows == [[1, 1]]
    judgements = await run_query(db.path, "SELECT shot_id, rating FROM v_judgements")
    assert judgements.rows == [[1, 5]]

    # And the column is gone from the views, rather than merely unselected.
    with pytest.raises(SqlRefused, match="no such column"):
        await run_query(db.path, "SELECT machine_id FROM v_shots")
    with pytest.raises(SqlRefused, match="no such column"):
        await run_query(db.path, "SELECT machine_id FROM v_sets")


async def test_0017_upgrades_a_populated_database(db: Database, tmp_path: Path) -> None:
    """Dropping `beans.altitude_m` has to survive beans that had one.

    `v_beans` names the column and SQLite re-parses every view when a table is
    altered, so the drop fails outright while the view stands. The migration
    drops it, drops the column and re-creates the view underneath; `v_sets` joins
    `beans` without naming the column and is left in place, which only a
    database with a Set on the bean shows still answers.
    """
    await _migrate_below(db, tmp_path, "0017")

    await db.execute(
        "INSERT INTO beans (name, roaster, origin, altitude_m, roast_level, created_at) "
        "VALUES ('Kenya Nyeri', 'Square Mile', 'Kenya', 1900, 'light', 'x')"
    )
    await db.execute("INSERT INTO beans (name, altitude_m, created_at) VALUES ('Decaf', NULL, 'x')")
    await db.execute("INSERT INTO sets (name, bean_id, created_at) VALUES ('S', 1, 'x')")
    await db.execute(
        "INSERT INTO set_versions (set_id, version_no, intent, created_at) "
        "VALUES (1, 1, 'baseline', 'x')"
    )
    await db.execute(
        "INSERT INTO shots (device_id, raw_slog, set_version_id, synced_at, updated_at) "
        "VALUES ('000001', x'00', 1, 'x', 'x')"
    )

    # Everything from 0017 up, so the assertions below are made against HEAD.
    assert (await run_migrations(db))[0] == "0017"

    columns = {str(row["name"]) for row in await db.fetch_all("PRAGMA table_info(beans)")}
    assert "altitude_m" not in columns
    # Everything else about the bean survived: this drops one column, not a row.
    assert await db.fetch_value("SELECT origin FROM beans WHERE id = 1") == "Kenya"
    assert await db.fetch_value("SELECT count(*) FROM beans") == 2
    assert await db.fetch_value("SELECT count(*) FROM sets") == 1
    assert await db.fetch_value("SELECT count(*) FROM shots") == 1

    # The view the drop had to remove is back, and the ones it left still read.
    assert await db.fetch_value("SELECT count(*) FROM v_beans") == 2
    assert await db.fetch_value("SELECT bean_name FROM v_sets WHERE set_id = 1") == "Kenya Nyeri"
    assert await db.fetch_value("SELECT shot_count FROM v_sets WHERE set_id = 1") == 1
    assert await db.fetch_value("SELECT count(*) FROM v_shots") == 1
    assert await db.fetch_all("PRAGMA foreign_key_check") == []

    # Through the chat's SQL tool too: the view text is what it describes, and
    # the column is gone from it rather than merely unselected.
    beans = await run_query(db.path, "SELECT name, origin FROM v_beans ORDER BY bean_id")
    assert beans.rows == [["Kenya Nyeri", "Kenya"], ["Decaf", None]]
    with pytest.raises(SqlRefused, match="no such column"):
        await run_query(db.path, "SELECT altitude_m FROM v_beans")


async def test_0018_deletes_the_retired_machine_write_switches_and_nothing_else(
    db: Database, tmp_path: Path
) -> None:
    """A stored consent to an automatic machine write must not outlive its switch."""
    await _migrate_below(db, tmp_path, "0018")
    for key, value in (
        ("mcpDeviceWrites", "true"),
        ("deviceCleanupAuto", "true"),
        ("notesWritebackEnabled", "true"),
        ("deviceWritesEnabled", "true"),
        ("gaggimateHost", "10.0.0.5"),
    ):
        await db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    await run_migrations(db)

    rows = await db.fetch_all("SELECT key, value FROM settings ORDER BY key")
    assert [(row["key"], row["value"]) for row in rows] == [
        ("deviceWritesEnabled", "true"),
        ("gaggimateHost", "10.0.0.5"),
    ]


async def test_0019_deletes_the_retired_mcp_endpoint_switch_and_nothing_else(
    db: Database, tmp_path: Path
) -> None:
    """A stored consent to serve the archive over a network endpoint goes with the endpoint."""
    await _migrate_below(db, tmp_path, "0019")
    for key, value in (
        ("mcpEnabled", "true"),
        ("chatMaxToolRounds", "5"),
        ("deviceWritesEnabled", "true"),
    ):
        await db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    await run_migrations(db)

    rows = await db.fetch_all("SELECT key, value FROM settings ORDER BY key")
    assert [(row["key"], row["value"]) for row in rows] == [
        ("chatMaxToolRounds", "5"),
        ("deviceWritesEnabled", "true"),
    ]


async def test_0022_maps_status_to_archived_and_offers_every_live_set_to_the_matcher(
    db: Database, tmp_path: Path
) -> None:
    """The upgrade keeps a database matching exactly what it matched before.

    A database on 0021 filed a shot under the one active Set and, when
    `shotsProfileAutomatch` was on, under any non-archived Set that brewed its
    profile. The flag now says which Sets the matcher may use, so every
    non-archived Set carries it: keeping only the old active one would silently
    stop the others collecting.
    """
    await _migrate_below(db, tmp_path, "0022")
    await db.execute("INSERT INTO beans (name, created_at) VALUES ('Guji', 'x')")
    await db.execute(
        "INSERT INTO sets (name, bean_id, status, active, created_at) "
        "VALUES ('was the active one', 1, 'active', 1, '2026-01-01')"
    )
    await db.execute(
        "INSERT INTO sets (name, bean_id, status, active, created_at) "
        "VALUES ('the other bag', 1, 'active', 0, '2026-02-01')"
    )
    await db.execute(
        "INSERT INTO sets (name, bean_id, status, active, created_at) "
        "VALUES ('finished with', 1, 'archived', 0, '2026-03-01')"
    )
    await db.execute("INSERT INTO settings (key, value) VALUES ('shotsProfileAutomatch', 'true')")
    await db.execute("INSERT INTO settings (key, value) VALUES ('deviceWritesEnabled', 'true')")

    assert "0022" in await run_migrations(db)

    rows = await db.fetch_all("SELECT name, archived, automatch FROM sets ORDER BY created_at")
    assert [(r["name"], r["archived"], r["automatch"]) for r in rows] == [
        ("was the active one", 0, 1),
        ("the other bag", 0, 1),
        ("finished with", 1, 0),
    ]

    # Two candidates at once is the whole point: the index that refused a second
    # one is gone.
    await db.execute("UPDATE sets SET automatch = 1 WHERE archived = 0")
    assert await db.fetch_value("SELECT count(*) FROM sets WHERE automatch = 1") == 2

    # The switch the flag replaced does not linger, and nothing else was touched.
    keys = await db.fetch_all("SELECT key FROM settings ORDER BY key")
    assert [str(r["key"]) for r in keys] == ["deviceWritesEnabled"]

    # The view the SQL tool reads serves the two columns by their new names.
    view = await db.fetch_all("SELECT set_id, name, archived, automatch FROM v_sets")
    assert len(view) == 3
    columns = {str(r["name"]) for r in await db.fetch_all("PRAGMA table_info(sets)")}
    assert "status" not in columns and "active" not in columns
    assert await db.fetch_all("PRAGMA foreign_key_check") == []


async def test_0026_carries_finished_analyses_into_reviews_and_drops_the_rest(
    env: EnvSettings, tmp_path: Path
) -> None:
    """An archive from before Review, booted with Review: what is carried, what goes.

    Built with every migration below 0026 holding a finished analysis with
    suggestions (one of them accepted into a Set version), a failed analysis,
    an insight the finished one proposed, a profile draft made from it, stored
    overrides for both analysis settings and a person's edit of the analysis
    prompt. Then the real app boots on it.
    """
    database = Database(env.database_path)
    await database.connect()
    try:
        await _migrate_below(database, tmp_path, "0026")
        execute = database.execute
        await execute("INSERT INTO beans (name, created_at) VALUES ('Guji', 'x')")
        await execute("INSERT INTO sets (name, bean_id, created_at) VALUES ('S', 1, 'x')")
        await execute(
            "INSERT INTO set_versions (set_id, version_no, intent, created_at) "
            "VALUES (1, 1, 'baseline', 'x')"
        )
        await execute(
            "INSERT INTO shots (device_id, raw_slog, set_version_id, synced_at, updated_at) "
            "VALUES ('000001', x'00', 1, 'x', 'x')"
        )
        output = {
            "shot_style": "classic",
            "execution": {"summary": "Clean.", "issues": []},
            "taste_prediction": {"balance": "bitter", "body": "heavy", "confidence": "high"},
            "diagnosis": "Ran long: 38 s against a 28 s target.",
            "suggestions": [],
            "profile_patch": [],
            "questions_for_user": [],
            "rules_used": ["hierarchy"],
            "excerpts_used": ["DOC#a"],
            "proposed_insights": [],
        }
        await execute(
            "INSERT INTO shot_analyses (id, shot_id, set_version_id, provider, model, "
            "prompt_name, prompt_version, input_json, output_json, usage_json, status, "
            "llm_call_id, created_at, finished_at) VALUES (7, 1, 1, 'anthropic', 'careful', "
            "'analysis', 'v1+v2', '{\"judgement\": \"secret\"}', ?, '{\"total_tokens\": 9}', 'ok', "
            "'call-7', '2026-09-01T08:00:00.000Z', '2026-09-01T08:01:00.000Z')",
            (json.dumps(output),),
        )
        # Failed, yet carrying a document (a reply that failed validation can
        # leave one): the status is what keeps it from being carried.
        await execute(
            "INSERT INTO shot_analyses (id, shot_id, status, error, output_json) "
            "VALUES (8, 1, 'failed', 'invalid_output: bad reply', ?)",
            (json.dumps(output),),
        )
        # Interrupted after a document was written (a hand edit, a crash at the
        # wrong moment): still not a finished analysis, so still not carried.
        await execute(
            "INSERT INTO shot_analyses (id, shot_id, status, output_json) "
            "VALUES (9, 1, 'interrupted', ?)",
            (json.dumps(output),),
        )
        await execute(
            "INSERT INTO set_versions (set_id, version_no, parent_version_id, origin, "
            "origin_analysis_id, grind_value, created_at) VALUES (1, 2, 1, 'analysis', 7, 20, 'y')"
        )
        await execute(
            "INSERT INTO suggestions (analysis_id, variable, direction, reason, status, "
            "resulting_set_version_id) VALUES (7, 'grind', 'finer', 'slow', 'accepted', 2)"
        )
        await execute(
            "INSERT INTO suggestions (analysis_id, variable, direction, reason) "
            "VALUES (7, 'yield', 'increase', 'backup')"
        )
        await execute(
            "INSERT INTO knowledge_insights (scope_json, text, source, analysis_id, confirmed) "
            "VALUES ('{\"bean_id\": 1}', 'this bag runs long', 'analysis', 7, 1)"
        )
        await execute(
            "INSERT INTO profile_versions (content_hash, label, type, json, created_at) "
            "VALUES ('abc', '9 Bar', 'pro', '{}', 'x')"
        )
        await execute(
            "INSERT INTO profile_drafts (base_version_id, source_analysis_id, "
            "source_suggestion_id, change_summary, created_at, updated_at) "
            "VALUES (1, 7, 1, 'from the analysis', 'x', 'x')"
        )
        await execute("INSERT INTO settings (key, value) VALUES ('modelAnalysis', '\"careful\"')")
        await execute(
            "INSERT INTO settings (key, value) VALUES ('analysisChunkTokenBudget', '900')"
        )
        await execute(
            "INSERT INTO prompts (name, content, default_content) "
            "VALUES ('analysis', 'my edited analysis prompt', 'the shipped one')"
        )
    finally:
        await database.close()

    async with running_app(env) as (app, client):
        db = app.state.db

        # The finished analysis is a review with the same id; the failed and the
        # interrupted ones are gone.
        reviews = await db.fetch_all("SELECT * FROM shot_reviews ORDER BY id")
        assert len(reviews) == 1
        review = dict(reviews[0])
        assert review["id"] == 7
        assert review["shot_id"] == 1
        assert review["status"] == "ok"
        assert (review["taste_balance"], review["taste_body"], review["taste_confidence"]) == (
            "bitter",
            "heavy",
            "high",
        )
        assert review["description"] == "Ran long: 38 s against a 28 s target."
        assert review["summary"] == ""
        assert json.loads(review["rules_used_json"]) == ["hierarchy"]
        assert json.loads(review["excerpts_used_json"]) == ["DOC#a"]
        assert (review["model"], review["provider"], review["llm_call_id"]) == (
            "careful",
            "anthropic",
            "call-7",
        )
        assert review["finished_at"] == "2026-09-01T08:01:00.000Z"

        # Served through the routes, as the shot page reads it.
        served = (await client.get("/api/shots/1")).json()["data"]["reviews"]
        assert [row["id"] for row in served] == [7]
        assert served[0]["summary"] == ""

        # The analysis tables, their views and the prompts are gone.
        names = {
            str(row["name"])
            for row in await db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        assert not names & {"shot_analyses", "suggestions", "v_analyses", "v_suggestions"}
        assert "v_reviews" in names
        prompts = {str(r["name"]) for r in await db.fetch_all("SELECT name FROM prompts")}
        assert not prompts & {"analysis", "analysis-user"}
        assert {"review", "review-user"} <= prompts

        # The rows that pointed at the analysis still read correctly.
        version = await db.fetch_one(
            "SELECT origin, origin_analysis_id FROM set_versions WHERE version_no = 2"
        )
        assert (version["origin"], version["origin_analysis_id"]) == ("analysis", 7)
        insight = await db.fetch_one(
            "SELECT text, source, analysis_id, confirmed FROM knowledge_insights"
        )
        assert tuple(insight) == ("this bag runs long", "analysis", 7, 1)
        listed = (await client.get("/api/knowledge/insights")).json()["data"]["items"]
        assert [(row["text"], row["source"]) for row in listed] == [
            ("this bag runs long", "analysis")
        ]
        draft = await db.fetch_one(
            "SELECT source_analysis_id, source_suggestion_id FROM profile_drafts"
        )
        assert tuple(draft) == (7, 1)

        # The settings moved to their new names; the old keys are gone.
        settings = {
            str(r["key"]): str(r["value"])
            for r in await db.fetch_all("SELECT key, value FROM settings")
        }
        assert settings.get("modelReview") == '"careful"'
        assert settings.get("knowledgeChunkTokenBudget") == "900"
        assert "modelAnalysis" not in settings and "analysisChunkTokenBudget" not in settings

        assert await db.fetch_all("PRAGMA foreign_key_check") == []

    # The SQL tool reads the new view.
    result = await run_query(
        env.database_path,
        "SELECT review_id, shot_id, taste_balance, description FROM v_reviews",
    )
    assert result.rows == [[7, 1, "bitter", "Ran long: 38 s against a 28 s target."]]
    with pytest.raises(SqlRefused):
        await run_query(env.database_path, "SELECT input_json FROM shot_reviews")


async def test_0027_names_every_existing_version_by_its_number(
    db: Database, tmp_path: Path
) -> None:
    """Existing versions keep their numbers: v3 is still v3, now as 3.0.

    Names written before the upgrade — in chats, predictions, outcome notes —
    must still point at the same version, so nothing is renumbered. The views
    the SQL tool reads carry the name beside the ordinal.
    """
    await _migrate_below(db, tmp_path, "0027")
    await db.execute("INSERT INTO beans (name, created_at) VALUES ('Guji', 'x')")
    await db.execute("INSERT INTO sets (name, bean_id, created_at) VALUES ('Guji', 1, 'x')")
    for number in (1, 2, 3):
        await db.execute(
            "INSERT INTO set_versions (set_id, version_no, parent_version_id, "
            "compares_to_version_id, prediction, created_at) VALUES (1, ?, ?, ?, ?, 'x')",
            (number, number - 1 or None, number - 1 or None, "longer" if number > 1 else ""),
        )
    await db.execute(
        "INSERT INTO shots (device_id, raw_slog, set_version_id, synced_at, updated_at) "
        "VALUES ('000001', x'00', 3, 'x', 'x')"
    )

    assert "0027" in await run_migrations(db)

    rows = await db.fetch_all(
        "SELECT version_no, version_major, version_minor FROM set_versions ORDER BY version_no"
    )
    assert [tuple(r) for r in rows] == [(1, 1, 0), (2, 2, 0), (3, 3, 0)]
    view = await db.fetch_all(
        "SELECT version_no, version_major, version_minor, version_label, "
        "compares_to_version_label FROM v_set_versions ORDER BY version_no"
    )
    assert [tuple(r) for r in view] == [
        (1, 1, 0, "v1", None),
        (2, 2, 0, "v2", "v1"),
        (3, 3, 0, "v3", "v2"),
    ]
    shot = await db.fetch_one(
        "SELECT set_version_no, set_version_major, set_version_minor, set_version_label "
        "FROM v_shots"
    )
    assert shot is not None
    assert tuple(shot) == (3, 3, 0, "v3")
    # The suggestion columns arrive empty on existing rows.
    proposal_columns = {
        str(r["name"]) for r in await db.fetch_all("PRAGMA table_info(set_version_proposals)")
    }
    draft_columns = {
        str(r["name"]) for r in await db.fetch_all("PRAGMA table_info(profile_drafts)")
    }
    assert {"suggest_major", "major_reason"} <= proposal_columns & draft_columns
    assert await db.fetch_all("PRAGMA foreign_key_check") == []

    # And the repository numbers the next one from there.
    from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch

    added = await SetsRepository(db).add_version(1, SetVersionPatch(grind_setting="21"))
    assert added is not None
    assert (added.version_no, added.version_label) == (4, "v3.1")


async def test_0029_keeps_every_shot_and_everything_that_hangs_off_it(
    db: Database, tmp_path: Path
) -> None:
    """Widening the shot key rebuilds `shots`, and four tables cascade off it.

    A rebuild done the obvious way (`DROP TABLE shots`) fires the cascade and
    silently empties samples, notes cards, judgements and reviews, which is the
    whole archive. So the archive here has one of each on more than one shot,
    every shot column set to something other than its default, a high-water
    mark above the highest surviving id, and the five views the SQL tool reads.
    """
    await _migrate_below(db, tmp_path, "0029")
    await db.execute("INSERT INTO beans (name, created_at) VALUES ('Guji', 'x')")
    await db.execute("INSERT INTO sets (name, bean_id, created_at) VALUES ('S', 1, 'x')")
    await db.execute(
        "INSERT INTO set_versions (set_id, version_no, intent, created_at) "
        "VALUES (1, 1, 'baseline', 'x')"
    )
    for number in range(1, 5):
        await db.execute(
            """
            INSERT INTO shots (
                device_id, set_version_id, started_at, start_epoch, duration_ms,
                profile_id_on_device, profile_name_on_device, final_weight_g,
                final_exit_reason, brew_delay_ms, slog_version, sample_interval_ms,
                fields_mask, sample_count, scale_connected, incomplete, source,
                deleted_on_device, quarantined, quarantine_reason, raw_slog,
                phases_json, diagnostics_json, execution_score, execution_reason,
                derivation_version, index_rating, index_volume_g, index_avg_temp_c,
                index_max_pressure_bar, index_avg_flow_ml_s, index_flags,
                synced_at, updated_at)
            VALUES (?, 1, '2026-01-01T00:00:00Z', ?, 28000, 'p', 'Profile', 36.5, 1, 800, 7,
                    250, 16383, 2, 1, 1, 'device', 1, 0, 'q', ?, '[]', '{"a":1}', 0.75,
                    'why', 2, 4, 37.0, 93.5, 9.1, 1.8, 3, 'synced', 'updated')
            """,
            (f"{number:06d}", 1_760_000_000 + number, bytes([number, 0, 255])),
        )
        await db.execute(
            "INSERT INTO shot_samples (shot_id, t_ms, ct, cp) VALUES (?, 0, 92.5, 1.5), "
            "(?, 250, 92.7, 2.5)",
            (number, number),
        )
    for number in (1, 2):
        await db.execute(
            "INSERT INTO shot_judgements (shot_id, rating, notes) VALUES (?, 4, 'good')",
            (number,),
        )
        await db.execute(
            "INSERT INTO device_shot_notes (shot_id, raw_json, rating, notes, fetched_at) "
            "VALUES (?, '{}', 3, 'card', 'x')",
            (number,),
        )
    await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (2, 'ok'), (2, 'failed')")
    await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (3, 'ok')")
    await db.execute(
        "INSERT INTO sync_events (run_id, at, kind, shot_id) VALUES (NULL, 'x', 'k', 4)"
    )
    # A shot that has been deleted since: its id must never be handed out again.
    await db.execute(
        "INSERT INTO shots (device_id, raw_slog, synced_at, updated_at) "
        "VALUES ('000099', x'00', 'x', 'x')"
    )
    await db.execute("DELETE FROM shots WHERE device_id = '000099'")

    async def everything() -> dict[str, list[list[object]]]:
        out: dict[str, list[list[object]]] = {}
        for table, order in (
            ("shots", "id"),
            ("shot_samples", "shot_id, t_ms"),
            ("shot_judgements", "shot_id"),
            ("device_shot_notes", "shot_id"),
            ("shot_reviews", "id"),
            ("sync_events", "id"),
        ):
            columns = sorted(
                str(r["name"]) for r in await db.fetch_all(f"PRAGMA table_info({table})")
            )
            rows = await db.fetch_all(
                f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order}"  # noqa: S608
            )
            out[table] = [list(r) for r in rows]
        return out

    views = "'v_shots', 'v_sets', 'v_set_versions', 'v_judgements', 'v_profiles'"
    view_sql = (
        f"SELECT name, sql FROM sqlite_master WHERE type = 'view' AND name IN ({views}) "  # noqa: S608
        "ORDER BY name"
    )
    before = await everything()
    views_before = [tuple(r) for r in await db.fetch_all(view_sql)]
    indexes_before = {
        str(r["name"])
        for r in await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'shots' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert len(before["shots"]) == 4 and len(before["shot_samples"]) == 8
    assert len(before["shot_reviews"]) == 3

    assert "0029" in await run_migrations(db)

    assert await everything() == before
    # 0035 later narrows v_profiles with a WHERE; everything before that clause is 0029's text.
    views_after = [
        (name, text.split("\n WHERE NOT (")[0]) for name, text in await db.fetch_all(view_sql)
    ]
    assert views_after == views_before
    assert await db.fetch_all("PRAGMA foreign_key_check") == []
    assert indexes_before == {
        str(r["name"])
        for r in await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'shots' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }
    # The cascade still hangs off the rebuilt table.
    await db.execute("DELETE FROM shots WHERE id = 2")
    assert await db.fetch_value("SELECT COUNT(*) FROM shot_samples WHERE shot_id = 2") == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM shot_reviews WHERE shot_id = 2") == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM shot_judgements WHERE shot_id = 2") == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM device_shot_notes WHERE shot_id = 2") == 0
    # The high-water mark survived, and so did the reviews' own.
    await db.execute(
        "INSERT INTO shots (device_id, raw_slog, synced_at, updated_at) "
        "VALUES ('000500', x'00', 'x', 'x')"
    )
    assert await db.fetch_value("SELECT MAX(id) FROM shots") == 6  # 5 was the deleted one
    await db.execute("INSERT INTO shot_reviews (shot_id) VALUES (1)")
    assert await db.fetch_value("SELECT MAX(id) FROM shot_reviews") == 4


async def test_0029_lets_two_shots_share_a_number_and_nothing_else(
    db: Database, tmp_path: Path
) -> None:
    """The key is the number and the start time together."""
    await run_migrations(db)
    insert = (
        "INSERT INTO shots (device_id, start_epoch, raw_slog, synced_at, updated_at) "
        "VALUES (?, ?, x'00', 'x', 'x')"
    )
    await db.execute(insert, ("000001", 1_760_000_000))
    await db.execute(insert, ("000001", 1_770_000_000))
    with pytest.raises(Exception, match="UNIQUE"):
        await db.execute(insert, ("000001", 1_770_000_000))
    assert await db.fetch_value("SELECT COUNT(*) FROM shots WHERE device_id = '000001'") == 2
