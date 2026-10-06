-- The per-shot review becomes a reading: claims tied to a window of the shot, each with the numbers
-- the server worked out, answered one by one by a person.
--
-- Breaking, on purpose: every stored review is deleted. They hold a blind taste prediction (retired:
-- taste stays the person's), a description and a summary, no claims, and nothing a person wrote, so
-- there is nothing to carry into the new shape and nothing to render read-only beside it. A person's
-- edited copy of the two review prompts goes with them: the output the old prompts ask for no longer
-- fits what the reading is validated against, and the seeder puts the new files back at boot.
--
-- What a reading writes, and why it is two tables rather than a document column:
--
-- * `shot_reviews` is the run, as before: opened `running` before the provider is contacted, closed
--   when it answers, its input snapshotted. It loses the taste columns and the description, and gains
--   `prediction_given`, the text of the Set version's prediction the model was shown (empty when
--   there was none), so a reading stays explainable after the version is read differently.
-- * `review_claims` is what it said, one row per statement. A `claim` is about a window; a
--   `free_text` row answers one free-text expectation of the confirmed signature (`expectation_id`
--   is the id it was answered under, a plain integer: the expectation may be re-proposed or removed
--   later, and a result counts only while the id is still confirmed); a `prediction` row says how
--   the shot moved against the version's prediction. Every one starts `proposed` and a person makes
--   it `confirmed` or `rejected`. `evidence_json` holds what the server evaluated for the claim (never
--   what the model typed), `supported` says whether those numbers bear it out.
--
-- A shot may be read again, so a shot has many reviews and only the newest finished one answers.
-- One of them may be running, which the partial unique index below turns from a convention into a
-- fact the database enforces for the opening transaction.
--
-- Nothing references `shot_reviews` but `review_claims` (new) and the views, so the table can be
-- dropped and made again under the default enforcement. Three other tables name a review by a plain
-- integer (`set_versions.origin_analysis_id`, `profile_drafts.source_analysis_id`,
-- `knowledge_insights.analysis_id`): the sequence is carried over so a new review never takes an id
-- one of those still holds.

CREATE TEMP TABLE c48_review_sequence AS
SELECT COALESCE(
           (SELECT seq FROM sqlite_sequence WHERE name = 'shot_reviews'),
           (SELECT MAX(id) FROM shot_reviews),
           0
       ) AS seq;

DROP VIEW v_reviews;
DROP TABLE shot_reviews;

CREATE TABLE shot_reviews (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    shot_id            INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    status             TEXT    NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'ok', 'failed', 'interrupted')),
    -- The LLM layer's own error code plus its message, for a row a person has to act on.
    error              TEXT,
    provider           TEXT    NOT NULL DEFAULT '',
    model              TEXT    NOT NULL DEFAULT '',
    prompt_name        TEXT    NOT NULL DEFAULT '',
    prompt_version     TEXT    NOT NULL DEFAULT '',
    -- What the model was told, verbatim: the reading stays explainable after the shot's
    -- information, the prompt and the knowledge base have all moved on.
    input_json         TEXT    NOT NULL DEFAULT '{}',
    -- One sentence. Shown to the person, never served to the chat: a person cannot confirm a
    -- sentence that is not a claim.
    summary            TEXT,
    -- The Set version's prediction as the model was shown it, or empty.
    prediction_given   TEXT    NOT NULL DEFAULT '',
    rules_used_json    TEXT    NOT NULL DEFAULT '[]',
    excerpts_used_json TEXT    NOT NULL DEFAULT '[]',
    usage_json         TEXT,
    -- The `llm_calls` ledger row, where the rendered prompt and the raw reply live.
    llm_call_id        TEXT,
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at        TEXT
) STRICT;

INSERT INTO sqlite_sequence (name, seq)
SELECT 'shot_reviews', seq FROM c48_review_sequence WHERE seq > 0;
DROP TABLE c48_review_sequence;

-- The newest review of a shot, and the newest finished one: both walk this.
CREATE INDEX idx_shot_reviews_shot ON shot_reviews(shot_id, id DESC);
-- Boot reconciliation's query.
CREATE INDEX idx_shot_reviews_status ON shot_reviews(status);
-- One running review per shot, whichever process opens it: the registry's task name is the readable
-- guard and this is the real one, written inside the opening transaction.
CREATE UNIQUE INDEX idx_shot_reviews_one_running
    ON shot_reviews(shot_id) WHERE status = 'running';

CREATE TABLE review_claims (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id      INTEGER NOT NULL REFERENCES shot_reviews(id) ON DELETE CASCADE,
    -- The order the reading gave them in: claims, then the free-text results, then the prediction.
    position       INTEGER NOT NULL,
    kind           TEXT    NOT NULL CHECK (kind IN ('claim', 'free_text', 'prediction')),
    -- The window as a metric-language window, how a person reads it, the phase it is when it is
    -- one, and the seconds the server resolved it to (the curve highlights that span). The span is
    -- null for a window the shot never reached and for a prediction, which has no window.
    window_json    TEXT    NOT NULL DEFAULT '{}',
    window_text    TEXT    NOT NULL DEFAULT '',
    phase          TEXT,
    start_s        REAL,
    end_s          REAL,
    -- A word from the fixed list (`domain/warnings.py`), or null for an observation that is not
    -- a fault. A free-text result carries its expectation's own word, and only when it failed.
    fault          TEXT    CHECK (fault IN (
                       'early yield', 'little yield', 'fast flow', 'slow flow', 'skipped',
                       'cut short', 'low pressure', 'high pressure', 'unstable', 'temperature',
                       'over target', 'under target')),
    text           TEXT    NOT NULL,
    -- `[{expression, sentence, value, unit, kind, absent, why, held}]`, evaluated by the server.
    evidence_json  TEXT    NOT NULL DEFAULT '[]',
    -- 0 when a comparison failed or none of the evidence could be measured: the page says "the
    -- numbers don't bear this out". The claim is kept, never dropped.
    supported      INTEGER NOT NULL DEFAULT 1 CHECK (supported IN (0, 1)),
    -- free_text only: the expectation answered, and whether the reading says it held.
    expectation_id INTEGER,
    held           INTEGER CHECK (held IN (0, 1)),
    -- prediction only.
    stance         TEXT    CHECK (stance IN ('as_predicted', 'partly', 'against', 'not_shown')),
    status         TEXT    NOT NULL DEFAULT 'proposed'
                   CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    -- One line a person may give for an answer; empty when they gave none.
    reason         TEXT    NOT NULL DEFAULT '',
    answered_at    TEXT,
    UNIQUE (review_id, position)
) STRICT;

CREATE INDEX idx_review_claims_status ON review_claims(review_id, status);

-- Shot information items and tiers of the retired review group: the keys are gone, so a tier a
-- person chose for one has nothing to apply to.
DELETE FROM shot_info_tiers
WHERE item_key IN (
    'review_taste_balance', 'review_taste_body', 'review_taste_confidence',
    'review_description', 'review_summary', 'review_written_at', 'review_model'
);

-- The prompts of the old review (see the top of the file).
DELETE FROM prompts WHERE name IN ('review', 'review-user');

-- What the SQL tool reads. No summary and no input: the summary is not a claim, and the input is
-- the whole rendered shot.
CREATE VIEW v_reviews AS
SELECT r.id AS review_id,
       r.shot_id,
       r.status,
       r.error,
       r.provider,
       r.model,
       r.rules_used_json,
       r.excerpts_used_json,
       r.created_at,
       r.finished_at,
       (SELECT COUNT(*) FROM review_claims c
         WHERE c.review_id = r.id AND c.status = 'confirmed') AS confirmed_claims,
       (SELECT COUNT(*) FROM review_claims c
         WHERE c.review_id = r.id AND c.status = 'proposed') AS unverified_claims
  FROM shot_reviews r;

-- Only what a person confirmed, and only from the reading that answers for its shot (the newest
-- finished one): the view itself holds the line, so a query cannot read what was never confirmed.
CREATE VIEW v_review_claims AS
SELECT c.id AS claim_id,
       c.review_id,
       r.shot_id,
       c.position,
       c.kind,
       c.phase,
       c.window_text,
       c.start_s,
       c.end_s,
       c.fault,
       c.text,
       c.evidence_json,
       c.supported,
       c.expectation_id,
       c.held,
       c.stance,
       c.answered_at
  FROM review_claims c
  JOIN shot_reviews r ON r.id = c.review_id
 WHERE c.status = 'confirmed'
   AND r.status = 'ok'
   AND r.id = (SELECT MAX(n.id) FROM shot_reviews n
                WHERE n.shot_id = r.shot_id AND n.status = 'ok');
