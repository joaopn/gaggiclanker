-- An insight belongs to the Set it was learned in.
--
-- Until now an insight was filed by bean and grinder, so it reached every Set that shared them,
-- and an agent-written one was confirmed only on the Knowledge page. From here an insight written
-- in a Set's conversation carries that Set (and the version the conversation was about) and
-- reaches only that Set's conversations; the Knowledge page holds general knowledge only.
--
--   * set_id: the Set the insight belongs to. NULL is a general insight (hand-written, or an
--     agent-written one nobody could place), which keeps the attribute matching it always had.
--     ON DELETE CASCADE: only a Set being designed is ever deleted, and it cannot have insights,
--     so this is a safety net and never a way to lose one.
--   * set_version_id: the version the insight was learned at, NULL when it is not known ("learned
--     before versions were recorded"). SET NULL, so a version going away never takes an insight.
--   * thread_id: the conversation that wrote it, for an insight a chat proposed. That is how a
--     conversation is told "you proposed this" about its own insights and no others (a placed
--     insight has none). SET NULL: deleting a conversation never deletes what it learned.
--   * dismissed: the person turned the card down. A dismissed insight is kept for the
--     conversation that proposed it and is shown nowhere else and reaches no prompt.
--
-- Adding columns only: `sets` has five cascading children and a rebuild would be a data-loss
-- decision. Existing rows are all general (set_id NULL) until the placement step below has run.
--
--   * insight_placement_build: one row once agent-written insights that fit exactly one Set have
--     been moved onto it. "Fits" is the live matching rule (`scope_matches` over the Set's
--     attributes) and evidence in that Set, which SQL cannot call, so the application runs the
--     step at boot, after migrations, once: see `db/repos/insight_placement.py`. A marker row
--     keeps it from redoing anything a person has since chosen.
--
-- No existing data is touched by this file.

ALTER TABLE knowledge_insights ADD COLUMN set_id INTEGER REFERENCES sets(id) ON DELETE CASCADE;
ALTER TABLE knowledge_insights
    ADD COLUMN set_version_id INTEGER REFERENCES set_versions(id) ON DELETE SET NULL;
ALTER TABLE knowledge_insights
    ADD COLUMN thread_id INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL;
ALTER TABLE knowledge_insights
    ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0, 1));

CREATE INDEX idx_knowledge_insights_set ON knowledge_insights(set_id, created_at, id);
CREATE INDEX idx_knowledge_insights_version ON knowledge_insights(set_version_id);

CREATE TABLE insight_placement_build (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    built_at TEXT    NOT NULL,
    moved    INTEGER NOT NULL DEFAULT 0
) STRICT;
