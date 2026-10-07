"""Download the whole database as one file, via ``VACUUM INTO``.

The whole install is one SQLite file — shots, raw ``.slog`` bytes, profiles,
judgements, reviews, settings — so a backup is a copy of it. ``VACUUM INTO`` is
the correct way to take one while the app is running: it produces a consistent,
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

**The file is made in a temp directory under ``DATA_DIR``**, never ``/tmp``: it
is on the same filesystem as the data, so the free-space check means what it
says, and a crash leaves it where the next boot's clean-up looks
(:func:`clean_stale_exports`: a copy interrupted before its keys were stripped
is a file full of credentials, so it must not outlive the process).

After the copy a second, throwaway connection works on the *copy* only (the live
database is never written to by this module): it adds the ``backup_manifest``
table the restore reads, empties ``auth_sessions`` (a token is a credential for
the app it was issued by, not data), and unless the person asked for the keys,
removes the API keys and tokens. The file is then vacuumed **again**: a deleted
row's bytes stay in the free pages of a SQLite file, so a stripped copy that
skipped this would still hold the key in its raw bytes.

This is the one module outside the repositories that writes SQL, because the file
it writes is not the application's database: it is a copy on its way out of the
building, and the manifest it adds is built and validated as a model first.

Restoring a file is :mod:`gaggiclanker.db.restore`.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import structlog
from pydantic import BaseModel, ConfigDict

from gaggiclanker import __version__
from gaggiclanker.auth.service import JWT_SECRET_KEY
from gaggiclanker.db.connection import Database
from gaggiclanker.infra.errors import AppError, InternalError
from gaggiclanker.settings import SETTINGS_REGISTRY

__all__ = [
    "KEY_SETTING_KEYS",
    "MANIFEST_FORMAT_VERSION",
    "SIGNING_KEY_ROW",
    "BackupManifest",
    "ExportedBackup",
    "InsufficientStorage",
    "clean_stale_exports",
    "create_export",
    "ensure_free_space",
    "timestamp",
]

log = structlog.get_logger(__name__)

#: The manifest's own shape, bumped only if the table below changes meaning.
MANIFEST_FORMAT_VERSION = 1

#: The settings an "include API keys and tokens" box covers: every secret setting
#: except the sign-in hash. Derived from the registry rather than listed, so a new
#: secret setting is stripped from a keyless backup the day it is added;
#: ``tests/test_backup.py`` names the three it is today. The hash is left out on
#: purpose: sign-in is on only when the user *and* a hash are set, so a file
#: without the hash would restore an app with every page open.
KEY_SETTING_KEYS: tuple[str, ...] = tuple(
    key
    for key, definition in SETTINGS_REGISTRY.items()
    if definition.secret and key != "authPasswordHash"
)

#: The ``runtime_secrets`` row holding the session signing key: the fourth thing
#: the box covers. It is not a setting, so it is named here.
SIGNING_KEY_ROW = JWT_SECRET_KEY

EXPORT_DIR_PREFIX = "backup-export-"

_MANIFEST_DDL = """
CREATE TABLE backup_manifest (
    format_version INTEGER NOT NULL,
    app_version    TEXT    NOT NULL,
    schema_version TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    keys_included  INTEGER NOT NULL
) STRICT
"""


class InsufficientStorage(AppError):
    """The disk does not have room for the copy this call would make."""

    status = 507
    code = "INSUFFICIENT_STORAGE"

    def __init__(self, message: str = "Not enough free disk space for this") -> None:
        super().__init__(message)


class BackupManifest(BaseModel):
    """The one row of ``backup_manifest``: what a file is and when it was taken."""

    model_config = ConfigDict(extra="forbid")

    format_version: int
    app_version: str
    schema_version: str
    created_at: str
    keys_included: bool


@dataclass(frozen=True, slots=True)
class ExportedBackup:
    """A finished export waiting in its temp directory to be sent."""

    path: Path
    directory: Path
    filename: str
    size_bytes: int
    keys_included: bool

    def discard(self) -> None:
        """Remove the temp directory; safe to call twice, and after the client went away."""
        shutil.rmtree(self.directory, ignore_errors=True)


def timestamp(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def ensure_free_space(directory: Path, needed: int) -> None:
    """Refuse, with a stable code, when ``directory``'s filesystem has less than ``needed``."""
    free = shutil.disk_usage(directory).free
    if free < needed:
        log.warning("backup_refused_disk", free_bytes=free, needed_bytes=needed)
        raise InsufficientStorage(
            "There is not enough free disk space on the data volume for this. "
            "Free some space and try again."
        )


def clean_stale_exports(data_dir: Path) -> int:
    """Delete export temp directories a stopped process left behind; returns how many."""
    removed = 0
    for path in data_dir.glob(f"{EXPORT_DIR_PREFIX}*"):
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    if removed:
        log.info("backup_exports_removed", count=removed)
    return removed


def _live_size(path: Path) -> int:
    return sum(p.stat().st_size for p in (path, Path(f"{path}-wal")) if p.exists())


def _prepare_copy(path: Path, *, include_keys: bool, now: datetime) -> None:
    """Manifest, sessions and keys, on the copy; then vacuum it again (see the module docstring)."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        # DELETE journal: the finished file must be one file, and no -wal may carry
        # a stripped value anywhere near it.
        conn.execute("PRAGMA journal_mode = DELETE")
        # Overwrite deleted content with zeros too: the vacuum below makes it moot,
        # and a step that is only safe because of the next one is one edit from not being.
        conn.execute("PRAGMA secure_delete = ON")
        schema_version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        manifest = BackupManifest(
            format_version=MANIFEST_FORMAT_VERSION,
            app_version=__version__,
            schema_version=str(schema_version or ""),
            created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            keys_included=include_keys,
        )
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_MANIFEST_DDL)
        conn.execute(
            "INSERT INTO backup_manifest (format_version, app_version, schema_version, "
            "created_at, keys_included) VALUES (?, ?, ?, ?, ?)",
            (
                manifest.format_version,
                manifest.app_version,
                manifest.schema_version,
                manifest.created_at,
                int(manifest.keys_included),
            ),
        )
        conn.execute("DELETE FROM auth_sessions")
        if not include_keys:
            marks = ",".join("?" for _ in KEY_SETTING_KEYS)
            conn.execute(f"DELETE FROM settings WHERE key IN ({marks})", KEY_SETTING_KEYS)  # noqa: S608
            conn.execute("DELETE FROM runtime_secrets WHERE key = ?", (SIGNING_KEY_ROW,))
        conn.execute("COMMIT")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


async def create_export(db: Database, data_dir: Path, *, include_keys: bool) -> ExportedBackup:
    """Write a consistent copy of the database for download, in a temp directory.

    The caller sends it and calls :meth:`ExportedBackup.discard`; an export that
    fails here cleans up after itself.
    """
    now = datetime.now(UTC)
    await asyncio.to_thread(
        ensure_free_space, data_dir, 2 * await asyncio.to_thread(_live_size, db.path)
    )
    directory = Path(
        await asyncio.to_thread(tempfile.mkdtemp, prefix=EXPORT_DIR_PREFIX, dir=data_dir)
    )
    target = directory / "backup.db"
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
            # Parameterised: the path is server-derived, but VACUUM INTO takes a
            # bound value and there is no reason to build this string by hand.
            await source.execute("VACUUM INTO ?", (str(target),))
        await asyncio.to_thread(_prepare_copy, target, include_keys=include_keys, now=now)
        size = await asyncio.to_thread(lambda: target.stat().st_size)
    except BaseException as exc:
        shutil.rmtree(directory, ignore_errors=True)
        if isinstance(exc, Exception):
            # The message reaches the client, and SQLite's own text carries the
            # absolute path it failed on. The operator gets the detail from the
            # log line below, keyed by the request id.
            log.error("backup_failed", exc_info=True)
            raise InternalError("Backup failed; see the server log for details") from exc
        raise
    log.info("backup_exported", size_bytes=size, keys_included=include_keys)
    return ExportedBackup(
        path=target,
        directory=directory,
        filename=f"gaggiclanker-{timestamp(now)}.db",
        size_bytes=size,
        keys_included=include_keys,
    )
