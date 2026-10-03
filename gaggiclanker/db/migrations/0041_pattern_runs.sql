-- Find patterns across Sets: a run a person presses, and the general insights it proposes.
--
-- One structured model call reads the confirmed insights of every Set and proposes general
-- insights that several Sets say in different words. Nothing it writes is an insight: a run
-- writes its own row and its proposals, and only a person's Approve writes (and deletes)
-- anything else.
--
--   * pattern_runs: one row per press, opened `running` before the provider is contacted and
--     closed `done`, `failed` or (at the next boot) `interrupted`. input_json is what the model
--     was told, verbatim, so an old run stays explainable. The counts say what was read, what
--     was kept and what the post-filter dropped (dropped_json: {"reason": count}), so a model
--     that invents sources often is visible. At most one run is `running`: the partial unique
--     index is the guard behind the registry's, so a second press can never open a second row.
--
--   * pattern_proposals: what a finished run proposed, waiting for the person. sources_json
--     keeps each source insight's Set and text as the model was given it, so the card can still
--     say what went after Approve deleted the sources. replaces_id is an existing general
--     insight the proposal sharpens (SET NULL if it goes first), with its text as given.
--     insight_id is the general insight Approve wrote (SET NULL if that is deleted later) and
--     skipped_json names the sources Approve found gone or no longer confirmed. A
--     proposal quotes its sources as the model was given them, as does a run's input, so when
--     a later run finishes every earlier run's proposals are deleted and its input blanked:
--     copies of insight text live one run (the status `superseded` is kept in the CHECK but no
--     longer written).
--
--   * knowledge_insights.pattern_run_id: the run an approved general insight came from. A
--     column rather than a new `source` value, so the table is not rebuilt for its CHECK.
--
-- Nothing existing is rebuilt or lost.

CREATE TABLE pattern_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    status            TEXT    NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'done', 'failed', 'interrupted')),
    error             TEXT,
    provider          TEXT    NOT NULL DEFAULT '',
    model             TEXT    NOT NULL DEFAULT '',
    prompt_name       TEXT    NOT NULL DEFAULT '',
    prompt_version    TEXT    NOT NULL DEFAULT '',
    input_json        TEXT    NOT NULL DEFAULT '{}',
    insights_read     INTEGER NOT NULL DEFAULT 0,
    sets_read         INTEGER NOT NULL DEFAULT 0,
    proposals_kept    INTEGER NOT NULL DEFAULT 0,
    proposals_dropped INTEGER NOT NULL DEFAULT 0,
    dropped_json      TEXT    NOT NULL DEFAULT '{}',
    usage_json        TEXT,
    llm_call_id       TEXT,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at       TEXT
) STRICT;

CREATE UNIQUE INDEX idx_pattern_runs_running ON pattern_runs(status) WHERE status = 'running';

CREATE TABLE pattern_proposals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES pattern_runs(id) ON DELETE CASCADE,
    text          TEXT    NOT NULL,
    scope_json    TEXT    NOT NULL DEFAULT '{}',
    sources_json  TEXT    NOT NULL DEFAULT '[]',
    replaces_id   INTEGER REFERENCES knowledge_insights(id) ON DELETE SET NULL,
    replaces_text TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'proposed'
                  CHECK (status IN ('proposed', 'approved', 'dismissed', 'superseded')),
    insight_id    INTEGER REFERENCES knowledge_insights(id) ON DELETE SET NULL,
    skipped_json  TEXT    NOT NULL DEFAULT '[]',
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    decided_at    TEXT
) STRICT;

CREATE INDEX idx_pattern_proposals_run ON pattern_proposals(run_id, id);
CREATE INDEX idx_pattern_proposals_status ON pattern_proposals(status);

ALTER TABLE knowledge_insights
    ADD COLUMN pattern_run_id INTEGER REFERENCES pattern_runs(id) ON DELETE SET NULL;
