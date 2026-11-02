"""Level 7 to level 8: signature change requests, and the expectations a draft touches.

What changes in the database:

* A new table, ``signature_change_requests``: a request the agent made in a conversation (or
  with a profile draft) to replace, retire, re-tier or put back an expectation, waiting for the
  person to press Approve. It holds the changes as one JSON document written through a model,
  its status (waiting, approved, declined, stale), why it went stale, the person's reason, the
  expectation ids an approval wrote and the times.
* ``profile_drafts`` gains one trailing column, ``affected_expectations_json``
  (``TEXT NOT NULL DEFAULT '[]'``): the carried expectations the draft's patch touches, worked
  out once when the draft is made. The table ends with a column, so ``ADD COLUMN`` leaves it as
  the schema file defines it.

What is carried forward: every row, unchanged. The new table starts empty, which is true of every
database (nobody could ask before), and every existing draft reads ``[]``: no flag was ever
worked out for it, and nothing is made up.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import sqlite3

from gaggiclanker.db.upgrades.step import Step

__all__ = ["EXPECTS", "STEP"]

#: The fingerprint of ``schemas/level_007.sql``, the database this step upgrades.
EXPECTS = "8d2eb66e9ce8"

_TABLE = """
CREATE TABLE signature_change_requests (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_version_id INTEGER NOT NULL REFERENCES profile_versions(id) ON DELETE CASCADE,
    thread_id          INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
    -- NULL unless the request came with a profile draft: that one is answered by its draft.
    draft_id           INTEGER REFERENCES profile_drafts(id) ON DELETE CASCADE,
    reason             TEXT    NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
    -- The changes, as a `StoredChanges` document (written through its model).
    changes_json       TEXT    NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'waiting'
                       CHECK (status IN ('waiting', 'approved', 'declined', 'stale')),
    stale_reason       TEXT    NOT NULL DEFAULT '',
    answer_reason      TEXT    NOT NULL DEFAULT '',
    -- The expectation ids an approval wrote (a replacement's new row).
    written_json       TEXT    NOT NULL DEFAULT '[]',
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    answered_at        TEXT
) STRICT
"""

_INDEXES = (
    "CREATE INDEX idx_signature_change_requests_thread ON signature_change_requests(thread_id, id)",
    "CREATE INDEX idx_signature_change_requests_version "
    "ON signature_change_requests(profile_version_id, id DESC)",
    "CREATE INDEX idx_signature_change_requests_draft "
    "ON signature_change_requests(draft_id) WHERE draft_id IS NOT NULL",
    "CREATE INDEX idx_signature_change_requests_waiting "
    "ON signature_change_requests(id) WHERE status = 'waiting'",
)


def run(conn: sqlite3.Connection) -> dict[str, int]:
    conn.execute(_TABLE)
    for statement in _INDEXES:
        conn.execute(statement)
    conn.execute(
        "ALTER TABLE profile_drafts ADD COLUMN "
        "affected_expectations_json TEXT NOT NULL DEFAULT '[]'"
    )
    return {"tables_added": 1, "columns_added": 1}


STEP = Step(level=8, expects=EXPECTS, foreign_keys_off=False, run=run)
