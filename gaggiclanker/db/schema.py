"""The database schema: one file, one comparison.

``db/schema.sql`` holds the whole schema and the few rows a new database starts with. A
database that does not exist yet is made by running it once, in one transaction. A
database that does exist is **compared** with what the file builds, and the app starts
on it only when the two have the same structure; otherwise it refuses to start and
never writes or deletes the database or its `-wal` (see :class:`SchemaMismatch` and
:func:`check_database_file`). A schema change is an edit to the file,
with a breaking note in the changelog telling the person to delete their database.

**The comparison is of structure, not of text.** Both sides are described the same way:
every table with its columns (position, type, not-null, default, primary key, hidden or
generated) and its definition with comments and layout removed, every index (columns,
uniqueness, partial ``WHERE``), every foreign key, and every view and trigger by its
normalised SQL. Editing a comment or the layout of ``schema.sql`` therefore never
refuses a database. Rows are not compared. The same function decides whether an
uploaded backup may be restored (``db/restore.py``), so what a boot would refuse, a
restore refuses.

Two tables are left out of the description because they are not part of the schema: the
``schema_migrations`` ledger an earlier version of the app kept (dropped at the first
boot that finds it, once the rest has matched), and the ``backup_manifest`` a downloaded
backup carries.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from functools import cache
from pathlib import Path

import structlog

from gaggiclanker.db.connection import Database

__all__ = [
    "SCHEMA_PATH",
    "SchemaMismatch",
    "check_database_file",
    "create_schema",
    "describe",
    "differences",
    "ensure_schema",
    "fingerprint_of",
    "normalize_sql",
    "refusal_message",
    "schema_differences",
    "schema_fingerprint",
    "schema_sql",
]

log = structlog.get_logger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

#: Tables that are not part of the schema: the legacy ledger and a backup's manifest.
_NOT_SCHEMA = frozenset({"schema_migrations", "backup_manifest"})

#: How many differences a refusal names in its log event.
_REPORTED = 5

#: Longest stretch of a definition quoted in one difference.
_QUOTED = 160


class SchemaMismatch(RuntimeError):
    """The database's structure is not the one ``schema.sql`` builds."""

    def __init__(self, path: Path, found: list[str]) -> None:
        self.path = path
        self.differences = found
        super().__init__(refusal_message(path))


def refusal_message(path: Path) -> str:
    """The one plain line a refused boot prints."""
    return (
        f"The database at {path} was made by a different version of gaggiclanker. "
        "Delete it to start fresh (a backup can be restored only into the version "
        "that made it)."
    )


def schema_sql() -> str:
    """The text of ``schema.sql``."""
    return SCHEMA_PATH.read_text(encoding="utf-8")


# Punctuation a space next to means nothing: "a(b, c)" and "a ( b,c )" are the same SQL.
_TIGHT = frozenset("(),;")
_QUOTES = {"'": "'", '"': '"', "`": "`", "[": "]"}


def normalize_sql(sql: str) -> str:
    """The SQL with everything that does not change it removed.

    Comments (``--`` to the end of the line, ``/* ... */``) go, every run of whitespace
    becomes one space, and a space beside ``( ) , ;`` goes. String literals and quoted
    names are kept byte for byte, since a space inside ``'...'`` is data. Case is kept:
    telling a keyword from a name would be a parser.
    """
    out: list[str] = []
    space = False
    i, n = 0, len(sql)

    def emit(text: str) -> None:
        nonlocal space
        if space and out and out[-1][-1] not in _TIGHT and text[0] not in _TIGHT:
            out.append(" ")
        space = False
        out.append(text)

    while i < n:
        char = sql[i]
        if char in _QUOTES:
            close = _QUOTES[char]
            end = i + 1
            while end < n:
                if sql[end] == close:
                    # A doubled quote is an escaped one ('it''s'); a bracket has no escape.
                    if close != "]" and end + 1 < n and sql[end + 1] == close:
                        end += 2
                        continue
                    break
                end += 1
            emit(sql[i : end + 1])
            i = end + 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end
            space = True
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            space = True
        elif char.isspace():
            space = True
            i += 1
        else:
            emit(char)
            i += 1
    return "".join(out)


# `CREATE [VIRTUAL] TABLE [IF NOT EXISTS] name`: the name may be quoted after a rename.
_TABLE_HEAD = re.compile(
    r"^CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:\"[^\"]*\"|`[^`]*`|\[[^\]]*\]|\w+)\s*",
    re.IGNORECASE,
)


def _table_body(sql: str) -> str:
    """A table's definition without its name, so a quoted name and a bare one agree."""
    return normalize_sql(_TABLE_HEAD.sub("", sql, count=1))


def describe(conn: sqlite3.Connection) -> dict[str, str]:
    """Everything about a database's structure that must match, as ``kind name -> text``."""
    out: dict[str, str] = {}
    sql_of = {
        str(name): sql
        for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL")
    }
    for row in conn.execute("PRAGMA table_list"):
        schema, name, kind = str(row[0]), str(row[1]), str(row[2])
        if schema != "main" or name.startswith("sqlite_") or name in _NOT_SCHEMA:
            continue
        if kind == "shadow":
            continue  # a virtual table's storage; the virtual table is described
        if kind == "view":
            out[f"view {name}"] = normalize_sql(sql_of[name])
            continue
        out[f"table {name}"] = _table_body(sql_of[name])
        for col in conn.execute(f'PRAGMA table_xinfo("{name}")'):
            cid, cname, ctype, notnull, default, pk, hidden = col[:7]
            out[f"column {name}.{cname}"] = (
                f"position {cid}, type {ctype or '-'}, not null {notnull}, "
                f"default {default if default is not None else '-'}, primary key {pk}, "
                f"hidden {hidden}"
            )
        fks = [
            "|".join(str(v) for v in fk[:8])
            for fk in conn.execute(f'PRAGMA foreign_key_list("{name}")')
        ]
        if fks:
            out[f"foreign keys {name}"] = "; ".join(sorted(fks))
        for idx in conn.execute(f'PRAGMA index_list("{name}")'):
            iname, unique, origin, partial = str(idx[1]), idx[2], idx[3], idx[4]
            columns = ", ".join(
                f"{c[2]} {'desc' if c[3] else 'asc'} {c[4]}"
                for c in conn.execute(f'PRAGMA index_xinfo("{iname}")')
                if c[5]
            )
            text = f"on {name}, unique {unique}, origin {origin}, partial {partial}, ({columns})"
            if iname in sql_of:
                text += f", {normalize_sql(sql_of[iname])}"
            out[f"index {iname}"] = text
    for kind, name, sql in conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type = 'trigger'"
    ):
        out[f"{kind} {name}"] = normalize_sql(sql)
    return out


def _shorten(found: str, expected: str) -> tuple[str, str]:
    """Both texts cut to the stretch around where they first part."""
    at = next((i for i, (a, b) in enumerate(zip(found, expected, strict=False)) if a != b), 0)
    at = min(at, max(len(found), len(expected)) - 1) if found or expected else 0
    start = max(0, at - 30)

    def cut(text: str) -> str:
        end = start + _QUOTED
        return ("..." if start else "") + text[start:end] + ("..." if len(text) > end else "")

    return cut(found), cut(expected)


def differences(found: dict[str, str], expected: dict[str, str]) -> list[str]:
    """What differs between two descriptions, one plain line each; empty when they match."""
    out: list[str] = []
    for key in sorted(expected.keys() | found.keys()):
        if key not in found:
            out.append(f"missing {key}")
        elif key not in expected:
            out.append(f"unexpected {key}")
        elif found[key] != expected[key]:
            have, want = _shorten(found[key], expected[key])
            out.append(f"{key} differs (database: {have}; expected: {want})")
    return out


def fingerprint_of(description: dict[str, str]) -> str:
    """A short name for a described schema."""
    text = "\n".join(f"{key}\t{value}" for key, value in sorted(description.items()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def schema_fingerprint() -> str:
    """A short name for the schema this build creates: what a backup's manifest records."""
    return fingerprint_of(_expected())


@cache
def _expected() -> dict[str, str]:
    """What ``schema.sql`` builds, described once (it is built in memory)."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(schema_sql())
        return describe(conn)
    finally:
        conn.close()


def schema_differences(conn: sqlite3.Connection) -> list[str]:
    """How the database behind ``conn`` differs from ``schema.sql``; empty when it matches."""
    return differences(describe(conn), _expected())


def _is_empty(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0)


def check_database_file(path: Path) -> None:
    """Refuse (raise :class:`SchemaMismatch`) a database file made by a different schema.

    Read-only, before the application opens the file: the database file and its `-wal` are
    never written, whatever the answer, and nothing is converted or deleted. A file that is
    missing or has nothing in it is fine; it is about to be made. One side effect remains:
    SQLite's read-only open of a database that a crash left a write-ahead log for rebuilds
    its `-shm` index (the database, the `-wal` and the directory listing stay as they were).
    """
    if not path.exists() or path.stat().st_size == 0:
        return
    # A read-only open of a write-ahead-log database makes an empty -wal and -shm beside
    # it that it cannot remove again; the ones this check made are removed here. That cleanup
    # has a race, accepted on purpose: if another process opens the same database in the
    # instant between this check closing and the unlink, a sidecar that process is using could
    # be removed under it. Only sidecars this check created are removed, and a running app's
    # `-wal` and `-shm` already exist before the check starts, so they are never touched; the
    # window needs a process that opens a database nobody had open, in that instant. Skipping
    # the cleanup would leave stray files beside every refused database instead.
    sidecars = [Path(f"{path}-wal"), Path(f"{path}-shm")]
    present = [p.exists() for p in sidecars]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if _is_empty(conn):
            return
        found = schema_differences(conn)
    finally:
        conn.close()
        for sidecar, existed in zip(sidecars, present, strict=True):
            if not existed:
                sidecar.unlink(missing_ok=True)
    if found:
        log.error(
            "database_schema_differs",
            path=str(path),
            count=len(found),
            differences=found[:_REPORTED],
        )
        raise SchemaMismatch(path, found)


async def create_schema(db: Database) -> None:
    """Build the schema and its seed rows in an empty database, in one transaction."""
    try:
        await db.execute_script(f"BEGIN;\n{schema_sql()}\nCOMMIT;")
    except BaseException:
        try:
            await db.execute("ROLLBACK")
        except Exception:  # pragma: no cover - no transaction was open
            log.debug("schema_rollback_noop", exc_info=True)
        raise


async def ensure_schema(db: Database) -> bool:
    """Make the open database ready: build it when empty, else drop the old ledger.

    Returns whether the database was just created. The caller has already run
    :func:`check_database_file`, so a database that is not empty is known to match.
    """
    if await db.fetch_value("SELECT COUNT(*) FROM sqlite_master") == 0:
        await create_schema(db)
        log.info("schema_created")
        return True
    if await db.fetch_value(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ):
        await db.execute("DROP TABLE schema_migrations")
        log.info("schema_ledger_dropped")
    return False
