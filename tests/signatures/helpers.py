"""Set versions to file shots under, for tests that need a Set."""

from __future__ import annotations

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionWrite, SetWrite


async def make_set_versions(
    db: Database, profile_version_id: int, *, target: float = 36.0
) -> tuple[int, int]:
    """A Set on one profile version with two versions of it (the second a grind change)."""
    bean = await BeansRepository(db).create(BeanWrite(name="Alturas", roast_level="light"))
    grinder = await GrindersRepository(db).create(GrinderWrite(name="Niche", step_unit="numbers"))
    sets = SetsRepository(db)
    row = await sets.create(
        SetWrite(name="Alturas", bean_id=bean.id, grinder_id=grinder.id),
        SetVersionWrite(
            profile_version_id=profile_version_id,
            grind_setting="14",
            dose_g=18.0,
            target_yield_g=target,
        ),
    )
    assert row.current_version_id is not None
    second = await sets.add_version(row.id, SetVersionPatch(grind_setting="13", intent="finer"))
    assert second is not None
    return row.current_version_id, second.id
