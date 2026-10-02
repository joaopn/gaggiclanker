-- A draft remembers that it is a new profile, not an edit of one.
--
-- Every draft has a base version (`base_version_id` is NOT NULL: the diff view and the stale-base
-- warning read it), so a profile designed from scratch used to be stored as an edit of whatever
-- stood in for "nothing": the synthetic "Empty baseline" version when a Set was designed with no
-- profile to fork, or the library's most-used profile when the starting point authored one. The
-- Profiles page then listed every field as a change from it. `is_new` says what the base cannot:
-- the draft is a new profile, so it is shown as one and never lands on an existing board row.
-- The services that make such a draft set it (and a refinement keeps it); every other draft is 0.
--
-- Existing drafts are backfilled where the stored data says so: a draft whose base is the
-- synthetic empty baseline, found by the version's own row (that label, with the description the
-- app gave it) rather than the label alone, since a person's profile could be called the same.
-- The starting point's authored profiles are found by their notes, which are exactly
-- `Proposed by the starting-point wizard (<option>).` (a refinement of one keeps that as the first
-- line of its notes and adds more below it); its temperature redrafts of a library profile continue
-- past the closing bracket on the same line and stay edits.
--
-- A new draft has no stop-condition changes, and the list a draft stored was computed against the
-- stand-in base (the baseline's, or the library profile's stops). The derived list is therefore
-- rewritten to empty for every row this file marks new, so the card stops warning that "this changes
-- when the machine stops" and a put no longer asks for the acknowledgement of a change that was
-- never one.
--
-- The synthetic baseline also stops being a profile the archive "has": `v_profiles`, the view the
-- agent's SQL tool reads, is re-created without it (its text otherwise as it stood after 0029).
--
-- Nothing else is lost or rewritten. No transaction control in this file: the runner wraps it plus
-- its ledger row in one.

ALTER TABLE profile_drafts ADD COLUMN is_new INTEGER NOT NULL DEFAULT 0 CHECK (is_new IN (0, 1));

UPDATE profile_drafts
   SET is_new = 1
 WHERE base_version_id IN (
        SELECT id FROM profile_versions
         WHERE label = 'Empty baseline'
           AND json_extract(json, '$.description') =
               'An empty baseline, created because the archive held no profile to diff a new draft against.'
       );

UPDATE profile_drafts
   SET is_new = 1
 WHERE CASE WHEN instr(notes, char(10)) > 0
            THEN substr(notes, 1, instr(notes, char(10)) - 1)
            ELSE notes
       END IN (
        'Proposed by the starting-point wizard (conservative).',
        'Proposed by the starting-point wizard (recommended).',
        'Proposed by the starting-point wizard (adventurous).'
       );

UPDATE profile_drafts SET stop_condition_changes_json = '[]' WHERE is_new = 1;

DROP VIEW v_profiles;

CREATE VIEW v_profiles AS
SELECT pv.id AS profile_version_id,
       pv.label,
       pv.type,
       pv.utility,
       pv.source,
       pv.content_hash,
       pv.created_at,
       (SELECT COUNT(*) FROM shots s WHERE s.profile_version_id = pv.id) AS shot_count
  FROM profile_versions pv
 WHERE NOT (pv.label = 'Empty baseline'
            AND json_extract(pv.json, '$.description') =
                'An empty baseline, created because the archive held no profile to diff a new draft against.');
