"""Carry a database made by an older version forward, at the app's own start.

``db/schema.sql`` ends with ``PRAGMA user_version = N``; ``db/upgrades`` holds one frozen
step per level. When the app starts on a database whose level is below N (and whose structure
is exactly what that level's step expects), :func:`upgrade_database_file` does this, in order:

1. checks there is room for a backup and the transaction's journal (twice the database's size);
2. writes the backup, ``upgrade-backup-L<level>-<time>.db``, the way the reset's marker is
   written: a temporary name, flushed, renamed, and the directory flushed, so the final name
   never exists unless the copy is whole;
3. runs every step from the file's level up in **one transaction** on one raw connection;
4. before committing, checks the result is exactly the schema file (structure and level), that
   it holds together (no reference that did not dangle before, ``integrity_check`` ok).

Any failure rolls back, deletes the backup (the database is as it was, so the copy is
redundant) and refuses with one plain line; the database is never left half-upgraded, and a
crash in the middle leaves an uncommitted transaction, so the next start begins again from the
same level. A backup that was made and kept is never deleted by the app, except by a reset.

Only the app's own start upgrades. ``import`` and ``mcp`` call
:func:`~gaggiclanker.db.schema.check_database_file`, which refuses a database that is behind
(an upgradable one with "start the app once"). ``serve``'s early check calls it with
``allow_older``, which lets every file below the latest level through, so this start either
upgrades it or refuses it with its own line. The restore of an older backup runs the same
:func:`upgrade_connection` on its staged copy.

Steps run on a raw ``sqlite3`` connection, which is the sanctioned exception to "repositories
own SQL" here: a step must not change when a repository does (``db/upgrades``).
"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from gaggiclanker.db.backup import InsufficientStorage, ensure_free_space, live_size, timestamp
from gaggiclanker.db.schema import (
    describe,
    describe_sql,
    differences,
    expected_description,
    fingerprint_of,
    inspect_database_file,
)
from gaggiclanker.db.upgrades import STEPS, Step, frozen_schema_sql

__all__ = [
    "BACKUP_GLOB",
    "BACKUP_PREFIX",
    "StepResult",
    "UpgradeRefused",
    "UpgradeReport",
    "clean_stale_backup_temporaries",
    "run_steps",
    "upgrade_connection",
    "upgrade_database_file",
]

log = structlog.get_logger(__name__)

BACKUP_PREFIX = "upgrade-backup-"
#: Matches a backup and, while it is being written, its temporary name.
BACKUP_GLOB = f"{BACKUP_PREFIX}*"

#: How many differences a refusal puts in its log event.
_REPORTED = 5


class UpgradeRefused(RuntimeError):
    """The upgrade did not happen and the database is as it was. ``str()`` is the plain line."""

    def __init__(
        self,
        message: str,
        *,
        step: int | None = None,
        reason: str = "",
        keep_backup: bool = False,
    ) -> None:
        super().__init__(message)
        #: The level of the step that failed, when one did.
        self.step = step
        self.reason = reason or message
        #: Set when a rollback cannot make the database what it was (a step that committed by
        #: itself): the backup is then the only way back, and stays.
        self.keep_backup = keep_backup


@dataclass(frozen=True, slots=True)
class StepResult:
    level: int
    counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class UpgradeReport:
    from_level: int
    to_level: int
    backup: Path | None
    steps: list[StepResult]


def _level(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def run_steps(conn: sqlite3.Connection, *, steps: Sequence[Step] = STEPS) -> list[StepResult]:
    """Run every step from the connection's level up, inside the caller's transaction.

    Each step first checks the database is the one it expects (its fingerprint), then runs,
    then the level moves to the step's own. Raises :class:`UpgradeRefused` naming the step.
    The caller begins, verifies and commits or rolls back.
    """
    results: list[StepResult] = []
    for step in steps[_level(conn) :]:
        if fingerprint_of(describe(conn)) != step.expects:
            raise UpgradeRefused(
                "the database is not the one this step upgrades",
                step=step.level,
            )
        if step.foreign_keys_off and conn.execute("PRAGMA foreign_keys").fetchone()[0]:
            # Foreign keys cannot be switched off inside the transaction; the caller does it.
            raise UpgradeRefused("the step needs foreign keys off", step=step.level)
        try:
            counts = step.run(conn)
        except Exception as exc:
            raise UpgradeRefused(
                f"{type(exc).__name__}: {exc}",
                step=step.level,
            ) from exc
        if not conn.in_transaction:
            raise UpgradeRefused(
                "the step ended the upgrade's transaction",
                step=step.level,
                keep_backup=True,
            )
        conn.execute(f"PRAGMA user_version = {int(step.level)}")
        results.append(StepResult(step.level, dict(counts)))
    return results


def _violations(conn: sqlite3.Connection) -> Counter[tuple[str, str]]:
    """References that do not hold, counted by the table that refers and the table it names."""
    return Counter((str(row[0]), str(row[2])) for row in conn.execute("PRAGMA foreign_key_check"))


def _renamed(
    violations: Counter[tuple[str, str]], steps: Sequence[Step]
) -> Counter[tuple[str, str]]:
    """The violations counted before the steps ran, under the table names they will have after.

    A step that renames tables moves a reference that already dangled to a new name, and it
    must not read as a reference the upgrade broke.
    """
    out = violations
    for step in steps:
        if not step.renamed_tables:
            continue
        moved: Counter[tuple[str, str]] = Counter()
        for (table, parent), count in out.items():
            key = (step.renamed_tables.get(table, table), step.renamed_tables.get(parent, parent))
            moved[key] += count
        out = moved
    return out


def _verify(
    conn: sqlite3.Connection, expected: dict[str, str], before: Counter[tuple[str, str]]
) -> str | None:
    """Why the result may not be committed; ``None`` when it may."""
    found = differences(describe(conn), expected)
    if found:
        return f"the result differs from the schema: {found[0]}"
    # Counted by (table, parent) so a rebuild that renumbers a table's foreign keys does not
    # make an old dangling row look new; a reference that already dangled does not stop the
    # upgrade, one the upgrade broke does.
    worse = _violations(conn) - before
    if worse:
        (table, parent), _ = next(iter(worse.items()))
        return f"the result has rows in {table} that refer to missing rows in {parent}"
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        return "the result fails SQLite's integrity check"
    return None


def upgrade_connection(
    conn: sqlite3.Connection,
    *,
    steps: Sequence[Step] = STEPS,
    expected: dict[str, str] | None = None,
) -> list[StepResult]:
    """Upgrade the database behind ``conn`` in one transaction, or change nothing.

    ``conn`` is a raw connection with ``isolation_level=None`` and nothing in progress. Raises
    :class:`UpgradeRefused` (after rolling back) when a step fails or the result is not the
    schema. Foreign keys are switched off for the whole transaction when any step to run needs
    it (they cannot change inside one), and back on afterwards.
    """
    want = expected_description() if expected is None else expected
    pending = steps[_level(conn) :]
    conn.execute(
        "PRAGMA foreign_keys = OFF"
        if any(s.foreign_keys_off for s in pending)
        else "PRAGMA foreign_keys = ON"
    )
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            before = _renamed(_violations(conn), pending)
            results = run_steps(conn, steps=steps)
            problem = _verify(conn, want, before)
            if problem is not None:
                raise UpgradeRefused(problem)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return results


def _fsync(path: Path, *, directory: bool = False) -> None:
    fd = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if directory else 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_backup(database: Path, data_dir: Path, level: int, now: datetime) -> Path:
    """A whole copy of the database, written under its final name only once it is complete.

    Temporary name, flushed, renamed, directory flushed. A failure of any kind removes both
    names and refuses, so a copy that was reported as failed cannot survive looking whole.
    """
    final = data_dir / f"{BACKUP_PREFIX}L{level}-{timestamp(now)}.db"
    temporary = final.with_name(f"{final.name}.tmp")
    try:
        # A plain connection: VACUUM INTO only reads the source, and the timeout rides out a
        # checkpoint. isolation_level=None because VACUUM cannot run inside a transaction.
        source = sqlite3.connect(database, isolation_level=None, timeout=10.0)
        try:
            source.execute("VACUUM INTO ?", (str(temporary),))
        finally:
            source.close()
        copy = sqlite3.connect(temporary, isolation_level=None)
        try:
            # One file, whatever the source's journal mode was.
            copy.execute("PRAGMA journal_mode = DELETE")
        finally:
            copy.close()
        _fsync(temporary)
        os.replace(temporary, final)
        _fsync(data_dir, directory=True)
    except BaseException as exc:
        temporary.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        try:
            _fsync(data_dir, directory=True)
        except OSError:
            log.debug("upgrade_backup_dir_fsync_failed", exc_info=True)
        if isinstance(exc, Exception):
            log.error("database_upgrade_backup_failed", exc_info=True)
            raise UpgradeRefused(
                f"The database at {database} could not be backed up before its upgrade, so it "
                "was not upgraded and was not changed. Check the data directory has room and "
                "can be written, then start again.",
                reason=f"backup: {type(exc).__name__}: {exc}",
            ) from exc
        raise
    return final


def clean_stale_backup_temporaries(data_dir: Path) -> int:
    """Delete a backup's temporary file a crash left behind; returns how many."""
    removed = 0
    for path in data_dir.glob(f"{BACKUP_PREFIX}*.db.tmp"):
        path.unlink(missing_ok=True)
        removed += 1
    if removed:
        log.info("database_upgrade_temporaries_removed", count=removed)
    return removed


def upgrade_database_file(
    database: Path,
    data_dir: Path,
    *,
    steps: Sequence[Step] = STEPS,
    expected: dict[str, str] | None = None,
    frozen_sql: Callable[[int], str] = frozen_schema_sql,
    now: datetime | None = None,
) -> UpgradeReport | None:
    """Upgrade the database file if it is an older version's; ``None`` when it is not.

    A missing or empty file, a file at the latest level, a file made by a newer version, and a
    file at the latest level with another structure are left alone: ``check_database_file``
    runs next and decides. Raises :class:`UpgradeRefused` (the database untouched) for an older
    database that does not match its level, for lack of room, for a backup that could not be
    made, and for a step or a check that failed.
    """
    clean_stale_backup_temporaries(data_dir)
    want = expected_description() if expected is None else expected
    inspection = inspect_database_file(database, steps=steps, expected=want)
    level = inspection.level
    if inspection.case == "differs" and level is not None and level < int(want["user_version"]):
        # An older level whose structure is not the one its step upgrades from: not ours to guess.
        try:
            against = differences(inspection.description, describe_sql(frozen_sql(level)))
        except (OSError, sqlite3.Error):
            against = inspection.found
        log.error(
            "database_upgrade_refused",
            path=str(database),
            db_level=level,
            count=len(against),
            differences=against[:_REPORTED],
        )
        raise UpgradeRefused(
            f"The database at {database} was made by an older version of gaggiclanker and is "
            "not in the state that version left it, so it cannot be upgraded. It was not "
            "changed. Restore a backup made by that version, or delete it to start fresh.",
            reason="structure differs from its level",
        )
    if inspection.case != "upgradable" or level is None:
        return None

    try:
        ensure_free_space(data_dir, 2 * live_size(database))
    except InsufficientStorage as exc:
        log.error("database_upgrade_refused", path=str(database), db_level=level, reason="disk")
        raise UpgradeRefused(
            "There is not enough free disk space on the data volume to upgrade the database "
            "(it needs room for a copy of it first). Free some space and start again; the "
            "database was not changed.",
            reason="not enough free disk space",
        ) from exc

    backup = write_backup(database, data_dir, level, now or datetime.now(UTC))
    conn = sqlite3.connect(database, isolation_level=None, timeout=10.0)
    try:
        results = upgrade_connection(conn, steps=steps, expected=want)
    except BaseException as exc:
        # Rolled back: the database is as it was, so the copy is redundant.
        keep = isinstance(exc, UpgradeRefused) and exc.keep_backup
        if not keep:
            backup.unlink(missing_ok=True)
        if not isinstance(exc, UpgradeRefused):
            if isinstance(exc, Exception):
                log.error("database_upgrade_failed", path=str(database), exc_info=True)
                raise UpgradeRefused(
                    f"Upgrading the database at {database} failed ({type(exc).__name__}), "
                    "so it was not upgraded and was not changed.",
                    reason=f"{type(exc).__name__}: {exc}",
                ) from exc
            raise
        log.error(
            "database_upgrade_failed",
            path=str(database),
            from_level=level,
            step=exc.step,
            reason=exc.reason,
        )
        where = f" at step {exc.step}" if exc.step is not None else ""
        outcome = (
            f"It may be partly changed; the copy made before is at {backup}."
            if keep
            else "It was not upgraded and was not changed."
        )
        raise UpgradeRefused(
            f"Upgrading the database at {database} failed{where}: {exc.reason}. {outcome}",
            step=exc.step,
            reason=exc.reason,
            keep_backup=keep,
        ) from exc
    finally:
        conn.close()
    report = UpgradeReport(level, results[-1].level if results else level, backup, results)
    log.info(
        "database_upgraded",
        from_level=report.from_level,
        to_level=report.to_level,
        backup=str(backup),
        steps={str(r.level): r.counts for r in results},
    )
    return report
