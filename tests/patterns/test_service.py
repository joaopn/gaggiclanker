"""Running Find patterns against a fake provider.

The behaviours under test are the ones the feature promises: a run writes only its own rows,
a provider failure is a stored row rather than an exception, every shown proposal rests on two
Sets, cites only what it was given and states a scope every source Set matches, one run at a
time, and a process that died mid-call leaves a row a page can render.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.patterns import (
    PatternProposalsRepository,
    PatternRunsRepository,
)
from gaggiclanker.infra.sse import EventBus, SseEvent
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.patterns.service import NotEnoughSets, PatternsService
from tests.llm.conftest import FakeProvider, api_error
from tests.patterns.world import Talking, build_pattern_world


def _answer(talking: Talking, **over: Any) -> dict[str, Any]:
    """One good proposal: A's and B's finding about the grinder, scoped by what they share."""
    proposal = {
        "text": "The Niche channels below 9 clicks with light roasts.",
        "scope": {"roast_level": "light", "grinder_id": talking.world.grinder_id},
        "source_insight_ids": [talking.a_grinder, talking.b_grinder],
        "replaces_insight_id": None,
    }
    proposal.update(over)
    return {"proposals": [proposal]}


async def _dump(db: Database) -> dict[str, list[str]]:
    """Every table but the pattern ones, row by row, sorted: what "nothing else" means."""
    tables = [
        str(row["name"])
        for row in await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    dump: dict[str, list[str]] = {}
    for table in tables:
        if table.startswith("pattern_"):
            continue
        rows = await db.fetch_all(f'SELECT * FROM "{table}"')  # noqa: S608 - names from the schema
        dump[table] = sorted(
            repr(tuple(row))
            for row in rows
            if not (table == "sqlite_sequence" and str(row["name"]).startswith("pattern_"))
        )
    return dump


async def test_a_run_stores_its_proposals_and_its_input(
    service: PatternsService, talking: Talking, provider: FakeProvider
) -> None:
    provider.script = [json.dumps(_answer(talking))]

    row = await service.run()

    assert row.status == "done" and row.error is None
    assert (row.insights_read, row.sets_read) == (4, 3)
    assert (row.proposals_kept, row.proposals_dropped) == (1, 0)
    assert row.prompt_name == "patterns" and "+" in row.prompt_version
    assert row.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert row.llm_call_id
    (proposal,) = await PatternProposalsRepository(talking.world.db).for_run(row.id)
    assert proposal.status == "proposed"
    assert proposal.scope.stated() == {
        "roast_level": "light",
        "grinder_id": talking.world.grinder_id,
    }
    assert [(s.insight_id, s.set_name, s.text) for s in proposal.sources] == [
        (talking.a_grinder, "Guji daily", "Below 9 clicks the Niche channels on this bag."),
        (talking.b_grinder, "Yirg daily", "The Niche gushes under 9 with this one."),
    ]
    stored = await PatternRunsRepository(talking.world.db).detail_input(row.id)
    assert stored is not None
    assert [item["id"] for item in stored["sets"]] == [
        talking.world.a,
        talking.world.b,
        talking.world.c,
    ]
    sent = "\n".join(message.content for message in provider.calls[-1].messages)
    assert "The Niche gushes under 9 with this one." in sent
    assert "Not confirmed yet." not in sent and "Still being designed." not in sent


async def test_a_run_writes_its_own_rows_and_no_insight(
    service: PatternsService, talking: Talking, provider: FakeProvider
) -> None:
    """Every other table is byte-for-byte what it was before the run."""
    provider.script = [json.dumps(_answer(talking))]
    before = await _dump(talking.world.db)

    row = await service.run()

    assert row.status == "done" and row.proposals_kept == 1
    assert await _dump(talking.world.db) == before


async def test_a_failed_run_writes_one_row_and_nothing_else(
    service: PatternsService, talking: Talking, provider: FakeProvider
) -> None:
    provider.script = [api_error(401, "bad key")]
    before = await _dump(talking.world.db)

    row = await service.run()

    assert row.status == "failed"
    assert row.error is not None and row.error.startswith("auth:")
    assert row.finished_at
    assert await _dump(talking.world.db) == before
    assert await talking.world.db.fetch_value("SELECT COUNT(*) FROM pattern_proposals") == 0


async def test_a_provider_failure_is_a_stored_row_not_an_exception(
    service: PatternsService, provider: FakeProvider
) -> None:
    provider.script = [api_error(429, "slow down")]

    row = await service.run()

    assert row.status == "failed"


@pytest.mark.parametrize(
    "script",
    [
        "not json at all",
        json.dumps({"proposals": [], "insights_to_delete": [1]}),
        json.dumps({"proposals": [{"text": "x", "scope": {"colour": "red"}}]}),
        json.dumps({"proposals": [{"text": "", "source_insight_ids": [1, 3]}]}),
        json.dumps({"proposals": [{"text": "   ", "source_insight_ids": [1, 3]}]}),
        json.dumps({"proposals": [{"text": "\u00a0 \t\n\u00a0", "source_insight_ids": [1, 3]}]}),
    ],
    ids=[
        "unparseable",
        "extra-key",
        "unknown-scope-key",
        "empty-text",
        "spaces-only",
        "non-breaking-spaces-only",
    ],
)
async def test_an_answer_that_does_not_fit_is_a_failed_run(
    service: PatternsService, provider: FakeProvider, script: str
) -> None:
    provider.script = [script]

    row = await service.run()

    assert row.status == "failed"
    assert row.error is not None and row.error.startswith("invalid_output:")


async def test_whitespace_only_text_never_leaves_a_run_running(
    service: PatternsService, provider: FakeProvider, talking: Talking
) -> None:
    """Refused by the output model, so the corrective turn and `invalid_output` handle it."""
    provider.script = [json.dumps(_answer(talking, text="  \u00a0 "))]

    row = await service.run()

    assert row.status == "failed" and (row.error or "").startswith("invalid_output:")
    assert len(provider.calls) == 2, "the corrective turn was spent"
    latest = await PatternRunsRepository(talking.world.db).latest()
    assert latest is not None and latest.status == "failed"


async def test_anything_raising_after_the_call_closes_the_run_as_failed(
    service: PatternsService,
    provider: FakeProvider,
    talking: Talking,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filtering or storing the answer is not allowed to strand the row as `running`."""
    provider.script = [json.dumps(_answer(talking))]

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the filter broke")

    monkeypatch.setattr("gaggiclanker.patterns.service.filter_proposals", broken)

    with pytest.raises(RuntimeError, match="the filter broke"):
        await service.run()

    runs = PatternRunsRepository(talking.world.db)
    latest = await runs.latest()
    assert latest is not None and latest.status == "failed"
    assert "the filter broke" in (latest.error or "") and latest.finished_at
    assert await runs.latest_running() is None
    # The button is not dead: a new press starts a new run.
    monkeypatch.undo()
    again = await service.run()
    assert again.status == "done" and again.id != latest.id


class TestThePostFilter:
    """Rule 3: every shown proposal is allowed, and every drop is counted on the run."""

    async def _run(
        self, service: PatternsService, provider: FakeProvider, answer: dict[str, Any]
    ) -> tuple[Any, list[Any]]:
        provider.script = [json.dumps(answer)]
        row = await service.run()
        proposals = await PatternProposalsRepository(service.db).for_run(row.id)
        return row, proposals

    async def test_a_proposal_on_one_set_is_dropped_and_counted(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        answer = _answer(talking, source_insight_ids=[talking.a_grinder, talking.a_only])

        row, proposals = await self._run(service, provider, answer)

        assert proposals == []
        assert (row.status, row.proposals_kept, row.proposals_dropped) == ("done", 0, 1)
        assert row.dropped == {"one_set": 1}

    async def test_the_same_insight_twice_is_still_one_set(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        answer = _answer(talking, source_insight_ids=[talking.a_grinder, talking.a_grinder])

        row, proposals = await self._run(service, provider, answer)

        assert proposals == [] and row.dropped == {"one_set": 1}

    @pytest.mark.parametrize("invented", [999, 5, 6])
    async def test_an_invented_source_is_dropped_and_counted(
        self, service: PatternsService, provider: FakeProvider, talking: Talking, invented: int
    ) -> None:
        # 5 is a waiting insight, 6 one in a Set being designed: neither was given.
        answer = _answer(
            talking, source_insight_ids=[talking.a_grinder, talking.b_grinder, invented]
        )

        row, proposals = await self._run(service, provider, answer)

        assert proposals == [] and row.dropped == {"invented_source": 1}

    async def test_a_general_insight_id_is_not_a_source(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        answer = _answer(talking, source_insight_ids=[talking.a_grinder, talking.general])

        row, proposals = await self._run(service, provider, answer)

        assert proposals == [] and row.dropped == {"invented_source": 1}

    async def test_a_scope_key_the_sources_do_not_share_is_dropped_and_counted(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        # A is natural and B is washed.
        answer = _answer(talking, scope={"process": "natural"})

        row, proposals = await self._run(service, provider, answer)

        assert proposals == [] and row.dropped == {"scope_not_shared": 1}

    async def test_a_scope_that_one_source_set_does_not_match_is_dropped(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        # Light roast: A and B match, C (dark) does not.
        answer = _answer(
            talking,
            scope={"roast_level": "light"},
            source_insight_ids=[talking.a_grinder, talking.b_grinder, talking.c_grinder],
        )

        row, proposals = await self._run(service, provider, answer)

        assert proposals == [] and row.dropped == {"scope_not_shared": 1}

    async def test_profile_style_is_never_proposed(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        answer = _answer(talking, scope={"profile_style": "turbo"})

        row, proposals = await self._run(service, provider, answer)

        assert proposals == [] and row.dropped == {"profile_style": 1}

    async def test_an_invented_replaced_insight_is_dropped_and_counted(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        answer = _answer(talking, replaces_insight_id=talking.a_only)  # a Set insight, not general

        row, proposals = await self._run(service, provider, answer)

        assert proposals == [] and row.dropped == {"invented_replaces": 1}

    async def test_the_scope_is_checked_with_the_live_matching_rule(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        """Case and spacing do not matter to `scope_matches`, so they do not to the filter."""
        answer = _answer(talking, scope={"origin": " ethiopia "})

        row, proposals = await self._run(service, provider, answer)

        assert (row.proposals_kept, row.dropped) == (1, {})
        assert len(proposals) == 1

    async def test_an_empty_scope_is_allowed_when_the_sources_share_nothing(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        answer = _answer(
            talking,
            scope={},
            source_insight_ids=[talking.a_grinder, talking.b_grinder, talking.c_grinder],
        )

        row, proposals = await self._run(service, provider, answer)

        assert row.proposals_kept == 1 and proposals[0].scope.stated() == {}

    async def test_a_replaced_general_insight_is_kept_with_its_text(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        answer = _answer(talking, replaces_insight_id=talking.general)

        row, proposals = await self._run(service, provider, answer)

        assert row.proposals_kept == 1
        assert (proposals[0].replaces_id, proposals[0].replaces_text) == (
            talking.general,
            "Rinse the portafilter between shots.",
        )

    async def test_good_proposals_survive_beside_dropped_ones(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        good = _answer(talking)["proposals"][0]
        bad = _answer(talking, scope={"process": "natural"})["proposals"][0]

        row, proposals = await self._run(service, provider, {"proposals": [bad, good, bad]})

        assert (row.proposals_kept, row.proposals_dropped) == (1, 2)
        assert row.dropped == {"scope_not_shared": 2}
        assert [p.text for p in proposals] == [good["text"]]


async def test_declined_proposals_are_told_to_the_run_and_gone_once_it_is_done(
    service: PatternsService, provider: FakeProvider, talking: Talking
) -> None:
    provider.script = [json.dumps(_answer(talking))]
    first = await service.run()
    proposals = PatternProposalsRepository(talking.world.db)
    (waiting,) = await proposals.for_run(first.id)
    await proposals.dismiss(waiting.id)
    provider.script = [json.dumps({"proposals": []})]

    second = await service.run()

    sent = "\n".join(message.content for message in provider.calls[-1].messages)
    assert "PROPOSALS THE PERSON DECLINED" in sent
    assert "The Niche channels below 9 clicks with light roasts." in sent
    assert second.status == "done"
    assert await proposals.declined() == []


async def test_a_run_uses_the_patterns_model(
    service: PatternsService, provider: FakeProvider, llm: LlmService
) -> None:
    await llm.settings.apply(
        {
            "modelDefault": "base-model",
            "modelReview": "review-model",
            "modelPatterns": "patterns-model",
        }
    )

    row = await service.run()

    assert provider.calls[-1].model == "patterns-model"
    assert row.model == "patterns-model"


async def test_events_are_published_on_the_bus(
    talking: Talking, llm: LlmService, service: PatternsService
) -> None:
    bus: EventBus[SseEvent] = EventBus()
    service.bus = bus
    seen: list[SseEvent] = []
    with bus.subscribe() as queue:
        await service.run()
        while not queue.empty():
            seen.append(queue.get_nowait())

    assert [event.event for event in seen] == ["patterns.started", "patterns.finished"]


async def test_the_rate_limit_latch_returns_without_touching_the_provider(
    service: PatternsService, provider: FakeProvider, llm: LlmService
) -> None:
    from gaggiclanker.llm.budget import RateLimitBudget

    assert isinstance(llm.budget, RateLimitBudget)
    llm.budget.latch()

    row = await service.run()

    assert row.status == "failed" and row.error is not None
    assert row.error.startswith("rate_limited:")
    assert provider.calls == []


async def test_a_cancelled_call_leaves_a_running_row_that_boot_marks_interrupted(
    service: PatternsService, provider: FakeProvider, talking: Talking
) -> None:
    provider.delay = 5.0
    task = asyncio.create_task(service.run())
    async with asyncio.timeout(5):
        while not provider.calls:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    runs = PatternRunsRepository(talking.world.db)
    latest = await runs.latest()
    assert latest is not None and latest.status == "running"

    assert await runs.reconcile_running() == 1

    latest = await runs.latest()
    assert latest is not None and latest.status == "interrupted"


class TestOneRunAtATime:
    async def test_a_second_press_while_one_runs_gets_the_running_row(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        provider.delay = 0.3
        tasks = TaskRegistry()

        first, started = await service.start(tasks=tasks)
        second, second_started = await service.start(tasks=tasks)

        assert started and not second_started
        assert first.id == second.id and first.status == "running"
        task = tasks.get("patterns")
        assert task is not None
        await task
        assert len(provider.calls) == 1
        assert await talking.world.db.fetch_value("SELECT COUNT(*) FROM pattern_runs") == 1

    async def test_simultaneous_presses_open_one_row(
        self, service: PatternsService, provider: FakeProvider, talking: Talking
    ) -> None:
        provider.delay = 0.3
        tasks = TaskRegistry()

        results = await asyncio.gather(*(service.start(tasks=tasks) for _ in range(4)))

        assert len({row.id for row, _started in results}) == 1
        assert sorted(started for _row, started in results) == [False, False, False, True]
        task = tasks.get("patterns")
        assert task is not None
        await task
        assert len(provider.calls) == 1

    async def test_once_it_is_done_a_new_press_starts_a_new_run(
        self, service: PatternsService, talking: Talking
    ) -> None:
        tasks = TaskRegistry()
        first, _ = await service.start(tasks=tasks)
        task = tasks.get("patterns")
        if task is not None:
            await task

        second, started = await service.start(tasks=tasks)
        task = tasks.get("patterns")
        if task is not None:
            await task

        assert started and second.id != first.id

    async def test_fewer_than_two_sets_with_insights_starts_nothing(
        self, db: Database, llm: LlmService, provider: FakeProvider
    ) -> None:
        world = await build_pattern_world(db)
        await world.own(world.a, "Only one Set has said anything.")
        service = PatternsService(db, llm, PromptService(PromptsRepository(db)))

        with pytest.raises(NotEnoughSets):
            await service.start(tasks=TaskRegistry())

        assert provider.calls == []
        assert await db.fetch_value("SELECT COUNT(*) FROM pattern_runs") == 0
