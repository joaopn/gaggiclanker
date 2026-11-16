"""Level 13 to level 14: the profiles of archived Recipes are switched off.

What changes in the database: nothing structural. The step is data only.

Archiving a Recipe used to leave its own profile switched on, so every sync with Writes on kept
that profile on the machine, where each shot brewed on it landed under "needs a Recipe". Archive
now switches the profile off (and Unarchive switches it back on). This step does the same for the
Recipes that were archived before: every profile on the list (``deleted_at IS NULL``) that is
switched on (``on_machine = 1``) and whose Recipe (``recipes.board_id = profile_board.id``) is
archived gets ``on_machine = 0`` and a new ``updated_at``, as the switch's own write does. A
Recipe with no profile (``board_id IS NULL``), a profile whose row is deleted and a profile that is
already off are not touched.

What is carried forward: every row. Nothing is removed, the star (``on_home_screen``) is kept so a
profile that is switched back on comes back starred as it was, and the Recipes themselves, their
versions, their pins (already unpinned when the Recipe was archived) and their shots are as they
were.

What the person does: nothing. The next sync with Writes on (possibly the first after the app
starts) takes the switched-off profiles off the machine through the usual content check, the
selected one last. To keep one, switch it back on on the Profiles page (it asks first) or
unarchive its Recipe, which switches it on too.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import sqlite3

from gaggiclanker.db.upgrades.step import Step

__all__ = ["EXPECTS", "STEP"]

#: The fingerprint of ``schemas/level_013.sql``, the database this step upgrades.
EXPECTS = "733218e02428"


def run(conn: sqlite3.Connection) -> dict[str, int]:
    cursor = conn.execute(
        "UPDATE profile_board "
        "SET on_machine = 0, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE deleted_at IS NULL AND on_machine = 1 "
        "AND id IN (SELECT board_id FROM recipes WHERE archived = 1 AND board_id IS NOT NULL)"
    )
    return {"archived_profiles_switched_off": max(cursor.rowcount, 0)}


STEP = Step(level=14, expects=EXPECTS, foreign_keys_off=False, run=run)
