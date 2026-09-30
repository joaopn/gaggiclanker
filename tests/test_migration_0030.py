"""0030 strips the firmware's credentials from a machine row stored before the fix."""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations


@pytest.fixture
async def db(data_dir: Path) -> AsyncIterator[Database]:
    database = Database(data_dir / "test.db")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


async def _migrate(db: Database, tmp_path: Path, *, below: str | None) -> None:
    directory = tmp_path / f"upto-{below}"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if below is None or path.name < below:
            shutil.copy(path, directory / path.name)
    await run_migrations(db, directory)


async def test_the_three_keys_go_from_both_columns_and_the_rest_stays(
    db: Database, tmp_path: Path
) -> None:
    await _migrate(db, tmp_path, below="0030")
    settings = {
        "pid": "1,2,3",
        "temperatureOffset": 2.5,
        "wifiPassword": "wifi-secret",
        "apPassword": "ap-secret",
        "haPassword": "ha-secret",
    }
    identity = {"hardware": "Pro", "haPassword": "ha-secret-2"}
    await db.execute(
        "UPDATE machines SET settings_json = ?, identity_json = ?",
        (json.dumps(settings), json.dumps(identity)),
    )

    await _migrate(db, tmp_path, below=None)

    row = await db.fetch_one("SELECT settings_json, identity_json FROM machines")
    assert row is not None
    assert json.loads(row["settings_json"]) == {"pid": "1,2,3", "temperatureOffset": 2.5}
    assert json.loads(row["identity_json"]) == {"hardware": "Pro"}
    assert "secret" not in row["settings_json"] + row["identity_json"]


async def test_a_row_with_no_documents_is_left_alone(db: Database, tmp_path: Path) -> None:
    await _migrate(db, tmp_path, below=None)

    row = await db.fetch_one("SELECT settings_json, identity_json FROM machines")
    assert row is not None
    assert row["settings_json"] is None and row["identity_json"] is None


async def test_an_invalid_document_is_left_untouched_and_boot_succeeds(
    db: Database, tmp_path: Path
) -> None:
    await _migrate(db, tmp_path, below="0030")
    await db.execute(
        "UPDATE machines SET settings_json = ?, identity_json = NULL", ("{not json wifiPassword",)
    )

    await _migrate(db, tmp_path, below=None)

    row = await db.fetch_one("SELECT settings_json, identity_json FROM machines")
    assert row is not None
    assert row["settings_json"] == "{not json wifiPassword"
    assert row["identity_json"] is None
