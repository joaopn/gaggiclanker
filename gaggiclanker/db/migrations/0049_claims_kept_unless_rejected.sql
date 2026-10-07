-- A review's claims are kept unless a person rejects them.
--
-- Until now every claim was written `proposed` and reached the chat only once a person confirmed
-- it, which made the person answer every statement before the review could be used. A person now
-- reads the review and rejects what is wrong: a claim is written `confirmed`, `rejected` is the
-- one answer a person gives, and restoring a claim makes it `confirmed` again. What the chat is
-- told changes with it: every claim of the review in force that is not rejected, never the
-- summary and never a rejected claim.
--
-- Breaking in one small way, on purpose: every `proposed` claim becomes `confirmed` and every
-- answer is kept, but the one-line reason a person could type with an answer is dropped with its
-- column. Rejecting is one click now and nothing reads a reason any more.
--
-- The table is rebuilt because its CHECK names `proposed` and SQLite cannot alter a constraint.
-- Nothing references it (it is the child of `shot_reviews`), so it is dropped and made again
-- under the default enforcement; the two views that read it go first and come back after.

DROP VIEW v_review_claims;
DROP VIEW v_reviews;

CREATE TABLE review_claims_new (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id      INTEGER NOT NULL REFERENCES shot_reviews(id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    kind           TEXT    NOT NULL CHECK (kind IN ('claim', 'free_text', 'prediction')),
    window_json    TEXT    NOT NULL DEFAULT '{}',
    window_text    TEXT    NOT NULL DEFAULT '',
    phase          TEXT,
    start_s        REAL,
    end_s          REAL,
    fault          TEXT    CHECK (fault IN (
                       'early yield', 'little yield', 'fast flow', 'slow flow', 'skipped',
                       'cut short', 'low pressure', 'high pressure', 'unstable', 'temperature',
                       'over target', 'under target')),
    text           TEXT    NOT NULL,
    evidence_json  TEXT    NOT NULL DEFAULT '[]',
    supported      INTEGER NOT NULL DEFAULT 1 CHECK (supported IN (0, 1)),
    expectation_id INTEGER,
    held           INTEGER CHECK (held IN (0, 1)),
    stance         TEXT    CHECK (stance IN ('as_predicted', 'partly', 'against', 'not_shown')),
    -- `confirmed` until a person rejects it; they may restore it.
    status         TEXT    NOT NULL DEFAULT 'confirmed'
                   CHECK (status IN ('confirmed', 'rejected')),
    answered_at    TEXT,
    UNIQUE (review_id, position)
) STRICT;

INSERT INTO review_claims_new
    (id, review_id, position, kind, window_json, window_text, phase, start_s, end_s, fault, text,
     evidence_json, supported, expectation_id, held, stance, status, answered_at)
SELECT id, review_id, position, kind, window_json, window_text, phase, start_s, end_s, fault, text,
       evidence_json, supported, expectation_id, held, stance,
       CASE status WHEN 'rejected' THEN 'rejected' ELSE 'confirmed' END,
       answered_at
  FROM review_claims;

DROP TABLE review_claims;
ALTER TABLE review_claims_new RENAME TO review_claims;

CREATE INDEX idx_review_claims_status ON review_claims(review_id, status);

-- The shot-information items of the review (they were called the reading's): a tier a person
-- chose for one follows it.
UPDATE shot_info_tiers SET item_key = 'review_state' WHERE item_key = 'reading_state';
UPDATE shot_info_tiers SET item_key = 'review_claims' WHERE item_key = 'reading_claims';
UPDATE shot_info_tiers SET item_key = 'review_prediction' WHERE item_key = 'reading_prediction';

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
         WHERE c.review_id = r.id AND c.status = 'confirmed') AS kept_claims,
       (SELECT COUNT(*) FROM review_claims c
         WHERE c.review_id = r.id AND c.status = 'rejected') AS rejected_claims
  FROM shot_reviews r;

-- Every claim that is not rejected, and only from the review in force (the newest finished one):
-- the view itself holds the line, so a query cannot read a claim a person rejected.
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
 WHERE c.status <> 'rejected'
   AND r.status = 'ok'
   AND r.id = (SELECT MAX(n.id) FROM shot_reviews n
                WHERE n.shot_id = r.shot_id AND n.status = 'ok');
