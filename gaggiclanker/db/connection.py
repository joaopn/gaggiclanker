"""The SQLite connection: one file, one connection, the pragmas that matter.

One connection, not a pool. aiosqlite runs the driver on a dedicated thread and
serialises statements onto it, which is exactly right here: the workload is one
writer (the sync engine) and a handful of short reads, and SQLite's own
write lock makes concurrent writers wait anyway. A pool would buy contention,
not throughput.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

__all__ = ["Database"]

log = structlog.get_logger(__name__)

SqlParams = Sequence[Any] | Mapping[str, Any]

# WAL so a reader (the UI listing shots) never blocks the writer (the sync
# engine storing a shot) and vice versa. NORMAL rather than FULL because with
# WAL it only risks the last transactions on a power cut, not corruption, and
# a lost shot is re-fetchable from the machine.
#
# foreign_keys is OFF by default in SQLite and must be set per connection.
# The schema depends on ON DELETE CASCADE (deleting a shot must take its
# samples with it), so this is load-bearing, not hygiene.
_PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA synchronous = NORMAL",
    # 5 s: long enough to ride out a checkpoint or a backup VACUUM, short
    # enough that a genuine deadlock surfaces as an error, not a hang.
    "PRAGMA busy_timeout = 5000",
    # Negative = KiB of page cache rather than a page count: 64 MiB, which
    # holds the working set of a few thousand shots' index rows.
    "PRAGMA cache_size = -64000",
)


class Database:
    """An open SQLite database, with the small query surface repositories need."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        """The live connection, or an error if the lifespan has not opened it."""
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    async def connect(self) -> aiosqlite.Connection:
        """Open the file, create its directory, apply the pragmas."""
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        for pragma in _PRAGMAS:
            await conn.execute(pragma)
        self._conn = conn
        log.info("db_connected", path=str(self.path))
        return conn

    async def close(self) -> None:
        """Close the connection. Safe to call when already closed."""
        if self._conn is None:
            return
        # Fold the WAL back into the main file so a container that is stopped
        # right after this leaves one self-contained file behind, not a file
        # plus a -wal nobody copies when they back up by hand.
        try:
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # pragma: no cover - best effort on shutdown
            log.warning("db_checkpoint_failed", exc_info=True)
        await self._conn.close()
        self._conn = None
        log.info("db_closed", path=str(self.path))

    async def execute(self, sql: str, params: SqlParams = ()) -> aiosqlite.Cursor:
        """Run one statement and return its cursor (for ``rowcount``/``lastrowid``)."""
        return await self.connection.execute(sql, params)

    async def execute_many(self, sql: str, params: Iterable[SqlParams]) -> None:
        """Run one statement over many parameter sets (sample-row inserts)."""
        await self.connection.executemany(sql, params)

    async def execute_script(self, sql: str) -> None:
        """Run a multi-statement script (a migration file)."""
        await self.connection.executescript(sql)

    async def fetch_one(self, sql: str, params: SqlParams = ()) -> aiosqlite.Row | None:
        async with self.connection.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: SqlParams = ()) -> list[aiosqlite.Row]:
        async with self.connection.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    async def fetch_value(self, sql: str, params: SqlParams = ()) -> Any:
        """The first column of the first row, or ``None``."""
        row = await self.fetch_one(sql, params)
        return None if row is None else row[0]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """An explicit transaction. Commits on success, rolls back on any exception.

        The connection runs in autocommit mode (``isolation_level=None``) so
        that transactions are opened here, visibly, rather than by the driver
        guessing from statement types.
        """
        conn = self.connection
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            await conn.execute("ROLLBACK")
            raise
        else:
            await conn.execute("COMMIT")
