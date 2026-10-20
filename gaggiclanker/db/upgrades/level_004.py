"""Level 3 to level 4: a Set is a Recipe, in the database too.

What changes in the database:

* Eight tables take the new word: ``sets``, ``set_versions``, ``set_version_proposals``,
  ``set_outcome_proposals``, ``set_insight_deletions``, ``set_version_reverts``,
  ``set_version_pins`` and ``set_version_signature_overrides`` become ``recipes``,
  ``recipe_versions`` and so on.
* Eighteen columns that name a Set or a Set version (``set_id``, ``set_version_id``,
  ``accepted_set_id``, ``accepted_set_version_id``, ``copied_from_set_id``, ``sets_read``)
  become ``recipe_id`` and the like. Columns with neutral names that point at these tables keep
  their names.
* Twenty-two indexes are dropped and made again under their new names (two keep the name and
  cover a renamed column), and the four views that read the renamed tables are made again
  (``v_sets`` and ``v_set_versions`` are ``v_recipes`` and ``v_recipe_versions``; ``v_shots`` and
  ``v_judgements`` show the new column names). SQLite renames neither an index nor a view's
  output column, so each is dropped first and created again from the text below.
* Stored JSON that carries the old words in its keys is rewritten so that what the application
  reads back is what it writes now: an insight's ``rests_on_json``, a pattern proposal's
  ``sources_json`` and ``skipped_json``, a starting-point run's ``output_json``, a pattern run's
  ``dropped_json`` and a discarded draft's ``outcome_json`` action. A document that is not valid
  JSON is left exactly as it is and counted.
* The prompt ``chat-set`` is called ``chat-recipe``. In a prompt the person has edited, the two
  variables the prompts used (``set_insights``, ``similar_sets``) and the four tool names that
  carried the word (``get_set``, ``list_sets``, ``list_set_shots``, ``propose_set_version``) are
  replaced as whole words, and nothing else in the person's text changes.

What is carried forward: every row. A rename changes no value, and ``RENAME`` keeps the rows, the
ids and the AUTOINCREMENT counter (``sqlite_sequence`` follows the table's name).

Left as history, not rewritten: the inputs a pattern run and a starting-point run were given,
``sync_events.data_json``, ``shot_reviews.input_json``, the chat's stored tool calls and results
and events, ``tool_calls.tool`` and the language-model ledger. Nothing validates the old words
there.

The foreign keys other tables hold on the renamed tables are rewritten by SQLite, which writes
the new table name in double quotes; the schema comparison reads a quoted plain name as the bare
one (``schema.normalize_sql``), so no table is rebuilt and no foreign key is switched off. The
upgrade runner is told the new names of the tables (``Step.renamed_tables``) so that a reference
that already dangled in a renamed table is not mistaken for one this step broke.

This module is frozen: it imports nothing from the application. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

import structlog

from gaggiclanker.db.upgrades.step import Step

__all__ = ["STEP"]

log = structlog.get_logger(__name__)

#: The fingerprint of ``schemas/level_003.sql``, the database this step upgrades.
EXPECTS = "59554b1fb547"

#: Old table name -> new table name.
TABLES: Mapping[str, str] = {
    "sets": "recipes",
    "set_versions": "recipe_versions",
    "set_version_proposals": "recipe_version_proposals",
    "set_outcome_proposals": "recipe_outcome_proposals",
    "set_insight_deletions": "recipe_insight_deletions",
    "set_version_reverts": "recipe_version_reverts",
    "set_version_pins": "recipe_version_pins",
    "set_version_signature_overrides": "recipe_version_signature_overrides",
}

#: ``(table as named after the renames, old column, new column)``.
COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("profile_drafts", "set_id", "recipe_id"),
    ("chat_threads", "set_id", "recipe_id"),
    ("chat_threads", "set_version_id", "recipe_version_id"),
    ("starting_point_runs", "accepted_set_id", "accepted_recipe_id"),
    ("starting_point_runs", "accepted_set_version_id", "accepted_recipe_version_id"),
    ("recipe_version_proposals", "set_id", "recipe_id"),
    ("knowledge_insights", "set_id", "recipe_id"),
    ("knowledge_insights", "set_version_id", "recipe_version_id"),
    ("knowledge_insights", "copied_from_set_id", "copied_from_recipe_id"),
    ("shots", "set_version_id", "recipe_version_id"),
    ("recipe_outcome_proposals", "set_id", "recipe_id"),
    ("recipe_outcome_proposals", "set_version_id", "recipe_version_id"),
    ("recipe_insight_deletions", "set_id", "recipe_id"),
    ("pattern_runs", "sets_read", "recipes_read"),
    ("recipe_version_reverts", "set_id", "recipe_id"),
    ("recipe_versions", "set_id", "recipe_id"),
    ("recipe_version_pins", "set_version_id", "recipe_version_id"),
    ("recipe_version_signature_overrides", "set_version_id", "recipe_version_id"),
)

#: The views that read the renamed tables, dropped before anything is renamed.
OLD_VIEWS = ("v_judgements", "v_shots", "v_set_versions", "v_sets")

#: Every index whose name or column changes, as it was named at level 3.
OLD_INDEXES = (
    "idx_knowledge_insights_set",
    "idx_set_insight_deletions_set",
    "idx_set_insight_deletions_thread",
    "idx_set_insight_deletions_waiting",
    "idx_set_outcome_proposals_set",
    "idx_set_outcome_proposals_waiting",
    "idx_set_version_pins_board",
    "idx_set_version_pins_live_version",
    "idx_set_version_proposals_set",
    "idx_set_version_proposals_waiting",
    "idx_set_version_reverts_set",
    "idx_set_version_reverts_to",
    "idx_set_version_signature_overrides_live",
    "idx_set_versions_created",
    "idx_set_versions_name",
    "idx_set_versions_parent",
    "idx_set_versions_profile",
    "idx_sets_bean",
    "idx_sets_profile",
    "idx_shots_set_version",
    "idx_chat_threads_version",
    "idx_knowledge_insights_version",
)

NEW_INDEXES = (
    """
CREATE INDEX idx_recipes_bean ON recipes(bean_id);
""",
    """
CREATE UNIQUE INDEX idx_recipes_profile ON recipes(board_id) WHERE board_id IS NOT NULL;
""",
    """
CREATE INDEX idx_chat_threads_version ON chat_threads(recipe_version_id, updated_at DESC, id DESC);
""",
    """
CREATE UNIQUE INDEX idx_recipe_version_proposals_waiting
    ON recipe_version_proposals(recipe_id) WHERE status = 'proposed';
""",
    """
CREATE INDEX idx_recipe_version_proposals_recipe ON recipe_version_proposals(recipe_id, id DESC);
""",
    """
CREATE INDEX idx_knowledge_insights_recipe ON knowledge_insights(recipe_id, created_at, id);
""",
    """
CREATE INDEX idx_knowledge_insights_version ON knowledge_insights(recipe_version_id);
""",
    """
CREATE INDEX idx_shots_recipe_version ON shots(recipe_version_id);
""",
    """
CREATE UNIQUE INDEX idx_recipe_outcome_proposals_waiting
    ON recipe_outcome_proposals(recipe_version_id) WHERE status = 'proposed';
""",
    """
CREATE INDEX idx_recipe_outcome_proposals_recipe ON recipe_outcome_proposals(recipe_id, id DESC);
""",
    """
CREATE UNIQUE INDEX idx_recipe_insight_deletions_waiting
    ON recipe_insight_deletions(insight_id) WHERE status = 'proposed';
""",
    """
CREATE INDEX idx_recipe_insight_deletions_thread ON recipe_insight_deletions(thread_id, id);
""",
    """
CREATE INDEX idx_recipe_insight_deletions_recipe ON recipe_insight_deletions(recipe_id, id DESC);
""",
    """
CREATE INDEX idx_recipe_version_reverts_recipe ON recipe_version_reverts(recipe_id, created_at);
""",
    """
CREATE INDEX idx_recipe_version_reverts_to ON recipe_version_reverts(to_version_id);
""",
    """
CREATE INDEX idx_recipe_versions_parent ON recipe_versions(parent_version_id);
""",
    """
CREATE INDEX idx_recipe_versions_profile ON recipe_versions(profile_version_id);
""",
    """
CREATE INDEX idx_recipe_versions_created ON recipe_versions(recipe_id, created_at, id);
""",
    """
CREATE UNIQUE INDEX idx_recipe_versions_name
    ON recipe_versions(recipe_id, version_major, version_minor);
""",
    """
CREATE UNIQUE INDEX idx_recipe_version_pins_live_version
    ON recipe_version_pins(recipe_version_id) WHERE unpinned_at IS NULL;
""",
    """
CREATE INDEX idx_recipe_version_pins_board ON recipe_version_pins(board_id);
""",
    """
CREATE UNIQUE INDEX idx_recipe_version_signature_overrides_live
    ON recipe_version_signature_overrides(recipe_version_id)
    WHERE status IN ('proposed', 'confirmed');
""",
)

_V_RECIPES = """
CREATE VIEW v_recipes AS
SELECT st.id AS recipe_id,
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
       (SELECT COUNT(*) FROM recipe_versions v WHERE v.recipe_id = st.id) AS version_count,
       (SELECT COUNT(*) FROM shots s
          JOIN recipe_versions v ON v.id = s.recipe_version_id
         WHERE v.recipe_id = st.id) AS shot_count
  FROM recipes st
  LEFT JOIN recipe_versions cur ON cur.id = st.current_version_id
  LEFT JOIN beans b    ON b.id = st.bean_id
  LEFT JOIN grinders g ON g.id = st.grinder_id;
"""

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

_V_JUDGEMENTS = """
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
       s.recipe_version_id
  FROM shot_judgements j
  JOIN shots s ON s.id = j.shot_id;
"""

NEW_VIEWS = (_V_RECIPES, _V_RECIPE_VERSIONS, _V_SHOTS, _V_JUDGEMENTS)

#: ``(table, JSON column)`` -> the keys renamed wherever they occur in the document, and a
#: substring every document that needs the rewrite contains (the others are not touched).
JSON_KEYS: tuple[tuple[str, str, Mapping[str, str]], ...] = (
    ("knowledge_insights", "rests_on_json", {"set_version_id": "recipe_version_id"}),
    (
        "pattern_proposals",
        "sources_json",
        {"set_id": "recipe_id", "set_name": "recipe_name"},
    ),
    ("pattern_proposals", "skipped_json", {"set_id": "recipe_id"}),
    (
        "starting_point_runs",
        "output_json",
        {"similar_set_version_ids": "similar_recipe_version_ids"},
    ),
    ("pattern_runs", "dropped_json", {"one_set": "one_recipe"}),
)

#: The ``action`` a draft's outcome records where a Set's first recipe was overtaken.
OUTCOME_ACTIONS: Mapping[str, str] = {
    "set_discarded": "recipe_discarded",
    "set_written_otherwise": "recipe_written_otherwise",
}

#: Prompt text the person edited: these names, as whole words, change.
PROMPT_WORDS: Mapping[str, str] = {
    "set_insights": "recipe_insights",
    "similar_sets": "similar_recipes",
    "get_set": "get_recipe",
    "list_sets": "list_recipes",
    "list_set_shots": "list_recipe_shots",
    "propose_set_version": "propose_recipe_version",
}
_PROMPT_WORD = re.compile(
    r"(?<![A-Za-z0-9_])("
    + "|".join(sorted(PROMPT_WORDS, key=len, reverse=True))
    + r")(?![A-Za-z0-9_])"
)
PROMPT_RENAMES: Mapping[str, str] = {"chat-set": "chat-recipe"}


def _rename_keys(node: Any, names: Mapping[str, str]) -> tuple[Any, bool]:
    """The document with the given keys renamed wherever they occur, in the same order."""
    if isinstance(node, dict):
        changed = False
        out: dict[str, Any] = {}
        for key, value in node.items():
            new_value, inner = _rename_keys(value, names)
            new_key = names.get(key, key) if isinstance(key, str) else key
            changed = changed or inner or new_key != key
            out[new_key] = new_value
        return out, changed
    if isinstance(node, list):
        items = [_rename_keys(item, names) for item in node]
        return [value for value, _ in items], any(flag for _, flag in items)
    return node, False


def _rewrite_json(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    fix: Callable[[Any], tuple[Any, bool]],
    marker: str,
    counts: Counter[str],
) -> None:
    """Rewrite one JSON column's documents that carry ``marker``; leave the others as they are."""
    rows = conn.execute(
        f"SELECT rowid, {column} FROM {table} "  # noqa: S608 - constant names
        f"WHERE {column} IS NOT NULL AND (instr({column}, ?) > 0 OR NOT json_valid({column}))",
        (marker,),
    ).fetchall()
    for rowid, text in rows:
        try:
            document = json.loads(text)
        except ValueError:
            counts["malformed_json_left"] += 1
            log.warning("recipe_rename_json_left", table=table, column=column, rowid=rowid)
            continue
        fixed, changed = fix(document)
        if not changed:
            continue
        conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE rowid = ?",  # noqa: S608 - constant names
            (json.dumps(fixed, separators=(",", ":"), ensure_ascii=False), rowid),
        )
        counts[f"rewritten_{table}_{column}"] += 1


def _fix_outcome(document: Any) -> tuple[Any, bool]:
    if isinstance(document, dict) and document.get("action") in OUTCOME_ACTIONS:
        return {**document, "action": OUTCOME_ACTIONS[document["action"]]}, True
    return document, False


def _rewrite_prompts(conn: sqlite3.Connection, counts: Counter[str]) -> None:
    taken = {str(row[0]) for row in conn.execute("SELECT name FROM prompts")}
    for old, new in PROMPT_RENAMES.items():
        if old in taken and new not in taken:
            conn.execute("UPDATE prompts SET name = ? WHERE name = ?", (new, old))
            counts["prompts_renamed"] += 1
    for name, content in conn.execute(
        "SELECT name, content FROM prompts WHERE content != default_content"
    ).fetchall():
        changed = _PROMPT_WORD.sub(lambda match: PROMPT_WORDS[match.group(1)], content)
        if changed != content:
            conn.execute("UPDATE prompts SET content = ? WHERE name = ?", (changed, name))
            counts["prompts_text_updated"] += 1


def run(conn: sqlite3.Connection) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for view in OLD_VIEWS:
        conn.execute(f"DROP VIEW {view}")
    for index in OLD_INDEXES:
        conn.execute(f"DROP INDEX {index}")
    for old, new in TABLES.items():
        conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
        counts["tables_renamed"] += 1
    for table, old, new in COLUMNS:
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
        counts["columns_renamed"] += 1
    for sql in NEW_INDEXES:
        conn.execute(sql)
        counts["indexes_made"] += 1
    for sql in NEW_VIEWS:
        conn.execute(sql)
        counts["views_made"] += 1

    for table, column, names in JSON_KEYS:
        marker = next(iter(names))
        _rewrite_json(conn, table, column, partial(_rename_keys, names=names), marker, counts)
    _rewrite_json(conn, "profile_drafts", "outcome_json", _fix_outcome, "set_", counts)
    _rewrite_prompts(conn, counts)
    return dict(counts)


STEP = Step(
    level=4,
    expects=EXPECTS,
    foreign_keys_off=False,
    run=run,
    renamed_tables=TABLES,
)
