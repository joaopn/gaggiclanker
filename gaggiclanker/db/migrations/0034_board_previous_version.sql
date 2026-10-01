-- A board profile remembers the version it was before its newest one, so a person can go back.
--
-- `previous_version_id` is the version the row held until a put replaced it (see
-- `drafts/board.py`). Going back makes it the current version again and clears it: one step,
-- not a history (every version a profile has been is still in profile_versions, and the
-- drafts that made them are still on the Profiles page). NULL for a row nothing has replaced
-- yet, and for every row that existed before this column, which cannot go back until its next
-- put.
--
-- `back_from_version_id` and `back_from_set_version_id` are set by going back, for what the next
-- sync needs to know and cannot read from the profile alone: which version was left (so the file
-- holding it is removed as a going back, not as a delayed replacement), and the Set version that
-- was its Set's current one at the click and recorded the version being left. That Set still names
-- the file, which would otherwise keep it from being removed; it is exempt only while that exact
-- Set version is still the Set's current one, and both are cleared once the sync has dealt with the
-- file.
--
-- No data is lost or rewritten. No transaction control in this file: the runner wraps it plus
-- its ledger row in one.

ALTER TABLE profile_board ADD COLUMN previous_version_id INTEGER REFERENCES profile_versions(id);
ALTER TABLE profile_board ADD COLUMN back_from_version_id INTEGER REFERENCES profile_versions(id);
ALTER TABLE profile_board ADD COLUMN back_from_set_version_id INTEGER
    REFERENCES set_versions(id) ON DELETE SET NULL;
