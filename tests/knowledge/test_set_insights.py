"""An insight belongs to the Set it was learned in.

The rules these pin: an insight written in a Set's conversation carries that Set
and the version the conversation was about and no attribute scope; it reaches
that Set's conversations and nothing else (not another Set on the same bean and
grinder, not the design chat, not the General chat's lists); general insights
keep the attribute matching they always had; a dismissed insight reaches no
prompt; and the Knowledge list is general only. Each reader (the opening
context, `get_insights`, the Set page's route) is asked the same question, so
each is tested against the same data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from gaggiclanker.chat.context import opening_context
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.knowledge_insights import (
    InsightScope,
    InsightsRepository,
    InsightWrite,
)
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite
from gaggiclanker.db.schema import create_schema
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext, registry
from gaggiclanker.tools.scope import ToolScope


@dataclass(slots=True)
class World:
    """Two Sets on one bean and one grinder, and a third on another bean."""

    db: Database
    bean_id: int
    grinder_id: int
    a: int
    a_version: int
    b: int
    b_version: int
    other: int
    insights: InsightsRepository


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    db = Database(tmp_path / "insights.db")
    await db.connect()
    await create_schema(db)
    try:
        bean = await BeansRepository(db).create(
            BeanWrite(name="Guji", roaster="Hasbean", roast_level="light", process="natural")
        )
        elsewhere = await BeansRepository(db).create(
            BeanWrite(name="Huila", roaster="Hasbean", roast_level="dark", process="washed")
        )
        grinder = await GrindersRepository(db).create(
            GrinderWrite(name="Niche Zero", burr_type="conical", step_unit="numbers")
        )
        sets = SetsRepository(db)
        made: list[Any] = []
        for name, bean_id in (("A", bean.id), ("B", bean.id), ("Other", elsewhere.id)):
            row = await sets.create(
                SetWrite(name=name, bean_id=bean_id, grinder_id=grinder.id),
                SetVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22"),
            )
            made.append(row)
        yield World(
            db=db,
            bean_id=bean.id,
            grinder_id=grinder.id,
            a=made[0].id,
            a_version=made[0].current_version_id,
            b=made[1].id,
            b_version=made[1].current_version_id,
            other=made[2].id,
            insights=InsightsRepository(db),
        )
    finally:
        await db.close()


async def _own(
    world: World, set_id: int, version_id: int, text: str, *, confirmed: bool = True
) -> int:
    return await world.insights.insert(
        InsightWrite(
            text=text,
            source="chat",
            confirmed=confirmed,
            set_id=set_id,
            set_version_id=version_id,
        )
    )


async def _general(world: World, text: str, *, confirmed: bool = True, **scope: Any) -> int:
    return await world.insights.insert(
        InsightWrite(text=text, scope=InsightScope(**scope), source="user", confirmed=confirmed)
    )


def _texts(rows: list[Any]) -> list[str]:
    return [row.text for row in rows]


class TestWhatIsStored:
    async def test_it_carries_the_set_and_the_version_and_no_scope(self, world: World) -> None:
        insight_id = await _own(world, world.a, world.a_version, "Finer helps.", confirmed=False)

        stored = await world.insights.get(insight_id)

        assert stored is not None
        assert (stored.set_id, stored.set_version_id) == (world.a, world.a_version)
        assert stored.set_version_label == "v1"
        assert stored.scope.stated() == {}
        assert stored.general is False
        assert stored.dismissed is False
        assert stored.scope_label == "this Set, learned at v1"

    async def test_a_general_insight_is_general(self, world: World) -> None:
        stored = await world.insights.get(await _general(world, "Wash the burrs.", bean_id=1))
        assert stored is not None
        assert stored.general is True and stored.set_id is None
        assert stored.set_version_label is None
        assert stored.scope_label == "bean_id=1"

    def test_an_insight_learned_at_a_version_needs_its_set(self) -> None:
        with pytest.raises(ValidationError):
            InsightWrite(text="x", set_version_id=3)

    def test_a_sets_insight_states_no_attribute_scope(self) -> None:
        with pytest.raises(ValidationError):
            InsightWrite(text="x", set_id=1, scope=InsightScope(bean_id=1))

    async def test_deleting_a_version_keeps_the_insight(self, world: World) -> None:
        insight_id = await _own(world, world.a, world.a_version, "Kept.")
        await world.db.execute("PRAGMA foreign_keys = ON")
        await world.db.execute(
            "UPDATE set_versions SET parent_version_id = NULL WHERE id = ?", (world.a_version,)
        )
        await world.db.execute(
            "UPDATE sets SET current_version_id = NULL WHERE current_version_id = ?",
            (world.a_version,),
        )
        await world.db.execute("DELETE FROM set_versions WHERE id = ?", (world.a_version,))
        stored = await world.insights.get(insight_id)
        assert stored is not None and stored.set_version_id is None

    async def test_dismissing_confirms_nothing_and_adding_undoes_it(self, world: World) -> None:
        insight_id = await _own(world, world.a, world.a_version, "Maybe.", confirmed=False)
        assert await world.insights.dismiss(insight_id)
        stored = await world.insights.get(insight_id)
        assert stored is not None and stored.dismissed and not stored.confirmed

        assert await world.insights.set_confirmed(insight_id, True)
        stored = await world.insights.get(insight_id)
        assert stored is not None and stored.confirmed and not stored.dismissed
        assert stored.confirmed_at is not None

        # Taking it back is unconfirm, not dismiss.
        assert await world.insights.set_confirmed(insight_id, False)
        stored = await world.insights.get(insight_id)
        assert stored is not None and not stored.confirmed and not stored.dismissed


class TestTheOneSelection:
    """Rule 5, at the repository: this Set's own, and general by attributes, and nothing else."""

    async def test_a_sets_insight_never_reaches_another_set_with_the_same_bean_and_grinder(
        self, world: World
    ) -> None:
        await _own(world, world.a, world.a_version, "A's lesson.")

        assert _texts(await world.insights.for_set(world.a)) == ["A's lesson."]
        assert await world.insights.for_set(world.b) == []
        assert await world.insights.for_set(world.other) == []
        # And it is not "general" by any attribute either.
        attributes = await world.insights.attributes_of(world.b)
        assert attributes is not None
        assert await world.insights.select(attributes) == []

    async def test_general_confirmed_insights_still_reach_every_matching_set(
        self, world: World
    ) -> None:
        await _general(world, "Naturals like it finer.", process="natural")
        await _general(world, "This grinder drifts.", grinder_id=world.grinder_id)
        await _general(world, "Dark roasts want it coarser.", roast_level="dark")
        await _general(world, "Everything.")

        for set_id in (world.a, world.b):
            assert _texts(await world.insights.for_set(set_id)) == [
                "Naturals like it finer.",
                "This grinder drifts.",
                "Everything.",
            ]
        assert _texts(await world.insights.for_set(world.other)) == [
            "This grinder drifts.",
            "Dark roasts want it coarser.",
            "Everything.",
        ]

    async def test_a_sets_own_come_with_the_general_ones_oldest_first(self, world: World) -> None:
        await _general(world, "First, general.", process="natural")
        await _own(world, world.a, world.a_version, "Second, A's.")
        await _general(world, "Third, general.")
        assert _texts(await world.insights.for_set(world.a)) == [
            "First, general.",
            "Second, A's.",
            "Third, general.",
        ]

    async def test_unconfirmed_and_dismissed_ones_are_never_selected(self, world: World) -> None:
        await _own(world, world.a, world.a_version, "Waiting.", confirmed=False)
        dismissed = await _own(world, world.a, world.a_version, "Turned down.", confirmed=False)
        await world.insights.dismiss(dismissed)
        await _general(world, "General, waiting.", confirmed=False)

        assert await world.insights.for_set(world.a) == []
        assert [item.text for item in await world.insights.own(world.a)] == ["Waiting."]
        assert [
            item.text for item in await world.insights.own(world.a, include_dismissed=True)
        ] == ["Waiting.", "Turned down."]

    async def test_the_general_list_never_holds_a_sets_insight(self, world: World) -> None:
        await _own(world, world.a, world.a_version, "A's.")
        await _own(world, world.a, world.a_version, "A's, waiting.", confirmed=False)
        await _general(world, "General.")
        assert _texts(await world.insights.list_insights()) == ["General."]
        assert _texts(await world.insights.list_insights(confirmed=False)) == []
        assert await world.insights.count() == 1

    async def test_a_set_that_does_not_exist_selects_nothing(self, world: World) -> None:
        assert await world.insights.for_set(9999) == []


class TestEveryReaderAgrees:
    """The context, `get_insights` and the Set page's route are one selection."""

    async def _seed(self, world: World) -> None:
        await _own(world, world.a, world.a_version, "A's confirmed lesson.")
        await _own(world, world.a, world.a_version, "A's waiting lesson.", confirmed=False)
        dismissed = await _own(world, world.a, world.a_version, "A's dismissed.", confirmed=False)
        await world.insights.dismiss(dismissed)
        await _own(world, world.b, world.b_version, "B's confirmed lesson.")
        await _general(world, "General, for naturals.", process="natural")
        await _general(world, "General, for darks.", roast_level="dark")

    def _ctx(self, world: World, scope: ToolScope) -> ToolContext:
        return ToolContext(
            db=world.db,
            settings=SettingsService(SettingsRepository(world.db)),
            scope=scope,
            caller="test",
            permissions=CHAT_PERMISSIONS,
        )

    async def test_the_opening_context(self, world: World) -> None:
        await self._seed(world)
        a = await opening_context(world.db, ToolScope.for_thread(world.a, world.a_version))
        b = await opening_context(world.db, ToolScope.for_thread(world.b, world.b_version))

        block_a = a.split("CONFIRMED INSIGHTS THAT APPLY HERE\n")[1].split("\n\n")[0]
        block_b = b.split("CONFIRMED INSIGHTS THAT APPLY HERE\n")[1].split("\n\n")[0]
        assert "A's confirmed lesson." in block_a
        assert "General, for naturals." in block_a
        assert "[this Set, learned at v1] A's confirmed lesson." in block_a
        for absent in ("A's waiting", "A's dismissed", "B's confirmed", "for darks"):
            assert absent not in block_a
        assert "B's confirmed lesson." in block_b
        for absent in ("A's confirmed", "A's waiting", "A's dismissed"):
            assert absent not in block_b

    async def test_get_insights_in_a_set_chat(self, world: World) -> None:
        await self._seed(world)
        a = await registry.dispatch(
            self._ctx(world, ToolScope.for_thread(world.a, world.a_version)), "get_insights", {}
        )
        b = await registry.dispatch(
            self._ctx(world, ToolScope.for_thread(world.b, world.b_version)), "get_insights", {}
        )

        assert [i["text"] for i in a.data["insights"]] == [
            "A's confirmed lesson.",
            "General, for naturals.",
        ]
        assert [i["text"] for i in b.data["insights"]] == [
            "B's confirmed lesson.",
            "General, for naturals.",
        ]
        assert "A's" not in str(b.data)

    async def test_a_set_chat_cannot_ask_for_another_sets_selection(self, world: World) -> None:
        await self._seed(world)
        outcome = await registry.dispatch(
            self._ctx(world, ToolScope.for_thread(world.b, world.b_version)),
            "get_insights",
            {"set_id": world.a},
        )
        assert not outcome.ok
        assert "A's" not in str(outcome.data)

    async def test_the_general_chat_reads_general_insights_only(self, world: World) -> None:
        await self._seed(world)
        general = self._ctx(world, ToolScope())

        everything = await registry.dispatch(general, "get_insights", {})
        with_waiting = await registry.dispatch(
            general, "get_insights", {"include_unconfirmed": True}
        )

        assert [i["text"] for i in everything.data["insights"]] == [
            "General, for naturals.",
            "General, for darks.",
        ]
        assert "A's" not in str(with_waiting.data) and "B's" not in str(with_waiting.data)

    async def test_the_general_chat_asking_with_a_set_gets_that_sets_selection(
        self, world: World
    ) -> None:
        await self._seed(world)
        outcome = await registry.dispatch(
            self._ctx(world, ToolScope()), "get_insights", {"set_id": world.a}
        )
        assert [i["text"] for i in outcome.data["insights"]] == [
            "A's confirmed lesson.",
            "General, for naturals.",
        ]

    async def test_the_design_chat_never_reaches_a_sets_insights(self, world: World) -> None:
        await self._seed(world)
        designed = await SetsRepository(world.db).create_design(
            SetWrite(name="New", bean_id=world.bean_id, grinder_id=world.grinder_id),
            _brief(),
        )
        scope = await ToolScope.resolve(world.db, designed.id)
        assert scope.designing
        outcome = await registry.dispatch(self._ctx(world, scope), "get_insights", {})
        assert [i["text"] for i in outcome.data["insights"]] == ["General, for naturals."]


def _brief() -> Any:
    from gaggiclanker.db.repos.sets import DesignBrief

    return DesignBrief()


async def _api_world(client: httpx.AsyncClient, app: FastAPI) -> dict[str, int]:
    bean = (
        await client.post(
            "/api/beans", json={"name": "Guji", "roast_level": "light", "process": "natural"}
        )
    ).json()["data"]["id"]
    grinder = (
        await client.post(
            "/api/grinders", json={"name": "Niche", "burr_type": "conical", "step_unit": "numbers"}
        )
    ).json()["data"]["id"]
    out: dict[str, int] = {"bean": bean, "grinder": grinder}
    for name in ("A", "B"):
        created = (
            await client.post(
                "/api/sets",
                json={
                    "name": name,
                    "bean_id": bean,
                    "grinder_id": grinder,
                    "version": {"dose_g": 18, "target_yield_g": 36, "grind_setting": "22"},
                },
            )
        ).json()["data"]
        out[name] = created["id"]
        out[f"{name}_version"] = created["current_version_id"]
    return out


class TestTheRoutes:
    async def test_the_knowledge_list_is_general_only_and_the_sets_list_is_its_own(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        ids = await _api_world(client, app)
        repo = InsightsRepository(app.state.db)
        await repo.insert(
            InsightWrite(
                text="A's waiting.",
                source="chat",
                set_id=ids["A"],
                set_version_id=ids["A_version"],
            )
        )
        await repo.insert(
            InsightWrite(
                text="A's confirmed.",
                source="chat",
                confirmed=True,
                set_id=ids["A"],
                set_version_id=ids["A_version"],
            )
        )
        gone = await repo.insert(
            InsightWrite(text="A's dismissed.", source="chat", set_id=ids["A"])
        )
        await repo.dismiss(gone)
        await repo.insert(
            InsightWrite(
                text="General, natural.",
                scope=InsightScope(process="natural"),
                source="user",
                confirmed=True,
            )
        )
        await repo.insert(
            InsightWrite(
                text="General, dark.",
                scope=InsightScope(roast_level="dark"),
                source="user",
                confirmed=True,
            )
        )
        await repo.insert(InsightWrite(text="General, waiting.", source="chat"))

        general = (await client.get("/api/knowledge/insights")).json()["data"]["items"]
        assert sorted(item["text"] for item in general) == [
            "General, dark.",
            "General, natural.",
            "General, waiting.",
        ]
        assert all(item["general"] for item in general)

        mine = (await client.get(f"/api/knowledge/insights?set_id={ids['A']}")).json()["data"][
            "items"
        ]
        assert [(item["text"], item["general"]) for item in mine] == [
            ("A's waiting.", False),
            ("A's confirmed.", False),
            ("General, natural.", True),
        ]
        assert mine[0]["set_version_label"] == "v1"
        assert mine[0]["confirmed"] is False and mine[1]["confirmed"] is True

        other = (await client.get(f"/api/knowledge/insights?set_id={ids['B']}")).json()["data"][
            "items"
        ]
        assert [item["text"] for item in other] == ["General, natural."]

    async def test_an_unknown_set_is_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/api/knowledge/insights?set_id=999")).status_code == 404

    async def test_add_dismiss_take_back_and_read_one(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        ids = await _api_world(client, app)
        insight_id = await InsightsRepository(app.state.db).insert(
            InsightWrite(
                text="Finer helps.", source="chat", set_id=ids["A"], set_version_id=ids["A_version"]
            )
        )
        url = f"/api/knowledge/insights/{insight_id}"

        dismissed = (await client.post(f"{url}/dismiss")).json()["data"]
        assert dismissed["dismissed"] is True and dismissed["confirmed"] is False
        assert (await client.get(url)).json()["data"]["dismissed"] is True
        # Shown nowhere else.
        listed = (await client.get(f"/api/knowledge/insights?set_id={ids['A']}")).json()["data"]
        assert listed["items"] == []

        added = (await client.patch(url, json={"confirmed": True})).json()["data"]
        assert added["confirmed"] is True and added["dismissed"] is False
        taken_back = (await client.patch(url, json={"confirmed": False})).json()["data"]
        assert taken_back["confirmed"] is False and taken_back["dismissed"] is False

    async def test_only_a_waiting_insight_is_dismissed(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        ids = await _api_world(client, app)
        repo = InsightsRepository(app.state.db)
        added = await repo.insert(
            InsightWrite(text="Added.", source="chat", confirmed=True, set_id=ids["A"])
        )
        waiting = await repo.insert(InsightWrite(text="Waiting.", source="chat", set_id=ids["A"]))

        refused = await client.post(f"/api/knowledge/insights/{added}/dismiss")
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "INSIGHT_NOT_WAITING"
        assert "Added." not in refused.text
        stored = await repo.get(added)
        assert stored is not None and stored.confirmed and not stored.dismissed

        assert (await client.post(f"/api/knowledge/insights/{waiting}/dismiss")).status_code == 200
        again = await client.post(f"/api/knowledge/insights/{waiting}/dismiss")
        assert again.status_code == 409

    async def test_dismissing_a_general_insight_is_409(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        insight_id = await InsightsRepository(app.state.db).insert(
            InsightWrite(text="General.", source="user", confirmed=True)
        )
        response = await client.post(f"/api/knowledge/insights/{insight_id}/dismiss")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INSIGHT_NOT_SET_OWNED"

    async def test_an_unknown_insight_is_404_everywhere(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/api/knowledge/insights/999")).status_code == 404
        assert (await client.post("/api/knowledge/insights/999/dismiss")).status_code == 404

    async def test_a_sets_insight_takes_no_scope(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        ids = await _api_world(client, app)
        insight_id = await InsightsRepository(app.state.db).insert(
            InsightWrite(text="Finer helps.", source="chat", set_id=ids["A"])
        )
        response = await client.patch(
            f"/api/knowledge/insights/{insight_id}", json={"scope": {"bean_id": ids["bean"]}}
        )
        assert response.status_code == 422
        assert "Finer helps." not in response.text
