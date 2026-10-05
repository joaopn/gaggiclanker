#!/usr/bin/env python
"""Rebuild the front end's shot-fields fixture from real shots run through the pipeline.

`web/src/test/fixtures/shot-fields.json` is what the shot page's tests render:
`GET /api/shots/{id}/fields` documents, in exactly the shape the route answers.
Generated rather than hand-written, like the shot fixture beside it, so a test
of the page is a test against what the server really serves. Four documents:

* ``lever``: the constructed lever shot (built from a real fixture, see
  ``tests/lever_shot.py``) filed under a version with a 36 g target: fast flow, a
  skipped decline phase and a cup over its target, with a phase the shot never
  reached;
* ``leverNoScale`` and ``leverNoPressure``: the same shot as a machine with no
  scale and as a Standard board record it. The channels are zeroed and the flag
  cleared, as the firmware writes them, never left out: the fields come out
  absent;
* ``real``: the exported shot, in no Set, with no warning and no target.

Re-run it whenever the catalogue, the metrics or the fields route change:

    uv run python scripts/build_web_shot_fields_fixture.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gaggiclanker.db.connection import Database  # noqa: E402
from gaggiclanker.db.migrations import run_migrations  # noqa: E402
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite  # noqa: E402
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite  # noqa: E402
from gaggiclanker.db.repos.profiles import ProfilesRepository  # noqa: E402
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite  # noqa: E402
from gaggiclanker.db.repos.shots import ShotsRepository  # noqa: E402
from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog, slog_to_raw  # noqa: E402
from gaggiclanker.domain.models import Profile  # noqa: E402
from gaggiclanker.domain.slog import Slog  # noqa: E402
from gaggiclanker.shotinfo.fields import shot_fields  # noqa: E402
from gaggiclanker.sync.derive import derive_shot  # noqa: E402
from tests.lever_shot import (  # noqa: E402
    LEVER_PROFILE,
    TARGET_YIELD_G,
    lever_shot,
    without_pressure,
    without_scale,
)

EXPORT = REPO / "tests" / "fixtures" / "exports" / "shot-129.json"
TARGET = REPO / "web" / "src" / "test" / "fixtures" / "shot-fields.json"


async def _document(
    slog: Slog, *, profile: dict[str, Any] | None, filed: bool, has_pressure: bool | None = None
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "fixture.db")
        await db.connect()
        try:
            await run_migrations(db)
            derived = derive_shot(
                slog,
                slog_to_raw(slog),
                device_id="000900",
                source="import",
                profile=profile,
                has_pressure=has_pressure,
            )
            shot = await ShotsRepository(db).insert(derived.shot, derived.samples)
            if filed:
                version, _ = await ProfilesRepository(db).ensure_version(
                    Profile.model_validate(LEVER_PROFILE)
                )
                bean = await BeansRepository(db).create(
                    BeanWrite(name="Constructed", roast_level="medium", process="washed")
                )
                grinder = await GrindersRepository(db).create(
                    GrinderWrite(name="Constructed", step_unit="numbers")
                )
                sets = SetsRepository(db)
                row = await sets.create(
                    SetWrite(name="Constructed lever", bean_id=bean.id, grinder_id=grinder.id),
                    SetVersionWrite(
                        profile_version_id=version.id,
                        grind_setting="14",
                        dose_g=18.0,
                        target_yield_g=TARGET_YIELD_G,
                    ),
                )
                assert row.current_version_id is not None
                assert await sets.assign_shot(shot, row.current_version_id)
            found = await shot_fields(db, shot)
            assert found is not None
            document = json.loads(json.dumps(found.model_dump(mode="json")))
            document["shot_id"] = 1
            return document
        finally:
            await db.close()


def format_like_biome(path: Path) -> None:
    """Run the project's formatter over the file, so `npm run check` accepts it as written.

    The front end's checks include its fixtures, and Biome lays JSON out its own way
    (short arrays on a line); a plain `json.dumps` differs from it on every regeneration.
    """
    web = REPO / "web"
    try:
        subprocess.run(
            ["npx", "--no-install", "biome", "format", "--write", str(path)],
            cwd=web,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - dev tooling
        raise SystemExit(
            f"could not format {path.name} with Biome (npm ci in web/?): {exc}"
        ) from exc


async def build() -> None:
    export = shot_export_to_slog(ShotExport.model_validate(json.loads(EXPORT.read_text())))
    documents = {
        "lever": await _document(lever_shot(), profile=LEVER_PROFILE, filed=True),
        "leverNoScale": await _document(
            without_scale(lever_shot()), profile=LEVER_PROFILE, filed=True
        ),
        "leverNoPressure": await _document(
            without_pressure(lever_shot()), profile=LEVER_PROFILE, filed=True, has_pressure=False
        ),
        "real": await _document(export, profile=None, filed=False),
    }
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(documents, indent=1) + "\n", encoding="utf-8")
    format_like_biome(TARGET)
    print(f"wrote {TARGET.relative_to(REPO)}")


if __name__ == "__main__":
    asyncio.run(build())
