-- migration: foreign-keys-off
--
-- A Set version's name is an identifier, not a position: no ordinal, a pointer
-- to the version the Set is on, and a record of every time it went back.
--
-- Until now `set_versions.version_no` ("the Nth version of this Set") ordered
-- the versions and meant "the current one is the highest". A revert that moves
-- the Set back onto an older version cannot be said that way, and the name
-- (`version_major`.`version_minor`) already says everything a person reads, so:
--
--   * `sets.current_version_id` is the version the Set is on, written by every
--     append and by every revert. Backfilled here from the old rule (the highest
--     ordinal), so every Set is on the version it was on a moment ago.
--   * versions are ordered by when they were made (`created_at`, with the row id
--     breaking a tie inside the database only).
--   * `set_version_reverts` is the log of a revert: which Set, from which version
--     to which, an optional note, and when. A revert writes no version.
--   * `version_no` is removed. It sits in an inline `UNIQUE (set_id, version_no)`
--     that `DROP COLUMN` refuses, so the table is rebuilt.
--
-- **The rebuild uses SQLite's own twelve steps**, which is what the first line
-- of this file asks the runner for: foreign keys off before the transaction,
-- `PRAGMA foreign_key_check` before the commit, back on afterwards. With them
-- on, `DROP TABLE set_versions` would delete (or count as violations) every row
-- in the tables that point at it: chat threads, proposals and outcome proposals
-- (all cascade), insights, drafts and the board (all set null), and the three
-- self-references. Every id is copied across unchanged, so nothing that points
-- at a version has to be touched. The runner refuses the whole file if it leaves
-- a reference dangling that was not dangling before.
--
-- The views that read `set_versions` are dropped first and created again
-- afterwards (SQLite re-parses every view when a table is renamed). They lose
-- the ordinal columns; `v_set_versions` gains `is_current` and
-- `parent_version_label`, `v_sets` the current version's id and name.
--
-- No transaction control in this file: the runner wraps it plus its ledger row.

-- ── the pointer, filled from the old rule ────────────────────────────

ALTER TABLE sets ADD COLUMN current_version_id INTEGER REFERENCES set_versions(id);

UPDATE sets
   SET current_version_id = (SELECT v.id FROM set_versions v
                              WHERE v.set_id = sets.id
                              ORDER BY v.version_no DESC LIMIT 1);

-- ── the log of a revert ──────────────────────────────────────────────

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

-- ── the rebuild ──────────────────────────────────────────────────────

-- AUTOINCREMENT keeps its counter in `sqlite_sequence`, which the rebuild would
-- reset to the highest surviving id: a version id given out once and since
-- deleted with its Set could then be given out again. Kept aside, put back below.
CREATE TEMP TABLE _sv_sequence AS
SELECT seq FROM sqlite_sequence WHERE name = 'set_versions';

DROP VIEW v_shots;
DROP VIEW v_sets;
DROP VIEW v_set_versions;

CREATE TABLE set_versions_new (
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

INSERT INTO set_versions_new
    (id, set_id, parent_version_id, profile_version_id, grind_setting, grind_value,
     dose_g, target_yield_g, intent, origin, origin_analysis_id, prediction,
     compares_to_version_id, restores_version_id, prediction_at, outcome,
     outcome_note, outcome_at, pushed_device_profile_id, created_at,
     version_major, version_minor)
SELECT id, set_id, parent_version_id, profile_version_id, grind_setting, grind_value,
       dose_g, target_yield_g, intent, origin, origin_analysis_id, prediction,
       compares_to_version_id, restores_version_id, prediction_at, outcome,
       outcome_note, outcome_at, pushed_device_profile_id, created_at,
       version_major, version_minor
  FROM set_versions;

DROP TABLE set_versions;
ALTER TABLE set_versions_new RENAME TO set_versions;

CREATE INDEX idx_set_versions_parent ON set_versions(parent_version_id);
CREATE INDEX idx_set_versions_profile ON set_versions(profile_version_id);
CREATE INDEX idx_set_versions_created ON set_versions(set_id, created_at, id);
CREATE UNIQUE INDEX idx_set_versions_name
    ON set_versions(set_id, version_major, version_minor);

UPDATE sqlite_sequence
   SET seq = MAX(seq, COALESCE((SELECT seq FROM _sv_sequence), 0))
 WHERE name = 'set_versions';

DROP TABLE _sv_sequence;

-- ── the views, without the ordinal ───────────────────────────────────

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
       s.execution_score,
       s.execution_reason,
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
