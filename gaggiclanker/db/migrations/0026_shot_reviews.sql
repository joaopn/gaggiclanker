-- The per-shot analysis becomes Review: a passive reading of one shot.
--
-- A review is one structured model call about one shot, started only by a
-- person's button on the shot page. It reads the shot's own information (never
-- the person's judgement, the Set, its versions or any other shot) and writes
-- three things back: a blind taste prediction, a description of what the
-- telemetry shows, and a one-sentence summary. It proposes nothing, so it has
-- no suggestions, no profile patches and no insights, and the tables that held
-- those for the analysis go.
--
-- Carried across: every finished analysis (`status = 'ok'`) becomes a review
-- with the same id, its taste prediction as it was and its diagnosis as the
-- description, with an empty summary. The same id means an old pointer to the
-- analysis (a Set version's `origin_analysis_id`, a profile draft's
-- `source_analysis_id`, an insight's `analysis_id`) still names the reading of
-- that same shot. A taste value outside today's closed vocabulary (the old
-- body and confidence were free text) is carried as NULL rather than refused.
-- Failed and interrupted analyses carry nothing a page could show, and are
-- dropped with their table.
--
-- Dropped: `suggestions` (and with it every accepted, rejected or open piece of
-- advice), `shot_analyses`, the views over both, the analysis prompts (a
-- person's edited copy of them included). The stored overrides of the two
-- analysis settings move: `modelAnalysis` to `modelReview`, and
-- `analysisChunkTokenBudget` to `knowledgeChunkTokenBudget` (the starting point
-- has always read it too, so it is named for what it limits).
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one, and SQLite's DDL is transactional.

-- ── the reviews ─────────────────────────────────────────────────────
--
-- One row per run, inserted `running` before the provider is contacted and
-- closed when it answers, whichever way it answered, exactly as the analysis
-- was: a crashed process leaves a `running` row that the next boot marks
-- `interrupted`, and a provider failure is a `failed` row carrying the error.
-- A shot may have many; the newest `ok` one is the one served and rendered,
-- and the earlier ones stay for traceability.
--
-- `input_json` is what the model was told, verbatim, so a review stays
-- explainable after the shot's information, the prompt and the knowledge base
-- have all moved on. `rules_used_json` and `excerpts_used_json` are the
-- citations, already checked against what was given.
CREATE TABLE shot_reviews (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    shot_id            INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    status             TEXT    NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'ok', 'failed', 'interrupted')),
    -- The LLM layer's own error code plus its message, for a row a person has
    -- to act on.
    error              TEXT,
    provider           TEXT    NOT NULL DEFAULT '',
    model              TEXT    NOT NULL DEFAULT '',
    prompt_name        TEXT    NOT NULL DEFAULT '',
    prompt_version     TEXT    NOT NULL DEFAULT '',
    input_json         TEXT    NOT NULL DEFAULT '{}',
    taste_balance      TEXT    CHECK (taste_balance IN ('sour', 'balanced', 'bitter')),
    taste_body         TEXT    CHECK (taste_body IN ('thin', 'medium', 'heavy')),
    taste_confidence   TEXT    CHECK (taste_confidence IN ('low', 'medium', 'high')),
    description        TEXT,
    summary            TEXT,
    rules_used_json    TEXT    NOT NULL DEFAULT '[]',
    excerpts_used_json TEXT    NOT NULL DEFAULT '[]',
    usage_json         TEXT,
    -- The `llm_calls` ledger row, where the rendered prompt and the raw reply
    -- live. Unconstrained: the ledger is a rolling record.
    llm_call_id        TEXT,
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at        TEXT
) STRICT;

-- The newest review of a shot, and the newest finished one: both walk this.
CREATE INDEX idx_shot_reviews_shot ON shot_reviews(shot_id, id DESC);
-- Boot reconciliation's query.
CREATE INDEX idx_shot_reviews_status ON shot_reviews(status);

INSERT INTO shot_reviews
    (id, shot_id, status, provider, model, prompt_name, prompt_version, input_json,
     taste_balance, taste_body, taste_confidence, description, summary,
     rules_used_json, excerpts_used_json, usage_json, llm_call_id, created_at, finished_at)
SELECT a.id,
       a.shot_id,
       'ok',
       a.provider,
       a.model,
       a.prompt_name,
       a.prompt_version,
       a.input_json,
       CASE WHEN json_extract(a.output_json, '$.taste_prediction.balance')
                 IN ('sour', 'balanced', 'bitter')
            THEN json_extract(a.output_json, '$.taste_prediction.balance') END,
       CASE WHEN json_extract(a.output_json, '$.taste_prediction.body')
                 IN ('thin', 'medium', 'heavy')
            THEN json_extract(a.output_json, '$.taste_prediction.body') END,
       CASE WHEN json_extract(a.output_json, '$.taste_prediction.confidence')
                 IN ('low', 'medium', 'high')
            THEN json_extract(a.output_json, '$.taste_prediction.confidence') END,
       CASE WHEN json_type(a.output_json, '$.diagnosis') = 'text'
            THEN json_extract(a.output_json, '$.diagnosis') ELSE '' END,
       '',
       -- Only the string entries: a citation list is read back as strings, and
       -- one odd element must not make the whole row unreadable.
       (SELECT json_group_array(c.value)
          FROM json_each(a.output_json, '$.rules_used') c
         WHERE c.type = 'text'),
       (SELECT json_group_array(c.value)
          FROM json_each(a.output_json, '$.excerpts_used') c
         WHERE c.type = 'text'),
       CASE WHEN json_valid(a.usage_json) THEN a.usage_json END,
       a.llm_call_id,
       a.created_at,
       a.finished_at
  FROM shot_analyses a
 WHERE a.status = 'ok'
   AND a.output_json IS NOT NULL
   AND json_valid(a.output_json)
   AND json_type(a.output_json) = 'object';

-- ── the insights keep their link, without the foreign key ────────────
--
-- `knowledge_insights.analysis_id` referenced `shot_analyses`, so the table
-- cannot outlive it as declared (the implicit delete inside DROP TABLE would
-- null every link first). Rebuilt with the same columns and rows and the
-- column as a plain integer: an insight an analysis proposed still says so,
-- through `source = 'analysis'`, and still names the reading it came from.
-- Nothing references this table, so the swap has no children to stash.
CREATE TABLE knowledge_insights_rebuilt (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_json             TEXT    NOT NULL DEFAULT '{}',
    text                   TEXT    NOT NULL,
    evidence_shot_ids_json TEXT    NOT NULL DEFAULT '[]',
    source                 TEXT    NOT NULL DEFAULT 'user'
                           CHECK (source IN ('analysis', 'chat', 'user')),
    -- The analysis that proposed it, when one did: the carried review of the
    -- same id, or a number that names nothing when that analysis never finished.
    analysis_id            INTEGER,
    confirmed              INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    created_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    confirmed_at           TEXT
) STRICT;

INSERT INTO knowledge_insights_rebuilt
    (id, scope_json, text, evidence_shot_ids_json, source, analysis_id, confirmed,
     created_at, updated_at, confirmed_at)
SELECT id, scope_json, text, evidence_shot_ids_json, source, analysis_id, confirmed,
       created_at, updated_at, confirmed_at
  FROM knowledge_insights;

DROP TABLE knowledge_insights;
ALTER TABLE knowledge_insights_rebuilt RENAME TO knowledge_insights;

CREATE INDEX idx_knowledge_insights_confirmed
    ON knowledge_insights(confirmed, created_at, id);

-- ── what goes ───────────────────────────────────────────────────────
--
-- The views first: a view over a dropped table breaks every later schema
-- change, the rename above included.
DROP VIEW v_suggestions;
DROP VIEW v_analyses;
DROP TABLE suggestions;
DROP TABLE shot_analyses;

-- The analysis prompts. The seeder never deletes a row whose file has gone, so
-- without this they would sit on the Prompts page editing nothing.
DELETE FROM prompts
WHERE name IN ('analysis', 'analysis-user');

-- The two analysis settings, under their new names. A value already stored
-- under the new name wins; the old key is gone either way.
INSERT OR IGNORE INTO settings (key, value, updated_at)
SELECT 'modelReview', value, updated_at FROM settings WHERE key = 'modelAnalysis';
INSERT OR IGNORE INTO settings (key, value, updated_at)
SELECT 'knowledgeChunkTokenBudget', value, updated_at
  FROM settings WHERE key = 'analysisChunkTokenBudget';
DELETE FROM settings
WHERE key IN ('modelAnalysis', 'analysisChunkTokenBudget');

-- ── what the SQL tool reads ─────────────────────────────────────────
--
-- `input_json` is omitted for the reason `v_analyses` omitted it: it is the
-- whole rendered shot, tens of kilobytes a row, and a `SELECT *` over it would
-- spend the row cap's budget on text the tools already serve.
CREATE VIEW v_reviews AS
SELECT r.id AS review_id,
       r.shot_id,
       r.status,
       r.error,
       r.provider,
       r.model,
       r.taste_balance,
       r.taste_body,
       r.taste_confidence,
       r.description,
       r.summary,
       r.rules_used_json,
       r.excerpts_used_json,
       r.created_at,
       r.finished_at
  FROM shot_reviews r;
