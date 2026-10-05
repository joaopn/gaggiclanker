"""Proposing: every problem named at once, nothing written when anything is wrong."""

from __future__ import annotations

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.signatures import SignatureRepository
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


async def test_a_good_proposal_is_stored_as_proposed_with_where_it_came_from(
    db: Database,
) -> None:
    version = await _version(db, LEVER_PROFILE)
    rows = await SignatureService(db).propose(
        version,
        [ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP)],
        reason="the cup must not be full before the decline",
    )
    (row,) = rows
    assert (row.status, row.fault, row.phase) == ("proposed", "early yield", "ramp")
    assert row.reason == "the cup must not be full before the decline"
    assert row.proposed_by_thread_id is None


async def test_a_proposal_for_a_profile_version_that_is_not_stored_is_refused(db: Database) -> None:
    with pytest.raises(SignatureRefused, match="no profile version 99"):
        await SignatureService(db).propose(
            99, [ExpectationInput(tier="critical", kind="reached", phase="x")], reason="r"
        )


async def test_an_override_changes_only_the_numbers_of_a_confirmed_measure(db: Database) -> None:
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

    async def propose(expectation: int, compare: dict[str, object]) -> object:
        return await service.propose_override(
            set_version_id=first,
            profile_version_id=version,
            expectation_id=expectation,
            compare=compare,
            reason="a coarser bean",
            thread_id=None,
        )

    with pytest.raises(SignatureRefused, match="not confirmed"):
        await propose(measure.id, {"op": "<=", "value": 0.2})
    await repo.answer(measure.id, confirm=True)
    await repo.answer(reached.id, confirm=True)
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
    assert stored.compare.value == 0.2  # type: ignore[attr-defined]
    # The expression, tier and phase are the profile's, untouched.
    again = await repo.get(measure.id)
    assert again is not None and again.expression is not None
    assert again.expression.compare.value == 0.15  # type: ignore[union-attr]
    assert (again.tier, again.phase) == ("critical", "ramp")


async def test_an_expectation_already_there_is_refused_as_already_there(db: Database) -> None:
    version = await _version(db, LEVER_PROFILE)
    service = SignatureService(db)
    repo = SignatureRepository(db)
    ramp = ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP)
    (first,) = await service.propose(version, [ramp], reason="r")

    # Waiting for the person.
    with pytest.raises(SignatureRefused, match="expectation 1 is already proposed and waiting"):
        await service.propose(version, [ramp.model_copy(update={"tier": "important"})], reason="r")
    # Confirmed.
    await repo.answer(first.id, confirm=True)
    with pytest.raises(SignatureRefused, match="expectation 1 is already confirmed"):
        await service.propose(version, [ramp], reason="r")
    # Free text is the same when it reads the same; a warning by its word and phase.
    text = ExpectationInput(
        tier="context", kind="free_text", text="Pressure  falls", fault="unstable"
    )
    await service.propose(version, [text], reason="r")
    with pytest.raises(SignatureRefused, match="already proposed"):
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
    # A rejected one is not "there": the proposer may try it again as it was.
    (rejected,) = await service.propose(
        version, [ExpectationInput(tier="context", kind="reached", phase="soak")], reason="r"
    )
    await repo.answer(rejected.id, confirm=False, reject_reason="no")
    await service.propose(
        version, [ExpectationInput(tier="context", kind="reached", phase="soak")], reason="r"
    )


async def test_a_proposal_that_repeats_itself_is_refused(db: Database) -> None:
    version = await _version(db, LEVER_PROFILE)
    twice = ExpectationInput(tier="critical", kind="reached", phase="decline")
    with pytest.raises(SignatureRefused, match="expectation 2 is repeated in this proposal"):
        await SignatureService(db).propose(version, [twice, twice], reason="r")
    assert await SignatureRepository(db).for_version(version) == []
