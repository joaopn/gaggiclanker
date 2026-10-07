"""0049 keeps a review's claims unless a person rejects them, and loses nothing a person said.

Upgraded from the previous tip's schema with a review holding a proposed, a confirmed and a
rejected claim: the proposed one becomes confirmed, the others keep their answer and their id, the
tiers a person chose for the review's shot-information items follow the renamed keys, the two
views answer, and a claim can no longer be written `proposed`.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations


async def _below_0049(database: Database, tmp_path: Path) -> None:
    directory = tmp_path / "below-0049"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "0049":
            shutil.copy(path, directory / path.name)
    await run_migrations(database, directory)


async def test_a_proposed_claim_is_kept_and_a_rejected_one_stays_rejected(
    data_dir: Path, tmp_path: Path
) -> None:
    database = Database(data_dir / "old.db")
    await database.connect()
    try:
        await _below_0049(database, tmp_path)
        await database.execute(
            "INSERT INTO shots (device_id, raw_slog, synced_at, updated_at) "
            "VALUES ('000001', x'00', 'x', 'x')"
        )
        await database.execute(
            "INSERT INTO shot_reviews (shot_id, status, summary, finished_at) "
            "VALUES (1, 'ok', 'The summary.', 'x')"
        )
        for position, (status, answered) in enumerate(
            [("proposed", None), ("confirmed", "a"), ("rejected", "b")]
        ):
            await database.execute(
                "INSERT INTO review_claims (review_id, position, kind, text, status, reason, "
                "answered_at) VALUES (1, ?, 'claim', ?, ?, 'a reason', ?)",
                (position, f"claim {position}", status, answered),
            )
        await database.execute(
            "INSERT INTO shot_info_tiers (item_key, tier) VALUES "
            "('reading_claims', 'extended'), ('reading_state', 'excluded')"
        )

        assert "0049" in await run_migrations(database)

        rows = await database.fetch_all(
            "SELECT id, position, status, answered_at, text FROM review_claims ORDER BY id"
        )
        assert [tuple(row) for row in rows] == [
            (1, 0, "confirmed", None, "claim 0"),
            (2, 1, "confirmed", "a", "claim 1"),
            (3, 2, "rejected", "b", "claim 2"),
        ]
        columns = {
            str(r["name"]) for r in await database.fetch_all("PRAGMA table_info(review_claims)")
        }
        assert "reason" not in columns
        tiers = await database.fetch_all("SELECT item_key, tier FROM shot_info_tiers ORDER BY 1")
        assert [tuple(r) for r in tiers] == [
            ("review_claims", "extended"),
            ("review_state", "excluded"),
        ]
        # The views answer: the claims a person did not reject, and the counts.
        served = await database.fetch_all("SELECT claim_id FROM v_review_claims ORDER BY 1")
        assert [r["claim_id"] for r in served] == [1, 2]
        counts = await database.fetch_all("SELECT kept_claims, rejected_claims FROM v_reviews")
        assert [tuple(r) for r in counts] == [(2, 1)]
        assert await database.fetch_all("PRAGMA foreign_key_check") == []

        # New claims are kept by default and `proposed` is no longer a state.
        await database.execute(
            "INSERT INTO review_claims (review_id, position, kind, text) "
            "VALUES (1, 3, 'claim', 'new')"
        )
        assert (
            await database.fetch_value("SELECT status FROM review_claims WHERE position = 3")
            == "confirmed"
        )
        with pytest.raises(sqlite3.IntegrityError):
            await database.execute(
                "INSERT INTO review_claims (review_id, position, kind, text, status) "
                "VALUES (1, 4, 'claim', 'x', 'proposed')"
            )
        # The id sequence carried over: the next claim never takes a retired id.
        await database.execute(
            "INSERT INTO review_claims (review_id, position, kind, text) "
            "VALUES (1, 5, 'claim', 'later')"
        )
        assert await database.fetch_value("SELECT MAX(id) FROM review_claims") == 5
    finally:
        await database.close()
