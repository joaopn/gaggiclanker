"""Level 4 to level 5: a Recipe version can state a drink, and notes on how it is made.

What changes in the database:

* ``recipe_versions`` gains two trailing columns: ``drink`` (``NULL``, or one of ``espresso``,
  ``americano``, ``cappuccino``, ``latte``, ``other``, by a ``CHECK``) and ``drink_notes``
  (``TEXT NOT NULL DEFAULT ''``). The table ends with a column, so ``ADD COLUMN`` leaves it as
  the schema file defines it.
* ``v_recipe_versions`` serves ``drink`` and ``drink_notes`` and ``v_shots`` serves
  ``recipe_drink`` (the drink of the version a shot is filed under). SQLite does not change a
  view's columns by itself, so each is dropped and made again from the text below.

What is carried forward: every row. The new columns start ``NULL`` and empty, which is true of
every existing version: no drink was ever stated, and "no drink stated" is not Espresso. Nothing
is filled in.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import sqlite3

from gaggiclanker.db.upgrades.step import Step

__all__ = ["STEP"]

#: The fingerprint of ``schemas/level_004.sql``, the database this step upgrades.
EXPECTS = "54a26cfd1ce5"

_V_RECIPE_VERSIONS = """
CREATE VIEW v_recipe_versions AS
SELECT v.id AS recipe_version_id,
       v.recipe_id,
       st.name AS recipe_name,
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
       COALESCE(pb.label, pv.label) AS profile_label,
       v.grind_setting,
       v.grind_value,
       v.dose_g,
       v.target_yield_g,
       v.drink,
       v.drink_notes,
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
       (SELECT COUNT(*) FROM shots s WHERE s.recipe_version_id = v.id) AS shot_count
  FROM recipe_versions v
  JOIN recipes st ON st.id = v.recipe_id
  LEFT JOIN recipe_versions par ON par.id = v.parent_version_id
  LEFT JOIN profile_versions pv ON pv.id = v.profile_version_id
  LEFT JOIN profile_board pb ON pb.id = pv.board_id
  LEFT JOIN recipe_versions cmp ON cmp.id = v.compares_to_version_id
  LEFT JOIN recipe_versions res ON res.id = v.restores_version_id;
"""

_V_SHOTS = """
CREATE VIEW v_shots AS
SELECT s.id                                        AS shot_id,
       s.device_id,
       s.started_at,
       s.duration_ms,
       s.duration_ms / 1000.0                      AS duration_s,
       s.profile_version_id,
       COALESCE(pb.label, pv.label)                AS profile_label,
       s.profile_name_on_device,
       COALESCE(s.final_weight_g, s.index_volume_g) AS volume_g,
       s.index_avg_temp_c                          AS avg_temp_c,
       s.index_max_pressure_bar                    AS max_pressure_bar,
       s.index_avg_flow_ml_s                       AS avg_flow_ml_s,
       s.sample_count,
       s.scale_connected,
       s.incomplete,
       s.quarantined,
       s.recipe_version_id,
       sv.recipe_id,
       sv.version_major                            AS recipe_version_major,
       sv.version_minor                            AS recipe_version_minor,
       'v' || sv.version_major
           || CASE WHEN sv.version_minor > 0 THEN '.' || sv.version_minor ELSE '' END
                                                   AS recipe_version_label,
       st.name                                     AS recipe_name,
       sv.grind_setting,
       sv.dose_g                                   AS recipe_dose_g,
       sv.target_yield_g,
       sv.drink                                    AS recipe_drink,
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
  LEFT JOIN profile_board pb    ON pb.id = pv.board_id
  LEFT JOIN recipe_versions sv     ON sv.id = s.recipe_version_id
  LEFT JOIN recipes st             ON st.id = sv.recipe_id
  LEFT JOIN beans b             ON b.id = st.bean_id
  LEFT JOIN grinders g          ON g.id = st.grinder_id
  LEFT JOIN shot_judgements j   ON j.shot_id = s.id;
"""


def run(conn: sqlite3.Connection) -> dict[str, int]:
    conn.execute("DROP VIEW v_shots")
    conn.execute("DROP VIEW v_recipe_versions")
    conn.execute(
        "ALTER TABLE recipe_versions ADD COLUMN drink TEXT "
        "CHECK (drink IS NULL OR drink IN "
        "('espresso', 'americano', 'cappuccino', 'latte', 'other'))"
    )
    conn.execute("ALTER TABLE recipe_versions ADD COLUMN drink_notes TEXT NOT NULL DEFAULT ''")
    conn.execute(_V_RECIPE_VERSIONS)
    conn.execute(_V_SHOTS)
    versions = conn.execute("SELECT COUNT(*) FROM recipe_versions").fetchone()[0]
    return {"columns_added": 2, "views_made": 2, "versions_carried": int(versions)}


STEP = Step(level=5, expects=EXPECTS, foreign_keys_off=False, run=run)
