"""Shots stored by an older derivation are derived again with the profile they are linked to.

Which of a phase's two logged targets is the one the machine steered by comes
from the shot's profile, so the derivation version moved and the boot step
brings every stored shot along, joining the profile the shot is linked to now.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.sync.derive import DERIVATION_VERSION, derive_shot, rederive_shots
from tests.domain.helpers import constructed_profile
from tests.sync.profile_helpers import (
    SLOG_204,
    compliance,
    derivation_version,
    profile_version,
    unlinked_shot,
)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "gaggiclanker.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


def test_the_derivation_version_moved_with_the_profile_input() -> None:
    assert DERIVATION_VERSION == 4


async def test_a_shot_stored_at_the_previous_version_is_derived_again_with_its_profile(
    db: Database,
) -> None:
    version_id = await profile_version(db)
    shot_id = await unlinked_shot(db)
    await db.execute(
        "UPDATE shots SET profile_version_id = ?, derivation_version = 2 WHERE id = ?",
        (version_id, shot_id),
    )
    assert await compliance(db, shot_id) is None  # derived without a profile

    assert await rederive_shots(ShotsRepository(db)) == (1, 0)

    block = await compliance(db, shot_id)
    assert block is not None
    assert block["pressure_grading"] == "graded"
    assert block["flow_grading"] == "not_applicable"
    assert await derivation_version(db, shot_id) == DERIVATION_VERSION
    # And it is now the score of a shot derived with the profile in the first place.
    fresh = derive_shot(
        parse_slog(SLOG_204.read_bytes()),
        SLOG_204.read_bytes(),
        device_id="000204",
        profile=constructed_profile("shot_204"),
    ).shot
    row = await db.fetch_one(
        "SELECT execution_score, diagnostics_json FROM shots WHERE id = ?", (shot_id,)
    )
    assert row is not None
    assert row["execution_score"] == fresh.execution_score
    assert row["diagnostics_json"] == fresh.diagnostics_json


async def test_a_shot_with_no_linked_profile_is_derived_again_as_not_graded(db: Database) -> None:
    shot_id = await unlinked_shot(db)
    await db.execute("UPDATE shots SET derivation_version = 2 WHERE id = ?", (shot_id,))

    assert await rederive_shots(ShotsRepository(db)) == (1, 0)

    assert await compliance(db, shot_id) is None
    assert await derivation_version(db, shot_id) == DERIVATION_VERSION
