"""``gaggiclanker import <paths…>`` — the importer without a browser.

The same :class:`~gaggiclanker.imports.service.ImportService` the API uses,
against the same database file, so a maintainer can seed a box from a folder of
exports before the front end is built — and so the shots UI can fill a development
database with one command:

    uv run gaggiclanker import tests/fixtures/exports

It opens the database and creates it when it is new itself rather than talking to a
running server: importing a few hundred files through HTTP means holding them
all in one request body, and the point of the command line is that the files are
already on this disk.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from gaggiclanker.db.connection import Database
from gaggiclanker.db.schema import SchemaMismatch, check_database_file, ensure_schema
from gaggiclanker.settings import EnvSettings

if TYPE_CHECKING:
    from gaggiclanker.imports.service import ImportFile, ImportResult, ImportSummary

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
    from gaggiclanker.imports.service import ImportResult

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
    replace: bool = False,
    env: EnvSettings | None = None,
) -> ImportSummary:
    """Import every file under ``paths`` into the configured database."""
    # Imported here rather than at module scope, and this is the one reason:
    # ``gaggiclanker/__main__.py`` registers every subcommand's arguments to
    # build its parser, so whatever this module imports is loaded by
    # ``gaggiclanker mcp`` too — and the import service reaches the device
    # client, the sync engine and the outbound HTTP layer. The chat spawns the
    # MCP server once per turn; it has no business loading a machine client.
    from gaggiclanker.imports.service import ImportFile, ImportResult, ImportService, ImportSummary

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

    # A database made by a different version is refused before anything opens it.
    check_database_file(env.database_path)
    db = Database(env.database_path)
    await db.connect()
    try:
        await ensure_schema(db)
        service = ImportService(db)
        summary = await service.import_files(payloads, replace=replace)
    finally:
        await db.close()

    # The unreadable paths are results too, and they belong in the same list and
    # the same counts as everything else.
    return ImportSummary.of([*problems, *summary.items])


def import_command(args: argparse.Namespace) -> int:
    """Run an import and print one line per file. Returns the exit status."""
    # Imported here: see run_import for why this module keeps its imports light.
    from gaggiclanker.db.reset import RESET_WAITING_MESSAGE, reset_is_waiting

    # The database about to be deleted is not one to write to: the import would report
    # success and the next start would serve nothing.
    if reset_is_waiting(EnvSettings().data_dir):
        print(RESET_WAITING_MESSAGE, file=sys.stderr)
        return 1
    try:
        summary = asyncio.run(run_import(args.paths, replace=args.replace))
    except SchemaMismatch as exc:
        print(exc, file=sys.stderr)
        return 1
    for item in summary.items:
        subject = item.device_id or item.label or item.kind
        detail = f" — {item.message}" if item.message else ""
        print(f"{item.status:<8} {subject:<16} {item.filename}{detail}")
    print(
        f"\n{len(summary.items)} file(s): {summary.created} created, "
        f"{summary.updated} updated, {summary.skipped} skipped, {summary.failed} failed"
    )
    # Non-zero when anything failed, so a seeding script in CI notices.
    return 1 if summary.failed else 0
