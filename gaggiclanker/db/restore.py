"""Restore a database from an uploaded file: stage it, check it, prepare it, swap it in.

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

**Applying** is three steps in three places. :func:`prepare_staged` makes the staged
file what the app should start with (no manifest, nobody signed in, the Writes switch
off, this app's own keys filled in where the file has none). The route then marks the
restore pending and, after its answer is sent, stops the process the ordinary way. The
lifespan runs :func:`swap_in` once ``_shutdown`` has closed the database: one atomic
``os.replace`` of the staged file over the live one on one filesystem. There is no
moment without a database: a crash before the replace leaves the old file (and a
staging file the next boot deletes), a crash after it leaves the new one. Nothing of
the replaced database is kept; keeping a copy is the person's job, by downloading a
backup first.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sqlite3
from collections.abc import AsyncIterator, Callable, Mapping
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
from gaggiclanker.settings import SETTINGS_REGISTRY

__all__ = [
    "MAX_RESTORE_BYTES",
    "RESTORE_MARKER",
    "STAGING_GLOB",
    "PendingRestore",
    "RestoreCounts",
    "RestoreRefused",
    "StagedRestore",
    "clean_stale_staging",
    "consume_restore_marker",
    "discard_staged",
    "prepare_staged",
    "stage_upload",
    "staged_path",
    "swap_in",
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
class PendingRestore:
    """A restore the app has accepted: prepared, and waiting for the process to stop."""

    path: Path
    schema_version: str


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


def prepare_staged(path: Path, live_settings: Mapping[str, str]) -> str:
    """Make the staged file what the app starts with after the restore; returns its schema version.

    On the staged copy only. Drops the manifest; deletes every session (a token belongs
    to the app that issued it); switches the Writes switch off, because a restored
    profile list with Writes on would make the machine match it at the first pull; and,
    for each API key or token the file does not hold, copies this app's value in (so
    restoring your own keyless backup on the same box does not break the chat).
    """
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        # DELETE journal: the staged file must be one file when it is renamed into place.
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE IF EXISTS backup_manifest")
        # A file from before sign-in existed has no session table, and is still a file the
        # check accepted: the preparation must work on every one of those.
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'auth_sessions'"
        ).fetchone():
            conn.execute("DELETE FROM auth_sessions")
        # `updated_at` moves as it does in the settings repository.
        upsert = (
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        )
        conn.execute(
            upsert,
            ("deviceWritesEnabled", SETTINGS_REGISTRY["deviceWritesEnabled"].serialize(False)),
        )
        for key in KEY_SETTING_KEYS:
            held = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if (held is None or held[0] == "") and live_settings.get(key):
                conn.execute(upsert, (key, live_settings[key]))
        conn.execute("COMMIT")
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return str(row[0] or "")
    finally:
        conn.close()
        for sidecar in _sidecars(path):
            sidecar.unlink(missing_ok=True)


#: Written beside the swap, read and deleted at the next boot, so the log says once that the
#: data it opened came from a restore.
RESTORE_MARKER = "restore-done.json"


def _fsync_path(path: Path, *, directory: bool = False) -> None:
    fd = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if directory else 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def swap_in(
    data_dir: Path,
    live: Path,
    staged: Path,
    *,
    schema_version: str,
    before_replace: Callable[[], None] | None = None,
    after_replace: Callable[[], None] | None = None,
) -> bool:
    """Replace the live database file with the staged one. The database must be closed.

    Returns whether the swap happened. It refuses (and the old database stays) when the
    old file still has a non-empty ``-wal``: the close checkpoints and truncates, so
    content there means a close that failed or a second process that still holds the
    file, and deleting it would lose committed data.

    The two hooks exist for the fault-injection tests (a crash just before and just
    after the replace); nothing in the app passes them.
    """
    wal = Path(f"{live}-wal")
    if wal.exists() and wal.stat().st_size > 0:
        log.error("restore_swap_refused", reason="wal_not_empty")
        return False
    # Before the replace, never after: a stale -wal beside the new file is a log SQLite
    # would try to apply to it. The old file was checkpointed, so nothing is lost.
    for sidecar in (wal, Path(f"{live}-shm")):
        sidecar.unlink(missing_ok=True)
    _fsync_path(staged)
    if before_replace is not None:
        before_replace()
    os.replace(staged, live)
    if after_replace is not None:
        after_replace()
    _fsync_path(data_dir, directory=True)
    (data_dir / RESTORE_MARKER).write_text(
        json.dumps({"schema_version": schema_version}), encoding="utf-8"
    )
    log.info("backup_restored", schema_version=schema_version)
    return True


def consume_restore_marker(data_dir: Path) -> dict[str, str] | None:
    """The note a swap left for this boot, deleted as it is read; ``None`` when there is none."""
    marker = data_dir / RESTORE_MARKER
    if not marker.exists():
        return None
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    marker.unlink(missing_ok=True)
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
