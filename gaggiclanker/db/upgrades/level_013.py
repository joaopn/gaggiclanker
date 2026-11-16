"""Level 12 to level 13: a live Recipe always files its shots; archiving is the one off switch.

What changes in the database: ``recipes`` loses its ``automatch`` column, and the view
``v_recipes`` stops serving it. The column was the switch "file the shots brewed on this
Recipe's profile here". It was made for several live Recipes sharing a profile; since a profile
has at most one Recipe (``idx_recipes_profile``) there is nothing left to choose, and a live
Recipe now files every shot brewed on its profile while an archived one files none.

What is carried forward: every row, with every other column unchanged. The only thing lost is
the switch's off state on a live Recipe, which is what the change removes: such a Recipe starts
filing new shots. Shots already waiting for a Recipe are not touched (the step files nothing);
"Match by profile" files them when the person presses it. An archived Recipe had the switch off
already and stays archived. The person does nothing.

``ALTER TABLE ... DROP COLUMN`` refuses while a view names the column, so the view is dropped
first and recreated as the schema file defines it at this level.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import sqlite3

from gaggiclanker.db.upgrades.step import Step

__all__ = ["EXPECTS", "STEP"]

#: The fingerprint of ``schemas/level_012.sql``, the database this step upgrades.
EXPECTS = "760675498300"

#: ``v_recipes`` as ``schemas/level_013.sql`` defines it.
V_RECIPES = """
CREATE VIEW v_recipes AS
SELECT st.id AS recipe_id,
       st.name,
       st.archived,
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
       (SELECT COUNT(*) FROM recipe_versions v WHERE v.recipe_id = st.id) AS version_count,
       (SELECT COUNT(*) FROM shots s
          JOIN recipe_versions v ON v.id = s.recipe_version_id
         WHERE v.recipe_id = st.id) AS shot_count
  FROM recipes st
  LEFT JOIN recipe_versions cur ON cur.id = st.current_version_id
  LEFT JOIN beans b    ON b.id = st.bean_id
  LEFT JOIN grinders g ON g.id = st.grinder_id;
"""


def run(conn: sqlite3.Connection) -> dict[str, int]:
    now_filing = conn.execute(
        "SELECT COUNT(*) FROM recipes WHERE automatch = 0 AND archived = 0"
    ).fetchone()[0]
    conn.execute("DROP VIEW v_recipes")
    conn.execute("ALTER TABLE recipes DROP COLUMN automatch")
    conn.execute(V_RECIPES)
    return {"columns_dropped": 1, "live_recipes_now_filing": int(now_filing)}


STEP = Step(level=13, expects=EXPECTS, foreign_keys_off=False, run=run)
