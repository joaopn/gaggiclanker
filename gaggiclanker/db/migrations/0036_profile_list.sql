-- The profile board becomes the profile list: every profile has an "on the machine" switch of
-- its own, apart from the home-screen star, and a list of the versions it has had.
--
--   * on_machine: whether a sync should put the profile on the machine (1) or take it off
--     (0). Independent of on_home_screen (shown as "Starred"), which only applies while the
--     profile is on the machine and is remembered while it is not. Every row that exists
--     today was on the board, so it starts on.
--   * profile_board_versions: the versions a profile has been, one row per (profile, version).
--     `source` says where the version came from: `agent` (a draft the agent proposed that a
--     person made active), `edit` (a draft a person made by hand), `machine` (taken from the
--     machine as it was), `edited_on_machine` (the file was changed on the display after the
--     app put it there), `import` (a file someone loaded). A row's current_version_id is its
--     active version and always one of them. A profile's other versions are what "make this
--     version active" chooses from.
--   * profile_list_build: one row once the list has been filled from everything the archive
--     already stores (old developing profiles, imports, discarded drafts, deleted rows). That is
--     a grouping SQL cannot do cleanly (versions are grouped by the files and drafts that link
--     them, then by label), so it is an idempotent step the application runs at boot, after
--     migrations, once: see `db/repos/profile_list.py`.
--
--   * conflict_overruled_hash: the content hash of a machine file a person chose to overrule
--     ("keep the app's version") when the file's content differed from everything the app knew.
--     The same content is not flagged as a conflict again; a further edit on the display is a new
--     one. NULL when nothing was overruled.
--
-- `origin` stays as information (where a profile came from); it no longer decides what a sync
-- may push or remove. Its CHECK stays: rebuilding the table would only restate it.
--
-- No data is lost. No transaction control in this file: the runner wraps it plus its ledger
-- row in one.

ALTER TABLE profile_board ADD COLUMN on_machine INTEGER NOT NULL DEFAULT 1
    CHECK (on_machine IN (0, 1));
ALTER TABLE profile_board ADD COLUMN conflict_overruled_hash TEXT;

CREATE TABLE profile_board_versions (
    board_id   INTEGER NOT NULL REFERENCES profile_board(id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL REFERENCES profile_versions(id),
    added_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    source     TEXT    NOT NULL
               CHECK (source IN ('agent', 'edit', 'machine', 'edited_on_machine', 'import')),
    PRIMARY KEY (board_id, version_id)
) STRICT;

CREATE INDEX idx_profile_board_versions_version ON profile_board_versions(version_id);

CREATE TABLE profile_list_build (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    built_at TEXT    NOT NULL
) STRICT;
