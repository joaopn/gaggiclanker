"""`record_insight` with what it rests on and what it replaces, and `propose_insight_deletion`.

Through the dispatcher, as the chat and the stdio server both use it. What these pin:
every refusal has a sentence of its own that says what to do instead, nothing is
deleted by calling a tool (the person's press is the only thing that removes an
added insight), the deletion tool exists in a Set's conversation only, and what the
agent reads about each insight is one line, built in one place.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.knowledge.insight_world import ProvenanceWorld, build_provenance_world
from tests.review.conftest import Fixture
from tests.tools.conftest import _context

REASON = "The two newest shots contradict it on every measure."


async def call(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert outcome.ok, outcome.data
    return outcome.data


async def refuse(ctx: ToolContext, name: str, **arguments: Any) -> str:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert not outcome.ok, "this call should have been refused"
    return str(outcome.data["detail"])


@pytest.fixture
async def world(archive: Fixture) -> ProvenanceWorld:
    return await build_provenance_world(archive.db)


@pytest.fixture
def conversation(
    archive: Fixture, settings: SettingsService, world: ProvenanceWorld
) -> ToolContext:
    """A conversation about the Set's newest version, with a thread of its own."""
    ctx = _context(archive, settings, ToolScope.for_thread(world.set_id, world.v3))
    ctx.thread_id = world.thread
    return ctx


class TestRestingOn:
    async def test_versions_are_named_the_way_the_record_names_them(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        data = await call(
            conversation,
            "record_insight",
            text="Finer lengthens it.",
            rests_on_versions=["v1", "v1.1"],
            evidence_shot_ids=[world.shot1],
        )

        stored = await world.insights.get(data["insight_id"])
        assert stored is not None
        assert [(item.label, item.outcome_then) for item in stored.rests_on] == [
            ("v1", "held"),
            ("v1.1", "held"),
        ]
        assert data["rests_on"] == ["v1 held", "v1.1 held"]
        assert stored.confirmed is False and stored.thread_id == world.thread

    async def test_an_unknown_version_names_the_versions_there_are(
        self, conversation: ToolContext
    ) -> None:
        detail = await refuse(conversation, "record_insight", text="x", rests_on_versions=["v9"])
        assert "no version called 'v9'" in detail and "v1, v1.1, v1.2" in detail
        # Its own sentence: this tool has no `version` argument to leave out.
        assert "leave version out" not in detail and "rests_on_versions" in detail

    async def test_another_sets_version_is_unknown_here(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        # The stranger Set's only version is v1 (failed): by name it is this Set's v1,
        # and a name only another Set has is simply not a version of this one.
        stranger = await world.sets.versions(world.stranger_set)
        assert [v.version_label for v in stranger] == ["v1"]
        detail = await refuse(conversation, "record_insight", text="x", rests_on_versions=["v2"])
        assert "no version called 'v2'" in detail

    async def test_a_version_with_no_recorded_outcome_says_what_to_do_instead(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        detail = await refuse(conversation, "record_insight", text="x", rests_on_versions=["v1.2"])
        assert detail == (
            "v1.2 has no recorded outcome yet: name shots instead, or wait until the person "
            "records its grade."
        )
        assert await world.insights.own(world.set_id, include_dismissed=True) == []

    async def test_it_has_to_rest_on_something(self, conversation: ToolContext) -> None:
        detail = await refuse(conversation, "record_insight", text="A bare claim.")
        assert "has to rest on something" in detail
        assert "evidence_shot_ids" in detail and "rests_on_versions" in detail

    async def test_another_sets_shot_is_still_refused(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        other = await world.sets.shot_ids(world.stranger_set)
        detail = await refuse(
            conversation, "record_insight", text="x", evidence_shot_ids=sorted(other)
        )
        assert "not a shot of this Set" in detail


class TestReplacing:
    async def _added(self, world: ProvenanceWorld, text: str = "Old claim.") -> int:
        return await world.own(text, thread_id=world.other_thread)

    async def test_it_names_an_added_insight_and_deletes_nothing(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        old = await self._added(world)

        data = await call(
            conversation,
            "record_insight",
            text="New claim.",
            evidence_shot_ids=[world.shot1],
            replaces_insight_id=old,
        )

        assert data["replaces_insight_id"] == old and data["confirmed"] is False
        assert f"Adding it deletes insight #{old}" in data["note"]
        survivor = await world.insights.get(old)
        assert survivor is not None and survivor.confirmed

    async def test_it_refuses_a_waiting_one_with_its_own_sentence(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        waiting = await world.own("Waiting.", confirmed=False)
        detail = await refuse(
            conversation,
            "record_insight",
            text="x",
            evidence_shot_ids=[world.shot1],
            replaces_insight_id=waiting,
        )
        assert f"Insight #{waiting} is not an added insight" in detail
        assert "waiting or dismissed" in detail

    async def test_it_refuses_a_dismissed_one(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        dismissed = await world.own("Dismissed.", confirmed=False)
        await world.insights.dismiss(dismissed)
        detail = await refuse(
            conversation,
            "record_insight",
            text="x",
            evidence_shot_ids=[world.shot1],
            replaces_insight_id=dismissed,
        )
        assert "is not an added insight" in detail

    async def test_it_refuses_a_general_one(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        from gaggiclanker.db.repos.knowledge_insights import InsightWrite

        general = await world.insights.insert(
            InsightWrite(text="General.", source="user", confirmed=True)
        )
        detail = await refuse(
            conversation,
            "record_insight",
            text="x",
            evidence_shot_ids=[world.shot1],
            replaces_insight_id=general,
        )
        assert "is general knowledge" in detail and "Knowledge page" in detail

    async def test_it_refuses_another_sets_and_a_missing_one_alike(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        theirs = await world.own(
            "Theirs.", set_id=world.stranger_set, version_id=world.stranger_version
        )
        sentences = []
        for target in (theirs, 99_999):
            sentences.append(
                await refuse(
                    conversation,
                    "record_insight",
                    text="x",
                    evidence_shot_ids=[world.shot1],
                    replaces_insight_id=target,
                )
            )
        assert all("is not an insight of this Set" in sentence for sentence in sentences)
        # The wording does not tell the two apart: no way to ask whether an id exists.
        masked = {
            sentence.replace(str(theirs), "N").replace("99999", "N") for sentence in sentences
        }
        assert len(masked) == 1
        assert await world.insights.get(theirs) is not None


class TestProposingADeletion:
    async def test_it_waits_and_deletes_nothing(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        target = await world.own("Doubtful claim.")

        data = await call(
            conversation, "propose_insight_deletion", insight_id=target, reason=REASON
        )

        assert data["status"] == "proposed" and data["insight_text"] == "Doubtful claim."
        assert "Nothing is deleted" in data["note"] and "presses Delete" in data["note"]
        assert await world.insights.get(target) is not None
        waiting = await world.deletions.waiting_for_set(world.set_id)
        assert [(row.insight_id, row.thread_id, row.reason) for row in waiting] == [
            (target, world.thread, REASON)
        ]

    async def test_a_second_call_replaces_the_waiting_one(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        target = await world.own("Doubtful claim.")
        await call(conversation, "propose_insight_deletion", insight_id=target, reason=REASON)

        data = await call(
            conversation,
            "propose_insight_deletion",
            insight_id=target,
            reason="Better words, written a moment later on.",
        )

        assert data["replaced_waiting_proposal"] is True
        assert len(await world.deletions.waiting_for_set(world.set_id)) == 1

    @pytest.mark.parametrize("reason", ["", "too short", "x" * 19])
    async def test_a_short_reason_is_refused_with_what_a_reason_is(
        self, conversation: ToolContext, world: ProvenanceWorld, reason: str
    ) -> None:
        target = await world.own("Doubtful claim.")
        detail = await refuse(
            conversation, "propose_insight_deletion", insight_id=target, reason=reason
        )
        assert "A deletion needs its reason" in detail and "at least 20" in detail
        assert await world.deletions.for_set(world.set_id) == []

    async def test_a_long_reason_is_refused(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        target = await world.own("Doubtful claim.")
        detail = await refuse(
            conversation, "propose_insight_deletion", insight_id=target, reason="x" * 501
        )
        assert "too long" in detail and "500" in detail

    async def test_waiting_dismissed_general_and_foreign_insights_each_have_a_sentence(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        from gaggiclanker.db.repos.knowledge_insights import InsightWrite

        waiting = await world.own("Waiting.", confirmed=False)
        dismissed = await world.own("Dismissed.", confirmed=False)
        await world.insights.dismiss(dismissed)
        general = await world.insights.insert(
            InsightWrite(text="General.", source="user", confirmed=True)
        )
        theirs = await world.own(
            "Theirs.", set_id=world.stranger_set, version_id=world.stranger_version
        )

        details = {
            name: await refuse(
                conversation, "propose_insight_deletion", insight_id=target, reason=REASON
            )
            for name, target in (
                ("waiting", waiting),
                ("dismissed", dismissed),
                ("general", general),
                ("theirs", theirs),
                ("missing", 99_999),
            )
        }

        assert "is not an added insight" in details["waiting"]
        assert "is not an added insight" in details["dismissed"]
        assert "is general knowledge" in details["general"]
        assert "is not an insight of this Set" in details["theirs"]
        assert "is not an insight of this Set" in details["missing"]
        assert await world.deletions.for_set(world.set_id) == []
        assert await world.deletions.for_set(world.stranger_set) == []

    async def test_a_session_with_no_conversation_has_nowhere_for_the_card(
        self, archive: Fixture, settings: SettingsService, world: ProvenanceWorld
    ) -> None:
        bare = _context(archive, settings, ToolScope.for_thread(world.set_id, world.v3))
        target = await world.own("Doubtful claim.")
        detail = await refuse(bare, "propose_insight_deletion", insight_id=target, reason=REASON)
        assert "no conversation" in detail

    async def test_it_is_a_set_conversation_tool_only(
        self, archive: Fixture, settings: SettingsService, world: ProvenanceWorld
    ) -> None:
        target = await world.own("Doubtful claim.")
        general = _context(archive, settings, ToolScope())
        outcome = await registry.dispatch(
            general, "propose_insight_deletion", {"insight_id": target, "reason": REASON}
        )
        assert outcome.status == "refused"
        designing = _context(archive, settings, ToolScope.for_thread(world.set_id, designing=True))
        outcome = await registry.dispatch(
            designing, "propose_insight_deletion", {"insight_id": target, "reason": REASON}
        )
        assert outcome.status == "refused"
        assert await world.insights.get(target) is not None


class TestWhatTheAgentReads:
    async def test_get_insights_and_the_set_carry_the_same_line(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        added = await call(
            conversation,
            "record_insight",
            text="Finer lengthens it.",
            rests_on_versions=["v1.1"],
            evidence_shot_ids=[world.shot2],
        )
        await world.insights.set_confirmed(added["insight_id"], True)
        await world.regrade(world.v2, "failed")

        listed = await call(conversation, "get_insights")
        in_set = await call(conversation, "get_set")

        (insight,) = listed["insights"]
        expected = (
            f"#{added['insight_id']} [this Set, learned at v1.2; rests on v1.1 held → now "
            f"failed; shots {world.shot2}] Finer lengthens it."
        )
        assert insight["line"] == expected
        assert in_set["insights"] == [expected]


class TestNothingATooCanRemove:
    async def test_no_tool_call_removes_an_added_insight(
        self, conversation: ToolContext, world: ProvenanceWorld
    ) -> None:
        old = await world.own("Old claim.")
        target = await world.own("Doubtful claim.")
        await call(
            conversation,
            "record_insight",
            text="New claim.",
            evidence_shot_ids=[world.shot1],
            replaces_insight_id=old,
        )
        await call(conversation, "propose_insight_deletion", insight_id=target, reason=REASON)

        repo = InsightsRepository(world.db)
        for insight_id in (old, target):
            row = await repo.get(insight_id)
            assert row is not None and row.confirmed
