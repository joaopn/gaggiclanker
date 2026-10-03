"""An agent's proposal to delete an added insight waits for the person, and only they remove it.

One waiting proposal per insight (a partial unique index, since the stdio child is a
second process), a newer one replaces it, every answer is one transaction, and what
may be named is an added insight of the same Set.
"""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from gaggiclanker.db.repos.insight_deletions import InsightDeletionWrite
from gaggiclanker.db.repos.knowledge_insights import InsightWrite
from tests.knowledge.insight_world import ProvenanceWorld

REASON = "The two newest shots contradict it on every measure."


def _write(world: ProvenanceWorld, insight_id: int, reason: str = REASON) -> InsightDeletionWrite:
    return InsightDeletionWrite(thread_id=world.thread, insight_id=insight_id, reason=reason)


class TestProposing:
    async def test_a_proposal_waits_and_deletes_nothing(self, provenance: ProvenanceWorld) -> None:
        target = await provenance.own("Doubtful claim.")

        result = await provenance.deletions.propose(provenance.set_id, _write(provenance, target))

        assert result.refused is None and result.proposal is not None
        assert result.proposal.status == "proposed"
        assert (result.proposal.insight_id, result.proposal.insight_text) == (
            target,
            "Doubtful claim.",
        )
        kept = await provenance.insights.get(target)
        assert kept is not None and kept.confirmed

    @pytest.mark.parametrize("reason", ["too short", "x" * 501])
    def test_the_reason_is_twenty_to_five_hundred_characters(self, reason: str) -> None:
        with pytest.raises(ValidationError):
            InsightDeletionWrite(thread_id=1, insight_id=1, reason=reason)

    @pytest.mark.parametrize(
        ("case", "refusal"),
        [
            ("waiting", "not_added"),
            ("dismissed", "not_added"),
            ("general", "general"),
            ("other_set", "bad_insight"),
            ("missing", "bad_insight"),
        ],
    )
    async def test_it_refuses_what_may_not_be_named(
        self, provenance: ProvenanceWorld, case: str, refusal: str
    ) -> None:
        if case == "waiting":
            target = await provenance.own("Waiting.", confirmed=False)
        elif case == "dismissed":
            target = await provenance.own("Dismissed.", confirmed=False)
            await provenance.insights.dismiss(target)
        elif case == "general":
            target = await provenance.insights.insert(
                InsightWrite(text="General.", source="user", confirmed=True)
            )
        elif case == "other_set":
            target = await provenance.own(
                "Theirs.", set_id=provenance.stranger_set, version_id=provenance.stranger_version
            )
        else:
            target = 9999

        result = await provenance.deletions.propose(provenance.set_id, _write(provenance, target))

        assert (result.refused, result.proposal) == (refusal, None)

    async def test_the_conversation_must_be_this_sets(self, provenance: ProvenanceWorld) -> None:
        target = await provenance.own("Doubtful claim.")
        result = await provenance.deletions.propose(
            provenance.set_id,
            InsightDeletionWrite(
                thread_id=provenance.stranger_thread, insight_id=target, reason=REASON
            ),
        )
        assert result.refused == "bad_thread"

    async def test_a_newer_proposal_supersedes_the_waiting_one(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        first = (
            await provenance.deletions.propose(provenance.set_id, _write(provenance, target))
        ).proposal
        assert first is not None

        second = await provenance.deletions.propose(
            provenance.set_id, _write(provenance, target, "A better reason, written later on.")
        )

        assert second.replaced is not None and second.replaced.id == first.id
        old = await provenance.deletions.get(provenance.set_id, first.id)
        assert old is not None and old.status == "superseded"
        waiting = await provenance.deletions.waiting_for_set(provenance.set_id)
        assert [row.id for row in waiting] == [second.proposal.id]  # type: ignore[union-attr]

    async def test_one_waits_per_insight_whatever_the_repository_says(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        await provenance.deletions.propose(provenance.set_id, _write(provenance, target))
        with pytest.raises(sqlite3.IntegrityError):
            await provenance.db.execute(
                "INSERT INTO set_insight_deletions "
                "(set_id, thread_id, insight_id, insight_text, reason) VALUES (?, ?, ?, 'x', ?)",
                (provenance.set_id, provenance.thread, target, REASON),
            )


class TestAnswering:
    async def test_delete_removes_the_insight_and_keeps_only_its_text_on_the_card(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        proposal = (
            await provenance.deletions.propose(provenance.set_id, _write(provenance, target))
        ).proposal
        assert proposal is not None

        result = await provenance.deletions.accept(provenance.set_id, proposal.id)

        assert result.refused is None and result.proposal is not None
        assert result.proposal.status == "deleted"
        assert result.proposal.insight_id is None
        assert result.proposal.insight_text == "Doubtful claim."
        assert await provenance.insights.get(target) is None

    async def test_keep_leaves_the_insight_exactly_as_it_was(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        before = await provenance.insights.get(target)
        proposal = (
            await provenance.deletions.propose(provenance.set_id, _write(provenance, target))
        ).proposal
        assert proposal is not None

        result = await provenance.deletions.keep(provenance.set_id, proposal.id)

        assert result.proposal is not None and result.proposal.status == "kept"
        assert await provenance.insights.get(target) == before

    async def test_an_answered_proposal_cannot_be_answered_again(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        proposal = (
            await provenance.deletions.propose(provenance.set_id, _write(provenance, target))
        ).proposal
        assert proposal is not None
        await provenance.deletions.keep(provenance.set_id, proposal.id)

        for answer in (provenance.deletions.accept, provenance.deletions.keep):
            result = await answer(provenance.set_id, proposal.id)
            assert result.refused == "not_waiting"
        assert await provenance.insights.get(target) is not None

    async def test_an_unknown_or_foreign_proposal_is_not_found(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        proposal = (
            await provenance.deletions.propose(provenance.set_id, _write(provenance, target))
        ).proposal
        assert proposal is not None
        assert (await provenance.deletions.accept(provenance.set_id, 9999)).refused == "no_proposal"
        assert (
            await provenance.deletions.accept(provenance.stranger_set, proposal.id)
        ).refused == "no_proposal"
        assert await provenance.insights.get(target) is not None

    async def test_an_insight_taken_back_since_is_not_deleted(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        proposal = (
            await provenance.deletions.propose(provenance.set_id, _write(provenance, target))
        ).proposal
        assert proposal is not None
        # Straight into the table: taking it back through the repository already stales the
        # proposal (the next test), so this is the guard for a write that did not.
        await provenance.db.execute(
            "UPDATE knowledge_insights SET confirmed = 0 WHERE id = ?", (target,)
        )

        result = await provenance.deletions.accept(provenance.set_id, proposal.id)

        assert result.refused == "insight_changed"
        assert await provenance.insights.get(target) is not None
        stored = await provenance.deletions.get(provenance.set_id, proposal.id)
        assert stored is not None and stored.status == "stale"

    async def test_a_failure_part_way_leaves_the_proposal_waiting(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        proposal = (
            await provenance.deletions.propose(provenance.set_id, _write(provenance, target))
        ).proposal
        assert proposal is not None

        async def boom(insight_id: int, **_: object) -> bool:
            raise RuntimeError("disk full")

        provenance.deletions.insights.delete_in_transaction = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await provenance.deletions.accept(provenance.set_id, proposal.id)

        stored = await provenance.deletions.get(provenance.set_id, proposal.id)
        assert stored is not None and stored.status == "proposed"
        assert await provenance.insights.get(target) is not None

    async def test_the_conversations_proposals_leave_with_the_conversation(
        self, provenance: ProvenanceWorld
    ) -> None:
        from gaggiclanker.db.repos.chat import ChatRepository

        target = await provenance.own("Doubtful claim.")
        await provenance.deletions.propose(provenance.set_id, _write(provenance, target))

        await ChatRepository(provenance.db).delete_thread(provenance.thread)

        assert await provenance.deletions.for_set(provenance.set_id) == []
        # The insight itself is the person's and stays.
        assert await provenance.insights.get(target) is not None

    async def test_taking_the_insight_back_makes_the_card_say_so_rather_than_offer_a_delete(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        proposal = (
            await provenance.deletions.propose(provenance.set_id, _write(provenance, target))
        ).proposal
        assert proposal is not None
        await provenance.insights.set_confirmed(target, False)

        result = await provenance.deletions.accept(provenance.set_id, proposal.id)

        assert result.refused == "not_waiting"
        assert await provenance.insights.get(target) is not None
