"""Upgrade step 4 (level 3 to 4): a Set is a Recipe, in the database too.

The databases here are built from the frozen schema of level 3 and filled with a row in every
table the step renames and in every column and document it rewrites. The step is held against
what the application then reads: an upgraded database must behave as the one the new code
would have written.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db import schema
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.patterns import PatternProposalsRepository, PatternRunsRepository
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.starting import StartingPointRunsRepository
from gaggiclanker.db.upgrade import UpgradeReport, upgrade_database_file
from gaggiclanker.db.upgrades import STEPS, frozen_schema_sql, level_004
from gaggiclanker.drafts.standing import classify
from gaggiclanker.llm.prompts import PromptService, seed_prompts
from gaggiclanker.starting.models import StartingPointOption

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)

#: Every table the step renames, old name to new.
TABLES = {
    "sets": "recipes",
    "set_versions": "recipe_versions",
    "set_version_proposals": "recipe_version_proposals",
    "set_outcome_proposals": "recipe_outcome_proposals",
    "set_insight_deletions": "recipe_insight_deletions",
    "set_version_reverts": "recipe_version_reverts",
    "set_version_pins": "recipe_version_pins",
    "set_version_signature_overrides": "recipe_version_signature_overrides",
}

#: Every column it renames, as (table at level 3, old name, new name).
COLUMNS = (
    ("profile_drafts", "set_id", "recipe_id"),
    ("chat_threads", "set_id", "recipe_id"),
    ("chat_threads", "set_version_id", "recipe_version_id"),
    ("starting_point_runs", "accepted_set_id", "accepted_recipe_id"),
    ("starting_point_runs", "accepted_set_version_id", "accepted_recipe_version_id"),
    ("set_version_proposals", "set_id", "recipe_id"),
    ("knowledge_insights", "set_id", "recipe_id"),
    ("knowledge_insights", "set_version_id", "recipe_version_id"),
    ("knowledge_insights", "copied_from_set_id", "copied_from_recipe_id"),
    ("shots", "set_version_id", "recipe_version_id"),
    ("set_outcome_proposals", "set_id", "recipe_id"),
    ("set_outcome_proposals", "set_version_id", "recipe_version_id"),
    ("set_insight_deletions", "set_id", "recipe_id"),
    ("pattern_runs", "sets_read", "recipes_read"),
    ("set_version_reverts", "set_id", "recipe_id"),
    ("set_versions", "set_id", "recipe_id"),
    ("set_version_pins", "set_version_id", "recipe_version_id"),
    ("set_version_signature_overrides", "set_version_id", "recipe_version_id"),
)

EDITED_PATTERNS_USER = """name: patterns-user
description: My own wording of the patterns turn.
variables:
  - name: set_insights
    description: What each Set learned.
  - name: general_insights
    description: What is known.
  - name: declined
    description: Declined proposals.
user: |
  MY HEADING FOR THE SETS

  {{set_insights}}

  KNOWN: {{general_insights}}

  DECLINED: {{declined}}
"""

EDITED_STARTING_USER = """name: starting_point-user
description: My own wording of the starting turn.
variables:
  - name: bean_facts
    description: The coffee.
  - name: similar_sets
    description: Past versions.
user: |
  Coffee: {{bean_facts}}

  Past ones, mine: {{similar_sets}}
"""

#: The person's own words: tool names to change, other words that must stay as they are.
EDITED_CHAT = (
    "name: chat-set\n"
    "description: Mine.\n"
    "system: |\n"
    "  Call get_set first, then list_set_shots, and list_sets only to compare.\n"
    "  Use propose_set_version when sure. Do not call get_set_proposals or reset_set.\n"
    "  A Set is the person's word; keep the Set talk, the set(), the {{app_version}}.\n"
    "  Mine too: my_list_sets, xget_set, get_set_x, list_sets2.\n"
    "  é café — unicode stays.\n"
)


class Level3:
    """A database as the previous version left it, filled row by row."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "gaggiclanker.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(frozen_schema_sql(3))
        self.ids: dict[str, int] = {}

    def add(self, name: str, sql: str, *params: Any) -> int:
        """Insert one row and remember its id under ``name``."""
        self.ids[name] = int(self.conn.execute(sql, params).lastrowid or 0)
        return self.ids[name]

    def one(self, sql: str, *params: Any) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def rows(self, sql: str, *params: Any) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def fill(self) -> None:
        """A row in every table and column the step touches."""
        add, conn = self.add, self.conn
        add("bean", "INSERT INTO beans (name) VALUES ('Guji')")
        for n in (1, 2, 3, 4):
            add(
                f"pv{n}",
                "INSERT INTO profile_versions (content_hash, label, type, json) "
                "VALUES (?, 'P', 'pro', '{}')",
                f"hash{n}",
            )
        add(
            "board",
            "INSERT INTO profile_board (label, current_version_id, origin) "
            "VALUES ('P', ?, 'adopted')",
            self.ids["pv1"],
        )
        add("set1", "INSERT INTO sets (name, bean_id) VALUES ('Morning', ?)", self.ids["bean"])
        add("set2", "INSERT INTO sets (name, bean_id) VALUES ('Evening', ?)", self.ids["bean"])
        add(
            "v1",
            "INSERT INTO set_versions (set_id, version_major, version_minor, profile_version_id) "
            "VALUES (?, 1, 0, ?)",
            self.ids["set1"],
            self.ids["pv1"],
        )
        add(
            "v2",
            "INSERT INTO set_versions (set_id, version_major, version_minor, parent_version_id, "
            "profile_version_id) VALUES (?, 1, 1, ?, ?)",
            self.ids["set1"],
            self.ids["v1"],
            self.ids["pv1"],
        )
        add(
            "w1",
            "INSERT INTO set_versions (set_id, version_major, version_minor) VALUES (?, 1, 0)",
            self.ids["set2"],
        )
        conn.execute(
            "UPDATE sets SET current_version_id = ?, copied_from_version_id = ? WHERE id = ?",
            (self.ids["v2"], self.ids["w1"], self.ids["set1"]),
        )
        conn.execute(
            "UPDATE sets SET current_version_id = ? WHERE id = ?",
            (self.ids["w1"], self.ids["set2"]),
        )
        add(
            "thread",
            "INSERT INTO chat_threads (set_id, set_version_id) VALUES (?, ?)",
            self.ids["set1"],
            self.ids["v2"],
        )
        add("thread2", "INSERT INTO chat_threads (title) VALUES ('general')")
        add(
            "proposal",
            "INSERT INTO set_version_proposals (set_id, base_version_id, patch_json) "
            "VALUES (?, ?, '{\"grind_value\": 12}')",
            self.ids["set1"],
            self.ids["v1"],
        )
        add(
            "outcome",
            "INSERT INTO set_outcome_proposals (set_id, set_version_id, outcome, note) "
            "VALUES (?, ?, 'held', '')",
            self.ids["set1"],
            self.ids["v1"],
        )
        add(
            "revert",
            "INSERT INTO set_version_reverts (set_id, from_version_id, to_version_id, note) "
            "VALUES (?, ?, ?, 'back')",
            self.ids["set1"],
            self.ids["v2"],
            self.ids["v1"],
        )
        add(
            "pin",
            "INSERT INTO set_version_pins (set_version_id, board_id, profile_version_id) "
            "VALUES (?, ?, ?)",
            self.ids["v1"],
            self.ids["board"],
            self.ids["pv2"],
        )
        add(
            "signature",
            "INSERT INTO profile_signatures (profile_version_id) VALUES (?)",
            self.ids["pv3"],
        )
        add(
            "expectation",
            "INSERT INTO signature_expectations (signature_id, position, tier, kind, sentence) "
            "VALUES (?, 0, 'important', 'reached', 'x')",
            self.ids["signature"],
        )
        add(
            "override",
            "INSERT INTO set_version_signature_overrides (set_version_id, expectation_id, "
            "compare_json) VALUES (?, ?, '{}')",
            self.ids["v1"],
            self.ids["expectation"],
        )
        # Insights: one resting on a version, one whose document is damaged, one general.
        rests = json.dumps([{"set_version_id": self.ids["v1"], "outcome": "held"}])
        add(
            "insight",
            "INSERT INTO knowledge_insights (text, set_id, set_version_id, thread_id, "
            "rests_on_json, copied_from_set_id, confirmed) VALUES ('Finer helps', ?, ?, ?, ?, ?, "
            "1)",
            self.ids["set1"],
            self.ids["v2"],
            self.ids["thread"],
            rests,
            self.ids["set2"],
        )
        add(
            "damaged",
            "INSERT INTO knowledge_insights (text, set_id, rests_on_json) VALUES ('Bad', ?, '[{')",
            self.ids["set1"],
        )
        add("general", "INSERT INTO knowledge_insights (text) VALUES ('Always rinse')")
        add(
            "deletion",
            "INSERT INTO set_insight_deletions (set_id, thread_id, insight_id, insight_text, "
            "reason) VALUES (?, ?, ?, 'Finer helps', 'This lesson is contradicted by later "
            "shots.')",
            self.ids["set1"],
            self.ids["thread"],
            self.ids["insight"],
        )
        add(
            "shot",
            "INSERT INTO shots (device_id, raw_slog, set_version_id) VALUES ('000001', x'00', ?)",
            self.ids["v2"],
        )
        # Documents the step rewrites, and the ones it leaves as history.
        option = {"similar_set_version_ids": [self.ids["v1"]], "profile_version_id": None}
        add(
            "run",
            "INSERT INTO starting_point_runs (bean_id, accepted_set_id, accepted_set_version_id, "
            "input_json, output_json) VALUES (?, ?, ?, ?, ?)",
            self.ids["bean"],
            self.ids["set1"],
            self.ids["v1"],
            json.dumps({"similar": [{"set_id": 1, "set_name": "Morning", "set_version_id": 1}]}),
            json.dumps({"options": [option, {"similar_set_version_ids": []}]}),
        )
        add(
            "patterns",
            "INSERT INTO pattern_runs (status, sets_read, dropped_json, input_json) "
            "VALUES ('done', 2, ?, ?)",
            json.dumps({"one_set": 2, "invented_source": 1}),
            json.dumps({"sets": [{"id": 1}], "copied_from_set": {}}),
        )
        add(
            "proposal_row",
            "INSERT INTO pattern_proposals (run_id, text, sources_json, skipped_json) "
            "VALUES (?, 'Rinse', ?, ?)",
            self.ids["patterns"],
            json.dumps(
                [{"insight_id": 1, "set_id": self.ids["set1"], "set_name": "Morning", "text": "a"}]
            ),
            json.dumps([{"insight_id": 2, "set_id": None, "text": "b", "reason": "gone"}]),
        )
        for name, action in (
            ("draft_discarded", "set_discarded"),
            ("draft_otherwise", "set_written_otherwise"),
            ("draft_declined", None),
            ("draft_went_back", "went_back"),
        ):
            add(
                name,
                "INSERT INTO profile_drafts (base_version_id, set_id, status, outcome_json) "
                "VALUES (?, ?, 'discarded', ?)",
                self.ids["pv1"],
                self.ids["set1"],
                None if action is None else json.dumps({"action": action}),
            )
        # Chat history and the ledger stay as stored.
        add(
            "message",
            "INSERT INTO chat_messages (thread_id, role, tool_calls_json, tool_results_json) "
            "VALUES (?, 'assistant', ?, ?)",
            self.ids["thread"],
            json.dumps([{"id": "1", "name": "get_set", "arguments": {"set_id": 1}}]),
            json.dumps([{"id": "1", "name": "get_set", "content": '{"set_id": 1}', "ok": True}]),
        )
        add(
            "event",
            "INSERT INTO sync_events (kind, data_json) VALUES ('shot_ingested', ?)",
            json.dumps({"set_version_id": 3}),
        )
        # Prompts: edited ones carry the person's words; an untouched one is the shipped text.
        for name, edited in (
            ("patterns-user", EDITED_PATTERNS_USER),
            ("starting_point-user", EDITED_STARTING_USER),
            ("chat-set", EDITED_CHAT),
        ):
            conn.execute(
                "INSERT INTO prompts (name, content, default_content) VALUES (?, ?, ?)",
                (name, edited, f"name: {name}\nuser: the shipped text\n"),
            )
        conn.execute(
            "INSERT INTO prompts (name, content, default_content) VALUES "
            "('chat-general', 'Call get_set.', 'Call get_set.')"
        )
        conn.commit()

    def upgrade(self) -> UpgradeReport:
        self.conn.commit()
        self.conn.close()
        report = upgrade_database_file(self.path, self.path.parent, steps=STEPS, now=NOW)
        assert report is not None
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        return report


@pytest.fixture
def level3(tmp_path: Path) -> Level3:
    db = Level3(tmp_path)
    db.fill()
    return db


def _counts(db: Level3) -> dict[str, int]:
    return {t: db.one(f"SELECT COUNT(*) FROM {t}") for t in TABLES}  # noqa: S608


# ── the registry and the frozen file ─────────────────────────────────────────────


def test_the_step_is_the_fourth_and_needs_no_foreign_keys_off_or_rebuild() -> None:
    assert [s.level for s in STEPS][:4] == [1, 2, 3, 4]
    assert STEPS[3].foreign_keys_off is False
    built = schema.describe_sql(frozen_schema_sql(3))
    assert schema.fingerprint_of(built) == STEPS[3].expects == level_004.EXPECTS
    assert dict(STEPS[3].renamed_tables) == TABLES


# ── what the step does ───────────────────────────────────────────────────────────


def test_the_result_is_the_schema_file_and_every_row_is_still_there(level3: Level3) -> None:
    before = _counts(level3)
    ids_before = {t: [r[0] for r in level3.rows(f"SELECT id FROM {t} ORDER BY id")] for t in TABLES}  # noqa: S608
    report = level3.upgrade()

    assert (report.from_level, report.to_level) == (3, 4)
    assert schema.schema_differences(level3.conn) == []
    assert level3.one("PRAGMA user_version") == 4
    for old, new in TABLES.items():
        assert level3.one(f"SELECT COUNT(*) FROM {new}") == before[old], new  # noqa: S608
        ids = [r[0] for r in level3.rows(f"SELECT id FROM {new} ORDER BY id")]  # noqa: S608
        assert ids == ids_before[old], new
        assert (
            level3.one("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", old) is None
        )
    assert level3.rows("PRAGMA foreign_key_check") == []
    assert level3.rows("PRAGMA integrity_check")[0][0] == "ok"


def test_every_renamed_column_is_there_under_its_new_name_with_its_values(level3: Level3) -> None:
    level3.upgrade()
    ids = level3.ids
    assert (
        level3.one("SELECT recipe_id FROM chat_threads WHERE id = ?", ids["thread"]) == ids["set1"]
    )
    assert (
        level3.one("SELECT recipe_version_id FROM chat_threads WHERE id = ?", ids["thread"])
        == ids["v2"]
    )
    assert (
        level3.one("SELECT recipe_id FROM profile_drafts WHERE id = ?", ids["draft_discarded"])
        == ids["set1"]
    )
    run = level3.rows("SELECT * FROM starting_point_runs")[0]
    assert (run["accepted_recipe_id"], run["accepted_recipe_version_id"]) == (
        ids["set1"],
        ids["v1"],
    )
    row = level3.rows("SELECT * FROM knowledge_insights WHERE id = ?", ids["insight"])[0]
    assert (row["recipe_id"], row["recipe_version_id"], row["copied_from_recipe_id"]) == (
        ids["set1"],
        ids["v2"],
        ids["set2"],
    )
    assert level3.one("SELECT recipe_version_id FROM shots") == ids["v2"]
    assert level3.one("SELECT recipes_read FROM pattern_runs") == 2
    assert (
        level3.one("SELECT recipe_id FROM recipe_versions WHERE id = ?", ids["v2"]) == ids["set1"]
    )
    assert level3.one("SELECT recipe_version_id FROM recipe_version_pins") == ids["v1"]
    assert (
        level3.one("SELECT recipe_version_id FROM recipe_version_signature_overrides") == ids["v1"]
    )
    assert level3.one("SELECT recipe_id FROM recipe_version_reverts") == ids["set1"]
    assert level3.one("SELECT recipe_id FROM recipe_insight_deletions") == ids["set1"]
    assert level3.one("SELECT recipe_id FROM recipe_version_proposals") == ids["set1"]
    for table, old, _new in COLUMNS:
        name = TABLES.get(table, table)
        columns = [r["name"] for r in level3.rows(f'PRAGMA table_info("{name}")')]
        assert old not in columns, (table, old)


def test_the_views_show_the_new_names_and_the_same_rows(level3: Level3) -> None:
    level3.upgrade()
    ids = level3.ids
    recipes = level3.rows("SELECT * FROM v_recipes ORDER BY recipe_id")
    assert [r["name"] for r in recipes] == ["Morning", "Evening"]
    versions = level3.rows("SELECT * FROM v_recipe_versions ORDER BY recipe_version_id")
    assert [(r["recipe_name"], r["version_label"]) for r in versions] == [
        ("Morning", "v1"),
        ("Morning", "v1.1"),
        ("Evening", "v1"),
    ]
    shot = level3.rows("SELECT * FROM v_shots")[0]
    assert shot["recipe_version_id"] == ids["v2"] and shot["recipe_id"] == ids["set1"]
    assert (shot["recipe_name"], shot["recipe_version_label"]) == ("Morning", "v1.1")
    assert "recipe_dose_g" in shot.keys()
    names = {r["name"] for r in level3.rows("SELECT name FROM sqlite_master")}
    assert not {"v_sets", "v_set_versions", "idx_sets_bean", "idx_shots_set_version"} & names
    assert {
        "v_recipes",
        "v_recipe_versions",
        "idx_recipes_bean",
        "idx_shots_recipe_version",
    } <= names


def test_all_twenty_two_indexes_are_made_again_under_the_new_names(level3: Level3) -> None:
    level3.upgrade()
    names = {r["name"] for r in level3.rows("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert not [n for n in names if "_set_" in n or n.startswith("idx_sets_") or n.endswith("_set")]
    assert len(level_004.NEW_INDEXES) == 22 == len(level_004.OLD_INDEXES)
    for sql in level_004.NEW_INDEXES:
        name = sql.split()[2] if sql.split()[1] == "INDEX" else sql.split()[3]
        assert name in names, name


def test_the_counter_follows_the_table_and_the_next_id_is_the_one_promised(level3: Level3) -> None:
    promised = level3.one("SELECT seq FROM sqlite_sequence WHERE name = 'set_versions'")
    level3.upgrade()
    assert level3.one("SELECT seq FROM sqlite_sequence WHERE name = 'recipe_versions'") == promised
    assert level3.one("SELECT 1 FROM sqlite_sequence WHERE name = 'set_versions'") is None
    new = level3.add(
        "next",
        "INSERT INTO recipe_versions (recipe_id, version_major, version_minor) VALUES (?, 2, 0)",
        level3.ids["set1"],
    )
    assert new == promised + 1


def test_the_step_returns_what_it_did_for_the_boot_log(level3: Level3) -> None:
    report = level3.upgrade()
    counts = report.steps[-1].counts
    assert report.steps[-1].level == 4
    assert counts["tables_renamed"] == 8
    assert counts["columns_renamed"] == 18
    assert counts["indexes_made"] == 22 and counts["views_made"] == 4
    assert counts["rewritten_knowledge_insights_rests_on_json"] == 1
    assert counts["rewritten_pattern_proposals_sources_json"] == 1
    assert counts["rewritten_pattern_proposals_skipped_json"] == 1
    assert counts["rewritten_starting_point_runs_output_json"] == 1
    assert counts["rewritten_pattern_runs_dropped_json"] == 1
    assert counts["rewritten_profile_drafts_outcome_json"] == 2
    assert counts["prompts_renamed"] == 1 and counts["prompts_text_updated"] == 3
    assert counts["malformed_json_left"] == 1


# ── stored documents ─────────────────────────────────────────────────────────────


def test_the_documents_carry_the_new_keys_and_nothing_else_changed(level3: Level3) -> None:
    level3.upgrade()
    ids = level3.ids
    rests = json.loads(
        level3.one("SELECT rests_on_json FROM knowledge_insights WHERE id = ?", ids["insight"])
    )
    assert rests == [{"recipe_version_id": ids["v1"], "outcome": "held"}]
    sources = json.loads(level3.one("SELECT sources_json FROM pattern_proposals"))
    assert sources == [
        {"insight_id": 1, "recipe_id": ids["set1"], "recipe_name": "Morning", "text": "a"}
    ]
    skipped = json.loads(level3.one("SELECT skipped_json FROM pattern_proposals"))
    assert skipped == [{"insight_id": 2, "recipe_id": None, "text": "b", "reason": "gone"}]
    output = json.loads(level3.one("SELECT output_json FROM starting_point_runs"))
    assert [o["similar_recipe_version_ids"] for o in output["options"]] == [[ids["v1"]], []]
    assert "similar_set_version_ids" not in json.dumps(output)
    assert json.loads(level3.one("SELECT dropped_json FROM pattern_runs")) == {
        "one_recipe": 2,
        "invented_source": 1,
    }
    actions = {
        r["id"]: (None if r["outcome_json"] is None else json.loads(r["outcome_json"])["action"])
        for r in level3.rows("SELECT id, outcome_json FROM profile_drafts")
    }
    assert actions == {
        ids["draft_discarded"]: "recipe_discarded",
        ids["draft_otherwise"]: "recipe_written_otherwise",
        ids["draft_declined"]: None,
        ids["draft_went_back"]: "went_back",
    }


def test_a_document_that_is_not_json_is_left_exactly_as_it_is_and_counted(level3: Level3) -> None:
    report = level3.upgrade()
    assert (
        level3.one(
            "SELECT rests_on_json FROM knowledge_insights WHERE id = ?", level3.ids["damaged"]
        )
        == "[{"
    )
    assert report.steps[-1].counts["malformed_json_left"] == 1


def test_documents_that_name_nothing_old_are_not_rewritten(level3: Level3) -> None:
    level3.conn.execute(
        "UPDATE knowledge_insights SET rests_on_json = '[ ]' WHERE id = ?", (level3.ids["general"],)
    )
    level3.upgrade()
    assert (
        level3.one(
            "SELECT rests_on_json FROM knowledge_insights WHERE id = ?", level3.ids["general"]
        )
        == "[ ]"
    )


def test_history_is_not_rewritten(level3: Level3) -> None:
    ids = level3.ids
    before = {
        "starting input": level3.one("SELECT input_json FROM starting_point_runs"),
        "patterns input": level3.one("SELECT input_json FROM pattern_runs"),
        "event": level3.one("SELECT data_json FROM sync_events"),
        "calls": level3.one(
            "SELECT tool_calls_json FROM chat_messages WHERE id = ?", ids["message"]
        ),
        "results": level3.one(
            "SELECT tool_results_json FROM chat_messages WHERE id = ?", ids["message"]
        ),
    }
    level3.upgrade()
    after = {
        "starting input": level3.one("SELECT input_json FROM starting_point_runs"),
        "patterns input": level3.one("SELECT input_json FROM pattern_runs"),
        "event": level3.one("SELECT data_json FROM sync_events"),
        "calls": level3.one(
            "SELECT tool_calls_json FROM chat_messages WHERE id = ?", ids["message"]
        ),
        "results": level3.one(
            "SELECT tool_results_json FROM chat_messages WHERE id = ?", ids["message"]
        ),
    }
    assert after == before


# ── prompts ──────────────────────────────────────────────────────────────────────


def test_the_set_prompt_is_renamed_and_an_edited_one_keeps_every_other_byte(level3: Level3) -> None:
    level3.upgrade()
    names = {r["name"] for r in level3.rows("SELECT name FROM prompts")}
    assert "chat-recipe" in names and "chat-set" not in names
    text = level3.one("SELECT content FROM prompts WHERE name = 'chat-recipe'")
    assert text == EDITED_CHAT.replace("get_set first", "get_recipe first").replace(
        "list_set_shots", "list_recipe_shots"
    ).replace("list_sets only", "list_recipes only").replace(
        "propose_set_version", "propose_recipe_version"
    )
    # Not renamed: other words that merely start like a tool name or are the person's own.
    assert "get_set_proposals or reset_set" in text and "A Set is the person's word" in text
    # A name inside a longer word is the person's own: neither side of the match may be a letter,
    # a digit or an underscore.
    assert "my_list_sets, xget_set, get_set_x, list_sets2." in text


def test_an_untouched_prompt_is_not_changed_and_the_default_is_left_for_the_seed(
    level3: Level3,
) -> None:
    level3.upgrade()
    assert level3.one("SELECT content FROM prompts WHERE name = 'chat-general'") == "Call get_set."
    old = level3.one("SELECT default_content FROM prompts WHERE name = 'patterns-user'")
    assert old == "name: patterns-user\nuser: the shipped text\n"


async def test_edited_prompts_render_after_the_upgrade_and_the_seed_keeps_them(
    level3: Level3,
) -> None:
    level3.upgrade()
    db = Database(level3.path)
    await db.connect()
    try:
        repo = PromptsRepository(db)
        service = PromptService(repo)
        patterns = await service.load(
            "patterns-user",
            {"recipe_insights": "THE RECIPES", "general_insights": "G", "declined": "D"},
        )
        assert "MY HEADING FOR THE SETS" in patterns.user and "THE RECIPES" in patterns.user
        starting = await service.load(
            "starting_point-user", {"bean_facts": "B", "similar_recipes": "THE SIMILAR"}
        )
        assert "Past ones, mine: THE SIMILAR" in starting.user
        await seed_prompts(repo)
        again = await service.load(
            "patterns-user",
            {"recipe_insights": "THE RECIPES", "general_insights": "G", "declined": "D"},
        )
        assert "MY HEADING FOR THE SETS" in again.user
        assert (await repo.get("chat-recipe")) is not None
        assert (await repo.get("chat-set")) is None
    finally:
        await db.close()


# ── what the application reads afterwards ────────────────────────────────────────


async def test_the_application_reads_every_rewritten_document_as_it_wrote_it(
    level3: Level3,
) -> None:
    level3.upgrade()
    ids = level3.ids
    db = Database(level3.path)
    await db.connect()
    try:
        insight = await InsightsRepository(db).get(ids["insight"])
        assert insight is not None
        assert [(r.label, r.outcome_then) for r in insight.rests_on] == [("v1", "held")]
        assert "rests on v1" in insight.render()

        proposal = await PatternProposalsRepository(db).get(ids["proposal_row"])
        assert proposal is not None
        assert [(s.recipe_id, s.recipe_name) for s in proposal.sources] == [
            (ids["set1"], "Morning")
        ]
        assert [s.insight_id for s in proposal.skipped] == [2]
        run = await PatternRunsRepository(db).get(ids["patterns"])
        assert run is not None and run.dropped == {"one_recipe": 2, "invented_source": 1}
        assert run.recipes_read == 2

        started = await StartingPointRunsRepository(db).get(ids["run"])
        assert started is not None and started.output is not None
        option = StartingPointOption.model_validate(started.output["options"][0] | _OPTION)
        assert option.similar_recipe_version_ids == [ids["v1"]]
        assert started.accepted_recipe_id == ids["set1"]

        drafts = ProfileDraftsRepository(db)
        for name, state in (
            ("draft_discarded", "replaced"),
            ("draft_otherwise", "replaced"),
            ("draft_declined", "declined"),
        ):
            draft = await drafts.get(ids[name])
            assert draft is not None
            standing = classify(
                draft, None, writes_enabled=True, paused=False, sync_failed_since=False
            )
            assert standing.state == state, name
    finally:
        await db.close()


#: The fields of an option a run's stored output carries besides the one the step renames.
_OPTION: dict[str, Any] = {
    "option": "recommended",
    "headline": "Classic 1:2",
    "grind_setting": "12",
    "dose_g": 18.0,
    "yield_g": 36.0,
    "ratio": 2.0,
    "temperature_c": 93.0,
    "rationale": "Because.",
}


# ── the runner ───────────────────────────────────────────────────────────────────


def test_a_reference_that_already_dangled_in_a_renamed_table_does_not_stop_the_upgrade(
    tmp_path: Path,
) -> None:
    """The runner counts dangling references by table; the step renames the tables."""
    db = Level3(tmp_path)
    db.fill()
    db.conn.execute("PRAGMA foreign_keys = OFF")
    db.conn.execute(
        "INSERT INTO set_version_pins (set_version_id, board_id, profile_version_id) "
        "VALUES (9999, ?, ?)",
        (db.ids["board"], db.ids["pv4"]),
    )
    db.conn.commit()
    assert db.rows("PRAGMA foreign_key_check")  # it dangles at level 3

    report = db.upgrade()

    assert report.to_level == 4
    assert [tuple(r)[0:3:2] for r in db.rows("PRAGMA foreign_key_check")] == [
        ("recipe_version_pins", "recipe_versions")
    ]


def test_the_step_alone_without_the_runners_map_would_have_refused(tmp_path: Path) -> None:
    """Leaving ``renamed_tables`` empty makes the same database read as broken by the step."""
    from dataclasses import replace

    from gaggiclanker.db.upgrade import UpgradeRefused

    db = Level3(tmp_path)
    db.fill()
    db.conn.execute("PRAGMA foreign_keys = OFF")
    db.conn.execute(
        "INSERT INTO set_version_pins (set_version_id, board_id, profile_version_id) "
        "VALUES (9999, ?, ?)",
        (db.ids["board"], db.ids["pv4"]),
    )
    db.conn.commit()
    db.conn.close()
    unmapped = (*STEPS[:3], replace(STEPS[3], renamed_tables={}))
    with pytest.raises(UpgradeRefused, match="refer to missing rows"):
        upgrade_database_file(db.path, db.path.parent, steps=unmapped, now=NOW)
    conn = sqlite3.connect(db.path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    conn.close()


def test_a_reference_the_step_itself_breaks_still_refuses(tmp_path: Path) -> None:
    """A synthetic step that leaves a row pointing at nothing is refused, whatever it renames."""
    from dataclasses import replace

    from gaggiclanker.db.upgrade import UpgradeRefused

    def breaks(conn: sqlite3.Connection) -> dict[str, int]:
        result = level_004.run(conn)
        # The step runs with foreign keys off (below), so the delete goes through and leaves
        # a dangling row: only the runner's own new-violation check can refuse it. With keys
        # on, even `defer_foreign_keys` makes SQLite refuse at commit, which would make this
        # test pass without that check.
        conn.execute("DELETE FROM recipe_versions WHERE id = 1")
        return result

    db = Level3(tmp_path)
    db.fill()
    db.conn.commit()
    db.conn.close()
    steps = (*STEPS[:3], replace(STEPS[3], run=breaks, foreign_keys_off=True))
    with pytest.raises(UpgradeRefused, match="refer to missing rows"):
        upgrade_database_file(db.path, db.path.parent, steps=steps, now=NOW)
    conn = sqlite3.connect(db.path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 2
    conn.close()
