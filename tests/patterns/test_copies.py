"""A copy made by duplicating a Set is never a second Set's evidence.

A duplicate copies insights to a new Set, each flagged with the insight it came from. Find
patterns weighs insights across Sets, so it folds a copy into its original's Set: a copy and its
original are one lesson. Pinned at each of the three places that count Sets (the proposal filter,
the run's gate and the Knowledge page's count), with a source Set A and its duplicate D, and in
what the model is told.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.knowledge_insights import (
    InsightScope,
    InsightWrite,
    home_sets,
)
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.patterns import (
    PatternProposalsRepository,
    PatternProposalWrite,
    PatternRunsRepository,
)
from gaggiclanker.db.repos.sets import SetVersionWrite, SetWrite
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.llm.prompts import DEFAULT_PROMPTS_DIR, PromptService, seed_prompts
from gaggiclanker.llm.service import LlmService
from gaggiclanker.patterns.context import build_patterns_input
from gaggiclanker.patterns.models import PatternProposalOut, PatternsResult, filter_proposals
from gaggiclanker.patterns.service import NotEnoughSets, PatternsService
from tests.llm.conftest import FakeProvider
from tests.patterns.test_repository import _finished, _source
from tests.patterns.world import PatternWorld


@dataclass(slots=True)
class Duplicated:
    """A's one insight and its copy on D, the duplicate of A."""

    world: PatternWorld
    d: int
    original: int
    copy: int


async def _duplicate(world: PatternWorld, *, copied_from: int | None = None) -> Duplicated:
    original = await world.own(world.a, "Light roasts channel below 9 clicks.")
    dup = await world.sets.create(
        SetWrite(name="Guji milk", bean_id=world.bean_a, grinder_id=world.grinder_id),
        SetVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22"),
    )
    copy = await world.insights.insert(
        InsightWrite(
            text="Light roasts channel below 9 clicks.",
            source="chat",
            confirmed=True,
            set_id=dup.id,
            copied_from_insight_id=original if copied_from is None else copied_from,
            copied_from_set_id=world.a,
        )
    )
    return Duplicated(world, dup.id, original, copy)


def _cite(*ids: int) -> PatternsResult:
    return PatternsResult(
        proposals=[
            PatternProposalOut(text="A lesson.", scope={}, source_insight_ids=list(ids))  # type: ignore[arg-type]
        ]
    )


def test_a_lessons_home_is_the_set_it_was_first_learned_in() -> None:
    # (id, its Set, the Set the first original was learned in). 2 and 4 are copies of lessons
    # learned in Set 10 and 3 is a lesson of Set 30; whether the originals are still read is not
    # asked at all.
    folded = home_sets([(1, 10, None), (2, 20, 10), (3, 30, None), (4, 25, 10), (5, 50, None)])

    assert folded == {1: 10, 2: 10, 3: 30, 4: 10, 5: 50}


async def _second_duplicate(world: PatternWorld, original: int, name: str) -> tuple[int, int]:
    """Another duplicate of A, with its copy of A's insight: (the Set's id, the copy's id)."""
    other = await world.sets.create(
        SetWrite(name=name, bean_id=world.bean_a, grinder_id=world.grinder_id),
        SetVersionWrite(dose_g=18),
    )
    copy = await world.insights.insert(
        InsightWrite(
            text="Light roasts channel below 9 clicks.",
            source="chat",
            confirmed=True,
            set_id=other.id,
            copied_from_insight_id=original,
            copied_from_set_id=world.a,
        )
    )
    return other.id, copy


class TestCopiesOfOneOriginal:
    async def test_two_duplicates_of_a_stay_one_lesson_after_as_insight_is_gone(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        _, second = await _second_duplicate(world, dup.original, "Guji second copy")
        runs = PatternRunsRepository(world.db)
        assert await runs.sets_with_insights() == 1  # A, D and E: one lesson

        await world.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (dup.original,))

        # Still one Set: D and E copy the same #N, listed or not.
        assert await runs.sets_with_insights() == 1
        given = await build_patterns_input(world.db)
        outcome = filter_proposals(_cite(dup.copy, second), given)
        assert outcome.kept == [] and outcome.dropped == {"one_set": 1}
        assert given.lesson_set_count == 1
        await world.own(world.c, "Its own.")
        assert await runs.sets_with_insights() == 2

    async def test_a_proposal_citing_only_the_copies_is_one_set_while_the_original_is_read(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        _, second = await _second_duplicate(world, dup.original, "Guji second copy")
        given = await build_patterns_input(world.db)

        outcome = filter_proposals(_cite(dup.copy, second), given)

        assert outcome.dropped == {"one_set": 1}

    async def test_the_runs_set_count_is_the_folded_one(self, world: PatternWorld) -> None:
        dup = await _duplicate(world)
        await _second_duplicate(world, dup.original, "Guji second copy")
        given = await build_patterns_input(world.db)

        # Three Sets hold insights, one lesson: what the run card and the Knowledge page say.
        assert len(given.sets) == 3
        assert given.lesson_set_count == 1
        assert await PatternRunsRepository(world.db).sets_with_insights() == 1


class TestWhicheverDuplicateCameFirst:
    """The verdict does not depend on which of two duplicates of a Set came first."""

    async def _scenario(self, world: PatternWorld, *, d_first: bool) -> tuple[int, int, int, int]:
        original = await world.own(world.a, "Light roasts channel below 9 clicks.")
        pair: list[tuple[int, int]] = []
        for name in ("D", "E") if d_first else ("E", "D"):
            pair.append(await _second_duplicate(world, original, name))
        by_name = dict(zip(("D", "E") if d_first else ("E", "D"), pair, strict=True))
        d_set, d_copy = by_name["D"]
        _, e_copy = by_name["E"]
        d_own = await world.own(d_set, "D's own: this bag wants 94 C.")
        await world.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (original,))
        return d_set, d_copy, e_copy, d_own

    @pytest.mark.parametrize("d_first", [True, False])
    async def test_a_copy_and_an_unrelated_lesson_of_another_duplicate_are_two_sets(
        self, world: PatternWorld, d_first: bool
    ) -> None:
        _d, d_copy, e_copy, d_own = await self._scenario(world, d_first=d_first)
        given = await build_patterns_input(world.db)

        # E's copy of X counts for A, D's own lesson Y for D: two lessons from two Sets.
        assert filter_proposals(_cite(e_copy, d_own), given).kept
        # D's copy of X and E's copy of X are one lesson.
        assert filter_proposals(_cite(d_copy, e_copy), given).dropped == {"one_set": 1}
        assert await PatternRunsRepository(world.db).sets_with_insights() == 2

    @pytest.mark.parametrize("d_first", [True, False])
    async def test_approval_gives_the_same_verdict(
        self, world: PatternWorld, d_first: bool
    ) -> None:
        d_set, _copy, e_copy, d_own = await self._scenario(world, d_first=d_first)
        e_set = int(
            await world.db.fetch_value(
                "SELECT set_id FROM knowledge_insights WHERE id = ?", (e_copy,)
            )
        )
        proposal = PatternProposalWrite(
            text="A lesson.",
            scope=InsightScope(),
            sources=[
                await _source(world, e_set, e_copy, "Light roasts channel below 9 clicks."),
                await _source(world, d_set, d_own, "D's own: this bag wants 94 C."),
            ],
        )
        await _finished(world, proposal)
        proposal_id = int(await world.db.fetch_value("SELECT MAX(id) FROM pattern_proposals"))

        result = await PatternProposalsRepository(world.db).approve(proposal_id)

        assert result.refused is None


class TestTheRunCard:
    async def test_the_run_records_the_folded_set_count(
        self, world: PatternWorld, llm: LlmService
    ) -> None:
        dup = await _duplicate(world)
        await _second_duplicate(world, dup.original, "Guji second copy")
        await world.own(world.c, "Its own.")
        await seed_prompts(PromptsRepository(world.db), DEFAULT_PROMPTS_DIR)
        service = PatternsService(world.db, llm, PromptService(PromptsRepository(world.db)))

        row = await service.run()

        # Four Sets hold insights, but A, D and E are one lesson: "from 2 Sets".
        assert (row.insights_read, row.sets_read) == (4, 2)


class TestApproval:
    async def _waiting(self, world: PatternWorld, dup: Duplicated, other: int) -> int:
        proposal = PatternProposalWrite(
            text="Light roasts channel below 9 clicks.",
            scope=InsightScope(),
            sources=[
                await _source(world, world.a, dup.original, "Light roasts channel below 9 clicks."),
                await _source(world, dup.d, dup.copy, "Light roasts channel below 9 clicks."),
                await _source(world, world.c, other, "Light roasts want care below 9 clicks."),
            ],
        )
        await _finished(world, proposal)
        return int(await world.db.fetch_value("SELECT MAX(id) FROM pattern_proposals"))

    async def test_a_copy_and_its_original_are_not_two_sets_at_approval(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        other = await world.own(world.c, "Light roasts want care below 9 clicks.")
        given = await build_patterns_input(world.db)
        assert filter_proposals(_cite(dup.original, dup.copy, other), given).kept
        proposal_id = await self._waiting(world, dup, other)
        # C's insight is deleted before the person presses Approve.
        await world.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (other,))
        before = int(await world.db.fetch_value("SELECT COUNT(*) FROM knowledge_insights"))

        result = await PatternProposalsRepository(world.db).approve(proposal_id)

        assert result.refused == "too_few_sets"
        assert int(await world.db.fetch_value("SELECT COUNT(*) FROM knowledge_insights")) == before
        assert (
            await world.db.fetch_value(
                "SELECT COUNT(*) FROM knowledge_insights "
                "WHERE set_id IS NULL AND pattern_run_id IS NOT NULL"
            )
            == 0
        )

    async def test_with_the_third_set_still_there_it_goes_through(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        other = await world.own(world.c, "Light roasts want care below 9 clicks.")
        proposal_id = await self._waiting(world, dup, other)

        result = await PatternProposalsRepository(world.db).approve(proposal_id)

        assert result.refused is None and result.proposal is not None

    async def test_a_copy_and_another_insight_of_its_originals_set_are_one_set(
        self, world: PatternWorld
    ) -> None:
        """The approval folds over every insight there is, not only the cited ones."""
        dup = await _duplicate(world)
        second_a = await world.own(world.a, "Another of A's lessons.")
        proposal = PatternProposalWrite(
            text="Something.",
            scope=InsightScope(),
            sources=[
                await _source(world, dup.d, dup.copy, "Light roasts channel below 9 clicks."),
                await _source(world, world.a, second_a, "Another of A's lessons."),
            ],
        )
        await _finished(world, proposal)
        proposal_id = int(await world.db.fetch_value("SELECT MAX(id) FROM pattern_proposals"))

        result = await PatternProposalsRepository(world.db).approve(proposal_id)

        assert result.refused == "too_few_sets"


class TestTheGate:
    """The run's gate: `NotEnoughSets`."""

    async def _service(self, db: Database, llm: LlmService) -> PatternsService:
        await seed_prompts(PromptsRepository(db), DEFAULT_PROMPTS_DIR)
        return PatternsService(db, llm, PromptService(PromptsRepository(db)))

    async def test_a_set_and_its_duplicate_alone_do_not_unlock_a_run(
        self, world: PatternWorld, llm: LlmService, provider: FakeProvider
    ) -> None:
        await _duplicate(world)
        service = await self._service(world.db, llm)

        with pytest.raises(NotEnoughSets) as refused:
            await service.start(tasks=TaskRegistry())

        assert "1 Set(s)" in str(refused.value)
        assert provider.calls == []

    async def test_with_a_third_sets_own_insight_it_does(
        self, world: PatternWorld, llm: LlmService
    ) -> None:
        await _duplicate(world)
        await world.own(world.c, "Dark roasts want a coarser grind.")
        service = await self._service(world.db, llm)
        tasks = TaskRegistry()

        row, started = await service.start(tasks=tasks)

        assert started and row.status == "running"
        task = tasks.get("patterns")
        if task is not None:
            await task


class TestTheKnowledgePagesCount:
    async def test_a_set_holding_only_copies_does_not_count(self, world: PatternWorld) -> None:
        runs = PatternRunsRepository(world.db)
        await _duplicate(world)
        assert await runs.sets_with_insights() == 1

        await world.own(world.c, "Its own.")
        assert await runs.sets_with_insights() == 2

    async def test_a_copy_stays_its_originals_lesson_when_the_original_is_gone(
        self, world: PatternWorld
    ) -> None:
        runs = PatternRunsRepository(world.db)
        dup = await _duplicate(world)

        await world.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (dup.original,))

        # D's copy still counts for A, where the lesson was learned: one Set, not two.
        assert await runs.sets_with_insights() == 1
        await world.own(world.c, "Its own.")
        assert await runs.sets_with_insights() == 2

    async def test_a_dismissed_original_still_homes_its_copies_in_a(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        await world.db.execute(
            "UPDATE knowledge_insights SET dismissed = 1 WHERE id = ?", (dup.original,)
        )
        runs = PatternRunsRepository(world.db)
        assert await runs.sets_with_insights() == 1  # the copy alone, homed in A

        await world.own(world.b, "Another Set.")

        assert await runs.sets_with_insights() == 2  # A (through the copy) and B
        given = await build_patterns_input(world.db)
        assert filter_proposals(_cite(dup.copy), given).dropped == {"one_set": 1}

    async def test_a_set_holding_a_copy_and_a_lesson_of_its_own_stands_for_two_sets(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        await world.own(dup.d, "D's own lesson.")
        runs = PatternRunsRepository(world.db)

        assert await runs.sets_with_insights() == 2  # A (the copy's home) and D

        await world.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (dup.original,))

        assert await runs.sets_with_insights() == 2

    async def test_a_copy_of_a_copy_folds_into_the_first_originals_set(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        second = await world.sets.create(
            SetWrite(name="Guji again", bean_id=world.bean_a, grinder_id=world.grinder_id),
            SetVersionWrite(dose_g=18),
        )
        # Written as a duplicate of D writes it: the first original's id, not the copy's.
        await world.insights.insert(
            InsightWrite(
                text="Again.",
                source="chat",
                confirmed=True,
                set_id=second.id,
                copied_from_insight_id=dup.original,
                copied_from_set_id=world.a,
            )
        )

        assert await PatternRunsRepository(world.db).sets_with_insights() == 1


class TestTheNewSinceTheLastRunCount:
    async def test_a_copy_is_not_a_new_lesson(self, world: PatternWorld) -> None:
        runs = PatternRunsRepository(world.db)
        await _duplicate(world)

        assert await runs.new_since_last_run() == 1  # the original; its copy is not new

        await world.own(world.c, "Its own.")
        assert await runs.new_since_last_run() == 2

    async def test_a_copy_is_never_new_even_when_its_original_is_gone(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        await world.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (dup.original,))

        assert await PatternRunsRepository(world.db).new_since_last_run() == 0


class TestTheProposalFilter:
    async def test_a_proposal_citing_an_insight_and_its_copy_is_one_set(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        given = await build_patterns_input(world.db)

        outcome = filter_proposals(_cite(dup.original, dup.copy), given)

        assert outcome.kept == [] and outcome.dropped == {"one_set": 1}

    async def test_with_a_third_sets_insight_it_is_two_sets(self, world: PatternWorld) -> None:
        dup = await _duplicate(world)
        other = await world.own(world.c, "Dark roasts want a coarser grind.")
        given = await build_patterns_input(world.db)

        outcome = filter_proposals(_cite(dup.original, dup.copy, other), given)

        assert outcome.dropped == {}
        assert [source.insight_id for source in outcome.kept[0].sources] == [
            dup.original,
            dup.copy,
            other,
        ]

    async def test_the_copys_own_set_must_still_match_the_scope(self, world: PatternWorld) -> None:
        """Approving deletes the copy too, so its Set is a source Set for the scope check."""
        dup = await _duplicate(world)
        other = await world.own(world.b, "Light roasts want a coarser grind.")
        await world.db.execute(
            "UPDATE sets SET bean_id = ? WHERE id = ?", (world.bean_c, dup.d)
        )  # D is now a dark roast, unlike A and B: the lesson's two home Sets are both light
        given = await build_patterns_input(world.db)
        result = PatternsResult(
            proposals=[
                PatternProposalOut(
                    text="Light roasts only.",
                    scope={"roast_level": "light"},  # type: ignore[arg-type]
                    source_insight_ids=[dup.original, dup.copy, other],
                )
            ]
        )

        outcome = filter_proposals(result, given)

        assert outcome.dropped == {"scope_not_shared": 1}


class TestWhatTheModelIsTold:
    async def test_a_copy_says_copy_of_its_original_and_names_no_set(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        await world.own(world.c, "Dark roasts want a coarser grind.")

        rendered = (await build_patterns_input(world.db)).render()["set_insights"]

        assert f"#{dup.copy} [copy of #{dup.original}]" in rendered
        assert f"#{dup.original} [learned at" in rendered
        assert "copied from" not in rendered.lower()

    async def test_a_copy_is_marked_even_when_its_original_is_not_listed(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        await world.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (dup.original,))

        rendered = (await build_patterns_input(world.db)).render()["set_insights"]

        # Copies naming one #N are one lesson, whether #N is listed or not.
        assert f"#{dup.copy} [copy of #{dup.original}]" in rendered


async def test_the_prompt_says_a_copy_is_never_a_second_sets_evidence() -> None:
    text = (
        Path(__file__).resolve().parents[2] / "gaggiclanker" / "prompts" / "patterns.yaml"
    ).read_text()
    assert "`copy of #N`" in text and "never a second Set's evidence" in text
