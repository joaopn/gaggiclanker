"""``gaggiclanker import <paths…>`` — the importer without a browser.

The same :class:`~gaggiclanker.imports.service.ImportService` the API uses,
against the same database file, so a maintainer can seed a box from a folder of
exports before the front end is built — and so the shots UI can fill a development
database with one command:

    uv run gaggiclanker import tests/fixtures/exports

It opens the database and runs migrations itself rather than talking to a
running server: importing a few hundred files through HTTP means holding them
all in one request body, and the point of the command line is that the files are
already on this disk.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable, Sequence
from pathlib import Path

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.imports.service import ImportFile, ImportResult, ImportService, ImportSummary
from gaggiclanker.settings import EnvSettings, load_dotenv_values
from gaggiclanker.settings_service import SettingsService

__all__ = ["IMPORTABLE_SUFFIXES", "add_import_parser", "collect_files", "run_import"]

#: What a directory walk picks up. A shot export and a profile export are both
#: `.json`; a zip is how people pass a folder around. Anything else in the
#: directory (a README, a `.slog`) is left alone — a `.slog` belongs to the sync
#: engine's path, not this one.
IMPORTABLE_SUFFIXES = (".json", ".zip")


def add_import_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``import`` subcommand."""
    parser = subparsers.add_parser(
        "import",
        help="load exported shots and profiles from files",
        description=(
            "Import GaggiMate web-UI exports. Paths may be files, directories "
            "(walked for .json and .zip) or zips."
        ),
    )
    parser.add_argument("paths", nargs="+", type=Path, help="files or directories to import")
    parser.add_argument(
        "--machine-id",
        type=int,
        default=None,
        help="machine these shots belong to; defaults to the configured one",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="overwrite shots already in the archive instead of skipping them",
    )


def collect_files(paths: Iterable[Path]) -> tuple[list[Path], list[ImportResult]]:
    """Expand paths into files to import, plus a result for each one that is not.

    A path that does not exist is reported rather than raised: `import *.json`
    in a shell that did not expand is a typo, not a reason to lose the rest of
    the batch.
    """
    found: list[Path] = []
    problems: list[ImportResult] = []
    for path in paths:
        if path.is_dir():
            found.extend(
                sorted(
                    child
                    for child in path.rglob("*")
                    if child.is_file() and child.suffix.lower() in IMPORTABLE_SUFFIXES
                )
            )
        elif path.is_file():
            found.append(path)
        else:
            problems.append(
                ImportResult(filename=str(path), status="failed", message="no such file")
            )
    return found, problems


async def run_import(
    paths: Sequence[Path],
    *,
    machine_id: int | None = None,
    replace: bool = False,
    env: EnvSettings | None = None,
) -> ImportSummary:
    """Import every file under ``paths`` into the configured database."""
    env = env or EnvSettings()
    env.data_dir.mkdir(parents=True, exist_ok=True)

    files, problems = collect_files(paths)
    payloads: list[ImportFile] = []
    for path in files:
        try:
            payloads.append(ImportFile(filename=str(path), data=path.read_bytes()))
        except OSError as exc:
            problems.append(
                ImportResult(filename=str(path), status="failed", message=f"unreadable: {exc}")
            )

    db = Database(env.database_path)
    await db.connect()
    try:
        await run_migrations(db)
        service = ImportService(
            db, SettingsService(SettingsRepository(db), dotenv=load_dotenv_values())
        )
        summary = await service.import_files(payloads, machine_id=machine_id, replace=replace)
    finally:
        await db.close()

    # The unreadable paths are results too, and they belong in the same list and
    # the same counts as everything else.
    return ImportSummary.of([*problems, *summary.items], summary.machine_id)


def import_command(args: argparse.Namespace) -> int:
    """Run an import and print one line per file. Returns the exit status."""
    summary = asyncio.run(run_import(args.paths, machine_id=args.machine_id, replace=args.replace))
    for item in summary.items:
        subject = item.device_id or item.label or item.kind
        detail = f" — {item.message}" if item.message else ""
        print(f"{item.status:<8} {subject:<16} {item.filename}{detail}")
    print(
        f"\n{len(summary.items)} file(s): {summary.created} created, "
        f"{summary.updated} updated, {summary.skipped} skipped, {summary.failed} failed "
        f"(machine {summary.machine_id})"
    )
    # Non-zero when anything failed, so a seeding script in CI notices.
    return 1 if summary.failed else 0
