"""A database with the shipped schema, and profile versions to hang signatures on."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.schema import create_schema


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "signatures.db")
    await database.connect()
    await create_schema(database)
    try:
        yield database
    finally:
        await database.close()
