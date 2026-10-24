"""Level 5 to level 6: a balance copied from the machine's notes card is only Sour or Bitter.

What changes in the database: nothing structural. The step is data only.

The machine's notes card has a balance control that starts on "balanced" whether or not anybody
touched it, so a "balanced" copied from a card says nothing about the cup. A verdict now holds a
balance only if the person chose it, here or on the machine, and the copy no longer takes
"balanced". This step clears the ones already copied, and the ones saved with a copy in them:
saving a verdict from a cell or the form writes the whole verdict back, so a copied "balanced"
that was carried into a saved verdict (flag 0) cannot be told from a deliberate one when the
shot's card says "balanced" too. A ``'balanced'`` is set to ``NULL`` where either the verdict is
still flagged as copied (``seeded_from_device_note = 1``) or the shot's card holds
``balance_taste = 'balanced'``.

The accepted cost: a Balanced the person really chose on a shot whose card also says balanced is
cleared too. It still shows, outlined, from the card, and one click fills it again. A saved
``'balanced'`` on a shot with no card, or with a Sour or Bitter card, is kept, and so is a
``'sour'`` or ``'bitter'`` copied from a card, which the firmware never fills in by itself.

What is carried forward: every row, with every other field as it was (rating, notes, doses,
the flag itself, ``updated_at``). Only the cleared balance goes, and the card still holds it
where there is one: the shots list and the judgement form show the machine's balance, outlined.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import sqlite3

from gaggiclanker.db.upgrades.step import Step

__all__ = ["EXPECTS", "STEP"]

#: The fingerprint of ``schemas/level_005.sql``, the database this step upgrades.
EXPECTS = "def160224d4b"


def run(conn: sqlite3.Connection) -> dict[str, int]:
    cursor = conn.execute(
        "UPDATE shot_judgements SET balance = NULL "
        "WHERE balance = 'balanced' AND (seeded_from_device_note = 1 "
        "OR shot_id IN (SELECT shot_id FROM device_shot_notes WHERE balance_taste = 'balanced'))"
    )
    return {"balances_cleared": max(cursor.rowcount, 0)}


STEP = Step(level=6, expects=EXPECTS, foreign_keys_off=False, run=run)
