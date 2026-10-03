"""Runs and proposals: what a run may write, what Approve does, and how long a decline lives."""

from __future__ import annotations

import pytest

from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.insight_deletions import InsightDeletionsRepository, InsightDeletionWrite
from gaggiclanker.db.repos.insight_placement import InsightPlacementBuilder
from gaggiclanker.db.repos.knowledge_insights import InsightScope
from gaggiclanker.db.repos.patterns import (
    PatternProposalsRepository,
    PatternProposalWrite,
    PatternRunOutcome,
    PatternRunsRepository,
    PatternRunStart,
    PatternSource,
)
from tests.knowledge.insight_world import rows_containing
from tests.patterns.world import PatternWorld

DONE = PatternRunOutcome(status="done")


async def _source(world: PatternWorld, set_id: int, insight_id: int, text: str) -> PatternSource:
    row = await world.sets.get(set_id)
    assert row is not None
    return PatternSource(insight_id=insight_id, set_id=set_id, set_name=row.name, text=text)


async def _two(world: PatternWorld) -> tuple[int, int, PatternProposalWrite]:
    """One insight in each of A and B, and a proposal that generalises them (light roasts)."""
    a = await world.own(world.a, "Below 9 clicks the Niche channels.")
    b = await world.own(world.b, "The Niche channels under 9 with this roast.")
    return (
        a,
        b,
        PatternProposalWrite(
            text="The Niche channels below 9 clicks with light roasts.",
            scope=InsightScope(roast_level="light"),
            sources=[
                await _source(world, world.a, a, "Below 9 clicks the Niche channels."),
                await _source(world, world.b, b, "The Niche channels under 9 with this roast."),
            ],
        ),
    )


async def _finished(
    world: PatternWorld, *proposals: PatternProposalWrite, seen: dict[str, str] | None = None
) -> int:
    """A finished run; ``seen`` is what its input holds (the insights the model was shown)."""
    runs = PatternRunsRepository(world.db)
    run_id = await runs.start(PatternRunStart(input=seen or {}))
    await runs.finish_done(run_id, DONE, list(proposals))
    return run_id


async def _dump_insights(world: PatternWorld) -> list[str]:
    rows = await world.db.fetch_all("SELECT * FROM knowledge_insights ORDER BY id")
    return [repr(tuple(row)) for row in rows]


class TestARunWritesOnlyItsOwnRows:
    async def test_finishing_a_run_with_proposals_changes_no_insight(
        self, world: PatternWorld
    ) -> None:
        _a, _b, proposal = await _two(world)
        before = await _dump_insights(world)

        await _finished(world, proposal)

        assert await _dump_insights(world) == before
        assert await PatternRunsRepository(world.db).latest() is not None

    async def test_a_failed_run_writes_its_row_and_no_proposal(self, world: PatternWorld) -> None:
        runs = PatternRunsRepository(world.db)
        run_id = await runs.start(PatternRunStart())

        row = await runs.finish_failed(
            run_id, PatternRunOutcome(status="failed", error="auth: bad key")
        )

        assert row is not None and (row.status, row.error) == ("failed", "auth: bad key")
        assert await world.db.fetch_value("SELECT COUNT(*) FROM pattern_proposals") == 0

    def test_a_proposal_needs_two_sources(self) -> None:
        with pytest.raises(ValueError):
            PatternProposalWrite(
                text="x", sources=[PatternSource(insight_id=1, set_id=1, set_name="A", text="t")]
            )


class TestOneRunAtATime:
    async def test_the_database_refuses_a_second_running_row(self, world: PatternWorld) -> None:
        runs = PatternRunsRepository(world.db)
        await runs.start(PatternRunStart())
        with pytest.raises(Exception, match="UNIQUE"):
            await runs.start(PatternRunStart())

    async def test_boot_marks_a_running_row_interrupted(self, world: PatternWorld) -> None:
        runs = PatternRunsRepository(world.db)
        run_id = await runs.start(PatternRunStart())

        assert await runs.reconcile_running() == 1

        row = await runs.get(run_id)
        assert row is not None and row.status == "interrupted" and row.error
        assert await runs.reconcile_running() == 0


class TestTheCountSinceTheLastRun:
    async def test_only_sets_that_count_and_only_confirmed_insights(
        self, world: PatternWorld
    ) -> None:
        runs = PatternRunsRepository(world.db)
        assert await runs.sets_with_insights() == 0
        await world.own(world.a, "One.")
        await world.own(world.a, "Waiting.", confirmed=False)
        await world.own(world.designing, "Never counts.")
        assert await runs.sets_with_insights() == 1
        assert await runs.new_since_last_run() == 1
        await world.own(world.b, "Two.")
        assert await runs.sets_with_insights() == 2
        assert await runs.new_since_last_run() == 2

    async def test_a_finished_run_resets_it_and_a_failed_one_does_not(
        self, world: PatternWorld
    ) -> None:
        runs = PatternRunsRepository(world.db)
        await world.own(world.a, "One.")
        await world.own(world.b, "Two.")
        failed = await runs.start(PatternRunStart())
        await runs.finish_failed(failed, PatternRunOutcome(status="failed", error="timeout: slow"))
        assert await runs.new_since_last_run() == 2

        await _finished(world)
        assert await runs.new_since_last_run() == 0

        later = await world.own(world.c, "Three.")
        # Confirmation after the run began is what counts, whatever the insight's age.
        await world.db.execute(
            "UPDATE knowledge_insights SET confirmed_at = '2999-01-01T00:00:00.000Z' WHERE id = ?",
            (later,),
        )
        assert await runs.new_since_last_run() == 1


class TestCopiesOfInsightTextLiveOneRun:
    """Proposals and inputs quote insights; once those are deleted they are the only copy."""

    async def test_a_finished_run_deletes_every_earlier_proposal_in_any_state(
        self, world: PatternWorld
    ) -> None:
        a, b, proposal = await _two(world)
        proposals = PatternProposalsRepository(world.db)
        first = await _finished(world, proposal, proposal, proposal)
        waiting, dismissed, approved = await proposals.for_run(first)
        await proposals.dismiss(dismissed.id)
        await proposals.approve(approved.id)
        assert await world.insights.get(a) is None and await world.insights.get(b) is None

        second = await _finished(world, proposal)

        assert await proposals.for_run(first) == []
        assert await proposals.get(waiting.id) is None
        assert [item.status for item in await proposals.for_run(second)] == ["proposed"]
        assert await proposals.declined() == []

    async def test_a_finished_run_blanks_every_earlier_runs_input_and_keeps_the_rows(
        self, world: PatternWorld
    ) -> None:
        runs = PatternRunsRepository(world.db)
        first = await _finished(world, seen={"sets": "one insight, in words"})
        failed = await runs.start(PatternRunStart(input={"sets": "a failed run's words"}))
        await runs.finish_failed(failed, PatternRunOutcome(status="failed", error="timeout: slow"))
        second = await _finished(world, seen={"sets": "newest words"})

        assert await runs.detail_input(first) == {}
        assert await runs.detail_input(failed) == {}
        assert await runs.detail_input(second) == {"sets": "newest words"}
        old = await runs.get(first)
        assert old is not None and old.status == "done" and old.finished_at
        assert await runs.get(failed) is not None

    async def test_a_failed_run_clears_nothing(self, world: PatternWorld) -> None:
        _a, _b, proposal = await _two(world)
        proposals = PatternProposalsRepository(world.db)
        runs = PatternRunsRepository(world.db)
        first = await _finished(world, proposal, seen={"sets": "words"})
        failed = await runs.start(PatternRunStart())
        await runs.finish_failed(failed, PatternRunOutcome(status="failed", error="timeout: slow"))

        assert [item.status for item in await proposals.for_run(first)] == ["proposed"]
        assert await runs.detail_input(first) == {"sets": "words"}

    async def test_dismissing_twice_or_an_unknown_one_is_refused(self, world: PatternWorld) -> None:
        _a, _b, proposal = await _two(world)
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        await proposals.dismiss(waiting.id)
        assert (await proposals.dismiss(waiting.id)).refused == "not_waiting"
        assert (await proposals.dismiss(9999)).refused == "no_proposal"

    async def test_the_deleted_text_is_in_no_pattern_row_after_one_more_run(
        self, world: PatternWorld
    ) -> None:
        """A dump of the whole file: after a deletion and one more finished run, no copy is left."""
        a, _b, proposal = await _two(world)
        words = "Below 9 clicks the Niche channels."
        proposals = PatternProposalsRepository(world.db)
        first = await _finished(world, proposal, seen={"sets": words})
        (waiting,) = await proposals.for_run(first)
        await proposals.approve(waiting.id)
        # Until the next run finishes the words survive in the newest run's rows only.
        found = await rows_containing(world.db, words)
        assert found and all(row.startswith("pattern_") for row in found), found
        assert await world.insights.get(a) is None

        await _finished(world)

        assert await rows_containing(world.db, words) == []


class TestApproval:
    async def test_it_writes_one_confirmed_general_insight_and_deletes_every_source(
        self, world: PatternWorld
    ) -> None:
        a, b, proposal = await _two(world)
        bystander = await world.own(world.c, "Huila likes it finer.")
        proposals = PatternProposalsRepository(world.db)
        run_id = await _finished(world, proposal)
        (waiting,) = await proposals.for_run(run_id)

        result = await proposals.approve(waiting.id)

        assert result.refused is None and result.skipped == []
        assert sorted(result.deleted) == sorted([a, b])
        assert await world.insights.get(a) is None and await world.insights.get(b) is None
        assert await world.insights.get(bystander) is not None
        general = await world.insights.list_insights()
        assert [(g.text, g.confirmed, g.pattern_run_id, g.scope.stated()) for g in general] == [
            (proposal.text, True, run_id, {"roast_level": "light"})
        ]
        done = await proposals.get(waiting.id)
        assert done is not None and done.status == "approved"
        assert done.insight_id == general[0].id
        # What the card needs after the sources are gone.
        assert [source.text for source in done.sources] == [s.text for s in proposal.sources]

    async def test_a_deleted_source_takes_its_waiting_deletion_card_with_it(
        self, world: PatternWorld
    ) -> None:
        a, _b, proposal = await _two(world)
        made = await ChatRepository(world.db).create_thread(ChatThreadWrite(set_id=world.a))
        assert made.thread is not None
        deletions = InsightDeletionsRepository(world.db)
        card = await deletions.propose(
            world.a,
            InsightDeletionWrite(
                thread_id=made.thread.id, insight_id=a, reason="The newest shots contradict it."
            ),
        )
        assert card.proposal is not None
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))

        await proposals.approve(waiting.id)

        stale = await deletions.get(world.a, card.proposal.id)
        assert stale is not None and (stale.status, stale.insight_text) == ("stale", "")

    async def test_it_deletes_the_general_insight_it_replaces(self, world: PatternWorld) -> None:
        _a, _b, proposal = await _two(world)
        old = await world.general("The Niche channels below 10.", roast_level="light")
        other = await world.general("Unrelated.", process="washed")
        proposal = proposal.model_copy(
            update={"replaces_id": old, "replaces_text": "The Niche channels below 10."}
        )
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))

        result = await proposals.approve(waiting.id)

        assert result.skipped == []
        assert await world.insights.get(old) is None
        assert await world.insights.get(other) is not None

    async def test_a_replaced_insight_already_gone_is_named_and_the_approval_still_succeeds(
        self, world: PatternWorld
    ) -> None:
        _a, _b, proposal = await _two(world)
        old = await world.general("The Niche channels below 10.", roast_level="light")
        proposal = proposal.model_copy(
            update={"replaces_id": old, "replaces_text": "The Niche channels below 10."}
        )
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        await world.insights.delete(old)

        result = await proposals.approve(waiting.id)

        assert [(item.reason, item.set_id) for item in result.skipped] == [("replaced_gone", None)]

    async def test_a_source_deleted_in_the_window_is_skipped_and_named(
        self, world: PatternWorld
    ) -> None:
        a, b, proposal = await _two(world)
        third = await world.own(world.c, "On dark roasts too.")
        proposal = proposal.model_copy(
            update={
                "scope": InsightScope(),
                "sources": [
                    *proposal.sources,
                    await _source(world, world.c, third, "On dark roasts too."),
                ],
            }
        )
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        await world.insights.delete(a)

        result = await proposals.approve(waiting.id)

        assert [(item.insight_id, item.reason) for item in result.skipped] == [(a, "gone")]
        assert sorted(result.deleted) == sorted([b, third])
        done = await proposals.get(waiting.id)
        assert done is not None and [item.reason for item in done.skipped] == ["gone"]

    async def test_a_source_unconfirmed_in_the_window_is_skipped_and_stays(
        self, world: PatternWorld
    ) -> None:
        a, b, proposal = await _two(world)
        third = await world.own(world.c, "On dark roasts too.")
        proposal = proposal.model_copy(
            update={
                "scope": InsightScope(),
                "sources": [
                    *proposal.sources,
                    await _source(world, world.c, third, "On dark roasts too."),
                ],
            }
        )
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        await world.insights.set_confirmed(a, False)

        result = await proposals.approve(waiting.id)

        assert [(item.insight_id, item.reason) for item in result.skipped] == [(a, "not_confirmed")]
        kept = await world.insights.get(a)
        assert kept is not None and not kept.confirmed
        assert await world.insights.get(b) is None

    async def test_a_source_whose_set_stopped_matching_the_scope_stays(
        self, world: PatternWorld
    ) -> None:
        """The live matching rule decides, not a copy taken when the run finished."""
        a, b, proposal = await _two(world)
        third = await world.own(world.c, "On dark roasts too.")
        light_c = await world.insights.attributes_of(world.c)
        assert light_c is not None and light_c["roast_level"] == "dark"
        proposal = proposal.model_copy(
            update={
                "scope": InsightScope(grinder_id=world.grinder_id),
                "sources": [
                    *proposal.sources,
                    await _source(world, world.c, third, "On dark roasts too."),
                ],
            }
        )
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        # C's grinder is cleared after the run: the scope no longer reaches it.
        await world.db.execute("UPDATE sets SET grinder_id = NULL WHERE id = ?", (world.c,))

        result = await proposals.approve(waiting.id)

        assert [(item.insight_id, item.reason) for item in result.skipped] == [
            (third, "scope_changed")
        ]
        assert await world.insights.get(third) is not None
        assert await world.insights.get(a) is None and await world.insights.get(b) is None

    async def test_fewer_than_two_sets_left_refuses_and_writes_nothing(
        self, world: PatternWorld
    ) -> None:
        a, b, proposal = await _two(world)
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        await world.insights.delete(a)
        before = await _dump_insights(world)

        result = await proposals.approve(waiting.id)

        assert result.refused == "too_few_sets"
        assert [item.reason for item in result.skipped] == ["gone"]
        assert await _dump_insights(world) == before
        still = await proposals.get(waiting.id)
        assert still is not None and still.status == "proposed"
        assert await world.insights.get(b) is not None

    async def test_two_insights_of_one_set_are_one_set(self, world: PatternWorld) -> None:
        a1, _b, proposal = await _two(world)
        a2 = await world.own(world.a, "Also in A.")
        proposal = proposal.model_copy(
            update={
                "sources": [proposal.sources[0], await _source(world, world.a, a2, "Also in A.")]
            }
        )
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))

        assert (await proposals.approve(waiting.id)).refused == "too_few_sets"
        assert await world.insights.get(a1) is not None

    async def test_it_is_atomic(self, world: PatternWorld, monkeypatch: pytest.MonkeyPatch) -> None:
        """A delete that fails after the general insight was written leaves nothing behind."""
        a, b, proposal = await _two(world)
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        before = await _dump_insights(world)
        real = proposals.insights.delete_in_transaction
        calls: list[int] = []

        async def failing(insight_id: int, **kwargs: object) -> bool:
            calls.append(insight_id)
            if len(calls) == 2:
                raise RuntimeError("disk full")
            return await real(insight_id, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(proposals.insights, "delete_in_transaction", failing)

        with pytest.raises(RuntimeError, match="disk full"):
            await proposals.approve(waiting.id)

        assert len(calls) == 2
        assert await _dump_insights(world) == before
        assert await world.insights.get(a) is not None and await world.insights.get(b) is not None
        still = await proposals.get(waiting.id)
        assert still is not None and still.status == "proposed" and still.insight_id is None

    async def test_a_decided_proposal_cannot_be_approved_again(self, world: PatternWorld) -> None:
        _a, _b, proposal = await _two(world)
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        await proposals.approve(waiting.id)

        assert (await proposals.approve(waiting.id)).refused == "not_waiting"
        assert (await proposals.approve(9999)).refused == "no_proposal"
        assert len(await world.insights.list_insights()) == 1

    async def test_the_last_write_failing_undoes_everything(
        self, world: PatternWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The proposal's status update is the final write: it has to be inside the transaction."""
        a, b, proposal = await _two(world)
        old = await world.general("The Niche channels below 10.", roast_level="light")
        proposal = proposal.model_copy(
            update={"replaces_id": old, "replaces_text": "The Niche channels below 10."}
        )
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        before = await _dump_insights(world)
        real = world.db.execute

        async def execute(sql: str, *args: object, **kwargs: object) -> object:
            if "SET status = 'approved'" in sql:
                raise RuntimeError("the last write failed")
            return await real(sql, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(world.db, "execute", execute)
        with pytest.raises(RuntimeError, match="the last write failed"):
            await proposals.approve(waiting.id)
        monkeypatch.setattr(world.db, "execute", real)

        assert await _dump_insights(world) == before
        for insight_id in (a, b, old):
            assert await world.insights.get(insight_id) is not None
        still = await proposals.get(waiting.id)
        assert still is not None and still.status == "proposed" and still.insight_id is None

    async def test_the_scope_recheck_is_the_live_matching_rule(self, world: PatternWorld) -> None:
        """`scope_matches` ignores case and spacing, so a case-variant scope is not skipped.

        An `==` in its place would call the Set's "Ethiopia" a mismatch for " ethiopia " and
        leave both sources behind.
        """
        a, b, proposal = await _two(world)
        proposal = proposal.model_copy(update={"scope": InsightScope(origin=" ethiopia ")})
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))

        result = await proposals.approve(waiting.id)

        assert result.skipped == [] and sorted(result.deleted) == sorted([a, b])

    async def test_the_written_insight_is_the_persons_and_placement_leaves_it_general(
        self, world: PatternWorld
    ) -> None:
        _a, _b, proposal = await _two(world)
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        result = await proposals.approve(waiting.id)
        assert result.proposal is not None and result.proposal.insight_id is not None
        general_id = result.proposal.insight_id
        written = await world.db.fetch_one(
            "SELECT source, set_id, confirmed FROM knowledge_insights WHERE id = ?", (general_id,)
        )
        assert written is not None
        assert (written["source"], written["set_id"], written["confirmed"]) == ("user", None, 1)

        # The one-time placement, run again as if its marker had never been written.
        await world.db.execute("DELETE FROM insight_placement_build")
        await InsightPlacementBuilder(world.db).build()

        after = await world.db.fetch_one(
            "SELECT set_id, set_version_id FROM knowledge_insights WHERE id = ?", (general_id,)
        )
        assert after is not None
        assert (after["set_id"], after["set_version_id"]) == (None, None)


class TestTheCounts:
    async def test_the_since_count_runs_from_the_last_finished_runs_start(
        self, world: PatternWorld
    ) -> None:
        runs = PatternRunsRepository(world.db)
        before = await world.own(world.a, "Confirmed before the run began.")
        during = await world.own(world.b, "Confirmed while the run was reading.")
        run_id = await _finished(world)
        for insight_id, stamp in ((before, "01"), (during, "03")):
            await world.db.execute(
                "UPDATE knowledge_insights SET confirmed_at = ? WHERE id = ?",
                (f"2026-01-01T00:00:{stamp}.000Z", insight_id),
            )
        await world.db.execute(
            "UPDATE pattern_runs SET created_at = '2026-01-01T00:00:02.000Z', "
            "finished_at = '2026-01-01T00:00:05.000Z' WHERE id = ?",
            (run_id,),
        )

        # Confirmed after the run *began* counts even though it was before the run *ended*:
        # the run may not have read it.
        assert await runs.new_since_last_run() == 1

    async def test_only_confirmed_insights_count_a_set(self, world: PatternWorld) -> None:
        runs = PatternRunsRepository(world.db)
        await world.own(world.a, "Waiting only.", confirmed=False)
        dismissed = await world.own(world.b, "Dismissed only.", confirmed=False)
        await world.insights.dismiss(dismissed)
        assert await runs.sets_with_insights() == 0
        assert await runs.new_since_last_run() == 0

        await world.own(world.c, "Confirmed.")
        assert await runs.sets_with_insights() == 1


class TestNoAnswerWhileARunIsGoing:
    """A finishing run deletes every earlier proposal, so an answer made during it is lost."""

    async def test_approve_and_dismiss_are_refused_and_change_nothing(
        self, world: PatternWorld
    ) -> None:
        a, b, proposal = await _two(world)
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        before = await _dump_insights(world)
        runs = PatternRunsRepository(world.db)
        going = await runs.start(PatternRunStart())

        approved = await proposals.approve(waiting.id)
        dismissed = await proposals.dismiss(waiting.id)

        assert (approved.refused, dismissed.refused) == ("run_going", "run_going")
        assert await _dump_insights(world) == before
        still = await proposals.get(waiting.id)
        assert still is not None and still.status == "proposed"
        # Once the run is over the same card can be answered.
        await runs.finish_failed(going, PatternRunOutcome(status="failed", error="timeout: slow"))
        assert (await proposals.dismiss(waiting.id)).refused is None
        assert await world.insights.get(a) is not None and await world.insights.get(b) is not None

    async def test_a_decided_proposal_is_still_told_as_decided_during_a_run(
        self, world: PatternWorld
    ) -> None:
        _a, _b, proposal = await _two(world)
        proposals = PatternProposalsRepository(world.db)
        (waiting,) = await proposals.for_run(await _finished(world, proposal))
        await proposals.dismiss(waiting.id)
        await PatternRunsRepository(world.db).start(PatternRunStart())

        assert (await proposals.dismiss(waiting.id)).refused == "not_waiting"


class TestClearingIsInsideTheFinishingTransaction:
    async def test_a_failing_last_write_leaves_the_earlier_run_and_the_new_one_untouched(
        self, world: PatternWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The clearing (earlier proposals deleted, earlier inputs blanked) is part of the
        finish: if its last write fails, nothing of the finish stays, and the earlier run is
        exactly as it was."""
        _a, _b, proposal = await _two(world)
        proposals = PatternProposalsRepository(world.db)
        runs = PatternRunsRepository(world.db)
        first = await _finished(world, proposal, seen={"sets": "earlier words"})
        second = await runs.start(PatternRunStart(input={"sets": "newer words"}))
        real = world.db.execute

        async def execute(sql: str, *args: object, **kwargs: object) -> object:
            if "SET input_json = '{}'" in sql:
                raise RuntimeError("the last write failed")
            return await real(sql, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(world.db, "execute", execute)
        with pytest.raises(RuntimeError, match="the last write failed"):
            await runs.finish_done(second, DONE, [proposal])
        monkeypatch.setattr(world.db, "execute", real)

        assert [item.status for item in await proposals.for_run(first)] == ["proposed"]
        assert await proposals.for_run(second) == []
        assert await runs.detail_input(first) == {"sets": "earlier words"}
        row = await runs.get(second)
        assert row is not None and row.status == "running"
