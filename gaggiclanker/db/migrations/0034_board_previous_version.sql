-- A board profile remembers the version it was before its newest one, so a person can go back.
--
-- `previous_version_id` is the version the row held until a put replaced it (see
-- `drafts/board.py`). Going back makes it the current version again and clears it: one step,
-- not a history (every version a profile has been is still in profile_versions, and the
-- drafts that made them are still on the Profiles page). NULL for a row nothing has replaced
-- yet, and for every row that existed before this column, which cannot go back until its next
-- put.
--
-- `back_from_set_id` is set by going back, for the one thing the next sync needs to know: the
-- Set whose current version recorded the version being left still names its profile (and the
-- file holding it), which would otherwise keep that file from being removed. It is cleared once
-- the sync has dealt with the file.
--
-- No data is lost or rewritten. No transaction control in this file: the runner wraps it plus
-- its ledger row in one.

ALTER TABLE profile_board ADD COLUMN previous_version_id INTEGER REFERENCES profile_versions(id);
ALTER TABLE profile_board ADD COLUMN back_from_set_id INTEGER REFERENCES sets(id) ON DELETE SET NULL;
