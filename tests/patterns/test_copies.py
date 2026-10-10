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
    InsightWrite,
    home_sets,
)
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.patterns import PatternRunsRepository
from gaggiclanker.db.repos.sets import SetVersionWrite, SetWrite
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.llm.prompts import DEFAULT_PROMPTS_DIR, PromptService, seed_prompts
from gaggiclanker.llm.service import LlmService
from gaggiclanker.patterns.context import build_patterns_input
from gaggiclanker.patterns.models import PatternProposalOut, PatternsResult, filter_proposals
from gaggiclanker.patterns.service import NotEnoughSets, PatternsService
from tests.llm.conftest import FakeProvider
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
        )
    )
    return Duplicated(world, dup.id, original, copy)


def _cite(*ids: int) -> PatternsResult:
    return PatternsResult(
        proposals=[
            PatternProposalOut(text="A lesson.", scope={}, source_insight_ids=list(ids))  # type: ignore[arg-type]
        ]
    )


def test_a_copy_counts_for_its_originals_set_and_anything_else_for_its_own() -> None:
    folded = home_sets([(1, 10, None), (2, 20, 1), (3, 30, None), (4, 40, 99)])

    # 2 is a copy of 1 (read), 4 a copy of an insight that is not read: its own lesson again.
    assert folded == {1: 10, 2: 10, 3: 30, 4: 40}


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

    async def test_a_copy_whose_original_is_gone_is_its_own_sets_lesson_again(
        self, world: PatternWorld
    ) -> None:
        runs = PatternRunsRepository(world.db)
        dup = await _duplicate(world)

        await world.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (dup.original,))

        assert await runs.sets_with_insights() == 1  # D alone: one Set, now its own
        await world.own(world.c, "Its own.")
        assert await runs.sets_with_insights() == 2

    async def test_a_dismissed_original_no_longer_holds_its_copy(self, world: PatternWorld) -> None:
        dup = await _duplicate(world)
        await world.db.execute(
            "UPDATE knowledge_insights SET dismissed = 1 WHERE id = ?", (dup.original,)
        )
        await world.own(world.b, "Another Set.")

        # B and D are two Sets now: D's copy is nobody's duplicate any more.
        assert await PatternRunsRepository(world.db).sets_with_insights() == 2

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

    async def test_a_copy_whose_original_is_gone_is_new_evidence_of_its_own_set(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)

        await world.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (dup.original,))

        assert await PatternRunsRepository(world.db).new_since_last_run() == 1


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

    async def test_a_copy_whose_original_is_not_read_says_nothing(
        self, world: PatternWorld
    ) -> None:
        dup = await _duplicate(world)
        await world.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (dup.original,))

        rendered = (await build_patterns_input(world.db)).render()["set_insights"]

        assert f"#{dup.copy} [learned earlier]" in rendered
        assert "copy of" not in rendered


async def test_the_prompt_says_a_copy_is_never_a_second_sets_evidence() -> None:
    text = (
        Path(__file__).resolve().parents[2] / "gaggiclanker" / "prompts" / "patterns.yaml"
    ).read_text()
    assert "`copy of #N`" in text and "never a second Set's evidence" in text
