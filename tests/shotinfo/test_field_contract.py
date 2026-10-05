"""The field contract: a method, a value and a unit for every item, and the fields served.

Each item answers ``{value, unit, phase, window, method}`` beside its sentence;
the value and the sentence are the same reading (the sentence starts with the
value at the precision it is shown), the method id names the computation, and a
route serves a shot's fields in the catalogue's order, grouped by phase, with the
warnings and what depends on where the shot is filed.

The shot read here is the constructed lever shot: its cup passes the target in
the ramp, it stops on its weight before the decline, and its scale flow is fast
at the top of the pressure.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.models import Profile
from gaggiclanker.shotinfo import (
    CATALOGUE,
    ITEMS,
    default_tiers,
    load_shots,
    render_shot,
)
from gaggiclanker.shotinfo.fields import shot_fields_of
from gaggiclanker.shotinfo.methods import METHODS
from gaggiclanker.sync.derive import derive_shot
from tests.lever_shot import (
    LEVER_PROFILE,
    RAMP_END_G,
    SOAK_END_G,
    TARGET_YIELD_G,
    lever_shot,
)
from tests.sets.conftest import make_shot
from tests.sets.test_api import data
from tests.shotinfo.conftest import Archive

LEADING_NUMBER = re.compile(r"^-?\d+(?:\.(\d+))?")


@pytest.fixture
async def lever(tmp_path: Path) -> AsyncIterator[tuple[Database, int, int]]:
    """The lever shot filed under a version with a 36 g target: (database, shot id, version id)."""
    db = Database(tmp_path / "lever.db")
    await db.connect()
    await run_migrations(db)
    try:
        profile, _ = await ProfilesRepository(db).ensure_version(
            Profile.model_validate(LEVER_PROFILE)
        )
        bean = await BeansRepository(db).create(
            BeanWrite(name="Constructed", roast_level="medium", process="washed")
        )
        grinder = await GrindersRepository(db).create(
            GrinderWrite(name="Constructed", step_unit="numbers")
        )
        row = await SetsRepository(db).create(
            SetWrite(name="Constructed lever", bean_id=bean.id, grinder_id=grinder.id),
            SetVersionWrite(
                profile_version_id=profile.id,
                grind_setting="14",
                dose_g=18.0,
                target_yield_g=TARGET_YIELD_G,
            ),
        )
        assert row.current_version_id is not None
        slog = lever_shot()
        derived = derive_shot(
            slog, slog_to_raw(slog), device_id="000900", source="import", profile=LEVER_PROFILE
        )
        shot = await ShotsRepository(db).insert(derived.shot, derived.samples)
        assert await SetsRepository(db).assign_shot(shot, row.current_version_id)
        yield db, shot, row.current_version_id
    finally:
        await db.close()


# ── the catalogue's side of the contract ────────────────────────────


def test_every_item_has_a_method_and_no_two_share_one() -> None:
    assert set(METHODS) == {item.key for item in CATALOGUE}
    ids = [item.method for item in CATALOGUE]
    assert ids == [METHODS[item.key] for item in CATALOGUE]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+@[1-9]\d*", method) for method in ids)


def test_every_item_is_computed_for_now() -> None:
    assert {item.source for item in CATALOGUE} == {"computed"}


def test_a_curve_channel_carries_the_unit_of_its_column() -> None:
    assert ITEMS["curve_pressure"].unit == "bar"
    assert ITEMS["curve_scale_flow"].unit == "g/s"
    assert ITEMS["curve_phase"].unit == ""


async def test_the_structured_value_is_the_number_the_sentence_starts_with(
    archive: Archive,
) -> None:
    """Held for every item that has a number, on a real shot, whole and per phase."""
    [facts] = await load_shots(archive.db, [archive.shot])
    checked = 0
    for item in CATALOGUE:
        # An item with a unit is a measurement; a code (how a phase ended) is not.
        if not item.unit or (item.shot_value is None and item.phase_value is None):
            continue
        readings = [item.field(facts)] if item.shot is not None else []
        readings += [item.field(facts, phase) for phase in facts.phases] if item.phase else []
        for found in readings:
            if found is None or not isinstance(found.value, int | float):
                continue
            match = LEADING_NUMBER.match(found.text)
            assert match, (item.key, found.text)
            decimals = len(match.group(1) or "")
            assert float(match.group(0)) == pytest.approx(found.value, abs=0.5 * 10**-decimals), (
                item.key,
                found.text,
            )
            assert found.unit == item.unit
            checked += 1
    assert checked > 50


async def test_a_field_that_is_only_a_number_is_served_as_a_number(archive: Archive) -> None:
    """A count is 29, not "29": words stay words, numbers are numbers."""
    [facts] = await load_shots(archive.db, [archive.shot])
    lone = re.compile(r"^-?\d+(?:\.\d+)?$")
    offenders: list[str] = []
    seen = 0
    for item in CATALOGUE:
        readings = [item.field(facts)] if item.shot is not None else []
        readings += [item.field(facts, phase) for phase in facts.phases] if item.phase else []
        for found in readings:
            if found is not None and lone.match(found.text):
                if not isinstance(found.value, int | float):
                    offenders.append(item.key)
                seen += 1
    # A machine number is zero-padded and a grind setting is whatever the grinder
    # calls it ("14", "2.1"): both are words that happen to be digits.
    words = {"machine_shot_number", "grind_as_brewed", "recipe_grind", "note_grind"}
    assert set(offenders) == words & set(offenders)
    assert seen > 0
    samples = ITEMS["phase_samples"].field(facts, facts.phases[2])
    assert samples is not None
    assert isinstance(samples.value, int)
    assert samples.value == int(samples.text)


async def test_a_phase_field_says_which_phase_and_the_span_it_covers(archive: Archive) -> None:
    [facts] = await load_shots(archive.db, [archive.shot])
    ramp = facts.phases[2]
    found = ITEMS["phase_cup_end"].field(facts, ramp)
    assert found is not None
    assert found.phase == {"number": 2, "name": "Ramp"}
    assert found.window == {"from_s": 17.2, "to_s": 22.2}
    assert found.method == METHODS["phase_cup_end"]
    shot_wide = ITEMS["shot_time"].field(facts)
    assert shot_wide is not None
    assert shot_wide.phase is None
    assert shot_wide.window is None


async def test_a_value_the_machine_did_not_record_is_not_a_field(archive: Archive) -> None:
    [facts] = await load_shots(archive.db, [archive.no_scale])
    for phase in facts.phases:
        assert ITEMS["phase_cup_end"].field(facts, phase) is None
        assert ITEMS["phase_scale_flow"].field(facts, phase) is None
    assert ITEMS["yield_share"].field(facts) is None
    assert ITEMS["yield"].field(facts) is None


# ── the lever shot, whole ───────────────────────────────────────────


async def test_the_base_rendering_leads_with_the_warnings_in_order(
    lever: tuple[Database, int, int],
) -> None:
    db, shot, _ = lever
    [facts] = await load_shots(db, [shot])
    lines = render_shot(facts, "base", default_tiers(), curve_points=60).splitlines()

    assert lines[0] == f"shot {shot}"
    assert lines[1] == "[Warnings]"
    assert [line.split(" (amber)")[0] for line in lines[2:5]] == [
        "ramp: fast flow",
        "decline: skipped",
        "Shot: over target",
    ]
    assert lines[5] == "[Identity and status]"


async def test_each_phases_cup_share_of_the_target_is_the_constructed_one(
    lever: tuple[Database, int, int],
) -> None:
    db, shot, _ = lever
    [facts] = await load_shots(db, [shot])
    document = shot_fields_of(facts)

    shares = {
        phase.number: next(f.value for f in phase.fields if f.key == "phase_cup_share")
        for phase in document.phases
    }
    assert shares == {
        0: 0.0,
        1: pytest.approx(SOAK_END_G / TARGET_YIELD_G * 100, abs=0.05),
        2: pytest.approx(RAMP_END_G / TARGET_YIELD_G * 100, abs=0.05),
    }
    assert document.yield_share_pct == pytest.approx(117.2, abs=0.05)
    assert document.target_yield_g == TARGET_YIELD_G
    assert document.badge == "ramp: fast flow +2"
    assert [w.fault for w in document.warnings] == ["fast flow", "skipped", "over target"]
    ended = {
        phase.number: next(f.text for f in phase.fields if f.key == "phase_ended_by")
        for phase in document.phases
    }
    assert ended == {0: "Duration", 1: "Duration", 2: "Volumetric target"}


async def test_the_fields_are_in_catalogue_order_and_group_the_phases_apart(
    lever: tuple[Database, int, int],
) -> None:
    db, shot, _ = lever
    [facts] = await load_shots(db, [shot])
    document = shot_fields_of(facts)

    order = {item.key: i for i, item in enumerate(CATALOGUE)}
    keys = [f.key for f in document.shot]
    assert keys == sorted(keys, key=order.__getitem__)
    assert "yield_share" in keys
    assert "phases_not_reached" in keys
    assert not any(ITEMS[key].kind == "phase" for key in keys)
    for phase in document.phases:
        phase_keys = [f.key for f in phase.fields]
        assert phase_keys == sorted(phase_keys, key=order.__getitem__)
        assert all(ITEMS[key].kind == "phase" for key in phase_keys)
        assert all(f.phase is not None and f.phase.number == phase.number for f in phase.fields)
    assert [p.name for p in document.phases] == ["preinfusion", "soak", "ramp"]


async def test_the_fields_route_serves_the_document_and_follows_the_filing(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    db: Database = app.state.db
    profile, _ = await ProfilesRepository(db).ensure_version(Profile.model_validate(LEVER_PROFILE))
    bean = await BeansRepository(db).create(
        BeanWrite(name="Constructed", roast_level="medium", process="washed")
    )
    grinder = await GrindersRepository(db).create(
        GrinderWrite(name="Constructed", step_unit="numbers")
    )
    sets = SetsRepository(db)
    row = await sets.create(
        SetWrite(name="Constructed lever", bean_id=bean.id, grinder_id=grinder.id),
        SetVersionWrite(profile_version_id=profile.id, dose_g=18.0, target_yield_g=TARGET_YIELD_G),
    )
    slog = lever_shot()
    derived = derive_shot(
        slog, slog_to_raw(slog), device_id="000900", source="import", profile=LEVER_PROFILE
    )
    shot = await ShotsRepository(db).insert(derived.shot, derived.samples)
    assert row.current_version_id is not None
    assert await sets.assign_shot(shot, row.current_version_id)

    document: dict[str, Any] = data(await client.get(f"/api/shots/{shot}/fields"))
    assert document["shot_id"] == shot
    assert document["badge"] == "ramp: fast flow +2"
    assert [w["fault"] for w in document["warnings"]] == ["fast flow", "skipped", "over target"]
    assert document["target_yield_g"] == TARGET_YIELD_G
    assert document["yield_share_pct"] == pytest.approx(117.2, abs=0.05)
    cup = next(f for f in document["phases"][2]["fields"] if f["key"] == "phase_cup_end")
    assert cup["value"] == pytest.approx(RAMP_END_G)
    assert cup["unit"] == "g"
    assert cup["method"] == METHODS["phase_cup_end"]
    assert cup["source"] == "computed"
    assert cup["phase"] == {"number": 2, "name": "ramp"}

    # Refiled under a version with a bigger target, the same shot reads another way.
    other = await sets.add_version(
        row.id, SetVersionPatch(target_yield_g=60.0, intent="a longer shot")
    )
    assert other is not None
    assert await sets.assign_shot(shot, other.id)
    refiled: dict[str, Any] = data(await client.get(f"/api/shots/{shot}/fields"))
    assert [w["fault"] for w in refiled["warnings"]] == ["fast flow", "skipped", "under target"]
    assert refiled["yield_share_pct"] == pytest.approx(70.3, abs=0.05)


async def test_the_fields_route_has_no_shot_to_serve_for_an_unknown_id(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/shots/9999/fields")
    assert response.status_code == 404


async def test_a_shot_with_nothing_derived_has_no_phases_and_no_warnings(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    shot = await make_shot(app.state.db, "000777")
    document: dict[str, Any] = data(await client.get(f"/api/shots/{shot}/fields"))
    assert document["phases"] == []
    assert document["warnings"] == []
    assert document["badge"] is None
    assert document["target_yield_g"] is None
