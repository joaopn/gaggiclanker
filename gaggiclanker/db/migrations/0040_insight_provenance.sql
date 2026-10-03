-- What a Set's insight rests on, replacing one outright, and the agent proposing a deletion.
--
-- An insight used to say only which shots it came from. A conclusion about a recipe also
-- rests on how its versions turned out, and those grades can be changed afterwards, so an
-- insight written when v3 "held" can outlive the day v3 was re-graded "failed". Two things
-- follow, and both are kept small on purpose (the archive should not pile up):
--
--   * an insight records the versions it rests on, each with the outcome it had when it was
--     written; the outcome *now* is always read live from the version, never copied;
--   * the agent may propose that a new insight replaces one the person has already added, or
--     that an added insight be deleted. Neither does anything until the person presses a
--     button, and when they do the old insight is **deleted**: nothing is retired, folded
--     or kept as a tombstone.
--
-- Columns added to knowledge_insights (ADD COLUMN only, so no row is rebuilt or lost):
--
--   * rests_on_json: [{"set_version_id": 12, "outcome": "held"}, ...] the versions this
--     insight rests on and the outcome each had at the moment of writing. That "as written"
--     value never changes afterwards.
--   * replaces_id: the added insight of the same Set this one was proposed to replace.
--     SET NULL, so deleting the old one by any path leaves the new one standing.
--   * replaces_text: the old insight's text, so the waiting card can show it above the new
--     one after nothing else of the old one might be read. Cleared the moment the old one
--     is gone (replaced, or deleted by another path), so no copy outlives it.
--   * replaced: how the person's Add ended, so the conversation's card and record can say
--     it: 'deleted' (the old insight was deleted by that Add) or 'old_changed' (it was
--     already gone or no longer added, and this one was added as an ordinary insight).
--
-- A new table for the agent's deletion proposals, shaped like the outcome proposals:
--
--   * set_id and thread_id cascade: the card lives in the conversation that proposed it
--     and nowhere else, so the conversation going takes it.
--   * insight_id is SET NULL and insight_text keeps the text as it was when proposed, so
--     that conversation's card can still say what was removed after the insight is gone.
--   * status: proposed (waiting), deleted (the person pressed Delete), kept (Keep), stale
--     (the insight went by another path first), superseded (a newer proposal for the same
--     insight replaced it). One proposed row per insight, enforced by the partial index
--     because the stdio child is a second process.
--
-- Existing insights get '[]' and NULL; nothing is carried and nothing is lost.

ALTER TABLE knowledge_insights ADD COLUMN rests_on_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE knowledge_insights
    ADD COLUMN replaces_id INTEGER REFERENCES knowledge_insights(id) ON DELETE SET NULL;
ALTER TABLE knowledge_insights ADD COLUMN replaces_text TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_insights
    ADD COLUMN replaced TEXT CHECK (replaced IS NULL OR replaced IN ('deleted', 'old_changed'));

CREATE INDEX idx_knowledge_insights_replaces ON knowledge_insights(replaces_id);

CREATE TABLE set_insight_deletions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id       INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    thread_id    INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    insight_id   INTEGER REFERENCES knowledge_insights(id) ON DELETE SET NULL,
    insight_text TEXT    NOT NULL,
    reason       TEXT    NOT NULL CHECK (length(reason) BETWEEN 20 AND 500),
    status       TEXT    NOT NULL DEFAULT 'proposed'
                 CHECK (status IN ('proposed', 'deleted', 'kept', 'stale', 'superseded')),
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    decided_at   TEXT
) STRICT;

CREATE UNIQUE INDEX idx_set_insight_deletions_waiting
    ON set_insight_deletions(insight_id) WHERE status = 'proposed';

CREATE INDEX idx_set_insight_deletions_thread ON set_insight_deletions(thread_id, id);
CREATE INDEX idx_set_insight_deletions_set ON set_insight_deletions(set_id, id DESC);
