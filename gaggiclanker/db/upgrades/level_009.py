"""Level 8 to level 9: a numbered grinder says which way its numbers run.

What changes in the database: ``grinders.step_unit`` no longer takes ``numbers``. In its place
are ``numbers_lower_finer`` (a lower number grinds finer, as on most dials) and
``numbers_lower_coarser`` (the other way round). ``clicks``, ``microns`` and ``free`` are
unchanged. The CHECK constraint is part of the table, so the table is rebuilt (the SQLite
12-step). ``recipes`` and ``starting_point_runs`` refer to it, so foreign keys are off for the
upgrade, and the three views that read it are dropped first and put back as they were.

What is carried forward: every grinder, with its id, name, model, burrs, notes and creation
time, and the AUTOINCREMENT counter. A grinder on ``numbers`` becomes ``numbers_lower_finer``.
That is a guess: the old value never said which way the dial runs. It is the common dial, and
it is what the agent assumed before. A grinder that runs the other way has to be switched on
the Hardware page once, and the changelog says so.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import sqlite3

from gaggiclanker.db.upgrades.step import Step

__all__ = ["EXPECTS", "STEP"]

#: The fingerprint of ``schemas/level_008.sql``, the database this step upgrades.
EXPECTS = "ae60815f1d1c"

#: ``grinders`` as ``schema.sql`` writes it at this level.
_NEW_TABLE = """
CREATE TABLE grinders_new (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    model      TEXT,
    burr_type  TEXT    NOT NULL DEFAULT 'unknown'
               CHECK (burr_type IN ('conical', 'flat', 'unknown')),
    step_unit  TEXT    NOT NULL DEFAULT 'clicks'
               CHECK (step_unit IN ('clicks', 'numbers_lower_finer', 'numbers_lower_coarser',
                                    'microns', 'free')),
    notes      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT
"""


def run(conn: sqlite3.Connection) -> dict[str, int]:
    # ALTER TABLE ... RENAME re-checks every view, and a view naming a table that is gone for
    # the moment fails it, so every view goes first and comes back verbatim at the end.
    views = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'view' ORDER BY rowid"
    ).fetchall()
    for name, _sql in views:
        conn.execute(f'DROP VIEW "{name}"')

    counts = {
        "grinders": conn.execute("SELECT COUNT(*) FROM grinders").fetchone()[0],
        "numbers_read_as_lower_finer": conn.execute(
            "SELECT COUNT(*) FROM grinders WHERE step_unit = 'numbers'"
        ).fetchone()[0],
    }
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'grinders'").fetchone()

    conn.execute(_NEW_TABLE)
    conn.execute(
        "INSERT INTO grinders_new (id, name, model, burr_type, step_unit, notes, created_at) "
        "SELECT id, name, model, burr_type, "
        "CASE step_unit WHEN 'numbers' THEN 'numbers_lower_finer' ELSE step_unit END, "
        "notes, created_at FROM grinders ORDER BY id"
    )
    conn.execute("DROP TABLE grinders")
    conn.execute("ALTER TABLE grinders_new RENAME TO grinders")
    # The counter is the old table's, so an id freed by a deleted last row is not handed out
    # again (the copy alone would set it to the highest id still there).
    if sequence is not None:
        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'grinders'", (sequence[0],))

    for _name, sql in views:
        conn.execute(sql)
    return counts


STEP = Step(level=9, expects=EXPECTS, foreign_keys_off=True, run=run)
