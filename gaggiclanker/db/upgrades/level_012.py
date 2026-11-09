"""Level 11 to level 12: a recorded outcome remembers how many shots it was recorded on.

What changes in the database: ``recipe_versions`` gains one trailing column,
``outcome_counted_shots`` (``INTEGER``, nullable). It is the number of counted shots (rated, not
discarded) the version's recorded outcome rests on, so that a later screen can say "2 more
counted since". The table ends with a column, so ``ADD COLUMN`` leaves it as the schema file
defines it. No view selects ``v.*`` from the table, so none changes.

What is carried forward: every row. A version with an outcome gets the ``counted_shots`` of its
newest accepted outcome proposal whose outcome and note are the ones the version carries now
and that was accepted within five seconds of the outcome being written (that press is what
wrote the outcome, and the proposal wrote down how many shots it was written on; a later hand
re-record of the same words is not that press). Every other row stays NULL: an outcome typed
by hand, or changed after it was accepted, has no stored count, and NULL is true for it, the
count is unknown. The person does nothing.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import sqlite3

from gaggiclanker.db.upgrades.step import Step

__all__ = ["EXPECTS", "STEP"]

#: The fingerprint of ``schemas/level_011.sql``, the database this step upgrades.
EXPECTS = "71ba290c810b"


def run(conn: sqlite3.Connection) -> dict[str, int]:
    conn.execute("ALTER TABLE recipe_versions ADD COLUMN outcome_counted_shots INTEGER")
    conn.execute(
        """
        UPDATE recipe_versions
           SET outcome_counted_shots = (
               SELECT p.counted_shots
                 FROM recipe_outcome_proposals p
                WHERE p.recipe_version_id = recipe_versions.id
                  AND p.status = 'accepted'
                  AND p.outcome = recipe_versions.outcome
                  AND p.note = recipe_versions.outcome_note
                  AND ABS(julianday(p.decided_at) - julianday(recipe_versions.outcome_at))
                      * 86400 <= 5
                ORDER BY p.id DESC
                LIMIT 1)
         WHERE outcome IS NOT NULL
        """
    )
    filled = conn.execute(
        "SELECT COUNT(*) FROM recipe_versions WHERE outcome_counted_shots IS NOT NULL"
    ).fetchone()[0]
    return {"columns_added": 1, "counts_filled": int(filled)}


STEP = Step(level=12, expects=EXPECTS, foreign_keys_off=False, run=run)
