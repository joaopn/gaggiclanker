"""Forward-only SQL migrations with a ledger.

Every file in ``db/migrations/NNNN_name.sql`` is applied once, in filename
order, inside a transaction that also carries its ``schema_migrations`` row, and
recorded there with the sha256 of the text that was applied. A file that fails
half-way leaves the database exactly as it was — no partial schema, no ledger
row — and the next boot retries it from the start.

A migration file must therefore contain no transaction control of its own
(``BEGIN``, ``COMMIT``, ``ROLLBACK``) and no statement SQLite refuses inside a
transaction (``VACUUM``, most ``PRAGMA`` writes). SQLite's DDL is transactional,
so everything a schema change actually needs is available.

The checksum is the point of the ledger. cvclanker's migrate.ts relies on
idempotent SQL (``CREATE TABLE IF NOT EXISTS``, "duplicate column name" treated
as success) with no record of what ran, which works until the first migration
that cannot be re-run. Here an applied file that changes afterwards is a hard
error at boot: editing a shipped migration silently gives two installs
different schemas, and finding that out weeks later, from a query that returns
the wrong answer, is much worse than refusing to start.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import structlog

from gaggiclanker.db.connection import Database

__all__ = ["Migration", "MigrationError", "load_migrations", "run_migrations"]

log = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# NNNN_some_name.sql — the numeric prefix is the version and orders the run.
_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT
"""


class MigrationError(RuntimeError):
    """A migration could not be applied, or the ledger disagrees with the files."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One migration file, read and hashed."""

    version: str
    name: str
    sql: str
    path: Path

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def load_migrations(directory: Path | None = None) -> list[Migration]:
    """Read every migration file in ``directory``, ordered by version."""
    directory = directory or MIGRATIONS_DIR
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")

    migrations: list[Migration] = []
    seen: dict[str, Path] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(f"migration file {path.name!r} does not match NNNN_name.sql")
        version = match["version"]
        if version in seen:
            raise MigrationError(
                f"duplicate migration version {version}: {seen[version].name} and {path.name}"
            )
        seen[version] = path
        migrations.append(
            Migration(
                version=version,
                name=match["name"],
                sql=path.read_text(encoding="utf-8"),
                path=path,
            )
        )
    return migrations


async def _rollback(db: Database) -> None:
    """Undo a half-applied migration. Tolerates there being nothing to undo."""
    try:
        await db.execute("ROLLBACK")
    except Exception:  # pragma: no cover - no transaction was open
        log.debug("migration_rollback_noop", exc_info=True)


async def _applied(db: Database) -> dict[str, str]:
    rows = await db.fetch_all("SELECT version, checksum FROM schema_migrations")
    return {str(row["version"]): str(row["checksum"]) for row in rows}


async def run_migrations(db: Database, directory: Path | None = None) -> list[str]:
    """Apply every pending migration. Returns the versions applied this run."""
    await db.execute(_LEDGER_DDL)

    migrations = load_migrations(directory)
    applied = await _applied(db)

    for version, checksum in applied.items():
        known = next((m for m in migrations if m.version == version), None)
        if known is None:
            raise MigrationError(
                f"migration {version} is recorded in schema_migrations but its file is missing; "
                "this database was written by a newer version of gaggiclanker"
            )
        if known.checksum != checksum:
            raise MigrationError(
                f"migration {version}_{known.name} changed after it was applied "
                f"(recorded {checksum[:12]}, file {known.checksum[:12]}). "
                "Migrations are immutable once shipped: add a new one instead."
            )

    pending = [m for m in migrations if m.version not in applied]
    if not pending:
        log.debug("migrations_up_to_date", count=len(migrations))
        return []

    done: list[str] = []
    for migration in pending:
        log.info("migration_applying", version=migration.version, name=migration.name)
        # executescript() commits whatever is pending and then runs the text
        # verbatim, so prefixing BEGIN opens a transaction the script does not
        # close. The ledger insert then joins that transaction and one COMMIT
        # makes schema and ledger move together.
        try:
            await db.execute_script(f"BEGIN;\n{migration.sql}")
            await db.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
                (migration.version, migration.name, migration.checksum),
            )
            await db.execute("COMMIT")
        except Exception as exc:
            await _rollback(db)
            raise MigrationError(
                f"migration {migration.version}_{migration.name} failed: {exc}"
            ) from exc
        done.append(migration.version)

    log.info("migrations_applied", versions=done)
    return done
