-- Tier 3 of the knowledge base: what this archive has learned about *this* kitchen.
--
-- Tiers 1 and 2 are the same for everybody — expert heuristics and the prose
-- behind them, shipped in the package. This table is the third tier and it is
-- the only one nobody else could have written: "the Niche needs two clicks
-- finer for anything anaerobic", "this machine reads 1.5 °C low at 93".
-- gaggimate-mcp keeps the same thing in `brewing-insights.md` and
-- `grind-map.md`; here it is rows, because an insight has to be *selected* by
-- the attributes it applies to rather than read whole.
--
-- **Nothing here reaches a prompt until a person has confirmed it.** An insight
-- is proposed by the analyzer (at most two per analysis) or by the chat, and it
-- lands unconfirmed. A model that generalises from one shot and is believed by
-- the next analysis is a feedback loop that manufactures its own evidence, and
-- the confirm button is what breaks it. `select_insights` reads
-- `confirmed = 1` and nothing else.
--
-- No transaction control here: the runner wraps the whole file plus its ledger
-- row in one, and SQLite's DDL is transactional.

CREATE TABLE knowledge_insights (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    -- What the insight is about, as a JSON object whose keys are any of
    -- `bean_id`, `roast_level`, `process`, `origin`, `grinder_id`,
    -- `profile_style`, `machine_id`. An insight applies to a Set when **every**
    -- key it states matches that Set — so `{}` is a fact about the whole
    -- kitchen, `{"grinder_id": 2}` is about one grinder, and
    -- `{"grinder_id": 2, "process": "anaerobic"}` is about their combination
    -- and does not fire for a washed bean on the same grinder.
    --
    -- JSON rather than seven nullable columns because the matching rule is "all
    -- stated keys", which is a property of the document; seven columns would
    -- put the same rule in a WHERE clause that has to be rewritten every time a
    -- dimension is added, and a NULL there would be ambiguous between "any" and
    -- "not stated".
    scope_json             TEXT    NOT NULL DEFAULT '{}',
    text                   TEXT    NOT NULL,
    -- The shots this was learned from. A claim with no evidence is an opinion,
    -- and the UI links each id straight to the shot page so the reader can
    -- check it before pressing confirm.
    evidence_shot_ids_json TEXT    NOT NULL DEFAULT '[]',
    source                 TEXT    NOT NULL DEFAULT 'user'
                           CHECK (source IN ('analysis', 'chat', 'user')),
    -- The analysis that proposed it, when one did. SET NULL rather than CASCADE:
    -- a confirmed insight is the user's, and deleting the analysis it came from
    -- must not take a fact they vouched for with it.
    analysis_id            INTEGER REFERENCES shot_analyses(id) ON DELETE SET NULL,
    confirmed              INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    created_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    confirmed_at           TEXT
) STRICT;

-- Selection reads the confirmed ones oldest first (the order they were learned
-- in is the order they read best in a prompt); the Insights page reads the
-- unconfirmed ones newest first. One index serves both.
CREATE INDEX idx_knowledge_insights_confirmed
    ON knowledge_insights(confirmed, created_at, id);

-- "What did this analysis propose" — the shot page's panel, per analysis.
CREATE INDEX idx_knowledge_insights_analysis
    ON knowledge_insights(analysis_id);
