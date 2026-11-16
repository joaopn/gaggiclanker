"""Upgrade step 14 (level 13 to 14): the profiles of archived Recipes are switched off.

Archive now switches the Recipe's own profile off; this step does it for the Recipes archived
before. The databases here are built from the frozen schema of level 13 with one profile of each
kind: of a live Recipe, of archived Recipes (switched on, starred or not), already off, with a
deleted row, of a Recipe with no profile, and one no Recipe has. After the step only the live rows
of archived Recipes that were on are off, with a new ``updated_at``; every row is still there and
every other column is as it was.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import upgrade_database_file
from gaggiclanker.db.upgrades import LATEST, STEPS, frozen_schema_sql, level_014
from tests.conftest import running_app
from tests.test_restore_apply import env_in, no_signal
from tests.test_restore_check import ROUTE, upload

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)

LONG_AGO = "2026-11-01T10:00:00.000Z"

#: (name, recipe state, on_machine, starred, row deleted): the state of the profile's Recipe,
#: "live" or "archived", or None for a profile no Recipe has.
CASES = [
    ("live_on", "live", 1, 1, 0),
    ("archived_on", "archived", 1, 1, 0),
    ("archived_on_unstarred", "archived", 1, 0, 0),
    ("archived_off", "archived", 0, 1, 0),
    ("archived_deleted_row", "archived", 1, 1, 1),
    ("no_recipe", None, 1, 1, 0),
]
SWITCHED_OFF = {"archived_on", "archived_on_unstarred"}

#: Every column of a profile row but the two the step may change.
KEPT = (
    "SELECT id, label, current_version_id, device_profile_id, device_version_id, "
    "on_home_screen, origin, failed_version_id, pending_draft_id, deleted_at, created_at, "
    "previous_version_id, back_from_version_id, conflict_overruled_hash, device_label "
    "FROM profile_board ORDER BY id"
)
SWITCH = "SELECT label, on_machine, updated_at FROM profile_board ORDER BY id"


def level_thirteen(directory: Path) -> Path:
    path = directory / "gaggiclanker.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(frozen_schema_sql(13))
        conn.execute("INSERT INTO beans (name) VALUES ('Alturas')")
        for name, state, on, starred, deleted in CASES:
            conn.execute(
                "INSERT INTO profile_versions (content_hash, label, type, json) "
                "VALUES (?, ?, 'pro', '{}')",
                (f"hash-{name}", name),
            )
            version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO profile_board (label, current_version_id, origin, on_machine, "
                "on_home_screen, deleted_at, created_at, updated_at) "
                "VALUES (?, ?, 'adopted', ?, ?, ?, ?, ?)",
                (
                    name,
                    version_id,
                    on,
                    starred,
                    LONG_AGO if deleted else None,
                    LONG_AGO,
                    LONG_AGO,
                ),
            )
            board_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "UPDATE profile_versions SET board_id = ? WHERE id = ?", (board_id, version_id)
            )
            if state is not None:
                _recipe(conn, name, state != "live", board_id, version_id)
        # Recipes with no profile: a design, and an archived one that never named a profile.
        _recipe(conn, "design", False, None, None)
        _recipe(conn, "old_archived", True, None, None)
    finally:
        conn.close()
    return path


def _recipe(
    conn: sqlite3.Connection, name: str, archived: bool, board_id: int | None, version: int | None
) -> None:
    conn.execute(
        "INSERT INTO recipes (name, bean_id, archived, board_id, created_at) "
        "VALUES (?, 1, ?, ?, ?)",
        (name, int(archived), board_id, LONG_AGO),
    )
    recipe_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO recipe_versions (recipe_id, version_major, profile_version_id) "
        "VALUES (?, 1, ?)",
        (recipe_id, version),
    )
    version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE recipes SET current_version_id = ? WHERE id = ?", (version_id, recipe_id))


def _read(path: Path, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_the_step_is_the_fourteenth_and_expects_the_frozen_level_13_schema() -> None:
    assert [s.level for s in STEPS] == list(range(1, LATEST + 1))
    assert LATEST >= 14
    built = schema.describe_sql(frozen_schema_sql(13))
    assert schema.fingerprint_of(built) == STEPS[13].expects == level_014.EXPECTS


def test_every_case_is_there_before_the_step(tmp_path: Path) -> None:
    """The precondition of the test below: three rows that must stay on are on (a live Recipe's,
    a deleted row's, a profile with no Recipe), so a step without its guards would show."""
    rows = _read(level_thirteen(tmp_path), SWITCH)
    assert {name: on for name, on, _ in rows} == {name: on for name, _, on, _, _ in CASES}
    on_now = sum(1 for _, on, _ in rows if on == 1)
    assert on_now == 5, "five rows are on, two of them to be switched off"


def test_only_the_live_rows_of_archived_recipes_that_were_on_are_switched_off(
    tmp_path: Path,
) -> None:
    path = level_thirteen(tmp_path)
    kept = _read(path, KEPT)
    before = {label: (on, at) for label, on, at in _read(path, SWITCH)}

    report = upgrade_database_file(path, tmp_path, steps=STEPS, now=NOW)

    assert report is not None and (report.from_level, report.to_level) == (13, 14)
    assert report.steps[0].counts == {"archived_profiles_switched_off": 2}
    assert _read(path, KEPT) == kept, "every row, and every other column as it was"
    after = {label: (on, at) for label, on, at in _read(path, SWITCH)}
    assert set(after) == set(before)
    for name, *_ in CASES:
        if name in SWITCHED_OFF:
            assert after[name][0] == 0, name
            assert after[name][1] != before[name][1], "updated_at moves as the switch's write does"
        else:
            assert after[name] == before[name], name
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 7
        assert conn.execute("SELECT COUNT(*) FROM recipes WHERE archived = 1").fetchone()[0] == 5
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_the_upgraded_database_is_the_one_a_new_database_is(tmp_path: Path) -> None:
    path = level_thirteen(tmp_path)
    assert upgrade_database_file(path, tmp_path, steps=STEPS, now=NOW) is not None
    conn = sqlite3.connect(path)
    try:
        assert schema.schema_differences(conn) == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    finally:
        conn.close()


async def test_a_backup_taken_at_level_13_restores_into_this_version(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    backup = level_thirteen(tmp_path / "source")
    target = env_in(tmp_path / "target")
    async with running_app(target) as (app, client):
        no_signal(app)
        response = await upload(client, backup)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["upgrade_from_level"] == 13
        applied = await client.post(f"{ROUTE}/{data['token']}/apply")
        assert applied.status_code == 202, applied.text

    async with running_app(target) as (app, client):
        assert await app.state.db.fetch_value("PRAGMA user_version") == LATEST
        # The live rows (a database that never had its list built gets it built at the first
        # boot, which revives deleted rows: that is the list's own step, not this one's).
        rows = await app.state.db.fetch_all(
            "SELECT label, on_machine FROM profile_board WHERE deleted_at IS NULL"
        )
        kept_live = {name for name, *_, deleted in CASES if not deleted}
        assert {r["label"]: r["on_machine"] for r in rows if r["label"] in kept_live} == {
            name: 0 if name in SWITCHED_OFF or on == 0 else 1
            for name, _, on, _, deleted in CASES
            if not deleted
        }
