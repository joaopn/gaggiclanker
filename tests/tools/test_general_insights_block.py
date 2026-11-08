"""What each conversation is told about the general insights it asked for or saved.

One block, built by one function, in the three opening contexts (Recipe, design and General).
Real tool calls leave the requests, stored as the runner stores them, so the block reads what
the records really hold.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.chat.context import GENERAL_INSIGHTS_TITLE, opening_context
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.chat import ChatMessageWrite, ChatRepository
from gaggiclanker.db.repos.knowledge_insights import InsightScope, InsightWrite
from gaggiclanker.db.repos.recipes import DesignBrief, RecipeWrite
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.knowledge.insight_world import ProvenanceWorld, build_provenance_world
from tests.review.conftest import Fixture
from tests.tools.conftest import _context

SAID = "never suggest going finer than 8 on the Niche, it chokes"
TEXT = "Never go finer than 8 on the Niche Zero: it chokes."


@pytest.fixture
async def world(archive: Fixture) -> ProvenanceWorld:
    return await build_provenance_world(archive.db)


async def request(
    world: ProvenanceWorld,
    archive: Fixture,
    settings: SettingsService,
    scope: ToolScope,
    *,
    text: str = TEXT,
    store: bool = True,
    **narrowed: Any,
) -> int:
    """The agent's real tool call, and its result stored on the thread as the runner stores it."""
    chat = ChatRepository(world.db)
    await chat.add_message(ChatMessageWrite(thread_id=world.thread, role="user", content=SAID))
    ctx: ToolContext = _context(archive, settings, scope)
    ctx.thread_id = world.thread
    outcome = await registry.dispatch(
        ctx, "request_general_insight", {"text": text, "said": SAID, "scope": narrowed}
    )
    assert outcome.ok, outcome.data
    if not store:
        # The run ended before the result was stored (the row is there all the same).
        return int(outcome.data["insight_id"])
    await chat.add_message(
        ChatMessageWrite(
            thread_id=world.thread,
            role="tool",
            tool_results=[
                {
                    "id": "t1",
                    "name": "request_general_insight",
                    "content": outcome.as_content(),
                    "ok": True,
                }
            ],
        )
    )
    return int(outcome.data["insight_id"])


async def grinder_of(world: ProvenanceWorld) -> int:
    row = await world.recipes.get(world.recipe_id)
    assert row is not None and row.grinder_id is not None
    return row.grinder_id


def block(context: str) -> list[str]:
    assert GENERAL_INSIGHTS_TITLE in context
    return context.split(GENERAL_INSIGHTS_TITLE + "\n")[1].split("\n\n")[0].split("\n")


async def recipe_context(world: ProvenanceWorld) -> str:
    return await opening_context(
        world.db, ToolScope.for_thread(world.recipe_id, world.v3), thread_id=world.thread
    )


async def test_a_conversation_that_asked_for_nothing_gets_no_block(world: ProvenanceWorld) -> None:
    assert GENERAL_INSIGHTS_TITLE not in await recipe_context(world)


async def test_a_waiting_request_is_told_as_waiting_and_not_as_an_insight_proposed(
    world: ProvenanceWorld, archive: Fixture, settings: SettingsService
) -> None:
    scope = ToolScope.for_thread(world.recipe_id, world.v3)
    insight_id = await request(world, archive, settings, scope, grinder_id=await grinder_of(world))

    # The precondition: this conversation's insights are all read through the thread, and the
    # request is among them, so the Recipe block would report it a second time without its
    # filter.
    assert insight_id in [row.id for row in await world.insights.requests_in(world.thread)]
    context = await recipe_context(world)

    lines = block(context)
    assert len(lines) == 1
    assert lines[0].startswith(
        f"- #{insight_id} [grinder_id={await grinder_of(world)}] {TEXT} (waiting:"
    )
    assert "INSIGHTS YOU PROPOSED IN THIS CONVERSATION" not in context


async def test_it_comes_after_what_does_not_change_between_turns(
    world: ProvenanceWorld, archive: Fixture, settings: SettingsService
) -> None:
    scope = ToolScope.for_thread(world.recipe_id, world.v3)
    before = await recipe_context(world)
    await request(world, archive, settings, scope)
    after = await recipe_context(world)
    # Everything above the block is byte-identical: the cached prefix does not move.
    assert after.startswith(before.split("\n\nThose facts are the record")[0])


async def test_each_answer_is_told_in_its_own_words(
    world: ProvenanceWorld, archive: Fixture, settings: SettingsService
) -> None:
    scope = ToolScope.for_thread(world.recipe_id, world.v3)
    added = await request(world, archive, settings, scope, text="Added as it was asked.")
    changed = await request(world, archive, settings, scope, text="Added, then edited.")
    dismissed = await request(world, archive, settings, scope, text="Turned down.")
    gone = await request(world, archive, settings, scope, text="Deleted afterwards, secret words.")
    saved_message = (await ChatRepository(world.db).user_messages(world.thread))[0][0]
    saved = await world.insights.insert(
        InsightWrite(
            text="Saved from a message.",
            source="user",
            confirmed=True,
            thread_id=world.thread,
            from_message_id=saved_message,
        )
    )

    assert await world.insights.set_confirmed(added, True)
    assert await world.insights.set_confirmed(changed, True)
    assert await world.insights.update(changed, text="Added, and the person reworded it.")
    assert await world.insights.dismiss(dismissed)
    assert await world.insights.delete(gone)

    lines = block(await recipe_context(world))

    by_id = {int(line.split("#")[1].split(" ")[0]): line for line in lines}
    assert by_id[added].endswith("(added by the person)")
    assert by_id[changed].endswith("(added by the person with their changes)")
    assert "reworded" in by_id[changed]
    assert "dismissed by the person" in by_id[dismissed]
    assert "saved by the person from their message" in by_id[saved]
    # A gone row has no text to quote, so its line says that and nothing more.
    assert lines[[line.startswith(f"- #{gone} ") for line in lines].index(True)] == (
        f"- #{gone} was deleted since"
    )
    assert "secret words" not in "\n".join(lines)


async def test_a_changed_scope_is_a_change_too(
    world: ProvenanceWorld, archive: Fixture, settings: SettingsService
) -> None:
    scope = ToolScope.for_thread(world.recipe_id, world.v3)
    insight_id = await request(world, archive, settings, scope, grinder_id=await grinder_of(world))
    await world.insights.update(insight_id, scope=InsightScope())
    await world.insights.set_confirmed(insight_id, True)

    assert "with their changes" in block(await recipe_context(world))[0]


async def test_a_general_conversation_gets_it_after_the_kitchen_lines(
    world: ProvenanceWorld, archive: Fixture, settings: SettingsService
) -> None:
    scope = ToolScope()
    insight_id = await request(world, archive, settings, scope)
    context = await opening_context(world.db, scope, thread_id=world.thread)

    kitchen, _, rest = context.partition("\n\n" + GENERAL_INSIGHTS_TITLE)
    assert "Niche Zero" in kitchen
    assert rest.startswith(f"\n- #{insight_id} ")


async def test_a_design_conversation_gets_it_too(
    world: ProvenanceWorld, archive: Fixture, settings: SettingsService
) -> None:
    bean = await BeansRepository(world.db).create(BeanWrite(name="Fresh"))
    designed = await world.recipes.create_design(
        RecipeWrite(name="New", bean_id=bean.id, grinder_id=None), DesignBrief()
    )
    scope = await ToolScope.resolve(world.db, designed.id)
    assert scope.designing
    insight_id = await request(world, archive, settings, scope)

    context = await opening_context(world.db, scope, thread_id=world.thread)

    assert block(context)[0].startswith(f"- #{insight_id} ")
    assert context.index(GENERAL_INSIGHTS_TITLE) > context.index("SIMILAR RECIPES")


async def test_another_conversation_is_not_told_of_it(
    world: ProvenanceWorld, archive: Fixture, settings: SettingsService
) -> None:
    await request(world, archive, settings, ToolScope.for_thread(world.recipe_id, world.v3))
    other = await opening_context(
        world.db, ToolScope.for_thread(world.recipe_id, world.v3), thread_id=world.other_thread
    )
    assert GENERAL_INSIGHTS_TITLE not in other


async def test_every_line_takes_its_state_from_the_row(
    world: ProvenanceWorld, archive: Fixture, settings: SettingsService
) -> None:
    scope = ToolScope.for_thread(world.recipe_id, world.v3)
    waiting = await request(world, archive, settings, scope, text="Still waiting.")
    unstored = await request(world, archive, settings, scope, text="Result lost.", store=False)
    taken_back = await request(world, archive, settings, scope, text="Added, then taken back.")
    edited_only = await request(world, archive, settings, scope, text="Edited, never added.")
    message = (await ChatRepository(world.db).user_messages(world.thread))[0][0]
    saved = await world.insights.insert(
        InsightWrite(
            text="Kept from a message.",
            source="user",
            confirmed=True,
            thread_id=world.thread,
            from_message_id=message,
        )
    )
    saved_then_off = await world.insights.insert(
        InsightWrite(
            text="Saved, then switched off.",
            source="user",
            confirmed=True,
            thread_id=world.thread,
            from_message_id=message,
        )
    )
    await world.insights.set_confirmed(taken_back, True)
    await world.insights.set_confirmed(taken_back, False)
    await world.insights.update(edited_only, text="Edited on the Knowledge page, never added.")
    await world.insights.set_confirmed(saved_then_off, False)

    lines = block(await recipe_context(world))
    by_id = {int(line.split("#")[1].split(" ")[0]): line for line in lines}

    assert "(waiting: " in by_id[waiting]
    # A request whose result was never stored is told by what its row says: waiting.
    assert "(waiting: " in by_id[unstored]
    assert "saved by the person" not in by_id[unstored]
    assert "saved by the person from their message" in by_id[saved]
    for insight_id in (taken_back, edited_only, saved_then_off):
        # Never "saved" and never "waiting" for a row that is not added and has been touched.
        assert "(not added now: " in by_id[insight_id], by_id[insight_id]
        assert "general insight now" not in by_id[insight_id]
        assert "waiting" not in by_id[insight_id]
