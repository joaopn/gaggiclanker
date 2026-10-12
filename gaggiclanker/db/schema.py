"""The database schema: one file, one comparison.

``db/schema.sql`` holds the whole schema and the few rows a new database starts with, and ends
with ``PRAGMA user_version = N``: the upgrade level, the number of steps in ``db/upgrades``. A
database that does not exist yet is made by running the file once, in one transaction. A
database that does exist is **compared** with what the file builds, level included, and the app
starts on it only when the two are the same. One that an older version made, in exactly the state
that version left it (:func:`classify` says ``upgradable``), is carried forward first by the
app's own start (``db/upgrade.py``: a backup, the steps in one transaction, a check of the
result). Anything else is refused: the app prints one line, logs what differs and never writes or
deletes the database or its `-wal` (see :class:`SchemaMismatch` and :func:`check_database_file`).
A schema change is an edit to the file together with the upgrade step that carries older
databases to it.

**The comparison is of structure, not of text.** Both sides are described the same way:
every table with its columns (position, type, not-null, default, primary key, hidden or
generated) and its definition with comments and layout removed, every index (columns,
uniqueness, partial ``WHERE``), every foreign key, every view and trigger by its
normalised SQL, and the upgrade level. Editing a comment or the layout of ``schema.sql``
therefore never refuses a database. Rows are not compared. The same function decides whether an
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
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.upgrades import STEPS, Step

__all__ = [
    "SCHEMA_PATH",
    "Case",
    "Inspection",
    "SchemaMismatch",
    "check_database_file",
    "classify",
    "create_schema",
    "describe",
    "describe_sql",
    "differences",
    "ensure_schema",
    "expected_description",
    "fingerprint_of",
    "inspect_database_file",
    "normalize_sql",
    "refusal_message",
    "schema_differences",
    "schema_fingerprint",
    "schema_sql",
    "upgrade_pending_message",
]

log = structlog.get_logger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

#: Tables that are not part of the schema: the legacy ledger and a backup's manifest.
_NOT_SCHEMA = frozenset({"schema_migrations", "backup_manifest"})

#: How many differences a refusal names in its log event.
_REPORTED = 5

#: Longest stretch of a definition quoted in one difference.
_QUOTED = 160


#: How a database file stands against this build (see :func:`classify`).
Case = Literal["missing", "current", "upgradable", "newer", "differs"]


class SchemaMismatch(RuntimeError):
    """The database cannot be opened by this build as it is.

    ``case`` says why: ``differs`` (another structure), ``newer`` (made by a later version) or
    ``upgradable`` (an older version's, which the app's own start upgrades; the offline
    commands refuse it).
    """

    def __init__(self, path: Path, found: list[str], case: Case = "differs") -> None:
        self.path = path
        self.differences = found
        self.case = case
        super().__init__(refusal_message(path, case))


def refusal_message(path: Path, case: Case = "differs") -> str:
    """The one plain line a refused start prints."""
    if case == "newer":
        return (
            f"The database at {path} was made by a newer version of gaggiclanker than this "
            "one. Start the newer version again, or restore a backup made by this one."
        )
    if case == "upgradable":
        return upgrade_pending_message()
    return (
        f"The database at {path} was made by a different version of gaggiclanker. "
        "Delete it to start fresh, or restore a backup made by this version."
    )


def upgrade_pending_message() -> str:
    """What the commands that never upgrade say about a database an older version made."""
    return (
        "This database is upgraded the next time the app starts; start the app once, "
        "then run this again."
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
    # The upgrade level is part of what a database is: the same tables at another level
    # are a database an upgrade step has not finished with (or one a newer version made).
    out: dict[str, str] = {"user_version": str(conn.execute("PRAGMA user_version").fetchone()[0])}
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


def describe_sql(sql: str) -> dict[str, str]:
    """What a schema script builds, described (it is run in an in-memory database)."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(sql)
        return describe(conn)
    finally:
        conn.close()


@cache
def _expected() -> dict[str, str]:
    """What ``schema.sql`` builds, described once."""
    return describe_sql(schema_sql())


def expected_description() -> dict[str, str]:
    """What ``schema.sql`` builds, described (a copy; the original is cached)."""
    return dict(_expected())


def schema_differences(
    conn: sqlite3.Connection, expected: dict[str, str] | None = None
) -> list[str]:
    """How the database behind ``conn`` differs from ``schema.sql``; empty when it matches.

    ``expected`` is for the upgrade tests, which compare with a schema of their own.
    """
    return differences(describe(conn), _expected() if expected is None else expected)


def _is_empty(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0)


@dataclass(frozen=True, slots=True)
class Inspection:
    """What a read-only look at a database file found."""

    case: Case
    #: The file's upgrade level; ``None`` when there is no database in it.
    level: int | None
    #: How its structure differs from the schema it is held against (empty when it matches).
    found: list[str]
    #: Its structure as :func:`describe` reads it (empty when there is no database in it).
    description: dict[str, str]


@contextmanager
def _open_read_only(path: Path) -> Iterator[sqlite3.Connection]:
    """A read-only connection that leaves no sidecar behind that it made itself.

    A read-only open of a write-ahead-log database makes an empty -wal and -shm beside
    it that it cannot remove again; the ones this open made are removed on the way out. That
    cleanup has a race, accepted on purpose: if another process opens the same database in the
    instant between this check closing and the unlink, a sidecar that process is using could
    be removed under it. Only sidecars this check created are removed, and a running app's
    `-wal` and `-shm` already exist before the check starts, so they are never touched; the
    window needs a process that opens a database nobody had open, in that instant. Skipping
    the cleanup would leave stray files beside every refused database instead.
    """
    sidecars = [Path(f"{path}-wal"), Path(f"{path}-shm")]
    present = [p.exists() for p in sidecars]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()
        for sidecar, existed in zip(sidecars, present, strict=True):
            if not existed:
                sidecar.unlink(missing_ok=True)


def inspect_database_file(
    path: Path,
    *,
    steps: Sequence[Step] = STEPS,
    expected: dict[str, str] | None = None,
) -> Inspection:
    """Look at a database file, read-only, and say how it stands against this build.

    The one place that decides the case; every caller (the boot's upgrade, the boot's check,
    ``import``, ``mcp``, ``serve``'s early check) goes through it. ``expected`` and ``steps``
    are for tests with a schema and steps of their own.
    """
    if not path.exists() or path.stat().st_size == 0:
        return Inspection("missing", None, [], {})
    want = _expected() if expected is None else expected
    with _open_read_only(path) as conn:
        if _is_empty(conn):
            return Inspection("missing", None, [], {})
        found_description = describe(conn)
    found = differences(found_description, want)
    level = int(found_description["user_version"])
    if not found:
        return Inspection("current", level, [], found_description)
    if level > int(want["user_version"]):
        return Inspection("newer", level, found, found_description)
    if level < len(steps) and fingerprint_of(found_description) == steps[level].expects:
        return Inspection("upgradable", level, found, found_description)
    return Inspection("differs", level, found, found_description)


def classify(
    path: Path,
    *,
    steps: Sequence[Step] = STEPS,
    expected: dict[str, str] | None = None,
) -> Case:
    """``missing``, ``current``, ``upgradable``, ``newer`` or ``differs`` for a database file."""
    return inspect_database_file(path, steps=steps, expected=expected).case


def _level_of(expected: dict[str, str] | None) -> int:
    return int((_expected() if expected is None else expected)["user_version"])


def check_database_file(
    path: Path,
    *,
    allow_older: bool = False,
    steps: Sequence[Step] = STEPS,
    expected: dict[str, str] | None = None,
) -> None:
    """Refuse (raise :class:`SchemaMismatch`) a database file this build cannot start on.

    Read-only, before the application opens the file: the database file and its `-wal` are
    never written, whatever the answer, and nothing is converted or deleted. A file that is
    missing or has nothing in it is fine; it is about to be made. A database below the latest
    level is refused too, unless ``allow_older``: only the app's own start upgrades one (or
    refuses it with a line of its own, when it is not in the state its level left), so the early
    check in ``serve`` lets every older file through to it, and the commands that only read the
    file say to start the app once. One side effect remains: SQLite's
    read-only open of a database that a crash left a write-ahead log for rebuilds its `-shm`
    index (the database, the `-wal` and the directory listing stay as they were).
    """
    inspection = inspect_database_file(path, steps=steps, expected=expected)
    if inspection.case in ("missing", "current"):
        return
    older = inspection.level is not None and inspection.level < _level_of(expected)
    if allow_older and older:
        return
    if inspection.case == "upgradable":
        log.info("database_upgrade_pending", path=str(path), db_level=inspection.level)
    else:
        log.error(
            "database_schema_differs",
            path=str(path),
            case=inspection.case,
            db_level=inspection.level,
            count=len(inspection.found),
            differences=inspection.found[:_REPORTED],
        )
    raise SchemaMismatch(path, inspection.found, inspection.case)


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
