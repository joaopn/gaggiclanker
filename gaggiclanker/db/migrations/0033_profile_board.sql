-- The app's own profile board, and a summary on every sync run.
--
-- The board is the list of profiles the app means the machine to hold: one row per profile,
-- not per version. A row names the version that is current, whether the profile belongs on
-- the machine's home screen, and which file on the machine holds it right now. A pull with
-- the writes switch on makes the machine's profiles match this table (see
-- `drafts/board.py`); with the switch off the table is only read and edited.
--
--   * current_version_id: what the profile is meant to be. A new version of the profile
--     (an approved draft in the same lineage) replaces it in place, so the row keeps its
--     identity and its home-screen choice.
--   * device_profile_id / device_version_id: the file on the machine this row last found or
--     put there, and the stored version its content was then. NULL when the machine holds
--     nothing for the row yet, or when the app let go of the file (it was left on the
--     machine because the app did not write it). They are what tells "an older version the
--     board has moved past" from "somebody edited the profile on the display": no foreign
--     key on the device id, like every device id, since it names a file that can be gone.
--   * on_home_screen: the machine's favourite star. The firmware has no enabled flag; a
--     profile off the home screen stays on the machine and leaves the carousel.
--   * origin: whose profile it is. `adopted`: a profile the person made, taken from the
--     machine as it was; a pull never pushes one. `draft`: the app's own, either from an
--     approved draft or adopted from a file this app had itself saved (an `ok` save of that
--     id in the audit and the app label); only these are pushed, replaced and removed by a
--     pull.
--   * failed_version_id: a version whose copy did not read back as sent and could not be
--     taken off the machine again. Not tried again until the row's version changes.
--   * pending_draft_id / pending_set_id / pending_major: what the person asked for when
--     they put the draft on the board, kept until the pull that puts it on the machine
--     records it (the draft as pushed, a Set version when a Set was named). NULL once done.
--   * deleted_at: a tombstone. The machine's copy is removed by the next pull when the app
--     wrote it, and left alone when it did not.
--
-- profile_board_adoption holds one row once the machine's own profiles have been taken onto
-- the board: that happens once, on the first pull with the switch on, and writes nothing.
-- paused_at / paused_reason: set when a pull finds the machine looks reset (none of the files
-- of the app's profiles is there any more). While set, a pull writes nothing; a person
-- resumes it (resume_pending then stops the very next pull judging the machine reset again).
--
-- sync_runs.summary_json is what a run did, in counts and lines the Sync page can show.
--
-- No transaction control in this file: the runner wraps it plus its ledger row in one.

CREATE TABLE profile_board (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    label              TEXT    NOT NULL,
    current_version_id INTEGER NOT NULL REFERENCES profile_versions(id),
    device_profile_id  TEXT,
    device_version_id  INTEGER REFERENCES profile_versions(id),
    on_home_screen     INTEGER NOT NULL DEFAULT 1 CHECK (on_home_screen IN (0, 1)),
    origin             TEXT    NOT NULL CHECK (origin IN ('adopted', 'draft')),
    failed_version_id  INTEGER REFERENCES profile_versions(id),
    pending_draft_id   INTEGER REFERENCES profile_drafts(id) ON DELETE SET NULL,
    pending_set_id     INTEGER REFERENCES sets(id) ON DELETE SET NULL,
    pending_major      INTEGER CHECK (pending_major IN (0, 1)),
    deleted_at         TEXT,
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_profile_board_version ON profile_board(current_version_id);
CREATE INDEX idx_profile_board_device ON profile_board(device_profile_id);

-- What the schema forbids, so two writers cannot make it true: a machine file is stood on by at
-- most one live row, and a draft waits on at most one live row. (Several live rows may share a
-- version: a machine can hold two identical files, and each is its own row. Deleted rows are
-- left out so a file can wait to be removed while its profile is put back.)
CREATE UNIQUE INDEX idx_profile_board_live_device ON profile_board(device_profile_id)
    WHERE deleted_at IS NULL AND device_profile_id IS NOT NULL;
CREATE UNIQUE INDEX idx_profile_board_live_pending ON profile_board(pending_draft_id)
    WHERE deleted_at IS NULL AND pending_draft_id IS NOT NULL;

CREATE TABLE profile_board_adoption (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    adopted_at TEXT    NOT NULL,
    host       TEXT    NOT NULL DEFAULT '',
    paused_at  TEXT,
    paused_reason TEXT,
    resume_pending INTEGER NOT NULL DEFAULT 0 CHECK (resume_pending IN (0, 1))
) STRICT;

ALTER TABLE sync_runs ADD COLUMN summary_json TEXT;
