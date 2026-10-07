"""Restore a database from an uploaded file: stage it, check it, and (later) swap it in.

The restore takes **only an uploaded file**: no copy kept on the server is ever
offered. The upload is streamed into ``DATA_DIR/restore-staging-<token>.db`` —
in the data directory, never ``/tmp`` or a multipart spool, so the final step is
an atomic rename on one filesystem — and checked there. Nothing in this module's
check half touches the live database or the live file.

What "a file we can restore" means (every one is a refusal with its own code):

* it starts with the SQLite header;
* it opens read-only and passes ``PRAGMA integrity_check``;
* it has the ledger and the core tables;
* every migration in its ledger is one this build knows, with the checksum this
  build computes (:func:`~gaggiclanker.db.migrations.classify_ledger`, the
  boot's own rule: what a boot would refuse, a restore refuses before touching
  anything). An *older* file is fine: the next boot migrates it;
* ``PRAGMA foreign_key_check`` is empty.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import structlog
from pydantic import BaseModel, ValidationError
from starlette.requests import ClientDisconnect

from gaggiclanker.db.backup import KEY_SETTING_KEYS, BackupManifest, ensure_free_space
from gaggiclanker.db.migrations import (
    ChangedMigration,
    NewerDatabase,
    classify_ledger,
    load_migrations,
)
from gaggiclanker.infra.errors import AppError, BadRequest, PayloadTooLarge

__all__ = [
    "MAX_RESTORE_BYTES",
    "STAGING_GLOB",
    "RestoreCounts",
    "RestoreRefused",
    "StagedRestore",
    "clean_stale_staging",
    "discard_staged",
    "stage_upload",
    "staged_path",
    "validate_staged",
]

log = structlog.get_logger(__name__)

#: The restore route's body limit: the whole archive, raw `.slog` bytes included.
MAX_RESTORE_BYTES = 1024 * 1024 * 1024

STAGING_PREFIX = "restore-staging-"
STAGING_GLOB = f"{STAGING_PREFIX}*"

_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_SQLITE_HEADER = b"SQLite format 3\x00"
_CORE_TABLES = ("schema_migrations", "settings", "shots", "sets", "beans")
_CHUNK = 1024 * 1024


class RestoreRefused(AppError):
    """The file cannot be restored. ``code`` says why; nothing was changed."""

    status = 422

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message, code=code)


class RestoreCounts(BaseModel):
    shots: int
    sets: int
    beans: int


@dataclass(frozen=True, slots=True)
class StagedRestore:
    """A staged file that passed the check, and what the preview shows about it."""

    token: str
    path: Path
    size_bytes: int
    manifest: BackupManifest | None
    counts: RestoreCounts
    keys_in_file: bool


def new_token() -> str:
    return secrets.token_hex(16)


def staged_path(data_dir: Path, token: str) -> Path:
    """The staging file for ``token``; a token that is not 32 hex digits names nothing."""
    if not _TOKEN.match(token):
        raise RestoreRefused("RESTORE_NOT_STAGED", "There is no staged file to restore.")
    return data_dir / f"{STAGING_PREFIX}{token}.db"


def _sidecars(path: Path) -> list[Path]:
    return [Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")]


def discard_staged(path: Path) -> None:
    """Delete a staged file and anything SQLite made beside it."""
    for target in (path, *_sidecars(path)):
        target.unlink(missing_ok=True)


def clean_stale_staging(data_dir: Path) -> int:
    """Delete every staging file left by an upload that never finished or was never applied."""
    removed = 0
    for path in data_dir.glob(STAGING_GLOB):
        path.unlink(missing_ok=True)
        removed += 1
    if removed:
        log.info("restore_staging_removed", count=removed)
    return removed


async def stage_upload(
    data_dir: Path, chunks: AsyncIterator[bytes], *, declared_size: int | None
) -> tuple[str, Path]:
    """Stream an upload into a fresh staging file; any earlier staged file goes first.

    Counted as it arrives, so a body without ``Content-Length`` is held to the limit
    too. A failure of any kind removes what was written.
    """
    if declared_size is not None:
        # Twice: the staged copy, and the room the apply step needs to prepare it.
        await asyncio.to_thread(ensure_free_space, data_dir, 2 * declared_size)
    await asyncio.to_thread(_discard_all_staged, data_dir)
    token = new_token()
    path = staged_path(data_dir, token)
    written = 0
    try:
        with path.open("wb") as out:
            async for chunk in chunks:
                written += len(chunk)
                if written > MAX_RESTORE_BYTES:
                    raise PayloadTooLarge("That file is larger than the 1 GB restore limit.")
                await asyncio.to_thread(out.write, chunk)
            out.flush()
    except ClientDisconnect as exc:
        # The client left, or the body-limit middleware cut a body that went over: in
        # the second case it replaces whatever is answered here with its own 413.
        discard_staged(path)
        raise BadRequest("The upload was interrupted.") from exc
    except BaseException:
        discard_staged(path)
        raise
    return token, path


def _discard_all_staged(data_dir: Path) -> None:
    for path in data_dir.glob(STAGING_GLOB):
        discard_staged(path)


def _scalar(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _read_manifest(conn: sqlite3.Connection) -> BackupManifest | None:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'backup_manifest'"
    ).fetchone()
    if has is None:
        return None
    row = conn.execute(
        "SELECT format_version, app_version, schema_version, created_at, keys_included "
        "FROM backup_manifest LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    try:
        return BackupManifest(
            format_version=row[0],
            app_version=row[1],
            schema_version=row[2],
            created_at=row[3],
            keys_included=bool(row[4]),
        )
    except ValidationError:
        # Details only for the preview: a manifest we cannot read is "a plain copy".
        return None


def validate_staged(path: Path, token: str) -> StagedRestore:
    """Run every check on a staged file, read-only. Raises :class:`RestoreRefused`."""
    with path.open("rb") as handle:
        if handle.read(len(_SQLITE_HEADER)) != _SQLITE_HEADER:
            raise RestoreRefused("RESTORE_NOT_A_DATABASE", "This is not a gaggiclanker database.")
    try:
        return _validate_open(path, token)
    except sqlite3.DatabaseError as exc:
        # A page that cannot be read anywhere in the checks is the same fault as a failed
        # integrity check. The message is SQLite's own and carries the path: log it, keep
        # it out of the answer.
        log.warning("restore_unreadable", error=str(exc))
        raise RestoreRefused(
            "RESTORE_DAMAGED", "This file is damaged: its integrity check failed."
        ) from exc
    finally:
        # A read-only open of a WAL-mode file makes -wal and -shm beside it.
        for sidecar in _sidecars(path):
            sidecar.unlink(missing_ok=True)


def _validate_open(path: Path, token: str) -> StagedRestore:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not set(_CORE_TABLES) <= names:
            raise RestoreRefused("RESTORE_NOT_A_DATABASE", "This is not a gaggiclanker database.")
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise RestoreRefused(
                "RESTORE_DAMAGED", "This file is damaged: its integrity check failed."
            )
        ledger = {
            str(v): str(c)
            for v, c in conn.execute("SELECT version, checksum FROM schema_migrations")
        }
        try:
            classify_ledger(ledger, load_migrations())
        except NewerDatabase as exc:
            raise RestoreRefused(
                "RESTORE_NEWER_VERSION",
                "This file was written by a newer version of gaggiclanker. "
                "Update the app, then restore it.",
            ) from exc
        except ChangedMigration as exc:
            raise RestoreRefused(
                "RESTORE_MIGRATION_DIFFERS",
                "A migration in this file differs from this version's, so this app cannot "
                "open it safely.",
            ) from exc
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RestoreRefused(
                "RESTORE_DAMAGED", "This file is damaged: its references do not hold together."
            )
        marks = ",".join("?" for _ in KEY_SETTING_KEYS)
        sql = f"SELECT COUNT(*) FROM settings WHERE key IN ({marks}) AND value <> ''"  # noqa: S608
        keys_in_file = conn.execute(sql, KEY_SETTING_KEYS).fetchone()[0] > 0
        return StagedRestore(
            token=token,
            path=path,
            size_bytes=path.stat().st_size,
            manifest=_read_manifest(conn),
            counts=RestoreCounts(
                shots=_scalar(conn, "SELECT COUNT(*) FROM shots"),
                sets=_scalar(conn, "SELECT COUNT(*) FROM sets"),
                beans=_scalar(conn, "SELECT COUNT(*) FROM beans"),
            ),
            keys_in_file=keys_in_file,
        )
    finally:
        conn.close()
