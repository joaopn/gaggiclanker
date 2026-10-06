"""Running a reading against a fake provider.

The behaviours under test are the ones the feature promises and the ones a
person notices when they go wrong: a reading writes one review and its claims
and nothing else, every claim starts `proposed`, the numbers behind a claim are
the evaluator's and never the model's, a provider failure is a stored row rather
than an exception, an answer that is not valid for *this* shot (an unknown phase,
a missing expectation, a taste prediction) is refused and recorded, and a
process that died mid-call leaves something a page can render.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.reviews import (
    ReviewAlreadyRunning,
    ReviewStart,
    ShotReviewsRepository,
)
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.domain.metric_language import Expression, evaluate
from gaggiclanker.infra.sse import EventBus, SseEvent
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.schema import strict_json_schema
from gaggiclanker.llm.service import LlmService
from gaggiclanker.review.context import build_review_input
from gaggiclanker.review.models import build_output_model
from gaggiclanker.review.service import ReviewService
from gaggiclanker.shotinfo.evaluation import stored_data
from tests.llm.conftest import FakeProvider, api_error
from tests.review.conftest import (
    GOOD_REVIEW,
    Fixture,
    confirm_free_text,
    free_text_result,
    predict,
    reading,
)

#: The tables a reading is allowed to write.
OWN_TABLES = {"shot_reviews", "review_claims"}


async def _dump(db: Database) -> dict[str, list[str]]:
    """Every table but a reading's own, row by row, sorted: what "nothing else" means.

    `sqlite_sequence` is compared too, less the counters the reading's own tables own.
    """
    tables = [
        str(row["name"])
        for row in await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    dump: dict[str, list[str]] = {}
    for table in tables:
        if table in OWN_TABLES:
            continue
        rows = await db.fetch_all(f'SELECT * FROM "{table}"')  # noqa: S608 - names from the schema
        dump[table] = sorted(
            repr(tuple(row))
            for row in rows
            if not (table == "sqlite_sequence" and row["name"] in OWN_TABLES)
        )
    return dump


async def test_a_successful_run_stores_the_summary_the_claims_and_its_input(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "ok"
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
    assert row.prediction_given == ""

    # Two claims, in the order the model gave them, every one of them waiting for a person.
    assert [(c.position, c.kind, c.status) for c in row.claims] == [
        (0, "claim", "proposed"),
        (1, "claim", "proposed"),
    ]
    first, second = row.claims
    assert (first.fault, second.fault) == ("fast flow", None)
    assert first.text == GOOD_REVIEW["claims"][0]["text"]
    assert first.answered_at is None and first.reason == ""

    # The input snapshot is what was sent, verbatim.
    detail = await ShotReviewsRepository(fixture.db).detail(row.id)
    assert detail is not None and detail.input is not None
    expected = await build_review_input(fixture.db, fixture.shots[-1])
    assert detail.input == expected.model_dump(mode="json")
    sent = "\n".join(message.content for message in provider.calls[-1].messages)
    assert expected.shot in sent


async def test_a_reading_writes_its_review_and_claims_and_nothing_else(
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
    assert await fixture.db.fetch_value("SELECT COUNT(*) FROM review_claims") == len(row.claims)


async def test_a_failed_run_writes_one_row_and_no_claim(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    provider.script = [api_error(401, "bad key")]
    before = await _dump(fixture.db)

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "failed"
    assert row.claims == []
    assert await _dump(fixture.db) == before
    assert await fixture.db.fetch_value("SELECT COUNT(*) FROM review_claims") == 0


async def test_a_provider_failure_is_a_stored_row(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    provider.script = [api_error(401, "bad key")]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("auth:")
    assert row.summary is None
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


# ── the output model is this shot's ─────────────────────────────────


async def _refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider, answer: dict[str, Any]
) -> str:
    """Run a reading whose every reply is ``answer``: it must fail validation, visibly."""
    provider.script = [json.dumps(answer)]
    before = await _dump(fixture.db)

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "failed", "a reply that does not fit the shot is not a reading"
    assert row.error is not None and row.error.startswith("invalid_output:")
    assert row.claims == []
    assert await _dump(fixture.db) == before
    return row.error


@pytest.mark.parametrize(
    "extra",
    [
        {"taste_prediction": {"balance": "sour", "body": "thin", "confidence": "medium"}},
        {"description": "A paragraph of advice."},
        {"suggestions": [{"variable": "grind", "direction": "finer", "reason": "fast"}]},
        {"profile_patch": [{"phase_index": 1, "field": "duration", "to": "10"}]},
        {"proposed_insights": [{"text": "Naturals want it finer."}]},
        {"questions_for_user": ["What did it taste like?"]},
    ],
    ids=["taste", "description", "suggestions", "profile_patch", "insights", "questions"],
)
async def test_an_answer_with_taste_advice_or_a_proposal_is_refused_and_recorded(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider, extra: dict[str, Any]
) -> None:
    """A reading says what a shot did. The schema has no field for anything else.

    The key is not silently dropped into a successful row: the answer fails validation (twice,
    the corrective turn included) and the row says so.
    """
    await _refused(reviewer, fixture, provider, {**GOOD_REVIEW, **extra})


async def test_a_phase_the_shot_did_not_log_is_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    claim = {**GOOD_REVIEW["claims"][0], "window": {"phase": "Decline"}}
    error = await _refused(reviewer, fixture, provider, reading(claims=[claim]))
    assert "window" in error


async def test_an_anchor_naming_an_unknown_phase_is_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    window = {"from": "shot_start", "to": {"phase_end": "Decline"}}
    claim = {**GOOD_REVIEW["claims"][0], "window": window}
    await _refused(reviewer, fixture, provider, reading(claims=[claim]))


async def test_an_expression_naming_an_unknown_phase_is_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    evidence = [{"channel": "pressure", "op": "mean", "window": {"phase": "Decline"}}]
    claim = {**GOOD_REVIEW["claims"][0], "evidence": evidence}
    await _refused(reviewer, fixture, provider, reading(claims=[claim]))


async def test_a_fault_word_outside_the_list_is_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    claim = {**GOOD_REVIEW["claims"][0], "fault": "sour"}
    await _refused(reviewer, fixture, provider, reading(claims=[claim]))


async def test_a_claim_with_no_evidence_or_with_four_is_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    mean = {"channel": "pressure", "op": "mean"}
    bare = {**GOOD_REVIEW["claims"][0], "evidence": []}
    await _refused(reviewer, fixture, provider, reading(claims=[bare]))
    crowded = {**GOOD_REVIEW["claims"][0], "evidence": [mean] * 4}
    await _refused(reviewer, fixture, provider, reading(claims=[crowded]))


async def test_no_claims_or_thirteen_are_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    await _refused(reviewer, fixture, provider, reading(claims=[]))
    await _refused(reviewer, fixture, provider, reading(claims=[GOOD_REVIEW["claims"][0]] * 13))


async def test_a_summary_longer_than_one_short_sentence_is_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    await _refused(reviewer, fixture, provider, reading(summary="x" * 201))


async def test_a_claim_sentence_longer_than_300_characters_is_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    claim = {**GOOD_REVIEW["claims"][0], "text": "x" * 301}
    await _refused(reviewer, fixture, provider, reading(claims=[claim]))


async def test_a_reply_missing_a_required_field_is_a_failed_row(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    broken = {key: value for key, value in GOOD_REVIEW.items() if key != "summary"}
    await _refused(reviewer, fixture, provider, broken)


async def test_a_phase_number_cannot_bypass_the_phase_names_in_any_window(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """`{"phase_number": 99}` would store a window on nothing: only logged names are allowed."""
    for number in (0, 99):
        window = {"phase_number": number}
        claim = {**GOOD_REVIEW["claims"][0], "window": window}
        await _refused(reviewer, fixture, provider, reading(claims=[claim]))
        evidence = [{"channel": "pressure", "op": "mean", "window": window}]
        claim = {**GOOD_REVIEW["claims"][0], "evidence": evidence}
        await _refused(reviewer, fixture, provider, reading(claims=[claim]))


async def test_a_text_of_only_whitespace_is_refused_everywhere_it_is_asked_for(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """Refused by the schema, so the corrective turn runs; never stored as nothing."""
    expectation = await confirm_free_text(fixture)
    await predict(fixture)
    stance = {"stance": "partly", "text": "A stance.", "evidence": []}
    answer = free_text_result(expectation)
    good = reading(free_text_results=[answer], prediction=stance)
    provider.script = [json.dumps(good)]
    assert (await reviewer.run_review(fixture.shots[-1])).status == "ok"

    blank = " \n\t "
    claim = {**GOOD_REVIEW["claims"][0], "text": blank}
    await _refused(reviewer, fixture, provider, {**good, "claims": [claim]})
    await _refused(reviewer, fixture, provider, {**good, "summary": blank})
    await _refused(
        reviewer, fixture, provider, {**good, "free_text_results": [{**answer, "text": blank}]}
    )
    await _refused(reviewer, fixture, provider, {**good, "prediction": {**stance, "text": blank}})


async def test_a_shot_with_no_phases_admits_no_phase_name_at_all(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The schema offers no phase to name, not a placeholder one; a whole-shot window is fine."""
    shot = await ShotsRepository(fixture.db).insert(
        ShotInsert(
            device_id="000778",
            raw_slog=b"fixture",
            started_at="2026-03-04T08:00:00.000Z",
            duration_ms=31_000,
        )
    )
    claim: dict[str, Any] = {
        "window": {},
        "fault": None,
        "text": "A shot with no record of its phases.",
        "evidence": [{"channel": "pressure", "op": "mean"}],
    }
    provider.script = [json.dumps(reading(claims=[claim]))]
    row = await reviewer.run_review(shot)
    assert row.status == "ok", row.error

    for window in ({"phase": "Pressurise"}, {"phase": "(this shot has no phases)"}):
        provider.script = [json.dumps(reading(claims=[{**claim, "window": window}]))]
        bad = await reviewer.run_review(shot)
        assert bad.status == "failed" and (bad.error or "").startswith("invalid_output:")

    output = build_output_model(phases=[], free_text_ids=[], has_prediction=False)
    window_schema = strict_json_schema(output)["properties"]["claims"]["items"]["properties"][
        "window"
    ]
    assert window_schema["properties"]["phase"] == {"title": "Phase", "type": "null"}


# ── free-text expectations and the prediction ────────────────────────


async def test_every_free_text_expectation_is_answered_exactly_once(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    expectation = await confirm_free_text(fixture)

    # Missing: the schema wants one answer for the one expectation.
    await _refused(reviewer, fixture, provider, GOOD_REVIEW)
    # An id the reading was not asked about.
    stray = free_text_result(expectation + 40)
    await _refused(reviewer, fixture, provider, reading(free_text_results=[stray]))
    # Twice.
    twice = [free_text_result(expectation), free_text_result(expectation)]
    await _refused(reviewer, fixture, provider, reading(free_text_results=twice))
    # An answer to an expectation nobody has: with no signature there is nothing to answer.
    await fixture.db.execute("UPDATE signature_expectations SET status = 'rejected'")
    await _refused(reviewer, fixture, provider, reading(free_text_results=[stray]))


async def test_a_free_text_result_is_a_claim_with_the_expectations_own_fault_word(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    expectation = await confirm_free_text(fixture, fault="unstable")
    provider.script = [
        json.dumps(reading(free_text_results=[free_text_result(expectation, held=False)]))
    ]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "ok"
    kinds = [claim.kind for claim in row.claims]
    assert kinds == ["claim", "claim", "free_text"]
    result = row.claims[-1]
    assert (result.expectation_id, result.held, result.status) == (expectation, False, "proposed")
    # The word is the expectation's, not the model's, and only a failure carries one.
    assert result.fault == "unstable"
    assert result.phase == "Pressurise"
    assert result.evidence[0].value is not None


async def test_a_held_free_text_result_has_no_fault_word(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    expectation = await confirm_free_text(fixture, fault="unstable")
    provider.script = [
        json.dumps(reading(free_text_results=[free_text_result(expectation, held=True)]))
    ]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.claims[-1].held is True
    assert row.claims[-1].fault is None


async def test_the_prediction_is_present_exactly_when_the_version_has_one(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    stance = {
        "stance": "partly",
        "text": "The shot was faster, the cup is not known.",
        "evidence": [],
    }

    # No prediction on the version: a stance for one nobody made is an unknown key.
    await _refused(reviewer, fixture, provider, reading(prediction=stance))

    await predict(fixture)
    # A prediction: leaving the stance out is a missing key.
    await _refused(reviewer, fixture, provider, GOOD_REVIEW)

    provider.script = [json.dumps(reading(prediction=stance))]
    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "ok"
    assert row.prediction_given.startswith("Expect a faster shot")
    last = row.claims[-1]
    assert (last.kind, last.stance, last.status) == ("prediction", "partly", "proposed")
    assert last.fault is None and last.start_s is None


async def test_a_stance_outside_the_four_is_refused(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    await predict(fixture)
    stance = {"stance": "right", "text": "Yes.", "evidence": []}
    await _refused(reviewer, fixture, provider, reading(prediction=stance))


# ── the numbers are the evaluator's ─────────────────────────────────


async def test_every_value_comes_from_the_evaluator_and_the_model_cannot_supply_one(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    shot_id = fixture.shots[-1]

    row = await reviewer.run_review(shot_id)

    data = await stored_data(fixture.db, shot_id)
    assert data is not None
    for answered, stored in zip(GOOD_REVIEW["claims"], row.claims, strict=True):
        for asked, shown in zip(answered["evidence"], stored.evidence, strict=True):
            result = evaluate(Expression.model_validate(asked), data)
            assert (shown.value, shown.unit, shown.kind) == (result.value, result.unit, result.kind)
            assert shown.sentence == result.sentence
            assert shown.held == result.held
    assert row.claims[0].evidence[0].value is not None

    # A model that types a result of its own is refused: there is no key for one.
    typed = {**GOOD_REVIEW["claims"][0]["evidence"][0], "value": 99.0, "held": True}
    claim = {**GOOD_REVIEW["claims"][0], "evidence": [typed]}
    await _refused(reviewer, fixture, provider, reading(claims=[claim]))


async def test_a_share_is_stored_as_a_fraction_and_served_as_a_percentage(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The language's share is a fraction of the target; a page reads it as a check does."""
    asked = {
        "channel": "cup_weight",
        "op": "at_end",
        "window": {"phase": "Pressurise"},
        "relative_to": "target_yield",
        "compare": {"op": "<=", "value": 0.5},
    }
    claim = {**GOOD_REVIEW["claims"][0], "evidence": [asked]}
    provider.script = [json.dumps(reading(claims=[claim]))]

    row = await reviewer.run_review(fixture.shots[-1])

    data = await stored_data(fixture.db, fixture.shots[-1])
    assert data is not None
    share = evaluate(Expression.model_validate(asked), data)
    assert share.unit == "share" and share.value is not None
    (shown,) = row.claims[0].evidence
    assert (shown.unit, shown.value) == ("%", round(share.value * 100, 1))
    assert shown.held is False
    # The limit is worded as a check words it, and the sentence says it in percent too.
    assert shown.limit_text == "at most 50 % of target"
    assert shown.sentence.endswith("as a share of the target yield, at most 50 % of target")
    assert "0.5" not in shown.sentence
    stored = await fixture.db.fetch_value("SELECT evidence_json FROM review_claims LIMIT 1")
    assert json.loads(stored)[0]["unit"] == "share", "stored as the evaluator gave it"


async def test_a_claim_whose_comparison_fails_is_kept_and_marked_unsupported(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    # The fixture's pressurise phase runs well above 0.5 ml/s of puck flow.
    evidence = [
        {
            "channel": "puck_flow",
            "op": "mean",
            "window": {"phase": "Pressurise"},
            "compare": {"op": "<=", "value": 0.5},
        }
    ]
    claim = {**GOOD_REVIEW["claims"][0], "evidence": evidence}
    provider.script = [json.dumps(reading(claims=[claim, GOOD_REVIEW["claims"][1]]))]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "ok"
    first, second = row.claims
    assert first.supported is False
    assert first.evidence[0].held is False
    assert first.evidence[0].value is not None
    assert second.supported is True, "one failing claim does not mark the next"


async def test_a_claim_whose_evidence_is_all_absent_is_kept_and_marked_unsupported(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    # The fixture never recorded the pumped-water counter, so nothing can be measured.
    evidence = [{"channel": "water_pumped", "op": "max", "window": {"phase": "Pressurise"}}]
    claim = {**GOOD_REVIEW["claims"][0], "evidence": evidence}
    provider.script = [json.dumps(reading(claims=[claim]))]

    row = await reviewer.run_review(fixture.shots[-1])

    (stored,) = row.claims
    assert stored.supported is False
    assert stored.evidence[0].value is None
    assert stored.evidence[0].absent, "an absent value says why, in words"


async def test_a_claim_is_supported_when_any_evidence_is_measured_and_nothing_failed(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    evidence = [
        {"channel": "water_pumped", "op": "max", "window": {"phase": "Pressurise"}},
        {"channel": "pressure", "op": "max", "window": {"phase": "Pressurise"}},
    ]
    claim = {**GOOD_REVIEW["claims"][0], "evidence": evidence}
    provider.script = [json.dumps(reading(claims=[claim]))]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.claims[0].supported is True
    assert [e.value is None for e in row.claims[0].evidence] == [True, False]


async def test_the_window_is_resolved_to_seconds_by_the_evaluators_own_window_code(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    span = {"from": {"phase_start": "Bloom", "offset_s": 1}, "to": {"phase_end": "Bloom"}}
    claims = [
        {**GOOD_REVIEW["claims"][0], "window": {"phase": "Pressurise"}},
        {**GOOD_REVIEW["claims"][1], "window": {}},
        {**GOOD_REVIEW["claims"][1], "window": span},
    ]
    provider.script = [json.dumps(reading(claims=claims))]

    row = await reviewer.run_review(fixture.shots[-1])

    pressurise, whole, spanned = row.claims
    assert (pressurise.phase, pressurise.start_s, pressurise.end_s) == ("Pressurise", 10.0, 27.75)
    assert pressurise.window_text == "the Pressurise"
    assert (whole.phase, whole.start_s, whole.end_s) == (None, 0.0, 27.75)
    assert whole.window_text == "the whole shot"
    assert spanned.phase is None and spanned.start_s is not None
    assert spanned.window_text.startswith("the span from 1 s after the start of the Bloom")


# ── citations, bookkeeping ──────────────────────────────────────────


async def test_an_invented_rule_citation_is_dropped(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A fabricated citation is dropped, not allowed to fail the whole answer."""
    output = reading(rules_used=["hierarchy", "no_such_rule", "hierarchy"])
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
    provider.script = [json.dumps(reading(excerpts_used=[real, "MADE_UP#nowhere", real]))]

    row = await reviewer.run_review(fixture.shots[-1])

    assert row.status == "ok"
    assert row.excerpts_used == [real]


async def test_a_second_reading_is_kept_beside_the_first(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """Reading again is allowed; the newest finished one answers for the shot."""
    first = await reviewer.run_review(fixture.shots[-1])
    provider.script = [json.dumps(reading(summary="The second reading."))]
    second = await reviewer.run_review(fixture.shots[-1])
    provider.script = [api_error(401, "bad key")]
    failed = await reviewer.run_review(fixture.shots[-1])

    repo = ShotReviewsRepository(fixture.db)
    assert [row.id for row in await repo.for_shot(fixture.shots[-1])] == [
        failed.id,
        second.id,
        first.id,
    ]
    finished = await repo.finished_for_shot(fixture.shots[-1])
    assert finished is not None and finished.id == second.id
    assert finished.summary == "The second reading."
    assert (await repo.get(first.id)) is not None
    assert len((await repo.get(first.id)).claims) == 2  # type: ignore[union-attr]


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


async def test_a_second_opening_past_the_registry_is_refused_by_the_database(
    fixture: Fixture,
) -> None:
    """One `running` row per shot is a fact the unique index holds, not a convention."""
    reviews = ShotReviewsRepository(fixture.db)
    shot = fixture.shots[-1]
    first = await reviews.start(ReviewStart(shot_id=shot))

    with pytest.raises(ReviewAlreadyRunning):
        await reviews.start(ReviewStart(shot_id=shot))

    assert [row.id for row in await reviews.for_shot(shot)] == [first]
    # Another shot is unaffected, and once the first finishes the shot may be read again.
    other = await reviews.start(ReviewStart(shot_id=fixture.shots[0]))
    assert other != first
    await fixture.db.execute("UPDATE shot_reviews SET status = 'failed' WHERE id = ?", (first,))
    assert await reviews.start(ReviewStart(shot_id=shot)) > first


async def test_two_concurrent_starts_leave_one_running_review(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The registry's name guard and the opening in one transaction: one row, one call."""
    from gaggiclanker.infra.tasks import TaskRegistry

    provider.delay = 0.2
    tasks = TaskRegistry()
    first, second = await asyncio.gather(
        reviewer.start(fixture.shots[-1], tasks=tasks),
        reviewer.start(fixture.shots[-1], tasks=tasks),
    )
    assert first[0].id == second[0].id
    await tasks.cancel_all()
    rows = await ShotReviewsRepository(fixture.db).for_shot(fixture.shots[-1])
    assert len(rows) == 1


async def test_a_reading_another_process_opened_is_handed_back_not_started_again(
    reviewer: ReviewService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The registry only knows this process: the database's index is what the stdio server meets."""
    from gaggiclanker.infra.tasks import TaskRegistry

    repo = ShotReviewsRepository(fixture.db)
    theirs = await repo.start(ReviewStart(shot_id=fixture.shots[-1]))

    row, _started = await reviewer.start(fixture.shots[-1], tasks=TaskRegistry())

    assert (row.id, row.status) == (theirs, "running")
    assert provider.calls == [], "nothing was sent: the reading that is running is theirs"
    assert len(await repo.for_shot(fixture.shots[-1])) == 1


async def test_a_shot_that_does_not_exist_is_the_callers_mistake(
    reviewer: ReviewService,
) -> None:
    with pytest.raises(LookupError):
        await reviewer.run_review(999_999)


async def test_events_are_published_on_the_llm_bus(fixture: Fixture, llm: LlmService) -> None:
    bus: EventBus[SseEvent] = EventBus()
    service = ReviewService(fixture.db, llm, PromptService(PromptsRepository(fixture.db)), bus=bus)
    service.retry_delay_s = 0.0
    seen: list[SseEvent] = []
    with bus.subscribe() as queue:
        row = await service.run_review(fixture.shots[-1])
        await service.confirm_all(row.id)
        while not queue.empty():
            seen.append(queue.get_nowait())

    names = [event.event for event in seen if event.event.startswith("review.")]
    assert names == ["review.started", "review.finished", "review.answered"]
    for event in seen:
        assert event.data["shot_id"] == fixture.shots[-1]
        assert event.data["review_id"] == row.id


async def test_a_reading_runs_on_the_review_model(
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
    assert (await repo.readings_for_shots([shot]))[shot].finished.id == second.id  # type: ignore[union-attr]
    assert (await repo.for_shot(shot))[0].id == second.id
    latest = await repo.latest_for_shot(shot)
    assert latest is not None and latest.id == second.id

    # The newer row stamped earlier than the older one.
    await fixture.db.execute(
        "UPDATE shot_reviews SET created_at = '2026-09-28T08:00:00.000Z', "
        "finished_at = '2026-09-28T08:00:00.000Z' WHERE id = ?",
        (second.id,),
    )
    assert (await repo.readings_for_shots([shot]))[shot].finished.id == second.id  # type: ignore[union-attr]
    assert [row.id for row in await repo.for_shot(shot)] == [second.id, first.id]
