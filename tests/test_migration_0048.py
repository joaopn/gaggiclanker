"""0048 turns reviews into readings: every stored review is deleted and nothing else is touched.

A database as the version before this one left it, with a populated archive and reviews of every
kind in it, is migrated; the new tables and views are there, the old reviews and the prompts and
tier choices that belonged to them are gone, the ids an old review held are never handed out
again, and no other table lost a row (the table is dropped and made again, and nothing
references it).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from tests.test_migrations import _migrate_below, _migrate_through

#: Every table that is not the reviews' own, with the count a populated archive has before.
OTHERS = (
    "shots",
    "shot_judgements",
    "sets",
    "set_versions",
    "knowledge_insights",
    "profile_drafts",
    "shot_info_tiers",
    "prompts",
)


async def _populate(db: Database) -> None:
    await db.execute(
        "INSERT INTO shots (device_id, raw_slog, synced_at, updated_at) VALUES "
        "('000001', x'00', 'x', 'x'), ('000002', x'00', 'x', 'x')"
    )
    await db.execute("INSERT INTO shot_judgements (shot_id, rating, notes) VALUES (1, 4, 'good')")
    await db.execute(
        "INSERT INTO shot_reviews (shot_id, status, taste_balance, taste_body, taste_confidence, "
        "description, summary) VALUES (1, 'ok', 'sour', 'thin', 'low', 'long text', 'short'), "
        "(1, 'failed', NULL, NULL, NULL, NULL, NULL), "
        "(2, 'ok', 'bitter', 'heavy', 'high', 'd', 's')"
    )
    # A later review that was deleted: its id is still never to be handed out again.
    await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (2, 'failed')")
    await db.execute("DELETE FROM shot_reviews WHERE id = 4")
    for key, tier in (("review_summary", "base"), ("review_model", "excluded"), ("rating", "base")):
        await db.execute("INSERT INTO shot_info_tiers (item_key, tier) VALUES (?, ?)", (key, tier))
    await db.execute(
        "INSERT INTO prompts (name, content, default_content) "
        "VALUES ('review', 'my edited review prompt', 'the shipped one')"
    )
    await db.execute(
        "INSERT INTO knowledge_insights (text, source, analysis_id, confirmed) "
        "VALUES ('an insight', 'analysis', 3, 1)"
    )


async def _counts(db: Database) -> dict[str, int]:
    return {
        table: int(await db.fetch_value(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
        for table in OTHERS
    }


async def test_every_review_is_deleted_and_nothing_else_is(data_dir: Path, tmp_path: Path) -> None:
    db = Database(data_dir / "old.db")
    await db.connect()
    try:
        assert (await _migrate_below(db, tmp_path, "0048"))[-1] == "0047"
        await _populate(db)
        before = await _counts(db)
        assert await db.fetch_value("SELECT COUNT(*) FROM shot_reviews") == 3

        assert await _migrate_through(db, tmp_path, "0048") == ["0048"]

        # No review, and no claim: nothing references the table, so nothing else went with it.
        assert await db.fetch_value("SELECT COUNT(*) FROM shot_reviews") == 0
        assert await db.fetch_value("SELECT COUNT(*) FROM review_claims") == 0
        after = await _counts(db)
        # The only other rows to go are the retired items' tiers and the review prompt.
        assert after["shot_info_tiers"] == before["shot_info_tiers"] - 2
        assert after["prompts"] == before["prompts"] - 1
        assert {k: v for k, v in after.items() if k not in {"shot_info_tiers", "prompts"}} == {
            k: v for k, v in before.items() if k not in {"shot_info_tiers", "prompts"}
        }
        tiers = {r["item_key"] for r in await db.fetch_all("SELECT item_key FROM shot_info_tiers")}
        assert tiers == {"rating"}
        assert await db.fetch_value("SELECT COUNT(*) FROM prompts WHERE name = 'review'") == 0
        assert await db.fetch_all("PRAGMA foreign_key_check") == []
    finally:
        await db.close()


async def test_a_new_review_never_takes_an_id_an_old_one_held(
    data_dir: Path, tmp_path: Path
) -> None:
    """Three tables name a review by a plain integer; the sequence is carried across."""
    db = Database(data_dir / "old.db")
    await db.connect()
    try:
        await _migrate_below(db, tmp_path, "0048")
        await _populate(db)
        await _migrate_through(db, tmp_path, "0048")

        cursor = await db.execute("INSERT INTO shot_reviews (shot_id) VALUES (1)")
        assert cursor.lastrowid == 5, "ids 1-4 were handed out before"
    finally:
        await db.close()


async def test_a_fresh_database_starts_its_reviews_at_one(data_dir: Path, tmp_path: Path) -> None:
    db = Database(data_dir / "fresh.db")
    await db.connect()
    try:
        await _migrate_below(db, tmp_path, "0048")
        await db.execute(
            "INSERT INTO shots (device_id, raw_slog, synced_at, updated_at) "
            "VALUES ('000001', x'00', 'x', 'x')"
        )
        await _migrate_through(db, tmp_path, "0048")

        cursor = await db.execute("INSERT INTO shot_reviews (shot_id) VALUES (1)")
        assert cursor.lastrowid == 1
    finally:
        await db.close()


async def test_the_new_shape(data_dir: Path, tmp_path: Path) -> None:
    db = Database(data_dir / "shape.db")
    await db.connect()
    try:
        await _migrate_below(db, tmp_path, "0048")
        await _migrate_through(db, tmp_path, "0048")

        columns = {r["name"] for r in await db.fetch_all("PRAGMA table_info(shot_reviews)")}
        assert {"summary", "prediction_given", "input_json", "rules_used_json"} <= columns
        assert not columns & {
            "taste_balance",
            "taste_body",
            "taste_confidence",
            "description",
        }
        claim_columns = {r["name"] for r in await db.fetch_all("PRAGMA table_info(review_claims)")}
        assert claim_columns == {
            "id",
            "review_id",
            "position",
            "kind",
            "window_json",
            "window_text",
            "phase",
            "start_s",
            "end_s",
            "fault",
            "text",
            "evidence_json",
            "supported",
            "expectation_id",
            "held",
            "stance",
            "status",
            "reason",
            "answered_at",
        }
        views = {
            r["name"]
            for r in await db.fetch_all("SELECT name FROM sqlite_master WHERE type='view'")
        }
        assert {"v_reviews", "v_review_claims"} <= views
        view_columns = {
            r["name"] for r in await db.fetch_all("PRAGMA table_info(v_review_claims)")
        } | {r["name"] for r in await db.fetch_all("PRAGMA table_info(v_reviews)")}
        assert "summary" not in view_columns and "input_json" not in view_columns
    finally:
        await db.close()


async def test_the_database_allows_one_running_review_per_shot(
    data_dir: Path, tmp_path: Path
) -> None:
    db = Database(data_dir / "index.db")
    await db.connect()
    try:
        await _migrate_below(db, tmp_path, "0048")
        await db.execute(
            "INSERT INTO shots (device_id, raw_slog, synced_at, updated_at) VALUES "
            "('000001', x'00', 'x', 'x'), ('000002', x'00', 'x', 'x')"
        )
        await _migrate_through(db, tmp_path, "0048")

        await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (1, 'running')")
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (1, 'running')")
        # Any number of finished ones, and a running one on another shot.
        await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (1, 'ok'), (1, 'ok')")
        await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (2, 'running')")
    finally:
        await db.close()


async def test_deleting_a_shot_or_a_review_takes_its_claims_with_it(
    data_dir: Path, tmp_path: Path
) -> None:
    db = Database(data_dir / "cascade.db")
    await db.connect()
    try:
        await _migrate_below(db, tmp_path, "0048")
        await db.execute(
            "INSERT INTO shots (device_id, raw_slog, synced_at, updated_at) "
            "VALUES ('000001', x'00', 'x', 'x')"
        )
        await _migrate_through(db, tmp_path, "0048")
        await db.execute("INSERT INTO shot_reviews (shot_id, status) VALUES (1, 'ok')")
        await db.execute(
            "INSERT INTO review_claims (review_id, position, kind, text) "
            "VALUES (1, 0, 'claim', 'a'), (1, 1, 'claim', 'b')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute(
                "INSERT INTO review_claims (review_id, position, kind, text) "
                "VALUES (1, 1, 'claim', 'same position')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute(
                "INSERT INTO review_claims (review_id, position, kind, text, fault) "
                "VALUES (1, 2, 'claim', 'x', 'sour')"
            )

        await db.execute("DELETE FROM shots WHERE id = 1")

        assert await db.fetch_value("SELECT COUNT(*) FROM shot_reviews") == 0
        assert await db.fetch_value("SELECT COUNT(*) FROM review_claims") == 0
    finally:
        await db.close()
