-- The starting-point wizard — a first Set version for a new bag.
--
-- Two changes, and they are the two halves of one feature: a Set version has to
-- be able to say it came from the wizard, and the wizard's own run has to be a
-- row so that "what was it told, what did it answer, and what did that cost"
-- survives the browser tab that asked.
--
-- No transaction control here: the runner wraps the whole file plus its ledger
-- row in one, and SQLite's DDL is transactional.

-- ── set_versions.origin gains 'starting_point' ───────────────────────
--
-- STRICT tables cannot have a CHECK constraint altered in place, so the table
-- is rebuilt: new table, copy, drop, rename, indexes re-created — the same
-- dance 0010 did to `device_writes`, and for the same reason. The column
-- order, defaults, foreign keys and indexes are identical to 0005's apart from
-- the widened CHECK; read the comments there for why each column exists.
--
-- `starting_point` matters because it is the only origin that can appear on a
-- **first** version. Every other origin describes a change to something that
-- already existed; this one describes the cold start, which is the one the
-- wizard exists to make less cold and therefore the one worth measuring.
--
-- **The hard part is that four columns point at this table**:
-- `set_versions.parent_version_id`, `compares_to_version_id` and
-- `restores_version_id` at itself, and
-- `suggestions.resulting_set_version_id` from 0006. The connection runs with
-- `foreign_keys = ON`, and `PRAGMA foreign_keys` is a no-op inside a
-- transaction, so the standard 12-step rebuild is not available here.
--
-- `defer_foreign_keys` alone is NOT enough, and getting that wrong is how this
-- migration first shipped broken. It postpones the *check*, not the counting:
-- the implicit DELETE inside `DROP TABLE set_versions` increments the deferred
-- violation counter once per surviving child row, and nothing decrements it
-- again, so COMMIT fails with "FOREIGN KEY constraint failed" on any install
-- that has a second Set version or an accepted suggestion. On a fresh database
-- it passes, which is exactly why it needs a test on a populated one
-- (`tests/test_migrations.py::test_0014_upgrades_a_populated_database`).
--
-- So the links are taken out of the way first and put back afterwards. Every
-- child reference is stashed in a TEMP table and set to NULL; a NULL foreign
-- key is always satisfied, so the DROP then deletes rows nothing points at and
-- the counter never moves. Once the new table is in place under the old name,
-- the stashed values are written straight back — the ids never changed, only
-- the table holding them did.
PRAGMA defer_foreign_keys = ON;

-- The two stashes. TEMP, so they live in the temp schema and cannot collide
-- with a real table, and dropped explicitly at the foot of this file rather
-- than left to the connection's lifetime. The first holds all three
-- self-references of a row: they are nulled and restored together, because a
-- single surviving one is enough to move the deferred counter.
CREATE TEMP TABLE _sv_parents AS
SELECT id, parent_version_id, compares_to_version_id, restores_version_id
  FROM set_versions
 WHERE parent_version_id IS NOT NULL
    OR compares_to_version_id IS NOT NULL
    OR restores_version_id IS NOT NULL;

CREATE TEMP TABLE _sv_suggestions AS
SELECT id, resulting_set_version_id FROM suggestions
 WHERE resulting_set_version_id IS NOT NULL;

UPDATE set_versions
   SET parent_version_id = NULL, compares_to_version_id = NULL, restores_version_id = NULL
 WHERE parent_version_id IS NOT NULL
    OR compares_to_version_id IS NOT NULL
    OR restores_version_id IS NOT NULL;
UPDATE suggestions SET resulting_set_version_id = NULL WHERE resulting_set_version_id IS NOT NULL;

-- The three curated views from 0013 read `set_versions`, and SQLite re-parses
-- every view whenever the schema changes: leaving them in place makes the
-- statement after the DROP fail with "error in view v_shots: no such table".
-- They are dropped here and re-created verbatim below — a view is a stored
-- SELECT and nothing rewrites one when the table underneath it is replaced.
-- Their text is API: `describe_schema()` reads it out of here and the SQL
-- tool's allow-list is exactly these names.
DROP VIEW v_shots;
DROP VIEW v_sets;
DROP VIEW v_set_versions;

CREATE TABLE set_versions_new (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id               INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    version_no           INTEGER NOT NULL,
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

    UNIQUE (set_id, version_no)
) STRICT;

-- The three self-references are copied as NULL and restored below. Until the
-- rename, this table's own foreign keys still name the OLD `set_versions`, so a
-- row carrying one here would be a child of the table the next statement
-- drops — which is the counter this whole dance exists to avoid touching.
INSERT INTO set_versions_new
    (id, set_id, version_no, parent_version_id, profile_version_id, grind_setting,
     grind_value, dose_g, target_yield_g, intent, origin,
     origin_analysis_id, prediction, compares_to_version_id, restores_version_id,
     prediction_at, outcome, outcome_note, outcome_at,
     pushed_device_profile_id, created_at)
SELECT id, set_id, version_no, NULL, profile_version_id, grind_setting,
       grind_value, dose_g, target_yield_g, intent, origin,
       origin_analysis_id, prediction, NULL, NULL,
       prediction_at, outcome, outcome_note, outcome_at,
       pushed_device_profile_id, created_at
FROM set_versions;

DROP TABLE set_versions;
ALTER TABLE set_versions_new RENAME TO set_versions;

CREATE INDEX idx_set_versions_parent ON set_versions(parent_version_id);
CREATE INDEX idx_set_versions_profile ON set_versions(profile_version_id);

-- The links, back where they were. The ids are unchanged — only the table
-- holding them was replaced — so this is a straight write-back rather than a
-- remap, and a version whose parent was somehow missing from the stash simply
-- stays NULL rather than failing the upgrade.
UPDATE set_versions
   SET parent_version_id =
       (SELECT p.parent_version_id FROM _sv_parents p WHERE p.id = set_versions.id),
       compares_to_version_id =
       (SELECT p.compares_to_version_id FROM _sv_parents p WHERE p.id = set_versions.id),
       restores_version_id =
       (SELECT p.restores_version_id FROM _sv_parents p WHERE p.id = set_versions.id)
 WHERE id IN (SELECT id FROM _sv_parents);

UPDATE suggestions
   SET resulting_set_version_id =
       (SELECT s.resulting_set_version_id FROM _sv_suggestions s WHERE s.id = suggestions.id)
 WHERE id IN (SELECT id FROM _sv_suggestions);

DROP TABLE _sv_parents;
DROP TABLE _sv_suggestions;

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

-- ── the runs ─────────────────────────────────────────────────────────
--
-- One row per "suggest a starting point", opened before the provider is
-- contacted and closed when it answers — the analysis row's shape, for the
-- analysis row's reasons: a process that dies mid-call leaves a `running` row
-- the next boot can mark `interrupted`, and a provider failure is a stored
-- `failed` row rather than an exception that loses everything the run was told.
--
-- `input_json` is the whole assembled context (bean, hardware, the similar Sets
-- with their outcomes, the selected rules, the retrieved excerpts). Stored
-- verbatim because a suggestion is only checkable if what it was told is still
-- readable after the bean has been finished and the rules edited.
--
-- `accepted_*` is the other half of the feature: which option somebody took,
-- what Set it became, and — when the option carried a whole profile — which
-- draft is waiting for them in the Drafts queue. Nullable for as long as
-- nobody has chosen, which is most rows: three options are offered and a
-- person is allowed to like none of them.
CREATE TABLE starting_point_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    bean_id           INTEGER NOT NULL REFERENCES beans(id) ON DELETE CASCADE,
    machine_id        INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
    -- Nullable for the same reason `sets.grinder_id` is: pre-ground coffee and
    -- a grinder nobody has recorded are both real. The similar-Set query then
    -- finds nothing and the rules tier carries the whole answer, which is
    -- exactly the cold start this feature is named after.
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

-- The wizard's own list: "what have I asked about this bag before".
CREATE INDEX idx_starting_point_runs_bean ON starting_point_runs(bean_id, id DESC);
-- Boot reconciliation's query, the same one the analyses table answers.
CREATE INDEX idx_starting_point_runs_status ON starting_point_runs(status, created_at DESC);
