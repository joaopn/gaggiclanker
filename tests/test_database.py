"""The connection: the pragmas that matter, enforced foreign keys, and the transaction."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database


@pytest.fixture
async def db(data_dir: Path) -> AsyncIterator[Database]:
    database = Database(data_dir / "test.db")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


async def test_connection_pragmas(db: Database) -> None:
    """WAL and foreign keys are load-bearing, not hygiene: assert them."""
    assert str(await db.fetch_value("PRAGMA journal_mode")).lower() == "wal"
    assert await db.fetch_value("PRAGMA foreign_keys") == 1


async def test_foreign_keys_are_enforced(db: Database) -> None:
    await db.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    await db.execute(
        "CREATE TABLE child (id INTEGER PRIMARY KEY, "
        "parent_id INTEGER REFERENCES parent(id) ON DELETE CASCADE)"
    )
    with pytest.raises(Exception, match="FOREIGN KEY"):
        await db.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")


async def test_transaction_rolls_back(db: Database) -> None:
    await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError):
        async with db.transaction() as conn:
            await conn.execute("INSERT INTO t (id) VALUES (1)")
            raise RuntimeError("boom")
    assert await db.fetch_value("SELECT count(*) FROM t") == 0
