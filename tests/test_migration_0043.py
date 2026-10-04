"""0043 adds three nullable cache columns to the ledger and touches no row."""

from __future__ import annotations

import shutil
from pathlib import Path

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations


async def test_old_ledger_rows_keep_null_cache_figures(data_dir: Path, tmp_path: Path) -> None:
    db = Database(data_dir / "old.db")
    await db.connect()
    try:
        below = tmp_path / "below"
        below.mkdir()
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name < "0043":
                shutil.copy(path, below / path.name)
        await run_migrations(db, below)
        await db.execute(
            "INSERT INTO llm_calls (call_id, purpose, provider, input_tokens, status) "
            "VALUES ('old', 'chat', 'anthropic', 900, 'succeeded')"
        )

        assert "0043" in await run_migrations(db)

        row = await db.fetch_one(
            "SELECT input_tokens, cache_read_tokens, cache_write_tokens, context_tokens "
            "FROM llm_calls WHERE call_id = 'old'"
        )
        assert row is not None
        assert tuple(row) == (900, None, None, None)
    finally:
        await db.close()
