"""The schema file: what it builds, and the comparison that guards an existing database."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db import schema
from gaggiclanker.db.connection import Database


@pytest.fixture
async def db(data_dir: Path) -> AsyncIterator[Database]:
    database = Database(data_dir / "test.db")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


def _memory_database(sql: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql if sql is not None else schema.schema_sql())
    return conn


def _tables(conn: sqlite3.Connection) -> list[str]:
    return sorted(
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name <> 'schema_migrations'"
        )
    )


# ── the comparison ──────────────────────────────────────────────────────


def test_a_comment_or_layout_edit_changes_nothing() -> None:
    text = schema.schema_sql()
    edited = re.sub(r"\n\s*--[^\n]*", "", text)  # every comment line gone
    edited = edited.replace("    ", "\t").replace(" NOT NULL", "  NOT   NULL")
    edited = "-- a new first line\n" + edited + "\n/* and a last one */\n"
    assert edited != text
    conn = _memory_database(edited)
    try:
        assert schema.schema_differences(conn) == []
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("ALTER TABLE recipes ADD COLUMN extra TEXT", "unexpected column recipes.extra"),
        ("ALTER TABLE recipes DROP COLUMN design_brief", "missing column recipes.design_brief"),
        ("DROP INDEX idx_recipes_bean", "missing index idx_recipes_bean"),
        ("CREATE INDEX idx_extra ON recipes(name)", "unexpected index idx_extra"),
        ("DROP VIEW v_judgements", "missing view v_judgements"),
        ("DROP TRIGGER knowledge_chunks_ai", "missing trigger knowledge_chunks_ai"),
        ("DROP TABLE chat_events", "missing table chat_events"),
    ],
)
def test_a_structural_difference_is_named(statement: str, expected: str) -> None:
    conn = _memory_database()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(statement)
        found = schema.schema_differences(conn)
    finally:
        conn.close()
    assert expected in found


def test_a_changed_view_a_changed_default_and_a_changed_check_are_caught() -> None:
    text = schema.schema_sql()
    cases = {
        "view": text.replace("WHERE c.status <> 'rejected'", "WHERE c.status = 'confirmed'"),
        "default": text.replace(
            "design_brief TEXT NOT NULL DEFAULT '{}'", "design_brief TEXT NOT NULL DEFAULT '[]'"
        ),
        "check": text.replace("CHECK (kind IN ('claim', 'free_text', 'prediction'))", ""),
        "foreign key": text.replace(
            "bean_id    INTEGER NOT NULL REFERENCES beans(id),",
            "bean_id    INTEGER NOT NULL REFERENCES beans(id) ON DELETE CASCADE,",
        ),
    }
    for name, changed in cases.items():
        assert changed != text, name
        conn = _memory_database(changed)
        try:
            assert schema.schema_differences(conn), name
        finally:
            conn.close()


_TRIGGER = (
    "AFTER INSERT ON knowledge_chunks BEGIN\n"
    "    INSERT INTO knowledge_chunks_fts(rowid, heading, body)\n"
    "    VALUES (new.id, new.%s, new.%s);"
)


def _schema_level() -> int:
    """The level ``schema.sql`` ends at, read from the file so no test names a number."""
    match = re.search(r"^PRAGMA user_version = (\d+);$", schema.schema_sql(), re.MULTILINE)
    assert match is not None
    return int(match.group(1))


LEVEL = _schema_level()

_ONE_FACT = [
    (
        "the upgrade level",
        f"PRAGMA user_version = {LEVEL};",
        f"PRAGMA user_version = {LEVEL + 1};",
        "user_version",
    ),
    (
        "a column type",
        "duration_ms             INTEGER NOT NULL",
        "duration_ms             REAL NOT NULL",
        "column shots.duration_ms",
    ),
    (
        "a not-null",
        "profile_id_on_device    TEXT    NOT NULL DEFAULT ''",
        "profile_id_on_device    TEXT    DEFAULT ''",
        "column shots.profile_id_on_device",
    ),
    (
        "a partial index condition",
        "ON pattern_runs(status) WHERE status = 'running'",
        "ON pattern_runs(status) WHERE status = 'queued'",
        "index idx_pattern_runs_running",
    ),
    (
        "a trigger body",
        _TRIGGER % ("heading", "body"),
        _TRIGGER % ("body", "heading"),
        "trigger knowledge_chunks_ai",
    ),
    (
        "index uniqueness",
        "CREATE UNIQUE INDEX idx_recipe_versions_name",
        "CREATE INDEX idx_recipe_versions_name",
        "index idx_recipe_versions_name",
    ),
    (
        "an index column order",
        "idx_sync_runs_kind ON sync_runs(kind, started_at DESC)",
        "idx_sync_runs_kind ON sync_runs(kind, started_at)",
        "index idx_sync_runs_kind",
    ),
]


@pytest.mark.parametrize(("what", "old", "new", "key"), _ONE_FACT, ids=[c[0] for c in _ONE_FACT])
def test_each_structural_fact_is_compared_on_its_own(
    what: str, old: str, new: str, key: str
) -> None:
    text = schema.schema_sql()
    assert text.count(old) == 1, what
    conn = _memory_database(text.replace(old, new))
    try:
        found = schema.schema_differences(conn)
    finally:
        conn.close()
    assert [d for d in found if d.startswith(f"{key} differs")], (what, found)
    # And the fingerprint follows it.
    changed = _memory_database(text.replace(old, new))
    try:
        assert schema.fingerprint_of(schema.describe(changed)) != schema.schema_fingerprint()
    finally:
        changed.close()


def test_the_description_names_each_fact() -> None:
    """The facts themselves, so dropping one from the description fails here."""
    conn = _memory_database()
    try:
        described = schema.describe(conn)
    finally:
        conn.close()
    column = described["column shots.profile_id_on_device"]
    assert "type TEXT" in column and "not null 1" in column and "default ''" in column
    running = described["index idx_pattern_runs_running"]
    assert (
        "unique 1" in running and "partial 1" in running and "WHERE status = 'running'" in running
    )
    assert "started_at desc" in described["index idx_sync_runs_kind"]
    assert "unique 0" in described["index idx_sync_runs_kind"]
    assert "new.heading,new.body" in described["trigger knowledge_chunks_ai"]


def test_the_level_is_described_and_a_database_at_another_level_differs() -> None:
    conn = _memory_database()
    try:
        assert schema.describe(conn)["user_version"] == str(LEVEL)
        conn.execute(f"PRAGMA user_version = {LEVEL + 3}")
        found = schema.schema_differences(conn)
    finally:
        conn.close()
    assert found == [f"user_version differs (database: {LEVEL + 3}; expected: {LEVEL})"]


async def test_the_level_is_the_same_on_every_kind_of_connection(
    db: Database, data_dir: Path
) -> None:
    """A new database, a read-only open (the boot's check, a staged restore) and a copy
    made by VACUUM INTO (a backup) all read the level the file holds."""
    await schema.create_schema(db)
    other = LEVEL + 4
    await db.execute(f"PRAGMA user_version = {other}")
    assert await db.fetch_value("PRAGMA user_version") == other
    await db.execute(f"VACUUM INTO '{data_dir / 'copy.db'}'")
    await db.close()
    for name in ("test.db", "copy.db"):
        readonly = sqlite3.connect(f"file:{data_dir / name}?mode=ro", uri=True)
        try:
            assert schema.describe(readonly)["user_version"] == str(other), name
        finally:
            readonly.close()


def test_the_ledger_and_a_backup_manifest_are_not_schema() -> None:
    conn = _memory_database()
    try:
        conn.execute("CREATE TABLE schema_migrations (version TEXT)")
        conn.execute("CREATE TABLE backup_manifest (format_version INTEGER)")
        assert schema.schema_differences(conn) == []
    finally:
        conn.close()


def test_the_fingerprint_is_stable_and_follows_the_structure() -> None:
    assert schema.schema_fingerprint() == schema.schema_fingerprint()
    base = {"table a": "x"}
    assert schema.fingerprint_of(base) != schema.fingerprint_of({**base, "column a.b": "y"})
    assert schema.fingerprint_of(base) != schema.fingerprint_of({"table a": "z"})
    assert re.fullmatch(r"[0-9a-f]{12}", schema.schema_fingerprint())


# ── a new database ──────────────────────────────────────────────────────


async def test_a_new_database_gets_the_schema_and_its_seed_rows(db: Database) -> None:
    assert await schema.ensure_schema(db) is True
    assert await db.fetch_value("SELECT COUNT(*) FROM machines") == 1
    assert await db.fetch_value("SELECT host FROM machines WHERE id = 1") == ""
    assert await db.fetch_value("SELECT COUNT(*) FROM flavor_picks") == 22
    assert await db.fetch_all("PRAGMA foreign_key_check") == []


async def test_the_schema_is_applied_in_one_transaction(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = schema.schema_sql() + "\nCREATE TABLE settings (again INTEGER);\n"
    monkeypatch.setattr(schema, "schema_sql", lambda: broken)
    with pytest.raises(sqlite3.Error):
        await schema.create_schema(db)
    assert await db.fetch_value("SELECT COUNT(*) FROM sqlite_master") == 0


async def test_ensure_schema_on_a_database_that_has_it_changes_nothing(db: Database) -> None:
    await schema.create_schema(db)
    await db.execute("UPDATE machines SET host = 'kept' WHERE id = 1")
    assert await schema.ensure_schema(db) is False
    assert await db.fetch_value("SELECT host FROM machines") == "kept"


# ── an existing database file ───────────────────────────────────────────


async def test_a_matching_file_a_missing_file_and_an_empty_file_pass(
    db: Database, data_dir: Path
) -> None:
    await schema.create_schema(db)
    await db.close()
    schema.check_database_file(data_dir / "test.db")
    schema.check_database_file(data_dir / "absent.db")
    (data_dir / "empty.db").write_bytes(b"")
    schema.check_database_file(data_dir / "empty.db")
    sqlite3.connect(data_dir / "blank.db").close()
    schema.check_database_file(data_dir / "blank.db")


async def test_a_file_with_another_schema_is_refused_and_left_alone(
    db: Database, data_dir: Path
) -> None:
    await schema.create_schema(db)
    await db.execute("ALTER TABLE recipes ADD COLUMN surprise TEXT")
    await db.close()
    path = data_dir / "test.db"
    before = path.read_bytes()
    with pytest.raises(schema.SchemaMismatch) as caught:
        schema.check_database_file(path)
    assert "unexpected column recipes.surprise" in caught.value.differences
    assert str(path) in str(caught.value)
    assert "Delete it to start fresh" in str(caught.value)
    assert path.read_bytes() == before
    assert sorted(p.name for p in data_dir.glob("test.db*")) == ["test.db"]


# ── a quoted plain name reads as the bare one ────────────────────────────────


def test_a_quoted_plain_table_name_equals_the_bare_one() -> None:
    """``ALTER TABLE ... RENAME TO`` writes ``"recipes"`` into every reference to the table."""
    assert schema.normalize_sql('REFERENCES "recipes"(id)') == schema.normalize_sql(
        "REFERENCES recipes(id)"
    )
    assert schema.normalize_sql('CREATE TABLE t (a INTEGER REFERENCES "recipes" (id))') == (
        schema.normalize_sql("CREATE TABLE t (a INTEGER REFERENCES recipes(id))")
    )


def test_a_double_quoted_text_inside_a_string_literal_is_data_and_untouched() -> None:
    assert schema.normalize_sql("x = '\"x\"'") == "x = '\"x\"'"
    assert schema.normalize_sql("DEFAULT '\"recipes\"'") == "DEFAULT '\"recipes\"'"


def test_a_quoted_name_that_is_not_a_plain_name_stays_quoted() -> None:
    assert schema.normalize_sql('SELECT "a b" FROM t') == 'SELECT "a b" FROM t'
    assert schema.normalize_sql('SELECT "1x", "a-b" FROM t') == 'SELECT "1x","a-b" FROM t'
    assert schema.normalize_sql('"a b"') != schema.normalize_sql("a b")


def test_the_schema_file_uses_no_double_quoted_string_as_a_value() -> None:
    """The unquoting is only sound for SQL that SQLite reads the same way: names, not values."""
    sql = schema.schema_sql()
    without_comments = re.sub(r"--[^\n]*", "", sql)
    without_literals = re.sub(r"'(?:[^']|'')*'", "''", without_comments)
    assert '"' not in without_literals


def test_a_renamed_table_equals_the_schema_file_despite_the_quotes_sqlite_wrote() -> None:
    """The stored text of a table that refers to a renamed one has the new name quoted."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            "CREATE TABLE old_parent (id INTEGER PRIMARY KEY);"
            "CREATE TABLE child (id INTEGER PRIMARY KEY, p INTEGER REFERENCES old_parent(id));"
            "ALTER TABLE old_parent RENAME TO parent;"
        )
        stored = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'child'").fetchone()[0]
        assert '"parent"' in stored
        fresh = "CREATE TABLE child (id INTEGER PRIMARY KEY, p INTEGER REFERENCES parent(id))"
        assert schema.normalize_sql(stored) == schema.normalize_sql(fresh)
    finally:
        conn.close()
