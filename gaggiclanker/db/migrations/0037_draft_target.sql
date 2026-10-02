-- A draft says which profile it was made for and who made it.
--
--   * target_board_id: the profile ("Edit a copy" was opened on one of its versions) the draft is
--     a new version of. A put of the draft continues that profile whichever of its versions was
--     edited, instead of working it out from the label, which only ever matched the version a
--     profile was on. NULL for a draft nobody aimed at a profile (the agent's, the starting-point
--     wizard's): its landing is still worked out by lineage.
--   * made_by: `edit` for a document a person typed into the editor, `agent` for everything a
--     model or a tool proposed. NULL for drafts made before this column; the list's fill reads
--     those with the old heuristic, for history only.
--
-- No data is lost and nothing is rewritten.

ALTER TABLE profile_drafts ADD COLUMN target_board_id INTEGER REFERENCES profile_board(id);
ALTER TABLE profile_drafts ADD COLUMN made_by TEXT CHECK (made_by IN ('agent', 'edit'));
