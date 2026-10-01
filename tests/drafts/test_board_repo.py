"""The board repository: a patch writes only what it names, and the rest is left alone."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.profile_board import (
    BoardRowPatch,
    BoardRowWrite,
    ProfileBoardRepository,
)


@pytest.fixture
async def repo(data_dir: Path) -> AsyncIterator[ProfileBoardRepository]:
    db = Database(data_dir / "board.db")
    await db.connect()
    await run_migrations(db)
    for version_id in (1, 2):
        await db.execute(
            "INSERT INTO profile_versions (id, content_hash, label, type, json) "
            "VALUES (?, ?, 'P', 'pro', '{}')",
            (version_id, f"h{version_id}"),
        )
    try:
        yield ProfileBoardRepository(db)
    finally:
        await db.close()


async def test_a_patch_writes_only_the_fields_it_names(repo: ProfileBoardRepository) -> None:
    row = await repo.insert(
        BoardRowWrite(
            label="P",
            current_version_id=1,
            device_profile_id="abc",
            device_version_id=1,
            origin="adopted",
        )
    )
    assert row.on_home_screen is True and row.pending_major is None

    await repo.update(row.id, BoardRowPatch(on_home_screen=False))
    after = await repo.get(row.id)
    assert after is not None
    assert (after.on_home_screen, after.device_profile_id, after.current_version_id) == (
        False,
        "abc",
        1,
    )

    # Naming a field as None is a write of NULL, not "leave it alone".
    await repo.update(row.id, BoardRowPatch(device_profile_id=None, pending_major=True))
    cleared = await repo.get(row.id)
    assert cleared is not None
    assert cleared.device_profile_id is None and cleared.pending_major is True
    assert cleared.device_version_id == 1


async def test_lookups_skip_deleted_rows_and_the_list_is_in_id_order(
    repo: ProfileBoardRepository,
) -> None:
    first = await repo.insert(
        BoardRowWrite(label="A", current_version_id=1, device_profile_id="a", origin="adopted")
    )
    second = await repo.insert(
        BoardRowWrite(label="B", current_version_id=2, device_profile_id="b", origin="draft")
    )
    assert [r.id for r in await repo.list_rows()] == [first.id, second.id]
    await repo.update(first.id, BoardRowPatch(deleted_at="2026-10-01T00:00:00.000Z"))
    assert await repo.find_live_by_version(1) is None
    assert await repo.find_live_by_device("a") is None
    found = await repo.find_live_by_device("b")
    assert found is not None and found.id == second.id
    assert [r.id for r in await repo.list_rows()] == [second.id]
    assert [r.id for r in await repo.list_rows(include_deleted=True)] == [first.id, second.id]


async def test_adoption_is_recorded_once(repo: ProfileBoardRepository) -> None:
    assert await repo.adoption() is None
    await repo.mark_adopted("10.0.0.5")
    await repo.mark_adopted("somewhere-else")
    adoption = await repo.adoption()
    assert adoption is not None and adoption.host == "10.0.0.5"


def test_a_row_must_say_where_it_came_from() -> None:
    with pytest.raises(ValidationError):
        BoardRowWrite.model_validate({"label": "P", "current_version_id": 1, "origin": "typed"})
