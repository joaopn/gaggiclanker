"""Proposing: every problem named at once, nothing written when anything is wrong."""

from __future__ import annotations

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.signatures import OverrideRow, SignatureRepository
from gaggiclanker.domain.signature import ExpectationInput, SignatureRefused
from gaggiclanker.signatures.service import SignatureService
from tests.lever_shot import LEVER_PROFILE
from tests.signatures.helpers import make_set_versions
from tests.signatures.test_carry import RAMP_CUP, _version


async def test_a_proposal_names_every_problem_and_writes_nothing(db: Database) -> None:
    version = await _version(db, LEVER_PROFILE)
    service = SignatureService(db)
    with pytest.raises(SignatureRefused) as refused:
        await service.propose(
            version,
            [
                ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP),
                ExpectationInput(tier="critical", kind="reached", phase="bloom"),
                ExpectationInput(tier="critical", kind="free_text", text="x", fault="weird"),
            ],
            reason="r",
        )
    message = str(refused.value)
    assert "expectation 2" in message and "expectation 3" in message
    assert "expectation 1" not in message
    assert await SignatureRepository(db).for_version(version) == []


async def test_a_good_proposal_is_in_force_at_once_with_where_it_came_from(
    db: Database,
) -> None:
    version = await _version(db, LEVER_PROFILE)
    rows = await SignatureService(db).propose(
        version,
        [ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP)],
        reason="the cup must not be full before the decline",
    )
    (row,) = rows
    assert (row.status, row.fault, row.phase) == ("confirmed", "early yield", "ramp")
    assert row.answered_at is None
    assert [
        r.id for r in (await SignatureRepository(db).confirmed_for_versions([version]))[version]
    ] == [row.id]
    assert row.reason == "the cup must not be full before the decline"
    assert row.proposed_by_thread_id is None


async def test_a_proposal_for_a_profile_version_that_is_not_stored_is_refused(db: Database) -> None:
    with pytest.raises(SignatureRefused, match="no profile version 99"):
        await SignatureService(db).propose(
            99, [ExpectationInput(tier="critical", kind="reached", phase="x")], reason="r"
        )


async def test_an_override_changes_only_the_numbers_of_a_measure_in_force(db: Database) -> None:
    version = await _version(db, LEVER_PROFILE)
    first, _ = await make_set_versions(db, version)
    service = SignatureService(db)
    repo = SignatureRepository(db)
    measure, reached = await service.propose(
        version,
        [
            ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP),
            ExpectationInput(tier="critical", kind="reached", phase="decline"),
        ],
        reason="r",
    )

    async def propose(expectation: int, compare: dict[str, object]) -> OverrideRow:
        return await service.propose_override(
            set_version_id=first,
            profile_version_id=version,
            expectation_id=expectation,
            compare=compare,
            reason="a coarser bean",
            thread_id=None,
        )

    await repo.reject(measure.id, reason="no")
    with pytest.raises(SignatureRefused, match="Only an expectation in force"):
        await propose(measure.id, {"op": "<=", "value": 0.2})
    await repo.restore(measure.id)
    with pytest.raises(SignatureRefused, match="Only a measure"):
        await propose(reached.id, {"op": "<=", "value": 0.2})
    with pytest.raises(SignatureRefused, match="not its comparison"):
        await propose(measure.id, {"op": ">=", "value": 0.2})
    with pytest.raises(SignatureRefused, match="compare is not valid"):
        await propose(measure.id, {"op": "<=", "low": 1})
    with pytest.raises(SignatureRefused, match="not one of this version's"):
        await service.propose_override(
            set_version_id=first,
            profile_version_id=None,
            expectation_id=measure.id,
            compare={"op": "<=", "value": 0.2},
            reason="r",
            thread_id=None,
        )
    stored = await propose(measure.id, {"op": "<=", "value": 0.2})
    assert stored.compare is not None and stored.compare.value == 0.2
    assert stored.status == "confirmed"
    # The expression, tier and phase are the profile's, untouched.
    again = await repo.get(measure.id)
    assert again is not None and again.expression is not None
    assert again.expression.compare.value == 0.15  # type: ignore[union-attr]
    assert (again.tier, again.phase) == ("critical", "ramp")


async def test_a_newer_override_replaces_the_same_expectations_and_refuses_another(
    db: Database,
) -> None:
    version = await _version(db, LEVER_PROFILE)
    first, _ = await make_set_versions(db, version)
    service = SignatureService(db)
    repo = SignatureRepository(db)
    measure, other = await service.propose(
        version,
        [
            ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP),
            ExpectationInput(
                tier="critical",
                kind="measure",
                expression={**RAMP_CUP, "window": {"phase": "soak"}},
            ),
        ],
        reason="r",
    )

    async def propose(expectation: int, value: float) -> OverrideRow:
        return await service.propose_override(
            set_version_id=first,
            profile_version_id=version,
            expectation_id=expectation,
            compare={"op": "<=", "value": value},
            reason="a coarser bean",
            thread_id=None,
        )

    one = await propose(measure.id, 0.2)
    two = await propose(measure.id, 0.25)
    assert one.id != two.id
    replaced = await repo.get_override(one.id)
    assert replaced is not None and replaced.status == "withdrawn"
    # A version has one override at a time: another expectation's is refused and said.
    with pytest.raises(SignatureRefused, match="already has an override in force"):
        await propose(other.id, 0.3)
    live = await repo.overrides_for_set_version(first, statuses=["confirmed"])
    assert [o.id for o in live] == [two.id]
    # What the person rejected is not put back by proposing exactly it again.
    await repo.reject_override(two.id, reason="no")
    with pytest.raises(SignatureRefused, match="rejected exactly this limit"):
        await propose(measure.id, 0.25)
    await propose(measure.id, 0.3)


async def test_an_expectation_already_there_is_refused_saying_which(db: Database) -> None:
    version = await _version(db, LEVER_PROFILE)
    service = SignatureService(db)
    repo = SignatureRepository(db)
    ramp = ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP)
    (first,) = await service.propose(version, [ramp], reason="r")

    # In force already, whatever tier it is proposed at.
    with pytest.raises(SignatureRefused, match="expectation 1 is already in force"):
        await service.propose(version, [ramp.model_copy(update={"tier": "important"})], reason="r")
    # Rejected by the person: only they can put it back.
    await repo.reject(first.id, reason="no")
    with pytest.raises(SignatureRefused, match="expectation 1 is one the person rejected"):
        await service.propose(version, [ramp], reason="r")
    assert [r.status for r in await repo.for_version(version)] == ["rejected"]
    await repo.restore(first.id)
    with pytest.raises(SignatureRefused, match="already in force"):
        await service.propose(version, [ramp], reason="r")
    # Free text is the same when it reads the same; a warning by its word and phase.
    text = ExpectationInput(
        tier="context", kind="free_text", text="Pressure  falls", fault="unstable"
    )
    await service.propose(version, [text], reason="r")
    with pytest.raises(SignatureRefused, match="already in force"):
        await service.propose(
            version, [text.model_copy(update={"text": "pressure falls"})], reason="r"
        )
    # The same measure with another limit, or another kind of thing in the same phase, is new.
    looser = {**RAMP_CUP, "compare": {"op": "<=", "value": 0.2}}
    await service.propose(
        version,
        [
            ExpectationInput(tier="critical", kind="measure", expression=looser),
            ExpectationInput(tier="critical", kind="reached", phase="ramp"),
        ],
        reason="r",
    )


async def test_a_proposal_that_repeats_itself_is_refused(db: Database) -> None:
    version = await _version(db, LEVER_PROFILE)
    twice = ExpectationInput(tier="critical", kind="reached", phase="decline")
    with pytest.raises(SignatureRefused, match="expectation 2 is repeated in this proposal"):
        await SignatureService(db).propose(version, [twice, twice], reason="r")
    assert await SignatureRepository(db).for_version(version) == []


async def test_the_refusal_says_which_kind_of_waiting_row_an_expectation_duplicates(
    db: Database,
) -> None:
    version = await _version(db, LEVER_PROFILE)
    service = SignatureService(db)
    repo = SignatureRepository(db)
    ramp = ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP)
    decline = ExpectationInput(tier="critical", kind="reached", phase="decline")
    rows = await service.propose(version, [ramp, decline], reason="r")
    # One from before signatures were in force at once, one carried without its phase.
    await db.execute(
        "UPDATE signature_expectations SET status = 'proposed' WHERE id = ?", (rows[0].id,)
    )
    await db.execute(
        "UPDATE signature_expectations SET status = 'proposed', needs_phase = 1 WHERE id = ?",
        (rows[1].id,),
    )

    with pytest.raises(SignatureRefused) as refused:
        await service.propose(version, [ramp, decline], reason="r")

    message = str(refused.value)
    old, carried = message.split("expectation 2")
    assert "from before signatures were in force at once" in old
    assert "reject it and then restore it to put it in force" in old
    assert "names a phase this profile version no longer has" not in old
    assert "names a phase this profile version no longer has" in carried
    assert "restore it" not in carried
    assert len(await repo.for_version(version)) == 2


async def test_count_in_force_counts_only_what_is_in_force(db: Database) -> None:
    version = await _version(db, LEVER_PROFILE)
    service = SignatureService(db)
    assert await service.count_in_force(version) == 0
    first, _ = await service.propose(
        version,
        [
            ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP),
            ExpectationInput(tier="critical", kind="reached", phase="decline"),
        ],
        reason="r",
    )
    assert await service.count_in_force(version) == 2
    await SignatureRepository(db).reject(first.id, reason="no")
    assert await service.count_in_force(version) == 1
