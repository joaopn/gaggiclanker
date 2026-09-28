-- A Set version is named major.minor: a dial-in change is a minor version, a
-- functional change to what the profile does is the next major.
--
-- Until now every new version was `version_no = MAX + 1` and was shown as
-- "v<version_no>", so a one-click grind nudge and a new pressure curve looked
-- alike in the log, the chat and every prediction's "compared to v4". From here
-- on a version carries a name of two numbers beside its ordinal:
--
--   * `version_no` keeps meaning "the Nth version of this Set". It stays
--     unique per Set and is what ordering, "the current version" and every
--     existing comparison read. Nothing about it changes.
--   * `version_major` and `version_minor` are the name a person and the agent
--     read: v1 (= 1.0, shown without ".0"), v1.1, v1.2, v2. They are assigned
--     by the repository in the same transaction as the insert, never typed.
--     A minor keeps the current version's major and takes the next minor in
--     it; a major takes the Set's highest major + 1 and minor 0.
--
-- Existing versions keep the numbers they already had: every row becomes
-- `version_no`.0, so "v3" still names the same version in every chat, every
-- prediction and every note written before the upgrade. The columns carry a
-- default only because SQLite cannot add a NOT NULL column without one; the
-- backfill below replaces it on every existing row and the repository names
-- both on every insert.
--
-- The unique index is what makes two tabs unable to mint the same name: the
-- repository computes the name inside the insert's transaction, and a second
-- writer with a stale idea of it would fail here rather than store a duplicate.
--
-- The agent may **suggest** that a change it proposes is a major one, with a
-- reason; the person decides on the card. The suggestion is stored on the
-- proposal and on a profile draft proposed in a Set's conversation, which is
-- where the card reads it.
--
-- Columns only, no table rebuild: several tables reference `set_versions` and
-- `sets`, and a rebuild would be a data-loss decision for nothing.
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one, and SQLite's DDL is transactional.

ALTER TABLE set_versions ADD COLUMN version_major INTEGER NOT NULL DEFAULT 0;

ALTER TABLE set_versions ADD COLUMN version_minor INTEGER NOT NULL DEFAULT 0;

UPDATE set_versions SET version_major = version_no, version_minor = 0;

CREATE UNIQUE INDEX idx_set_versions_name
    ON set_versions(set_id, version_major, version_minor);

ALTER TABLE set_version_proposals
    ADD COLUMN suggest_major INTEGER NOT NULL DEFAULT 0 CHECK (suggest_major IN (0, 1));

ALTER TABLE set_version_proposals ADD COLUMN major_reason TEXT NOT NULL DEFAULT '';

ALTER TABLE profile_drafts
    ADD COLUMN suggest_major INTEGER NOT NULL DEFAULT 0 CHECK (suggest_major IN (0, 1));

ALTER TABLE profile_drafts ADD COLUMN major_reason TEXT NOT NULL DEFAULT '';

-- ── the views, with the version's name ───────────────────────────────
--
-- The SQL tool reads only the curated views, and a model reading them reasons
-- in "v1.1", never in an ordinal. Both views that expose a version number get
-- the name beside it, as its two parts and as the label the app shows. Their
-- latest text is 0016's; everything else below is that text verbatim.
--
-- The label is 'v' || major, plus '.' || minor when the minor is above 0: the
-- same rule as `domain/sets.py::version_label`, repeated here because a view
-- cannot call Python, and pinned to it by a test.
DROP VIEW v_shots;

DROP VIEW v_set_versions;

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
