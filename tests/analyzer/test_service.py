"""Running an analysis against a fake provider.

The behaviours under test are the ones the chunk's acceptance criteria name and
the ones a person notices when they go wrong: a provider failure is a stored row
rather than an exception, an unparseable reply is the same, the rate-limit latch
stops a batch instead of grinding through it, and a process that died mid-call
leaves something a page can render.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from gaggiclanker.analyzer.context import build_context
from gaggiclanker.analyzer.service import AnalyzerService
from gaggiclanker.db.repos.analyses import AnalysesRepository, AnalysisStart
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.service import LlmService
from tests.analyzer.conftest import GOOD_OUTPUT, Fixture
from tests.llm.conftest import FakeProvider, api_error


async def test_a_successful_run_stores_everything(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    row = await analyzer.run_analysis(fixture.shots[-1])

    assert row.status == "ok"
    assert row.output is not None
    assert row.output["diagnosis"].startswith("The shot ran four seconds fast")
    assert row.set_version_id == fixture.version_id
    assert row.finished_at
    assert row.prompt_name == "analysis"
    # Both prompts' versions, joined: an edited layout and an edited persona are
    # different analyses and the ledger has one column for it.
    assert "+" in row.prompt_version
    assert row.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    # The ledger row holding the rendered prompt and the raw reply.
    assert row.llm_call_id

    # The input snapshot is the context, verbatim, so the analysis stays
    # explainable after the prompt and the rules have moved on.
    assert row.input is not None
    assert row.input["shot"]["device_id"] == "000106"
    assert row.input["style"] == "bloom"
    assert len(row.input["trajectory"]) == 5

    assert [item.variable for item in row.suggestions] == ["grind", "yield", "pressure"]
    assert [item.priority for item in row.suggestions] == [1, 2, 3]
    assert row.suggestions[0].direction == "finer"
    assert row.suggestions[0].magnitude == 2
    assert all(item.status == "open" for item in row.suggestions)

    # The whole context reached the provider.
    sent = "\n".join(message.content for message in provider.calls[-1].messages)
    assert "Guji natural on the Niche" in sent
    assert "shot 000101" in sent
    assert "expert barista" in sent


async def test_a_provider_failure_is_a_stored_row(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The chunk's fourth acceptance criterion."""
    provider.script = [api_error(401, "bad key")]

    row = await analyzer.run_analysis(fixture.shots[-1])

    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("auth:")
    assert row.output is None
    assert row.suggestions == []
    assert row.finished_at


async def test_an_unparseable_reply_is_a_failed_row(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The corrective turn happens inside the LLM layer; this is what is left."""
    provider.script = ["not json at all"]

    row = await analyzer.run_analysis(fixture.shots[-1])

    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("invalid_output:")


async def test_a_reply_missing_a_required_field_is_a_failed_row(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    broken = {key: value for key, value in GOOD_OUTPUT.items() if key != "diagnosis"}
    provider.script = [json.dumps(broken)]

    row = await analyzer.run_analysis(fixture.shots[-1])
    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("invalid_output:")


async def test_an_invented_rule_citation_is_dropped(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A fabricated citation is dropped, not allowed to fail the whole answer."""
    output = dict(GOOD_OUTPUT, rules_used=["hierarchy", "no_such_rule", "hierarchy"])
    provider.script = [json.dumps(output)]

    row = await analyzer.run_analysis(fixture.shots[-1])

    assert row.status == "ok"
    assert row.output is not None
    assert row.output["rules_used"] == ["hierarchy"]


async def test_the_rate_limit_latch_returns_without_touching_the_provider(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider, budget: RateLimitBudget
) -> None:
    budget.latch()

    row = await analyzer.run_analysis(fixture.shots[-1])

    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("rate_limited:")
    assert provider.calls == [], "the latch exists so that nothing leaves the box"


async def test_a_cancelled_call_leaves_a_running_row(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A cancellation is not a provider failure, so it is not a `failed` row.

    It stays `running` and boot reconciliation turns it into `interrupted`,
    which is what actually happened to it.
    """
    provider.delay = 5.0
    task = asyncio.create_task(analyzer.run_analysis(fixture.shots[-1]))
    # Cancel once the call is in flight, not after a fixed sleep. Before it
    # reaches the provider the analysis has written its `running` row on a
    # database thread, and on a machine busy with a parallel suite that took
    # longer than the fifty milliseconds this used to wait: the cancellation
    # landed before the row existed and the test found no row at all.
    async with asyncio.timeout(5):
        while not provider.calls:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    rows = await AnalysesRepository(fixture.db).for_shot(fixture.shots[-1])
    assert [row.status for row in rows] == ["running"]


async def test_boot_reconciliation_marks_running_rows_interrupted(fixture: Fixture) -> None:
    analyses = AnalysesRepository(fixture.db)
    analysis_id = await analyses.start(
        AnalysisStart(shot_id=fixture.shots[-1], set_version_id=fixture.version_id)
    )

    assert await analyses.reconcile_running() == 1

    row = await analyses.get(analysis_id)
    assert row is not None
    assert row.status == "interrupted"
    assert row.error is not None
    assert "stopped before this analysis finished" in row.error
    assert row.finished_at
    # Idempotent: the second boot has nothing left to reconcile.
    assert await analyses.reconcile_running() == 0


async def test_an_interrupted_row_reads_as_failed_in_a_list(fixture: Fixture) -> None:
    """Four states on the list, not five: to a reader the two are the same."""
    from gaggiclanker.db.repos.shots import ShotsRepository

    analyses = AnalysesRepository(fixture.db)
    await analyses.start(AnalysisStart(shot_id=fixture.shots[-1]))
    await analyses.reconcile_running()

    page = await ShotsRepository(fixture.db).list_shots(limit=10)
    states = {row.device_id: row.analysis_state for row in page.items}
    assert states["000106"] == "failed"
    assert states["000104"] == "none"


async def test_a_batch_runs_every_unanalysed_shot(
    analyzer: AnalyzerService, fixture: Fixture
) -> None:
    result = await analyzer.analyse_set(fixture.set_id)

    # Six shots; four of the five earlier ones already carry a fixture analysis.
    assert result.requested == 2
    assert result.succeeded == 2
    assert result.failed == 0
    assert result.stopped is False

    # And a second pass has nothing left to do.
    assert (await analyzer.analyse_set(fixture.set_id)).requested == 0


async def test_a_batch_can_be_told_to_redo_everything(
    analyzer: AnalyzerService, fixture: Fixture
) -> None:
    result = await analyzer.analyse_set(fixture.set_id, only_unanalysed=False)
    assert result.requested == 6
    assert result.succeeded == 6


async def test_a_batch_is_bounded_and_stops_at_the_latch(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider, budget: RateLimitBudget
) -> None:
    """Two at a time, and once the latch is set the rest are never attempted."""
    provider.script = [api_error(429, "slow down")]

    result = await analyzer.analyse_set(fixture.set_id, only_unanalysed=False)

    assert budget.stopped is True
    assert result.stopped is True
    assert result.requested == 6
    # The two that were already in flight failed; the other four were never
    # attempted at all. That is the latch working: "one shot failed and the LLM
    # is stopped" rather than "six shots each failed after three retries".
    assert result.failed == 2
    assert result.succeeded == 0
    assert result.failed + result.succeeded < result.requested


async def test_a_shot_that_does_not_exist_is_the_callers_mistake(
    analyzer: AnalyzerService,
) -> None:
    with pytest.raises(LookupError):
        await analyzer.run_analysis(999_999)


async def test_events_are_published_on_the_llm_bus(fixture: Fixture, llm: LlmService) -> None:
    from gaggiclanker.db.repos.llm import PromptsRepository
    from gaggiclanker.infra.sse import EventBus, SseEvent
    from gaggiclanker.llm.prompts import PromptService

    bus: EventBus[SseEvent] = EventBus()
    service = AnalyzerService(
        fixture.db, llm, PromptService(PromptsRepository(fixture.db)), bus=bus
    )
    seen: list[SseEvent] = []
    with bus.subscribe() as queue:
        await service.run_analysis(fixture.shots[-1])
        while not queue.empty():
            seen.append(queue.get_nowait())

    names = [event.event for event in seen if event.event.startswith("analysis.")]
    assert names == ["analysis.started", "analysis.finished"]
    assert seen[-1].data["shot_id"] == fixture.shots[-1]


async def test_an_invented_excerpt_citation_is_dropped(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A heading path is followed by a reader, so a fabricated one is worse than none."""
    context = await build_context(fixture.db, fixture.shots[-1])
    real = sorted(context.excerpt_paths)[0]
    provider.script = [json.dumps(dict(GOOD_OUTPUT, excerpts_used=[real, "MADE_UP#nowhere", real]))]

    row = await analyzer.run_analysis(fixture.shots[-1])

    assert row.status == "ok"
    assert row.output is not None
    assert row.output["excerpts_used"] == [real]


async def test_proposed_insights_are_stored_unconfirmed_and_linked(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The safety property: a proposal is a row to confirm, never a fact."""
    provider.script = [
        json.dumps(
            dict(
                GOOD_OUTPUT,
                proposed_insights=[
                    {
                        "scope": {"grinder_id": fixture.grinder_id, "process": "natural"},
                        "text": "This bag wants a longer pre-infusion than the rules say.",
                        "evidence_shot_ids": [fixture.shots[0], fixture.shots[-1]],
                    }
                ],
            )
        )
    ]

    row = await analyzer.run_analysis(fixture.shots[-1])

    assert row.status == "ok"
    insights = await InsightsRepository(fixture.db).list_insights(analysis_id=row.id)
    assert len(insights) == 1
    stored = insights[0]
    assert stored.confirmed is False
    assert stored.source == "analysis"
    assert stored.analysis_id == row.id
    assert stored.scope.stated() == {"process": "natural", "grinder_id": fixture.grinder_id}
    assert stored.evidence_shot_ids == sorted([fixture.shots[0], fixture.shots[-1]])

    # And it stays out of the next analysis's context until somebody confirms it.
    context = await build_context(fixture.db, fixture.shots[-1])
    assert stored.text not in {insight["text"] for insight in context.insights}


async def test_invented_evidence_shot_ids_are_dropped(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """An insight is only checkable if its evidence is real."""
    provider.script = [
        json.dumps(
            dict(
                GOOD_OUTPUT,
                proposed_insights=[
                    {
                        "scope": {},
                        "text": "A claim with half-invented evidence.",
                        "evidence_shot_ids": [fixture.shots[0], 99_999],
                    }
                ],
            )
        )
    ]

    row = await analyzer.run_analysis(fixture.shots[-1])

    assert row.output is not None
    assert row.output["proposed_insights"][0]["evidence_shot_ids"] == [fixture.shots[0]]


async def test_too_many_proposed_insights_are_trimmed_not_refused(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A list of eight is one pattern and seven restatements of this shot.

    Trimmed rather than rejected, and asserted as such: bounding it in the
    schema turned a third proposal into a validation failure, a corrective turn
    and a second paid call — over a field whose whole point is that a person
    triages it. The first two are kept, in the order the model gave them.
    """
    too_many = [
        {"scope": {}, "text": f"Claim {index}.", "evidence_shot_ids": []} for index in range(5)
    ]
    # One scripted reply, because there must be no second call.
    provider.script = [json.dumps(dict(GOOD_OUTPUT, proposed_insights=too_many))]

    row = await analyzer.run_analysis(fixture.shots[-1])

    assert row.status == "ok"
    assert len(provider.calls) == 1, "trimming must not cost a corrective turn"
    assert row.output is not None
    assert [item["text"] for item in row.output["proposed_insights"]] == ["Claim 0.", "Claim 1."]
    stored = await InsightsRepository(fixture.db).list_insights(analysis_id=row.id)
    assert [insight.text for insight in stored] == ["Claim 0.", "Claim 1."]
