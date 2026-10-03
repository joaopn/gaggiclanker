"""The Knowledge page's routes for Find patterns, over the real app.

The LLM service's provider factory hands back the scripted fake, so a route test spends no
tokens and the thing under test is the route. The world is built against the app's own
database handle.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.chat.context import opening_context
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.patterns.service import PATTERNS_TASK, PatternsService
from gaggiclanker.settings import EnvSettings
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.conftest import running_app
from tests.llm.conftest import FakeProvider, api_error
from tests.patterns.world import PatternWorld, Talking, build_pattern_world, build_talking

BASE = "/api/knowledge/patterns"


@pytest.fixture
async def api(
    env: EnvSettings, provider: FakeProvider
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, FakeProvider]]:
    async with running_app(env) as (app, client):
        app.state.llm = LlmService(
            app.state.settings_service,
            observer=app.state.llm.observer,
            budget=RateLimitBudget(retries=0),
            mode_memory=ModeMemory(),
            provider_factory=lambda _config, _name: provider,
        )
        app.state.patterns = PatternsService(
            app.state.db,
            app.state.llm,
            PromptService(PromptsRepository(app.state.db)),
            bus=app.state.events,
        )
        app.state.patterns.retry_delay_s = 0.0
        yield app, client, provider


async def _talking(app: FastAPI) -> Talking:
    return await build_talking(await build_pattern_world(app.state.db))


def _answer(talking: Talking, **over: Any) -> str:
    proposal: dict[str, Any] = {
        "text": "The Niche channels below 9 clicks with light roasts.",
        "scope": {"roast_level": "light", "grinder_id": talking.world.grinder_id},
        "source_insight_ids": [talking.a_grinder, talking.b_grinder],
        "replaces_insight_id": None,
    }
    proposal.update(over)
    return json.dumps({"proposals": [proposal]})


async def _settle(app: FastAPI) -> None:
    task = app.state.tasks.get(PATTERNS_TASK)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 10)


async def _run(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(f"{BASE}/runs?wait=1", json={})
    assert response.status_code == 202, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def _waiting(client: httpx.AsyncClient) -> dict[str, Any]:
    data = (await client.get(BASE)).json()["data"]
    (proposal,) = [item for item in data["proposals"] if item["status"] == "proposed"]
    return dict(proposal)


async def test_before_any_run_the_page_has_nothing_but_the_numbers(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, _provider = api
    await _talking(app)

    body = (await client.get(BASE)).json()

    assert body["ok"] is True
    assert body["data"] == {
        "run": None,
        "proposals": [],
        "new_since_last_run": 4,
        "counted_from": None,
        "sets_with_insights": 3,
        "min_sets": 2,
    }


async def test_a_press_runs_and_the_page_then_shows_the_proposal_and_a_reset_count(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, provider = api
    talking = await _talking(app)
    provider.script = [_answer(talking)]

    run = await _run(client)

    assert run["status"] == "done" and run["proposals_kept"] == 1
    data = (await client.get(BASE)).json()["data"]
    assert data["run"]["id"] == run["id"]
    assert data["new_since_last_run"] == 0
    (proposal,) = data["proposals"]
    assert proposal["status"] == "proposed" and proposal["run_id"] == run["id"]
    assert [source["set_name"] for source in proposal["sources"]] == ["Guji daily", "Yirg daily"]
    detail = (await client.get(f"{BASE}/runs/{run['id']}")).json()["data"]
    assert [item["id"] for item in detail["input"]["sets"]] == [
        talking.world.a,
        talking.world.b,
        talking.world.c,
    ]


async def test_a_press_answers_202_with_the_running_row_and_a_second_press_gets_it_back(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, provider = api
    await _talking(app)
    provider.delay = 0.3

    first = await client.post(f"{BASE}/runs", json={})
    second = await client.post(f"{BASE}/runs", json={})

    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["status"] == "running"
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    await _settle(app)
    assert len(provider.calls) == 1


async def test_fewer_than_two_sets_is_a_409_with_its_own_code_and_nothing_echoed(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, provider = api
    world = await build_pattern_world(app.state.db)
    await world.own(world.a, "Only one Set has said anything.")

    response = await client.post(f"{BASE}/runs", json={"model": "secret-model-name"})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "PATTERNS_NOT_ENOUGH_SETS"
    assert "secret-model-name" not in response.text
    assert provider.calls == []


async def test_a_provider_failure_still_answers_2xx_with_a_failed_row(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, provider = api
    await _talking(app)
    provider.script = [api_error(401, "bad key")]

    run = await _run(client)

    assert run["status"] == "failed" and run["error"].startswith("auth:")
    data = (await client.get(BASE)).json()["data"]
    assert data["run"]["status"] == "failed" and data["proposals"] == []


async def test_a_failed_run_leaves_the_earlier_proposals_standing(
    api: tuple[FastAPI, httpx.AsyncClient, FakeProvider],
) -> None:
    app, client, provider = api
    talking = await _talking(app)
    provider.script = [_answer(talking)]
    first_run = await _run(client)
    provider.script = [api_error(401, "bad key")]
    provider.calls.clear()

    failed = await _run(client)

    data = (await client.get(BASE)).json()["data"]
    assert data["run"]["id"] == failed["id"] and data["run"]["status"] == "failed"
    # The count still runs from the run that finished, not from the failed one.
    assert data["counted_from"] == first_run["created_at"]
    assert [item["status"] for item in data["proposals"]] == ["proposed"]


async def test_a_run_left_running_by_a_dead_process_is_interrupted_at_the_next_boot(
    env: EnvSettings,
) -> None:
    async with running_app(env) as (app, _client):
        await app.state.db.execute("INSERT INTO pattern_runs (status) VALUES ('running')")
    async with running_app(env) as (app, client):
        data = (await client.get(BASE)).json()["data"]
        assert data["run"]["status"] == "interrupted"
        assert data["run"]["error"]
        assert PATTERNS_TASK not in app.state.tasks.names


class TestApproving:
    async def test_it_writes_a_general_insight_and_deletes_the_sources(
        self, api: tuple[FastAPI, httpx.AsyncClient, FakeProvider]
    ) -> None:
        app, client, provider = api
        talking = await _talking(app)
        provider.script = [_answer(talking)]
        await _run(client)
        proposal = await _waiting(client)

        response = await client.post(f"{BASE}/proposals/{proposal['id']}/approve")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["proposal"]["status"] == "approved" and data["skipped"] == []
        assert sorted(data["deleted_insight_ids"]) == sorted([talking.a_grinder, talking.b_grinder])
        general = (await client.get("/api/knowledge/insights")).json()["data"]["items"]
        written = [item for item in general if item["pattern_run_id"] is not None]
        assert [(i["text"], i["confirmed"]) for i in written] == [
            ("The Niche channels below 9 clicks with light roasts.", True)
        ]
        assert (await client.get(f"/api/knowledge/insights/{talking.a_grinder}")).status_code == 404
        # The card after the sources are gone still says what they said.
        again = (await client.get(BASE)).json()["data"]["proposals"][0]
        assert next(source["text"] for source in again["sources"]).startswith("Below 9 clicks")

    async def test_a_source_deleted_in_the_window_is_named_and_the_approval_succeeds(
        self, api: tuple[FastAPI, httpx.AsyncClient, FakeProvider]
    ) -> None:
        app, client, provider = api
        talking = await _talking(app)
        provider.script = [
            _answer(
                talking,
                scope={},
                source_insight_ids=[talking.a_grinder, talking.b_grinder, talking.c_grinder],
            )
        ]
        await _run(client)
        proposal = await _waiting(client)
        await client.delete(f"/api/knowledge/insights/{talking.c_grinder}")

        response = await client.post(f"{BASE}/proposals/{proposal['id']}/approve")

        assert response.status_code == 200
        skipped = response.json()["data"]["skipped"]
        assert [(item["insight_id"], item["reason"]) for item in skipped] == [
            (talking.c_grinder, "gone")
        ]

    async def test_fewer_than_two_sets_left_is_a_409_and_nothing_is_written(
        self, api: tuple[FastAPI, httpx.AsyncClient, FakeProvider]
    ) -> None:
        app, client, provider = api
        talking = await _talking(app)
        provider.script = [_answer(talking)]
        await _run(client)
        proposal = await _waiting(client)
        await client.delete(f"/api/knowledge/insights/{talking.a_grinder}")

        response = await client.post(f"{BASE}/proposals/{proposal['id']}/approve")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PATTERN_TOO_FEW_SETS"
        assert (await client.get(f"/api/knowledge/insights/{talking.b_grinder}")).status_code == 200
        general = (await client.get("/api/knowledge/insights")).json()["data"]["items"]
        assert [item for item in general if item["pattern_run_id"] is not None] == []

    async def test_a_second_approval_and_an_unknown_one_are_refused(
        self, api: tuple[FastAPI, httpx.AsyncClient, FakeProvider]
    ) -> None:
        app, client, provider = api
        talking = await _talking(app)
        provider.script = [_answer(talking)]
        await _run(client)
        proposal = await _waiting(client)
        await client.post(f"{BASE}/proposals/{proposal['id']}/approve")

        again = await client.post(f"{BASE}/proposals/{proposal['id']}/approve")
        unknown = await client.post(f"{BASE}/proposals/999/approve")

        assert again.status_code == 409
        assert again.json()["error"]["code"] == "PATTERN_PROPOSAL_DECIDED"
        assert unknown.status_code == 404

    async def test_a_replacement_deletes_the_general_insight_it_names(
        self, api: tuple[FastAPI, httpx.AsyncClient, FakeProvider]
    ) -> None:
        app, client, provider = api
        talking = await _talking(app)
        provider.script = [_answer(talking, replaces_insight_id=talking.general)]
        await _run(client)
        proposal = await _waiting(client)
        assert proposal["replaces_text"] == "Rinse the portafilter between shots."

        await client.post(f"{BASE}/proposals/{proposal['id']}/approve")

        assert (await client.get(f"/api/knowledge/insights/{talking.general}")).status_code == 404


class TestDismissing:
    async def test_a_dismissed_proposal_stays_for_one_run_and_is_gone_after_the_next(
        self, api: tuple[FastAPI, httpx.AsyncClient, FakeProvider]
    ) -> None:
        app, client, provider = api
        talking = await _talking(app)
        provider.script = [_answer(talking)]
        await _run(client)
        proposal = await _waiting(client)

        response = await client.post(f"{BASE}/proposals/{proposal['id']}/dismiss")
        assert response.status_code == 200
        assert response.json()["data"]["proposal"]["status"] == "dismissed"
        # Nothing was written or deleted.
        assert (await client.get(f"/api/knowledge/insights/{talking.a_grinder}")).status_code == 200
        shown = (await client.get(BASE)).json()["data"]["proposals"]
        assert [item["status"] for item in shown] == ["dismissed"]

        provider.script = [json.dumps({"proposals": []})]
        await _run(client)

        assert (await client.get(BASE)).json()["data"]["proposals"] == []
        count = await app.state.db.fetch_value("SELECT COUNT(*) FROM pattern_proposals")
        assert count == 0

    async def test_dismissing_twice_or_an_unknown_one_is_refused(
        self, api: tuple[FastAPI, httpx.AsyncClient, FakeProvider]
    ) -> None:
        app, client, provider = api
        talking = await _talking(app)
        provider.script = [_answer(talking)]
        await _run(client)
        proposal = await _waiting(client)
        await client.post(f"{BASE}/proposals/{proposal['id']}/dismiss")

        again = await client.post(f"{BASE}/proposals/{proposal['id']}/dismiss")
        unknown = await client.post(f"{BASE}/proposals/999/dismiss")

        assert again.status_code == 409
        assert unknown.status_code == 404


class TestWhatTheSourceSetsAreToldAfterApproval:
    """Rule 5: each source Set's conversations are told the lesson exactly once, as a general one.

    A byte comparison of the opening context and of `get_insights`, before and after: they
    differ only in that the source insights' lines are replaced by the one general line; a Set
    that was no source is byte-identical.
    """

    async def _context(self, app: FastAPI, world: PatternWorld, set_id: int) -> str:
        row = await world.sets.get(set_id)
        assert row is not None and row.current_version_id is not None
        return await opening_context(
            app.state.db, ToolScope.for_thread(set_id, row.current_version_id)
        )

    async def _tool(self, app: FastAPI, world: PatternWorld, set_id: int) -> list[dict[str, Any]]:
        row = await world.sets.get(set_id)
        assert row is not None and row.current_version_id is not None
        ctx = ToolContext(
            db=app.state.db,
            settings=SettingsService(app.state.settings_service.repo),
            scope=ToolScope.for_thread(set_id, row.current_version_id),
            caller="test",
            permissions=CHAT_PERMISSIONS,
        )
        result = await registry.dispatch(ctx, "get_insights", {})
        return list(result.data["insights"])

    async def test_the_lesson_is_told_once_and_nothing_else_moves(
        self, api: tuple[FastAPI, httpx.AsyncClient, FakeProvider]
    ) -> None:
        app, client, provider = api
        talking = await _talking(app)
        world = talking.world
        provider.script = [_answer(talking)]
        await _run(client)
        proposal = await _waiting(client)
        before = {s: await self._context(app, world, s) for s in (world.a, world.b, world.c)}
        tools_before = {s: await self._tool(app, world, s) for s in (world.a, world.b, world.c)}

        response = await client.post(f"{BASE}/proposals/{proposal['id']}/approve")
        assert response.status_code == 200

        written = response.json()["data"]["proposal"]["insight_id"]
        line = (
            f"- #{written} [roast_level=light · grinder_id={world.grinder_id}] "
            "The Niche channels below 9 clicks with light roasts."
        )
        gone = {world.a: talking.a_grinder, world.b: talking.b_grinder}
        for set_id, insight_id in gone.items():
            lines = before[set_id].split("\n")
            kept = [text for text in lines if not text.startswith(f"- #{insight_id} [")]
            assert len(kept) == len(lines) - 1
            header = kept.index("CONFIRMED INSIGHTS THAT APPLY HERE")
            # Newest first, and the general insight is the newest row in the archive.
            expected = "\n".join([*kept[: header + 1], line, *kept[header + 1 :]])
            assert await self._context(app, world, set_id) == expected
            assert (await self._context(app, world, set_id)).count(line) == 1

            after_tool = await self._tool(app, world, set_id)
            assert [item for item in after_tool if item["id"] == written] != []
            rest = [item for item in after_tool if item["id"] != written]
            assert rest == [item for item in tools_before[set_id] if item["id"] != insight_id]
            assert len([item for item in after_tool if item["id"] == written]) == 1
        # C was no source and is light-roast-free: not one byte moves.
        assert await self._context(app, world, world.c) == before[world.c]
        assert await self._tool(app, world, world.c) == tools_before[world.c]


class TestNoAnswerWhileARunIsGoing:
    async def _held(
        self, app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider, talking: Talking
    ) -> tuple[int, asyncio.Event, Any]:
        provider.script = [_answer(talking)]
        await _run(client)
        proposal_id = (await _waiting(client))["id"]
        gate = asyncio.Event()
        real = app.state.llm.call_json

        async def slow(request: Any) -> Any:
            await gate.wait()
            return await real(request)

        app.state.llm.call_json = slow
        provider.script = [json.dumps({"proposals": []})]
        started = await client.post(f"{BASE}/runs", json={})
        assert started.json()["data"]["status"] == "running"
        return proposal_id, gate, real

    @pytest.mark.parametrize("answer", ["approve", "dismiss"])
    async def test_both_answers_are_a_409_while_a_run_is_going(
        self, api: tuple[FastAPI, httpx.AsyncClient, FakeProvider], answer: str
    ) -> None:
        app, client, provider = api
        talking = await _talking(app)
        proposal_id, gate, real = await self._held(app, client, provider, talking)
        try:
            response = await client.post(f"{BASE}/proposals/{proposal_id}/{answer}")
        finally:
            gate.set()
            await _settle(app)
            app.state.llm.call_json = real

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PATTERNS_RUNNING"
        assert (await client.get(f"/api/knowledge/insights/{talking.a_grinder}")).status_code == 200

    async def test_a_dismissal_is_never_lost_to_a_run_that_was_not_told(
        self, api: tuple[FastAPI, httpx.AsyncClient, FakeProvider]
    ) -> None:
        """Dismissed during a run, deleted by it, told to no run: why answers wait."""
        app, client, provider = api
        talking = await _talking(app)
        proposal_id, gate, real = await self._held(app, client, provider, talking)
        refused = await client.post(f"{BASE}/proposals/{proposal_id}/dismiss")
        gate.set()
        await _settle(app)
        app.state.llm.call_json = real
        assert refused.status_code == 409

        # The run is over: the earlier proposal is gone with it, and nothing was left
        # dismissed-but-untold.
        data = (await client.get(BASE)).json()["data"]
        assert [item["id"] for item in data["proposals"]] != [proposal_id]
        count = await app.state.db.fetch_value(
            "SELECT COUNT(*) FROM pattern_proposals WHERE status = 'dismissed'"
        )
        assert count == 0


async def test_the_llm_stream_carries_a_runs_lifecycle_and_nothing_unrelated() -> None:
    """The header's LLM activity watches this stream, as it does for a review."""
    from gaggiclanker.api.llm import _call_stream
    from gaggiclanker.infra.sse import SseEvent
    from gaggiclanker.llm.observer import LlmCallObserver

    class Bus:
        async def stream(self) -> AsyncIterator[SseEvent]:
            for name in (
                "patterns.started",
                "shot.ingested",
                "patterns.finished",
                "patterns.failed",
            ):
                yield SseEvent(event=name, data={})

    seen = [event.event async for event in _call_stream(LlmCallObserver(), Bus())]

    assert seen == ["llm.snapshot", "patterns.started", "patterns.finished", "patterns.failed"]
