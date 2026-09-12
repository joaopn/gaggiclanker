"""The SQL sandbox: what ``query_shots`` may run, and how it is stopped.

The SQL tool is the workhorse of the chat — "average flow-vs-target residual
for the last ten shots per Set version" is one query and no bespoke endpoint —
and it is also the only tool that runs text a language model wrote against the
archive. Four layers stand between the two, in this order, and each exists
because the one before it is not sufficient on its own:

1. **A textual pre-check.** One statement, starting with ``SELECT`` or ``WITH``,
   no stray semicolon. This layer's job is not safety, it is *error messages*: a
   model that is told "only a single SELECT, and only over the v_* views" fixes
   its query, where a model shown a sqlite3 authorization failure usually tries
   the same thing again.
2. **A SQLite authorizer callback.** The real gate. It is consulted by the
   *parser*, sees the resolved table names rather than the text, and refuses
   anything that is not a read of a whitelisted view. A blocklist of keywords
   cannot do this: ``SELECT * FROM "shots"`` and ``select*from[shots]`` are the
   same query and different strings.
3. **``PRAGMA query_only``.** Belt and braces. Even a read the authorizer let
   through cannot become a write.
4. **A progress handler and a row cap.** A recursive CTE is a Turing-complete
   loop, so "it is only a SELECT" says nothing about how long it runs; the
   progress handler interrupts it, and the row cap stops a cross join from
   returning a million rows into a prompt.

It runs on its own ``sqlite3`` connection in a worker thread rather than on the
app's ``aiosqlite`` one. Two reasons: the authorizer and the progress handler
are per-connection settings that must not leak onto the connection every other
query in the process uses, and an interrupt has to be able to kill this query
without touching anything else.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

__all__ = [
    "ALLOWED_VIEWS",
    "DEFAULT_ROW_LIMIT",
    "MAX_ROW_LIMIT",
    "VIEW_BASE_TABLES",
    "QueryResult",
    "SqlRefused",
    "run_query",
    "validate_sql",
]

log = structlog.get_logger(__name__)

#: The only relations a generated query may read. Created by migration 0013.
#: Adding one here without adding the view is a refusal; adding the view without
#: adding it here makes it invisible, which is the safe direction.
ALLOWED_VIEWS: frozenset[str] = frozenset(
    {
        "v_shots",
        "v_samples",
        "v_sets",
        "v_set_versions",
        "v_judgements",
        "v_analyses",
        "v_suggestions",
        "v_profiles",
        "v_beans",
        "v_grinders",
    }
)

#: The base tables the views above read. SQLite issues a column-less
#: ``SQLITE_READ`` while it resolves a view's joins — an empty column name and,
#: annoyingly, **no view in the fifth argument** — so a rule of "the view, or a
#: read attributed to a view" refuses every legitimate query over `v_shots`.
#: This is the narrow exception: a column-less read of a table one of the views
#: is built from. It is not a hole worth closing further, because the worst a
#: column-less read discloses is a row count of a table whose rows the views
#: already serve in full — and `settings`, `auth_sessions`, `runtime_secrets`,
#: `llm_calls` and `device_writes` are not on this list, so they stay invisible.
VIEW_BASE_TABLES: frozenset[str] = frozenset(
    {
        "shots",
        "shot_samples",
        "shot_judgements",
        "shot_analyses",
        "suggestions",
        "sets",
        "set_versions",
        "beans",
        "grinders",
        "profile_versions",
    }
)

DEFAULT_ROW_LIMIT = 100
MAX_ROW_LIMIT = 500

#: How long one query may run. Long enough for a full-table aggregate over a
#: year of shots, short enough that a runaway CTE is cut off well inside the
#: tool's own timeout, so the model gets "too slow" rather than the run getting
#: "the tool hung".
QUERY_TIMEOUT_S = 2.0

#: How often SQLite asks the progress handler whether to carry on. Small enough
#: that a tight loop is interrupted promptly, large enough that the callback is
#: not a measurable share of an ordinary query.
PROGRESS_INSTRUCTIONS = 1000

#: Ceiling on any single string or blob SQLite will build. A shot's raw bytes
#: are not reachable through the views, so nothing legitimate comes close, and
#: an allocation this size is a request to exhaust the box's memory rather than
#: a query.
MAX_VALUE_BYTES = 1_000_000

#: Ceiling on the statement itself, comfortably above the 8 000-character cap
#: the tool's own input model applies.
MAX_SQL_BYTES = 100_000

#: Cells are truncated at this many characters. A ``diagnostics_json`` blob is
#: several kilobytes and a hundred of them is the whole context window.
CELL_LIMIT = 2000

# Statements that are not a read, spelled as whole words. The authorizer is what
# actually refuses them; this is here so the *message* names the problem.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex"
    r"|begin|commit|rollback|savepoint|analyze)\b",
    re.IGNORECASE,
)

# `--` to end of line, and /* ... */ across lines. Stripped before the textual
# checks so `SELECT 1 -- ; DROP` is not read as two statements.
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)

_STRINGS = re.compile(r"'(?:[^']|'')*'")


class SqlRefused(Exception):
    """The query was refused before it ran. The message is shown to the model."""


class QueryResult:
    """Columns, rows, and whether the cap bit."""

    __slots__ = ("columns", "duration_ms", "rows", "truncated")

    def __init__(
        self,
        columns: list[str],
        rows: list[list[Any]],
        *,
        truncated: bool = False,
        duration_ms: int = 0,
    ) -> None:
        self.columns = columns
        self.rows = rows
        self.truncated = truncated
        self.duration_ms = duration_ms


def _stripped(sql: str) -> str:
    """The statement with comments and string literals blanked out.

    String literals go too, because a table name inside one is not a table name
    and a semicolon inside one is not a statement separator.
    """
    return _STRINGS.sub("''", _COMMENTS.sub(" ", sql))


def validate_sql(sql: str) -> str:
    """Refuse anything that is not one plain SELECT, and return it trimmed."""
    text = sql.strip()
    if not text:
        raise SqlRefused("The query was empty.")
    bare = _stripped(text).strip().rstrip(";").strip()
    if not bare:
        raise SqlRefused("The query was only comments.")
    if ";" in bare:
        raise SqlRefused("Only one statement per call. Remove the ';' and send a single SELECT.")
    first = bare.split(None, 1)[0].lower()
    if first not in {"select", "with"}:
        raise SqlRefused(
            "Only SELECT is allowed (a WITH ... SELECT is fine). "
            f"This query starts with {first.upper()!r}."
        )
    forbidden = _FORBIDDEN.search(bare)
    if forbidden is not None:
        raise SqlRefused(
            f"{forbidden.group(0).upper()} is not allowed. This tool reads; it never writes, "
            "and PRAGMA and ATTACH are refused outright."
        )
    return text.rstrip().rstrip(";")


#: Relations SQLite provides itself. Not in ``sqlite_master``, and every one of
#: them is a way to read the schema, so they are named here to be refused.
_BUILTIN_RELATIONS: frozenset[str] = frozenset(
    {
        "sqlite_master",
        "sqlite_schema",
        "sqlite_temp_master",
        "sqlite_temp_schema",
        "sqlite_sequence",
    }
)


def _relation_names(conn: sqlite3.Connection) -> frozenset[str]:
    """Every table and view in the file, lowercased. Read before the gate goes up.

    The authorizer needs this because of how SQLite reports a **column-less**
    read — the one it issues for ``SELECT COUNT(*) FROM t`` and for a table
    named but not projected. That callback arrives with *no database name*,
    which is also how a CTE arrives, so "no database name means a CTE" reads
    `SELECT COUNT(*) FROM settings` as a subquery alias and lets the row count
    of the password table out. Knowing which names are real relations is what
    tells the two apart.
    """
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    return frozenset({str(row[0]).lower() for row in rows} | _BUILTIN_RELATIONS)


def _bare_name(qualified: str) -> str:
    """``main.settings`` -> ``settings``; quotes and case dropped."""
    name = qualified.strip().strip('"').strip("`").strip("[]")
    if "." in name:
        name = name.rsplit(".", 1)[1].strip().strip('"').strip("`")
    return name.lower()


def _make_authorizer(relations: frozenset[str]) -> Callable[..., int]:
    """SQLite's own gate: refuse everything that is not a read of a ``v_`` view.

    ``source`` (the fifth argument) is the view a nested access came from, which
    is how a read of ``shots`` *through* ``v_shots`` is told apart from a direct
    one — the first is the whole point of the views and the second is what they
    exist to prevent.

    ``relations`` is the closed-over list of real tables and views. A read of a
    name that is **not** one of them is a CTE or a subquery alias (or a
    table-valued function such as ``json_each``) and is allowed; a read of a
    name that *is* one is allowed only by the two rules above. The cost is that
    a CTE deliberately named after a real table is refused rather than read,
    which is a strange thing to write and a safe way to be wrong.
    """

    allowed_views = frozenset(name.lower() for name in ALLOWED_VIEWS)
    column_less_ok = frozenset(name.lower() for name in VIEW_BASE_TABLES)

    def authorize(action: int, arg1: Any, arg2: Any, dbname: Any, source: Any) -> int:
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
            # SQLITE_FUNCTION covers the scalar, aggregate and table-valued
            # functions a real query needs — COUNT, AVG, json_extract,
            # json_each. `load_extension` is a separate action and is not here.
            return sqlite3.SQLITE_OK
        if action != sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_DENY

        name = _bare_name(str(arg1 or ""))
        column = str(arg2 or "")
        via = _bare_name(str(source or ""))
        if name in allowed_views or via in allowed_views:
            return sqlite3.SQLITE_OK
        # The column-less read a view's own join resolution produces, and the
        # one `SELECT COUNT(*)` produces. Allowed only for the tables the views
        # are built from: the worst it discloses is a row count of rows the
        # views already serve in full, and `settings`, `auth_sessions`,
        # `runtime_secrets`, `llm_calls` and `device_writes` are not among them.
        if column == "" and name in column_less_ok:
            return sqlite3.SQLITE_OK
        if name in relations:
            return sqlite3.SQLITE_DENY
        # Not a relation in this file: a CTE, a subquery alias, or a
        # table-valued function's virtual row source.
        return sqlite3.SQLITE_OK

    return authorize


def _truncate(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, str) and len(value) > CELL_LIMIT:
        return value[:CELL_LIMIT] + f"… ({len(value)} chars)"
    return value


def _run_blocking(path: Path, sql: str, limit: int, timeout_s: float) -> QueryResult:
    started = time.monotonic()
    deadline = started + timeout_s
    conn = sqlite3.connect(path, timeout=1.0)
    try:
        # Layer 3. Set before the authorizer so that even the pragma itself
        # cannot be the thing that slips through.
        conn.execute("PRAGMA query_only = ON")
        # `SELECT randomblob(1000000000)` is one short statement that asks for a
        # gigabyte, and neither the row cap nor the deadline helps: the
        # allocation happens while producing the first row. A length limit makes
        # it an error instead, immediately.
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES)
        conn.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, PROGRESS_INSTRUCTIONS
        )
        # Read the relation names first: the authorizer refuses `sqlite_master`,
        # so this query cannot be made once the gate is up.
        conn.set_authorizer(_make_authorizer(_relation_names(conn)))
        cursor = conn.execute(sql)
        columns = [description[0] for description in (cursor.description or [])]
        rows = cursor.fetchmany(limit + 1)
        truncated = len(rows) > limit
        return QueryResult(
            columns=columns,
            rows=[[_truncate(cell) for cell in row] for row in rows[:limit]],
            truncated=truncated,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "interrupted" in message.lower():
            raise SqlRefused(
                f"The query was still running after {timeout_s:g}s and was stopped. "
                "Add a WHERE clause, aggregate, or ask for fewer rows."
            ) from exc
        raise SqlRefused(f"SQLite refused the query: {message}") from exc
    except sqlite3.DatabaseError as exc:
        # `not authorized` arrives here. Naming the views is what turns a dead
        # end into a second, correct attempt.
        raise SqlRefused(
            f"{exc}. Only these views are readable: {', '.join(sorted(ALLOWED_VIEWS))}."
        ) from exc
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_authorizer(None)
        conn.close()


async def run_query(
    path: Path,
    sql: str,
    *,
    limit: int = DEFAULT_ROW_LIMIT,
    timeout_s: float = QUERY_TIMEOUT_S,
) -> QueryResult:
    """Validate, then run in a worker thread so the event loop keeps serving."""
    statement = validate_sql(sql)
    capped = max(1, min(int(limit), MAX_ROW_LIMIT))
    return await asyncio.to_thread(_run_blocking, path, statement, capped, timeout_s)
