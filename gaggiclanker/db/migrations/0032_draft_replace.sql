-- A push now replaces the profile it supersedes on the machine, and a rollback puts it back.
-- The draft records what it did so a rollback does not have to guess.
--
--   * pushed_saved: 1 when this push actually saved a profile on the machine, 0 when it
--     found an identical one already there and used it. A rollback removes only a profile
--     its own push created: removing one a different push put there would take it away
--     from that push. Every push made before this migration saved (the old code never
--     looked for an existing copy), so the rows that already name a device profile are
--     backfilled to 1; a default of 0 would read them as "saved nothing" and a rollback
--     would then leave their profile on the machine and forget it.
--   * replaced_device_profile_id / replaced_version_id: the device id of the predecessor this
--     push removed from the machine, and the stored version holding the content the archive
--     recorded for it. NULL when the push left the predecessor alone or had none. A rollback
--     that finds them set brings that content back before removing the draft's copy. No
--     foreign key on the device id, like every device id: it names a file that is gone.
--   * cleared_set_version_ids_json: the Set versions that named the removed predecessor and
--     were cleared by the removal, so a rollback repoints exactly those and no others.
--   * recorded_version_id: the Set version this push recorded, when it recorded one. Stored
--     because two drafts that push identical content share one device id and can no longer
--     be told apart by it.
--   * replaced_by_draft_id: set on a pushed draft whose profile a later push removed. Its
--     profile is no longer on the machine, so it has nothing left to roll back.
--   * outcome_json: what the latest machine action on this draft did, in words a person
--     reads under the push or rollback button. Overwritten by the next action.
--
-- No transaction control in this file: the runner wraps it plus its ledger row in one.

ALTER TABLE profile_drafts ADD COLUMN pushed_saved INTEGER NOT NULL DEFAULT 0;
ALTER TABLE profile_drafts ADD COLUMN replaced_device_profile_id TEXT;
ALTER TABLE profile_drafts ADD COLUMN replaced_version_id INTEGER;
ALTER TABLE profile_drafts ADD COLUMN cleared_set_version_ids_json TEXT;
ALTER TABLE profile_drafts ADD COLUMN recorded_version_id INTEGER;
ALTER TABLE profile_drafts ADD COLUMN replaced_by_draft_id INTEGER;
ALTER TABLE profile_drafts ADD COLUMN outcome_json TEXT;

UPDATE profile_drafts SET pushed_saved = 1 WHERE pushed_device_profile_id IS NOT NULL;
