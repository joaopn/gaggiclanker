"""Real shots, derived from real `.slog` files, filed in a Set and judged.

The shot is `shot_204_ramping_flow.slog` run through the ingest path
(`derive_shot`), so its diagnostics are the full block the archive actually
stores, with every sub-block and per-phase metric a catalogue item reads. Two
variants of the same bytes cover the machines that record less: one with no
scale (the weight channels zeroed and the scale flag cleared, as a machine
with no scale writes them) and one with no pressure sensor (derived with
``has_pressure=False``, which is what a Standard board is).

Deriving rather than hand-writing the diagnostics means the golden files move
if the vendored engine's output does — which is the point of them here: they
are what the model is told about a shot.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog, slog_to_raw
from gaggiclanker.domain.models import ShotNotes
from gaggiclanker.domain.slog import Slog, parse_slog
from gaggiclanker.sync.derive import SI_SCALE_CONNECTED, derive_shot
from tests.sets.conftest import make_profile_version

SLOG = Path(__file__).resolve().parents[1] / "fixtures" / "slog" / "shot_204_ramping_flow.slog"
#: The profiles these two shots are derived with: constructed to match them (the
#: real ones no longer exist; see the README beside the files), each phase
#: steering by pressure. The profile is what says which target a phase steered
#: by, so it is an input of the derived adherence the examples show.
CONSTRUCTED = Path(__file__).resolve().parents[1] / "fixtures" / "constructed_profiles"
PROFILE_204 = json.loads((CONSTRUCTED / "shot_204_pressure-first.json").read_text())
PROFILE_129 = json.loads((CONSTRUCTED / "shot_129_pressure-first.json").read_text())

#: The newest shot of the demo archive, exported by the machine's web UI: 213
#: samples over 53 s, four phases, a real scale. What a person's own shot looks
#: like, and so what the curve's budget is measured on.
SHOT_129 = Path(__file__).resolve().parents[1] / "fixtures" / "exports" / "shot-129.json"


@dataclass(slots=True)
class Archive:
    db: Database
    set_id: int
    version_id: int
    #: The judged shot the goldens are rendered from.
    shot: int
    #: The same bytes with no scale, and with no pressure sensor.
    no_scale: int
    no_pressure: int


def _without_scale(slog: Slog) -> Slog:
    """The shot as a machine with no scale records it: zero weight, no flag."""
    samples = [
        sample.model_copy(
            update={
                "v": 0.0,
                "vf": 0.0,
                "si": (sample.si or 0) & ~SI_SCALE_CONNECTED,
            }
        )
        for sample in slog.samples
    ]
    header = slog.header.model_copy(update={"final_weight_g": None})
    return dataclasses.replace(slog, samples=samples, header=header)


async def insert_shot_129(db: Database) -> int:
    """The exported shot, stored the way an import stores it."""
    slog = shot_export_to_slog(ShotExport.model_validate(json.loads(SHOT_129.read_text())))
    derived = derive_shot(
        slog, slog_to_raw(slog), device_id="000129", source="import", profile=PROFILE_129
    )
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


async def _insert(
    db: Database, slog: Slog, device_id: str, *, has_pressure: bool | None = None
) -> int:
    derived = derive_shot(
        slog,
        SLOG.read_bytes(),
        device_id=device_id,
        has_pressure=has_pressure,
        profile=PROFILE_204,
    )
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


@pytest.fixture
async def archive(tmp_path: Path) -> AsyncIterator[Archive]:
    db = Database(tmp_path / "shotinfo.db")
    await db.connect()
    await run_migrations(db)
    try:
        bean = await BeansRepository(db).create(
            BeanWrite(name="Amigo Alturas", roast_level="light", process="washed")
        )
        grinder = await GrindersRepository(db).create(
            GrinderWrite(name="Niche Zero", step_unit="numbers")
        )
        profile = await make_profile_version(db, "Alturas bloom", temperature=93.0)
        sets = SetsRepository(db)
        row = await sets.create(
            SetWrite(name="Alturas on the Niche", bean_id=bean.id, grinder_id=grinder.id),
            SetVersionWrite(
                profile_version_id=profile,
                grind_setting="14",
                dose_g=18.0,
                target_yield_g=36.0,
            ),
        )
        assert row.current_version_id is not None
        version_id = row.current_version_id

        slog = parse_slog(SLOG.read_bytes())
        shot = await _insert(db, slog, "000204")
        no_scale = await _insert(db, _without_scale(slog), "000205")
        no_pressure = await _insert(db, slog, "000206", has_pressure=False)
        for shot_id in (shot, no_scale, no_pressure):
            assert await sets.assign_shot(shot_id, version_id)

        await JudgementsRepository(db).upsert(
            shot,
            JudgementWrite(
                rating=4,
                balance="balanced",
                taste_notes=["sweet.brown_sugar.caramelized", "fruity.citrus_fruit.lemon"],
                aroma_notes=["floral.floral.jasmine"],
                dose_in_g=18.0,
                dose_out_g=36.5,
                grind_setting="14",
                notes="Sweet, a little thin at the end.",
                decision="keep",
            ),
        )
        await NotesRepository(db).upsert(
            shot,
            ShotNotes.model_validate(
                {
                    "id": "000204",
                    "rating": 4,
                    "beanType": "Alturas",
                    "doseIn": "18",
                    "doseOut": "36.5",
                    "ratio": "2.03",
                    "grindSetting": "14",
                    "balanceTaste": "balanced",
                    "notes": "typed at the machine",
                }
            ),
        )
        yield Archive(
            db=db,
            set_id=row.id,
            version_id=version_id,
            shot=shot,
            no_scale=no_scale,
            no_pressure=no_pressure,
        )
    finally:
        await db.close()
