"""0047 moves a tier a person chose for the Warnings item to the Checks item, and nothing else."""

from __future__ import annotations

from pathlib import Path

from gaggiclanker.db.connection import Database
from tests.test_migrations import _migrate_below, _migrate_through


async def test_a_tier_chosen_for_warnings_follows_it_to_checks(
    data_dir: Path, tmp_path: Path
) -> None:
    db = Database(data_dir / "old.db")
    await db.connect()
    try:
        assert (await _migrate_below(db, tmp_path, "0047"))[-1] == "0046"
        for key, tier in (("warnings", "excluded"), ("rating", "base"), ("phase_ramp", "extended")):
            await db.execute(
                "INSERT INTO shot_info_tiers (item_key, tier) VALUES (?, ?)", (key, tier)
            )

        assert await _migrate_through(db, tmp_path, "0047") == ["0047"]

        tiers = {
            r["item_key"]: r["tier"] for r in await db.fetch_all("SELECT * FROM shot_info_tiers")
        }
        assert tiers == {"checks": "excluded", "rating": "base", "phase_ramp": "extended"}
    finally:
        await db.close()


async def test_an_archive_with_no_choice_about_it_is_left_as_it_was(
    data_dir: Path, tmp_path: Path
) -> None:
    db = Database(data_dir / "plain.db")
    await db.connect()
    try:
        await _migrate_below(db, tmp_path, "0047")
        assert await _migrate_through(db, tmp_path, "0047") == ["0047"]
        assert await db.fetch_value("SELECT COUNT(*) FROM shot_info_tiers") == 0
    finally:
        await db.close()
