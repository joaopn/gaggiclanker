#!/usr/bin/env python
"""Reproduce: a comment edited in a shipped migration refuses to boot.

    uv run python scripts/repro_cosmetic_migration_edit.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The migration ledger recorded the sha256 of each file's bytes, so any change to
a shipped file — a reworded comment, a re-indented line — made every existing
database refuse to start with "changed after it was applied". The only way past
that was deleting the database, for a change that did nothing to the schema.

This script applies the shipped migrations to a fresh database, then edits a
copy of them the ways that do not change what runs (a comment added, a comment
reworded, whitespace re-flowed) and boots again. It also checks the other side
of the line: an edit that does change what runs is still refused, and a
database whose ledger rows were written by the old byte checksum still boots.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, MigrationError, run_migrations


async def boot(path: Path, directory: Path) -> str | None:
    """Run the migrations; the refusal message, or None when it booted."""
    db = Database(path)
    await db.connect()
    try:
        await run_migrations(db, directory)
        return None
    except MigrationError as exc:
        return str(exc)
    finally:
        await db.close()


async def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        directory = root / "migrations"
        shutil.copytree(MIGRATIONS_DIR, directory)
        database = root / "archive.db"
        if (refusal := await boot(database, directory)) is not None:
            print(f"fresh database refused: {refusal}")
            return 1

        target = directory / "0005_sets.sql"
        original = target.read_text(encoding="utf-8")

        cosmetic = {
            "a comment added": "-- A note added after the file shipped.\n" + original,
            "a comment reworded": re.sub(r"--[^\n]*", "-- reworded", original, count=1),
            "whitespace re-flowed": re.sub(r"\n\s*\n", "\n\n\n", original).replace("  ", "    "),
        }
        for label, text in cosmetic.items():
            target.write_text(text, encoding="utf-8")
            refusal = await boot(database, directory)
            print(f"{label}: {'refused: ' + refusal if refusal else 'boots'}")
            if refusal:
                failures.append(label)

        target.write_text(original + "\nCREATE TABLE repro_extra (id INTEGER);\n", encoding="utf-8")
        refusal = await boot(database, directory)
        print(f"a statement added: {'refused' if refusal else 'BOOTS (must be refused)'}")
        if refusal is None:
            failures.append("a real edit was accepted")
        target.write_text(original, encoding="utf-8")

        # A database from before the fix carries byte checksums in its ledger.
        legacy = root / "legacy.db"
        if await boot(legacy, directory) is not None:
            return 1
        db = Database(legacy)
        await db.connect()
        for path in sorted(directory.glob("*.sql")):
            raw = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            await db.execute(
                "UPDATE schema_migrations SET checksum = ? WHERE version = ?", (raw, path.name[:4])
            )
        await db.close()
        target.write_text("-- Added after the upgrade.\n" + original, encoding="utf-8")
        first = await boot(legacy, directory)
        second = await boot(legacy, directory)
        print(
            f"a byte-checksum ledger, then a comment edit: {first or 'boots'} / {second or 'boots'}"
        )
        if first or second:
            failures.append("byte-checksum ledger")

    if failures:
        print("BUG PRESENT:", ", ".join(failures))
        return 1
    print("fixed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
