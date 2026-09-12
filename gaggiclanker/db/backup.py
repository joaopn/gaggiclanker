"""Database backup via ``VACUUM INTO``.

The whole install is one SQLite file — shots, raw ``.slog`` bytes, profiles,
judgements, analyses — so a backup is a file copy. ``VACUUM INTO`` is the
correct way to take one while the app is running: it produces a consistent,
defragmented copy from a read transaction without stopping writers, which
``cp`` cannot promise with WAL in play.

**It runs on its own connection**, not the app's. ``VACUUM`` cannot run inside a
transaction, and once sync is running the app's single shared connection spends its time
inside one: the sync engine wraps each shot and its samples in
``BEGIN IMMEDIATE`` so they land together. Sharing the connection meant a backup
taken while a backfill was in flight — exactly when somebody reaches for one —
answered 500 with "cannot VACUUM from within a transaction"
(``scripts/repro_backup_during_sync.py``). A second connection is also the right
shape on its own terms: ``VACUUM INTO`` is a reader, WAL means it neither blocks
the writer nor is blocked by it, and a multi-second vacuum has no business
occupying the connection every request shares.

Restoring is deliberately not an endpoint: it means stopping the container and
copying the file back over ``DATA_DIR/gaggiclanker.db``. A running server
cannot swap the file out from under its own open connection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.infra.errors import InternalError

__all__ = ["BackupResult", "create_backup", "list_backups"]

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Where the backup landed and how big it is."""

    filename: str
    path: Path
    size_bytes: int
    created_at: datetime


def _timestamp(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def _pick_target(backups_dir: Path, timestamp: str) -> Path:
    """Create the directory and return the first free filename for this second."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    stem = f"gaggiclanker-{timestamp}"
    target = backups_dir / f"{stem}.db"
    attempt = 1
    while target.exists():
        attempt += 1
        target = backups_dir / f"{stem}-{attempt}.db"
        if attempt > 100:  # pragma: no cover - a pathological clock or a full disk
            raise InternalError("Could not find a free backup filename")
    return target


def _scan(backups_dir: Path) -> list[BackupResult]:
    if not backups_dir.is_dir():
        return []
    found: list[BackupResult] = []
    for path in sorted(backups_dir.glob("*.db"), reverse=True):
        stat = path.stat()
        found.append(
            BackupResult(
                filename=path.name,
                path=path,
                size_bytes=stat.st_size,
                # The file's own mtime, not the name's timestamp: a backup
                # copied in from elsewhere is still a backup, and its name may
                # not follow our convention at all.
                created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )
        )
    return found


async def list_backups(backups_dir: Path) -> list[BackupResult]:
    """Every backup file on disk, newest first.

    Restore is still a file copy and deliberately not an endpoint — a running
    server cannot swap the file out from under its own open connection — but an
    operator has to be able to *see* what there is to copy without a shell in
    the container.

    ``glob``/``stat`` are blocking syscalls on a bind mount that may be network
    backed, and this event loop is also serving SSE streams.
    """
    return await asyncio.to_thread(_scan, backups_dir)


async def create_backup(db: Database, backups_dir: Path) -> BackupResult:
    """Write a consistent copy of the database into ``backups_dir``.

    The filename is ``gaggiclanker-<UTC timestamp>.db``. ``VACUUM INTO`` refuses
    to overwrite, so a second backup inside the same second gets a ``-2``
    suffix rather than an error.
    """
    now = datetime.now(UTC)
    # mkdir/exists/stat are blocking syscalls; on a network-backed bind mount
    # they are not instant, and the event loop also serves the SSE streams.
    target = await asyncio.to_thread(_pick_target, backups_dir, _timestamp(now))

    # Parameterised: the path is server-derived, but VACUUM INTO takes a bound
    # value and there is no reason to build this string by hand.
    try:
        # isolation_level=None so the driver opens no implicit transaction
        # around the statement — the very thing VACUUM refuses to run inside.
        #
        # `timeout` rather than a `PRAGMA busy_timeout`: the pragma *returns a
        # row*, so its statement stays in progress until the cursor is drained,
        # and VACUUM then refuses with "SQL statements in progress". The
        # constructor argument sets the same thing with nothing left open. Ten
        # seconds is long enough to ride out a checkpoint.
        async with aiosqlite.connect(db.path, isolation_level=None, timeout=10.0) as source:
            await source.execute("VACUUM INTO ?", (str(target),))
    except Exception as exc:
        # The message reaches the client, and SQLite's own text carries the
        # absolute path it failed on. The operator gets the detail from the
        # log line above, keyed by the request id.
        log.error("backup_failed", target=str(target), exc_info=True)
        raise InternalError("Backup failed; see the server log for details") from exc

    size = await asyncio.to_thread(lambda: target.stat().st_size)
    log.info("backup_created", path=str(target), size_bytes=size)
    return BackupResult(filename=target.name, path=target, size_bytes=size, created_at=now)
