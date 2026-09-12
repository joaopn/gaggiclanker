-- 0006_analysis: what the model was told, what it said, and what you did about it.
--
-- 0005 recorded what the coffee was and what the person thought of it. This
-- migration adds the third voice: one structured LLM call per shot that reads
-- the diagnostics, the Set, the trajectory of earlier shots and a tier of
-- knowledge rules, and answers with a diagnosis and prioritised suggestions.
--
-- Three tables, and the split between them is the design:
--
--   * `knowledge_rules` is the *input* nobody wants to re-type into a prompt:
--     machine-readable dial-in heuristics with the conditions they apply under.
--     Seeded from a file and editable in place, exactly like `prompts`.
--   * `shot_analyses` is one run. It stores the rendered input as well as the
--     output, because a prompt that has since been edited would otherwise make
--     last month's analysis unexplainable.
--   * `suggestions` is the actionable part, lifted out of the output document
--     so it can be accepted, rejected and counted. Accepting one writes a
--     `set_versions` row with `origin = 'analysis'` — which is the join that
--     answers "did following the model's advice actually help".
--
-- Everything is STRICT and timestamps are the same ISO-8601 UTC strings the
-- rest of the schema uses, so ORDER BY on a text column is chronological.

-- ── knowledge rules ──────────────────────────────────────────────────
--
-- Tier 1 of the knowledge base: small,
-- structured, queryable facts rather than prose. The analyzer receives the
-- matching ones verbatim, the Knowledge page lists them, and a later chat can
-- query them — one representation, three readers.
--
-- `applies_json` is the condition: an object of `{roast_level, process, style,
-- burr_type, signal}` lists, where an absent key means "does not care". A JSON
-- document rather than five columns because the dimensions differ per category
-- (a pressure-matrix cell is roast x process, a band meaning is keyed on a
-- diagnostics signal) and five mostly-NULL columns would model none of them
-- well. Selection is in Python over a small table — a few hundred rows at most
-- — so there is nothing here for an index to do.
--
-- `value_json` is the fact itself. The reserved key `text` inside it is the
-- one-sentence human form, which is what the prompt renders and the UI shows;
-- everything else in the object is the machine-readable version of the same
-- fact. Keeping the sentence *in* the value rather than in its own column is
-- what lets an edited rule change both at once, and it is why editing a rule
-- is one PATCH of one document.
--
-- `confidence` and `source` carry provenance. The seed's heuristics are
-- gaggimate-barista's (Charlie Hall, MIT) by way of gaggimate-mcp; the
-- diagnostic band meanings are calibrated against real shots in this
-- repository's own fixtures. A rule that misleads is findable because the model
-- is asked which ones it used.
--
-- `enabled` rather than DELETE: turning a rule off is a thing a user does to
-- see what changes, and a deleted row would come straight back at the next boot
-- when the seed re-runs.
CREATE TABLE knowledge_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    applies_json TEXT   NOT NULL DEFAULT '{}',
    value_json  TEXT    NOT NULL,
    unit        TEXT    NOT NULL DEFAULT '',
    confidence  TEXT    NOT NULL DEFAULT 'expert'
                CHECK (confidence IN ('expert', 'calibrated', 'anecdotal', 'learned')),
    source      TEXT    NOT NULL DEFAULT '',
    source_ref  TEXT    NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    -- The shipped text, so the seeding rules can tell "the file changed" from
    -- "the user edited this" — the same three-way upsert `prompts` uses.
    default_json TEXT   NOT NULL DEFAULT '{}',
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    -- The natural key. `key` alone would collide across categories the day a
    -- band meaning and a taste mapping both want to be called 'sour'.
    UNIQUE (category, key)
) STRICT;

CREATE INDEX idx_knowledge_rules_enabled ON knowledge_rules(enabled, category, key);

-- ── shot analyses ────────────────────────────────────────────────────
--
-- One row per run, inserted `running` *before* the provider is contacted and
-- closed afterwards. That order is the reason a crashed process leaves evidence
-- rather than silence: boot reconciliation turns every `running` row into
-- `interrupted`, so "the container restarted mid-analysis" is a state on the
-- page instead of a spinner that never stops.
--
-- **A failed analysis is a stored row, not an exception.** The acceptance
-- criterion for this chunk is that a provider failure surfaces as a `failed`
-- row carrying the error, and `run_analysis` never raises for anything the
-- provider did — the LLM layer already returns failures as values
-- (gaggiclanker/llm/types.py), and this table is where they come to rest.
--
-- `input_json` is the rendered context, not a reference to it. The prompt text,
-- the rules and the Set version can all change afterwards; without the snapshot
-- an old analysis would be explained by today's inputs, which is worse than not
-- explaining it at all.
--
-- `set_version_id` is copied from the shot at analysis time rather than joined
-- through it: re-assigning a shot to a different Set must not silently rewrite
-- what an analysis was told. No foreign key on it for the same reason
-- `shots.set_version_id` has none (migration 0005).
--
-- `llm_call_id` points at the `llm_calls` ledger row, which is where the
-- rendered prompt and the raw reply live when `llmStoreCallText` is on. TEXT
-- and unconstrained: the ledger is a rolling record and an analysis outliving
-- its call row is expected.
CREATE TABLE shot_analyses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    shot_id        INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    set_version_id INTEGER,
    provider       TEXT    NOT NULL DEFAULT '',
    model          TEXT    NOT NULL DEFAULT '',
    prompt_name    TEXT    NOT NULL DEFAULT '',
    prompt_version TEXT    NOT NULL DEFAULT '',
    input_json     TEXT    NOT NULL DEFAULT '{}',
    output_json    TEXT,
    usage_json     TEXT,
    -- Nullable and stays NULL for now: nothing in the prototype knows what a
    -- token costs on the provider you happen to be using, and a made-up figure
    -- in a money column is worse than an empty one.
    cost_estimate  REAL,
    status         TEXT    NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'ok', 'failed', 'interrupted')),
    -- The LLM layer's own error code plus its message, for a row a person has
    -- to act on: `auth` and `rate_limited` mean "go and fix something".
    error          TEXT,
    llm_call_id    TEXT,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at    TEXT
) STRICT;

-- Two queries walk this table by shot, and the index is keyed on `id` rather
-- than `created_at` so that *both* are satisfied by it. The shots list carries
-- an `analysis_state` column whose correlated subquery takes the newest row per
-- shot; with a `created_at DESC` key the planner has to sort to break ties on
-- id, and `tests/sync/test_resilience.py` — which fails the build on a TEMP
-- B-TREE anywhere in the list plan — catches it. Ids are monotonic within a
-- shot, so "highest id" and "newest" are the same row.
CREATE INDEX idx_shot_analyses_shot ON shot_analyses(shot_id, id DESC);
-- Boot reconciliation's query, and the "is anything running" badge.
CREATE INDEX idx_shot_analyses_status ON shot_analyses(status, created_at DESC);

-- ── suggestions ──────────────────────────────────────────────────────
--
-- The one part of an analysis that has a button next to it. Lifted out of
-- `output_json` into rows because a suggestion has a *life*: it is open, then
-- accepted or rejected, or overtaken by a later one for the same variable.
-- None of that fits inside an immutable output document.
--
-- `variable` is closed, and the closure is what makes accepting one safe: the
-- four actionable values map onto exactly four columns of `set_versions`, and
-- everything else is recorded, shown, and refused by the accept route with a
-- message saying why (the prototype writes nothing to the device).
--
-- `direction` and `magnitude` are kept apart rather than folded into one signed
-- number, because "two steps finer" is how a person says it and "-2" is not;
-- the sign is applied in one place (the accept path) where the grinder's
-- convention is written down.
--
-- `resulting_set_version_id` closes the loop: an accepted suggestion names the
-- version it produced, and that version names the analysis it came from
-- (`set_versions.origin_analysis_id`). Both directions exist because both
-- questions get asked — "what did this suggestion do" from the shot page, and
-- "why is this version here" from the Set page.
CREATE TABLE suggestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES shot_analyses(id) ON DELETE CASCADE,
    variable    TEXT    NOT NULL CHECK (variable IN
                  ('grind', 'dose', 'yield', 'temperature', 'pressure',
                   'flow', 'preinfusion', 'puck_prep', 'profile')),
    direction   TEXT    NOT NULL DEFAULT '',
    magnitude   REAL,
    unit        TEXT    NOT NULL DEFAULT 'none',
    reason      TEXT    NOT NULL DEFAULT '',
    confidence  TEXT    NOT NULL DEFAULT '',
    -- 1 first. Stored as the model gave it so the order it *meant* survives,
    -- and the UI sorts on it rather than on the array index.
    priority    INTEGER NOT NULL DEFAULT 1,
    status      TEXT    NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'accepted', 'rejected', 'superseded')),
    resulting_set_version_id INTEGER REFERENCES set_versions(id),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_suggestions_analysis ON suggestions(analysis_id, priority);
-- "What is still open for this variable" — the query the accept path runs to
-- supersede the siblings, and the Set page runs to list outstanding advice.
CREATE INDEX idx_suggestions_status ON suggestions(status, variable);
