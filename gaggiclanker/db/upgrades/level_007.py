"""Level 6 to level 7: the stars are the judgement, and Discard is its own column.

What changes in the database: ``shot_judgements.decision`` (``keep`` / ``improve`` / ``discard``)
is replaced, in the same place, by ``discarded INTEGER NOT NULL DEFAULT 0 CHECK (discarded IN
(0, 1))``. The two views that served ``decision`` (``v_shots`` and ``v_judgements``) serve
``discarded`` instead. Good, Neutral and Bad are no longer stored at all: they are the words for
a rating's band (4-5, 3, 1-2 stars), worked out where shots are counted.

What is carried forward: every row, with every other field (balance, notes, doses, the flags,
``updated_at``) as it was.

* ``discard`` becomes ``discarded = 1`` and keeps whatever rating the row had.
* ``keep`` or ``improve`` on a shot with a rating anywhere (the verdict's, else the machine's
  notes card's, else the index's, as the Rating column reads it) loses its label: the rating
  stands and says what the label tried to.
* ``keep`` or ``improve`` on a shot with no rating anywhere writes the verdict's rating, 4 for
  ``keep`` and 2 for ``improve``, so the person's word is not lost; the label is dropped.
* No label: ``discarded = 0``.

The accepted cost: a label that contradicted the stars (Good on a 2 star shot) now reads as the
stars say. A rating this step wrote differs from the machine's card, and reaches the machine only
if the person chooses it on the Sync page.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does. No other table refers to
``shot_judgements``, so foreign keys stay on.
"""

from __future__ import annotations

import sqlite3

from gaggiclanker.db.upgrades.step import Step

__all__ = ["EXPECTS", "STEP"]

#: The fingerprint of ``schemas/level_006.sql``, the database this step upgrades.
EXPECTS = "bd1eabab577d"

#: ``shot_judgements`` as ``schema.sql`` writes it at this level.
_NEW_TABLE = """
CREATE TABLE shot_judgements_new (
    shot_id                 INTEGER PRIMARY KEY REFERENCES shots(id) ON DELETE CASCADE,
    rating                  INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    balance                 TEXT    CHECK (balance IS NULL OR balance IN
                                    ('sour', 'balanced', 'bitter')),
    taste_notes_json        TEXT    NOT NULL DEFAULT '[]',
    aroma_notes_json        TEXT    NOT NULL DEFAULT '[]',
    dose_in_g               REAL,
    dose_out_g              REAL,
    notes                   TEXT    NOT NULL DEFAULT '',
    discarded               INTEGER NOT NULL DEFAULT 0 CHECK (discarded IN (0, 1)),
    seeded_from_device_note INTEGER NOT NULL DEFAULT 0,
    device_synced_at        TEXT,
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT
"""

_INDEX = "CREATE INDEX idx_shot_judgements_rating ON shot_judgements(rating)"

#: The views that named ``j.decision``; every view is dropped and made again so the table can be
#: renamed, and these two are made again with the column's new name. ``v_shots`` joins the
#: judgement on the left, so it serves ``COALESCE(j.discarded, 0)``: a shot nobody judged is not
#: discarded, and the column is a true 0 or 1 for every shot. ``v_judgements`` is an inner join.
_RENAMED_IN = {
    "v_shots": "COALESCE(j.discarded, 0)                    AS discarded,",
    "v_judgements": "j.discarded,",
}


def run(conn: sqlite3.Connection) -> dict[str, int]:
    views = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'view' ORDER BY rowid"
    ).fetchall()
    for name, _sql in views:
        conn.execute(f'DROP VIEW "{name}"')

    counts = {
        "discarded": conn.execute(
            "SELECT COUNT(*) FROM shot_judgements WHERE decision = 'discard'"
        ).fetchone()[0],
        "labels_dropped": 0,
        "ratings_written": 0,
    }
    # The rating the Rating column shows, for the labelled shots only. COALESCE as the column
    # reads it: the first rating present, and a zero on the card hides the index's figure.
    rated = {
        row[0]
        for row in conn.execute(
            "SELECT j.shot_id FROM shot_judgements j JOIN shots s ON s.id = j.shot_id "
            "LEFT JOIN device_shot_notes n ON n.shot_id = j.shot_id "
            "WHERE COALESCE(j.rating, n.rating, s.index_rating) > 0"
        )
    }

    conn.execute(_NEW_TABLE)
    rows = conn.execute(
        "SELECT shot_id, rating, balance, taste_notes_json, aroma_notes_json, dose_in_g, "
        "dose_out_g, notes, decision, seeded_from_device_note, device_synced_at, updated_at "
        "FROM shot_judgements ORDER BY shot_id"
    ).fetchall()
    for row in rows:
        shot_id, rating, decision = row[0], row[1], row[8]
        discarded = 1 if decision == "discard" else 0
        if decision in ("keep", "improve"):
            counts["labels_dropped"] += 1
            if shot_id not in rated:
                rating = 4 if decision == "keep" else 2
                counts["ratings_written"] += 1
        conn.execute(
            "INSERT INTO shot_judgements_new (shot_id, rating, balance, taste_notes_json, "
            "aroma_notes_json, dose_in_g, dose_out_g, notes, discarded, seeded_from_device_note, "
            "device_synced_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (shot_id, rating, *row[2:8], discarded, *row[9:]),
        )
    conn.execute("DROP TABLE shot_judgements")
    conn.execute("ALTER TABLE shot_judgements_new RENAME TO shot_judgements")
    conn.execute(_INDEX)

    for name, sql in views:
        if name in _RENAMED_IN:
            if "j.decision," not in sql:
                raise RuntimeError(f"{name} does not name j.decision where it was expected")
            sql = sql.replace("j.decision,", _RENAMED_IN[name])
        conn.execute(sql)
    return counts


STEP = Step(level=7, expects=EXPECTS, foreign_keys_off=False, run=run)
