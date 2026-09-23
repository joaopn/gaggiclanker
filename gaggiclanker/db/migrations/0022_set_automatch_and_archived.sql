-- A Set's two flags say what they mean.
--
-- `active` meant "this is what the machine is set up for right now", at most
-- one Set at a time (0005's comment, and 0016's `idx_sets_one_active`). That
-- reading assumes one hopper. A kitchen with several grinders has several
-- coffees loaded at once, so "which Set is *the* current one" has no answer,
-- and the one answer the column can still give is worth keeping: **this Set is
-- a candidate when a shot is filed by profile**. Any number of Sets may be. It
-- is renamed `automatch` for that, because three different things have now worn
-- the word "active" on this table and the fourth would be read as one of the
-- other three.
--
-- `status` was a two-value enum — 'active' or 'archived' — that will not gain a
-- third value, and its 'active' is not the flag's "active", which is how
-- `status = 'active' AND active = 1` came to be written in this codebase. It
-- becomes `archived`, the plain boolean `beans` has carried since 0002.
--
-- With `automatch` naming the candidates, the rule that filed a shot under the
-- one active Set has nothing left to read: profile matching is the only path
-- into a Set now, and `shotsProfileAutomatch`, which switched that matching on
-- for newly arrived shots, has nothing left to gate. The row goes with it.
--
-- This is an in-place change, not a table rebuild. Five tables reference
-- `sets(id)` with ON DELETE CASCADE and the connection runs with
-- `foreign_keys = ON`, which cannot be turned off inside the runner's
-- transaction: a create-copy-drop-rename would take every set version, chat
-- thread, proposal and starting-point run down with the dropped table. Both
-- columns are alterable in place once the partial index is gone.
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one, and SQLite's DDL is transactional.

-- The view is dropped first and re-created at the foot: it names both columns,
-- and SQLite re-parses every view in the schema at each ALTER, so one naming a
-- column mid-rename fails the *next* statement. `v_shots` and `v_set_versions`
-- also read `sets`, but only for its name and its bean, so they stand.
-- The view text is API: `describe_schema()` reads it out of the schema and the
-- SQL tool's allow-list is exactly these names.
DROP VIEW v_sets;

-- "One active Set, full stop" is the invariant this whole migration exists to
-- retire. It comes down before anything sets the flag on a second row.
DROP INDEX idx_sets_one_active;

-- ── status becomes archived ──────────────────────────────────────────
ALTER TABLE sets ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;

UPDATE sets SET archived = CASE status WHEN 'archived' THEN 1 ELSE 0 END;

ALTER TABLE sets DROP COLUMN status;

-- ── active becomes automatch ─────────────────────────────────────────
--
-- Every Set that is not archived becomes a candidate, not just the one that
-- held the flag. That is what this database already did whenever
-- `shotsProfileAutomatch` was on — the matcher's candidates were every
-- non-archived Set — so carrying only the old active Set over would quietly
-- stop the others matching. An archived Set receives no shots, so it is never a
-- candidate; `archived` is authoritative and the flag is cleared under it.
UPDATE sets SET active = CASE WHEN archived = 1 THEN 0 ELSE 1 END;

ALTER TABLE sets RENAME COLUMN active TO automatch;

-- The column keeps `DEFAULT 0` from 0005: every insert names it (the Sets
-- repository passes the create form's answer, on by default), and changing a
-- default is the one thing here that would need a rebuild.

-- ── the setting the flag replaces ────────────────────────────────────
--
-- `shotsProfileAutomatch` switched profile matching on for newly pulled and
-- imported shots. Matching is now what the archive does, and `automatch` on the
-- Set is the opt-in that replaced the switch. Deleted rather than left behind
-- for 0018's reason: a stored value must not be picked up by a later setting
-- that happens to reuse the name. A backup restored from before this migration
-- has no ledger row for it, so the delete runs again at that boot.
DELETE FROM settings WHERE key = 'shotsProfileAutomatch';

-- ── the view comes back ──────────────────────────────────────────────
--
-- 0016's definition with the two columns renamed. Everything else is verbatim.
CREATE VIEW v_sets AS
SELECT st.id AS set_id,
       st.name,
       st.archived,
       st.automatch,
       st.bean_id,
       b.name AS bean_name,
       b.roaster,
       b.roast_level,
       b.process,
       b.origin,
       st.grinder_id,
       g.name AS grinder_name,
       g.step_unit AS grinder_step_unit,
       st.created_at,
       (SELECT COUNT(*) FROM set_versions v WHERE v.set_id = st.id) AS version_count,
       (SELECT COUNT(*) FROM shots s
          JOIN set_versions v ON v.id = s.set_version_id
         WHERE v.set_id = st.id) AS shot_count
  FROM sets st
  LEFT JOIN beans b    ON b.id = st.bean_id
  LEFT JOIN grinders g ON g.id = st.grinder_id;
