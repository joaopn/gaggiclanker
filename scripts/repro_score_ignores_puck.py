#!/usr/bin/env python
"""Reproduce: a shot whose cup passed its target before the decline is served with no warning.

    uv run python scripts/repro_score_ignores_puck.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The execution score only asked whether the machine followed its profile. A lever
profile whose cup reached 117 % of the version's target yield during the ramp,
that stopped on its weight before the decline phase ever began, and whose scale
flow ran at 4 g/s at the top of the pressure, scored 8.9 out of 10 (its only
penalty a channeling guess) and carried no sign of any of it: the base
information a chat reads said nothing about the yield, the phase that never ran
or the flow.

The check builds that shot out of a real fixture (`tests/lever_shot.py`: the
weight, the scale flow, the water counter and the end of the shot are rewritten,
nothing is taken from a person's shot), files it under a Set version whose
target yield is 36 g, and reads it the way a chat does (the base rendering of
the shot renderer). It requires the three things that are plainly true of it to
be said: it is over target, its ramp ran fast, and the decline was skipped.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path

import gaggiclanker
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.models import Profile
from gaggiclanker.shotinfo import default_tiers, load_shots, render_shot
from gaggiclanker.sync.derive import derive_shot

ROOT = Path(__file__).resolve().parents[1]
WANTED = ("over target", "fast flow", "skipped")


def _lever_shot_module():
    # By path, not by `import tests...`: putting the repository on sys.path would
    # make this script import the working tree's package whatever tree it was
    # asked to check.
    spec = importlib.util.spec_from_file_location("lever_shot", ROOT / "tests" / "lever_shot.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["lever_shot"] = module
    spec.loader.exec_module(module)
    return module


async def read_base_rendering() -> str:
    lever = _lever_shot_module()
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "repro.db")
        await db.connect()
        try:
            await run_migrations(db)
            version, _ = await ProfilesRepository(db).ensure_version(
                Profile.model_validate(lever.LEVER_PROFILE)
            )
            bean = await BeansRepository(db).create(
                BeanWrite(name="Constructed bean", roast_level="medium", process="washed")
            )
            grinder = await GrindersRepository(db).create(
                GrinderWrite(name="Constructed grinder", step_unit="numbers")
            )
            row = await SetsRepository(db).create(
                SetWrite(name="Constructed lever", bean_id=bean.id, grinder_id=grinder.id),
                SetVersionWrite(
                    profile_version_id=version.id,
                    grind_setting="14",
                    dose_g=18.0,
                    target_yield_g=lever.TARGET_YIELD_G,
                ),
            )
            slog = lever.lever_shot()
            raw = slog_to_raw(slog)
            derived = derive_shot(
                slog, raw, device_id="000900", source="import", profile=lever.LEVER_PROFILE
            )
            shot_id = await ShotsRepository(db).insert(derived.shot, derived.samples)
            assert row.current_version_id is not None
            assert await SetsRepository(db).assign_shot(shot_id, row.current_version_id)
            facts = (await load_shots(db, [shot_id]))[0]
            return render_shot(facts, "base", default_tiers(), curve_points=60)
        finally:
            await db.close()


def main() -> int:
    print("gaggiclanker imported from", gaggiclanker.__file__)
    text = asyncio.run(read_base_rendering())
    print(text)
    missing = [word for word in WANTED if word not in text.lower()]
    print("FAIL: not said about the shot:" if missing else "ok", ", ".join(missing))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
