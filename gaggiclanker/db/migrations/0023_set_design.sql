-- A Set can be designed in its own conversation before it has a recipe.
--
-- The design path starts from a bean and a grinder, creates the Set with a
-- version 1 that states nothing yet, and lets the chat work out that recipe
-- with the person. Accepting the agent's answer writes it onto that same
-- version 1 rather than appending a v2 after an empty one: a Set's history
-- starts with the recipe that was brewed.
--
--   * `sets.designing` — the Set is being designed. An explicit flag, never
--     inferred: "version 1 names no profile and has no shots" also describes a
--     hand-made Set that deliberately names no profile, and inferring design
--     mode from it would switch that Set's conversation to the design prompt.
--     Set only by the design route; cleared by the first version written to
--     the Set, whichever path writes it.
--   * `sets.design_brief` — what the person asked for (the profile to fork,
--     their usual grind, the goal in their words), as JSON validated by the
--     repository's model on the way in and out. Read on every turn so it
--     survives a long conversation, and kept after the design is done as the
--     record of what was asked for.
--   * `set_version_proposals.kind` — `change` for the ordinary one-change
--     proposal, `design` for the initial recipe. The values are the pydantic
--     model's to check, not a CHECK's: adding one to the constraint later
--     would mean rebuilding the table.
--   * `set_version_proposals.draft_id` — the profile draft an initial recipe
--     carries. `ON DELETE SET NULL`: a proposal is the record of what was
--     proposed and outlives the draft row.
--
-- Columns only, no table rebuild. Five tables reference `sets(id)` with
-- ON DELETE CASCADE and the connection runs with foreign keys on, which cannot
-- be turned off inside the runner's transaction (see 0022): a
-- create-copy-drop-rename would take every version, thread and proposal down
-- with the dropped table. Every existing row takes the default, which is what
-- it already was — not being designed, an empty brief, an ordinary change —
-- so nothing is lost and no database needs resetting.
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one, and SQLite's DDL is transactional.

ALTER TABLE sets ADD COLUMN designing INTEGER NOT NULL DEFAULT 0;

ALTER TABLE sets ADD COLUMN design_brief TEXT NOT NULL DEFAULT '{}';

ALTER TABLE set_version_proposals ADD COLUMN kind TEXT NOT NULL DEFAULT 'change';

-- A foreign key may be added by ALTER only with a NULL default, which is the
-- default this column wants anyway: an ordinary change carries no draft.
ALTER TABLE set_version_proposals
    ADD COLUMN draft_id INTEGER REFERENCES profile_drafts(id) ON DELETE SET NULL;
