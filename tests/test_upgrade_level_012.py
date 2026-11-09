"""Upgrade step 12 (level 11 to 12): a recorded outcome remembers how many shots it rests on.

The databases here are built from the frozen schema of level 11. The column is filled from the
newest accepted outcome proposal that still says what the version says, and is NULL for every
other row; a missing fill that should be there, or a fill that should not, fails one of these.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import upgrade_database_file
from gaggiclanker.db.upgrades import LATEST, STEPS, frozen_schema_sql, level_012
from tests.conftest import running_app
from tests.test_restore_apply import env_in, no_signal
from tests.test_restore_check import ROUTE, upload

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)

#: What this step must produce, now that a later step may move the schema file on.
LEVEL_TWELVE = schema.describe_sql(frozen_schema_sql(12))
NOTE = "Time held; the sourness did not move."

#: Version ids by what the case is, so a failure names it.
ACCEPTED_ONE = 1
NEWEST_OF_TWO = 2
NOTE_EDITED_SINCE = 3
OUTCOME_CHANGED_SINCE = 4
CHANGED_CARD_ONLY = 5
BY_HAND = 6
NO_OUTCOME = 7
OLDER_ONLY_MATCHES = 8
DISMISSED_ONLY = 9
RE_RECORDED_LATER = 10
JUST_INSIDE_THE_WINDOW = 11


def level_eleven(directory: Path) -> Path:
    path = directory / "gaggiclanker.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(frozen_schema_sql(11))
        conn.execute("INSERT INTO beans (name) VALUES ('Alturas')")
        conn.execute("INSERT INTO recipes (name, bean_id) VALUES ('Alturas', 1)")
        for number in range(1, 12):
            conn.execute(
                "INSERT INTO recipe_versions (recipe_id, prediction, version_major) "
                "VALUES (1, 'Expect 30 s.', ?)",
                (number,),
            )

        def outcome(
            version: int, value: str | None, note: str = NOTE, at: str = "2026-11-09T10:00:00.000Z"
        ) -> None:
            conn.execute(
                "UPDATE recipe_versions SET outcome = ?, outcome_note = ?, outcome_at = ? "
                "WHERE id = ?",
                (value, note if value else "", at if value else None, version),
            )

        def card(
            version: int,
            value: str,
            status: str,
            counted: int,
            note: str = NOTE,
            decided_at: str = "2026-11-09T10:00:00.200Z",
        ) -> None:
            conn.execute(
                "INSERT INTO recipe_outcome_proposals "
                "(recipe_id, recipe_version_id, outcome, note, counted_shots, status, decided_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?)",
                (version, value, note, counted, status, decided_at),
            )

        # A grade the person accepted: the card's count is the version's.
        outcome(ACCEPTED_ONE, "held")
        card(ACCEPTED_ONE, "held", "accepted", 3)
        # Accepted twice (a grade again): the newest one's count.
        outcome(NEWEST_OF_TWO, "failed")
        card(NEWEST_OF_TWO, "failed", "accepted", 1)
        card(NEWEST_OF_TWO, "failed", "accepted", 4)
        # Accepted, then the note was edited by hand: no card vouches for what it says now.
        outcome(NOTE_EDITED_SINCE, "held", "Edited since.")
        card(NOTE_EDITED_SINCE, "held", "accepted", 2)
        # Accepted, then another outcome was recorded by hand.
        outcome(OUTCOME_CHANGED_SINCE, "failed")
        card(OUTCOME_CHANGED_SINCE, "held", "accepted", 2)
        # The person recorded another outcome from the card: the card's count is not that grade's.
        outcome(CHANGED_CARD_ONLY, "failed")
        card(CHANGED_CARD_ONLY, "held", "changed", 2)
        # By hand, no card at all.
        outcome(BY_HAND, "partly_held")
        # A card exists, but the outcome was cleared.
        outcome(NO_OUTCOME, None)
        card(NO_OUTCOME, "held", "accepted", 2)
        # An older accepted card matches, a newer accepted one does not: newest-matching wins.
        outcome(OLDER_ONLY_MATCHES, "held")
        card(OLDER_ONLY_MATCHES, "held", "accepted", 2)
        card(OLDER_ONLY_MATCHES, "failed", "accepted", 5)
        # Dismissed and waiting cards are never a record.
        outcome(DISMISSED_ONLY, "held")
        card(DISMISSED_ONLY, "held", "dismissed", 2)
        # Accepted, then the same words were recorded again by hand an hour later: the count
        # belongs to the press that wrote the outcome, not to that re-record.
        outcome(RE_RECORDED_LATER, "held", at="2026-11-09T11:00:00.000Z")
        card(RE_RECORDED_LATER, "held", "accepted", 2)
        # The card was decided 4 s before the outcome was written: still the same press.
        outcome(JUST_INSIDE_THE_WINDOW, "held", at="2026-11-09T10:00:04.000Z")
        card(JUST_INSIDE_THE_WINDOW, "held", "accepted", 7)
    finally:
        conn.close()
    return path


def counts(path: Path) -> dict[int, int | None]:
    conn = sqlite3.connect(path)
    try:
        return dict(
            conn.execute("SELECT id, outcome_counted_shots FROM recipe_versions").fetchall()
        )
    finally:
        conn.close()


def test_the_step_is_the_twelfth_and_expects_the_frozen_level_11_schema() -> None:
    assert [s.level for s in STEPS] == list(range(1, LATEST + 1))
    assert LATEST >= 12
    built = schema.describe_sql(frozen_schema_sql(11))
    assert schema.fingerprint_of(built) == STEPS[11].expects == level_012.EXPECTS


def test_an_accepted_grade_gives_its_count_and_every_other_row_stays_empty(tmp_path: Path) -> None:
    path = level_eleven(tmp_path)
    assert (
        upgrade_database_file(path, tmp_path, steps=STEPS, expected=LEVEL_TWELVE, now=NOW)
        is not None
    )

    assert counts(path) == {
        ACCEPTED_ONE: 3,
        NEWEST_OF_TWO: 4,
        NOTE_EDITED_SINCE: None,
        OUTCOME_CHANGED_SINCE: None,
        CHANGED_CARD_ONLY: None,
        BY_HAND: None,
        NO_OUTCOME: None,
        OLDER_ONLY_MATCHES: 2,
        DISMISSED_ONLY: None,
        RE_RECORDED_LATER: None,
        JUST_INSIDE_THE_WINDOW: 7,
    }


def test_the_report_says_what_was_filled_and_nothing_else_moves(tmp_path: Path) -> None:
    path = level_eleven(tmp_path)
    before = sqlite3.connect(path)
    kept = before.execute(
        "SELECT id, recipe_id, prediction, outcome, outcome_note, outcome_at, created_at "
        "FROM recipe_versions ORDER BY id"
    ).fetchall()
    cards = before.execute("SELECT * FROM recipe_outcome_proposals ORDER BY id").fetchall()
    before.close()

    report = upgrade_database_file(path, tmp_path, steps=STEPS[:12], expected=LEVEL_TWELVE, now=NOW)

    assert report is not None and (report.from_level, report.to_level) == (11, 12)
    assert report.steps[0].counts == {"columns_added": 1, "counts_filled": 4}
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            conn.execute(
                "SELECT id, recipe_id, prediction, outcome, outcome_note, outcome_at, created_at "
                "FROM recipe_versions ORDER BY id"
            ).fetchall()
            == kept
        )
        assert (
            conn.execute("SELECT * FROM recipe_outcome_proposals ORDER BY id").fetchall() == cards
        )
    finally:
        conn.close()


def test_the_upgraded_database_is_the_one_a_new_database_is(tmp_path: Path) -> None:
    path = level_eleven(tmp_path)
    assert (
        upgrade_database_file(path, tmp_path, steps=STEPS, expected=LEVEL_TWELVE, now=NOW)
        is not None
    )
    conn = sqlite3.connect(path)
    try:
        assert schema.schema_differences(conn) == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    finally:
        conn.close()


async def test_a_backup_taken_at_level_11_restores_into_this_version(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    backup = level_eleven(tmp_path / "source")
    target = env_in(tmp_path / "target")
    async with running_app(target) as (app, client):
        no_signal(app)
        response = await upload(client, backup)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["upgrade_from_level"] == 11
        applied = await client.post(f"{ROUTE}/{data['token']}/apply")
        assert applied.status_code == 202, applied.text

    async with running_app(target) as (app, _client):
        assert await app.state.db.fetch_value("PRAGMA user_version") == LATEST
        filled = await app.state.db.fetch_value(
            "SELECT outcome_counted_shots FROM recipe_versions WHERE id = ?", (ACCEPTED_ONE,)
        )
        assert filled == 3
