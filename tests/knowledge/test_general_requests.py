"""General insights asked for by the agent, or saved from the person's own message.

Over the real app. What these pin: the picker's two reads (`scope-options`, `scope-reach`), the
person-only answers to a waiting request (Add is a PATCH, Dismiss its own route, and nothing
else dismisses a general insight), a save from a message, the message's `saved_insights`, and
the mark a prompt line carries for who a general insight is from.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.chat import ChatMessageWrite, ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.knowledge_insights import (
    InsightScope,
    InsightsRepository,
    InsightWrite,
)
from gaggiclanker.db.repos.recipes import (
    DesignBrief,
    RecipesRepository,
    RecipeVersionWrite,
    RecipeWrite,
)

API = "/api/knowledge"


async def data(response: httpx.Response, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


class Kitchen:
    """Two Recipes on one grinder and a third on another, and a general conversation."""

    def __init__(self, app: FastAPI) -> None:
        self.db = app.state.db
        self.insights = InsightsRepository(self.db)
        self.chat = ChatRepository(self.db)

    async def build(self) -> Kitchen:
        beans = BeansRepository(self.db)
        guji = await beans.create(
            BeanWrite(name="Guji", origin="Ethiopia", roast_level="light", process="washed")
        )
        huila = await beans.create(
            BeanWrite(name="Huila", origin="Colombia ", roast_level="dark", process="natural")
        )
        await beans.create(BeanWrite(name="Blend", origin="ethiopia"))
        grinders = GrindersRepository(self.db)
        self.niche = (await grinders.create(GrinderWrite(name="Niche Zero"))).id
        self.comandante = (await grinders.create(GrinderWrite(name="Comandante"))).id
        recipes = RecipesRepository(self.db)
        made = []
        for name, bean, grinder in (
            ("A", guji.id, self.niche),
            ("B", guji.id, self.niche),
            ("C", huila.id, self.comandante),
        ):
            made.append(
                await recipes.create(
                    RecipeWrite(name=name, bean_id=bean, grinder_id=grinder),
                    RecipeVersionWrite(
                        drink="espresso", dose_g=18, target_yield_g=36, grind_setting="22"
                    ),
                )
            )
        self.a, self.b, self.c = (row.id for row in made)
        self.guji, self.huila = guji.id, huila.id
        thread = await self.chat.create_thread(ChatThreadWrite())
        assert thread.thread is not None
        self.thread = thread.thread.id
        return self

    async def say(self, content: str, role: str = "user") -> int:
        stored = await self.chat.add_message(
            ChatMessageWrite(thread_id=self.thread, role=role, content=content)  # type: ignore[arg-type]
        )
        return stored.id

    async def request(self, text: str = "Never go finer than 8.", **scope: Any) -> int:
        """What the tool leaves behind: a waiting general row filed under the conversation."""
        message = await self.say(f"{text} (said)")
        return await self.insights.insert(
            InsightWrite(
                text=text,
                scope=InsightScope(**scope),
                source="user",
                confirmed=False,
                thread_id=self.thread,
                from_message_id=message,
            )
        )


async def kitchen(app: FastAPI) -> Kitchen:
    return await Kitchen(app).build()


class TestScopeOptions:
    async def test_a_recipe_offers_its_own_stated_attributes_in_key_order(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)

        body = await data(await client.get(f"{API}/scope-options", params={"recipe_id": world.a}))

        assert body["recipe"] == [
            {"key": "bean_id", "value": world.guji, "label": "bean Guji"},
            {"key": "roast_level", "value": "light", "label": "roast light"},
            {"key": "process", "value": "washed", "label": "process washed"},
            {"key": "origin", "value": "Ethiopia", "label": "origin Ethiopia"},
            {"key": "grinder_id", "value": world.niche, "label": "grinder Niche Zero"},
        ]
        assert all(option["key"] != "profile_style" for option in body["recipe"])

    async def test_without_a_recipe_it_offers_the_archive_and_the_closed_vocabularies(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)

        body = await data(await client.get(f"{API}/scope-options"))

        assert body["recipe"] is None
        assert [item["label"] for item in body["beans"]] == ["Blend", "Guji", "Huila"]
        assert [item["label"] for item in body["grinders"]] == ["Comandante", "Niche Zero"]
        assert {"value": "light", "label": "light"} in body["roast_levels"]
        assert {"value": "washed", "label": "washed"} in body["processes"]
        # Origins come from the beans the archive has, once each however they were typed.
        assert [item["value"] for item in body["origins"]] == ["Colombia", "Ethiopia"]
        assert world.guji

    async def test_an_unknown_recipe_is_a_404(self, client: httpx.AsyncClient) -> None:
        assert (
            await client.get(f"{API}/scope-options", params={"recipe_id": 999})
        ).status_code == 404


class TestScopeReach:
    async def test_an_empty_scope_counts_every_recipe_that_is_in_use(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)
        recipes = RecipesRepository(world.db)
        assert (await data(await client.get(f"{API}/scope-reach")))["recipes"] == 3

        # Precondition shown: both Recipes count until they leave.
        assert (
            await data(await client.get(f"{API}/scope-reach", params={"grinder_id": world.niche}))
        )["recipes"] == 2
        await world.db.execute("UPDATE recipes SET archived = 1 WHERE id = ?", (world.b,))
        designed = await recipes.create_design(
            RecipeWrite(name="New", bean_id=world.guji, grinder_id=world.niche), DesignBrief()
        )
        assert designed.designing
        assert (
            await data(await client.get(f"{API}/scope-reach", params={"grinder_id": world.niche}))
        )["recipes"] == 1
        assert (await data(await client.get(f"{API}/scope-reach")))["recipes"] == 2

    async def test_every_key_it_names_must_match(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)
        params: dict[str, str | int] = {
            "grinder_id": world.niche,
            "process": "washed",
            "roast_level": "light",
        }
        assert (await data(await client.get(f"{API}/scope-reach", params=params)))["recipes"] == 2
        params["process"] = "natural"
        assert (await data(await client.get(f"{API}/scope-reach", params=params)))["recipes"] == 0


class TestAnsweringARequest:
    async def test_add_confirms_with_the_changes_the_person_made(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)
        insight_id = await world.request(grinder_id=world.niche)

        added = await data(
            await client.patch(
                f"{API}/insights/{insight_id}",
                json={
                    "text": "Never go finer than 8 on the Niche.",
                    "scope": {"grinder_id": world.niche, "roast_level": "light"},
                    "confirmed": True,
                },
            )
        )

        assert added["confirmed"] is True and added["dismissed"] is False
        assert added["text"] == "Never go finer than 8 on the Niche."
        assert added["scope"]["roast_level"] == "light"
        # It now reaches the matching Recipes' conversations, and not the other grinder's.
        assert insight_id in [row.id for row in await world.insights.for_recipe(world.a)]
        assert insight_id not in [row.id for row in await world.insights.for_recipe(world.c)]

    async def test_dismiss_turns_a_waiting_request_down_and_it_is_listed_nowhere(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)
        insight_id = await world.request()
        # Precondition: while it waits, the Knowledge list shows it as any unconfirmed one.
        listed = await data(await client.get(f"{API}/insights"))
        assert insight_id in [row["id"] for row in listed["items"]]

        dismissed = await data(await client.post(f"{API}/insights/{insight_id}/dismiss"))

        assert dismissed["dismissed"] is True and dismissed["confirmed"] is False
        listed = await data(await client.get(f"{API}/insights"))
        assert insight_id not in [row["id"] for row in listed["items"]]
        assert insight_id not in [row.id for row in await world.insights.for_recipe(world.a)]
        # It was already answered, so it is no longer a waiting request. It came from a
        # conversation, so the refusal says it is not waiting.
        again = await client.post(f"{API}/insights/{insight_id}/dismiss")
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "INSIGHT_NOT_WAITING"

    async def test_no_other_general_insight_can_be_dismissed(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)
        added = await world.insights.insert(
            InsightWrite(text="Added by hand.", source="user", confirmed=True)
        )
        unplaced = await world.insights.insert(
            InsightWrite(text="Waiting, with no conversation.", source="chat", confirmed=False)
        )
        for insight_id in (added, unplaced):
            refused = await client.post(f"{API}/insights/{insight_id}/dismiss")
            assert refused.status_code == 409
            assert refused.json()["error"]["code"] == "INSIGHT_NOT_RECIPE_OWNED"

    async def test_one_made_in_a_conversation_and_answered_is_not_waiting(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)
        added = await world.request()
        await world.insights.set_confirmed(added, True)
        saved_from = await world.say("always rinse")
        saved = await world.insights.insert(
            InsightWrite(
                text="Rinse.",
                source="user",
                confirmed=True,
                thread_id=world.thread,
                from_message_id=saved_from,
            )
        )
        for insight_id in (added, saved):
            refused = await client.post(f"{API}/insights/{insight_id}/dismiss")
            assert refused.status_code == 409
            assert refused.json()["error"]["code"] == "INSIGHT_NOT_WAITING"

    async def test_an_older_chat_written_general_row_with_a_thread_is_not_a_request(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)
        for confirmed in (False, True):
            old = await world.insights.insert(
                InsightWrite(
                    text=f"Older chat code wrote this ({confirmed}).",
                    source="chat",
                    confirmed=confirmed,
                    thread_id=world.thread,
                )
            )
            row = await world.insights.get(old)
            assert row is not None and not row.is_general_request
            refused = await client.post(f"{API}/insights/{old}/dismiss")
            assert refused.status_code == 409
            assert refused.json()["error"]["code"] == "INSIGHT_NOT_RECIPE_OWNED"

    async def test_one_rule_says_what_a_waiting_request_is(self, app: FastAPI) -> None:
        world = await kitchen(app)
        waiting = await world.insights.get(await world.request())
        assert waiting is not None and waiting.is_waiting_general_request
        for change in (
            {"confirmed": True},
            {"dismissed": True},
            {"thread_id": None},
            {"source": "chat"},
            {"recipe_id": world.a},
        ):
            assert not waiting.model_copy(update=change).is_waiting_general_request, change
        # The block's reader and the dismiss route read the same rule: the SQL names exactly it.
        from gaggiclanker.db.repos.knowledge_insights import GENERAL_REQUEST_SQL

        assert GENERAL_REQUEST_SQL == (
            "i.recipe_id IS NULL AND i.source = 'user' AND i.thread_id IS NOT NULL"
        )


class TestSavingAMessage:
    async def test_it_writes_an_added_general_insight_filed_under_the_messages_conversation(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)
        message = await world.say("always rinse the basket")

        saved = await data(
            await client.post(
                f"{API}/insights",
                json={
                    "text": "Always rinse the basket.",
                    "scope": {"grinder_id": world.niche},
                    "from_message_id": message,
                    "confirmed": True,
                },
            ),
            201,
        )

        assert (saved["confirmed"], saved["source"]) == (True, "user")
        assert (saved["thread_id"], saved["from_message_id"]) == (world.thread, message)
        assert saved["recipe_id"] is None

    async def test_only_the_persons_own_message_can_be_saved(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)
        agent = await world.say("The agent said so.", role="assistant")
        for message in (agent, 9999):
            refused = await client.post(
                f"{API}/insights", json={"text": "Nope.", "from_message_id": message}
            )
            assert refused.status_code == 422, refused.text

    async def test_the_thread_serves_the_added_insights_made_from_each_message(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        world = await kitchen(app)
        saved_from = await world.say("always rinse the basket")
        waiting_from = await world.say("remember the Niche chokes")
        other = await world.say("hello")
        saved = await world.insights.insert(
            InsightWrite(
                text="Rinse.",
                source="user",
                confirmed=True,
                thread_id=world.thread,
                from_message_id=saved_from,
            )
        )
        waiting = await world.insights.insert(
            InsightWrite(
                text="Waiting.",
                source="user",
                confirmed=False,
                thread_id=world.thread,
                from_message_id=waiting_from,
            )
        )

        def saved_by_message(detail: dict[str, Any]) -> dict[int, list[int]]:
            return {m["id"]: [s["id"] for s in m["saved_insights"]] for m in detail["messages"]}

        detail = await data(await client.get(f"/api/chat/threads/{world.thread}"))
        # An unanswered request is no saved insight; the added one is, on its message only.
        assert saved_by_message(detail) == {saved_from: [saved], waiting_from: [], other: []}

        await client.patch(f"{API}/insights/{waiting}", json={"confirmed": True})
        detail = await data(await client.get(f"/api/chat/threads/{world.thread}"))
        assert saved_by_message(detail)[waiting_from] == [waiting]

        # "Still present": once it is deleted the message no longer says it is saved.
        assert (await client.delete(f"{API}/insights/{saved}")).status_code == 200
        detail = await data(await client.get(f"/api/chat/threads/{world.thread}"))
        assert saved_by_message(detail)[saved_from] == []


class TestWhoAGeneralInsightIsFrom:
    async def test_the_prompt_line_says_so_and_an_older_one_gets_no_mark(
        self, app: FastAPI
    ) -> None:
        world = await kitchen(app)
        insights = world.insights
        note = await insights.insert(
            InsightWrite(
                text="Mine.",
                source="user",
                confirmed=True,
                scope=InsightScope(grinder_id=world.niche),
            )
        )
        waiting = await insights.insert(
            InsightWrite(text="Not yet.", source="user", scope=InsightScope(grinder_id=world.niche))
        )
        run = await world.db.execute("INSERT INTO pattern_runs (status) VALUES ('done')")
        pattern = await insights.insert(
            InsightWrite(
                text="Found.", source="user", pattern_run_id=int(run.lastrowid or 0), confirmed=True
            )
        )
        old_chat = await insights.insert(InsightWrite(text="Old.", source="chat", confirmed=True))
        old_analysis = await insights.insert(
            InsightWrite(text="Older.", source="analysis", confirmed=True)
        )

        lines = {
            insight_id: (await insights.get(insight_id)).render()  # type: ignore[union-attr]
            for insight_id in (note, pattern, old_chat, old_analysis)
        }

        assert lines[note] == f"#{note} [your note; grinder_id={world.niche}] Mine."
        # A request nobody has added is not their instruction: no mark.
        waiting_row = await insights.get(waiting)
        assert waiting_row is not None
        assert waiting_row.render() == f"#{waiting} [grinder_id={world.niche}] Not yet."
        assert lines[pattern] == f"#{pattern} [from Find patterns; any shot] Found."
        assert lines[old_chat] == f"#{old_chat} [any shot] Old."
        assert lines[old_analysis] == f"#{old_analysis} [any shot] Older."


class TestWhereADismissedRequestIsRead:
    """A dismissed request reaches no prompt except the conversation's own block.

    ``dismiss`` always un-confirms, so the filter on ``dismissed`` only ever matters for a row
    that is both: the state is forced here, once, to show each reader would otherwise carry it.
    """

    async def test_no_reader_of_added_insights_carries_it(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        from gaggiclanker.patterns.context import build_patterns_input

        world = await kitchen(app)
        insight_id = await world.request("Dismissed, but forced added.", grinder_id=world.niche)
        await world.db.execute(
            "UPDATE knowledge_insights SET confirmed = 1, confirmed_at = updated_at, "
            "dismissed = 1 WHERE id = ?",
            (insight_id,),
        )

        # The precondition: without the dismissed filter, a reader of added insights has it.
        rows = await world.db.fetch_all(
            "SELECT id FROM knowledge_insights WHERE recipe_id IS NULL AND confirmed = 1"
        )
        assert insight_id in [row["id"] for row in rows]

        assert insight_id not in [row.id for row in await world.insights.list_insights()]
        assert insight_id not in [
            row.id for row in await world.insights.select({"grinder_id": world.niche})
        ]
        assert insight_id not in [row.id for row in await world.insights.for_recipe(world.a)]
        listed = await data(await client.get(f"{API}/insights", params={"recipe_id": world.a}))
        assert insight_id not in [row["id"] for row in listed["items"]]
        found = await build_patterns_input(world.db)
        assert insight_id not in [item.id for item in found.general]


class TestWhatGetInsightsShowsInAGeneralChat:
    async def test_a_waiting_request_is_not_the_persons_note_and_a_dismissed_one_is_absent(
        self, app: FastAPI
    ) -> None:
        from gaggiclanker.tools.registry import registry
        from gaggiclanker.tools.scope import ToolScope
        from tests.knowledge.chat_tools import ctx_for

        world = await kitchen(app)
        waiting = await world.request("Waiting words.")
        # The precondition: with the flag, the waiting row is among what get_insights returns.
        got = await registry.dispatch(
            ctx_for(app, ToolScope(), world.thread), "get_insights", {"include_unconfirmed": True}
        )
        lines = [item["line"] for item in got.data["insights"] if item["id"] == waiting]
        assert len(lines) == 1 and "your note" not in lines[0]

        forced = await world.insights.insert(
            InsightWrite(text="forced", source="user", confirmed=True, thread_id=world.thread)
        )
        await world.db.execute(
            "UPDATE knowledge_insights SET dismissed = 1, confirmed = 1 WHERE id = ?", (forced,)
        )
        for flag in (False, True):
            got = await registry.dispatch(
                ctx_for(app, ToolScope(), world.thread),
                "get_insights",
                {"include_unconfirmed": flag},
            )
            assert forced not in [item["id"] for item in got.data["insights"]]
