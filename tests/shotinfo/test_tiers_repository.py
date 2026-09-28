"""The person's tier choices: overrides only, each through the write model."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite
from gaggiclanker.shotinfo.catalogue import ITEMS


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "tiers.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


async def test_a_fresh_archive_has_no_overrides(db: Database) -> None:
    assert await ShotInfoTiersRepository(db).overrides() == {}


async def test_moving_an_item_stores_it_and_moving_it_again_replaces_it(db: Database) -> None:
    repo = ShotInfoTiersRepository(db)
    assert ITEMS["flow_jitter"].default_tier == "extended"

    await repo.set_tier(ShotInfoTierWrite(item_key="flow_jitter", tier="base"))
    await repo.set_tier(ShotInfoTierWrite(item_key="rating", tier="excluded"))
    assert await repo.overrides() == {"flow_jitter": "base", "rating": "excluded"}

    await repo.set_tier(ShotInfoTierWrite(item_key="flow_jitter", tier="excluded"))
    assert await repo.overrides() == {"flow_jitter": "excluded", "rating": "excluded"}
    assert [row.item_key for row in await repo.rows()] == ["flow_jitter", "rating"]


async def test_moving_an_item_back_to_its_default_deletes_its_row(db: Database) -> None:
    repo = ShotInfoTiersRepository(db)
    await repo.set_tier(ShotInfoTierWrite(item_key="flow_jitter", tier="base"))

    await repo.set_tier(ShotInfoTierWrite(item_key="flow_jitter", tier="extended"))

    assert await db.fetch_value("SELECT COUNT(*) FROM shot_info_tiers") == 0
    # And setting an untouched item to its default stores nothing either.
    await repo.set_tier(ShotInfoTierWrite(item_key="rating", tier="base"))
    assert await repo.overrides() == {}


async def test_reset_deletes_every_override(db: Database) -> None:
    repo = ShotInfoTiersRepository(db)
    await repo.set_tier(ShotInfoTierWrite(item_key="flow_jitter", tier="base"))
    await repo.set_tier(ShotInfoTierWrite(item_key="processing_note", tier="extended"))

    assert await repo.reset() == 2
    assert await repo.overrides() == {}
    assert await repo.reset() == 0


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"item_key": "no_such_item", "tier": "base"}, "item_key"),
        ({"item_key": "shot_id", "tier": "extended"}, "item_key"),
        ({"item_key": "counted", "tier": "base"}, "item_key"),
        ({"item_key": "rating", "tier": "full"}, "tier"),
        ({"item_key": "rating", "tier": "Base"}, "tier"),
        ({"item_key": "rating"}, "tier"),
        ({"item_key": "rating", "tier": "base", "extra": 1}, "extra"),
    ],
)
def test_the_write_model_refuses_what_the_table_must_never_hold(
    payload: dict[str, object], field: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        ShotInfoTierWrite.model_validate(payload)
    assert [error["loc"][0] for error in caught.value.errors()] == [field]


async def test_the_table_refuses_a_tier_that_is_not_one(db: Database) -> None:
    """The CHECK behind the model, for anything that writes SQL by hand."""
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute("INSERT INTO shot_info_tiers (item_key, tier) VALUES ('rating', 'full')")


async def test_a_row_for_an_item_the_catalogue_no_longer_has_still_reads(db: Database) -> None:
    """Removed from a later release: read, so that the reader can ignore it."""
    await db.execute("INSERT INTO shot_info_tiers (item_key, tier) VALUES ('retired_item', 'base')")

    assert await ShotInfoTiersRepository(db).overrides() == {"retired_item": "base"}
