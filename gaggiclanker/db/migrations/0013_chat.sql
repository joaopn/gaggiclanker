-- The chat and the MCP server: the transcript, and the curated views the
-- `query_shots` tool is allowed to see.
--
-- Two halves that only look unrelated. The first is the conversation itself —
-- threads, messages, runs, the streamed events and the tool-call audit — and it
-- is ordinary bookkeeping. The second is `v_*`: a closed set of read-only views
-- that are the **only** tables the SQL tool may touch, in the chat and over
-- MCP alike.
--
-- Why views rather than "SELECT over the real tables with a blocklist". A
-- blocklist has to enumerate everything dangerous and is wrong the day a table
-- is added; an allow-list of views is wrong only in the direction of refusing
-- something useful. The views also hide the two columns nothing should ever
-- pull into a prompt — `shots.raw_slog` (megabytes of binary per row) and the
-- password hash in `settings` — without the tool having to know they exist.
--
-- No transaction control here: the runner wraps the whole file plus its ledger
-- row in one, and SQLite's DDL is transactional.

-- ── threads ──────────────────────────────────────────────────────────
--
-- A thread is one of two things, and the pair of columns says which. **General**
-- has both NULL: "what does a 1:2 ratio mean" is a real question with no coffee
-- behind it, and such a thread reads the whole archive and changes no Set.
-- **A Set thread** has both set: it is about one version of one Set, it sees
-- that Set and nothing else, and the version is the change being argued rather
-- than "the Set in general" — which is why the version is stored rather than
-- read as "whichever is current now". The pair is kept true by
-- `ChatRepository`, where the version can be checked against its Set; a CHECK
-- here could only say "both or neither" and would report the interesting half
-- (a version of somebody else's Set) as a constraint failure.
--
-- Both references CASCADE, and the alternative is why. Nothing deletes a Set
-- today — they are archived — but `SET NULL` would mean that the day something
-- does, every conversation about it becomes a **general** conversation: the
-- scope is these two columns, so the transcript of one coffee's experiments
-- would come back with the whole archive's tools attached to it. A Set's
-- conversations are about that Set and go with it.
CREATE TABLE chat_threads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT    NOT NULL DEFAULT '',
    set_id         INTEGER REFERENCES sets(id) ON DELETE CASCADE,
    set_version_id INTEGER REFERENCES set_versions(id) ON DELETE CASCADE,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_chat_threads_updated ON chat_threads(updated_at DESC, id DESC);

-- "The most recently updated conversation about this version", which is what
-- Review and Discuss continue rather than starting a third room about one
-- change.
CREATE INDEX idx_chat_threads_version ON chat_threads(set_version_id, updated_at DESC, id DESC);

-- ── runs ─────────────────────────────────────────────────────────────
--
-- A run is one press of Send: a system prompt, the history, and however many
-- tool rounds it takes to answer. `status` is reconciled at boot the way an
-- analysis is — a `running` row is only true while a process holds it, and no
-- process survives a restart.
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

-- ── messages ─────────────────────────────────────────────────────────
--
-- One row per turn, `tool` included: a tool result is part of the transcript a
-- later turn is shown, so folding it into the assistant message would make the
-- history unreplayable. `tool_calls_json` is what the assistant asked for and
-- `tool_results_json` is what came back, both as the provider-agnostic shape
-- `gaggiclanker/llm/chat_types.py` defines.
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

-- ── the stream, persisted ────────────────────────────────────────────
--
-- Every event the SSE stream emits is written here first. That is what makes a
-- reconnect a replay rather than a shrug: a phone that locked mid-answer comes
-- back, asks for everything after the last `seq` it saw, and catches up. The
-- rows are small and a run is bounded, so this does not need pruning before it
-- needs a product decision about transcript retention.
CREATE TABLE chat_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  INTEGER NOT NULL REFERENCES chat_runs(id) ON DELETE CASCADE,
    seq     INTEGER NOT NULL,
    kind    TEXT    NOT NULL,
    data    TEXT    NOT NULL DEFAULT '{}',
    at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    UNIQUE (run_id, seq)
) STRICT;

-- ── the tool audit ───────────────────────────────────────────────────
--
-- Every dispatch, whoever asked: the in-app chat (`run_id` set), an MCP client
-- (`caller = 'mcp'`), or a test. The input is stored as a hash rather than
-- verbatim because a tool argument can carry a whole SQL statement and a
-- transcript already holds the readable copy; what this table is for is "which
-- tool did what, how long did it take, and did it work".
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

-- ── the curated views ────────────────────────────────────────────────
--
-- The allow-list `gaggiclanker/tools/sql.py` enforces. Names are stable API:
-- `describe_schema()` reads them out of here, the model is told these are the
-- only tables that exist, and an authorizer callback refuses everything else.
--
-- `v_shots` deliberately flattens the four things a question is usually about —
-- what the machine did, what the person thought, which Set it belonged to and
-- what the analyzer made of it — because the alternative is a five-way join in
-- every generated query and a model that gets one of them wrong.
CREATE VIEW v_shots AS
SELECT s.id                                        AS shot_id,
       s.device_id,
       s.machine_id,
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
       s.execution_score,
       s.execution_reason,
       s.sample_count,
       s.scale_connected,
       s.incomplete,
       s.quarantined,
       s.set_version_id,
       sv.set_id,
       sv.version_no                               AS set_version_no,
       st.name                                     AS set_name,
       sv.grind_setting,
       sv.dose_g                                   AS set_dose_g,
       sv.target_yield_g,
       -- The brew temperature is the profile's, never the Set's: the machine
       -- heats to what the document says. Read out of the profile this shot was
       -- pulled with, with the firmware's 0 ("not set") nulled out, which is how
       -- `profile_recipe` reads the same field.
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
       CASE WHEN j.dose_in_g > 0 THEN j.dose_out_g / j.dose_in_g END AS ratio,
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

-- 200-odd rows per shot. Exposed because "flow against target over the last
-- third of the shot" is a real question and the alternative is shipping the
-- whole curve into the prompt; the row cap is what keeps it honest.
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

CREATE VIEW v_sets AS
SELECT st.id AS set_id,
       st.name,
       st.status,
       st.active,
       st.machine_id,
       st.bean_id,
       b.name AS bean_name,
       b.roaster,
       b.roast_level,
       b.process,
       b.origin,
       b.roast_date,
       st.grinder_id,
       g.name AS grinder_name,
       g.step_unit AS grinder_step_unit,
       st.created_at,
       (SELECT COUNT(*) FROM set_versions v WHERE v.set_id = st.id) AS version_count,
       (SELECT COUNT(*) FROM shots s
          JOIN set_versions v ON v.id = s.set_version_id
         WHERE v.set_id = st.id) AS shot_count
  FROM sets st
  LEFT JOIN beans b    ON b.id = st.bean_id
  LEFT JOIN grinders g ON g.id = st.grinder_id;

CREATE VIEW v_set_versions AS
SELECT v.id AS set_version_id,
       v.set_id,
       st.name AS set_name,
       v.version_no,
       v.parent_version_id,
       v.profile_version_id,
       pv.label AS profile_label,
       v.grind_setting,
       v.grind_value,
       v.dose_g,
       v.target_yield_g,
       -- The brew temperature this version is brewed at: the profile's own,
       -- nulled out at the firmware's 0 ("not set"), exactly as
       -- `profile_recipe` reads it. A Set version states no temperature of its
       -- own — changing it means changing the profile.
       CASE WHEN json_type(pv.json, '$.temperature') IN ('integer', 'real')
             AND json_extract(pv.json, '$.temperature') > 0
            THEN json_extract(pv.json, '$.temperature') END AS profile_temperature_c,
       v.intent,
       v.origin,
       -- The experiment half of a version: what it was expected to do, against
       -- which version, and how that turned out. The two comparisons are joined
       -- back to their version *numbers* because a model reading this view
       -- reasons in "v3", never in a row id.
       v.prediction,
       cmp.version_no AS compares_to_version_no,
       res.version_no AS restores_version_no,
       v.outcome,
       v.outcome_note,
       v.created_at,
       (SELECT COUNT(*) FROM shots s WHERE s.set_version_id = v.id) AS shot_count
  FROM set_versions v
  JOIN sets st ON st.id = v.set_id
  LEFT JOIN profile_versions pv ON pv.id = v.profile_version_id
  LEFT JOIN set_versions cmp ON cmp.id = v.compares_to_version_id
  LEFT JOIN set_versions res ON res.id = v.restores_version_id;

CREATE VIEW v_judgements AS
SELECT j.shot_id,
       j.rating,
       j.balance,
       j.taste_notes_json,
       j.aroma_notes_json,
       j.dose_in_g,
       j.dose_out_g,
       j.grind_setting,
       j.decision,
       j.notes,
       j.updated_at,
       s.started_at,
       s.set_version_id
  FROM shot_judgements j
  JOIN shots s ON s.id = j.shot_id;

-- `input_json` is omitted on purpose: it is the whole rendered prompt, tens of
-- kilobytes a row, and a `SELECT *` over it would blow the row cap's budget on
-- text the model wrote in the first place.
CREATE VIEW v_analyses AS
SELECT a.id AS analysis_id,
       a.shot_id,
       a.set_version_id,
       a.provider,
       a.model,
       a.status,
       a.error,
       a.output_json,
       a.created_at,
       a.finished_at
  FROM shot_analyses a;

CREATE VIEW v_suggestions AS
SELECT sg.id AS suggestion_id,
       sg.analysis_id,
       a.shot_id,
       sg.variable,
       sg.direction,
       sg.magnitude,
       sg.unit,
       sg.reason,
       sg.confidence,
       sg.priority,
       sg.status,
       sg.resulting_set_version_id
  FROM suggestions sg
  JOIN shot_analyses a ON a.id = sg.analysis_id;

-- `json` (the whole profile document) is omitted for the reason `input_json`
-- is: it is the payload, not a fact to aggregate over. `get_shot` and the
-- profile API are how a document is read.
CREATE VIEW v_profiles AS
SELECT pv.id AS profile_version_id,
       pv.label,
       pv.type,
       pv.utility,
       pv.source,
       pv.content_hash,
       pv.created_at,
       (SELECT COUNT(*) FROM shots s WHERE s.profile_version_id = pv.id) AS shot_count
  FROM profile_versions pv;

CREATE VIEW v_beans AS
SELECT b.id AS bean_id,
       b.name,
       b.roaster,
       b.origin,
       b.altitude_m,
       b.process,
       b.roast_level,
       b.roast_date,
       b.decaf,
       b.description,
       b.notes,
       b.archived,
       b.created_at
  FROM beans b;

CREATE VIEW v_grinders AS
SELECT g.id AS grinder_id,
       g.name,
       g.model,
       g.burr_type,
       g.step_unit,
       g.notes,
       g.created_at
  FROM grinders g;
