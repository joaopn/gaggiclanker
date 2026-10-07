"""Put the data directory back to a fresh install.

The reset is **committed by a marker file, and carried out at the next boot**. The route
writes ``DATA_DIR/reset-requested`` (file and directory flushed) before it answers 202;
from then on the reset happens no matter how the process ends. The boot looks for the
marker **before the database is opened** and deletes what the app wrote: the database
with its ``-wal``, ``-shm`` and ``-journal``, every restore staging file, every backup
export directory, the restore's note, and the downloaded Claude Code program. The marker
goes last, so a crash in the middle of the deletion finishes it at the following start.

Doing it at boot rather than in the shutdown is what makes it crash-safe, and deleting the
database together with its sidecars before anything opens it means an old write-ahead log
can never be replayed onto the new database. The fresh database is the ordinary first
boot's: migrations and seeds.

Not deleted: the data directory itself and the ``.write-test`` probe the boot makes.
Everything else the app writes under ``DATA_DIR`` is in the list below; an audit of the
code found no other writer (the Claude CLI's scratch directories live in the system temp
directory and are removed by the provider).
"""

from __future__ import annotations

import os
import shutil
from contextlib import suppress
from pathlib import Path

import structlog

from gaggiclanker.db.backup import EXPORT_DIR_PREFIX
from gaggiclanker.db.restore import RESTORE_MARKER, STAGING_GLOB
from gaggiclanker.settings import CLAUDE_CODE_DIRNAME

__all__ = [
    "RESET_MARKER",
    "RESET_WAITING_MESSAGE",
    "complete_reset",
    "reset_is_waiting",
    "write_reset_marker",
]

log = structlog.get_logger(__name__)

#: The commitment: present means "this data directory is to be emptied at the next start".
RESET_MARKER = "reset-requested"

#: The marker is written under this name and renamed, so the final name never exists unless
#: the whole write succeeded. A leftover is deleted at boot and never counts.
_MARKER_TMP = f"{RESET_MARKER}.tmp"

_DATABASE = "gaggiclanker.db"

#: What the offline commands (import, mcp) say when they find a reset waiting.
RESET_WAITING_MESSAGE = "A reset is waiting to finish: start the app once, then run this again."


def reset_is_waiting(data_dir: Path) -> bool:
    """Whether a reset has been committed and not yet carried out."""
    return (data_dir / RESET_MARKER).exists()


def _fsync(path: Path, *, directory: bool = False) -> None:
    fd = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if directory else 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_reset_marker(data_dir: Path) -> None:
    """Commit to a reset: on disk, whole, under its final name, before this returns.

    Written to a temporary name, flushed, renamed, and the directory flushed. Any failure
    removes both names (and flushes the directory again), so a marker the caller was told
    failed can never survive to wipe the archive at some later, ordinary start.
    """
    marker = data_dir / RESET_MARKER
    temporary = data_dir / _MARKER_TMP
    try:
        with temporary.open("wb") as handle:
            handle.write(b"reset\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
        _fsync(data_dir, directory=True)
    except BaseException:
        temporary.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        with suppress(OSError):
            _fsync(data_dir, directory=True)
        raise


def _doomed(data_dir: Path) -> list[Path]:
    database = data_dir / _DATABASE
    paths = [database, *(Path(f"{database}{suffix}") for suffix in ("-wal", "-shm", "-journal"))]
    paths.append(data_dir / RESTORE_MARKER)
    paths.extend(data_dir.glob(STAGING_GLOB))
    # A staging file's own sidecars carry the same prefix, so the glob has them.
    paths.extend(data_dir.glob(f"{EXPORT_DIR_PREFIX}*"))
    paths.append(data_dir / CLAUDE_CODE_DIRNAME)
    return paths


def complete_reset(data_dir: Path) -> bool:
    """Finish a requested reset; ``False`` (and nothing deleted) when no marker is there.

    Called at boot before the database is opened. Idempotent: every step tolerates what an
    earlier, interrupted run already removed.
    """
    marker = data_dir / RESET_MARKER
    # A write that never reached its rename: not a commitment.
    (data_dir / _MARKER_TMP).unlink(missing_ok=True)
    if not marker.exists():
        return False
    removed = 0
    for path in _doomed(data_dir):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
            removed += 1
        elif path.exists() or path.is_symlink():
            path.unlink()
            removed += 1
    # Contents first, then the commitment: a crash before this line repeats the reset.
    _fsync(data_dir, directory=True)
    marker.unlink()
    _fsync(data_dir, directory=True)
    log.info("app_reset", removed=removed)
    return True
