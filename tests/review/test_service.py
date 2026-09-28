"""Running a review against a fake provider.

The behaviours under test are the ones the feature promises and the ones a
person notices when they go wrong: a review writes one row about one shot and
nothing else, a provider failure is a stored row rather than an exception, an
answer carrying anything but the three things a review writes is refused and
recorded, and a process that died mid-call leaves something a page can render.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.reviews import ReviewStart, ShotReviewsRepository
from gaggiclanker.infra.sse import EventBus, SseEvent
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.review.context import build_review_input
from gaggiclanker.review.service import ReviewService
from tests.llm.conftest import FakeProvider, api_error
from tests.review.conftest import GOOD_REVIEW, Fixture


async def _dump(db: Database) -> dict[str, list[str]]:
    """Every table but `shot_reviews`, row by row, sorted: what "nothing else" means.

    `sqlite_sequence` is compared too, less the counter `shot_reviews` owns.
    """
    tables = [
        str(row["name"])
        for row in await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    dump: dict[str, list[str]] = {}
    for table in tables:
        if table == "shot_reviews":
            continue
        rows = await db.fetch_all(f'SELECT * FROM "{table}"')  # noqa: S608 - names from the schema
        dump[table] = sorted(
            repr(tuple(row))
            for row in rows
            if not (table == "sqlite_sequence" and row["name"] == "shot_reviews")
        )
    return dump


async def test_a_successful_run_stores_the_three_things_and_its_input(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "ok"
    assert (row.taste_balance, row.taste_body, row.taste_confidence) == ("sour", "thin", "medium")
    assert row.description == GOOD_REVIEW["description"]
    assert row.summary == GOOD_REVIEW["summary"]
    assert row.rules_used == ["hierarchy", "grind"]
    assert row.excerpts_used == []
    assert row.finished_at
    assert row.prompt_name == "review"
    # Both prompts' versions, joined: an edited layout and edited instructions
    # are different reviews and the row has one column for it.
    assert "+" in row.prompt_version
    assert row.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert row.llm_call_id

    # The input snapshot is what was sent, verbatim.
    detail = await ShotReviewsRepository(fixture.db).detail(row.id)
    assert detail is not None and detail.input is not None
    expected = await build_review_input(fixture.db, fixture.shots[-1])
    assert detail.input == expected.model_dump(mode="json")
    sent = "\n".join(message.content for message in provider.calls[-1].messages)
    assert expected.shot in sent
    assert "blind" in sent.lower()


async def test_a_review_writes_one_row_and_nothing_else(
    reviewer: ReviewService, fixture: Fixture
) -> None:
    """Every other table is byte-for-byte what it was before the run."""
    before = await _dump(fixture.db)
    reviews_before = await ShotReviewsRepository(fixture.db).for_shot(fixture.shots[-1])

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "ok"
    assert await _dump(fixture.db) == before
    reviews = await ShotReviewsRepository(fixture.db).for_shot(fixture.shots[-1])
    assert [review.id for review in reviews] == [row.id] + [r.id for r in reviews_before]
    total = await fixture.db.fetch_value("SELECT COUNT(*) FROM shot_reviews")
    assert total == len(reviews_before) + 1


async def test_a_failed_run_writes_one_row_and_nothing_else(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    provider.script = [api_error(401, "bad key")]
    before = await _dump(fixture.db)

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "failed"
    assert await _dump(fixture.db) == before


async def test_a_provider_failure_is_a_stored_row(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    provider.script = [api_error(401, "bad key")]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("auth:")
    assert row.description is None
    assert row.taste_balance is None
    assert row.finished_at


async def test_an_unparseable_reply_is_a_failed_row(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The corrective turn happens inside the LLM layer; this is what is left."""
    provider.script = ["not json at all"]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("invalid_output:")


@pytest.mark.parametrize(
    "extra",
    [
        {"suggestions": [{"variable": "grind", "direction": "finer", "reason": "fast"}]},
        {"profile_patch": [{"phase_index": 1, "field": "duration", "to": "10"}]},
        {"proposed_insights": [{"text": "Naturals want it finer."}]},
        {"questions_for_user": ["What did it taste like?"]},
        {"shot_style": "bloom"},
    ],
    ids=["suggestions", "profile_patch", "insights", "questions", "style_guess"],
)
async def test_an_answer_with_anything_more_is_refused_and_recorded(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider, extra: dict[str, Any]
) -> None:
    """A review writes three things. Anything else fails the run, visibly.

    The key is not silently dropped into a successful row: the answer fails
    validation (twice, the corrective turn included) and the row says so.
    """
    provider.script = [json.dumps({**GOOD_REVIEW, **extra})]
    before = await _dump(fixture.db)

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("invalid_output:")
    assert row.description is None
    assert await _dump(fixture.db) == before


async def test_a_reply_missing_a_required_field_is_a_failed_row(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    broken = {key: value for key, value in GOOD_REVIEW.items() if key != "summary"}
    provider.script = [json.dumps(broken)]

    row = await reviewer.run_review(fixture.shots[-1])
    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("invalid_output:")


async def test_a_summary_longer_than_one_short_sentence_is_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    provider.script = [json.dumps(dict(GOOD_REVIEW, summary="x" * 201))]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "failed"
    assert row.error is not None and row.error.startswith("invalid_output:")


async def test_a_taste_word_outside_the_vocabulary_is_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    taste = {"balance": "sour", "body": "watery", "confidence": "medium"}
    provider.script = [json.dumps(dict(GOOD_REVIEW, taste_prediction=taste))]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "failed"


async def test_an_invented_rule_citation_is_dropped(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A fabricated citation is dropped, not allowed to fail the whole answer."""
    output = dict(GOOD_REVIEW, rules_used=["hierarchy", "no_such_rule", "hierarchy"])
    provider.script = [json.dumps(output)]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "ok"
    assert row.rules_used == ["hierarchy"]


async def test_an_invented_excerpt_citation_is_dropped(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A heading path is followed by a reader, so a fabricated one is worse than none."""
    review = await build_review_input(fixture.db, fixture.shots[-1])
    real = sorted(review.excerpt_paths)[0]
    provider.script = [json.dumps(dict(GOOD_REVIEW, excerpts_used=[real, "MADE_UP#nowhere", real]))]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "ok"
    assert row.excerpts_used == [real]


async def test_a_second_review_is_kept_beside_the_first(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """Re-running is allowed; the newest finished one is the one served."""
    first = await reviewer.run_review(fixture.shots[-1])
    provider.script = [json.dumps(dict(GOOD_REVIEW, summary="The second reading."))]
    second = await reviewer.run_review(fixture.shots[-1])
    provider.script = [api_error(401, "bad key")]
    failed = await reviewer.run_review(fixture.shots[-1])

    repo = ShotReviewsRepository(fixture.db)
    assert [row.id for row in await repo.for_shot(fixture.shots[-1])] == [
        failed.id,
        second.id,
        first.id,
    ]
    served = await repo.latest_finished_for_shots([fixture.shots[-1], fixture.shots[0]])
    assert list(served) == [fixture.shots[-1]]
    assert served[fixture.shots[-1]].id == second.id
    assert served[fixture.shots[-1]].summary == "The second reading."
    assert (await repo.get(first.id)) is not None


async def test_the_rate_limit_latch_returns_without_touching_the_provider(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider, budget: RateLimitBudget
) -> None:
    budget.latch()

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("rate_limited:")
    assert provider.calls == [], "the latch exists so that nothing leaves the box"


async def test_a_cancelled_call_leaves_a_running_row(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A cancellation is not a provider failure, so it is not a `failed` row."""
    provider.delay = 5.0
    task = asyncio.create_task(reviewer.run_review(fixture.shots[-1]))
    # Cancel once the call is in flight, not after a fixed sleep: before it
    # reaches the provider the review has written its `running` row on a
    # database thread, which a loaded machine can make slow.
    async with asyncio.timeout(5):
        while not provider.calls:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    rows = await ShotReviewsRepository(fixture.db).for_shot(fixture.shots[-1])
    assert [row.status for row in rows] == ["running"]


async def test_boot_reconciliation_marks_running_rows_interrupted(fixture: Fixture) -> None:
    reviews = ShotReviewsRepository(fixture.db)
    review_id = await reviews.start(ReviewStart(shot_id=fixture.shots[-1]))

    assert await reviews.reconcile_running() == 1

    row = await reviews.get(review_id)
    assert row is not None
    assert row.status == "interrupted"
    assert row.error is not None
    assert "stopped before this review finished" in row.error
    assert row.finished_at
    # Idempotent: the second boot has nothing left to reconcile.
    assert await reviews.reconcile_running() == 0


async def test_a_shot_that_does_not_exist_is_the_callers_mistake(
    reviewer: ReviewService,
) -> None:
    with pytest.raises(LookupError):
        await reviewer.run_review(999_999)


async def test_events_are_published_on_the_llm_bus(fixture: Fixture, llm: LlmService) -> None:
    bus: EventBus[SseEvent] = EventBus()
    service = ReviewService(fixture.db, llm, PromptService(PromptsRepository(fixture.db)), bus=bus)
    seen: list[SseEvent] = []
    with bus.subscribe() as queue:
        await service.run_review(fixture.shots[-1])
        while not queue.empty():
            seen.append(queue.get_nowait())

    names = [event.event for event in seen if event.event.startswith("review.")]
    assert names == ["review.started", "review.finished"]
    assert seen[-1].data["shot_id"] == fixture.shots[-1]


async def test_a_review_runs_on_the_review_model(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider, llm: LlmService
) -> None:
    """`modelReview` names the model; the chat's and the default are for other calls."""
    await llm.settings.apply(
        {"modelDefault": "base-model", "modelChat": "chat-model", "modelReview": "review-model"}
    )

    row = await reviewer.run_review(fixture.shots[-1])

    assert provider.calls[-1].model == "review-model"
    assert row.model == "review-model"


async def test_newest_is_by_id_whatever_the_timestamps_say(
    reviewer: ReviewService, fixture: Fixture
) -> None:
    """Two finished reviews stamped alike, or a clock that stepped back: the higher id wins."""
    shot = fixture.shots[-1]
    first = await reviewer.run_review(shot)
    second = await reviewer.run_review(shot)
    repo = ShotReviewsRepository(fixture.db)

    stamp = "2026-09-28T09:00:00.000Z"
    await fixture.db.execute(
        "UPDATE shot_reviews SET created_at = ?, finished_at = ? WHERE shot_id = ?",
        (stamp, stamp, shot),
    )
    assert (await repo.latest_finished_for_shots([shot]))[shot].id == second.id
    assert (await repo.for_shot(shot))[0].id == second.id
    latest = await repo.latest_for_shot(shot)
    assert latest is not None and latest.id == second.id

    # The newer row stamped earlier than the older one.
    await fixture.db.execute(
        "UPDATE shot_reviews SET created_at = '2026-09-28T08:00:00.000Z', "
        "finished_at = '2026-09-28T08:00:00.000Z' WHERE id = ?",
        (second.id,),
    )
    assert (await repo.latest_finished_for_shots([shot]))[shot].id == second.id
    assert [row.id for row in await repo.for_shot(shot)] == [second.id, first.id]
    latest = await repo.latest_for_shot(shot)
    assert latest is not None and latest.id == second.id
