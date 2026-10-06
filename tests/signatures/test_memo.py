"""A read never parses a log, a shot's checks are remembered, and nothing stale is served."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch
from gaggiclanker.db.repos.shots import REVIEW_SORT, ShotsRepository
from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.shotinfo.fields import shot_fields
from gaggiclanker.signatures import checks as checks_module
from gaggiclanker.signatures.service import SignatureService
from tests.lever_shot import LEVER_PROFILE, lever_shot
from tests.signatures.helpers import add_shot, derived_lever, make_set_versions
from tests.signatures.test_carry import RAMP_CUP, _version


async def _signed(db: Database) -> tuple[int, int, int, int]:
    version = await _version(db, LEVER_PROFILE)
    set_version, other = await make_set_versions(db, version)
    shot = await add_shot(db, set_version_id=set_version, profile_version_id=version)
    rows = await SignatureService(db).propose(
        version,
        [
            ExpectationInput(tier="critical", kind="reached", phase="decline"),
            ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP),
        ],
        reason="r",
    )
    await SignatureRepository(db).confirm_all(version)
    assert len(rows) == 2
    return version, set_version, other, shot


async def _badge(db: Database, shot: int) -> str | None:
    page = await ShotsRepository(db).list_shots(limit=50, sort=REVIEW_SORT)
    return next(row.badge for row in page.items if row.id == shot)


async def test_no_read_path_parses_a_log(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, _, shot = await _signed(db)

    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("a read parsed a log")

    monkeypatch.setattr("gaggiclanker.domain.slog.parse_slog", refuse)
    monkeypatch.setattr("gaggiclanker.signatures.checks.stored_samples", refuse, raising=False)
    assert await _badge(db, shot) == "ramp: early yield +3"
    document = await shot_fields(db, shot)
    assert document is not None and document.badge == "ramp: early yield +3"


async def test_a_shot_is_worked_out_once_and_then_remembered(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, _, shot = await _signed(db)
    reads = 0
    original = SignatureRepository.stored_samples

    async def counting(self: SignatureRepository, ids: list[int]) -> object:
        nonlocal reads
        reads += 1 if ids else 0
        return await original(self, ids)

    monkeypatch.setattr(SignatureRepository, "stored_samples", counting)
    first = await _badge(db, shot)
    cold = reads
    for _ in range(3):
        assert await _badge(db, shot) == first
    assert cold == 1 and reads == 1, "the warm reads touched no sample"


async def test_whatever_a_result_depends_on_changing_is_a_different_answer(db: Database) -> None:
    version, set_version, _, shot = await _signed(db)
    repo = SignatureRepository(db)
    service = SignatureService(db)
    assert await _badge(db, shot) == "ramp: early yield +3"

    # Another expectation confirmed is another fingerprint of the signature.
    (extra,) = await service.propose(
        version,
        [ExpectationInput(tier="critical", kind="reached", phase="soak")],
        reason="r",
    )
    await repo.answer(extra.id, confirm=True)
    assert await _badge(db, shot) == "ramp: early yield +3"  # held: nothing new on the badge
    ramp = next(r for r in await repo.for_version(version) if r.kind == "measure")
    override = await service.propose_override(
        set_version_id=set_version,
        profile_version_id=version,
        expectation_id=ramp.id,
        compare={"op": "<=", "value": 1.3},
        reason="r",
        thread_id=None,
    )
    assert await _badge(db, shot) == "ramp: early yield +3"  # proposed: no change
    await repo.answer_override(override.id, confirm=True)
    assert await _badge(db, shot) == "decline: skipped +2"
    await repo.withdraw_override(override.id)
    assert await _badge(db, shot) == "ramp: early yield +3"

    # A confirmed override again, and then a refiling under a version with another target.
    again = await service.propose_override(
        set_version_id=set_version,
        profile_version_id=version,
        expectation_id=ramp.id,
        compare={"op": "<=", "value": 1.3},
        reason="r",
        thread_id=None,
    )
    await repo.answer_override(again.id, confirm=True)
    assert await _badge(db, shot) == "decline: skipped +2"
    sets = SetsRepository(db)
    longer = await sets.add_version(
        (await repo.set_of_version(set_version)) or 0,
        SetVersionPatch(target_yield_g=100.0, intent="a long shot"),
    )
    assert longer is not None
    assert await sets.assign_shot(shot, longer.id)
    # 42.2 g against 100 g is 42 % of target: over 15 %, and no override on this version.
    assert await _badge(db, shot) == "ramp: early yield +3"


async def test_a_re_derived_shot_is_not_served_from_the_memory(db: Database) -> None:
    version, _, _, shot = await _signed(db)
    assert await _badge(db, shot) == "ramp: early yield +3"
    # The derivation stored something else about the shot (here: its decline is reached).
    await db.execute(
        "UPDATE shots SET phases_json = '[]', diagnostics_json = '{}' WHERE id = ?", (shot,)
    )
    assert version
    assert await _badge(db, shot) != "ramp: early yield +3"


async def test_the_memory_is_bounded_but_never_below_one_requests_working_set(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checks_module, "MEMO_LIMIT", 2)
    version, set_version, _, _ = await _signed(db)
    for number in range(5):
        await add_shot(
            db,
            set_version_id=set_version,
            profile_version_id=version,
            device_id=f"0010{number:02d}",
        )
    shots = ShotsRepository(db)
    # A page of one shot at a time: the bound holds.
    for offset in range(6):
        await shots.list_shots(limit=1, offset=offset)
    assert len(checks_module._MEMO) <= 2

    # A Review sort works out all six at once and keeps all six, so the next page is warm.
    reads = 0
    original = SignatureRepository.stored_samples

    async def counting(self: SignatureRepository, ids: list[int]) -> object:
        nonlocal reads
        reads += 1 if ids else 0
        return await original(self, ids)

    monkeypatch.setattr(SignatureRepository, "stored_samples", counting)
    await shots.list_shots(limit=2, sort=REVIEW_SORT)
    assert len(checks_module._MEMO) == 6
    reads_after_first = reads
    await shots.list_shots(limit=2, offset=2, sort=REVIEW_SORT)
    assert reads == reads_after_first


async def test_the_list_and_the_fields_share_one_entry_per_shot(db: Database) -> None:
    _, _, _, shot = await _signed(db)
    await _badge(db, shot)
    assert len(checks_module._MEMO) == 1
    document = await shot_fields(db, shot)
    assert document is not None and document.badge == "ramp: early yield +3"
    assert len(checks_module._MEMO) == 1, "the fields route used its own key"


async def test_a_replaced_copy_of_a_shot_is_not_served_from_the_memory(db: Database) -> None:
    """The importer's "a better copy": the samples change and the derived facts do not."""
    version = await _version(db, LEVER_PROFILE)
    set_version, _ = await make_set_versions(db, version)
    shot = await add_shot(db, set_version_id=set_version, profile_version_id=version)
    service = SignatureService(db)
    await service.propose(
        version,
        [
            ExpectationInput(
                tier="critical",
                kind="measure",
                expression={
                    "channel": "scale_flow",
                    "op": "max",
                    "window": {"phase": "ramp"},
                    "compare": {"op": "<=", "value": 5},
                },
            )
        ],
        reason="r",
    )
    await SignatureRepository(db).confirm_all(version)
    before = await _badge(db, shot)
    assert (
        before is not None and "fast flow" in before and "ramp: fast flow" == before.split(" +")[0]
    )

    # A copy whose ramp is much faster, stored with the old derivation's facts, as an import
    # that cannot re-derive them leaves them.
    slog = lever_shot()
    samples = [
        s.model_copy(update={"vf": 9.0}) if index >= 95 else s
        for index, s in enumerate(slog.samples)
    ]
    faster = dataclasses.replace(slog, samples=samples)
    derived = derived_lever(faster)
    old = await db.fetch_one(
        "SELECT phases_json, diagnostics_json, derivation_version FROM shots WHERE id = ?", (shot,)
    )
    assert old is not None
    await asyncio.sleep(0.01)
    await ShotsRepository(db).replace_derived(
        shot,
        derived.shot.model_copy(
            update={
                "phases_json": old["phases_json"],
                "diagnostics_json": old["diagnostics_json"],
                "derivation_version": old["derivation_version"],
            }
        ),
        derived.samples,
    )

    after = await _badge(db, shot)
    assert after is not None and after.startswith("ramp: fast flow")
    document = await shot_fields(db, shot)
    assert document is not None
    measure = next(c for c in document.checks if c.kind == "measure")
    assert (measure.status, measure.value) == ("failed", 9.0)


async def test_a_refiling_that_changes_only_the_dose_is_a_different_answer(db: Database) -> None:
    version = await _version(db, LEVER_PROFILE)
    set_version, _ = await make_set_versions(db, version)
    shot = await add_shot(db, set_version_id=set_version, profile_version_id=version)
    await SignatureService(db).propose(
        version,
        [
            ExpectationInput(
                tier="critical",
                kind="measure",
                expression={
                    "channel": "cup_weight",
                    "op": "at_end",
                    "window": {"phase": "ramp"},
                    "relative_to": "dose",
                    "compare": {"op": "<=", "value": 2.0},
                },
            )
        ],
        reason="r",
    )
    await SignatureRepository(db).confirm_all(version)
    sets = SetsRepository(db)
    set_id = (await SignatureRepository(db).set_of_version(set_version)) or 0
    # The same Set version's recipe but for the dose (a version with the same target and profile).
    same_dose = await sets.add_version(
        set_id, SetVersionPatch(grind_setting="15", intent="same dose")
    )
    bigger_dose = await sets.add_version(set_id, SetVersionPatch(dose_g=22.0, intent="more coffee"))
    assert same_dose is not None and bigger_dose is not None
    # 42.2 g over 18 g is 2.34, over the limit; over 22 g it is 1.92, within it.
    first = await _badge(db, shot)
    assert first is not None and first.startswith("ramp: early yield")
    assert await sets.assign_shot(shot, bigger_dose.id)
    second = await _badge(db, shot)
    assert second is not None and second.startswith("ramp: fast flow")
    assert await sets.assign_shot(shot, same_dose.id)
    assert await _badge(db, shot) == first


async def test_a_re_derivation_that_changes_only_the_metrics_is_worked_out_again(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, _, shot = await _signed(db)
    reads = 0
    original = SignatureRepository.stored_samples

    async def counting(self: SignatureRepository, ids: list[int]) -> object:
        nonlocal reads
        reads += 1 if ids else 0
        return await original(self, ids)

    monkeypatch.setattr(SignatureRepository, "stored_samples", counting)
    first = await _badge(db, shot)
    assert reads == 1
    await _badge(db, shot)
    assert reads == 1
    # The warnings stay what they were and the stored metrics gain a fact: a different derivation.
    await db.execute(
        "UPDATE shots SET diagnostics_json = json_set(diagnostics_json, '$.metrics.marker', 1) "
        "WHERE id = ?",
        (shot,),
    )
    assert await _badge(db, shot) == first
    assert reads == 2


async def test_a_quarantined_shot_has_no_checks_and_logs_nothing(db: Database) -> None:
    import structlog

    from gaggiclanker.db.repos.shots import ShotInsert

    version, set_version, _, _ = await _signed(db)
    shots = ShotsRepository(db)
    quarantined = await shots.insert(
        ShotInsert(
            device_id="000999",
            raw_slog=b"not a log",
            quarantined=True,
            quarantine_reason="it did not parse",
            profile_version_id=version,
        )
    )
    assert await SetsRepository(db).assign_shot(quarantined, set_version) in (True, False)
    with structlog.testing.capture_logs() as logs:
        page = await shots.list_shots(limit=50, sort=REVIEW_SORT, quarantined=True)
        document = await shot_fields(db, quarantined)
    row = next(r for r in page.items if r.id == quarantined)
    assert (row.badge, row.warnings) == (None, [])
    assert document is not None and document.checks == []
    assert [e for e in logs if e["log_level"] in ("warning", "error")] == []


def test_a_lever_shot_is_the_one_these_tests_stand_on() -> None:
    assert lever_shot().header.final_weight_g == 42.2


async def test_confirming_another_expectation_is_not_served_the_old_answer(db: Database) -> None:
    version, _, _, shot = await _signed(db)
    assert await _badge(db, shot) == "ramp: early yield +3"
    (soak,) = await SignatureService(db).propose(
        version,
        [
            ExpectationInput(
                tier="critical",
                kind="measure",
                expression={
                    "channel": "cup_weight",
                    "op": "at_end",
                    "window": {"phase": "soak"},
                    "relative_to": "target_yield",
                    "compare": {"op": "<=", "value": 0.05},
                },
            )
        ],
        reason="r",
    )
    assert await _badge(db, shot) == "ramp: early yield +3"  # proposed: nothing moves
    await SignatureRepository(db).answer(soak.id, confirm=True)
    assert await _badge(db, shot) == "soak: early yield +4"  # the soak's cup is over its limit


async def test_what_the_shot_is_filed_under_is_part_of_the_key_even_when_the_shot_row_is_untouched(
    db: Database,
) -> None:
    """The recipe of a Set version is immutable, but nothing here relies on that: a dose, a
    target or a filing changed by a write that never touches the shot row is a different answer."""
    version = await _version(db, LEVER_PROFILE)
    first, second = await make_set_versions(db, version)
    shot = await add_shot(db, set_version_id=first, profile_version_id=version)
    service = SignatureService(db)
    repo = SignatureRepository(db)
    ramp_expression = {
        "channel": "cup_weight",
        "op": "at_end",
        "window": {"phase": "ramp"},
        "compare": {"op": "<=", "value": 0.15},
    }
    by_dose, by_target = await service.propose(
        version,
        [
            ExpectationInput(
                tier="critical",
                kind="measure",
                expression={
                    **ramp_expression,
                    "relative_to": "dose",
                    "compare": {"op": "<=", "value": 2.0},
                },
            ),
            ExpectationInput(
                tier="important",
                kind="measure",
                expression={**ramp_expression, "relative_to": "target_yield"},
            ),
        ],
        reason="r",
    )
    await repo.confirm_all(version)

    async def statuses() -> dict[int, str]:
        document = await shot_fields(db, shot)
        assert document is not None
        return {c.expectation_id: c.status for c in document.checks if c.kind == "measure"}  # type: ignore[misc]

    assert await statuses() == {by_dose.id: "failed", by_target.id: "failed"}
    await db.execute("UPDATE set_versions SET dose_g = 22 WHERE id = ?", (first,))
    assert (await statuses())[by_dose.id] == "held"
    await db.execute("UPDATE set_versions SET target_yield_g = 400 WHERE id = ?", (first,))
    assert (await statuses())[by_target.id] == "held"
    # Moved to another version with its own dose and target, by a write that leaves the row alone.
    await db.execute(
        "UPDATE set_versions SET dose_g = 18, target_yield_g = 36 WHERE id = ?", (second,)
    )
    await db.execute("UPDATE shots SET set_version_id = ? WHERE id = ?", (second, shot))
    assert await statuses() == {by_dose.id: "failed", by_target.id: "failed"}
