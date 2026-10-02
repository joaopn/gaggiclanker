-- A draft says who made it.
--
--   * made_by: `edit` for a document a person typed into the editor, `agent` for everything a
--     model or a tool proposed. It becomes the source of the version a proposal makes (shown
--     on the profile's version list). NULL for drafts made before this column; the list's fill
--     reads those with the old heuristic, for history only.
--
-- No data is lost and nothing is rewritten.

ALTER TABLE profile_drafts ADD COLUMN made_by TEXT CHECK (made_by IN ('agent', 'edit'));
