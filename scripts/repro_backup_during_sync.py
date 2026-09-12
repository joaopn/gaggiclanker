#!/usr/bin/env python
"""Reproduce: `POST /api/backup` fails while the sync engine is writing.

    uv run python scripts/repro_backup_during_sync.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The whole app shares **one** SQLite connection (`db/connection.py`: one writer,
a handful of short reads, and aiosqlite serialises them onto its own thread).
The sync engine made that writer busy: it wraps every shot insert in
``BEGIN IMMEDIATE`` so a shot and its samples land together.

``VACUUM INTO`` cannot run inside a transaction. So a backup taken while a
backfill is in flight — which is exactly when somebody reaches for one, because
the archive is filling up — hits "cannot VACUUM from within a transaction" and
the route answers 500.

It is intermittent by nature: the window is one shot's insert, a few
milliseconds, and the suite only caught it about one run in four. This script
removes the timing by holding the transaction open itself.

The fix is to take the backup on its own connection. `VACUUM INTO` is a reader:
with WAL it does not block the writer and the writer does not block it, and a
multi-second vacuum has no business occupying the connection every request
shares.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from gaggiclanker.db.backup import create_backup
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = Database(root / "gaggiclanker.db")
        await db.connect()
        await run_migrations(db)

        failure: Exception | None = None
        result = None
        # Exactly what ShotsRepository.insert() is doing when a shot lands.
        async with db.transaction():
            await db.execute("INSERT INTO machines (host) VALUES (?)", ("repro.local",))
            try:
                result = await create_backup(db, root / "backups")
            except Exception as exc:
                failure = exc
        await db.close()

        if failure is not None:
            print(
                "FAIL: a backup during a write transaction raised "
                f"{type(failure).__name__}: {failure}"
            )
            return 1
        assert result is not None
        if not (result.path.is_file() and result.size_bytes > 0):
            print("FAIL: the backup call returned but wrote no file")
            return 1
        print(
            f"PASS: backed up to {result.filename} ({result.size_bytes} bytes) "
            "with a write transaction open"
        )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
