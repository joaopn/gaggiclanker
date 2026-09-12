"""A real database and an import service over it.

Same rule as the rest of the suite: a real SQLite file, real migrations, no
mocked repositories. The importer's whole job is writing rows that the sync
engine's own constraints accept — `UNIQUE (machine_id, device_id)`, the
`source` CHECK, the foreign key to `machines` — and none of those exist in a
mock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.imports.service import ImportService


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = Database(data_dir / "gaggiclanker.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def service(db: Database) -> ImportService:
    """The importer with no settings service, i.e. no machine configured."""
    return ImportService(db)
