-- 0005_sets: what the coffee was, and what the person thought of it.
--
-- 0002 archived what the *machine* did. This migration adds the half the
-- machine has no idea about: which beans were in the hopper, which grinder
-- ground them, what the user was trying when they pulled the shot, and whether
-- it was any good.
--
-- The organising idea is the **Set**. A
-- Set is a stable identity — "Ethiopia natural on the Niche with Adaptive v2" —
-- and a `set_versions` row is one concrete tuple that was actually brewed with:
-- a profile version, a grind, a dose, a target yield, a temperature, and the
-- one sentence saying *why* this differs from the version before it. Changing
-- anything creates a new version pointing at its parent, so a Set's history is
-- a linked list that the analyzer and the chat can walk backwards
-- from any shot.
--
-- Why versions rather than editing a row in place: the whole product is "what
-- did changing this do", and an edited row answers that question with the
-- current value for every shot ever attached to it. A version is immutable once
-- shots hang off it, which is what makes a trend chart mean anything.
--
-- Everything is STRICT and timestamps are the same ISO-8601 UTC strings 0002
-- uses, so ORDER BY on a text column is chronological.

-- ── beans ────────────────────────────────────────────────────────────
--
-- Closed vocabularies for `process` and `roast_level` because they are the two
-- fields the knowledge tier reasons over (temperature by roast, pressure by
-- roast × process); free text there would make every rule lookup a fuzzy match.
-- Both are nullable: a bag whose roaster printed neither is still a bag, and a
-- NULL is an honest "not stated" where 'other' would be a claim.
--
-- `archived` is how a finished bean is retired: it is still the bean a year of
-- shots was pulled with. A delete is only for a bean nobody used, and
-- `sets.bean_id` below has no ON DELETE, so a bean a Set points at cannot go.
CREATE TABLE beans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    roaster           TEXT,
    origin            TEXT,
    altitude_m        INTEGER,
    process           TEXT    CHECK (process IS NULL OR process IN
                              ('washed', 'natural', 'honey', 'anaerobic', 'other')),
    roast_level       TEXT    CHECK (roast_level IS NULL OR roast_level IN
                              ('light', 'medium-light', 'medium', 'medium-dark', 'dark')),
    -- A date, 'YYYY-MM-DD'. Days off roast is the only freshness signal there
    -- is, and it is the difference between "this bag needs three more days" and
    -- "grind finer".
    roast_date        TEXT,
    decaf             INTEGER NOT NULL DEFAULT 0,
    -- A free-form description of the coffee in the person's words: what the
    -- bag or the roaster says, tasting notes, anything worth knowing about the
    -- bean. The prompts get it as written, not as a verified fact.
    description       TEXT    NOT NULL DEFAULT '',
    notes             TEXT    NOT NULL DEFAULT '',
    archived          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_beans_archived ON beans(archived, name);

-- ── grinders ─────────────────────────────────────────────────────────
--
-- `step_unit` exists so that advice can be given in the grinder's *own* units.
-- "Go two clicks finer" is actionable on a Niche; "go 15 microns finer" is not,
-- and the analyzer prompt is handed this field precisely so it stops
-- inventing a scale.
--
-- `burr_type` because conical and flat burrs want different profiles, which is
-- one of the knowledge tier's own rules.
CREATE TABLE grinders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    model      TEXT,
    burr_type  TEXT    NOT NULL DEFAULT 'unknown'
               CHECK (burr_type IN ('conical', 'flat', 'unknown')),
    step_unit  TEXT    NOT NULL DEFAULT 'clicks'
               CHECK (step_unit IN ('clicks', 'numbers', 'microns', 'free')),
    notes      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- ── sets ─────────────────────────────────────────────────────────────
--
-- Two orthogonal flags, and conflating them is the mistake this comment exists
-- to prevent:
--
--   * `status` is the Set's **lifecycle**. 'archived' means finished with —
--     the bag is gone — and an archived Set never receives another shot.
--   * `active` is "this is what the machine is set up for **right now**".
--     Exactly one Set per machine may hold it (the partial unique index below),
--     and it is what auto-assignment consults when a shot lands.
--
-- Activating a Set therefore *switches* the flag and archives nothing: swapping
-- between two bags over a week is an ordinary morning, and an activate that
-- retired the other Set would make going back a new Set with no history.
CREATE TABLE sets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    bean_id    INTEGER NOT NULL REFERENCES beans(id),
    machine_id INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
    grinder_id INTEGER REFERENCES grinders(id),
    status     TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    active     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- One active Set per machine, enforced by the schema rather than by whoever
-- remembers to clear the old one. A partial index, so the many inactive Sets
-- do not all collide on the same machine_id.
CREATE UNIQUE INDEX idx_sets_one_active_per_machine ON sets(machine_id) WHERE active = 1;
CREATE INDEX idx_sets_machine ON sets(machine_id, status);
CREATE INDEX idx_sets_bean ON sets(bean_id);

-- ── set versions ─────────────────────────────────────────────────────
--
-- Immutable once created. `parent_version_id` is what makes the history a
-- trajectory rather than a list: the diff shown in the UI, and the "what did
-- you change and did it help" the analyzer needs, are both computed against the
-- parent rather than stored.
--
-- `origin` says who proposed this version — the user ('manual'), an accepted
-- analysis suggestion ('analysis', with `origin_analysis_id` naming it),
-- or the chat ('chat'). It is on the row rather than inferred so that a year
-- later "did following the model's advice help" is a GROUP BY.
--
-- `grind_setting` is text and `grind_value` is a number, deliberately both: a
-- Niche says "22", an EK43 says "7.5", a Mazzer says "between 3 and 4". The
-- text is what the user reads back and what the analyzer is shown; the number
-- is what a chart can plot, when there is one.
CREATE TABLE set_versions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id               INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    version_no           INTEGER NOT NULL,
    parent_version_id    INTEGER REFERENCES set_versions(id),
    profile_version_id   INTEGER REFERENCES profile_versions(id),
    grind_setting        TEXT,
    grind_value          REAL,
    dose_g               REAL,
    target_yield_g       REAL,
    target_temperature_c REAL,
    -- One sentence: what this version is trying to find out. Empty is allowed
    -- (the first version of a Set is not trying anything yet), but the UI asks
    -- for it on every subsequent version, because a change with no stated
    -- intent is indistinguishable from a typo three weeks later.
    intent               TEXT    NOT NULL DEFAULT '',
    origin               TEXT    NOT NULL DEFAULT 'manual'
                         CHECK (origin IN ('manual', 'analysis', 'chat')),
    origin_analysis_id   INTEGER,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    UNIQUE (set_id, version_no)
) STRICT;

CREATE INDEX idx_set_versions_parent ON set_versions(parent_version_id);
CREATE INDEX idx_set_versions_profile ON set_versions(profile_version_id);

-- `shots.set_version_id` was created in 0002 without a foreign key, because
-- this table did not exist yet. SQLite cannot add one to an existing column
-- without rebuilding the table, and rebuilding `shots` means rewriting every
-- `raw_slog` blob in the archive — the one table whose bytes are the product.
-- The reference is therefore enforced in `SetsRepository` (which is the only
-- code that writes the column) and a Set version is never deleted: a Set is
-- archived, and its versions stay. This index is what the "shots in this Set"
-- and "needs a Set" queries walk.
CREATE INDEX idx_shots_set_version ON shots(set_version_id);

-- ── judgement ────────────────────────────────────────────────────────
--
-- The user's verdict on the cup, which is a different measurement from the
-- execution score and must never be folded into it: a flawlessly executed shot
-- of stale beans scores well and tastes of cardboard, and an archive that
-- averaged the two could not tell you that.
--
-- One row per shot, so the primary key *is* the shot id. `taste_tags_json` is a
-- JSON array of tag slugs from `gaggiclanker/domain/vocab.py`; it is a
-- serialised list rather than a join table because it is never queried by tag
-- alone — it is read with the shot, and shown with it.
--
-- `seeded_from_device_note` is the flag that keeps sync honest. The machine's
-- own notes card is the same data, so the first time a shot arrives with notes
-- and no judgement we copy them across and set this to 1. It is never used to
-- decide whether to *overwrite*: any later user edit clears it, and sync only
-- ever creates a judgement that does not exist. Losing a typed verdict to a
-- re-pull would be unforgivable, and that is a one-line rule in one place.
CREATE TABLE shot_judgements (
    shot_id                 INTEGER PRIMARY KEY REFERENCES shots(id) ON DELETE CASCADE,
    -- 1..5, or NULL for "judged but not scored". The device writes 0 for
    -- "unrated", which is translated to NULL on the way in: 0 is a number
    -- somebody would average.
    rating                  INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    balance                 TEXT    CHECK (balance IS NULL OR balance IN
                                    ('sour', 'balanced', 'bitter')),
    taste_tags_json         TEXT    NOT NULL DEFAULT '[]',
    dose_in_g               REAL,
    dose_out_g              REAL,
    grind_setting           TEXT,
    -- Capped at 200 characters by the pydantic model, matching the firmware's
    -- own limit, so a judgement stays writable back to the device if a later feature
    -- ever enables that.
    notes                   TEXT    NOT NULL DEFAULT '',
    decision                TEXT    CHECK (decision IS NULL OR decision IN
                                    ('keep', 'adjust', 'discard')),
    seeded_from_device_note INTEGER NOT NULL DEFAULT 0,
    -- When this judgement was last reconciled with the device's notes card.
    -- Set by the seeding path; NULL on a judgement typed here.
    device_synced_at        TEXT,
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_shot_judgements_rating ON shot_judgements(rating);
