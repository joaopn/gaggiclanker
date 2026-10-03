"""What a conversation is told about the insights it proposed and the deletions it asked for.

The record at the end of the opening context is how the agent learns what the person
did with a card (nothing is sent as a message), so each state of each kind is pinned
here in the words the agent reads, and the property the feature rests on: after an
insight is removed, every other prompt for the Set is byte-for-byte what it was before
the insight existed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.chat.context import opening_context
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.insight_deletions import InsightDeletionWrite
from gaggiclanker.db.repos.knowledge_insights import InsightWrite
from gaggiclanker.tools.scope import ToolScope
from tests.knowledge.insight_world import ProvenanceWorld, build_provenance_world

REASON = "The two newest shots contradict it on every measure."
INSIGHTS = "INSIGHTS YOU PROPOSED IN THIS CONVERSATION"
DELETIONS = "INSIGHT DELETIONS YOU PROPOSED IN THIS CONVERSATION"


@pytest.fixture
async def provenance(tmp_path: Path) -> AsyncIterator[ProvenanceWorld]:
    db = Database(tmp_path / "records.db")
    await db.connect()
    await run_migrations(db)
    try:
        yield await build_provenance_world(db)
    finally:
        await db.close()


@pytest.fixture
def scope(provenance: ProvenanceWorld) -> ToolScope:
    return ToolScope.for_thread(provenance.set_id, provenance.v3)


async def _context(world: ProvenanceWorld, scope: ToolScope, thread: int | None) -> str:
    return await opening_context(world.db, scope, thread_id=thread)


def _block(text: str, heading: str) -> list[str]:
    if heading not in text:
        return []
    lines = text.split(heading + "\n", 1)[1].split("\n\n", 1)[0]
    return lines.splitlines()


async def _propose(
    world: ProvenanceWorld, text: str, *, replaces: int | None = None, thread: int | None = None
) -> int:
    result = await world.insights.propose(
        InsightWrite(
            text=text,
            source="chat",
            set_id=world.set_id,
            set_version_id=world.v3,
            thread_id=world.thread if thread is None else thread,
            replaces_id=replaces,
        ),
        version_ids=[world.v1],
    )
    assert result.insight_id is not None
    return result.insight_id


class TestWhatItProposedAsInsights:
    async def test_each_state_is_told_in_its_own_words(
        self, provenance: ProvenanceWorld, scope: ToolScope
    ) -> None:
        world = provenance
        await _propose(world, "Waiting claim.")
        dismissed = await _propose(world, "Dismissed claim.")
        await world.insights.dismiss(dismissed)
        added = await _propose(world, "Added claim.")
        await world.insights.set_confirmed(added, True)
        old = await world.own("Old claim.", thread_id=world.other_thread)
        replacement = await _propose(world, "Replacing claim.", replaces=old)
        await world.insights.set_confirmed(replacement, True)
        stale_old = await world.own("Other old claim.", thread_id=world.other_thread)
        overtaken = await _propose(world, "Overtaken claim.", replaces=stale_old)
        await world.insights.delete(stale_old)
        await world.insights.set_confirmed(overtaken, True)
        waiting_replacement_target = await world.own("Target.", thread_id=world.other_thread)
        await _propose(world, "Would replace.", replaces=waiting_replacement_target)

        lines = _block(await _context(world, scope, world.thread), INSIGHTS)

        assert len(lines) == 6
        by_text = {line[2:].split(" (", 1)[0]: line for line in lines}
        assert "waiting: the person has not answered" in by_text["Waiting claim."]
        assert "dismissed by the person — do not offer it again" in by_text["Dismissed claim."]
        assert "added by the person — it is in the confirmed list above" in by_text["Added claim."]
        assert (
            "added by the person, and it replaced the old insight, which was deleted"
            in by_text["Replacing claim."]
        )
        assert (
            "the insight it was meant to replace had already changed, so nothing was deleted"
            in by_text["Overtaken claim."]
        )
        assert f"adding it would delete #{waiting_replacement_target}" in by_text["Would replace."]

    async def test_an_insight_the_person_later_deleted_no_longer_appears(
        self, provenance: ProvenanceWorld, scope: ToolScope
    ) -> None:
        added = await _propose(provenance, "Added claim.")
        await provenance.insights.set_confirmed(added, True)
        assert "Added claim." in await _context(provenance, scope, provenance.thread)

        await provenance.insights.delete(added)

        text = await _context(provenance, scope, provenance.thread)
        assert "Added claim." not in text
        assert INSIGHTS not in text


class TestWhatItProposedDeleting:
    async def _propose(self, world: ProvenanceWorld, target: int, reason: str = REASON) -> int:
        result = await world.deletions.propose(
            world.set_id,
            InsightDeletionWrite(thread_id=world.thread, insight_id=target, reason=reason),
        )
        assert result.proposal is not None
        return result.proposal.id

    async def test_each_state_is_told_in_its_own_words(
        self, provenance: ProvenanceWorld, scope: ToolScope
    ) -> None:
        world = provenance
        waiting = await world.own("Waiting for the press.")
        deleted = await world.own("Deleted by the press.")
        kept = await world.own("Kept by the press.")
        gone = await world.own("Gone another way.")
        await self._propose(world, waiting)
        await world.deletions.accept(world.set_id, await self._propose(world, deleted))
        await world.deletions.keep(world.set_id, await self._propose(world, kept))
        await self._propose(world, gone)
        await world.insights.delete(gone)

        lines = _block(await _context(world, scope, world.thread), DELETIONS)

        assert lines == [
            f"- #{waiting} Waiting for the press. (waiting: nothing is deleted until the "
            "person presses Delete)",
            "- Deleted by the press. (deleted by the person)",
            f"- #{kept} Kept by the press. (kept by the person)",
            "- an insight that has since been removed (already gone, nothing to do)",
        ]

    async def test_a_kept_proposal_whose_insight_was_deleted_since_says_already_gone(
        self, provenance: ProvenanceWorld, scope: ToolScope
    ) -> None:
        target = await provenance.own("Kept, then deleted: zyxwvut.")
        await provenance.deletions.keep(provenance.set_id, await self._propose(provenance, target))
        assert "(kept by the person)" in await _context(provenance, scope, provenance.thread)

        await provenance.insights.delete(target)

        text = await _context(provenance, scope, provenance.thread)
        assert "kept by the person" not in text and "zyxwvut" not in text
        assert "(already gone, nothing to do)" in text

    async def test_a_kept_one_carries_nothing_but_the_answer(
        self, provenance: ProvenanceWorld, scope: ToolScope
    ) -> None:
        target = await provenance.own("Kept claim.")
        await provenance.deletions.keep(provenance.set_id, await self._propose(provenance, target))

        text = await _context(provenance, scope, provenance.thread)

        assert REASON not in text

    async def test_a_superseded_proposal_is_not_told(
        self, provenance: ProvenanceWorld, scope: ToolScope
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        await self._propose(provenance, target, "A first reason, long enough to count.")
        await self._propose(provenance, target, "A second reason, long enough to count.")

        lines = _block(await _context(provenance, scope, provenance.thread), DELETIONS)

        assert len(lines) == 1 and "waiting" in lines[0]

    async def test_it_is_told_to_its_own_conversation_only(
        self, provenance: ProvenanceWorld, scope: ToolScope
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        await self._propose(provenance, target)

        assert DELETIONS in await _context(provenance, scope, provenance.thread)
        assert DELETIONS not in await _context(provenance, scope, provenance.other_thread)
        assert DELETIONS not in await _context(provenance, scope, None)


class TestRemovedMeansRemoved:
    """Every prompt for the Set, bar the proposing conversation's own record, is unchanged."""

    async def _prompts(self, world: ProvenanceWorld, scope: ToolScope) -> list[str]:
        return [
            await _context(world, scope, world.other_thread),
            await _context(world, scope, None),
        ]

    async def _doomed(self, world: ProvenanceWorld) -> tuple[int, int]:
        target = await world.own("Doomed claim, with a rare phrase: zyxwvut.")
        proposal = (
            await world.deletions.propose(
                world.set_id,
                InsightDeletionWrite(thread_id=world.thread, insight_id=target, reason=REASON),
            )
        ).proposal
        assert proposal is not None
        return target, proposal.id

    async def test_after_the_persons_delete(
        self, provenance: ProvenanceWorld, scope: ToolScope
    ) -> None:
        before = await self._prompts(provenance, scope)
        target, _ = await self._doomed(provenance)
        assert "zyxwvut" in (await self._prompts(provenance, scope))[0]

        await provenance.insights.delete(target)

        assert await self._prompts(provenance, scope) == before

    async def test_after_an_accepted_deletion(
        self, provenance: ProvenanceWorld, scope: ToolScope
    ) -> None:
        before = await self._prompts(provenance, scope)
        _, proposal = await self._doomed(provenance)

        await provenance.deletions.accept(provenance.set_id, proposal)

        assert await self._prompts(provenance, scope) == before
        # The conversation that asked is told what went; nobody else is.
        assert "Doomed claim" in await _context(provenance, scope, provenance.thread)

    async def test_after_a_replacements_add(
        self, provenance: ProvenanceWorld, scope: ToolScope
    ) -> None:
        old = await provenance.own("Doomed claim, with a rare phrase: zyxwvut.")
        new = await _propose(provenance, "The newer claim.", replaces=old)
        await provenance.insights.set_confirmed(new, True)

        for text in await self._prompts(provenance, scope):
            assert "zyxwvut" not in text and "The newer claim." in text
        assert "zyxwvut" not in await _context(provenance, scope, provenance.thread)
