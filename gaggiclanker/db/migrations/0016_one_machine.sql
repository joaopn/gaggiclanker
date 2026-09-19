-- One machine.
--
-- The archive was built to hold several: a `machines` table keyed by host, a
-- `machine_id` on shots, device profiles, Sets, cleanup runs and starting-point
-- runs, shots unique per `(machine_id, device_id)`, one active Set *per
-- machine*. Nobody runs two GaggiMates against one archive, and the generality
-- cost correctness rather than merely surface:
--
--   * identity was the configured host, so a machine that changed address
--     became a second machine — its shots, profiles and Sets split off, its
--     active Set stopped auto-assigning, and nothing merged them back;
--   * an import done before the machine was configured landed on a synthetic
--     `import:default` machine, which is the natural order for a new install,
--     and the same shot could then exist twice because the unique key included
--     the machine.
--
-- After this the row is a singleton describing whatever host is configured now:
-- the host is a setting, not an identity. Shots are unique by their device id,
-- there is one active Set, and the analyzer still reads the machine's facts
-- (hardware string, capabilities, temperature offset, brew delay) from the one
-- row. Grinders stay plural: a kitchen really does have several, and a grind
-- number only means something on the grinder it was set on.
--
-- The table keeps its plural name. Renaming it would be a second rebuild for a
-- cosmetic gain.
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one, and SQLite's DDL is transactional.

-- ── the views come down first ────────────────────────────────────────
--
-- SQLite re-parses every view in the schema whenever a table is altered or
-- replaced, so a view naming a table that is mid-swap makes the *next*
-- statement fail with "error in view v_shots: no such table". Five of the ten
-- curated views read `shots` or `sets`; they are dropped here and re-created at
-- the foot of the file. Two of them (`v_shots`, `v_sets`) lose the `machine_id`
-- column; the other three are re-created verbatim, because they read the tables
-- rather than the column.
--
-- Their text is API: `describe_schema()` reads it out of the schema and the SQL
-- tool's allow-list is exactly these names.
DROP VIEW v_shots;
DROP VIEW v_sets;
DROP VIEW v_set_versions;
DROP VIEW v_judgements;
DROP VIEW v_profiles;

-- The partial unique index enforcing "one active Set per machine" and the
-- lookup index beside it both name `machine_id`. They come down before anything
-- is re-pointed, which is also what makes the re-pointing below safe: two
-- machines could each have an active Set, and collapsing them onto one id would
-- otherwise violate the index mid-migration.
DROP INDEX idx_sets_one_active_per_machine;
DROP INDEX idx_sets_machine;
DROP INDEX idx_cleanup_runs_machine;

-- ── 1. which machine survives ────────────────────────────────────────
--
-- A real host beats the importer's synthetic `import:<label>` placeholder: the
-- placeholder exists only because `shots.machine_id` was NOT NULL and an import
-- had to file its rows under something. Among several real hosts the most
-- recently seen wins, because that is the one the container is pointed at now.
-- `last_seen_at` is bumped on every identity refresh, so it is the freshest
-- statement of that; `id` breaks a tie deterministically.
--
-- A TEMP table rather than a repeated sub-select: this value is read a dozen
-- times below and a migration that computes its own pivot twice is a migration
-- that can disagree with itself.
CREATE TEMP TABLE _survivor AS
SELECT id
  FROM machines
 ORDER BY (host NOT LIKE 'import:%') DESC, last_seen_at DESC, id DESC
 LIMIT 1;

-- ── 2. de-duplicate shots by device id ───────────────────────────────
--
-- Before anything is re-pointed, because `shots` still carries
-- `UNIQUE (machine_id, device_id)`: the same shot imported under the
-- placeholder and pulled from the live machine is two rows with one device id,
-- and moving them onto one machine id would violate that index rather than
-- merge them.
--
-- The keeper is the `source = 'device'` row — bytes read off the machine beat
-- bytes read out of a JSON export, which is a re-encoding of them — and the
-- newest by id otherwise.
CREATE TEMP TABLE _shot_keepers AS
SELECT device_id,
       (SELECT s2.id
          FROM shots s2
         WHERE s2.device_id = s.device_id
         ORDER BY (s2.source = 'device') DESC, s2.id DESC
         LIMIT 1) AS keeper_id
  FROM shots s
 GROUP BY device_id;

CREATE TEMP TABLE _shot_merge AS
SELECT s.id AS loser_id, k.keeper_id
  FROM shots s
  JOIN _shot_keepers k ON k.device_id = s.device_id
 WHERE s.id <> k.keeper_id;

-- What the loser owns that the keeper does not is moved across; the rest
-- cascades with the row. The decision is snapshotted into a TEMP table *before*
-- the UPDATE runs, deliberately: a WHERE clause that asks "does the keeper have
-- samples yet" is re-evaluated per row, so half a curve would move and the
-- second half would find the keeper occupied and stay behind to be deleted.
-- Deciding once, up front, moves a curve whole or not at all.
CREATE TEMP TABLE _move_samples AS
SELECT loser_id, keeper_id FROM _shot_merge m
 WHERE EXISTS (SELECT 1 FROM shot_samples WHERE shot_id = m.loser_id)
   AND NOT EXISTS (SELECT 1 FROM shot_samples WHERE shot_id = m.keeper_id);

UPDATE shot_samples
   SET shot_id = (SELECT keeper_id FROM _move_samples m WHERE m.loser_id = shot_samples.shot_id)
 WHERE shot_id IN (SELECT loser_id FROM _move_samples);

-- The user's verdict on the cup. One row per shot, so it moves only onto a
-- keeper that has none — and a verdict typed here is the least replaceable
-- thing in the archive, which is why it is moved rather than left to cascade.
CREATE TEMP TABLE _move_judgements AS
SELECT loser_id, keeper_id FROM _shot_merge m
 WHERE EXISTS (SELECT 1 FROM shot_judgements WHERE shot_id = m.loser_id)
   AND NOT EXISTS (SELECT 1 FROM shot_judgements WHERE shot_id = m.keeper_id);

UPDATE shot_judgements
   SET shot_id = (SELECT keeper_id FROM _move_judgements m WHERE m.loser_id = shot_judgements.shot_id)
 WHERE shot_id IN (SELECT loser_id FROM _move_judgements);

-- The mirror of the machine's own notes card. Same shape, same rule.
CREATE TEMP TABLE _move_notes AS
SELECT loser_id, keeper_id FROM _shot_merge m
 WHERE EXISTS (SELECT 1 FROM device_shot_notes WHERE shot_id = m.loser_id)
   AND NOT EXISTS (SELECT 1 FROM device_shot_notes WHERE shot_id = m.keeper_id);

UPDATE device_shot_notes
   SET shot_id = (SELECT keeper_id FROM _move_notes m WHERE m.loser_id = device_shot_notes.shot_id)
 WHERE shot_id IN (SELECT loser_id FROM _move_notes);

-- Analyses are many per shot and nothing makes them unique, so every one moves
-- and its suggestions follow it by their own foreign key.
UPDATE shot_analyses
   SET shot_id = (SELECT keeper_id FROM _shot_merge m WHERE m.loser_id = shot_analyses.shot_id)
 WHERE shot_id IN (SELECT loser_id FROM _shot_merge);

-- The sync feed's shot ids carry no foreign key, so nothing would have caught
-- them dangling. A line in the feed that points at a deleted row reads as a
-- broken link on the page that shows it.
UPDATE sync_events
   SET shot_id = (SELECT keeper_id FROM _shot_merge m WHERE m.loser_id = sync_events.shot_id)
 WHERE shot_id IN (SELECT loser_id FROM _shot_merge);

-- "What were you brewing" is the user's answer, not the machine's, and the
-- import placeholder's copy of a shot is exactly the one likely to be missing
-- it. Copied only onto a keeper that has none: an assignment made by hand is
-- never overwritten.
UPDATE shots
   SET set_version_id = (SELECT l.set_version_id
                           FROM shots l
                           JOIN _shot_merge m ON m.loser_id = l.id
                          WHERE m.keeper_id = shots.id
                            AND l.set_version_id IS NOT NULL
                          LIMIT 1)
 WHERE set_version_id IS NULL
   AND id IN (SELECT keeper_id FROM _shot_merge);

DELETE FROM shots WHERE id IN (SELECT loser_id FROM _shot_merge);

DROP TABLE _move_samples;
DROP TABLE _move_judgements;
DROP TABLE _move_notes;
DROP TABLE _shot_merge;
DROP TABLE _shot_keepers;

-- ── 3. de-duplicate the profile mirror by device id ──────────────────
--
-- Same problem one table over: `device_profiles` is keyed
-- `PRIMARY KEY (machine_id, device_id)`, so the same slot mirrored from two
-- machines is two rows that would collide the moment they share a machine id.
--
-- There is no `source` column to prefer here, so the rule is the one the table
-- already states about itself: a live mapping beats a tombstone, and the most
-- recently seen wins. Nothing references these rows, so the losers simply go.
CREATE TEMP TABLE _profile_keepers AS
SELECT device_id,
       (SELECT d2.rowid
          FROM device_profiles d2
         WHERE d2.device_id = d.device_id
         ORDER BY (d2.deleted_at IS NULL) DESC, d2.last_seen_at DESC, d2.rowid DESC
         LIMIT 1) AS keeper_rowid
  FROM device_profiles d
 GROUP BY device_id;

DELETE FROM device_profiles
 WHERE rowid NOT IN (SELECT keeper_rowid FROM _profile_keepers);

DROP TABLE _profile_keepers;

-- ── 4. one active Set ────────────────────────────────────────────────
--
-- Two machines could each hold an active Set, and "active" is about to stop
-- being per-machine. The survivor's own active Set is the one to keep — it is
-- what the machine in the kitchen is set up for — and the most recently created
-- one otherwise. Archiving nothing: the others simply stop being *the* one.
UPDATE sets
   SET active = 0
 WHERE active = 1
   AND id <> (SELECT id FROM sets
               WHERE active = 1
               ORDER BY (machine_id = (SELECT id FROM _survivor)) DESC,
                        created_at DESC, id DESC
               LIMIT 1);

-- ── 5. everything points at the survivor ─────────────────────────────
--
-- Safe now: the two unique keys that spanned `machine_id` have been collapsed
-- (shots above, the profile mirror above) and the partial index that enforced
-- one active Set per machine has been dropped.
UPDATE shots              SET machine_id = (SELECT id FROM _survivor);
UPDATE device_profiles    SET machine_id = (SELECT id FROM _survivor);
UPDATE sets               SET machine_id = (SELECT id FROM _survivor);
UPDATE cleanup_runs       SET machine_id = (SELECT id FROM _survivor);
UPDATE starting_point_runs SET machine_id = (SELECT id FROM _survivor);

DELETE FROM machines WHERE id NOT IN (SELECT id FROM _survivor);

DROP TABLE _survivor;

-- An insight's scope is a JSON document rather than columns, so it is the one
-- place a machine id can outlive the column it came from. `_decode` drops a key
-- it does not recognise, which is why a stale one changes no answer — but the
-- stored document would still *say* the insight was scoped to a machine, and an
-- insight is a claim somebody confirmed and may read back. Every other stated
-- key is left exactly as it was, so what still applies still matches.
UPDATE knowledge_insights
   SET scope_json = json_remove(scope_json, '$.machine_id')
 WHERE json_type(scope_json, '$.machine_id') IS NOT NULL;

-- ── 6. the three tables that can drop the column in place ────────────
--
-- `sets`, `cleanup_runs` and `starting_point_runs` carry `machine_id` as a
-- plain foreign key column: not in a primary key, not in a UNIQUE constraint,
-- and — after the DROP INDEXes at the top of this file — not indexed. SQLite's
-- ALTER TABLE DROP COLUMN handles that case, so these three cost a column
-- rewrite rather than a table rebuild.
ALTER TABLE sets DROP COLUMN machine_id;
ALTER TABLE cleanup_runs DROP COLUMN machine_id;
ALTER TABLE starting_point_runs DROP COLUMN machine_id;

-- One active Set, full stop, enforced by the schema rather than by whoever
-- remembers to clear the old flag. Partial, so the many inactive Sets do not
-- all collide on the same value.
CREATE UNIQUE INDEX idx_sets_one_active ON sets(active) WHERE active = 1;

-- `idx_sets_machine` was (machine_id, status) and had no purpose left without
-- the first column: "every active Set" is a scan of a table with a handful of
-- rows in it, and an index on `status` alone would be read once a page load to
-- save nothing measurable. It is not replaced.

-- The Device page's cleanup history, newest first — what the machine column
-- used to lead.
CREATE INDEX idx_cleanup_runs_started ON cleanup_runs(started_at DESC, id DESC);

-- ── 7. the profile mirror is re-keyed on the device id ───────────────
--
-- `machine_id` is half the primary key, so this one is a rebuild. Nothing
-- references `device_profiles` (0008 says why the draft audit deliberately does
-- not), so it is the plain create-copy-drop-rename with no dance around it.
CREATE TABLE device_profiles_new (
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

INSERT INTO device_profiles_new
    (device_id, current_version_id, favorite, selected, position,
     first_seen_at, last_seen_at, deleted_at)
SELECT device_id, current_version_id, favorite, selected, position,
       first_seen_at, last_seen_at, deleted_at
FROM device_profiles;

DROP TABLE device_profiles;
ALTER TABLE device_profiles_new RENAME TO device_profiles;

CREATE INDEX idx_device_profiles_version ON device_profiles(current_version_id);

-- ── 8. shots ─────────────────────────────────────────────────────────
--
-- The hard one. `machine_id` sits inside `UNIQUE (machine_id, device_id)`, and
-- the implicit index a table constraint creates cannot be dropped, so DROP
-- COLUMN refuses and the table has to be rebuilt.
--
-- **And four tables cascade off it.** 0014's dance — stash the referencing
-- columns in TEMP tables, NULL them, swap, write them back — does not apply
-- here, because these children hold `shot_id` as part of a NOT NULL primary key
-- and there is no NULL to set them to. Worse, the failure mode is the opposite
-- one: `DROP TABLE shots` performs an implicit DELETE, and an implicit DELETE
-- fires foreign key *actions*. `PRAGMA defer_foreign_keys` postpones the
-- checking of violations and does nothing at all about ON DELETE CASCADE, so a
-- naive rebuild would silently take every sample row, every notes card, every
-- verdict and every analysis in the archive with it. The archive's whole
-- purpose is that those survive the machine.
--
-- So the children are taken out of the way whole: copied into TEMP tables,
-- deleted, and written straight back once the new `shots` is in place under the
-- old name. The ids never change — only the table holding them does — so this
-- is a copy out and a copy back rather than a remap. It costs reading and
-- writing the sample rows twice on an upgrade, which is the price of not having
-- to trust a cascade to stop half-way.
--
-- The order of the deletes below is the order of the cascade, deepest first,
-- and `knowledge_insights.analysis_id` is stashed alongside because it is
-- ON DELETE SET NULL: deleting an analysis does not delete the insight, it
-- quietly unlinks it.
CREATE TEMP TABLE _shot_samples AS SELECT * FROM shot_samples;
CREATE TEMP TABLE _device_shot_notes AS SELECT * FROM device_shot_notes;
CREATE TEMP TABLE _shot_judgements AS SELECT * FROM shot_judgements;
CREATE TEMP TABLE _shot_analyses AS SELECT * FROM shot_analyses;
CREATE TEMP TABLE _suggestions AS SELECT * FROM suggestions;
CREATE TEMP TABLE _insight_analysis AS
SELECT id, analysis_id FROM knowledge_insights WHERE analysis_id IS NOT NULL;

DELETE FROM suggestions;
DELETE FROM shot_analyses;
DELETE FROM shot_judgements;
DELETE FROM device_shot_notes;
DELETE FROM shot_samples;

-- Column for column what 0002 declared, minus `machine_id`, with the unique key
-- now on the device id alone. `raw_slog` is still NOT NULL and still the source
-- of truth; every derived column is still rebuildable from it.
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

    index_rating            INTEGER,
    index_volume_g          REAL,
    index_avg_temp_c        REAL,
    index_max_pressure_bar  REAL,
    index_avg_flow_ml_s     REAL,
    index_flags             INTEGER,

    synced_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    -- The shot id the machine gave it, and now the whole of its identity. Step
    -- 2 above is what makes this hold on an archive that had two machines in it.
    UNIQUE (device_id)
) STRICT;

INSERT INTO shots_new
    (id, device_id, set_version_id, started_at, start_epoch, duration_ms,
     profile_version_id, profile_id_on_device, profile_name_on_device, final_weight_g,
     final_exit_reason, brew_delay_ms, slog_version, sample_interval_ms, fields_mask,
     sample_count, scale_connected, incomplete, source, deleted_on_device, quarantined,
     quarantine_reason, raw_slog, phases_json, diagnostics_json, execution_score,
     execution_reason, index_rating, index_volume_g, index_avg_temp_c,
     index_max_pressure_bar, index_avg_flow_ml_s, index_flags, synced_at, updated_at)
SELECT id, device_id, set_version_id, started_at, start_epoch, duration_ms,
       profile_version_id, profile_id_on_device, profile_name_on_device, final_weight_g,
       final_exit_reason, brew_delay_ms, slog_version, sample_interval_ms, fields_mask,
       sample_count, scale_connected, incomplete, source, deleted_on_device, quarantined,
       quarantine_reason, raw_slog, phases_json, diagnostics_json, execution_score,
       execution_reason, index_rating, index_volume_g, index_avg_temp_c,
       index_max_pressure_bar, index_avg_flow_ml_s, index_flags, synced_at, updated_at
FROM shots;

-- No rows are left in any child table, so the implicit DELETE inside this DROP
-- has nothing to cascade into and nothing to count as a violation.
DROP TABLE shots;
ALTER TABLE shots_new RENAME TO shots;

-- 0002's expression index, unchanged and for its original reason: the list
-- sorts on `COALESCE(started_at, '') DESC, id DESC` and SQLite cannot see
-- through a COALESCE to an index on the column inside it, so a plain index on
-- `started_at` would never be used and every page would sort the archive in a
-- temp b-tree. 0002 has the long version.
CREATE INDEX idx_shots_list            ON shots(COALESCE(started_at, '') DESC, id DESC);
CREATE INDEX idx_shots_profile_version ON shots(profile_version_id);
CREATE INDEX idx_shots_quarantined     ON shots(quarantined);
-- What `idx_shots_machine` was for, without the column that used to lead it: a
-- date-range filter over the archive. `idx_shots_list` cannot serve one — the
-- COALESCE again — which is why this is still worth its space.
CREATE INDEX idx_shots_started         ON shots(started_at DESC);
-- 0005's: the "shots in this Set" and "needs a Set" queries walk it.
CREATE INDEX idx_shots_set_version     ON shots(set_version_id);

-- The children, back where they were.
INSERT INTO shot_samples SELECT * FROM _shot_samples;
INSERT INTO device_shot_notes SELECT * FROM _device_shot_notes;
INSERT INTO shot_judgements SELECT * FROM _shot_judgements;
INSERT INTO shot_analyses SELECT * FROM _shot_analyses;
INSERT INTO suggestions SELECT * FROM _suggestions;

UPDATE knowledge_insights
   SET analysis_id = (SELECT a.analysis_id FROM _insight_analysis a
                       WHERE a.id = knowledge_insights.id)
 WHERE id IN (SELECT id FROM _insight_analysis);

DROP TABLE _shot_samples;
DROP TABLE _device_shot_notes;
DROP TABLE _shot_judgements;
DROP TABLE _shot_analyses;
DROP TABLE _suggestions;
DROP TABLE _insight_analysis;

-- ── 9. `machines` becomes a one-row table ────────────────────────────
--
-- Nothing references it any more — the five foreign keys that did are the five
-- columns this migration has just removed — so this is an unencumbered rebuild.
--
-- `CHECK (id = 1)` is what makes "the machine" a thing the code can ask for
-- without being told which. The id is fixed rather than merely single so that a
-- repository can upsert onto it (`INSERT ... ON CONFLICT(id) DO UPDATE`) and a
-- test can name it.
--
-- `host` loses its UNIQUE and its AUTOINCREMENT id goes with the old table: the
-- host is a setting now, not an identity, and connecting to a new address
-- updates this row rather than inserting a second one. Every other column is
-- 0002's, unchanged — the hardware string, the two firmware versions, the
-- capability flags, the temperature offset, the PID string, the brew delay, the
-- two verbatim device documents, and the name and notes a person owns.
CREATE TABLE machines_new (
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

-- Whatever id the survivor had, it is 1 now.
INSERT INTO machines_new
    (id, host, name, hardware_string, display_version, controller_version,
     has_pressure, has_dimming, has_gear_pump, has_led, temperature_offset_c, pid,
     brew_delay_ms, identity_json, settings_json, notes, first_seen_at, last_seen_at,
     created_at)
SELECT 1, host, name, hardware_string, display_version, controller_version,
       has_pressure, has_dimming, has_gear_pump, has_led, temperature_offset_c, pid,
       brew_delay_ms, identity_json, settings_json, notes, first_seen_at, last_seen_at,
       created_at
FROM machines;

DROP TABLE machines;
ALTER TABLE machines_new RENAME TO machines;

-- A fresh install and an import-only install both have "the machine" before any
-- pull. The empty host is the honest statement of "nothing is configured yet",
-- and the first identity refresh fills it in. Without this row the Hardware page
-- and the analyzer would both have to model the absence of a singleton, which is
-- the generality this migration exists to remove.
INSERT INTO machines (id, host)
SELECT 1, '' WHERE NOT EXISTS (SELECT 1 FROM machines);

-- An archive that only ever imported has the placeholder as its survivor, and
-- `import:default` was never an address anything could reach. The host is a
-- setting now, so the honest value is the empty one the page reads as "not
-- connected yet" — the same state a fresh install is in, which is what an
-- import-only install actually is.
UPDATE machines SET host = '' WHERE host LIKE 'import:%';

-- ── the views, back ──────────────────────────────────────────────────
--
-- `v_shots` and `v_sets` without `machine_id`; the other three verbatim. What
-- the SQL tool is allowed to read is exactly these names, so this is where the
-- chat learns the column is gone.
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
       st.name                                     AS set_name,
       sv.grind_setting,
       sv.dose_g                                   AS set_dose_g,
       sv.target_yield_g,
       sv.target_temperature_c,
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
       v.target_temperature_c,
       v.intent,
       v.origin,
       v.created_at,
       (SELECT COUNT(*) FROM shots s WHERE s.set_version_id = v.id) AS shot_count
  FROM set_versions v
  JOIN sets st ON st.id = v.set_id
  LEFT JOIN profile_versions pv ON pv.id = v.profile_version_id;

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
