"""What a Recipe conversation reads about how many shots a recorded outcome rests on.

The version's ``Outcome:`` line says how many counted shots it was recorded on and how many
were counted since; the answer to the agent's grade stops saying "recorded as X" once the person
has recorded something else by hand.
"""

from __future__ import annotations

from gaggiclanker.chat.context import opening_context
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.outcome_proposals import (
    OutcomeProposalsRepository,
    OutcomeProposalWrite,
)
from gaggiclanker.db.repos.recipes import RecipesRepository, VersionOutcomeWrite
from gaggiclanker.tools.scope import ToolScope
from tests.chat import test_context as _base
from tests.chat.test_context import Experiment, _shot

#: The experiment fixture of the opening-context tests, re-exported so it is one archive.
experiment = _base.experiment

NOTE = "Time went up by 2.5 s against v2.1 (target 2 to 3 s); the finish is still drying."


async def _context(experiment: Experiment, version_id: int) -> str:
    return await opening_context(
        experiment.db, ToolScope.for_thread(experiment.recipe_id, version_id), thread_id=None
    )


def _outcome_line(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("Outcome:"))


async def _stored(experiment: Experiment, version_id: int) -> int | None:
    value = await experiment.db.fetch_value(
        "SELECT outcome_counted_shots FROM recipe_versions WHERE id = ?", (version_id,)
    )
    return None if value is None else int(value)


async def _another_counted_shot(experiment: Experiment, version_id: int, device: str) -> None:
    shot = await _shot(
        experiment.db,
        device,
        started_at="2026-04-09T08:00:00.000Z",
        duration_ms=40_000,
        weight_g=36.0,
        first_drip_s=7.0,
        max_bar=9.0,
        brew_flow=1.9,
    )
    assert await RecipesRepository(experiment.db).assign_shot(shot, version_id)
    await JudgementsRepository(experiment.db).upsert(shot, JudgementWrite(rating=2))


class TestTheOutcomeLine:
    async def test_it_says_how_many_counted_shots_the_outcome_was_recorded_on(
        self, experiment: Experiment
    ) -> None:
        recorded = await _stored(experiment, experiment.v2)
        assert recorded is not None and recorded >= 1

        line = _outcome_line(await _context(experiment, experiment.v2))

        shots = "1 counted shot" if recorded == 1 else f"{recorded} counted shots"
        assert line.startswith(f"Outcome: failed, recorded on {shots}.")
        assert "more counted since" not in line

    async def test_it_says_how_many_were_counted_since(self, experiment: Experiment) -> None:
        recorded = await _stored(experiment, experiment.v2)
        assert recorded is not None
        # Half one: without the new shots there is nothing to add to the line.
        assert "since" not in _outcome_line(await _context(experiment, experiment.v2))
        await _another_counted_shot(experiment, experiment.v2, "000901")
        await _another_counted_shot(experiment, experiment.v2, "000902")

        line = _outcome_line(await _context(experiment, experiment.v2))

        assert f"recorded on {recorded} counted shot" in line
        assert "; 2 more counted since it was recorded." in line

    async def test_one_more_shot_is_said_too(self, experiment: Experiment) -> None:
        await _another_counted_shot(experiment, experiment.v2, "000901")

        line = _outcome_line(await _context(experiment, experiment.v2))

        assert "; 1 more counted since it was recorded." in line

    async def test_a_discarded_shot_is_not_counted_since(self, experiment: Experiment) -> None:
        await _another_counted_shot(experiment, experiment.v2, "000901")
        shot_id = await experiment.db.fetch_value("SELECT id FROM shots WHERE device_id = '000901'")
        await JudgementsRepository(experiment.db).upsert(
            int(shot_id), JudgementWrite(rating=2, discarded=True)
        )

        assert "since" not in _outcome_line(await _context(experiment, experiment.v2))

    async def test_an_unknown_count_reads_as_it_always_did(self, experiment: Experiment) -> None:
        assert await _stored(experiment, experiment.v2) is not None
        await _another_counted_shot(experiment, experiment.v2, "000901")
        await experiment.db.execute(
            "UPDATE recipe_versions SET outcome_counted_shots = NULL WHERE id = ?",
            (experiment.v2,),
        )

        line = _outcome_line(await _context(experiment, experiment.v2))

        assert line.startswith("Outcome: failed. Note:")
        assert "counted" not in line

    async def test_an_open_version_has_no_count_to_state(self, experiment: Experiment) -> None:
        line = _outcome_line(await _context(experiment, experiment.v5))
        assert line == "Outcome: open — nobody has graded it yet."


class TestTheAnswerAfterTheOutcomeMoved:
    async def _accepted(self, experiment: Experiment) -> OutcomeProposalsRepository:
        outcomes = OutcomeProposalsRepository(experiment.db)
        made = await outcomes.create(
            experiment.recipe_id,
            experiment.v2,
            OutcomeProposalWrite.model_validate({"outcome": "partly_held", "note": NOTE}),
        )
        assert made.proposal is not None
        assert (await outcomes.accept(experiment.recipe_id, made.proposal.id)).refused is None
        return outcomes

    async def test_a_hand_change_after_the_accept_is_said(self, experiment: Experiment) -> None:
        await self._accepted(experiment)
        # Half one: the accepted answer is rendered, and nothing contradicts it yet.
        before = await _context(experiment, experiment.v2)
        assert "They accepted it: the outcome is recorded as partly held." in before
        assert "has since been recorded" not in before

        await RecipesRepository(experiment.db).set_outcome(
            experiment.recipe_id, experiment.v2, VersionOutcomeWrite(outcome="held", note="")
        )

        after = await _context(experiment, experiment.v2)
        assert "They accepted it: the outcome is recorded as partly held." in after
        assert "The outcome has since been recorded as held." in after

    async def test_a_clear_after_the_accept_is_said(self, experiment: Experiment) -> None:
        await self._accepted(experiment)
        await RecipesRepository(experiment.db).clear_outcome(experiment.recipe_id, experiment.v2)

        assert "The outcome has since been recorded as open again." in await _context(
            experiment, experiment.v2
        )

    async def test_the_note_edited_by_hand_with_the_same_grade_is_not_a_new_outcome(
        self, experiment: Experiment
    ) -> None:
        await self._accepted(experiment)
        await RecipesRepository(experiment.db).set_outcome(
            experiment.recipe_id,
            experiment.v2,
            VersionOutcomeWrite(outcome="partly_held", note="A different reason."),
        )

        assert "has since been recorded" not in await _context(experiment, experiment.v2)

    async def test_a_changed_card_is_compared_with_what_the_person_recorded(
        self, experiment: Experiment
    ) -> None:
        outcomes = OutcomeProposalsRepository(experiment.db)
        made = await outcomes.create(
            experiment.recipe_id,
            experiment.v2,
            OutcomeProposalWrite.model_validate({"outcome": "partly_held", "note": NOTE}),
        )
        assert made.proposal is not None
        await outcomes.change(experiment.recipe_id, made.proposal.id, "failed")
        assert "has since been recorded" not in await _context(experiment, experiment.v2)

        await RecipesRepository(experiment.db).set_outcome(
            experiment.recipe_id, experiment.v2, VersionOutcomeWrite(outcome="held", note="")
        )

        assert "The outcome has since been recorded as held." in await _context(
            experiment, experiment.v2
        )

    async def test_a_dismissed_card_says_nothing_about_the_outcome_since(
        self, experiment: Experiment
    ) -> None:
        outcomes = OutcomeProposalsRepository(experiment.db)
        made = await outcomes.create(
            experiment.recipe_id,
            experiment.v2,
            OutcomeProposalWrite.model_validate({"outcome": "held", "note": NOTE}),
        )
        assert made.proposal is not None
        await outcomes.dismiss(experiment.recipe_id, made.proposal.id)

        text = await _context(experiment, experiment.v2)

        assert "They dismissed it." in text
        assert "has since been recorded" not in text
        # v2 is recorded (failed): a dismissal leaves it so, and the text must not say open.
        assert "the outcome is still open" not in text
        assert "the outcome stays recorded as failed" in text

    async def test_a_dismissed_grade_of_an_open_outcome_still_says_it_is_open(
        self, experiment: Experiment
    ) -> None:
        # v5 is open: the original sentence is true there.
        outcomes = OutcomeProposalsRepository(experiment.db)
        made = await outcomes.create(
            experiment.recipe_id,
            experiment.v5,
            OutcomeProposalWrite.model_validate({"outcome": "held", "note": NOTE}),
        )
        assert made.proposal is not None
        await outcomes.dismiss(experiment.recipe_id, made.proposal.id)

        text = await _context(experiment, experiment.v5)

        assert "the outcome is still open until they grade it" in text
        assert "stays recorded" not in text


class TestSinceAfterADismissal:
    """A dismissal means keep the outcome as it is, until more shots arrive than the card saw."""

    async def _dismissed_grade(self, experiment: Experiment) -> int:
        outcomes = OutcomeProposalsRepository(experiment.db)
        made = await outcomes.create(
            experiment.recipe_id,
            experiment.v2,
            OutcomeProposalWrite.model_validate({"outcome": "held", "note": NOTE}),
        )
        assert made.proposal is not None
        await outcomes.dismiss(experiment.recipe_id, made.proposal.id)
        return made.proposal.id

    async def test_the_agent_hears_of_new_shots_only_beyond_those_the_dismissed_card_saw(
        self, experiment: Experiment
    ) -> None:
        await _another_counted_shot(experiment, experiment.v2, "000901")
        await _another_counted_shot(experiment, experiment.v2, "000902")
        # Half one: without the dismissal the line says two more.
        assert "; 2 more counted since it was recorded." in _outcome_line(
            await _context(experiment, experiment.v2)
        )

        await self._dismissed_grade(experiment)

        line = _outcome_line(await _context(experiment, experiment.v2))
        assert "more counted since" not in line
        assert "recorded on" in line  # still the stored count, which the page also shows

        await _another_counted_shot(experiment, experiment.v2, "000903")
        assert (
            "; 1 more counted since the grade they dismissed, written on 3 counted shots."
            in _outcome_line(await _context(experiment, experiment.v2))
        )

    async def test_a_dismissal_from_before_the_outcome_was_recorded_does_not_count(
        self, experiment: Experiment
    ) -> None:
        await _another_counted_shot(experiment, experiment.v2, "000901")
        await _another_counted_shot(experiment, experiment.v2, "000902")
        card = await self._dismissed_grade(experiment)
        assert "more counted since" not in _outcome_line(await _context(experiment, experiment.v2))

        # The same card, but older than the recorded outcome: the person has graded since.
        await experiment.db.execute(
            "UPDATE recipe_outcome_proposals SET created_at = '2000-01-01T00:00:00.000Z' "
            "WHERE id = ?",
            (card,),
        )

        assert "; 2 more counted since it was recorded." in _outcome_line(
            await _context(experiment, experiment.v2)
        )
