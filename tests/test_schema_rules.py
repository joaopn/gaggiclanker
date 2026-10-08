"""What the schema itself enforces and what its views answer.

These are the rules that live in ``schema.sql`` (a CHECK, a unique index, a cascade, a
view's text) rather than in a repository, so a database built from the file has to show
them. The structural comparison in ``test_schema.py`` says the file and a database agree;
this file says what the file means.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.schema import create_schema
from gaggiclanker.tools.sql import SqlRefused, run_query


@pytest.fixture
async def db(data_dir: Path) -> AsyncIterator[Database]:
    database = Database(data_dir / "test.db")
    await database.connect()
    await create_schema(database)
    try:
        yield database
    finally:
        await database.close()


_SHOT = "INSERT INTO shots (device_id, raw_slog, synced_at, updated_at) VALUES (?, x'00', 'x', 'x')"


# ── seed rows ───────────────────────────────────────────────────────────


async def test_a_new_database_has_the_one_machine_and_the_flavour_picks(db: Database) -> None:
    assert await db.fetch_value("SELECT COUNT(*) FROM machines") == 1
    assert await db.fetch_value("SELECT host FROM machines WHERE id = 1") == ""
    assert await db.fetch_value("SELECT COUNT(*) FROM flavor_picks WHERE kind = 'taste'") == 12
    assert await db.fetch_value("SELECT COUNT(*) FROM flavor_picks WHERE kind = 'aroma'") == 10
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute("INSERT INTO machines (id) VALUES (2)")


# ── the shot's own key ──────────────────────────────────────────────────


async def test_two_shots_may_share_a_number_and_nothing_else(db: Database) -> None:
    """The key is the number and the start time together."""
    insert = (
        "INSERT INTO shots (device_id, start_epoch, raw_slog, synced_at, updated_at) "
        "VALUES (?, ?, x'00', 'x', 'x')"
    )
    await db.execute(insert, ("000001", 1_760_000_000))
    await db.execute(insert, ("000001", 1_770_000_000))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        await db.execute(insert, ("000001", 1_770_000_000))
    assert await db.fetch_value("SELECT COUNT(*) FROM shots WHERE device_id = '000001'") == 2


# ── reviews and their claims ────────────────────────────────────────────


async def test_a_fresh_database_starts_its_reviews_at_one(db: Database) -> None:
    await db.execute(_SHOT, ("000001",))
    cursor = await db.execute("INSERT INTO shot_reviews (shot_id) VALUES (1)")
    assert cursor.lastrowid == 1


async def test_the_database_allows_one_running_review_per_shot(db: Database) -> None:
    await db.execute(_SHOT, ("000001",))
    await db.execute(_SHOT, ("000002",))
    await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (1, 'running')")
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (1, 'running')")
    # Any number of finished ones, and a running one on another shot.
    await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (1, 'ok'), (1, 'ok')")
    await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (2, 'running')")


async def test_deleting_a_shot_takes_its_reviews_and_claims_with_it(db: Database) -> None:
    await db.execute(_SHOT, ("000001",))
    await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (1, 'ok')")
    await db.execute(
        "INSERT INTO review_claims (review_id, position, kind, text) "
        "VALUES (1, 0, 'claim', 'a'), (1, 1, 'claim', 'b')"
    )
    with pytest.raises(sqlite3.IntegrityError):  # one claim per position
        await db.execute(
            "INSERT INTO review_claims (review_id, position, kind, text) "
            "VALUES (1, 1, 'claim', 'same position')"
        )
    with pytest.raises(sqlite3.IntegrityError):  # the fault vocabulary is closed
        await db.execute(
            "INSERT INTO review_claims (review_id, position, kind, text, fault) "
            "VALUES (1, 2, 'claim', 'x', 'sour')"
        )

    await db.execute("DELETE FROM shots WHERE id = 1")

    assert await db.fetch_value("SELECT COUNT(*) FROM shot_reviews") == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM review_claims") == 0


async def test_a_claim_is_kept_unless_rejected_and_the_views_count_what_is_kept(
    db: Database,
) -> None:
    await db.execute(_SHOT, ("000001",))
    await db.execute(
        "INSERT INTO shot_reviews (shot_id, status, summary, finished_at) "
        "VALUES (1, 'ok', 'The summary.', 'x')"
    )
    for position, status in enumerate([None, "confirmed", "rejected"]):
        if status is None:
            await db.execute(
                "INSERT INTO review_claims (review_id, position, kind, text) "
                "VALUES (1, ?, 'claim', 'c')",
                (position,),
            )
        else:
            await db.execute(
                "INSERT INTO review_claims (review_id, position, kind, text, status) "
                "VALUES (1, ?, 'claim', 'c', ?)",
                (position, status),
            )

    assert await db.fetch_value("SELECT status FROM review_claims WHERE position = 0") == (
        "confirmed"
    )
    served = await db.fetch_all("SELECT claim_id FROM v_review_claims ORDER BY 1")
    assert [r["claim_id"] for r in served] == [1, 2]
    counts = await db.fetch_all("SELECT kept_claims, rejected_claims FROM v_reviews")
    assert [tuple(r) for r in counts] == [(2, 1)]
    with pytest.raises(sqlite3.IntegrityError):  # `proposed` is not a state of a claim
        await db.execute(
            "INSERT INTO review_claims (review_id, position, kind, text, status) "
            "VALUES (1, 3, 'claim', 'x', 'proposed')"
        )
    columns = {str(r["name"]) for r in await db.fetch_all("PRAGMA table_info(v_reviews)")}
    assert "summary" not in columns


# ── insights and pattern runs ───────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "INSERT INTO set_insight_deletions (set_id, thread_id, insight_text, reason, status) "
        "VALUES (1, 1, 'x', 'too short', 'proposed')",
        "INSERT INTO set_insight_deletions (set_id, thread_id, insight_text, reason, status) "
        "VALUES (1, 1, 'x', 'a reason that is long enough to count', 'retired')",
        "UPDATE knowledge_insights SET replaced = 'maybe'",
    ],
)
async def test_the_insight_constraints_refuse_what_the_rules_forbid(db: Database, bad: str) -> None:
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute("INSERT INTO knowledge_insights (scope_json, text) VALUES ('{}', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "INSERT INTO pattern_runs (status) VALUES ('paused')",
        "INSERT INTO pattern_proposals (run_id, text, status) VALUES (1, 'x', 'approved-ish')",
        "INSERT INTO pattern_runs (status) VALUES ('running')",
    ],
)
async def test_the_pattern_constraints_refuse_what_the_rules_forbid(db: Database, bad: str) -> None:
    await db.execute("INSERT INTO pattern_runs (status) VALUES ('running')")
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(bad)


async def test_a_deleted_run_takes_its_proposals_and_leaves_the_insight_it_wrote(
    db: Database,
) -> None:
    await db.execute("INSERT INTO pattern_runs (id, status) VALUES (1, 'done')")
    await db.execute(
        "INSERT INTO knowledge_insights (id, scope_json, text, pattern_run_id) "
        "VALUES (1, '{}', 'General.', 1)"
    )
    await db.execute("INSERT INTO pattern_proposals (run_id, text, insight_id) VALUES (1, 'x', 1)")

    await db.execute("DELETE FROM pattern_runs WHERE id = 1")

    assert await db.fetch_value("SELECT COUNT(*) FROM pattern_proposals") == 0
    assert (
        await db.fetch_value("SELECT pattern_run_id FROM knowledge_insights WHERE id = 1") is None
    )


# ── what a Set owns ─────────────────────────────────────────────────────


async def test_deleting_a_set_empties_what_is_its_own(db: Database) -> None:
    await db.execute("INSERT INTO beans (name) VALUES ('Guji')")
    for set_id in (1, 2):
        await db.execute("INSERT INTO sets (id, name, bean_id) VALUES (?, 'S', 1)", (set_id,))
    for vid, set_id, minor in ((1, 1, 0), (2, 1, 1), (3, 2, 0), (4, 2, 1)):
        await db.execute(
            "INSERT INTO set_versions (id, set_id, version_major, version_minor) "
            "VALUES (?, ?, 1, ?)",
            (vid, set_id, minor),
        )
    await db.execute("INSERT INTO chat_threads (id, set_id, set_version_id) VALUES (1, 2, 3)")
    await db.execute("INSERT INTO chat_messages (thread_id, role, content) VALUES (1, 'user', 'x')")
    await db.execute(
        "INSERT INTO set_version_proposals (set_id, base_version_id) VALUES (2, 4), (1, 1)"
    )
    await db.execute(
        "INSERT INTO set_outcome_proposals (set_id, set_version_id, outcome, note) "
        "VALUES (2, 3, 'held', 'a note')"
    )
    await db.execute(
        "INSERT INTO set_version_reverts (set_id, from_version_id, to_version_id) VALUES (2, 4, 3)"
    )
    await db.execute("INSERT INTO knowledge_insights (text, set_id) VALUES ('learned', 2)")

    await db.execute("DELETE FROM sets WHERE id = 2")

    for table, left in (
        ("set_versions", 2),
        ("chat_threads", 0),
        ("chat_messages", 0),
        ("set_version_proposals", 1),
        ("set_outcome_proposals", 0),
        ("set_version_reverts", 0),
        ("knowledge_insights", 0),
    ):
        assert await db.fetch_value(f"SELECT COUNT(*) FROM {table}") == left, table  # noqa: S608
    assert await db.fetch_all("PRAGMA foreign_key_check") == []


async def test_a_set_version_name_is_unique_within_its_set(db: Database) -> None:
    await db.execute("INSERT INTO beans (name) VALUES ('Guji')")
    await db.execute("INSERT INTO sets (name, bean_id) VALUES ('S', 1), ('T', 1)")
    insert = "INSERT INTO set_versions (set_id, version_major, version_minor) VALUES (?, 1, 1)"
    await db.execute(insert, (1,))
    await db.execute(insert, (2,))
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(insert, (1,))


# ── the curated views ───────────────────────────────────────────────────


async def test_the_shot_views_ratio(db: Database) -> None:
    """Yield over dose: the judgement's dose in when entered and the version's otherwise;
    the judgement's dose out when entered and the scale's (else the index's) weight otherwise."""
    await db.execute("INSERT INTO beans (name) VALUES ('Guji')")
    await db.execute("INSERT INTO sets (name, bean_id) VALUES ('S', 1)")
    await db.execute(
        "INSERT INTO set_versions (set_id, version_major, dose_g, target_yield_g) "
        "VALUES (1, 1, 18.0, 36.0)"
    )
    for number, weight in ((1, 31.5), (2, 31.5), (3, None)):
        await db.execute(
            "INSERT INTO shots (device_id, raw_slog, set_version_id, final_weight_g, "
            "synced_at, updated_at) VALUES (?, x'00', 1, ?, 'x', 'x')",
            (f"{number:06d}", weight),
        )
    await db.execute(
        "INSERT INTO shot_judgements (shot_id, rating, dose_in_g, dose_out_g) "
        "VALUES (1, 1, 18.0, 42.2)"
    )

    rows = {
        r["shot_id"]: r["ratio"]
        for r in await db.fetch_all("SELECT shot_id, ratio FROM v_shots ORDER BY shot_id")
    }
    assert round(rows[1], 2) == 2.34  # the judgement's 42.2 g over its 18 g
    assert round(rows[2], 2) == round(31.5 / 18.0, 2)  # the scale's weight, the version's dose
    assert rows[3] is None  # no yield, no ratio


async def test_every_view_answers_on_a_populated_database(db: Database) -> None:
    await db.execute("INSERT INTO beans (name, roast_level) VALUES ('Guji', 'light')")
    await db.execute("INSERT INTO sets (name, bean_id) VALUES ('S', 1)")
    await db.execute(
        "INSERT INTO set_versions (set_id, version_major, intent) VALUES (1, 1, 'baseline')"
    )
    await db.execute("UPDATE sets SET current_version_id = 1")
    await db.execute(
        "INSERT INTO shots (device_id, raw_slog, set_version_id, synced_at, updated_at) "
        "VALUES ('000001', x'00', 1, 'x', 'x')"
    )
    await db.execute("INSERT INTO shot_judgements (shot_id, rating) VALUES (1, 5)")
    views = [
        str(r["name"])
        for r in await db.fetch_all("SELECT name FROM sqlite_master WHERE type='view'")
    ]
    assert len(views) == 10
    for view in views:
        assert await db.fetch_all(f"SELECT * FROM {view}") is not None, view  # noqa: S608


async def test_the_views_the_chat_reads_answer_through_the_sql_tool(db: Database) -> None:
    """`query_shots` reads these views; a view with stale text fails on a column that is gone."""
    await db.execute(
        "INSERT INTO beans (name, roast_level) VALUES ('Guji', 'light'), ('Decaf', NULL)"
    )
    await db.execute("INSERT INTO sets (name, bean_id) VALUES ('S', 1)")
    await db.execute(
        "INSERT INTO set_versions (set_id, version_major, origin, intent) "
        "VALUES (1, 1, 'starting_point', 'from the wizard')"
    )
    await db.execute("UPDATE sets SET current_version_id = 1")
    await db.execute(
        "INSERT INTO shots (device_id, raw_slog, set_version_id, synced_at, updated_at) "
        "VALUES ('000001', x'00', 1, 'x', 'x')"
    )
    await db.execute("INSERT INTO shot_judgements (shot_id, rating) VALUES (1, 5)")

    versions = await run_query(
        db.path, "SELECT origin, intent, version_label, shot_count FROM v_set_versions"
    )
    assert versions.rows == [["starting_point", "from the wizard", "v1", 1]]
    beans = await run_query(db.path, "SELECT name, roast_level FROM v_beans ORDER BY bean_id")
    assert beans.rows == [["Guji", "light"], ["Decaf", None]]
    sets = await run_query(db.path, "SELECT name, bean_name, shot_count FROM v_sets")
    assert sets.rows == [["S", "Guji", 1]]
    shots = await run_query(db.path, "SELECT device_id, set_name, rating FROM v_shots")
    assert shots.rows == [["000001", "S", 5]]
    judgements = await run_query(db.path, "SELECT shot_id, rating FROM v_judgements")
    assert judgements.rows == [[1, 5]]

    # Columns the schema no longer has are gone from the views, not merely unselected.
    for column, view in (
        ("roast_date", "v_beans"),
        ("altitude_m", "v_beans"),
        ("machine_id", "v_shots"),
        ("machine_id", "v_sets"),
        ("grind_setting", "v_judgements"),
        ("execution_score", "v_shots"),
        ("version_no", "v_set_versions"),
    ):
        with pytest.raises(SqlRefused, match="no such column"):
            await run_query(db.path, f"SELECT {column} FROM {view}")  # noqa: S608
