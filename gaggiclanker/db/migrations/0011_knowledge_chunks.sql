-- Tier 2 of the knowledge base: its prose half.
--
-- Tier 1 (`knowledge_rules`, migration 0006) is a few hundred one-sentence
-- facts with machine-readable conditions. It is authoritative and it is small.
-- What it cannot carry is the *reasoning* — why a natural wants less pressure,
-- what a pressure cliff at 14 s actually looks like, the six-branch diagnostic
-- tree for a bitter-and-fast shot. That is prose, it is thirty thousand words
-- of it, and no analysis wants all of it. So it is stored split into chunks and
-- retrieved a handful at a time.
--
-- Two tables and an index over them:
--
--   `knowledge_docs`   one row per markdown document, holding the live text.
--   `knowledge_chunks` the document split at its H2/H3 headings into 200-600
--                      word pieces, each with a `heading_path` that is both its
--                      stable id and the citation an analysis prints.
--   `knowledge_chunks_fts`  an FTS5 index over the chunks, BM25-ranked.
--
-- **Why the markdown lives in the row and not only in the file.** The seed
-- files ship in the package the way `seed/rules.yaml` and the prompts do, and
-- the same three-way rule applies at document level: a new file is inserted, an
-- unedited row takes the new text, an edited row keeps the user's text and only
-- records the new default. That needs both copies in the table — `body` is what
-- is chunked and searched, `default_body` is what "reset" restores and what
-- `edited` is computed against. It is 200 KB in total; the archive it sits next
-- to is measured in gigabytes of shot samples.
--
-- No transaction control here: the runner wraps the whole file plus its ledger
-- row in one, and SQLite's DDL is transactional.

CREATE TABLE knowledge_docs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The citation's first half and the URL's path segment, so it has to be
    -- stable and URL-safe: the file's stem, e.g. `ESPRESSO_BREWING_BASICS`.
    slug          TEXT    NOT NULL UNIQUE,
    title         TEXT    NOT NULL DEFAULT '',
    -- Where the *document* came from, as a short name (`gaggimate-mcp`). The
    -- sources behind it are named in the document's own text, which is part of
    -- the chunked body on purpose: an excerpt carries its provenance with it.
    source        TEXT    NOT NULL DEFAULT '',
    licence       TEXT    NOT NULL DEFAULT '',
    attribution   TEXT    NOT NULL DEFAULT '',
    -- The live markdown, and the copy the package shipped.
    body          TEXT    NOT NULL DEFAULT '',
    default_body  TEXT    NOT NULL DEFAULT '',
    -- sha256 of `body` and of `default_body`. Stored rather than derived on
    -- read because seeding compares `default_hash` against the shipped file on
    -- every boot, for every document: hashing eight kilobytes of file is
    -- cheap, and pulling 200 KB of markdown back out of the database to
    -- discover that nothing changed is the work this column avoids.
    content_hash  TEXT    NOT NULL DEFAULT '',
    default_hash  TEXT    NOT NULL DEFAULT '',
    -- Whether the live text differs from the shipped one. Denormalised from
    -- (content_hash != default_hash) because the docs list renders it for every
    -- row and a stored flag that is written in exactly one place cannot drift
    -- from the two hashes it summarises.
    edited        INTEGER NOT NULL DEFAULT 0 CHECK (edited IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- One row per chunk. Chunks are **derived**: they are deleted and rebuilt
-- whenever the document's text changes, so nothing may reference a chunk id.
-- What is referenced — by an analysis's `excerpts_used`, by a citation in a
-- diagnosis — is the `heading_path`, which the chunker derives from the
-- headings themselves and which therefore survives a re-chunk of unchanged
-- text.
CREATE TABLE knowledge_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          INTEGER NOT NULL REFERENCES knowledge_docs(id) ON DELETE CASCADE,
    -- `ESPRESSO_BREWING_BASICS#adjustment-strategies/variable-hierarchy`: the
    -- document slug, then the slugified heading trail. Unique across the whole
    -- table, which is what makes it usable as a citation with no document
    -- qualifier beside it.
    heading_path    TEXT    NOT NULL UNIQUE,
    -- The same trail as the headings were actually written, joined with " > ".
    -- Indexed by FTS5 as its own column so a query that names a heading ranks
    -- the section above a passing mention of the word in somebody's prose.
    heading         TEXT    NOT NULL DEFAULT '',
    -- Position in the document, 0-based. The doc view renders in this order.
    ordinal         INTEGER NOT NULL,
    body            TEXT    NOT NULL,
    -- Words x 1.35, rounded. An estimate is all the retrieval budget needs and
    -- a real tokeniser would be a dependency, a model choice and a number that
    -- changes under you when the provider does.
    tokens_estimate INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE INDEX idx_knowledge_chunks_doc ON knowledge_chunks(doc_id, ordinal);

-- External-content FTS5: the index holds the tokens, `knowledge_chunks` holds
-- the text, and the triggers below keep them level. `content=` rather than a
-- standalone table because a second copy of 200 KB of prose that can silently
-- disagree with the first is worth avoiding for the cost of three triggers.
--
-- `porter unicode61`: stemming is what makes "channeling" find "channel" and
-- "extracting" find "extraction", which is most of the value at this corpus
-- size. unicode61 splits on underscores and hashes too, so the document slug
-- inside a heading path is searchable as words.
CREATE VIRTUAL TABLE knowledge_chunks_fts USING fts5(
    heading,
    body,
    content='knowledge_chunks',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER knowledge_chunks_ai AFTER INSERT ON knowledge_chunks BEGIN
    INSERT INTO knowledge_chunks_fts(rowid, heading, body)
    VALUES (new.id, new.heading, new.body);
END;

-- The 'delete' command writes an inverted entry rather than removing rows: with
-- `content=`, FTS5 cannot read the old text back once the content row is gone,
-- so the old values have to be handed to it here.
CREATE TRIGGER knowledge_chunks_ad AFTER DELETE ON knowledge_chunks BEGIN
    INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, heading, body)
    VALUES ('delete', old.id, old.heading, old.body);
END;

CREATE TRIGGER knowledge_chunks_au AFTER UPDATE ON knowledge_chunks BEGIN
    INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, heading, body)
    VALUES ('delete', old.id, old.heading, old.body);
    INSERT INTO knowledge_chunks_fts(rowid, heading, body)
    VALUES (new.id, new.heading, new.body);
END;
