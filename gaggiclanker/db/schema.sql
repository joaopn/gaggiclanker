-- The whole database, in one file.
--
-- A fresh database is made by running this script once, in one transaction. A change to the
-- schema edits this file, and an app started on a database whose structure differs from
-- what this file builds refuses to start (see `gaggiclanker/db/schema.py`). It never
-- alters or deletes the database it refuses.
--
-- What the comparison looks at is structure (tables and their columns and constraints,
-- indexes, foreign keys, views, triggers), not text layout: comments and whitespace here
-- can change freely. The seed rows at the end are not compared; they are only what a new
-- database starts with.
--
-- Foreign keys are enforced by the connection (`PRAGMA foreign_keys = ON`); several tables
-- rely on `ON DELETE CASCADE` / `SET NULL`.

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE profile_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT    NOT NULL UNIQUE,
    label        TEXT    NOT NULL,
    type         TEXT    NOT NULL,
    utility      INTEGER NOT NULL DEFAULT 0,
    -- The canonical JSON, not the bytes the device sent: two device documents
    -- that brew the same shot must be one row, and this is the form they agree
    -- on. `device_json` keeps the last raw document for reference.
    json         TEXT    NOT NULL,
    device_json  TEXT,
    source       TEXT    NOT NULL DEFAULT 'device'
                 CHECK (source IN ('device', 'draft', 'import')),
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE shot_samples (
    shot_id      INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    t_ms         INTEGER NOT NULL,
    tt           REAL,    -- target temperature, °C
    ct           REAL,    -- current temperature, °C
    tp           REAL,    -- target/limit pressure, bar
    cp           REAL,    -- current pressure, bar
    fl           REAL,    -- pump flow, ml/s
    tf           REAL,    -- target/limit flow, ml/s
    pf           REAL,    -- estimated puck flow, ml/s
    vf           REAL,    -- scale flow, g/s
    v            REAL,    -- scale weight, g
    ev           REAL,    -- estimated weight, g
    pr           REAL,    -- puck resistance
    si           INTEGER, -- system-info bitfield
    wp           REAL,    -- cumulative water pumped, ml
    phase_number INTEGER,
    PRIMARY KEY (shot_id, t_ms)
) STRICT, WITHOUT ROWID;

CREATE TABLE device_shot_notes (
    shot_id          INTEGER PRIMARY KEY REFERENCES shots(id) ON DELETE CASCADE,
    raw_json         TEXT    NOT NULL,
    rating           INTEGER,
    bean_type        TEXT,
    dose_in_g        REAL,
    dose_out_g       REAL,
    ratio            REAL,
    grind_setting    TEXT,
    balance_taste    TEXT,
    notes            TEXT    NOT NULL DEFAULT '',
    device_timestamp INTEGER,
    -- What the index said when these notes were pulled. The device rewrites the
    -- index entry in place when a rating or a dose changes, so a difference
    -- here is the signal to re-pull.
    synced_rating    INTEGER,
    synced_volume_g  REAL,
    fetched_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE sync_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              TEXT    NOT NULL
                      CHECK (kind IN ('backfill', 'live', 'profiles', 'notes', 'identity')),
    status            TEXT    NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'ok', 'error')),
    trigger           TEXT    NOT NULL DEFAULT '',
    started_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at       TEXT,
    shots_seen        INTEGER NOT NULL DEFAULT 0,
    shots_inserted    INTEGER NOT NULL DEFAULT 0,
    shots_updated     INTEGER NOT NULL DEFAULT 0,
    shots_quarantined INTEGER NOT NULL DEFAULT 0,
    profiles_changed  INTEGER NOT NULL DEFAULT 0,
    notes_synced      INTEGER NOT NULL DEFAULT 0,
    errors            INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    summary_json TEXT
) STRICT;
CREATE INDEX idx_sync_runs_kind ON sync_runs(kind, started_at DESC);

CREATE TABLE sync_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER REFERENCES sync_runs(id) ON DELETE CASCADE,
    at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    kind      TEXT    NOT NULL,
    shot_id   INTEGER,
    device_id TEXT,
    message   TEXT    NOT NULL DEFAULT '',
    data_json TEXT
) STRICT;
CREATE INDEX idx_sync_events_run ON sync_events(run_id);

CREATE TABLE prompts (
    name            TEXT PRIMARY KEY,
    -- The live text: what the next call renders. Edited through the API.
    content         TEXT NOT NULL,
    -- What the file on disk said at the last boot. The "reset" target, and
    -- half of the edited test.
    default_content TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE llm_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The observer's record id, so a row here and a line in the live list are
    -- recognisably the same call.
    call_id        TEXT    NOT NULL UNIQUE,
    purpose        TEXT    NOT NULL,
    label          TEXT    NOT NULL DEFAULT '',
    subject        TEXT,
    provider       TEXT    NOT NULL,
    model          TEXT,
    -- Which response mode actually worked. Worth keeping: "this gateway has
    -- silently dropped to text mode" explains a lot of bad output.
    mode           TEXT,
    prompt_name    TEXT,
    prompt_version TEXT,
    -- NULL means the provider reported nothing, which is not the same as zero.
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    duration_ms    INTEGER,
    status         TEXT    NOT NULL CHECK (status IN ('succeeded', 'failed')),
    error          TEXT,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    input_text TEXT,
    output_text TEXT,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    context_tokens INTEGER
) STRICT;
CREATE INDEX idx_llm_calls_created_at ON llm_calls(created_at DESC);

CREATE TABLE beans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    roaster           TEXT,
    origin            TEXT,
    process           TEXT    CHECK (process IS NULL OR process IN
                              ('washed', 'natural', 'honey', 'anaerobic', 'other')),
    roast_level       TEXT    CHECK (roast_level IS NULL OR roast_level IN
                              ('light', 'medium-light', 'medium', 'medium-dark', 'dark')),
    decaf             INTEGER NOT NULL DEFAULT 0,
    -- A free-form description of the coffee in the person's words: what the
    -- bag or the roaster says, tasting notes, anything worth knowing about the
    -- bean. The prompts get it as written, not as a verified fact.
    description       TEXT    NOT NULL DEFAULT '',
    notes             TEXT    NOT NULL DEFAULT '',
    archived          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    acidity INTEGER CHECK (acidity IS NULL OR acidity BETWEEN 1 AND 5),
    intensity INTEGER CHECK (intensity IS NULL OR intensity BETWEEN 1 AND 5),
    sweetness INTEGER CHECK (sweetness IS NULL OR sweetness BETWEEN 1 AND 5)
) STRICT;
CREATE INDEX idx_beans_archived ON beans(archived, name);

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

CREATE TABLE sets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    bean_id    INTEGER NOT NULL REFERENCES beans(id),
    grinder_id INTEGER REFERENCES grinders(id),
    automatch     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    archived INTEGER NOT NULL DEFAULT 0,
    designing INTEGER NOT NULL DEFAULT 0,
    design_brief TEXT NOT NULL DEFAULT '{}',
    current_version_id INTEGER REFERENCES set_versions(id)
) STRICT;
CREATE INDEX idx_sets_bean ON sets(bean_id);

CREATE TABLE shot_judgements (
    shot_id                 INTEGER PRIMARY KEY REFERENCES shots(id) ON DELETE CASCADE,
    -- 1..5, or NULL for "judged but not scored". The device writes 0 for
    -- "unrated", which is translated to NULL on the way in: 0 is a number
    -- somebody would average.
    rating                  INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    balance                 TEXT    CHECK (balance IS NULL OR balance IN
                                    ('sour', 'balanced', 'bitter')),
    taste_notes_json        TEXT    NOT NULL DEFAULT '[]',
    aroma_notes_json        TEXT    NOT NULL DEFAULT '[]',
    dose_in_g               REAL,
    dose_out_g              REAL,
    notes                   TEXT    NOT NULL DEFAULT '',
    decision                TEXT    CHECK (decision IS NULL OR decision IN
                                    ('keep', 'improve', 'discard')),
    seeded_from_device_note INTEGER NOT NULL DEFAULT 0,
    -- When this judgement was last reconciled with the device's notes card.
    -- Set by the seeding path; NULL on a judgement typed here.
    device_synced_at        TEXT,
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE INDEX idx_shot_judgements_rating ON shot_judgements(rating);

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

CREATE TABLE runtime_secrets (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE auth_sessions (
    id         TEXT PRIMARY KEY,
    subject    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at INTEGER NOT NULL,
    revoked_at TEXT,
    -- Purely so the maintainer can tell one signed-in browser from another when
    -- deciding whether to revoke. Truncated by the writer; never trusted.
    user_agent TEXT    NOT NULL DEFAULT ''
) STRICT;
CREATE INDEX idx_auth_sessions_expires ON auth_sessions(expires_at);

CREATE TABLE profile_drafts (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    base_version_id          INTEGER NOT NULL REFERENCES profile_versions(id),
    draft_version_id         INTEGER REFERENCES profile_versions(id),
    source_analysis_id       INTEGER,
    source_suggestion_id     INTEGER,
    set_id                   INTEGER,
    prediction               TEXT    NOT NULL DEFAULT '',
    compares_to_version_id   INTEGER REFERENCES set_versions(id) ON DELETE SET NULL,
    -- The draft this one refines. A refinement supersedes its parent rather
    -- than editing it, so the advice that produced each attempt stays readable.
    parent_draft_id          INTEGER REFERENCES profile_drafts(id),
    change_summary           TEXT    NOT NULL DEFAULT '',
    stop_condition_changes_json TEXT NOT NULL DEFAULT '[]',
    clamp_changes_json       TEXT    NOT NULL DEFAULT '[]',
    -- What the barista asked for, in their words. Carried into the next
    -- refinement's prompt, which is the whole reason a refinement is a new
    -- draft rather than a re-run.
    notes                    TEXT    NOT NULL DEFAULT '',
    status                   TEXT    NOT NULL DEFAULT 'draft'
                             CHECK (status IN ('draft', 'approved', 'pushed',
                                               'failed', 'discarded', 'superseded')),
    -- Set by the approval, and read by the push: a draft whose stop conditions
    -- moved cannot be pushed without somebody having said so.
    pushed_device_profile_id TEXT,
    verification_json        TEXT,
    error                    TEXT,
    created_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    base_device_profile_id TEXT,
    suggest_major INTEGER NOT NULL DEFAULT 0 CHECK (suggest_major IN (0, 1)),
    major_reason TEXT NOT NULL DEFAULT '',
    pushed_saved INTEGER NOT NULL DEFAULT 0,
    replaced_device_profile_id TEXT,
    replaced_version_id INTEGER,
    cleared_set_version_ids_json TEXT,
    recorded_version_id INTEGER,
    replaced_by_draft_id INTEGER,
    outcome_json TEXT,
    is_new INTEGER NOT NULL DEFAULT 0 CHECK (is_new IN (0, 1)),
    made_by TEXT CHECK (made_by IN ('agent', 'edit'))
) STRICT;
CREATE INDEX idx_profile_drafts_status ON profile_drafts(status, id DESC);
CREATE INDEX idx_profile_drafts_base ON profile_drafts(base_version_id, id DESC);

CREATE TABLE device_writes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL CHECK (kind IN
                   ('profile_save', 'profile_delete', 'profile_select',
                    'profile_favorite', 'profile_unfavorite',
                    'shot_delete', 'notes_save')),
    host         TEXT    NOT NULL DEFAULT '',
    device_id    TEXT,
    payload_hash TEXT    NOT NULL DEFAULT '',
    result       TEXT    NOT NULL CHECK (result IN ('ok', 'refused', 'failed')),
    error        TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE INDEX idx_device_writes_created ON device_writes(created_at DESC, id DESC);
CREATE INDEX idx_device_writes_provenance ON device_writes(device_id, kind, result);

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
CREATE TRIGGER knowledge_chunks_ai AFTER INSERT ON knowledge_chunks BEGIN
    INSERT INTO knowledge_chunks_fts(rowid, heading, body)
    VALUES (new.id, new.heading, new.body);
END;
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

CREATE VIRTUAL TABLE knowledge_chunks_fts USING fts5(
    heading,
    body,
    content='knowledge_chunks',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TABLE chat_threads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT    NOT NULL DEFAULT '',
    set_id         INTEGER REFERENCES sets(id) ON DELETE CASCADE,
    set_version_id INTEGER REFERENCES set_versions(id) ON DELETE CASCADE,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE INDEX idx_chat_threads_updated ON chat_threads(updated_at DESC, id DESC);
CREATE INDEX idx_chat_threads_version ON chat_threads(set_version_id, updated_at DESC, id DESC);

CREATE TABLE chat_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    status      TEXT    NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'ok', 'failed', 'cancelled', 'interrupted')),
    provider    TEXT    NOT NULL DEFAULT '',
    model       TEXT    NOT NULL DEFAULT '',
    error       TEXT,
    usage_json  TEXT,
    tool_rounds INTEGER NOT NULL DEFAULT 0,
    tool_calls  INTEGER NOT NULL DEFAULT 0,
    started_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at TEXT
) STRICT;
CREATE INDEX idx_chat_runs_thread ON chat_runs(thread_id, id DESC);
CREATE INDEX idx_chat_runs_status ON chat_runs(status, started_at DESC);

CREATE TABLE chat_messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id         INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    run_id            INTEGER REFERENCES chat_runs(id) ON DELETE SET NULL,
    role              TEXT    NOT NULL CHECK (role IN ('user', 'assistant', 'tool', 'system')),
    content           TEXT    NOT NULL DEFAULT '',
    tool_calls_json   TEXT,
    tool_results_json TEXT,
    usage_json        TEXT,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE INDEX idx_chat_messages_thread ON chat_messages(thread_id, id);

CREATE TABLE chat_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  INTEGER NOT NULL REFERENCES chat_runs(id) ON DELETE CASCADE,
    seq     INTEGER NOT NULL,
    kind    TEXT    NOT NULL,
    data    TEXT    NOT NULL DEFAULT '{}',
    at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    UNIQUE (run_id, seq)
) STRICT;

CREATE TABLE tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER REFERENCES chat_runs(id) ON DELETE CASCADE,
    caller      TEXT    NOT NULL DEFAULT 'chat',
    tool        TEXT    NOT NULL,
    permission  TEXT    NOT NULL DEFAULT 'read',
    input_hash  TEXT    NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL CHECK (status IN ('ok', 'error', 'refused', 'timeout')),
    error       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE INDEX idx_tool_calls_run ON tool_calls(run_id, id);
CREATE INDEX idx_tool_calls_tool ON tool_calls(tool, created_at DESC);

CREATE TABLE starting_point_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    bean_id           INTEGER NOT NULL REFERENCES beans(id) ON DELETE CASCADE,
    grinder_id        INTEGER REFERENCES grinders(id) ON DELETE SET NULL,
    -- What the user says they normally grind at, in the grinder's own units.
    -- The single most useful input there is: it is what turns "finer than usual"
    -- into a number somebody can dial.
    usual_grind       TEXT    NOT NULL DEFAULT '',
    dose_hint_g       REAL,
    provider          TEXT    NOT NULL DEFAULT '',
    model             TEXT    NOT NULL DEFAULT '',
    prompt_name       TEXT    NOT NULL DEFAULT '',
    prompt_version    TEXT    NOT NULL DEFAULT '',
    input_json        TEXT    NOT NULL DEFAULT '{}',
    output_json       TEXT,
    usage_json        TEXT,
    status            TEXT    NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'ok', 'failed', 'interrupted')),
    error             TEXT,
    llm_call_id       TEXT,
    -- Which of the three options was taken, and what it produced. One accept
    -- per run: the Set exists after the first one, and a second accept would
    -- silently create a duplicate Set on the same bag.
    accepted_option   TEXT    CHECK (accepted_option IS NULL OR
                                     accepted_option IN ('conservative', 'recommended',
                                                         'adventurous')),
    accepted_set_id   INTEGER REFERENCES sets(id) ON DELETE SET NULL,
    accepted_set_version_id INTEGER,
    accepted_draft_id INTEGER REFERENCES profile_drafts(id) ON DELETE SET NULL,
    accepted_at       TEXT,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at       TEXT
) STRICT;
CREATE INDEX idx_starting_point_runs_bean ON starting_point_runs(bean_id, id DESC);
CREATE INDEX idx_starting_point_runs_status ON starting_point_runs(status, created_at DESC);

CREATE TABLE device_profiles (
    device_id          TEXT    NOT NULL,
    current_version_id INTEGER NOT NULL REFERENCES profile_versions(id),
    favorite           INTEGER NOT NULL DEFAULT 0,
    selected           INTEGER NOT NULL DEFAULT 0,
    position           INTEGER,
    first_seen_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at         TEXT,
    PRIMARY KEY (device_id)
) STRICT;
CREATE INDEX idx_device_profiles_version ON device_profiles(current_version_id);

CREATE TABLE machines (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    host                 TEXT    NOT NULL DEFAULT '',
    name                 TEXT    NOT NULL DEFAULT '',
    hardware_string      TEXT,
    display_version      TEXT,
    controller_version   TEXT,
    has_pressure         INTEGER NOT NULL DEFAULT 0,
    has_dimming          INTEGER NOT NULL DEFAULT 0,
    has_gear_pump        INTEGER NOT NULL DEFAULT 0,
    has_led              INTEGER NOT NULL DEFAULT 0,
    temperature_offset_c REAL,
    pid                  TEXT,
    brew_delay_ms        INTEGER,
    identity_json        TEXT,
    settings_json        TEXT,
    notes                TEXT    NOT NULL DEFAULT '',
    first_seen_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE flavor_picks (
    kind     TEXT    NOT NULL CHECK (kind IN ('taste', 'aroma')),
    note     TEXT    NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (kind, note)
) STRICT;

CREATE TABLE set_version_proposals (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id                 INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    thread_id              INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
    base_version_id        INTEGER NOT NULL REFERENCES set_versions(id) ON DELETE CASCADE,
    patch_json             TEXT    NOT NULL DEFAULT '{}',
    reason                 TEXT    NOT NULL DEFAULT '',
    prediction             TEXT    NOT NULL DEFAULT '',
    compares_to_version_id INTEGER REFERENCES set_versions(id) ON DELETE SET NULL,
    combined_reason        TEXT    NOT NULL DEFAULT '',
    -- `stale` is the one nobody chooses: a version appended to this Set by any
    -- other path retires whatever was waiting, because a change argued against
    -- a recipe that is no longer current is not a question anybody can answer.
    status                 TEXT    NOT NULL DEFAULT 'proposed'
                           CHECK (status IN ('proposed', 'accepted', 'declined', 'stale')),
    -- What the person said when they turned it down. The agent is told about it
    -- in the next conversation, because "not that, I have tried it" is the most
    -- useful sentence a person can write here.
    decline_note           TEXT    NOT NULL DEFAULT '',
    resulting_version_id   INTEGER REFERENCES set_versions(id) ON DELETE SET NULL,
    created_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    decided_at             TEXT,
    kind TEXT NOT NULL DEFAULT 'change',
    draft_id INTEGER REFERENCES profile_drafts(id) ON DELETE SET NULL,
    suggest_major INTEGER NOT NULL DEFAULT 0 CHECK (suggest_major IN (0, 1)),
    major_reason TEXT NOT NULL DEFAULT ''
) STRICT;
CREATE UNIQUE INDEX idx_set_version_proposals_waiting
    ON set_version_proposals(set_id) WHERE status = 'proposed';
CREATE INDEX idx_set_version_proposals_set ON set_version_proposals(set_id, id DESC);

CREATE TABLE shot_info_tiers (
    item_key   TEXT NOT NULL PRIMARY KEY,
    tier       TEXT NOT NULL CHECK (tier IN ('base', 'extended', 'excluded')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE knowledge_insights (
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
    confirmed_at           TEXT,
    set_id INTEGER REFERENCES sets(id) ON DELETE CASCADE,
    set_version_id INTEGER REFERENCES set_versions(id) ON DELETE SET NULL,
    thread_id INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
    dismissed INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0, 1)),
    rests_on_json TEXT NOT NULL DEFAULT '[]',
    replaces_id INTEGER REFERENCES knowledge_insights(id) ON DELETE SET NULL,
    replaces_text TEXT NOT NULL DEFAULT '',
    replaced TEXT CHECK (replaced IS NULL OR replaced IN ('deleted', 'old_changed')),
    pattern_run_id INTEGER REFERENCES pattern_runs(id) ON DELETE SET NULL
) STRICT;
CREATE INDEX idx_knowledge_insights_confirmed
    ON knowledge_insights(confirmed, created_at, id);
CREATE INDEX idx_knowledge_insights_set ON knowledge_insights(set_id, created_at, id);
CREATE INDEX idx_knowledge_insights_version ON knowledge_insights(set_version_id);
CREATE INDEX idx_knowledge_insights_replaces ON knowledge_insights(replaces_id);

CREATE TABLE shots (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id               TEXT    NOT NULL,
    set_version_id          INTEGER,

    started_at              TEXT,
    start_epoch             INTEGER NOT NULL DEFAULT 0,
    duration_ms             INTEGER NOT NULL DEFAULT 0,
    profile_version_id      INTEGER REFERENCES profile_versions(id),
    profile_id_on_device    TEXT    NOT NULL DEFAULT '',
    profile_name_on_device  TEXT    NOT NULL DEFAULT '',
    final_weight_g          REAL,
    final_exit_reason       INTEGER,
    brew_delay_ms           INTEGER,
    slog_version            INTEGER,
    sample_interval_ms      INTEGER,
    fields_mask             INTEGER,
    sample_count            INTEGER NOT NULL DEFAULT 0,
    scale_connected         INTEGER NOT NULL DEFAULT 0,
    incomplete              INTEGER NOT NULL DEFAULT 0,

    source                  TEXT    NOT NULL DEFAULT 'device'
                            CHECK (source IN ('device', 'import')),
    deleted_on_device       INTEGER NOT NULL DEFAULT 0,
    quarantined             INTEGER NOT NULL DEFAULT 0,
    quarantine_reason       TEXT,
    raw_slog                BLOB    NOT NULL,

    phases_json             TEXT,
    diagnostics_json        TEXT,
    derivation_version      INTEGER NOT NULL DEFAULT 0,

    index_rating            INTEGER,
    index_volume_g          REAL,
    index_avg_temp_c        REAL,
    index_max_pressure_bar  REAL,
    index_avg_flow_ml_s     REAL,
    index_flags             INTEGER,

    synced_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    -- The number the machine gave the shot and the second it started. Two shots
    -- may share a number (a machine whose counter restarted); at most one of
    -- them is the machine's current one. The pair is unique for a machine whose
    -- clock is set (the firmware gets it from NTP, which needs internet). On a
    -- machine without one `startEpoch` is seconds since boot, two shots can
    -- share both, and the later one is then taken for the earlier one.
    UNIQUE (device_id, start_epoch)
) STRICT;
CREATE INDEX idx_shots_list            ON shots(COALESCE(started_at, '') DESC, id DESC);
CREATE INDEX idx_shots_profile_version ON shots(profile_version_id);
CREATE INDEX idx_shots_quarantined     ON shots(quarantined);
CREATE INDEX idx_shots_started         ON shots(started_at DESC);
CREATE INDEX idx_shots_set_version     ON shots(set_version_id);

CREATE TABLE profile_board (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    label              TEXT    NOT NULL,
    current_version_id INTEGER NOT NULL REFERENCES profile_versions(id),
    device_profile_id  TEXT,
    device_version_id  INTEGER REFERENCES profile_versions(id),
    on_home_screen     INTEGER NOT NULL DEFAULT 1 CHECK (on_home_screen IN (0, 1)),
    origin             TEXT    NOT NULL CHECK (origin IN ('adopted', 'draft')),
    failed_version_id  INTEGER REFERENCES profile_versions(id),
    pending_draft_id   INTEGER REFERENCES profile_drafts(id) ON DELETE SET NULL,
    pending_set_id     INTEGER REFERENCES sets(id) ON DELETE SET NULL,
    pending_major      INTEGER CHECK (pending_major IN (0, 1)),
    deleted_at         TEXT,
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    previous_version_id INTEGER REFERENCES profile_versions(id),
    back_from_version_id INTEGER REFERENCES profile_versions(id),
    back_from_set_version_id INTEGER
    REFERENCES set_versions(id) ON DELETE SET NULL,
    on_machine INTEGER NOT NULL DEFAULT 1
    CHECK (on_machine IN (0, 1)),
    conflict_overruled_hash TEXT
) STRICT;
CREATE INDEX idx_profile_board_version ON profile_board(current_version_id);
CREATE INDEX idx_profile_board_device ON profile_board(device_profile_id);
CREATE UNIQUE INDEX idx_profile_board_live_device ON profile_board(device_profile_id)
    WHERE deleted_at IS NULL AND device_profile_id IS NOT NULL;
CREATE UNIQUE INDEX idx_profile_board_live_pending ON profile_board(pending_draft_id)
    WHERE deleted_at IS NULL AND pending_draft_id IS NOT NULL;

CREATE TABLE profile_board_adoption (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    adopted_at TEXT    NOT NULL,
    host       TEXT    NOT NULL DEFAULT '',
    paused_at  TEXT,
    paused_reason TEXT,
    resume_pending INTEGER NOT NULL DEFAULT 0 CHECK (resume_pending IN (0, 1))
) STRICT;

CREATE TABLE profile_board_versions (
    board_id   INTEGER NOT NULL REFERENCES profile_board(id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL REFERENCES profile_versions(id),
    added_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    source     TEXT    NOT NULL
               CHECK (source IN ('agent', 'edit', 'machine', 'edited_on_machine', 'import')),
    PRIMARY KEY (board_id, version_id)
) STRICT;
CREATE INDEX idx_profile_board_versions_version ON profile_board_versions(version_id);

CREATE TABLE profile_list_build (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    built_at TEXT    NOT NULL
) STRICT;

CREATE TABLE set_outcome_proposals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id           INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    set_version_id   INTEGER NOT NULL REFERENCES set_versions(id) ON DELETE CASCADE,
    thread_id        INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
    outcome          TEXT    NOT NULL
                     CHECK (outcome IN ('held', 'partly_held', 'failed', 'inconclusive')),
    note             TEXT    NOT NULL,
    counted_shots    INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT 'proposed'
                     CHECK (status IN ('proposed', 'accepted', 'changed', 'dismissed', 'superseded')),
    recorded_outcome TEXT
                     CHECK (recorded_outcome IS NULL
                            OR recorded_outcome IN ('held', 'partly_held', 'failed', 'inconclusive')),
    decision_note    TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    decided_at       TEXT
) STRICT;
CREATE UNIQUE INDEX idx_set_outcome_proposals_waiting
    ON set_outcome_proposals(set_version_id) WHERE status = 'proposed';
CREATE INDEX idx_set_outcome_proposals_set ON set_outcome_proposals(set_id, id DESC);

CREATE TABLE insight_placement_build (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    built_at TEXT    NOT NULL,
    moved    INTEGER NOT NULL DEFAULT 0
) STRICT;

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

CREATE TABLE set_version_reverts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id           INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    from_version_id  INTEGER NOT NULL REFERENCES set_versions(id) ON DELETE CASCADE,
    to_version_id    INTEGER NOT NULL REFERENCES set_versions(id) ON DELETE CASCADE,
    note             TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE INDEX idx_set_version_reverts_set ON set_version_reverts(set_id, created_at);
CREATE INDEX idx_set_version_reverts_to ON set_version_reverts(to_version_id);

CREATE TABLE set_versions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id               INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    parent_version_id    INTEGER REFERENCES set_versions(id),
    profile_version_id   INTEGER REFERENCES profile_versions(id),
    grind_setting        TEXT,
    grind_value          REAL,
    dose_g               REAL,
    target_yield_g       REAL,
    intent               TEXT    NOT NULL DEFAULT '',
    origin               TEXT    NOT NULL DEFAULT 'manual'
                         CHECK (origin IN ('manual', 'analysis', 'chat', 'starting_point')),
    origin_analysis_id   INTEGER,
    prediction           TEXT    NOT NULL DEFAULT '',
    compares_to_version_id INTEGER REFERENCES set_versions(id),
    restores_version_id  INTEGER REFERENCES set_versions(id),
    prediction_at        TEXT,
    outcome              TEXT    CHECK (outcome IS NULL OR outcome IN
                              ('held', 'partly_held', 'failed', 'inconclusive')),
    outcome_note         TEXT    NOT NULL DEFAULT '',
    outcome_at           TEXT,
    pushed_device_profile_id TEXT,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    version_major        INTEGER NOT NULL DEFAULT 0,
    version_minor        INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX idx_set_versions_parent ON set_versions(parent_version_id);
CREATE INDEX idx_set_versions_profile ON set_versions(profile_version_id);
CREATE INDEX idx_set_versions_created ON set_versions(set_id, created_at, id);
CREATE UNIQUE INDEX idx_set_versions_name
    ON set_versions(set_id, version_major, version_minor);

CREATE TABLE profile_signatures (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_version_id INTEGER NOT NULL UNIQUE REFERENCES profile_versions(id) ON DELETE CASCADE,
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE signature_expectations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    signature_id            INTEGER NOT NULL REFERENCES profile_signatures(id) ON DELETE CASCADE,
    position                INTEGER NOT NULL,
    tier                    TEXT    NOT NULL CHECK (tier IN ('critical', 'important', 'context')),
    phase                   TEXT,
    kind                    TEXT    NOT NULL
                            CHECK (kind IN ('measure', 'reached', 'expects_warning', 'free_text')),
    expression_json         TEXT,
    warning_fault           TEXT,
    text                    TEXT    NOT NULL DEFAULT '',
    fault                   TEXT,
    sentence                TEXT    NOT NULL,
    reason                  TEXT    NOT NULL DEFAULT '',
    status                  TEXT    NOT NULL DEFAULT 'proposed'
                            CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    reject_reason           TEXT    NOT NULL DEFAULT '',
    needs_phase             INTEGER NOT NULL DEFAULT 0 CHECK (needs_phase IN (0, 1)),
    proposed_by_thread_id   INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
    proposed_by_draft_id    INTEGER REFERENCES profile_drafts(id) ON DELETE SET NULL,
    carried_from_id         INTEGER REFERENCES signature_expectations(id) ON DELETE SET NULL,
    proposed_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    answered_at             TEXT
) STRICT;
CREATE UNIQUE INDEX idx_signature_expectations_position
    ON signature_expectations(signature_id, position);
CREATE INDEX idx_signature_expectations_thread
    ON signature_expectations(proposed_by_thread_id) WHERE proposed_by_thread_id IS NOT NULL;

CREATE TABLE set_version_signature_overrides (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    set_version_id        INTEGER NOT NULL REFERENCES set_versions(id) ON DELETE CASCADE,
    expectation_id        INTEGER NOT NULL REFERENCES signature_expectations(id) ON DELETE CASCADE,
    compare_json          TEXT    NOT NULL,
    reason                TEXT    NOT NULL DEFAULT '',
    status                TEXT    NOT NULL DEFAULT 'proposed'
                          CHECK (status IN ('proposed', 'confirmed', 'rejected', 'withdrawn')),
    reject_reason         TEXT    NOT NULL DEFAULT '',
    proposed_by_thread_id INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
    proposed_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    answered_at           TEXT
) STRICT;
CREATE UNIQUE INDEX idx_set_version_signature_overrides_live
    ON set_version_signature_overrides(set_version_id) WHERE status IN ('proposed', 'confirmed');

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
CREATE INDEX idx_shot_reviews_shot ON shot_reviews(shot_id, id DESC);
CREATE INDEX idx_shot_reviews_status ON shot_reviews(status);
CREATE UNIQUE INDEX idx_shot_reviews_one_running
    ON shot_reviews(shot_id) WHERE status = 'running';

CREATE TABLE review_claims (
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
CREATE INDEX idx_review_claims_status ON review_claims(review_id, status);

CREATE VIEW v_samples AS
SELECT shot_id,
       t_ms,
       t_ms / 1000.0 AS t_s,
       phase_number,
       tt AS target_temperature_c,
       ct AS temperature_c,
       tp AS target_pressure_bar,
       cp AS pressure_bar,
       tf AS target_flow_ml_s,
       fl AS flow_ml_s,
       pf AS puck_flow_ml_s,
       vf AS scale_flow_g_s,
       v  AS weight_g,
       ev AS estimated_weight_g,
       pr AS resistance,
       wp AS water_pumped_ml
  FROM shot_samples;

CREATE VIEW v_grinders AS
SELECT g.id AS grinder_id,
       g.name,
       g.model,
       g.burr_type,
       g.step_unit,
       g.notes,
       g.created_at
  FROM grinders g;

CREATE VIEW v_beans AS
SELECT b.id AS bean_id,
       b.name,
       b.roaster,
       b.origin,
       b.process,
       b.roast_level,
       b.decaf,
       b.acidity,
       b.intensity,
       b.sweetness,
       b.description,
       b.notes,
       b.archived,
       b.created_at
  FROM beans b;

CREATE VIEW v_profiles AS
SELECT pv.id AS profile_version_id,
       pv.label,
       pv.type,
       pv.utility,
       pv.source,
       pv.content_hash,
       pv.created_at,
       (SELECT COUNT(*) FROM shots s WHERE s.profile_version_id = pv.id) AS shot_count
  FROM profile_versions pv
 WHERE NOT (pv.label = 'Empty baseline'
            AND json_extract(pv.json, '$.description') =
                'An empty baseline, created because the archive held no profile to diff a new draft against.');

CREATE VIEW v_sets AS
SELECT st.id AS set_id,
       st.name,
       st.archived,
       st.automatch,
       st.bean_id,
       b.name AS bean_name,
       b.roaster,
       b.roast_level,
       b.process,
       b.origin,
       st.grinder_id,
       g.name AS grinder_name,
       g.step_unit AS grinder_step_unit,
       st.created_at,
       st.current_version_id,
       'v' || cur.version_major
           || CASE WHEN cur.version_minor > 0 THEN '.' || cur.version_minor ELSE '' END
           AS current_version_label,
       (SELECT COUNT(*) FROM set_versions v WHERE v.set_id = st.id) AS version_count,
       (SELECT COUNT(*) FROM shots s
          JOIN set_versions v ON v.id = s.set_version_id
         WHERE v.set_id = st.id) AS shot_count
  FROM sets st
  LEFT JOIN set_versions cur ON cur.id = st.current_version_id
  LEFT JOIN beans b    ON b.id = st.bean_id
  LEFT JOIN grinders g ON g.id = st.grinder_id;

CREATE VIEW v_set_versions AS
SELECT v.id AS set_version_id,
       v.set_id,
       st.name AS set_name,
       v.version_major,
       v.version_minor,
       'v' || v.version_major
           || CASE WHEN v.version_minor > 0 THEN '.' || v.version_minor ELSE '' END
           AS version_label,
       (st.current_version_id = v.id) AS is_current,
       v.parent_version_id,
       'v' || par.version_major
           || CASE WHEN par.version_minor > 0 THEN '.' || par.version_minor ELSE '' END
           AS parent_version_label,
       v.profile_version_id,
       pv.label AS profile_label,
       v.grind_setting,
       v.grind_value,
       v.dose_g,
       v.target_yield_g,
       CASE WHEN json_type(pv.json, '$.temperature') IN ('integer', 'real')
             AND json_extract(pv.json, '$.temperature') > 0
            THEN json_extract(pv.json, '$.temperature') END AS profile_temperature_c,
       v.intent,
       v.origin,
       v.prediction,
       'v' || cmp.version_major
           || CASE WHEN cmp.version_minor > 0 THEN '.' || cmp.version_minor ELSE '' END
           AS compares_to_version_label,
       'v' || res.version_major
           || CASE WHEN res.version_minor > 0 THEN '.' || res.version_minor ELSE '' END
           AS restores_version_label,
       v.outcome,
       v.outcome_note,
       v.created_at,
       (SELECT COUNT(*) FROM shots s WHERE s.set_version_id = v.id) AS shot_count
  FROM set_versions v
  JOIN sets st ON st.id = v.set_id
  LEFT JOIN set_versions par ON par.id = v.parent_version_id
  LEFT JOIN profile_versions pv ON pv.id = v.profile_version_id
  LEFT JOIN set_versions cmp ON cmp.id = v.compares_to_version_id
  LEFT JOIN set_versions res ON res.id = v.restores_version_id;

CREATE VIEW v_shots AS
SELECT s.id                                        AS shot_id,
       s.device_id,
       s.started_at,
       s.duration_ms,
       s.duration_ms / 1000.0                      AS duration_s,
       s.profile_version_id,
       pv.label                                    AS profile_label,
       s.profile_name_on_device,
       COALESCE(s.final_weight_g, s.index_volume_g) AS volume_g,
       s.index_avg_temp_c                          AS avg_temp_c,
       s.index_max_pressure_bar                    AS max_pressure_bar,
       s.index_avg_flow_ml_s                       AS avg_flow_ml_s,
       s.sample_count,
       s.scale_connected,
       s.incomplete,
       s.quarantined,
       s.set_version_id,
       sv.set_id,
       sv.version_major                            AS set_version_major,
       sv.version_minor                            AS set_version_minor,
       'v' || sv.version_major
           || CASE WHEN sv.version_minor > 0 THEN '.' || sv.version_minor ELSE '' END
                                                   AS set_version_label,
       st.name                                     AS set_name,
       sv.grind_setting,
       sv.dose_g                                   AS set_dose_g,
       sv.target_yield_g,
       CASE WHEN json_type(pv.json, '$.temperature') IN ('integer', 'real')
             AND json_extract(pv.json, '$.temperature') > 0
            THEN json_extract(pv.json, '$.temperature') END AS profile_temperature_c,
       b.id                                        AS bean_id,
       b.name                                      AS bean_name,
       b.roast_level,
       b.process,
       b.origin,
       g.id                                        AS grinder_id,
       g.name                                      AS grinder_name,
       j.rating,
       j.balance,
       j.decision,
       j.dose_in_g,
       j.dose_out_g,
       -- Yield over dose: the judgement's dose in when one was entered and the
       -- version's otherwise; the judgement's dose out when entered and the scale's
       -- (else the machine index's) weight otherwise. No dose or no yield, no ratio.
       CASE WHEN COALESCE(NULLIF(j.dose_in_g, 0), sv.dose_g) > 0
             AND COALESCE(j.dose_out_g, s.final_weight_g, s.index_volume_g) IS NOT NULL
            THEN COALESCE(j.dose_out_g, s.final_weight_g, s.index_volume_g)
                 / COALESCE(NULLIF(j.dose_in_g, 0), sv.dose_g) END AS ratio,
       j.notes                                     AS judgement_notes,
       s.diagnostics_json,
       s.synced_at
  FROM shots s
  LEFT JOIN profile_versions pv ON pv.id = s.profile_version_id
  LEFT JOIN set_versions sv     ON sv.id = s.set_version_id
  LEFT JOIN sets st             ON st.id = sv.set_id
  LEFT JOIN beans b             ON b.id = st.bean_id
  LEFT JOIN grinders g          ON g.id = st.grinder_id
  LEFT JOIN shot_judgements j   ON j.shot_id = s.id;

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

CREATE VIEW v_judgements AS
SELECT j.shot_id,
       j.rating,
       j.balance,
       j.taste_notes_json,
       j.aroma_notes_json,
       j.dose_in_g,
       j.dose_out_g,
       j.decision,
       j.notes,
       j.updated_at,
       s.started_at,
       s.set_version_id
  FROM shot_judgements j
  JOIN shots s ON s.id = j.shot_id;

-- ── seed rows ────────────────────────────────────────────────────────

-- The one machine. The app has exactly one, and the row always exists so that nothing has to
-- model its absence; the host is empty until the Settings page names one.
INSERT INTO machines (id, host) VALUES (1, '');

-- The flavour words the judgement form offers, in the order it shows them.
INSERT INTO flavor_picks (kind, note, position) VALUES
    ('aroma', 'floral',                                  0),
    ('aroma', 'fruity',                                  1),
    ('aroma', 'fruity.berry',                            2),
    ('aroma', 'fruity.citrus_fruit',                     3),
    ('aroma', 'roasted',                                 4),
    ('aroma', 'spices',                                  5),
    ('aroma', 'nutty_cocoa.nutty',                       6),
    ('aroma', 'nutty_cocoa.cocoa.chocolate',             7),
    ('aroma', 'sweet.brown_sugar',                       8),
    ('aroma', 'sweet.brown_sugar.caramelized',           9),
    ('taste', 'fruity.berry',                            0),
    ('taste', 'fruity.dried_fruit',                      1),
    ('taste', 'fruity.citrus_fruit',                     2),
    ('taste', 'sour_fermented.alcohol_fermented.winey',  3),
    ('taste', 'other.papery_musty',                      4),
    ('taste', 'other.chemical.bitter',                   5),
    ('taste', 'other.chemical.salty',                    6),
    ('taste', 'nutty_cocoa.nutty',                       7),
    ('taste', 'nutty_cocoa.cocoa.chocolate',             8),
    ('taste', 'nutty_cocoa.cocoa.dark_chocolate',        9),
    ('taste', 'sweet.brown_sugar.caramelized',          10),
    ('taste', 'sweet.brown_sugar.honey',                11);

-- The row counters of five AUTOINCREMENT tables start at zero rather than absent, which is
-- the same to SQLite (the next id is 1) and is what a database built step by step has.
INSERT INTO sqlite_sequence (name, seq) VALUES
    ('device_writes', 0),
    ('knowledge_insights', 0),
    ('shots', 0),
    ('set_versions', 0),
    ('review_claims', 0);
