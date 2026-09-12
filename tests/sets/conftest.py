"""A real database, and a Set already wired to a machine, a bean and a grinder.

Same rule as the rest of the suite: a real SQLite file, real migrations, no
mocked repositories. Half of what these tests assert is a constraint —
``UNIQUE(set_id, version_no)``, the partial "one active Set per machine" index,
the CHECK on every vocabulary column — and none of those exist in a mock.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.judgements import JudgementsRepository
from gaggiclanker.db.repos.machines import MachinesRepository, MachineUpsert
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.domain.models import Profile


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


@dataclass(slots=True)
class Fixtures:
    """The ids a Set needs to exist, plus the repositories under test."""

    db: Database
    machine_id: int
    bean_id: int
    grinder_id: int
    sets: SetsRepository
    shots: ShotsRepository
    judgements: JudgementsRepository


@pytest.fixture
async def wired(db: Database) -> Fixtures:
    machine = await MachinesRepository(db).upsert(MachineUpsert(host="kitchen.local"))
    bean = await BeansRepository(db).create(
        BeanWrite(name="Ethiopia Guji", roaster="Hasbean", roast_level="light", process="natural")
    )
    grinder = await GrindersRepository(db).create(
        GrinderWrite(name="Niche Zero", burr_type="conical", step_unit="numbers")
    )
    return Fixtures(
        db=db,
        machine_id=machine.id,
        bean_id=bean.id,
        grinder_id=grinder.id,
        sets=SetsRepository(db),
        shots=ShotsRepository(db),
        judgements=JudgementsRepository(db),
    )


async def make_profile_version(db: Database, label: str) -> int:
    """A stored profile version with this label, through the real repository.

    Built from a shipped fixture rather than hand-written JSON so that the row
    is one the parser and the content hash both accept — two versions here
    differ by their label, which is enough to make them two versions
    (`ProfilesRepository.ensure_version` explains why a rename counts).
    """
    document = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures" / "profiles" / "docs-medium-18g.json"
        ).read_text()
    )
    document["label"] = label
    profile = Profile.model_validate(document)
    version, _ = await ProfilesRepository(db).ensure_version(profile)
    return version.id


async def make_shot(
    db: Database,
    machine_id: int,
    device_id: str,
    *,
    profile_version_id: int | None = None,
    profile_id_on_device: str = "",
    execution_score: float | None = 8.0,
    duration_ms: int = 28_000,
    started_at: str = "2026-04-01T08:00:00.000Z",
    final_weight_g: float | None = 36.0,
) -> int:
    """One shot row, with bytes that are not a `.slog` and do not need to be.

    `raw_slog` is NOT NULL and nothing in these tests parses it: what is under
    test is the Set a shot is attached to, not the curve. Anything that needs a
    real file uses the fake device, which serves the real fixtures.
    """
    return await ShotsRepository(db).insert(
        ShotInsert(
            device_id=device_id,
            machine_id=machine_id,
            raw_slog=b"not-a-slog",
            started_at=started_at,
            duration_ms=duration_ms,
            profile_version_id=profile_version_id,
            profile_id_on_device=profile_id_on_device,
            execution_score=execution_score,
            final_weight_g=final_weight_g,
        )
    )
