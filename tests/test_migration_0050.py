"""0050 drops the judgement's grind setting and keeps everything else of a judgement.

Upgraded from the previous tip's schema with judgements that carried a grind: every other column
survives, `v_judgements` answers without the column, the tier a person chose for the retired
`grind_as_brewed` item is gone, and the seeding path no longer copies the machine's grind.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations


async def test_the_grind_goes_and_the_rest_of_a_judgement_stays(
    data_dir: Path, tmp_path: Path
) -> None:
    database = Database(data_dir / "old.db")
    await database.connect()
    try:
        directory = tmp_path / "below-0050"
        directory.mkdir()
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name < "0050":
                shutil.copy(path, directory / path.name)
        await run_migrations(database, directory)
        for number in (1, 2):
            await database.execute(
                "INSERT INTO shots (device_id, raw_slog, synced_at, updated_at) "
                "VALUES (?, x'00', 'x', 'x')",
                (f"00000{number}",),
            )
        await database.execute(
            "INSERT INTO shot_judgements (shot_id, rating, balance, dose_in_g, dose_out_g, "
            "grind_setting, notes, decision) VALUES "
            "(1, 4, 'sour', 18, 36, '22', 'sharp', 'improve'), "
            "(2, NULL, NULL, NULL, NULL, '3.5', '', NULL)"
        )
        await database.execute(
            "INSERT INTO shot_info_tiers (item_key, tier) VALUES "
            "('grind_as_brewed', 'excluded'), ('rating', 'base')"
        )

        assert "0050" in await run_migrations(database)

        columns = {
            str(r["name"]) for r in await database.fetch_all("PRAGMA table_info(shot_judgements)")
        }
        assert "grind_setting" not in columns
        kept = await database.fetch_all(
            "SELECT shot_id, rating, balance, dose_in_g, dose_out_g, notes, decision "
            "FROM shot_judgements ORDER BY shot_id"
        )
        assert [tuple(r) for r in kept] == [
            (1, 4, "sour", 18.0, 36.0, "sharp", "improve"),
            (2, None, None, None, None, "", None),
        ]
        view = await database.fetch_all("SELECT * FROM v_judgements ORDER BY shot_id")
        assert len(view) == 2 and "grind_setting" not in view[0].keys()
        tiers = await database.fetch_all("SELECT item_key FROM shot_info_tiers")
        assert [r["item_key"] for r in tiers] == ["rating"]
        assert await database.fetch_all("PRAGMA foreign_key_check") == []
    finally:
        await database.close()
