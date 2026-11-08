"""Level 9 to level 10: an insight can name the person's message it came from.

What changes in the database: ``knowledge_insights`` gains one trailing nullable column,
``from_message_id`` (``REFERENCES chat_messages(id) ON DELETE SET NULL``): the person's own
message a general insight was requested from or saved from. The table ends with a column, so
``ADD COLUMN`` leaves it as the schema file defines it.

What is carried forward: every row, unchanged. Every existing insight reads NULL, which is true
of each: no insight was ever made from a message.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import sqlite3

from gaggiclanker.db.upgrades.step import Step

__all__ = ["EXPECTS", "STEP"]

#: The fingerprint of ``schemas/level_009.sql``, the database this step upgrades.
EXPECTS = "319a18c4ffc3"


def run(conn: sqlite3.Connection) -> dict[str, int]:
    conn.execute(
        "ALTER TABLE knowledge_insights ADD COLUMN "
        "from_message_id INTEGER REFERENCES chat_messages(id) ON DELETE SET NULL"
    )
    return {"columns_added": 1}


STEP = Step(level=10, expects=EXPECTS, foreign_keys_off=False, run=run)
