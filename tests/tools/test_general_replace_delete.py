"""Replacing or deleting a general insight: on the person's own words, on a card.

Through the dispatcher. What these pin: the quote check is the one `request_general_insight`
makes (same function, same minimum, newest matching message, same sentence) on both new paths;
one function decides which general insights a conversation may name; Add of a replacement
deletes the old insight only while it is still an added general one; a deletion is stored with
no Recipe and deletes nothing until a person presses Delete; Delete refuses words that changed.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.chat.context import GENERAL_INSIGHTS_TITLE, opening_context
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.chat import ChatMessageWrite, ChatRepository
from gaggiclanker.db.repos.insight_deletions import INSIGHT_CHANGED_SENTENCE
from gaggiclanker.db.repos.knowledge_insights import InsightScope, InsightWrite
from gaggiclanker.db.repos.recipes import DesignBrief, RecipeWrite
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.builtin import QUOTE_NOT_FOUND, QUOTE_TOO_SHORT
from gaggiclanker.tools.registry import ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.knowledge.insight_world import ProvenanceWorld, build_provenance_world
from tests.review.conftest import Fixture
from tests.tools.conftest import _context

SAID = "forget the Niche note, 7 is fine now"
REASON = "The person said the Niche note is wrong now."
ONLY_FROM_WORDS = "A general insight is changed only from what the person said."
CONTRADICTION = (
    "When this Recipe's shots contradict it, record a Recipe insight saying it does not hold "
    "here (record_insight)."
)
NEW = "On the Niche Zero 7 is fine."


def not_told(insight_id: int) -> str:
    return (
        f"#{insight_id} is not a general insight this conversation is told, so it cannot be "
        "replaced or deleted from here."
    )


async def run(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert outcome.ok, outcome.data
    return outcome.data


async def refuse(ctx: ToolContext, name: str, **arguments: Any) -> str:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert not outcome.ok, "this call should have been refused"
    return str(outcome.data["detail"])


async def say(world: ProvenanceWorld, content: str, role: str = "user") -> int:
    stored = await ChatRepository(world.db).add_message(
        ChatMessageWrite(thread_id=world.thread, role=role, content=content)  # type: ignore[arg-type]
    )
    return stored.id


@pytest.fixture
async def world(archive: Fixture) -> ProvenanceWorld:
    return await build_provenance_world(archive.db)


@pytest.fixture
def recipe_chat(archive: Fixture, settings: SettingsService, world: ProvenanceWorld) -> ToolContext:
    ctx = _context(archive, settings, ToolScope.for_thread(world.recipe_id, world.v3))
    ctx.thread_id = world.thread
    return ctx


@pytest.fixture
def general_chat(
    archive: Fixture, settings: SettingsService, world: ProvenanceWorld
) -> ToolContext:
    ctx = _context(archive, settings, ToolScope())
    ctx.thread_id = world.thread
    return ctx


@pytest.fixture
async def design_chat(
    archive: Fixture, settings: SettingsService, world: ProvenanceWorld
) -> ToolContext:
    bean = await BeansRepository(world.db).create(BeanWrite(name="Fresh"))
    designed = await world.recipes.create_design(
        RecipeWrite(name="New", bean_id=bean.id, grinder_id=None), DesignBrief()
    )
    ctx = _context(archive, settings, await ToolScope.resolve(world.db, designed.id))
    assert ctx.scope.designing
    ctx.thread_id = world.thread
    return ctx


async def added(world: ProvenanceWorld, text: str = "Old Niche note.", **scope: Any) -> int:
    return await world.insights.insert(
        InsightWrite(text=text, scope=InsightScope(**scope), source="user", confirmed=True)
    )


async def grinder_of(world: ProvenanceWorld) -> int:
    row = await world.recipes.get(world.recipe_id)
    assert row is not None and row.grinder_id is not None
    return row.grinder_id


# ── replacing ────────────────────────────────────────────────────────


class TestReplacing:
    async def test_it_stores_the_link_and_the_old_words_and_changes_nothing(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        old = await added(world)
        await say(world, SAID)

        data = await run(
            recipe_chat, "request_general_insight", text=NEW, said=SAID, replaces_insight_id=old
        )

        assert (data["replaces_insight_id"], data["replaces_text"]) == (old, "Old Niche note.")
        assert f"Adding it deletes #{old}; until then #{old} stays" in data["note"]
        stored = await world.insights.get(data["insight_id"])
        assert stored is not None
        assert (stored.replaces_id, stored.replaces_text, stored.confirmed) == (
            old,
            "Old Niche note.",
            False,
        )
        assert await world.insights.get(old) is not None

    async def test_it_needs_the_quote_by_the_same_rules(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        old = await added(world)
        # Not found, then too short, then found only in the agent's own message.
        assert (
            await refuse(
                recipe_chat, "request_general_insight", text=NEW, said=SAID, replaces_insight_id=old
            )
            == f"{QUOTE_NOT_FOUND} {CONTRADICTION}"
        )
        await say(world, SAID, role="assistant")
        assert (
            await refuse(
                recipe_chat, "request_general_insight", text=NEW, said=SAID, replaces_insight_id=old
            )
            == f"{QUOTE_NOT_FOUND} {CONTRADICTION}"
        )
        await say(world, "forget it")
        assert (
            await refuse(
                recipe_chat,
                "request_general_insight",
                text=NEW,
                said="forget it",
                replaces_insight_id=old,
            )
            == f"{QUOTE_TOO_SHORT} {CONTRADICTION}"
        )
        assert [r.id for r in await world.insights.requests_in(world.thread)] == []

    @pytest.mark.parametrize(
        "case", ["missing", "recipe_own", "waiting", "dismissed", "out_of_scope"]
    )
    async def test_what_the_conversation_is_not_told_has_one_sentence(
        self, recipe_chat: ToolContext, world: ProvenanceWorld, case: str
    ) -> None:
        await say(world, SAID)
        if case == "missing":
            target = 99999
        elif case == "recipe_own":
            target = await world.own("A Recipe lesson.")
        elif case == "waiting":
            target = await world.insights.insert(InsightWrite(text="W.", source="user"))
        elif case == "dismissed":
            target = await world.insights.insert(InsightWrite(text="D.", source="user"))
            await world.insights.dismiss(target)
        else:
            target = await added(world, "Other bean.", bean_id=987654)
        assert await refuse(
            recipe_chat, "request_general_insight", text=NEW, said=SAID, replaces_insight_id=target
        ) == not_told(target)

    async def test_the_contradiction_path_is_named_in_a_recipe_conversation_only(
        self,
        recipe_chat: ToolContext,
        general_chat: ToolContext,
        design_chat: ToolContext,
        world: ProvenanceWorld,
    ) -> None:
        old = await added(world)
        for ctx, named in ((recipe_chat, True), (general_chat, False), (design_chat, False)):
            for call, arguments in (
                (
                    "request_general_insight",
                    {"text": NEW, "said": SAID, "replaces_insight_id": old},
                ),
                (
                    "propose_insight_deletion",
                    {"insight_id": old, "said": SAID, "reason": REASON},
                ),
            ):
                detail = await refuse(ctx, call, **arguments)
                assert (CONTRADICTION in detail) is named, (call, detail)
                assert ("record_insight" in detail) is named, (call, detail)

    async def test_a_new_general_request_is_not_pointed_at_the_contradiction_path(
        self, recipe_chat: ToolContext
    ) -> None:
        detail = await refuse(recipe_chat, "request_general_insight", text=NEW, said=SAID)
        assert detail == QUOTE_NOT_FOUND

    async def test_a_general_conversation_may_name_any_added_one_and_a_matching_scope_is_told(
        self, general_chat: ToolContext, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        await say(world, SAID)
        elsewhere = await added(world, "Other bean.", bean_id=987654)
        mine = await added(world, "Mine.", grinder_id=await grinder_of(world))

        assert (
            await run(
                general_chat,
                "request_general_insight",
                text=NEW,
                said=SAID,
                replaces_insight_id=elsewhere,
            )
        )["replaces_insight_id"] == elsewhere
        assert (
            await run(
                recipe_chat,
                "request_general_insight",
                text="Newer.",
                said=SAID,
                replaces_insight_id=mine,
            )
        )["replaces_insight_id"] == mine
        assert await refuse(
            recipe_chat,
            "request_general_insight",
            text="Third.",
            said=SAID,
            replaces_insight_id=elsewhere,
        ) == not_told(elsewhere)

    async def test_the_scope_of_a_replacement_follows_the_conversation_not_the_old_one(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        await say(world, SAID)
        old = await added(world, "Old.", grinder_id=await grinder_of(world))
        data = await run(
            recipe_chat, "request_general_insight", text=NEW, said=SAID, replaces_insight_id=old
        )
        assert data["scope"] == {}

    async def test_add_deletes_the_old_one_in_the_same_step(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        old = await added(world)
        await say(world, SAID)
        data = await run(
            recipe_chat, "request_general_insight", text=NEW, said=SAID, replaces_insight_id=old
        )

        assert await world.insights.set_confirmed(data["insight_id"], True)

        assert await world.insights.get(old) is None
        new = await world.insights.get(data["insight_id"])
        assert new is not None and (new.confirmed, new.replaced, new.replaces_id) == (
            True,
            "deleted",
            None,
        )

    @pytest.mark.parametrize("change", ["deleted", "taken_back", "dismissed", "recipe_own"])
    async def test_add_deletes_only_a_still_added_general_insight(
        self, recipe_chat: ToolContext, world: ProvenanceWorld, change: str
    ) -> None:
        old = await added(world)
        await say(world, SAID)
        data = await run(
            recipe_chat, "request_general_insight", text=NEW, said=SAID, replaces_insight_id=old
        )
        if change == "deleted":
            await world.insights.delete(old)
        elif change == "taken_back":
            await world.insights.set_confirmed(old, False)
        elif change == "dismissed":
            await world.insights.dismiss(old)
        else:
            # A row of a Recipe under the same id is not a general insight: the explicit check.
            await world.db.execute(
                "UPDATE knowledge_insights SET recipe_id = ? WHERE id = ?", (world.recipe_id, old)
            )

        await world.insights.set_confirmed(data["insight_id"], True)

        new = await world.insights.get(data["insight_id"])
        assert new is not None and (new.confirmed, new.replaced) == (True, "old_changed")
        if change != "deleted":
            assert await world.insights.get(old) is not None

    async def test_dismiss_leaves_the_old_one_alone(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        old = await added(world)
        await say(world, SAID)
        data = await run(
            recipe_chat, "request_general_insight", text=NEW, said=SAID, replaces_insight_id=old
        )

        await world.insights.dismiss(data["insight_id"])

        kept = await world.insights.get(old)
        assert kept is not None and kept.confirmed

    async def test_a_deletion_waiting_on_the_old_one_goes_stale(
        self, recipe_chat: ToolContext, general_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        old = await added(world)
        await say(world, SAID)
        asked = await run(
            general_chat, "propose_insight_deletion", insight_id=old, said=SAID, reason=REASON
        )
        data = await run(
            recipe_chat, "request_general_insight", text=NEW, said=SAID, replaces_insight_id=old
        )

        await world.insights.set_confirmed(data["insight_id"], True)

        row = await world.deletions.get_general(asked["proposal_id"])
        assert row is not None and (row.status, row.insight_id) == ("stale", None)

    async def test_record_insight_points_a_general_replacement_at_the_two_paths(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        old = await added(world)
        detail = await refuse(
            recipe_chat,
            "record_insight",
            text="It does not hold here.",
            evidence_shot_ids=[world.shot1],
            replaces_insight_id=old,
        )
        assert "request_general_insight" in detail and "propose_insight_deletion" in detail


# ── deleting ─────────────────────────────────────────────────────────


class TestDeleting:
    async def test_it_waits_with_no_recipe_and_deletes_nothing(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        target = await added(world, "Old Niche note.", grinder_id=await grinder_of(world))
        await say(world, SAID)

        data = await run(
            recipe_chat, "propose_insight_deletion", insight_id=target, said=SAID, reason=REASON
        )

        assert data["general"] is True and data["recipe_id"] is None and data["said"] == SAID
        assert data["scope_labels"] == ["grinder Niche Zero"] and data["reach"] == 2
        assert f"Nothing is deleted: #{target} stays" in data["note"]
        assert "(2 Recipes now)" in data["note"]
        assert "1 Recipes" not in data["note"]
        row = await world.deletions.get_general(data["proposal_id"])
        assert row is not None and (row.recipe_id, row.thread_id, row.status) == (
            None,
            world.thread,
            "proposed",
        )
        assert await world.deletions.get(world.recipe_id, data["proposal_id"]) is None
        kept = await world.insights.get(target)
        assert kept is not None and kept.confirmed

    @pytest.mark.parametrize("said", ["", "forget it", "words nobody wrote at all"])
    async def test_a_missing_short_or_unfound_quote_is_one_refusal_that_says_whose_words(
        self, recipe_chat: ToolContext, world: ProvenanceWorld, said: str
    ) -> None:
        target = await added(world)
        await say(world, SAID)
        detail = await refuse(
            recipe_chat, "propose_insight_deletion", insight_id=target, said=said, reason=REASON
        )
        assert ONLY_FROM_WORDS in detail
        assert (QUOTE_TOO_SHORT if len(said) < 12 else QUOTE_NOT_FOUND) in detail
        assert detail.endswith(CONTRADICTION)
        assert await world.deletions.get_general(1) is None

    async def test_the_newest_message_is_not_needed_any_matching_message_of_theirs_will_do(
        self, general_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        target = await added(world)
        await say(world, SAID, role="assistant")
        await say(world, f"Declined: {SAID}")
        assert ONLY_FROM_WORDS in await refuse(
            general_chat, "propose_insight_deletion", insight_id=target, said=SAID, reason=REASON
        )
        await say(world, f"Please, {SAID.upper()}.")
        assert (
            await run(
                general_chat,
                "propose_insight_deletion",
                insight_id=target,
                said=SAID,
                reason=REASON,
            )
        )["general"]

    async def test_the_note_counts_one_recipe_in_the_singular(
        self, general_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        await say(world, SAID)
        one = await added(world, "Only here.", bean_id=987654)
        # A scope no Recipe matches and one exactly one does.
        none = await run(
            general_chat, "propose_insight_deletion", insight_id=one, said=SAID, reason=REASON
        )
        assert none["reach"] == 0 and "no Recipe is told it now" in none["note"]
        grinder = await grinder_of(world)
        await world.db.execute(
            "UPDATE recipes SET grinder_id = NULL WHERE grinder_id = ? AND id != ?",
            (grinder, world.recipe_id),
        )
        target = await added(world, "On the Niche.", grinder_id=grinder)
        data = await run(
            general_chat, "propose_insight_deletion", insight_id=target, said=SAID, reason=REASON
        )
        assert data["reach"] == 1
        assert "(1 Recipe now)" in data["note"] and "1 Recipes" not in data["note"]

    async def test_said_is_ignored_for_a_recipe_insight(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        target = await world.own("Doubtful claim.")
        data = await run(
            recipe_chat, "propose_insight_deletion", insight_id=target, reason=REASON, said="x"
        )
        assert data["general"] is False and data["recipe_id"] == world.recipe_id

    async def test_what_the_conversation_is_not_told_has_the_one_sentence(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        await say(world, SAID)
        elsewhere = await added(world, "Other bean.", bean_id=987654)
        waiting = await world.insights.insert(InsightWrite(text="W.", source="user"))
        for target in (elsewhere, waiting):
            detail = await refuse(
                recipe_chat, "propose_insight_deletion", insight_id=target, said=SAID, reason=REASON
            )
            assert not_told(target) in detail

    async def test_a_general_conversation_names_general_insights_only(
        self, general_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        await say(world, SAID)
        own = await world.own("A Recipe lesson.")
        assert await refuse(
            general_chat, "propose_insight_deletion", insight_id=own, said=SAID, reason=REASON
        ) == not_told(own)
        target = await added(world, "Anything.", bean_id=987654)
        assert (
            await run(
                general_chat,
                "propose_insight_deletion",
                insight_id=target,
                said=SAID,
                reason=REASON,
            )
        )["insight_id"] == target

    async def test_a_design_conversation_names_general_insights_only_and_says_so(
        self, design_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        await say(world, SAID)
        own = await world.own("A Recipe lesson.")
        detail = await refuse(
            design_chat, "propose_insight_deletion", insight_id=own, said=SAID, reason=REASON
        )
        assert not_told(own) in detail and "general insights only" in detail
        target = await added(world)
        assert (
            await run(
                design_chat, "propose_insight_deletion", insight_id=target, said=SAID, reason=REASON
            )
        )["general"]

    async def test_a_second_proposal_supersedes_the_waiting_one(
        self, general_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        target = await added(world)
        await say(world, SAID)
        first = await run(
            general_chat, "propose_insight_deletion", insight_id=target, said=SAID, reason=REASON
        )
        second = await run(
            general_chat,
            "propose_insight_deletion",
            insight_id=target,
            said=SAID,
            reason=REASON + " Again.",
        )
        assert second["replaced_waiting_proposal"] is True
        row = await world.deletions.get_general(first["proposal_id"])
        assert row is not None and row.status == "superseded"

    async def test_delete_removes_it_and_keep_does_not(
        self, general_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        a, b = await added(world, "A."), await added(world, "B.")
        await say(world, SAID)
        first = await run(
            general_chat, "propose_insight_deletion", insight_id=a, said=SAID, reason=REASON
        )
        second = await run(
            general_chat, "propose_insight_deletion", insight_id=b, said=SAID, reason=REASON
        )

        assert (await world.deletions.accept_general(first["proposal_id"])).proposal is not None
        assert (await world.deletions.keep_general(second["proposal_id"])).proposal is not None

        assert await world.insights.get(a) is None
        assert await world.insights.get(b) is not None
        gone = await world.deletions.get_general(first["proposal_id"])
        assert gone is not None and (gone.status, gone.insight_text) == ("deleted", "A.")

    async def test_a_recipe_never_answers_a_general_proposal_nor_the_other_way(
        self, general_chat: ToolContext, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        general = await added(world)
        own = await world.own("Doubtful claim.")
        await say(world, SAID)
        g = await run(
            general_chat, "propose_insight_deletion", insight_id=general, said=SAID, reason=REASON
        )
        r = await run(recipe_chat, "propose_insight_deletion", insight_id=own, reason=REASON)

        assert (
            await world.deletions.accept(world.recipe_id, g["proposal_id"])
        ).refused == "no_proposal"
        assert (
            await world.deletions.keep(world.recipe_id, g["proposal_id"])
        ).refused == "no_proposal"
        assert (await world.deletions.accept_general(r["proposal_id"])).refused == "no_proposal"
        assert (await world.deletions.keep_general(r["proposal_id"])).refused == "no_proposal"
        assert (
            await world.insights.get(general) is not None
            and await world.insights.get(own) is not None
        )

    @pytest.mark.parametrize("edit", ["words", "scope_only", "case_and_spacing"])
    async def test_delete_refuses_words_that_changed_since_the_agent_asked(
        self, general_chat: ToolContext, world: ProvenanceWorld, edit: str
    ) -> None:
        target = await added(world, "Old Niche note.")
        await say(world, SAID)
        asked = await run(
            general_chat, "propose_insight_deletion", insight_id=target, said=SAID, reason=REASON
        )
        if edit == "words":
            # Straight into the table, past the repository's own hook: this pins Delete's check.
            await world.db.execute(
                "UPDATE knowledge_insights SET text = ? WHERE id = ?",
                ("Something quite different now.", target),
            )
        elif edit == "scope_only":
            await world.insights.update(target, scope=InsightScope(bean_id=987654))
        else:
            await world.insights.update(target, text="  OLD   niche NOTE. ")

        result = await world.deletions.accept_general(asked["proposal_id"])

        if edit == "words":
            assert await world.insights.get(target) is not None
            assert result.refused == "insight_changed"
            assert result.proposal is not None and result.proposal.status == "stale"
        else:
            # Only the words count: a scope change or a recasing deletes as asked.
            assert result.refused is None
            assert await world.insights.get(target) is None

    async def test_the_same_refusal_covers_a_recipes_proposal(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        target = await world.own("Doubtful claim.")
        asked = await run(recipe_chat, "propose_insight_deletion", insight_id=target, reason=REASON)
        await world.db.execute(
            "UPDATE knowledge_insights SET text = ? WHERE id = ?",
            ("Another claim entirely.", target),
        )

        result = await world.deletions.accept(world.recipe_id, asked["proposal_id"])

        assert result.refused == "insight_changed" and await world.insights.get(target) is not None
        assert (
            INSIGHT_CHANGED_SENTENCE == "It was changed since the agent asked: nothing was deleted."
        )


class TestTheToldCheckInADesignChat:
    async def test_a_general_insight_it_is_not_told_is_refused_on_both_paths(
        self, design_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        # The designed Recipe has no grinder; the insight applies to the Niche only.
        old = await added(world, grinder_id=await grinder_of(world))
        await say(world, SAID)

        detail = await refuse(
            design_chat,
            "request_general_insight",
            text="Niche 7 is fine.",
            said=SAID,
            replaces_insight_id=old,
        )
        assert not_told(old) in detail
        detail = await refuse(
            design_chat, "propose_insight_deletion", insight_id=old, said=SAID, reason=REASON
        )
        assert not_told(old) in detail


# ── what the conversation is told ────────────────────────────────────


class TestTheContext:
    async def _stored(
        self, ctx: ToolContext, world: ProvenanceWorld, name: str, **arguments: Any
    ) -> dict[str, Any]:
        outcome = await registry.dispatch(ctx, name, arguments)
        assert outcome.ok, outcome.data
        await ChatRepository(world.db).add_message(
            ChatMessageWrite(
                thread_id=world.thread,
                role="tool",
                tool_results=[
                    {"id": "t", "name": name, "content": outcome.as_content(), "ok": True}
                ],
            )
        )
        return outcome.data

    async def test_a_replacement_is_told_waiting_added_and_unchanged_in_its_words(
        self, recipe_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        old, other = await added(world, "Old one."), await added(world, "Other one.")
        await say(world, SAID)
        waiting = await self._stored(
            recipe_chat,
            world,
            "request_general_insight",
            text=NEW,
            said=SAID,
            replaces_insight_id=old,
        )
        done = await self._stored(
            recipe_chat,
            world,
            "request_general_insight",
            text="Second.",
            said=SAID,
            replaces_insight_id=other,
        )
        scope = recipe_chat.scope

        text = await opening_context(world.db, scope, thread_id=world.thread)
        assert (
            f"(waiting: the person has not pressed Add, so nothing is saved yet, replacing #{old})"
            in text
        )

        await world.insights.set_confirmed(done["insight_id"], True)
        text = await opening_context(world.db, scope, thread_id=world.thread)
        assert f"(added by the person, replaced #{other})" in text

        # The first one's old insight goes before its Add: nothing was replaced, and it is said.
        await world.insights.delete(old)
        await world.insights.set_confirmed(waiting["insight_id"], True)
        text = await opening_context(world.db, scope, thread_id=world.thread)
        assert (
            f"added by the person; #{old} had already changed or gone, so nothing was replaced"
            in text
        )
        assert GENERAL_INSIGHTS_TITLE in text

    async def test_a_deletion_card_is_not_mistaken_for_a_request(
        self, general_chat: ToolContext, world: ProvenanceWorld
    ) -> None:
        target = await added(world)
        await say(world, SAID)
        await self._stored(
            general_chat,
            world,
            "propose_insight_deletion",
            insight_id=target,
            said=SAID,
            reason=REASON,
        )

        assert await ChatRepository(world.db).requested_general_insights(world.thread) == {}
        text = await opening_context(world.db, general_chat.scope, thread_id=world.thread)
        assert GENERAL_INSIGHTS_TITLE not in text

    async def test_every_opening_context_lists_the_deletions_marked_general(
        self,
        general_chat: ToolContext,
        recipe_chat: ToolContext,
        design_chat: ToolContext,
        world: ProvenanceWorld,
    ) -> None:
        target = await added(world, "Old Niche note.")
        await say(world, SAID)
        await self._stored(
            general_chat,
            world,
            "propose_insight_deletion",
            insight_id=target,
            said=SAID,
            reason=REASON,
        )
        line = (
            f"- #{target} Old Niche note. (general; waiting: nothing is deleted until the person "
            "presses Delete)"
        )

        for ctx in (general_chat, recipe_chat, design_chat):
            text = await opening_context(world.db, ctx.scope, thread_id=world.thread)
            assert "INSIGHT DELETIONS YOU PROPOSED IN THIS CONVERSATION" in text, ctx.scope
            assert line in text

        # After the person answers, it is told in the past tense.
        proposal = (await world.deletions.proposed_in(world.thread))[0]
        await world.deletions.keep_general(proposal.id)
        text = await opening_context(world.db, general_chat.scope, thread_id=world.thread)
        assert f"- #{target} Old Niche note. (general; kept by the person)" in text


# ── the contradiction clause only for a general insight this conversation is told ──

UNSAID = "please drop that Niche note now"  # never said in the thread: unfound


async def _target(world: ProvenanceWorld, case: str) -> int:
    if case == "missing":
        return 99999
    if case == "recipe_own":
        return await world.own("A Recipe lesson.")
    return await added(world, "Other bean.", bean_id=987654)


@pytest.mark.parametrize("case", ["missing", "recipe_own", "out_of_scope"])
async def test_replace_not_told_with_bad_quote_has_no_contradiction_clause(
    recipe_chat: ToolContext, world: ProvenanceWorld, case: str
) -> None:
    target = await _target(world, case)
    detail = await refuse(
        recipe_chat, "request_general_insight", text=NEW, said=UNSAID, replaces_insight_id=target
    )
    assert "is not a general insight this conversation is told" in detail
    assert CONTRADICTION not in detail, detail


async def test_delete_not_told_with_bad_quote_has_no_contradiction_clause(
    recipe_chat: ToolContext, world: ProvenanceWorld
) -> None:
    target = await _target(world, "out_of_scope")
    detail = await refuse(
        recipe_chat, "propose_insight_deletion", insight_id=target, said=UNSAID, reason=REASON
    )
    assert "is not a general insight this conversation is told" in detail
    assert CONTRADICTION not in detail, detail


async def test_a_replace_refused_for_its_scope_with_the_quote_found_has_no_contradiction_clause(
    recipe_chat: ToolContext, world: ProvenanceWorld
) -> None:
    """The contradiction path follows a refusal of the person's words, never one for the scope."""
    old = await added(world)
    await say(world, SAID)
    detail = await refuse(
        recipe_chat,
        "request_general_insight",
        text=NEW,
        said=SAID,
        replaces_insight_id=old,
        scope={"bean_id": 987654},
    )
    assert "Quote" not in detail and "is not a general insight" not in detail, detail
    assert CONTRADICTION not in detail, detail
