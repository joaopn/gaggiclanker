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
        ("ALTER TABLE sets ADD COLUMN extra TEXT", "unexpected column sets.extra"),
        ("ALTER TABLE sets DROP COLUMN design_brief", "missing column sets.design_brief"),
        ("DROP INDEX idx_sets_bean", "missing index idx_sets_bean"),
        ("CREATE INDEX idx_extra ON sets(name)", "unexpected index idx_extra"),
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
_ONE_FACT = [
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
        "CREATE UNIQUE INDEX idx_set_versions_name",
        "CREATE INDEX idx_set_versions_name",
        "index idx_set_versions_name",
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


async def test_the_legacy_ledger_is_dropped(db: Database) -> None:
    await schema.create_schema(db)
    await db.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY) STRICT")
    await db.execute("INSERT INTO schema_migrations VALUES ('0050')")
    await schema.ensure_schema(db)
    assert (
        await db.fetch_value("SELECT COUNT(*) FROM sqlite_master WHERE name = 'schema_migrations'")
        == 0
    )


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
    await db.execute("ALTER TABLE sets ADD COLUMN surprise TEXT")
    await db.close()
    path = data_dir / "test.db"
    before = path.read_bytes()
    with pytest.raises(schema.SchemaMismatch) as caught:
        schema.check_database_file(path)
    assert "unexpected column sets.surprise" in caught.value.differences
    assert str(path) in str(caught.value)
    assert "Delete it to start fresh" in str(caught.value)
    assert path.read_bytes() == before
    assert sorted(p.name for p in data_dir.glob("test.db*")) == ["test.db"]
