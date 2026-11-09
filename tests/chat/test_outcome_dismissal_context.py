"""What a Recipe conversation is told about a dismissed grade when an outcome is recorded.

"Nothing changed: the outcome stays recorded as X" is true only when X was on record at the
dismissal and nothing was recorded since; and "more counted since" names the moment it counts
from: the recording, or the grade the person dismissed after it.
"""

from __future__ import annotations

import asyncio

from gaggiclanker.chat.context import opening_context
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.outcome_proposals import OutcomeProposalsRepository, OutcomeProposalWrite
from gaggiclanker.db.repos.recipes import RecipesRepository, RecipeVersionPatch, VersionOutcomeWrite
from gaggiclanker.tools.scope import ToolScope
from tests.recipes.conftest import make_shot
from tests.review.conftest import Fixture

NOTE = "Time held at 31 s against 28 s; the sourness did not move at all."
_n = [0]


async def _setup(a: Fixture, shots: int) -> int:
    r = RecipesRepository(a.db)
    v = await r.add_version(
        a.recipe_id,
        RecipeVersionPatch.model_validate(
            {
                "grind_setting": "21",
                "prediction": "Expect two seconds longer and less sour.",
                "compares_to_version_id": None,
            }
        ),
    )
    assert v is not None
    for _ in range(shots):
        await _shot(a, v.id)
    return v.id


async def _shot(a: Fixture, vid: int) -> None:
    _n[0] += 1
    sid = await make_shot(a.db, f"0097{_n[0]:02d}")
    assert await RecipesRepository(a.db).assign_shot(sid, vid)
    await JudgementsRepository(a.db).upsert(sid, JudgementWrite.model_validate({"rating": 4}))
    await asyncio.sleep(0.003)


async def _ctx(a: Fixture, vid: int) -> str:
    return await opening_context(a.db, ToolScope.for_thread(a.recipe_id, vid), thread_id=None)


async def test_since_it_was_recorded_counts_from_the_recording(archive: Fixture) -> None:
    """Recorded by hand on 1, a grade on 3 dismissed, one more shot: 3 were counted since the
    outcome was recorded, and the line must not say 1 more 'since it was recorded'."""
    a = archive
    vid = await _setup(a, 1)
    await RecipesRepository(a.db).set_outcome(
        a.recipe_id, vid, VersionOutcomeWrite(outcome="failed", note="by hand")
    )
    await asyncio.sleep(0.003)
    await _shot(a, vid)
    await _shot(a, vid)
    p = (
        await OutcomeProposalsRepository(a.db).create(
            a.recipe_id, vid, OutcomeProposalWrite.model_validate({"outcome": "held", "note": NOTE})
        )
    ).proposal
    assert p is not None
    await OutcomeProposalsRepository(a.db).dismiss(a.recipe_id, p.id, "")
    await _shot(a, vid)
    line = next(x for x in (await _ctx(a, vid)).splitlines() if x.startswith("Outcome:"))
    assert "recorded on 1 counted shot; 1 more counted since it was recorded" not in line, line
    assert line.startswith(
        "Outcome: failed, recorded on 1 counted shot; 1 more counted since the grade they "
        "dismissed, written on 3 counted shots."
    ), line


async def test_a_dismissal_before_the_outcome_was_recorded_does_not_say_it_stays(
    archive: Fixture,
) -> None:
    """Grade 'held' dismissed while the outcome was open; the person then recorded 'held' by hand.
    Nothing was recorded when they dismissed, so "the outcome stays recorded as held"
    is false (and reads as if they kept held over the agent's held)."""
    a = archive
    vid = await _setup(a, 1)
    p = (
        await OutcomeProposalsRepository(a.db).create(
            a.recipe_id, vid, OutcomeProposalWrite.model_validate({"outcome": "held", "note": NOTE})
        )
    ).proposal
    assert p is not None
    await OutcomeProposalsRepository(a.db).dismiss(a.recipe_id, p.id, "")
    await asyncio.sleep(0.003)
    await RecipesRepository(a.db).set_outcome(
        a.recipe_id, vid, VersionOutcomeWrite(outcome="held", note="by hand")
    )
    text = await _ctx(a, vid)
    assert "Nothing changed: the outcome stays recorded as held" not in text, text
    assert "They dismissed it. The outcome has since been recorded as held." in text, text


async def test_a_change_by_hand_after_the_dismissal_is_not_told_as_unchanged(
    archive: Fixture,
) -> None:
    """Recorded failed, grade dismissed, then changed to partly held by hand: 'Nothing changed: the
    outcome stays recorded as partly held' is false; it was failed when they dismissed."""
    a = archive
    vid = await _setup(a, 1)
    R = RecipesRepository(a.db)
    await R.set_outcome(a.recipe_id, vid, VersionOutcomeWrite(outcome="failed", note="by hand"))
    await asyncio.sleep(0.003)
    p = (
        await OutcomeProposalsRepository(a.db).create(
            a.recipe_id, vid, OutcomeProposalWrite.model_validate({"outcome": "held", "note": NOTE})
        )
    ).proposal
    assert p is not None
    await OutcomeProposalsRepository(a.db).dismiss(a.recipe_id, p.id, "")
    await asyncio.sleep(0.003)
    await R.set_outcome(
        a.recipe_id, vid, VersionOutcomeWrite(outcome="partly_held", note="by hand")
    )
    text = await _ctx(a, vid)
    assert "Nothing changed: the outcome stays recorded as partly held" not in text, text
    assert "The outcome has since been recorded as partly held." in text, text


async def test_a_dismissal_over_the_outcome_on_record_says_it_stays(archive: Fixture) -> None:
    """The other half: recorded, then a grade dismissed with nothing recorded after it."""
    a = archive
    vid = await _setup(a, 1)
    await RecipesRepository(a.db).set_outcome(
        a.recipe_id, vid, VersionOutcomeWrite(outcome="failed", note="by hand")
    )
    await asyncio.sleep(0.003)
    p = (
        await OutcomeProposalsRepository(a.db).create(
            a.recipe_id, vid, OutcomeProposalWrite.model_validate({"outcome": "held", "note": NOTE})
        )
    ).proposal
    assert p is not None
    await OutcomeProposalsRepository(a.db).dismiss(a.recipe_id, p.id, "")
    text = await _ctx(a, vid)
    assert "Nothing changed: the outcome stays recorded as failed." in text, text
