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
            "tier": "critical",
            "phase": "ramp",
            "kind": "measure",
            "expression": RAMP_CUP,
            "fault": "early yield",
            "sentence": "the cup at the end of the ramp, at most 0.15 of the target",
            **extra,
        }
    )


def _reached(phase: str = "decline") -> ExpectationWrite:
    return ExpectationWrite(
        tier="critical",
        phase=phase,
        kind="reached",
        fault="skipped",
        sentence=f"the {phase} begins",
    )


async def test_a_proposed_expectation_is_stored_and_is_not_confirmed(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    rows = await repo.add(version, [_measure(), _reached()])

    assert [r.position for r in rows] == [0, 1]
    assert {r.status for r in rows} == {"proposed"}
    assert rows[0].expression == RAMP_CUP
    # Nothing counts until a person confirmed it.
    assert await repo.confirmed_for_versions([version]) == {}
    assert (await repo.counts([version]))[version] == {"proposed": 2}


async def test_positions_are_never_reused(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    first = await repo.add(version, [_measure(), _reached()])
    assert (await repo.answer(first[1].id, confirm=False, reject_reason="no")).row is not None
    more = await repo.add(version, [_reached("soak")])
    assert more[0].position == 2


async def test_confirm_reject_and_the_reason_are_kept(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    first, second = await repo.add(version, [_measure(), _reached()])

    confirmed = await repo.answer(first.id, confirm=True)
    assert confirmed.row is not None
    assert confirmed.row.status == "confirmed"
    assert confirmed.row.answered_at is not None
    rejected = await repo.answer(second.id, confirm=False, reject_reason="  the cup is enough ")
    assert rejected.row is not None
    assert (rejected.row.status, rejected.row.reject_reason) == ("rejected", "the cup is enough")

    assert [r.id for r in (await repo.confirmed_for_versions([version]))[version]] == [first.id]


async def test_two_confirmations_at_once_give_one_answer(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])

    results = await asyncio.gather(
        repo.answer(row.id, confirm=True), repo.answer(row.id, confirm=True)
    )

    assert sorted(r.refused or "ok" for r in results) == ["not_waiting", "ok"]
    stored = await repo.get(row.id)
    assert stored is not None and stored.status == "confirmed"


async def test_a_confirmation_does_not_overwrite_a_rejection_made_a_moment_earlier(
    db: Database,
) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])

    results = await asyncio.gather(
        repo.answer(row.id, confirm=False, reject_reason="no"),
        repo.answer(row.id, confirm=True),
    )

    assert sorted(r.refused or "ok" for r in results) == ["not_waiting", "ok"]
    stored = await repo.get(row.id)
    assert stored is not None
    assert stored.status == next(r.row.status for r in results if r.row is not None)


async def test_confirm_all_confirms_the_waiting_ones_and_leaves_one_that_needs_a_phase(
    db: Database,
) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    waiting = await repo.add(version, [_measure(), _reached(), _reached("soak")])
    (stuck,) = await repo.add(version, [_reached("gone").model_copy(update={"needs_phase": True})])
    await repo.answer(waiting[2].id, confirm=False)

    done = await repo.confirm_all(version)

    assert sorted(r.id for r in done) == sorted([waiting[0].id, waiting[1].id])
    assert (await repo.get(stuck.id)).status == "proposed"  # type: ignore[union-attr]
    assert (await repo.get(waiting[2].id)).status == "rejected"  # type: ignore[union-attr]
    assert await repo.confirm_all(version) == []


async def test_an_expectation_that_needs_a_phase_cannot_be_confirmed(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_reached("gone").model_copy(update={"needs_phase": True})])
    assert (await repo.answer(row.id, confirm=True)).refused == "needs_phase"
    # It can still be rejected.
    assert (await repo.answer(row.id, confirm=False)).row is not None


async def test_the_tier_changes_only_while_the_expectation_waits(db: Database) -> None:
    version = await make_profile_version(db, "Lever")
    repo = SignatureRepository(db)
    first, second = await repo.add(version, [_measure(), _reached()])
    moved = await repo.set_tier(first.id, "important")
    assert moved.row is not None and moved.row.tier == "important"
    await repo.answer(second.id, confirm=True)
    assert (await repo.set_tier(second.id, "context")).refused == "not_waiting"
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


async def test_an_override_is_one_per_set_version_and_a_confirmed_one_stands(db: Database) -> None:
    from tests.signatures.helpers import make_set_versions

    version = await make_profile_version(db, "Lever")
    first_version, second_version = await make_set_versions(db, version)
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])
    await repo.answer(row.id, confirm=True)

    loose = Compare(op="<=", value=0.2)
    one = await repo.add_override(
        OverrideWrite(set_version_id=first_version, expectation_id=row.id, compare=loose)
    )
    assert one is not None and one.status == "proposed"
    # A newer proposal replaces the waiting one.
    two = await repo.add_override(
        OverrideWrite(
            set_version_id=first_version,
            expectation_id=row.id,
            compare=Compare(op="<=", value=0.25),
        )
    )
    assert two is not None
    assert (await repo.get_override(one.id)).status == "rejected"  # type: ignore[union-attr]
    answered = await repo.answer_override(two.id, confirm=True)
    assert answered.row is not None and answered.row.status == "confirmed"
    # It stands against a later proposal for the same version, and it is that version's alone.
    assert (
        await repo.add_override(
            OverrideWrite(set_version_id=first_version, expectation_id=row.id, compare=loose)
        )
        is None
    )
    found = await repo.confirmed_overrides([first_version, second_version])
    assert list(found) == [first_version]
    assert found[first_version].compare == Compare(op="<=", value=0.25)


async def test_an_override_cannot_be_confirmed_when_its_expectation_is_not(db: Database) -> None:
    from tests.signatures.helpers import make_set_versions

    version = await make_profile_version(db, "Lever")
    (set_version, _) = await make_set_versions(db, version)
    repo = SignatureRepository(db)
    (row,) = await repo.add(version, [_measure()])
    proposed = await repo.add_override(
        OverrideWrite(
            set_version_id=set_version, expectation_id=row.id, compare=Compare(op="<=", value=0.2)
        )
    )
    assert proposed is not None
    assert (await repo.answer_override(proposed.id, confirm=True)).refused == (
        "expectation_not_confirmed"
    )
    # Rejecting is always possible, once.
    assert (await repo.answer_override(proposed.id, confirm=False, reject_reason="x")).row
    assert (await repo.answer_override(proposed.id, confirm=False)).refused == "not_waiting"


@pytest.mark.parametrize("bad", [{"kind": "reached", "expression": RAMP_CUP}, {"kind": "measure"}])
def test_the_shape_of_a_write_is_checked(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"expression|warning"):
        ExpectationWrite.model_validate(
            {"tier": "critical", "sentence": "x", "phase": "ramp", **bad}
        )
