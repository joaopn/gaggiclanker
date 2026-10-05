"""What depends on where a shot is filed is worked out when it is read, never stored.

Filing, moving or discarding a shot changes its version's target yield, and so
its share of the target and its over- and under-target warnings. None of that is
in the stored derivation: refiling must change what is read and leave the stored
columns, and the derivation version, exactly as they were.
"""

from __future__ import annotations

from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch
from gaggiclanker.shotinfo import load_shots
from tests.shotinfo.conftest import Archive


async def _stored(archive: Archive) -> tuple[object, ...]:
    row = await archive.db.fetch_one(
        "SELECT phases_json, diagnostics_json, derivation_version FROM shots WHERE id = ?",
        (archive.shot,),
    )
    assert row is not None
    return tuple(row)


async def test_refiling_a_shot_into_a_version_with_another_target_changes_what_is_read(
    archive: Archive,
) -> None:
    sets = SetsRepository(archive.db)
    before_row = await _stored(archive)
    (facts,) = await load_shots(archive.db, [archive.shot])
    # 31.6 g in the cup against the version's 36 g target.
    assert facts.target_yield_g == 36.0
    assert facts.share_of_target(31.6) == 87.8
    assert [w.badge for w in facts.warnings] == ["Shot: under target"]

    other = await sets.add_version(
        archive.set_id, SetVersionPatch(target_yield_g=30.0, intent="a shorter shot")
    )
    assert other is not None
    assert await sets.assign_shot(archive.shot, other.id)
    (refiled,) = await load_shots(archive.db, [archive.shot])
    assert refiled.target_yield_g == 30.0
    assert refiled.share_of_target(31.6) == 105.3
    assert refiled.warnings == []

    longer = await sets.add_version(
        archive.set_id, SetVersionPatch(target_yield_g=25.0, intent="a much shorter shot")
    )
    assert longer is not None
    assert await sets.assign_shot(archive.shot, longer.id)
    (over,) = await load_shots(archive.db, [archive.shot])
    assert [w.badge for w in over.warnings] == ["Shot: over target"]

    assert await sets.assign_shot(archive.shot, None)
    (inbox,) = await load_shots(archive.db, [archive.shot])
    assert inbox.target_yield_g is None
    assert inbox.share_of_target(31.6) is None
    assert inbox.warnings == []

    assert await _stored(archive) == before_row


async def test_a_shot_with_no_scale_has_no_yield_warning_whatever_the_target(
    archive: Archive,
) -> None:
    (facts,) = await load_shots(archive.db, [archive.no_scale])
    assert facts.target_yield_g == 36.0
    assert facts.warnings == []
