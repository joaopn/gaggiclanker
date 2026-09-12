-- Storage cleanup and notes write-back: two more things this box may ask a machine to do, and
-- the ledger of the one that deletes something.
--
-- Profile push opened five writes, all of them profile operations, and wrote that
-- fact into a CHECK constraint: `device_writes.kind` accepted exactly those
-- five spellings and nothing else. That constraint is doing its job — this
-- migration is what "widening the surface is a design decision, not a
-- refactor" looks like in the schema, and it is deliberately a file of its own
-- rather than an edit to 0008.
--
-- Two kinds join the list:
--
--   * `shot_delete`  — `req:history:delete`, the storage cleanup. The firmware performs
--     the same deletion itself when free space drops below 500 KB, so this is
--     not a capability the machine lacks; what it is, is unrecoverable. The
--     gate refuses it for any shot whose bytes are not already in the archive,
--     intact, unquarantined and on this machine.
--   * `notes_save`   — `req:history:notes:save`, the notes write-back. It overwrites the
--     machine's own notes card for a shot, and as a side effect the index's
--     `rating` and (when `doseOut` arrives as a non-empty string) `volume`.
--
-- STRICT tables cannot have a CHECK constraint altered in place, so the table
-- is rebuilt: new table, copy, drop, rename, indexes re-created. It holds one
-- row per write attempt on a box that has made a few hundred of them at most,
-- so the copy is free. The column order, defaults and indexes are identical to
-- 0008's apart from the widened CHECK — read the comments there for why each
-- column exists.
--
-- No transaction control here: the runner wraps the whole file plus its ledger
-- row in one, and SQLite's DDL is transactional.

CREATE TABLE device_writes_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL CHECK (kind IN
                   ('profile_save', 'profile_delete', 'profile_select',
                    'profile_favorite', 'profile_unfavorite',
                    'shot_delete', 'notes_save')),
    host         TEXT    NOT NULL DEFAULT '',
    device_id    TEXT,
    payload_hash TEXT    NOT NULL DEFAULT '',
    result       TEXT    NOT NULL CHECK (result IN ('ok', 'refused', 'failed')),
    error        TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

INSERT INTO device_writes_new
    (id, kind, host, device_id, payload_hash, result, error, created_at)
SELECT id, kind, host, device_id, payload_hash, result, error, created_at
FROM device_writes;

DROP TABLE device_writes;
ALTER TABLE device_writes_new RENAME TO device_writes;

CREATE INDEX idx_device_writes_created ON device_writes(created_at DESC, id DESC);
CREATE INDEX idx_device_writes_provenance ON device_writes(device_id, kind, result);

-- ── the cleanup ledger ───────────────────────────────────────────────
--
-- One row per cleanup pass, not per deleted shot: the per-shot record already
-- exists twice over — `device_writes` has the attempt and `shots.deleted_on_device`
-- has the outcome — and a third copy keyed by shot would be the one that drifts.
-- What this answers is the question a person asks the Device page: *what has
-- this thing been deleting off my machine, and did any of it go wrong?*
--
-- `mode` and `target` are the policy **as it was when the run happened**, not
-- as it is now. A run that deleted thirty shots under `keep_newest = 20` and a
-- run that deleted thirty under a 4 MB free-space floor are different events,
-- and the setting they were made under is editable.
--
-- `planned` is what the plan said; `deleted` is what the machine acknowledged.
-- They differ whenever a run stopped early, which is the whole reason both are
-- stored: "planned 40, deleted 7, error: the machine disconnected" is a
-- complete account and "deleted 7" is not.
--
-- `free_before`/`free_after` are bytes from `res:ota-settings` (`spiffsFree`, or
-- `sdFree` when a card is mounted). Nullable, because a machine that never
-- broadcast an identity frame reports neither and a zero would read as "full".
CREATE TABLE cleanup_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id   INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
    mode         TEXT    NOT NULL CHECK (mode IN ('off', 'keep_newest', 'free_space')),
    -- The policy's number: the keep-newest count, or the free-space floor in KB.
    target       INTEGER NOT NULL DEFAULT 0,
    trigger      TEXT    NOT NULL DEFAULT 'manual',
    status       TEXT    NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running', 'ok', 'error')),
    planned      INTEGER NOT NULL DEFAULT 0,
    deleted      INTEGER NOT NULL DEFAULT 0,
    errors       INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    free_before  INTEGER,
    free_after   INTEGER,
    started_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at  TEXT
) STRICT;

-- The Device page's list, newest first.
CREATE INDEX idx_cleanup_runs_machine ON cleanup_runs(machine_id, started_at DESC, id DESC);
