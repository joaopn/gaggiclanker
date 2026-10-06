"""Set versions to file shots under, for tests that need a Set."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.diagnostics import as_sample_dicts
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.metric_language import ShotData
from gaggiclanker.domain.phase_names import raw_phase_names
from gaggiclanker.domain.slog import Slog
from gaggiclanker.domain.warnings import ShotWarning, shot_warnings
from gaggiclanker.sync.derive import derive_shot
from tests.lever_shot import LEVER_PROFILE, TARGET_YIELD_G, lever_shot


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


# ── the constructed lever shot, as the language and the checks read it ──────────


def shot_data(
    slog: Slog | None = None,
    *,
    profile: dict[str, Any] | None = None,
    target: float | None = TARGET_YIELD_G,
    dose: float | None = 18.0,
    has_pressure: bool = True,
    scale: bool = True,
) -> ShotData:
    """What the language reads of a shot: the lever shot unless another is given."""
    slog = slog or lever_shot()
    final = slog.header.final_weight_g if scale else None
    return ShotData.build(
        as_sample_dicts(slog),
        slog.transitions,
        profile_phases=raw_phase_names(profile or LEVER_PROFILE),
        has_pressure=has_pressure,
        scale_connected=scale,
        final_weight_g=final if final and final > 0 else None,
        target_yield_g=target,
        dose_g=dose,
    )


def derived_lever(
    slog: Slog | None = None, *, device_id: str = "000900", profile: dict[str, Any] | None = None
) -> Any:
    """The shot as ingest stores it (its phases, diagnostics and bytes)."""
    slog = slog or lever_shot()
    return derive_shot(
        slog,
        slog_to_raw(slog),
        device_id=device_id,
        source="import",
        profile=profile or LEVER_PROFILE,
    )


def stored_phases(derived: Any) -> list[dict[str, Any]]:
    return json.loads(derived.shot.phases_json) if derived.shot.phases_json else []


def universal_warnings(
    derived: Any, *, target: float | None = TARGET_YIELD_G
) -> Sequence[ShotWarning]:
    """The universal warnings of a derived shot, as the read path works them out."""
    metrics = json.loads(derived.shot.diagnostics_json).get("metrics", {})
    return shot_warnings(
        final_weight_g=derived.shot.final_weight_g,
        scale_connected=derived.shot.scale_connected,
        final_exit_reason=derived.shot.final_exit_reason or 0,
        duration_s=derived.shot.duration_ms / 1000,
        target_yield_g=target,
        phases=stored_phases(derived),
        metrics=metrics,
    )


def turbo_profile() -> dict[str, Any]:
    """A turbo-like profile: three phases, the last one fast on purpose."""
    profile: dict[str, Any] = json.loads(json.dumps(LEVER_PROFILE))
    profile["id"] = "constructed-turbo"
    profile["label"] = "Constructed turbo"
    profile["phases"] = [dict(p) for p in profile["phases"][:3]]
    for phase, name in zip(profile["phases"], ("fill", "hold", "main"), strict=True):
        phase["name"] = name
    profile["phases"][2]["targets"] = [{"type": "volumetric", "operator": "gte", "value": 42.0}]
    return profile


def turbo_shot() -> Slog:
    """The constructed lever shot with its phases named as the turbo profile names them."""
    slog = lever_shot()
    names = {"preinfusion": "fill", "soak": "hold", "ramp": "main"}
    transitions = [
        t.model_copy(update={"phase_name": names[t.phase_name]}) for t in slog.transitions
    ]
    samples = [
        s.model_copy(update={"phase_name": names.get(s.phase_name or "", s.phase_name)})
        for s in slog.samples
    ]
    header = slog.header.model_copy(
        update={"transitions": transitions, "profile_id": "constructed-turbo"}
    )
    return dataclasses.replace(slog, header=header, samples=samples)


#: A profile whose phase names are longer than the 24 bytes the firmware logs of them.
LONG_NAME_SETS = (
    ("Pre-infusion with a long soak", "Ramp up to the first pressure", "Final push to the cup"),
    # The firmware cuts raw bytes: a double space and a leading space count in the 24.
    (
        "Pre  infusion with a long soak",
        " Ramp up to the full nine bar",
        "Final  push to the cup, long",
    ),
)
LONG_NAMES = LONG_NAME_SETS[0]


def long_name_profile(names: tuple[str, ...] = LONG_NAMES) -> dict[str, Any]:
    profile: dict[str, Any] = json.loads(json.dumps(LEVER_PROFILE))
    profile["id"] = "constructed-long-names"
    profile["label"] = "Constructed long names"
    profile["phases"] = [dict(p) for p in profile["phases"][:3]]
    for phase, name in zip(profile["phases"], names, strict=True):
        phase["name"] = name
    profile["phases"][2]["targets"] = [{"type": "volumetric", "operator": "gte", "value": 42.0}]
    return profile


def logged(name: str) -> str:
    """What the firmware logs of a phase name: 24 bytes, never half a character."""
    return name.encode("utf-8")[:24].decode("utf-8", errors="ignore")


def long_name_shot(names_in: tuple[str, ...] = LONG_NAMES) -> Slog:
    """The lever shot with its phases named as the long-name profile names them, logged cut."""
    slog = lever_shot()
    names = dict(zip(("preinfusion", "soak", "ramp"), names_in, strict=True))
    transitions = [
        t.model_copy(update={"phase_name": logged(names[t.phase_name])}) for t in slog.transitions
    ]
    samples = [
        s.model_copy(
            update={"phase_name": logged(names.get(s.phase_name or "", s.phase_name or ""))}
        )
        for s in slog.samples
    ]
    header = slog.header.model_copy(update={"transitions": transitions})
    return dataclasses.replace(slog, header=header, samples=samples)


async def add_shot(
    db: Database,
    *,
    set_version_id: int | None,
    profile_version_id: int,
    slog: Slog | None = None,
    profile: dict[str, Any] | None = None,
    device_id: str = "000900",
) -> int:
    """A constructed shot, stored the way ingest stores it, filed and linked to its profile."""
    derived = derived_lever(slog, device_id=device_id, profile=profile)
    shot = await ShotsRepository(db).insert(derived.shot, derived.samples)
    await db.execute(
        "UPDATE shots SET profile_version_id = ? WHERE id = ?", (profile_version_id, shot)
    )
    if set_version_id is not None:
        assert await SetsRepository(db).assign_shot(shot, set_version_id)
    return shot
