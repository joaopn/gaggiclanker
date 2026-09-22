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
that cannot be re-run. Here an applied file whose SQL changes afterwards is a
hard error at boot: editing a shipped migration silently gives two installs
different schemas, and finding that out weeks later, from a query that returns
the wrong answer, is much worse than refusing to start.

**The checksum covers what runs, not the file's bytes.** It is taken over the
SQL with comments removed and whitespace collapsed (string literals and quoted
names kept exactly), so a reworded comment or a re-indented line is not a
change and never refuses a boot. A refusal meant deleting the database, and a
database must only ever be lost to a change that needs it. Ledger rows written
before this rule carry the sha256 of the raw bytes; a row that matches that way
is accepted and rewritten to the statement checksum, once — by the file's
current bytes, or by the bytes every shipped file had when the rule came in
(``_BYTE_CHECKSUMS``), so a comment edited in between does not strand it. What keeps a real
edit from shipping at all is ``tests/test_migrations_frozen.py``, which pins
every shipped file's checksum.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import structlog

from gaggiclanker.db.connection import Database

__all__ = ["Migration", "MigrationError", "load_migrations", "run_migrations", "statements"]

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
        """The sha256 of the statements this file runs (see ``statements``)."""
        return hashlib.sha256(statements(self.sql).encode("utf-8")).hexdigest()

    @property
    def byte_checksum(self) -> str:
        """The sha256 of the raw text: what ledgers recorded before ``checksum``."""
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


# Every shipped file as the ledger recorded it before the statement checksum:
# version -> (sha256 of the file's bytes, sha256 of its statements). A row
# holding the first is from a database made before the change, and it is
# accepted as long as the file still runs the same statements — whatever its
# comments say by then, which the bytes alone could not tell. Frozen: a file
# added later is only ever recorded by its statement checksum.
_BYTE_CHECKSUMS: dict[str, tuple[str, str]] = {
    "0001": (
        "698af876bc7e69b777454bc1e185170d14ae4ae2bfd002ddbe1c8d87563d3872",
        "f8a49816e2da28d1db1f9f1cae82482eee475c7c3c2981d6f936d8570995b300",
    ),
    "0002": (
        "95420a1e2c784fd4886f6218a7cd9a1a77243670dbd0d99f99ad541063434f5c",
        "ce9b1558f74e7de48f6695594c3720c9a9d7b3f857dc2403fe3690f8fb0b9f36",
    ),
    "0003": (
        "3e72cf444f7522746b8ce0b6506eee3ba4eaa6157a8a5ba7240d581972089db0",
        "280f4540d2f6aa8a41a6935e65f684fbe195571fa68eea3a39d7a740c00f2c02",
    ),
    "0004": (
        "372710985ee57428485d1e6d0536ca357be7a79aca4bc1db11729145b7323cfa",
        "29a5e836d528154d26dbbfddb8a8e2c4f09107b3044f8426cb3609ba5606d685",
    ),
    "0005": (
        "4487271f0e423969e24cdbc3a9e6609147cb85467596effc97850bf1c7478992",
        "6bcaf046a0657684e3e79d216babc2f4e5982dee2e50e4f4b07faadb502acc3a",
    ),
    "0006": (
        "59858b6e95444d71feaeba23bde1efc6a9567ab0f896aa8f195f083d2ea75c59",
        "9896e0dbf3df8cb96de04d08cf32a9562b3480a36e52bfb1ec09ee14e2287978",
    ),
    "0007": (
        "8357a7426ff0efb975861f6bd467214d0b7721a1359b3f93fea9f7b819d440dd",
        "3786546b04414abfcb4ae4c58779cb207b34dc58b0e4d3389b8a5f7ed815d385",
    ),
    "0008": (
        "1cc1f48177c4b02f7d700e9312878ceb9969a9d14fef4f1ed3432c8672737cf2",
        "9618ed2e9df11a0de7b8a281446ca4b5e6996e8add81cbdc3169691b0b77e559",
    ),
    "0009": (
        "03224fdf3b50e2af832bf30cfad4547d89b301790b2713ec377fd7909f3ce96f",
        "3a71bed2924176669dc8fff32c823f0c2cfda0b52314e94907f7a2dea12664c9",
    ),
    "0010": (
        "7a9952b9848a03249487b35e5cdc665e5d5ffc82a5d782c890f9f7a7b9df223c",
        "349563dbbc81dc587260dc7ba5a54b8b20dc66873a3ab78d4d31c927ef78f8cb",
    ),
    "0011": (
        "276897a1e030c848332b3869b662d4b299ec222ab1b567d47f52308639eb255d",
        "af061ead5c60ff5a5f9713dbd944f885d39e247a0e9d29649077fecc6863b036",
    ),
    "0012": (
        "8306fa5ac27f25ac894f194767ad41e5aee343a688ad0a25a1910c886c7864b8",
        "b550812eb978964ad833414d90b3ca74b73837d245a0a9876ce1d3d7e8ef8c2b",
    ),
    "0013": (
        "ce2d37724b6685443e0dea58fa7acf4e4a7b011a7c5aabfcc5a85d7bac4f909e",
        "9f1dae47f1c9292b7190c409293d57783cf6311a2cb28a21c6139f557579a934",
    ),
    "0014": (
        "80adbbb487f6a7aa394d1ff3761afdde5bd3a73b38ea63a24d774ca799415f69",
        "825c79581ac0aa5117e210ac41b0f51b9fae0d480297becc98fed5cb27982638",
    ),
    "0015": (
        "19e1e82b10c322eb92fa92b90f9b83e3c8f2f77cbb23a11a6b52d2ac174973d2",
        "3481c7e345fb6ba68df979ebe088e33cb939833c3bfcd1f7aa1350a484c0aa5c",
    ),
    "0016": (
        "56a2b2917c010ec894418494f06cde8a86ef26c62100096f712cbe0df6d64fd8",
        "33d519c6b939ecd3afdfa182ce6a9616d6b30792f0b0340d43033ab83bba1db0",
    ),
    "0017": (
        "72a1627472d2898737061241d8c39826c636076d87a064ad024989d61c682676",
        "259f4a38b06954fbb71f28a1eb170c1e4e11c4f94b11db981073f88caa5408f3",
    ),
    "0018": (
        "1c70a0f6a4d74c4bc0e0bfc0f4dcf3fdbe0f9837153d6f014150604bfdf62ca9",
        "916a40a25c768fc12ee09935c22d95f90d7ed59fbe013e746aaabb37e22f3db0",
    ),
    "0019": (
        "cdbd9938db7340ba69ecfbc703141c250eef9d79c250c0684308b142964bb7db",
        "96ea7a1f9b8cffaca298cb649acb19a5c50d97b233660e9097c10b2939a85559",
    ),
    "0020": (
        "d53af04a9759284a52529e5b11e13a1a16039ee5223fb4779c6d203f6fca8b07",
        "71bc0723de8eb486e8ed3ab007ce1741594437dc54ac2fd969d76a10f602c55c",
    ),
    "0021": (
        "f00a62a5c4a457670d249c9ec0904e9e5f8a03c8b70f7e381d293a6c6c4020ef",
        "549a9712246148a693f98f6b6c43fc32c8ebbcdbb2cc820fd67171a478db1b28",
    ),
}

# Punctuation a space next to means nothing: "a(b, c)" and "a ( b,c )" run the same.
_TIGHT = frozenset("(),;")
_QUOTES = {"'": "'", '"': '"', "`": "`", "[": "]"}


def statements(sql: str) -> str:
    """The SQL a migration runs, with everything that does not change it removed.

    Comments (``--`` to the end of the line, ``/* … */``) go, every run of
    whitespace becomes one space, and a space beside ``( ) , ;`` goes. String
    literals and quoted names are kept byte for byte, since a space inside
    ``'…'`` is data. Case is kept too: SQLite keywords are case-insensitive, but
    telling a keyword from a name here would be a parser, and nobody re-cases a
    shipped file.
    """
    out: list[str] = []
    space = False
    i, n = 0, len(sql)

    def emit(text: str) -> None:
        nonlocal space
        if space and out and out[-1][-1] not in _TIGHT and text[0] not in _TIGHT:
            out.append(" ")
        space = False
        out.append(text)

    while i < n:
        char = sql[i]
        if char in _QUOTES:
            close = _QUOTES[char]
            end = i + 1
            while end < n:
                if sql[end] == close:
                    # A doubled quote is an escaped one ('it''s'); a bracket has no escape.
                    if close != "]" and end + 1 < n and sql[end + 1] == close:
                        end += 2
                        continue
                    break
                end += 1
            emit(sql[i : end + 1])
            i = end + 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end
            space = True
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            space = True
        elif char.isspace():
            space = True
            i += 1
        else:
            emit(char)
            i += 1
    return "".join(out)


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


def _legacy_bytes(migration: Migration) -> str | None:
    """The byte checksum a pre-statement ledger holds for this file, if it runs the same SQL."""
    frozen = _BYTE_CHECKSUMS.get(migration.version)
    if frozen is None or frozen[1] != migration.checksum:
        return None
    return frozen[0]


async def _applied(db: Database) -> dict[str, str]:
    rows = await db.fetch_all("SELECT version, checksum FROM schema_migrations")
    return {str(row["version"]): str(row["checksum"]) for row in rows}


async def run_migrations(db: Database, directory: Path | None = None) -> list[str]:
    """Apply every pending migration. Returns the versions applied this run."""
    await db.execute(_LEDGER_DDL)

    migrations = load_migrations(directory)
    applied = await _applied(db)

    legacy: list[Migration] = []
    for version, checksum in applied.items():
        known = next((m for m in migrations if m.version == version), None)
        if known is None:
            raise MigrationError(
                f"migration {version} is recorded in schema_migrations but its file is missing; "
                "this database was written by a newer version of gaggiclanker"
            )
        if known.checksum == checksum:
            continue
        if checksum in (known.byte_checksum, _legacy_bytes(known)):
            legacy.append(known)
            continue
        raise MigrationError(
            f"migration {version}_{known.name} changed after it was applied "
            f"(recorded {checksum[:12]}, file {known.checksum[:12]}). "
            "Migrations are immutable once shipped: add a new one instead."
        )

    if legacy:
        # Only after every row checked out: a refused boot rewrites nothing.
        async with db.transaction():
            for migration in legacy:
                await db.execute(
                    "UPDATE schema_migrations SET checksum = ? WHERE version = ?",
                    (migration.checksum, migration.version),
                )
        log.info("migration_ledger_upgraded", versions=[m.version for m in legacy])

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
