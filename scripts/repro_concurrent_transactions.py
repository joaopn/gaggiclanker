#!/usr/bin/env python
"""Reproduce: two concurrent transactions on the shared connection fail.

    uv run python scripts/repro_concurrent_transactions.py

Exits non-zero while the bug exists, zero when it is fixed.

Nested transactions
-------------------

The whole app shares **one** SQLite connection (`db/connection.py`: one writer,
a handful of short reads, and aiosqlite serialises them onto its own thread).
What aiosqlite serialises is individual *statements*; it knows nothing about a
transaction spanning several of them.

So two coroutines that both reach ``BEGIN IMMEDIATE`` — two browser tabs adding
a Set version, or a tab racing a sync pass — give the second one
``cannot start a transaction within a transaction``, which surfaces as a 500 on
a request that did nothing wrong. The sync engine has a lock of its own,
which is why this only showed up once the API got its first transactional
write.

The fix is a lock inside ``Database`` so that the one connection has at most one
transaction at a time. Nesting stays unsupported: an inner ``COMMIT`` would
either publish the outer block's half-finished work or be a no-op whose rollback
silently loses data, so it is asserted rather than reference-counted.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from gaggiclanker.db.connection import Database


async def _insert(db: Database, value: int) -> None:
    """One transaction that reads, then writes what it read.

    Read-then-write inside the transaction on purpose: it is the shape
    `SetsRepository.add_version` has (find the current version, insert the next
    one), so this reproduces the collision *and* the reason the fix has to be a
    lock rather than a retry.
    """
    async with db.transaction():
        highest = await db.fetch_value("SELECT COALESCE(MAX(n), 0) FROM counter")
        await asyncio.sleep(0)  # hand control over, the way real I/O does
        await db.execute("INSERT INTO counter (n) VALUES (?)", (int(highest) + 1,))
        _ = value


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "repro.db")
        await db.connect()
        await db.execute("CREATE TABLE counter (n INTEGER PRIMARY KEY)")
        try:
            results = await asyncio.gather(_insert(db, 1), _insert(db, 2), return_exceptions=True)
        finally:
            await db.close()

    failures = [result for result in results if isinstance(result, BaseException)]
    if failures:
        print(f"FAIL: {len(failures)} of 2 concurrent transactions raised")
        for failure in failures:
            print(f"  {type(failure).__name__}: {failure}")
        return 1
    print("OK: both concurrent transactions committed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
