"""Upgrade step 5 (level 4 to 5): a Recipe version can state a drink and drink notes.

The databases here are built from the frozen schema of level 4, which is what the previous
version of the application wrote, and filled with Recipes, versions, shots and a pin. The step
adds two trailing columns and remakes two views; every version made before states no drink.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import UpgradeReport, upgrade_database_file
from gaggiclanker.db.upgrades import STEPS, frozen_schema_sql, level_005
from gaggiclanker.domain.drinks import DRINKS

LEVEL_FIVE = schema.describe_sql(frozen_schema_sql(5))

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)

#: Every table of the level-4 database, by row count, before and after.
FILLED = (
    "beans",
    "recipes",
    "recipe_versions",
    "shots",
    "recipe_version_proposals",
    "recipe_version_pins",
    "chat_threads",
)


class Level4:
    """A database as the previous version left it."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "gaggiclanker.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(frozen_schema_sql(4))
        self.ids: dict[str, int] = {}
        self._fill()

    def one(self, sql: str, *params: Any) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def _add(self, name: str, sql: str, *params: Any) -> int:
        self.ids[name] = int(self.conn.execute(sql, params).lastrowid or 0)
        return self.ids[name]

    def _fill(self) -> None:
        add = self._add
        add("bean", "INSERT INTO beans (name) VALUES ('Guji')")
        add(
            "pv",
            "INSERT INTO profile_versions (content_hash, label, type, json) "
            "VALUES ('h1', 'P', 'pro', '{\"temperature\": 93}')",
        )
        add(
            "board",
            "INSERT INTO profile_board (label, current_version_id, origin) "
            "VALUES ('P', ?, 'adopted')",
            self.ids["pv"],
        )
        for n in (1, 2):
            add(
                f"recipe{n}",
                "INSERT INTO recipes (name, bean_id) VALUES (?, ?)",
                f"R{n}",
                self.ids["bean"],
            )
        add(
            "v1",
            "INSERT INTO recipe_versions (recipe_id, version_major, version_minor, grind_setting, "
            "dose_g, target_yield_g, profile_version_id, intent) VALUES (?, 1, 0, '22', 18, 36, ?, "
            "'first')",
            self.ids["recipe1"],
            self.ids["pv"],
        )
        add(
            "v2",
            "INSERT INTO recipe_versions (recipe_id, version_major, version_minor, "
            "parent_version_id, grind_setting, dose_g, target_yield_g, intent) "
            "VALUES (?, 1, 1, ?, '21', 18, 37, 'finer')",
            self.ids["recipe1"],
            self.ids["v1"],
        )
        add(
            "w1",
            "INSERT INTO recipe_versions (recipe_id, version_major, version_minor) "
            "VALUES (?, 1, 0)",
            self.ids["recipe2"],
        )
        self.conn.execute(
            "UPDATE recipes SET current_version_id = ? WHERE id = ?",
            (self.ids["v2"], self.ids["recipe1"]),
        )
        self.conn.execute(
            "UPDATE recipes SET current_version_id = ? WHERE id = ?",
            (self.ids["w1"], self.ids["recipe2"]),
        )
        add(
            "shot",
            "INSERT INTO shots (device_id, raw_slog, recipe_version_id) VALUES ('1', x'00', ?)",
            self.ids["v2"],
        )
        add("loose", "INSERT INTO shots (device_id, raw_slog) VALUES ('2', x'00')")
        add(
            "proposal",
            "INSERT INTO recipe_version_proposals (recipe_id, base_version_id, patch_json) "
            "VALUES (?, ?, '{\"grind_value\": 12}')",
            self.ids["recipe1"],
            self.ids["v2"],
        )
        add(
            "pin",
            "INSERT INTO recipe_version_pins (recipe_version_id, board_id, profile_version_id) "
            "VALUES (?, ?, ?)",
            self.ids["v1"],
            self.ids["board"],
            self.ids["pv"],
        )
        add(
            "thread",
            "INSERT INTO chat_threads (recipe_id, recipe_version_id) VALUES (?, ?)",
            self.ids["recipe1"],
            self.ids["v2"],
        )
        self.conn.commit()

    def counts(self) -> dict[str, int]:
        return {t: self.one(f"SELECT COUNT(*) FROM {t}") for t in FILLED}  # noqa: S608

    def upgrade(self) -> UpgradeReport:
        self.conn.commit()
        self.conn.close()
        report = upgrade_database_file(
            self.path, self.path.parent, steps=STEPS[:5], expected=LEVEL_FIVE, now=NOW
        )
        assert report is not None
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        return report


@pytest.fixture
def level4(tmp_path: Path) -> Level4:
    return Level4(tmp_path)


def test_the_step_is_the_fifth_and_expects_the_frozen_level_4_schema() -> None:
    assert [s.level for s in STEPS][:5] == [1, 2, 3, 4, 5]
    assert STEPS[4].foreign_keys_off is False
    built = schema.describe_sql(frozen_schema_sql(4))
    assert schema.fingerprint_of(built) == STEPS[4].expects == level_005.EXPECTS


def test_every_version_made_before_states_no_drink_and_no_notes(level4: Level4) -> None:
    before = level4.counts()

    report = level4.upgrade()

    assert (report.from_level, report.to_level) == (4, 5)
    assert level4.counts() == before
    rows = level4.conn.execute("SELECT drink, drink_notes FROM recipe_versions").fetchall()
    assert len(rows) == 3
    assert {(row["drink"], row["drink_notes"]) for row in rows} == {(None, "")}


def test_the_result_is_the_schema_file_and_nothing_else_moved(level4: Level4) -> None:
    kept = [
        tuple(row)
        for row in level4.conn.execute(
            "SELECT id, recipe_id, version_major, version_minor, grind_setting, dose_g, "
            "target_yield_g, profile_version_id, intent, parent_version_id "
            "FROM recipe_versions ORDER BY id"
        )
    ]

    level4.upgrade()

    assert schema.schema_differences(level4.conn, LEVEL_FIVE) == []
    assert level4.one("PRAGMA user_version") == 5
    assert level4.one("PRAGMA integrity_check") == "ok"
    assert list(level4.conn.execute("PRAGMA foreign_key_check")) == []
    after = [
        tuple(row)
        for row in level4.conn.execute(
            "SELECT id, recipe_id, version_major, version_minor, grind_setting, dose_g, "
            "target_yield_g, profile_version_id, intent, parent_version_id "
            "FROM recipe_versions ORDER BY id"
        )
    ]
    assert after == kept


def test_the_log_line_counts_what_it_did(level4: Level4) -> None:
    report = level4.upgrade()

    assert [step.level for step in report.steps] == [5]
    assert report.steps[0].counts == {"columns_added": 2, "views_made": 2, "versions_carried": 3}


def test_the_views_serve_the_new_columns_and_the_check_holds_afterwards(level4: Level4) -> None:
    level4.upgrade()

    version = level4.conn.execute(
        "SELECT drink, drink_notes FROM v_recipe_versions WHERE recipe_version_id = ?",
        (level4.ids["v2"],),
    ).fetchone()
    shot = level4.conn.execute(
        "SELECT recipe_drink, recipe_version_label FROM v_shots WHERE shot_id = ?",
        (level4.ids["shot"],),
    ).fetchone()
    loose = level4.conn.execute(
        "SELECT recipe_drink FROM v_shots WHERE shot_id = ?", (level4.ids["loose"],)
    ).fetchone()

    assert tuple(version) == (None, "")
    assert (shot["recipe_drink"], shot["recipe_version_label"]) == (None, "v1.1")
    assert loose["recipe_drink"] is None
    for drink in DRINKS:
        level4.conn.execute(
            "UPDATE recipe_versions SET drink = ? WHERE id = ?", (drink, level4.ids["v1"])
        )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        level4.conn.execute("UPDATE recipe_versions SET drink = 'tea' WHERE id = 1")


def test_the_frozen_level_4_file_is_the_schema_the_previous_version_wrote(level4: Level4) -> None:
    assert level4.one("PRAGMA user_version") == 4
    columns = [row["name"] for row in level4.conn.execute("PRAGMA table_info(recipe_versions)")]
    assert "drink" not in columns and columns[-1] == "version_minor"
