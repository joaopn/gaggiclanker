"""The shots list carries each shot's Curve check and sorts by it (the Curve check column).

The checks depend on where a shot is filed, so the list works them out when it
reads, from the stored derivation and the filed version's target, and stores
nothing. Every shot here is derived from a real fixture; the one with no scale
has its weight channels zeroed and its flag cleared.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import CHECK_SORT, ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.models import Profile
from gaggiclanker.sync.derive import derive_shot
from tests.lever_shot import LEVER_PROFILE, TARGET_YIELD_G, lever_shot
from tests.sets.test_api import data
from tests.shotinfo.conftest import Archive


async def _add_lever(archive: Archive) -> int:
    """The constructed lever shot, filed under the archive's 36 g version."""
    await ProfilesRepository(archive.db).ensure_version(Profile.model_validate(LEVER_PROFILE))
    slog = lever_shot()
    derived = derive_shot(
        slog, slog_to_raw(slog), device_id="000900", source="import", profile=LEVER_PROFILE
    )
    shot = await ShotsRepository(archive.db).insert(derived.shot, derived.samples)
    assert await SetsRepository(archive.db).assign_shot(shot, archive.version_id)
    return shot


async def test_a_listed_shot_carries_its_curve_check_and_the_badge_text(archive: Archive) -> None:
    lever = await _add_lever(archive)
    page = await ShotsRepository(archive.db).list_shots(limit=10)
    rows = {row.id: row for row in page.items}

    assert rows[lever].checks.badge == "ramp: fast flow +2"
    assert [(w.phase, w.fault, w.severity) for w in rows[lever].checks.entries] == [
        ("ramp", "fast flow", "amber"),
        ("decline", "skipped", "amber"),
        ("Shot", "over target", "amber"),
    ]
    assert (
        rows[lever].checks.entries[1].detail.startswith("The shot stopped on its volumetric target")
    )
    assert rows[archive.shot].checks.badge == "Shot: under target"
    # No scale: the yield warnings need one, so there is nothing to say.
    assert rows[archive.no_scale].checks.entries == []
    # Nothing to name: no Curve check badge, and nobody has reviewed it, so the Review column is
    # the button's alone.
    assert rows[archive.no_scale].checks.badge is None
    assert rows[archive.no_scale].review.state == "unreviewed"
    assert rows[archive.no_scale].review.badge is None


async def test_the_curve_check_sort_puts_the_most_severe_first_and_the_clean_shots_last(
    archive: Archive,
) -> None:
    lever = await _add_lever(archive)
    repo = ShotsRepository(archive.db)

    worst_first = await repo.list_shots(limit=10, sort=CHECK_SORT, descending=True)
    ids = [row.id for row in worst_first.items]
    # A phase's warning before a shot-wide one, a shot with none last.
    assert ids[0] == lever
    assert ids[-1] == archive.no_scale
    # The two under-target shots start together: equal keys come newest (highest id) first.
    assert ids[1:-1] == [archive.no_pressure, archive.shot]
    assert worst_first.total == 4

    reversed_ = await repo.list_shots(limit=10, sort=CHECK_SORT, descending=False)
    assert [row.id for row in reversed_.items] == ids[::-1]


async def test_shots_with_the_same_warning_come_newest_by_start_time_not_by_id(
    archive: Archive,
) -> None:
    # The two under-target shots have the same key. The one with the older id
    # started later, so start time, not id, decides who is newest.
    assert archive.shot < archive.no_pressure
    await archive.db.execute(
        "UPDATE shots SET started_at = ? WHERE id = ?", ("2026-03-05T10:00:00.000Z", archive.shot)
    )
    await archive.db.execute(
        "UPDATE shots SET started_at = ? WHERE id = ?",
        ("2026-03-04T10:00:00.000Z", archive.no_pressure),
    )
    repo = ShotsRepository(archive.db)

    newest_first = [
        row.id for row in (await repo.list_shots(limit=10, sort=CHECK_SORT, descending=True)).items
    ]
    assert newest_first[:2] == [archive.shot, archive.no_pressure]
    oldest_first = [
        row.id for row in (await repo.list_shots(limit=10, sort=CHECK_SORT, descending=False)).items
    ]
    assert oldest_first == newest_first[::-1]


async def test_the_curve_check_sort_pages_by_offset_and_refuses_a_cursor(archive: Archive) -> None:
    lever = await _add_lever(archive)
    repo = ShotsRepository(archive.db)
    everything = [row.id for row in (await repo.list_shots(limit=10, sort=CHECK_SORT)).items]
    second = await repo.list_shots(limit=2, offset=2, sort=CHECK_SORT)
    assert [row.id for row in second.items] == everything[2:]
    assert second.total == 4
    assert second.next_cursor is None
    assert everything[0] == lever
    with pytest.raises(ValueError, match="cursor"):
        await repo.list_shots(sort=CHECK_SORT, cursor="anything")


async def test_refiling_a_shot_changes_its_listed_warnings_and_its_place_in_the_sort(
    archive: Archive,
) -> None:
    lever = await _add_lever(archive)
    sets = SetsRepository(archive.db)
    repo = ShotsRepository(archive.db)
    roomy = await sets.add_version(
        archive.set_id, SetVersionPatch(target_yield_g=60.0, intent="a longer shot")
    )
    assert roomy is not None
    # Under a 60 g target the lever's 42.2 g is 70 %: under, not over.
    assert await sets.assign_shot(lever, roomy.id)
    page = await repo.list_shots(limit=10, sort=CHECK_SORT)
    row = next(item for item in page.items if item.id == lever)
    assert [w.fault for w in row.checks.entries] == ["fast flow", "skipped", "under target"]

    assert await sets.assign_shot(lever, None)
    row = next(item for item in (await repo.list_shots(limit=10)).items if item.id == lever)
    assert [w.fault for w in row.checks.entries] == ["fast flow", "skipped"]
    assert row.checks.badge == "ramp: fast flow +1"


async def test_the_list_and_the_set_routes_serve_the_curve_check_and_the_sort(
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

    listed: dict[str, Any] = data(await client.get("/api/shots?sort=check&order=desc"))
    assert [item["id"] for item in listed["items"]] == [shot]
    assert listed["items"][0]["checks"]["badge"] == "ramp: fast flow +2"
    assert listed["items"][0]["review"]["state"] == "unreviewed"
    assert [w["fault"] for w in listed["items"][0]["checks"]["entries"]] == [
        "fast flow",
        "skipped",
        "over target",
    ]
    detail: dict[str, Any] = data(await client.get(f"/api/sets/{row.id}"))
    in_set = detail["versions"][0]["shots"]
    assert [item["checks"]["badge"] for item in in_set] == ["ramp: fast flow +2"]
    assert (await client.get("/api/shots?sort=check&cursor=abc")).status_code == 400
