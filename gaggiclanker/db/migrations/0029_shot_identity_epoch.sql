-- A shot is identified by its number *and* its start time.
--
-- The archive identified a shot by the number the machine gave it (`UNIQUE
-- (device_id)`, since 0016). That number is the firmware's `hi` counter, which
-- lives in NVS with the machine's settings, not with the shot files: after a
-- reflash, a factory reset or a replacement board it starts again from 0 while
-- the clock keeps going. The machine's new shots then carried numbers the
-- archive already held for different shots, and a pull treated them as the old
-- ones: it never fetched them, copied their index rows (rating, volume,
-- temperature, pressure, flow, flags) onto the archived shots and attached
-- their notes cards to the archived shots' judgements.
--
-- Every index row and every `.slog` header carries the shot's start epoch
-- (`indexEntry.timestamp = header.startEpoch`), which is stored as
-- `shots.start_epoch`. The identity is now `(device_id, start_epoch)`. The
-- number stays what a person sees and what `/h/<id>.slog` is fetched by, but
-- several archived shots may now share one.
--
-- `UNIQUE (device_id)` is a table constraint, so its implicit index cannot be
-- dropped and `shots` has to be rebuilt, the way 0016 rebuilt it. Nothing about
-- the rows changes: every column is carried as it is, every id is kept.
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one, and SQLite's DDL is transactional.

-- ── the views come down first ────────────────────────────────────────
--
-- SQLite re-parses every view in the schema when a table is replaced, so a view
-- naming `shots` while it is mid-swap makes the next statement fail with "error
-- in view". The five that read `shots` are dropped here and re-created, text
-- for text as they stood after 0027, at the foot of the file. Their text is
-- API: the SQL tool reads it out of the schema.
DROP VIEW v_shots;
DROP VIEW v_sets;
DROP VIEW v_set_versions;
DROP VIEW v_judgements;
DROP VIEW v_profiles;

-- ── the children are taken out of the way whole ──────────────────────
--
-- Four tables cascade off `shots(id)`. `DROP TABLE shots` performs an implicit
-- DELETE, which fires foreign key *actions* (`PRAGMA defer_foreign_keys` only
-- postpones the violation check), so a plain rebuild would silently take every
-- sample, notes card, judgement and review in the archive with it. They are
-- copied into TEMP tables, deleted, and written straight back once the new
-- `shots` is in place under the old name. The ids never change, so this is a
-- copy out and a copy back, not a remap. `shot_reviews` is the one child that
-- has an id of its own: it is copied with its ids, and the AUTOINCREMENT
-- sequence is untouched because the rows come back with the ids they had.
--
-- Nothing else points at these tables with a foreign key (`sync_events.shot_id`
-- carries none, and Set versions, drafts and insights name analyses and reviews
-- by plain integers), so no other column needs stashing.
CREATE TEMP TABLE _shot_samples AS SELECT * FROM shot_samples;
CREATE TEMP TABLE _device_shot_notes AS SELECT * FROM device_shot_notes;
CREATE TEMP TABLE _shot_judgements AS SELECT * FROM shot_judgements;
CREATE TEMP TABLE _shot_reviews AS SELECT * FROM shot_reviews;
-- The AUTOINCREMENT high-water mark of `shots`, which the copy below would
-- lower to the highest surviving id: an id the archive once gave to a shot that
-- has since been deleted must never be handed out again.
CREATE TEMP TABLE _shots_sequence AS SELECT seq FROM sqlite_sequence WHERE name = 'shots';

DELETE FROM shot_reviews;
DELETE FROM shot_judgements;
DELETE FROM device_shot_notes;
DELETE FROM shot_samples;

-- Column for column what the table holds now (0016's, plus 0028's
-- `derivation_version`), with the identity key widened. `raw_slog` is still
-- NOT NULL and still the source of truth.
CREATE TABLE shots_new (
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
    execution_score         REAL,
    execution_reason        TEXT,
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

INSERT INTO shots_new
    (id, device_id, set_version_id, started_at, start_epoch, duration_ms,
     profile_version_id, profile_id_on_device, profile_name_on_device, final_weight_g,
     final_exit_reason, brew_delay_ms, slog_version, sample_interval_ms, fields_mask,
     sample_count, scale_connected, incomplete, source, deleted_on_device, quarantined,
     quarantine_reason, raw_slog, phases_json, diagnostics_json, execution_score,
     execution_reason, derivation_version, index_rating, index_volume_g,
     index_avg_temp_c, index_max_pressure_bar, index_avg_flow_ml_s, index_flags,
     synced_at, updated_at)
SELECT id, device_id, set_version_id, started_at, start_epoch, duration_ms,
       profile_version_id, profile_id_on_device, profile_name_on_device, final_weight_g,
       final_exit_reason, brew_delay_ms, slog_version, sample_interval_ms, fields_mask,
       sample_count, scale_connected, incomplete, source, deleted_on_device, quarantined,
       quarantine_reason, raw_slog, phases_json, diagnostics_json, execution_score,
       execution_reason, derivation_version, index_rating, index_volume_g,
       index_avg_temp_c, index_max_pressure_bar, index_avg_flow_ml_s, index_flags,
       synced_at, updated_at
FROM shots;

-- No rows are left in any child table, so the implicit DELETE inside this DROP
-- has nothing to cascade into and nothing to count as a violation.
DROP TABLE shots;
ALTER TABLE shots_new RENAME TO shots;

-- The indexes, unchanged and for their original reasons (0002 and 0016 have the
-- long versions): the list sorts on `COALESCE(started_at, '') DESC, id DESC`
-- and SQLite cannot see through the COALESCE to a plain index on the column.
CREATE INDEX idx_shots_list            ON shots(COALESCE(started_at, '') DESC, id DESC);
CREATE INDEX idx_shots_profile_version ON shots(profile_version_id);
CREATE INDEX idx_shots_quarantined     ON shots(quarantined);
CREATE INDEX idx_shots_started         ON shots(started_at DESC);
CREATE INDEX idx_shots_set_version     ON shots(set_version_id);

UPDATE sqlite_sequence
   SET seq = (SELECT seq FROM _shots_sequence)
 WHERE name = 'shots' AND seq < (SELECT seq FROM _shots_sequence);
INSERT INTO sqlite_sequence (name, seq)
SELECT 'shots', seq FROM _shots_sequence
 WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'shots');
DROP TABLE _shots_sequence;

-- The children, back where they were.
INSERT INTO shot_samples SELECT * FROM _shot_samples;
INSERT INTO device_shot_notes SELECT * FROM _device_shot_notes;
INSERT INTO shot_judgements SELECT * FROM _shot_judgements;
INSERT INTO shot_reviews SELECT * FROM _shot_reviews;

DROP TABLE _shot_samples;
DROP TABLE _device_shot_notes;
DROP TABLE _shot_judgements;
DROP TABLE _shot_reviews;

-- ── the views, back ──────────────────────────────────────────────────
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
       (SELECT COUNT(*) FROM set_versions v WHERE v.set_id = st.id) AS version_count,
       (SELECT COUNT(*) FROM shots s
          JOIN set_versions v ON v.id = s.set_version_id
         WHERE v.set_id = st.id) AS shot_count
  FROM sets st
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
       sv.version_no                               AS set_version_no,
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
       v.version_no,
       v.version_major,
       v.version_minor,
       'v' || v.version_major
           || CASE WHEN v.version_minor > 0 THEN '.' || v.version_minor ELSE '' END
           AS version_label,
       v.parent_version_id,
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
       cmp.version_no AS compares_to_version_no,
       'v' || cmp.version_major
           || CASE WHEN cmp.version_minor > 0 THEN '.' || cmp.version_minor ELSE '' END
           AS compares_to_version_label,
       res.version_no AS restores_version_no,
       'v' || res.version_major
           || CASE WHEN res.version_minor > 0 THEN '.' || res.version_minor ELSE '' END
           AS restores_version_label,
       v.outcome,
       v.outcome_note,
       v.created_at,
       (SELECT COUNT(*) FROM shots s WHERE s.set_version_id = v.id) AS shot_count
  FROM set_versions v
  JOIN sets st ON st.id = v.set_id
  LEFT JOIN profile_versions pv ON pv.id = v.profile_version_id
  LEFT JOIN set_versions cmp ON cmp.id = v.compares_to_version_id
  LEFT JOIN set_versions res ON res.id = v.restores_version_id;

