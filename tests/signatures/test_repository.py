"""The signature tables: only a confirmed expectation counts, and an answer is given once."""

from __future__ import annotations

import asyncio

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.signatures import (
    ExpectationWrite,
    OverrideWrite,
    SignatureRepository,
)
from gaggiclanker.domain.metric_language import Compare, Expression
from tests.sets.conftest import make_profile_version

RAMP_CUP = Expression.model_validate(
    {
        "channel": "cup_weight",
        "op": "at_end",
        "window": {"phase": "ramp"},
        "relative_to": "target_yield",
        "compare": {"op": "<=", "value": 0.15},
    }
)


def _measure(**extra: object) -> ExpectationWrite:
    return ExpectationWrite.model_validate(
        {
            "status": "confirmed",
            "tier": "critical",
            "phase": "ramp",
            "kind": "measure",
            "expression": RAMP_CUP,
            "fault": "early yield",
            "sentence": "the cup at the end of the ramp, at most 0.15 of the target",
            **extra,
        }
    )


def _reached(phase: str = "decline", **extra: object) -> ExpectationWrite:
    return ExpectationWrite.model_validate(
        {
            "status": "confirmed",
            "tier": "critical",
            "phase": phase,
            "kind": "reached",
            "fault": "skipped",
            "sentence": f"the {phase} begins",
            **extra,
        }
    )


async def test_an_expectation_is_stored_with_the_status_it_is_given(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    rows = await repo.add(
        version, [_measure(), _reached(), _reached("gone", status="proposed", needs_phase=True)]
    )

    assert [r.position for r in rows] == [0, 1, 2]
    assert [r.status for r in rows] == ["confirmed", "confirmed", "proposed"]
    assert rows[0].expression == RAMP_CUP
    # In force at once, with no answer from anyone; a needs-phase row never counts.
    assert rows[0].answered_at is None
    found = (await repo.confirmed_for_versions([version]))[version]
    assert [r.id for r in found] == [rows[0].id, rows[1].id]
    assert (await repo.counts([version]))[version] == {"confirmed": 2, "proposed": 1}


def test_a_write_names_its_status_and_a_needs_phase_one_is_never_in_force() -> None:
    with pytest.raises(ValueError, match="status"):
        ExpectationWrite.model_validate(
            {"tier": "critical", "kind": "reached", "fault": "skipped", "sentence": "x"}
        )
    with pytest.raises(ValueError, match="never in force"):
        _reached("gone", needs_phase=True)


async def test_positions_are_never_reused(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    first = await repo.add(version, [_measure(), _reached()])
    assert (await repo.reject(first[1].id, reason="no")).row is not None
    more = await repo.add(version, [_reached("soak")])
    assert more[0].position == 2


async def test_reject_restore_and_the_reason_are_kept(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    first, second = await repo.add(version, [_measure(), _reached()])

    rejected = await repo.reject(second.id, reason="  the cup is enough ")
    assert rejected.row is not None
    assert (rejected.row.status, rejected.row.reject_reason) == ("rejected", "the cup is enough")
    assert rejected.row.answered_at is not None
    assert [r.id for r in (await repo.confirmed_for_versions([version]))[version]] == [first.id]

    restored = await repo.restore(second.id)
    assert restored.row is not None
    assert (restored.row.status, restored.row.reject_reason) == ("confirmed", "")
    found = (await repo.confirmed_for_versions([version]))[version]
    assert [r.id for r in found] == [first.id, second.id]


async def test_restore_needs_a_rejected_row_and_reject_needs_one_not_rejected(
    db: Database,
) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])

    assert (await repo.restore(row.id)).refused == "not_rejected"
    assert (await repo.reject(row.id)).row is not None
    assert (await repo.reject(row.id)).refused == "already_rejected"
    assert (await repo.reject(9999)).refused == "no_expectation"
    assert (await repo.restore(9999)).refused == "no_expectation"


async def test_two_rejections_at_once_give_one_answer(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])

    results = await asyncio.gather(repo.reject(row.id), repo.reject(row.id))

    assert sorted(r.refused or "ok" for r in results) == ["already_rejected", "ok"]
    stored = await repo.get(row.id)
    assert stored is not None and stored.status == "rejected"


async def test_a_restore_does_not_overwrite_a_rejection_made_a_moment_earlier(
    db: Database,
) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])
    await repo.reject(row.id)

    results = await asyncio.gather(repo.restore(row.id), repo.restore(row.id))

    assert sorted(r.refused or "ok" for r in results) == ["not_rejected", "ok"]
    stored = await repo.get(row.id)
    assert stored is not None and stored.status == "confirmed"


async def test_an_expectation_that_needs_a_phase_can_be_rejected_and_never_restored(
    db: Database,
) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_reached("gone", status="proposed", needs_phase=True)])
    assert (await repo.restore(row.id)).refused == "not_rejected"
    rejected = await repo.reject(row.id, reason="not needed")
    assert rejected.row is not None and rejected.row.status == "rejected"
    assert (await repo.restore(row.id)).refused == "needs_phase"
    assert await repo.confirmed_for_versions([version]) == {}


async def test_the_tier_changes_only_while_the_expectation_is_in_force(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    first, second = await repo.add(version, [_measure(), _reached()])
    moved = await repo.set_tier(first.id, "important")
    assert moved.row is not None and moved.row.tier == "important"
    (stuck,) = await repo.add(version, [_reached("gone", status="proposed", needs_phase=True)])
    # A row that is not in force has no tier to move.
    assert (await repo.set_tier(stuck.id, "context")).refused == "not_in_force"
    await repo.reject(second.id)
    assert (await repo.set_tier(second.id, "context")).refused == "already_rejected"
    assert (await repo.set_tier(9999, "context")).refused == "no_expectation"


async def test_a_damaged_expression_is_read_as_unreadable_not_as_an_error(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])
    await db.execute(
        "UPDATE signature_expectations SET expression_json = '{\"op\": 3}' WHERE id = ?", (row.id,)
    )
    stored = await repo.get(row.id)
    assert stored is not None
    assert stored.expression is None and stored.readable is False


async def test_a_newer_override_replaces_the_one_in_force_without_being_a_rejection(
    db: Database,
) -> None:
    from tests.signatures.helpers import make_set_versions

    version = await make_profile_version(db, "Lever")
    first_version, second_version = await make_set_versions(db, version)
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])

    one = await repo.add_override(
        OverrideWrite(
            set_version_id=first_version, expectation_id=row.id, compare=Compare(op="<=", value=0.2)
        )
    )
    # In force at once, with no answer from anyone.
    assert one.status == "confirmed" and one.answered_at is None
    two = await repo.add_override(
        OverrideWrite(
            set_version_id=first_version,
            expectation_id=row.id,
            compare=Compare(op="<=", value=0.25),
        )
    )
    replaced = await repo.get_override(one.id)
    assert replaced is not None
    # Withdrawn is not the person's rejection: it carries no reason and is not "rejected".
    assert (replaced.status, replaced.reject_reason) == ("withdrawn", "")
    found = await repo.confirmed_overrides([first_version, second_version])
    assert list(found) == [first_version]
    assert found[first_version].id == two.id
    assert found[first_version].compare == Compare(op="<=", value=0.25)


async def test_an_override_is_rejected_and_restored_by_the_person(db: Database) -> None:
    from tests.signatures.helpers import make_set_versions

    version = await make_profile_version(db, "Lever")
    (set_version, _) = await make_set_versions(db, version)
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])
    first = await repo.add_override(
        OverrideWrite(
            set_version_id=set_version, expectation_id=row.id, compare=Compare(op="<=", value=0.2)
        )
    )

    assert (await repo.restore_override(first.id)).refused == "not_rejected"
    rejected = await repo.reject_override(first.id, reason="too loose")
    assert rejected.row is not None
    assert (rejected.row.status, rejected.row.reject_reason) == ("rejected", "too loose")
    assert await repo.confirmed_overrides([set_version]) == {}
    assert (await repo.reject_override(first.id)).refused == "already_rejected"

    restored = await repo.restore_override(first.id)
    assert restored.row is not None and restored.row.status == "confirmed"
    assert list(await repo.confirmed_overrides([set_version])) == [set_version]


async def test_a_rejected_override_is_not_restored_over_another_in_force_or_without_its_expectation(
    db: Database,
) -> None:
    from tests.signatures.helpers import make_set_versions

    version = await make_profile_version(db, "Lever")
    (set_version, _) = await make_set_versions(db, version)
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])
    first = await repo.add_override(
        OverrideWrite(
            set_version_id=set_version, expectation_id=row.id, compare=Compare(op="<=", value=0.2)
        )
    )
    await repo.reject_override(first.id)
    second = await repo.add_override(
        OverrideWrite(
            set_version_id=set_version, expectation_id=row.id, compare=Compare(op="<=", value=0.3)
        )
    )
    assert (await repo.restore_override(first.id)).refused == "another_in_force"
    await repo.reject_override(second.id)
    await repo.reject(row.id)
    assert (await repo.restore_override(first.id)).refused == "expectation_not_confirmed"
    assert (await repo.restore_override(9999)).refused == "no_override"


@pytest.mark.parametrize("bad", [{"kind": "reached", "expression": RAMP_CUP}, {"kind": "measure"}])
def test_the_shape_of_a_write_is_checked(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"expression|warning"):
        ExpectationWrite.model_validate(
            {"status": "confirmed", "tier": "critical", "sentence": "x", "phase": "ramp", **bad}
        )


async def test_rejecting_an_expectation_withdraws_its_override_and_restoring_leaves_it_out(
    db: Database,
) -> None:
    from tests.signatures.helpers import make_set_versions

    version = await make_profile_version(db, "Lever")
    (set_version, _) = await make_set_versions(db, version)
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])
    override = await repo.add_override(
        OverrideWrite(
            set_version_id=set_version, expectation_id=row.id, compare=Compare(op="<=", value=0.2)
        )
    )

    await repo.reject(row.id, reason="no")

    withdrawn = await repo.get_override(override.id)
    assert withdrawn is not None
    # Out of force with the expectation, and not the person's rejection of the override.
    assert (withdrawn.status, withdrawn.reject_reason) == ("withdrawn", "")
    assert await repo.confirmed_overrides([set_version]) == {}

    await repo.restore(row.id)
    assert (await repo.get_override(override.id)).status == "withdrawn"  # type: ignore[union-attr]
    assert await repo.confirmed_overrides([set_version]) == {}


async def test_an_override_for_another_expectation_is_refused_inside_the_transaction(
    db: Database,
) -> None:
    from gaggiclanker.db.repos.signatures import AnotherOverrideInForce
    from tests.signatures.helpers import make_set_versions

    version = await make_profile_version(db, "Lever")
    (set_version, _) = await make_set_versions(db, version)
    repo = SignatureRepository(db)
    first, second = await repo.add(version, [_measure(), _measure(phase="soak")])

    def write(expectation: int, value: float) -> OverrideWrite:
        return OverrideWrite(
            set_version_id=set_version,
            expectation_id=expectation,
            compare=Compare(op="<=", value=value),
        )

    # Two at once for two expectations: one is in force, the other is refused, never both.
    results = await asyncio.gather(
        repo.add_override(write(first.id, 0.2)),
        repo.add_override(write(second.id, 0.3)),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["AnotherOverrideInForce", "OverrideRow"]
    live = await repo.overrides_for_set_version(set_version, statuses=["confirmed"])
    assert len(live) == 1
    with pytest.raises(AnotherOverrideInForce):
        await repo.add_override(
            write(second.id if live[0].expectation_id == first.id else first.id, 0.4)
        )


async def test_a_row_proposed_before_in_force_signatures_is_no_check_and_can_be_rejected(
    db: Database,
) -> None:
    from tests.signatures.helpers import make_set_versions

    version = await make_profile_version(db, "Lever")
    (set_version, _) = await make_set_versions(db, version)
    repo = SignatureRepository(db)
    (old,) = await repo.add(version, [_reached(status="proposed")])
    assert old.needs_phase is False and old.status == "proposed"
    assert await repo.confirmed_for_versions([version]) == {}
    assert (await repo.counts([version]))[version] == {"proposed": 1}
    # An override proposed in the old way is not in force either.
    (measure,) = await repo.add(version, [_measure()])
    await db.execute(
        "INSERT INTO set_version_signature_overrides (set_version_id, expectation_id, "
        'compare_json, status) VALUES (?, ?, \'{"op": "<=", "value": 0.2}\', \'proposed\')',
        (set_version, measure.id),
    )
    assert await repo.confirmed_overrides([set_version]) == {}

    assert (await repo.restore(old.id)).refused == "not_rejected"
    assert (await repo.set_tier(old.id, "context")).refused == "not_in_force"
    assert (await repo.reject(old.id, reason="old")).row is not None
