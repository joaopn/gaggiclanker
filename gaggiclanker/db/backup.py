"""Database backup via ``VACUUM INTO``.

The whole install is one SQLite file — shots, raw ``.slog`` bytes, profiles,
judgements, analyses — so a backup is a file copy. ``VACUUM INTO`` is the
correct way to take one while the app is running: it produces a consistent,
defragmented copy from a read transaction without stopping writers, which
``cp`` cannot promise with WAL in play.

Restoring is deliberately not an endpoint: it means stopping the container and
copying the file back over ``DATA_DIR/gaggiclanker.db``. A running server
cannot swap the file out from under its own open connection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.infra.errors import InternalError

__all__ = ["BackupResult", "create_backup"]

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
        await db.execute("VACUUM INTO ?", (str(target),))
    except Exception as exc:
        # The message reaches the client, and SQLite's own text carries the
        # absolute path it failed on. The operator gets the detail from the
        # log line above, keyed by the request id.
        log.error("backup_failed", target=str(target), exc_info=True)
        raise InternalError("Backup failed; see the server log for details") from exc

    size = await asyncio.to_thread(lambda: target.stat().st_size)
    log.info("backup_created", path=str(target), size_bytes=size)
    return BackupResult(filename=target.name, path=target, size_bytes=size, created_at=now)
