"""Level 10 to level 11: a deletion proposal may belong to no Recipe.

What changes in the database: ``recipe_insight_deletions.recipe_id`` no longer says ``NOT NULL``.
A proposal to delete a general insight has no Recipe, whichever conversation proposed it. The
constraint is part of the table, so the table is rebuilt (the SQLite 12-step) and its three
indexes are created again. Nothing refers to the table, but it refers to three others, so foreign
keys are off for the upgrade and checked after.

What is carried forward: every row, in every status, with its id, and the AUTOINCREMENT counter.
Every existing row keeps the Recipe it already had. The person does nothing.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import sqlite3

from gaggiclanker.db.upgrades.step import Step

__all__ = ["EXPECTS", "STEP"]

#: The fingerprint of ``schemas/level_010.sql``, the database this step upgrades.
EXPECTS = "507f31006add"

#: ``recipe_insight_deletions`` as ``schema.sql`` writes it at this level.
_NEW_TABLE = """
CREATE TABLE recipe_insight_deletions_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id    INTEGER REFERENCES recipes(id) ON DELETE CASCADE,
    thread_id    INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    insight_id   INTEGER REFERENCES knowledge_insights(id) ON DELETE SET NULL,
    insight_text TEXT    NOT NULL,
    reason       TEXT    NOT NULL CHECK (length(reason) BETWEEN 20 AND 500),
    status       TEXT    NOT NULL DEFAULT 'proposed'
                 CHECK (status IN ('proposed', 'deleted', 'kept', 'stale', 'superseded')),
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    decided_at   TEXT
) STRICT
"""

_INDEXES = (
    "CREATE UNIQUE INDEX idx_recipe_insight_deletions_waiting "
    "ON recipe_insight_deletions(insight_id) WHERE status = 'proposed'",
    "CREATE INDEX idx_recipe_insight_deletions_thread ON recipe_insight_deletions(thread_id, id)",
    "CREATE INDEX idx_recipe_insight_deletions_recipe "
    "ON recipe_insight_deletions(recipe_id, id DESC)",
)

_COLUMNS = (
    "id, recipe_id, thread_id, insight_id, insight_text, reason, status, created_at, decided_at"
)


def run(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT COUNT(*) FROM recipe_insight_deletions").fetchone()[0]
    sequence = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'recipe_insight_deletions'"
    ).fetchone()

    conn.execute(_NEW_TABLE)
    conn.execute(
        f"INSERT INTO recipe_insight_deletions_new ({_COLUMNS}) "  # noqa: S608 - constants
        f"SELECT {_COLUMNS} FROM recipe_insight_deletions ORDER BY id"
    )
    conn.execute("DROP TABLE recipe_insight_deletions")
    conn.execute("ALTER TABLE recipe_insight_deletions_new RENAME TO recipe_insight_deletions")
    for sql in _INDEXES:
        conn.execute(sql)
    # The counter is the old table's, so an id freed by a deleted last row is not handed out
    # again.
    if sequence is not None:
        conn.execute(
            "UPDATE sqlite_sequence SET seq = ? WHERE name = 'recipe_insight_deletions'",
            (sequence[0],),
        )
    return {"recipe_insight_deletions": rows}


STEP = Step(level=11, expects=EXPECTS, foreign_keys_off=True, run=run)
