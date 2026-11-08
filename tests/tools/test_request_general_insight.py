"""`request_general_insight`: the agent's request for a general insight, as a row a person answers.

Through the dispatcher, as the chat and the stdio server both use it. What these pin: the tool
asks and never adds, it is refused without the person's own words from this conversation, a
Recipe or Design conversation can narrow only to what the picker offers for its Recipe (one
function answers both), and every problem is named at once.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.chat import ChatMessageWrite, ChatRepository
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.knowledge_insights import InsightScope, InsightWrite, collapsed
from gaggiclanker.db.repos.recipes import DesignBrief, RecipeWrite
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.builtin import (
    QUOTE_MIN_CHARS,
    QUOTE_NOT_FOUND,
    QUOTE_TOO_SHORT,
    SCOPE_NOT_THIS_RECIPE,
)
from gaggiclanker.tools.registry import ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.knowledge.insight_world import ProvenanceWorld, build_provenance_world
from tests.review.conftest import Fixture
from tests.tools.conftest import _context

SAID = "never suggest going finer than 8 on the Niche, it chokes"
TEXT = "Never go finer than 8 on the Niche Zero: it chokes."


async def ask(ctx: ToolContext, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, "request_general_insight", arguments)
    assert outcome.ok, outcome.data
    return outcome.data


async def refuse(ctx: ToolContext, **arguments: Any) -> str:
    outcome = await registry.dispatch(ctx, "request_general_insight", arguments)
    assert not outcome.ok, "this call should have been refused"
    return str(outcome.data["detail"])


async def say(world: ProvenanceWorld, thread: int, content: str, role: str = "user") -> int:
    stored = await ChatRepository(world.db).add_message(
        ChatMessageWrite(thread_id=thread, role=role, content=content)  # type: ignore[arg-type]
    )
    return stored.id


@pytest.fixture
async def world(archive: Fixture) -> ProvenanceWorld:
    return await build_provenance_world(archive.db)


@pytest.fixture
def conversation(
    archive: Fixture, settings: SettingsService, world: ProvenanceWorld
) -> ToolContext:
    ctx = _context(archive, settings, ToolScope.for_thread(world.recipe_id, world.v3))
    ctx.thread_id = world.thread
    return ctx


@pytest.fixture
def general(archive: Fixture, settings: SettingsService, world: ProvenanceWorld) -> ToolContext:
    ctx = _context(archive, settings, ToolScope())
    ctx.thread_id = world.thread
    return ctx


async def test_it_writes_one_waiting_row_the_person_has_not_answered(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    older = await say(world, world.thread, SAID)
    newest = await say(world, world.thread, f"I said it before: {SAID.upper()}!")
    grinder = (await world.recipes.get(world.recipe_id)).grinder_id  # type: ignore[union-attr]

    data = await ask(conversation, text=TEXT, said=SAID, scope={"grinder_id": grinder})

    stored = await world.insights.get(data["insight_id"])
    assert stored is not None
    assert (stored.recipe_id, stored.recipe_version_id) == (None, None)
    assert (stored.confirmed, stored.dismissed, stored.source) == (False, False, "user")
    assert stored.thread_id == world.thread
    # The newest of the person's messages that contains the quote.
    assert stored.from_message_id == newest != older
    assert stored.scope.stated() == {"grinder_id": grinder}
    assert data["scope_labels"] == ["grinder Niche Zero"]
    # Two Recipes on that grinder; the tool says how far Add would reach now.
    assert data["reach"] == 2
    assert "nothing is saved" in data["note"].lower()
    # It is a proposal: no conversation is told it as knowledge.
    assert data["insight_id"] not in [
        item.id for item in await world.insights.list_insights(confirmed=True)
    ]


async def test_it_is_refused_without_a_quote_found_in_the_persons_own_messages(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    # The quote is in the agent's own message and in another conversation, not in the person's
    # here: the role and thread filters are what refuse it.
    await say(world, world.thread, SAID, role="assistant")
    await say(world, world.other_thread, SAID)
    assert await refuse(conversation, text=TEXT, said=SAID) == QUOTE_NOT_FOUND

    # With the person's own message there, the same call is accepted: the refusal was the guard's.
    await say(world, world.thread, SAID)
    assert (await ask(conversation, text=TEXT, said=SAID))["said"] == SAID


async def test_a_quote_is_found_after_collapsing_whitespace_and_folding_case(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    await say(world, world.thread, "Never   suggest going\nfiner than 8 on the NICHE, it chokes")
    assert (await ask(conversation, text=TEXT, said=SAID))["insight_id"]


async def test_a_session_with_no_conversation_is_refused(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    await say(world, world.thread, SAID)
    conversation.thread_id = None
    detail = await refuse(conversation, text=TEXT, said=SAID)
    assert "has no conversation, so nothing was written" in detail


async def test_a_profile_style_is_refused(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    await say(world, world.thread, SAID)
    detail = await refuse(conversation, text=TEXT, said=SAID, scope={"profile_style": "turbo"})
    assert "profile style" in detail


async def test_every_problem_is_named_at_once(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    detail = await refuse(
        conversation,
        text=TEXT,
        said="words nobody wrote",
        scope={"profile_style": "turbo", "bean_id": 9999},
    )
    assert QUOTE_NOT_FOUND in detail
    assert SCOPE_NOT_THIS_RECIPE in detail
    assert "profile style" in detail


async def test_a_recipe_conversation_is_held_to_what_the_picker_offers_for_its_recipe(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    await say(world, world.thread, SAID)
    options = await world.insights.recipe_scope_options(world.recipe_id)
    assert options, "the Recipe states a bean, a grinder and a roast level"
    assert {option.key for option in options} == {"bean_id", "grinder_id", "roast_level"}

    # Every option the picker offers is accepted, one at a time and all together...
    for option in options:
        data = await ask(
            conversation, text=f"{TEXT} {option.key}", said=SAID, scope={option.key: option.value}
        )
        assert data["scope"] == {option.key: option.value}
    together = {option.key: option.value for option in options}
    assert (await ask(conversation, text="All of it.", said=SAID, scope=together))[
        "scope"
    ] == together

    # ...and nothing else is: another bean or grinder (existing or not), another roast, a
    # process the Recipe does not state, in the same words whether or not the value exists.
    other_bean = await BeansRepository(world.db).create(
        BeanWrite(name="Elsewhere", process="natural")
    )
    other_grinder = await GrindersRepository(world.db).create(GrinderWrite(name="Other"))
    outside = [
        {"bean_id": other_bean.id},
        {"bean_id": 99999},
        {"grinder_id": other_grinder.id},
        {"grinder_id": 99999},
        {"roast_level": "dark"},
        {"process": "natural"},
        {"origin": "Ethiopia"},
    ]
    for scope in outside:
        assert (
            await refuse(conversation, text="Nope.", said=SAID, scope=scope)
            == SCOPE_NOT_THIS_RECIPE
        )


async def test_a_general_conversation_may_name_what_the_archive_has(
    general: ToolContext, world: ProvenanceWorld
) -> None:
    await say(world, world.thread, SAID)
    other_bean = await BeansRepository(world.db).create(
        BeanWrite(name="Elsewhere", process="natural")
    )
    data = await ask(
        general,
        text=TEXT,
        said=SAID,
        scope={"bean_id": other_bean.id, "roast_level": "light", "process": "natural"},
    )
    assert data["scope_labels"] == ["bean Elsewhere", "roast light", "process natural"]
    # No Recipe has that bean, so Add would reach none.
    assert data["reach"] == 0

    assert "no bean" in await refuse(general, text="A.", said=SAID, scope={"bean_id": 9999})
    assert "no grinder" in await refuse(general, text="B.", said=SAID, scope={"grinder_id": 9999})
    assert "roast_level" in await refuse(
        general, text="C.", said=SAID, scope={"roast_level": "black"}
    )
    assert "process" in await refuse(general, text="D.", said=SAID, scope={"process": "magic"})


async def test_an_added_general_insight_with_the_same_words_and_scope_is_not_asked_again(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    await say(world, world.thread, SAID)
    # Waiting and dismissed ones are no duplicate; an added one is.
    await world.insights.insert(InsightWrite(text=TEXT, source="user", confirmed=False))
    added = await world.insights.insert(InsightWrite(text=TEXT, source="user", confirmed=True))
    detail = await refuse(
        conversation, text="  never go finer THAN 8 on the niche zero: it chokes. ", said=SAID
    )
    assert detail == f"That is already general insight #{added}."
    # The same words with another scope are a different insight.
    grinder = (await world.recipes.get(world.recipe_id)).grinder_id  # type: ignore[union-attr]
    assert (await ask(conversation, text=TEXT, said=SAID, scope={"grinder_id": grinder}))[
        "insight_id"
    ]


async def test_a_design_conversation_has_it_and_is_held_to_its_own_recipe(
    archive: Fixture, settings: SettingsService, world: ProvenanceWorld
) -> None:
    bean = await BeansRepository(world.db).create(BeanWrite(name="Fresh", process="washed"))
    designed = await world.recipes.create_design(
        RecipeWrite(name="New", bean_id=bean.id, grinder_id=None), DesignBrief()
    )
    ctx = _context(archive, settings, await ToolScope.resolve(world.db, designed.id))
    ctx.thread_id = world.thread
    assert ctx.scope.designing
    await say(world, world.thread, SAID)
    assert (await ask(ctx, text=TEXT, said=SAID, scope={"process": "washed"}))["scope_labels"] == [
        "process washed"
    ]
    assert (
        await refuse(ctx, text="B.", said=SAID, scope={"process": "natural"})
        == SCOPE_NOT_THIS_RECIPE
    )


def test_the_schema_the_model_is_shown_keeps_the_scope_closed_and_the_quote_required() -> None:
    spec = registry.get("request_general_insight")
    assert spec is not None
    schema = spec.input_model.model_json_schema()
    assert set(schema["required"]) == {"text", "said"}
    assert schema.get("additionalProperties") is False
    assert schema["$defs"]["RequestedScope"].get("additionalProperties") is False
    assert schema["properties"]["said"]["maxLength"] == 500
    assert schema["properties"]["reason"]["maxLength"] == 300
    # The scope model names the InsightScope keys, profile_style included, to refuse it by name.
    assert set(schema["$defs"]["RequestedScope"]["properties"]) == set(InsightScope.model_fields)


async def test_a_blank_or_tiny_quote_is_refused_whatever_the_person_wrote(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    await say(world, world.thread, SAID)
    for short in ("   ", "\n", "\t \n", "never", "never   go", "a" * 11):
        detail = await refuse(conversation, text=TEXT, said=short)
        assert detail == QUOTE_TOO_SHORT, short
    # Twelve characters, whitespace collapsed, is enough to be looked for.
    assert collapsed("never   suggest") == "never suggest"
    assert QUOTE_MIN_CHARS == 12
    assert (await ask(conversation, text=TEXT, said="never suggest"))["insight_id"]


async def test_a_quote_the_person_never_wrote_is_refused_though_they_wrote_something(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    await say(world, world.thread, "what grind should I try next?")
    assert await refuse(conversation, text="x", said="never go finer than 8") == QUOTE_NOT_FOUND


async def test_a_cards_own_turn_is_not_the_persons_words(
    conversation: ToolContext, world: ProvenanceWorld
) -> None:
    turn = "Declined: never suggest going finer than 8 on the Niche, it chokes"
    await say(world, world.thread, turn)
    # The precondition: the quote is in a message stored as the person's.
    assert [
        content for _id, content in await ChatRepository(world.db).user_messages(world.thread)
    ] == [turn]
    assert await refuse(conversation, text=TEXT, said=SAID) == QUOTE_NOT_FOUND

    await say(world, world.thread, SAID)
    assert (await ask(conversation, text=TEXT, said=SAID))["insight_id"]


def test_the_automatic_turns_are_the_ones_the_web_builds() -> None:
    import json
    from pathlib import Path

    from gaggiclanker.domain.automatic_turns import AUTOMATIC_TURN_PREFIXES, is_automatic_turn

    turns = json.loads(
        (
            Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "profileTurns.json"
        ).read_text(encoding="utf-8")
    )
    # One list, in the file the web reads; the back end's constant is held equal to it.
    assert tuple(turns["automatic_turn_prefixes"]) == AUTOMATIC_TURN_PREFIXES
    templates = [v for k, v in turns.items() if k != "automatic_turn_prefixes"]
    standalone = [t for t in templates if not t.startswith((",", " "))]
    assert standalone and all(is_automatic_turn(t) for t in standalone)
    # And the turn the accept sends, built in the web's tellAgent.
    assert is_automatic_turn("Accepted: your proposed change is now v2 of this Recipe.")
    assert not is_automatic_turn("never suggest going finer than 8")
    assert not is_automatic_turn("I Accepted: that earlier")


async def test_an_origin_is_stored_as_the_picker_spells_it(
    general: ToolContext, world: ProvenanceWorld
) -> None:
    beans = BeansRepository(world.db)
    await beans.create(BeanWrite(name="A", origin=" Ethiopia "))
    await beans.create(BeanWrite(name="B", origin="ethiopia"))
    await say(world, world.thread, SAID)
    options = await world.insights.general_scope_options()
    assert [o.value for o in options.origins] == ["Ethiopia"]

    data = await ask(general, text=TEXT, said=SAID, scope={"origin": "  ETHIOPIA"})

    assert data["scope"] == {"origin": "Ethiopia"}
    # Some bean must have it: an origin nothing in the archive carries could never match a Recipe.
    assert "No bean in the archive has that origin" in await refuse(
        general, text="Other.", said=SAID, scope={"origin": "Narnia"}
    )


async def test_an_archived_bean_is_in_neither_the_picker_nor_the_tool(
    general: ToolContext, world: ProvenanceWorld
) -> None:
    beans = BeansRepository(world.db)
    old = await beans.create(BeanWrite(name="Old bag", origin="Kenya", roast_level="dark"))
    await say(world, world.thread, SAID)
    # The precondition: while it is in use it is offered, and the tool accepts it.
    before = await world.insights.general_scope_options()
    assert old.id in [b.id for b in before.beans] and "Kenya" in [o.value for o in before.origins]
    assert (await ask(general, text="Kenya one.", said=SAID, scope={"bean_id": old.id}))[
        "insight_id"
    ]
    assert (await ask(general, text="Kenya two.", said=SAID, scope={"origin": "Kenya"}))[
        "insight_id"
    ]

    await beans.set_archived(old.id, archived=True)

    after = await world.insights.general_scope_options()
    assert old.id not in [b.id for b in after.beans]
    assert "Kenya" not in [o.value for o in after.origins]
    assert "no bean" in await refuse(general, text="Kenya 3.", said=SAID, scope={"bean_id": old.id})
    assert "origin" in await refuse(general, text="Kenya 4.", said=SAID, scope={"origin": "Kenya"})


def test_the_number_is_named_where_the_agent_reads_it() -> None:
    spec = registry.get("request_general_insight")
    assert spec is not None
    assert QUOTE_MIN_CHARS == 12
    assert "at least 12 characters of them" in QUOTE_TOO_SHORT
    assert "at least 12 characters" in spec.description
    said = spec.input_model.model_json_schema()["properties"]["said"]["description"]
    assert "at least 12 characters" in said
