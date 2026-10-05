-- The execution score is gone, and so are the band labels it was built on.
--
-- `shots.execution_score` and `execution_reason` held a 1-10 score and its sentence,
-- computed once at ingest from the diagnostics. The score only asked whether the machine
-- followed its profile, so a shot could score a clean ten with its cup passed its target
-- before the profile's last phase ever began. What replaces it is facts per phase and a few
-- warnings that need no knowledge of the profile (worked out when a shot is read), so the
-- columns, the list's score sort and filter, and the view's two columns go.
--
-- `v_shots` names both columns, and SQLite refuses to drop a column a view names, so the view
-- is dropped, the columns dropped, and the view recreated with every other column as it was.
-- Its `ratio` now follows the one rule the shot page, the chat and the trends share: yield over
-- dose, the dose being the judgement's when typed and the version's otherwise.
-- No index names either column.
--
-- Stored diagnostics are not touched here: the derivation version moved with this change, so
-- the next boot derives every shot again from its bytes and no row is lost.
--
-- Two clean-ups that belong with it. The seeded knowledge rules that explained band labels
-- (and the three that described the retired channeling thresholds) have no label left to
-- explain; the seed no longer carries them, and an unedited copy in this archive is deleted
-- (a rule the person edited stays, as theirs). The same goes for the one rule whose key
-- named the retired word and which the seed now carries under another key. And the shot-information tier a person chose
-- for the resistance item that was renamed follows it, while the choices for items that no
-- longer exist are dropped.
--
-- No transaction control in this file: the runner wraps it plus its ledger row in one.

DROP VIEW v_shots;

ALTER TABLE shots DROP COLUMN execution_score;
ALTER TABLE shots DROP COLUMN execution_reason;

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

-- The pressure-matrix rule that had named erosion is now `decline_holds_flow`. A person's
-- edited copy is renamed, with their text, so the seed's new row does not sit beside it as
-- a second rule on the same thing; an untouched copy is deleted below and re-seeded.
UPDATE knowledge_rules SET key = 'decline_holds_flow'
 WHERE category = 'pressure_matrix' AND key = 'decline_compensates_erosion'
   AND value_json <> default_json
   AND NOT EXISTS (SELECT 1 FROM knowledge_rules
                    WHERE category = 'pressure_matrix' AND key = 'decline_holds_flow');

DELETE FROM knowledge_rules
 WHERE value_json = default_json
   AND (category = 'band_meanings'
        OR (category = 'telemetry_to_cause'
            AND key IN ('flow_jitter', 'flow_vs_target', 'two_indicators_rule'))
        OR (category = 'pressure_matrix' AND key = 'decline_compensates_erosion'));

UPDATE shot_info_tiers SET item_key = 'resistance_slope' WHERE item_key = 'resistance_erosion';

DELETE FROM shot_info_tiers
 WHERE item_key IN (
    'execution_score', 'score_confidence', 'score_reason', 'penalty_components',
    'temperature_overshoot', 'temperature_undershoot', 'temperature_stability',
    'pressure_auc', 'pressure_slope', 'flow_slope', 'weight_rate_variability',
    'resistance_stability', 'resistance_peak', 'saturation',
    'channeling_risk', 'primary_signal', 'flow_jitter', 'flow_vs_target',
    'pressure_drop_rate', 'late_flow_acceleration', 'pressure_jitter', 'flow_spread',
    'flow_shape', 'window_confidence', 'guidance', 'processing_note',
    'pressure_overshoot_max', 'flow_overshoot_max', 'flow_undershoot_max',
    'phase_channeling');
