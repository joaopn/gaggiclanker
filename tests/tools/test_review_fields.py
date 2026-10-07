"""A shot's review as shot information: what the chat reads, and what it never does.

A review is a model's claims about one shot, each kept until a person rejects it. The chat reads
it through the one renderer, like every other item: the Review group of the catalogue, at the
base tier. These tests pin the rule the whole feature rests on (nothing rejected teaches, and
the summary never does), one surface at a time, each as a test that fails when its filter is
removed:

* the rendered shot at every tier, and the shot tools that serve it;
* the Checks lines, where a free-text expectation's answer counts unless rejected;
* the Set chat's opening context;
* the SQL tool's views.

And the smaller things: an unreviewed shot says so, the newest finished review is the one shown,
a person's tiers move the group's items like any other, and the glossary says each line was
written by a model.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.reviews import (
    ClaimWrite,
    EvidenceStored,
    ReviewOutcome,
    ReviewStart,
    ShotReviewsRepository,
)
from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite
from gaggiclanker.shotinfo import CATALOGUE, default_tiers
from gaggiclanker.shotinfo.catalogue import REVIEW_GROUP
from gaggiclanker.shotinfo.glossary import render_glossary
from gaggiclanker.tools.registry import ToolContext, registry
from tests.review.conftest import Fixture, confirm_free_text

REVIEW_KEYS = [item.key for item in CATALOGUE if item.group == REVIEW_GROUP]

#: Distinctive words, so a test can find a claim, the summary or an unconfirmed answer anywhere.
CLAIM = "The cup was already full before the decline began."
OTHER_CLAIM = "A stray observation the person rejected about the preinfusion."
SUMMARY = "Distinctive summary: cup full early."
ANSWER = "Distinctive free-text answer: pressure and flow rose together."
STANCE = "Distinctive stance text: the shot was faster."


def _SHOT_TOOLS(ctx: ToolContext, set_ctx: ToolContext) -> list[tuple[ToolContext, str]]:
    """Every shot tool that renders a shot, each with the context it is allowed in."""
    return [(ctx, "get_shot"), (ctx, "get_shot_extended"), (set_ctx, "get_shot_full")]


async def call(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert outcome.ok, outcome.data
    return outcome.data


def _evidence(value: float | None = 36.4, *, held: bool | None = None) -> EvidenceStored:
    return EvidenceStored(
        expression={"channel": "cup_weight", "op": "at_end", "window": {"phase": "Pressurise"}},
        sentence="cup weight at the end of the Pressurise",
        value=value,
        unit="g",
        kind="measured",
        absent=None if value is not None else "empty_window",
        why=None if value is not None else "no recorded sample falls in the window",
        held=held,
    )


async def _reading(
    db: Database,
    shot_id: int,
    *,
    claims: list[ClaimWrite] | None = None,
    summary: str = SUMMARY,
    status: str = "ok",
    model: str = "careful",
) -> int:
    """A reading, written through the repository the service writes through."""
    repo = ShotReviewsRepository(db)
    review_id = await repo.start(ReviewStart(shot_id=shot_id, model=model))
    if status == "ok":
        outcome = ReviewOutcome(
            status="ok",
            summary=summary,
            claims=claims
            if claims is not None
            else [
                ClaimWrite(
                    kind="claim",
                    window={"phase": "Pressurise"},
                    window_text="the Pressurise",
                    phase="Pressurise",
                    start_s=10.0,
                    end_s=27.75,
                    fault="early yield",
                    text=CLAIM,
                    evidence=[_evidence()],
                ),
            ],
        )
    else:
        outcome = ReviewOutcome(status="failed", error="auth: bad key")
    await repo.finish(review_id, outcome)
    return review_id


async def _answer(db: Database, review_id: int, position: int, *, keep: bool) -> None:
    repo = ShotReviewsRepository(db)
    review = await repo.get(review_id)
    assert review is not None
    result = await repo.answer(review_id, review.claims[position].id, keep=keep)
    assert result.refused is None


def test_the_review_group_is_three_base_items_none_locked() -> None:
    tiers = default_tiers()
    assert REVIEW_KEYS == ["review_state", "review_claims", "review_prediction"]
    assert {tiers[key] for key in REVIEW_KEYS} == {"base"}
    assert not [item.key for item in CATALOGUE if item.key in REVIEW_KEYS and item.locked]
    # The retired taste items and the summary are gone, by the word.
    retired = {
        "review_taste_balance",
        "review_taste_body",
        "review_taste_confidence",
        "review_description",
        "review_summary",
        "review_written_at",
        "review_model",
    }
    assert not [item.key for item in CATALOGUE if item.key in retired]


async def test_an_unreviewed_shot_says_so_and_shows_no_claim(
    ctx: ToolContext, archive: Fixture
) -> None:
    for tool in ("get_shot", "get_shot_full"):
        text = (await call(ctx, tool, shot_id=archive.shots[-1]))["text"]
        assert "Review: not reviewed" in text, tool
        assert "Review claims" not in text, tool


async def test_a_kept_claim_is_in_every_rendering_with_its_numbers(
    ctx: ToolContext, set_ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    await _reading(archive.db, shot)

    base = (await call(ctx, "get_shot", shot_id=shot))["text"]
    extended = (await call(ctx, "get_shot_extended", shot_id=shot))["text"]
    full = (await call(set_ctx, "get_shot_full", shot_id=shot))["text"]
    compared = (await call(ctx, "compare_shots", shot_ids=[shot, archive.shots[0]]))["shots"]

    for text in (base, full, compared[0]["text"]):
        assert f"[{REVIEW_GROUP}]" in text
        assert "1 claim kept, 0 rejected" in text
        assert "by careful" in text
        assert (
            f"the Pressurise (10-27.75 s): early yield: {CLAIM} "
            "[cup weight at the end of the Pressurise: 36.4 g]"
        ) in text
    # The review is base: the extended-only rendering holds the extended items only.
    assert "Review claims" not in extended
    # The other shot was never reviewed.
    assert "Review: not reviewed" in compared[1]["text"]


async def test_the_summary_is_never_served_to_the_chat(
    ctx: ToolContext, set_ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    await _reading(archive.db, shot)

    for tool_context, tool in (
        (ctx, "get_shot"),
        (ctx, "get_shot_extended"),
        (set_ctx, "get_shot_full"),
    ):
        assert SUMMARY not in (await call(tool_context, tool, shot_id=shot))["text"], tool
    compared = (await call(ctx, "compare_shots", shot_ids=[shot, archive.shots[0]]))["shots"]
    assert SUMMARY not in json.dumps(compared)
    listed = await call(set_ctx, "list_set_shots")
    assert SUMMARY not in json.dumps(listed)


async def test_a_rejected_claim_is_only_counted_and_every_other_is_served(
    ctx: ToolContext, set_ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    claims = [
        ClaimWrite(kind="claim", text=CLAIM, fault="early yield", evidence=[_evidence()]),
        ClaimWrite(kind="claim", text="A claim nobody answered, kept all the same."),
        ClaimWrite(kind="claim", text=OTHER_CLAIM),
    ]
    review_id = await _reading(archive.db, shot, claims=claims)
    await _answer(archive.db, review_id, 2, keep=False)

    for tool_context, tool in (
        (ctx, "get_shot"),
        (ctx, "get_shot_extended"),
        (set_ctx, "get_shot_full"),
    ):
        text = (await call(tool_context, tool, shot_id=shot))["text"]
        assert OTHER_CLAIM not in text, tool
        if tool != "get_shot_extended":  # the base items are not in the extended rendering
            assert "2 claims kept, 1 rejected" in text, tool
            assert CLAIM in text, tool
            assert "A claim nobody answered, kept all the same." in text, tool


async def test_a_prediction_stance_is_served_unless_rejected(
    ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    claims = [
        ClaimWrite(kind="claim", text=CLAIM),
        ClaimWrite(
            kind="prediction", stance="partly", text=STANCE, evidence=[_evidence(held=True)]
        ),
    ]
    review_id = await _reading(archive.db, shot, claims=claims)

    text = (await call(ctx, "get_shot", shot_id=shot))["text"]
    assert f"Review, against the prediction: partly as predicted: {STANCE}" in text
    assert "cup weight at the end of the Pressurise: 36.4 g (held)" in text

    await _answer(archive.db, review_id, 1, keep=False)
    assert STANCE not in (await call(ctx, "get_shot", shot_id=shot))["text"]

    await _answer(archive.db, review_id, 1, keep=True)
    assert STANCE in (await call(ctx, "get_shot", shot_id=shot))["text"]


async def test_a_claim_the_numbers_do_not_bear_out_says_so_to_the_chat(
    ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    claims = [
        ClaimWrite(kind="claim", text=CLAIM, evidence=[_evidence(None)], supported=False),
    ]
    await _reading(archive.db, shot, claims=claims)

    text = (await call(ctx, "get_shot", shot_id=shot))["text"]
    assert "not measured (no recorded sample falls in the window)" in text
    assert "(the numbers do not bear this out)" in text


async def test_a_claim_the_numbers_do_not_bear_out_still_reaches_the_chat(
    ctx: ToolContext, archive: Fixture
) -> None:
    """Left out of the badge, not out of what the model is told (unless it is rejected)."""
    shot = archive.shots[-1]
    claims = [
        ClaimWrite(
            kind="claim",
            text=CLAIM,
            fault="early yield",
            evidence=[_evidence(None)],
            supported=False,
        )
    ]
    review_id = await _reading(archive.db, shot, claims=claims)
    text = (await call(ctx, "get_shot", shot_id=shot))["text"]
    assert CLAIM in text and "(the numbers do not bear this out)" in text

    await _answer(archive.db, review_id, 0, keep=False)
    assert CLAIM not in (await call(ctx, "get_shot", shot_id=shot))["text"]


async def test_the_structured_claims_value_and_the_fields_route_never_carry_a_rejected_claim(
    archive: Fixture,
) -> None:
    """The accessor behind `review_claims` (the structured value) filters like the text does, and
    `/fields` never serves a rejected one (its review block names the kept claims only)."""
    from gaggiclanker.shotinfo import ITEMS
    from gaggiclanker.shotinfo.fields import shot_fields
    from gaggiclanker.shotinfo.render import load_shots

    shot = archive.shots[-1]
    claims = [
        ClaimWrite(kind="claim", text=CLAIM, fault="early yield"),
        ClaimWrite(kind="claim", text=OTHER_CLAIM, fault="unstable"),
        ClaimWrite(kind="free_text", expectation_id=1, held=True, text=ANSWER),
    ]
    review_id = await _reading(archive.db, shot, claims=claims)
    await _answer(archive.db, review_id, 1, keep=False)
    await _answer(archive.db, review_id, 2, keep=False)

    (facts,) = await load_shots(archive.db, [shot])
    found = ITEMS["review_claims"].field(facts)
    assert found is not None
    assert [entry["text"] for entry in found.value] == [CLAIM]  # type: ignore[union-attr,index]
    assert OTHER_CLAIM not in json.dumps(found.value) and ANSWER not in json.dumps(found.value)

    fields = await shot_fields(archive.db, shot)
    assert fields is not None
    served = json.dumps(fields.model_dump(mode="json"))
    assert OTHER_CLAIM not in served and ANSWER not in served
    assert "review_claims" not in {item.name for item in fields.shot}


async def test_the_newest_finished_review_is_the_one_shown(
    ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    first_claims = [ClaimWrite(kind="claim", text="First claim.")]
    await _reading(archive.db, shot, claims=first_claims)
    await _reading(archive.db, shot, claims=[ClaimWrite(kind="claim", text="Second claim.")])
    await _reading(archive.db, shot, status="failed")

    text = (await call(ctx, "get_shot", shot_id=shot))["text"]

    assert "Second claim." in text
    assert "First claim." not in text, "the newest review replaces the old one, kept claims too"
    assert (
        await archive.db.fetch_value("SELECT COUNT(*) FROM shot_reviews WHERE shot_id = ?", (shot,))
        == 3
    )


async def test_a_free_text_answer_is_a_review_claim_and_never_part_of_the_checks(
    ctx: ToolContext, archive: Fixture
) -> None:
    """The Checks group is deterministic; the review's answer to a free-text expectation is a line
    of the Review group while nobody rejects it."""
    shot = archive.shots[-1]
    expectation = await confirm_free_text(archive)
    claims = [
        ClaimWrite(
            kind="free_text",
            expectation_id=expectation,
            held=False,
            fault="unstable",
            text=ANSWER,
            window_text="the Pressurise",
            phase="Pressurise",
            start_s=10.0,
            end_s=27.75,
        )
    ]
    review_id = await _reading(archive.db, shot, claims=claims)

    kept = (await call(ctx, "get_shot", shot_id=shot))["text"]
    assert ANSWER in kept
    assert "failed (unstable)" in kept
    assert "1 claim kept" in kept
    # The checks do not change: no failure is merged into them.
    assert "Pressurise: unstable (red, critical)" not in kept
    extended = (await call(ctx, "get_shot_extended", shot_id=shot))["text"]
    assert "checked by the review" in extended
    assert ANSWER not in extended

    # Rejecting it takes it out of the chat altogether.
    await _answer(archive.db, review_id, 0, keep=False)
    rejected = (await call(ctx, "get_shot", shot_id=shot))["text"]
    assert ANSWER not in rejected and "1 claim kept" not in rejected
    assert "0 claims kept, 1 rejected" in rejected


async def test_the_set_chat_s_opening_context_holds_every_claim_not_rejected(
    archive: Fixture,
) -> None:
    from gaggiclanker.chat.context import opening_context
    from gaggiclanker.tools.scope import ToolScope

    shot = archive.shots[-1]
    claims = [ClaimWrite(kind="claim", text=CLAIM), ClaimWrite(kind="claim", text=OTHER_CLAIM)]
    review_id = await _reading(archive.db, shot, claims=claims)
    await _answer(archive.db, review_id, 1, keep=False)

    text = await opening_context(archive.db, ToolScope.for_thread(archive.set_id))
    assert CLAIM in text
    assert OTHER_CLAIM not in text
    assert SUMMARY not in text
    assert "1 claim kept, 1 rejected" in text


async def test_the_person_s_tiers_move_review_items_like_any_other(
    ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    await _reading(archive.db, shot)
    tiers = ShotInfoTiersRepository(archive.db)

    await tiers.set_tier(ShotInfoTierWrite(item_key="review_claims", tier="extended"))
    await tiers.set_tier(ShotInfoTierWrite(item_key="review_state", tier="excluded"))

    base = (await call(ctx, "get_shot", shot_id=shot))["text"]
    extended = (await call(ctx, "get_shot_extended", shot_id=shot))["text"]
    assert CLAIM not in base and "Review:" not in base
    assert CLAIM in extended


def test_the_glossary_says_each_review_line_was_written_by_a_model() -> None:
    glossary = render_glossary(default_tiers(), "base")
    section = glossary.split(f"[{REVIEW_GROUP}]\n", 1)[1]
    labels = [item.label for item in CATALOGUE if item.group == REVIEW_GROUP]
    for label in labels:
        entry = next(line for line in section.splitlines() if line.startswith(f"- {label} ["))
        assert "[base]" in entry, label
        assert "Written by a model from this shot's data, without the person's judgement" in entry
        # Only the items that carry a claim say a rejected one is never shown; the state counts.
        assert ("never shown" in entry) == (label != "Review"), label
        assert "not a measurement" in entry
    assert "Weigh a claim below the measured numbers" in section
    assert "the review's own summary is never shown" in section


# ── the SQL tool ────────────────────────────────────────────────────


async def _query(ctx: ToolContext, sql: str) -> dict[str, Any]:
    return await call(ctx, "query_shots", sql=sql)


async def test_the_claims_view_holds_the_claims_not_rejected_of_the_newest_finished_review(
    ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    old = await _reading(archive.db, shot, claims=[ClaimWrite(kind="claim", text="Old claim.")])
    claims = [
        ClaimWrite(kind="claim", text=CLAIM, fault="early yield", evidence=[_evidence()]),
        ClaimWrite(kind="claim", text=OTHER_CLAIM),
        ClaimWrite(kind="claim", text="A second kept one."),
    ]
    new = await _reading(archive.db, shot, claims=claims)
    await _answer(archive.db, new, 1, keep=False)

    found = await _query(ctx, "SELECT shot_id, text FROM v_review_claims ORDER BY position")
    assert found["rows"] == [[shot, CLAIM], [shot, "A second kept one."]]

    counts = await _query(
        ctx, "SELECT review_id, kept_claims, rejected_claims FROM v_reviews ORDER BY review_id"
    )
    assert counts["rows"] == [[old, 1, 0], [new, 2, 1]]


async def test_no_view_serves_the_summary_or_a_rejected_claim(
    ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    review_id = await _reading(
        archive.db, shot, claims=[ClaimWrite(kind="claim", text=OTHER_CLAIM)]
    )
    await _answer(archive.db, review_id, 0, keep=False)

    for view in ("v_reviews", "v_review_claims"):
        found = await _query(ctx, "SELECT * FROM " + view)  # noqa: S608 - two literal view names
        assert "summary" not in found["columns"], view
        assert SUMMARY not in json.dumps(found), view
        assert OTHER_CLAIM not in json.dumps(found), view
    # And the tables under them are not readable by column, so there is no other way in.
    refused = await registry.dispatch(ctx, "query_shots", {"sql": "SELECT text FROM review_claims"})
    assert not refused.ok
    refused = await registry.dispatch(
        ctx, "query_shots", {"sql": "SELECT summary FROM shot_reviews"}
    )
    assert not refused.ok


@pytest.mark.parametrize("kind", ["claim", "free_text", "prediction"])
async def test_the_view_serves_every_kind_until_it_is_rejected(
    ctx: ToolContext, archive: Fixture, kind: str
) -> None:
    shot = archive.shots[-1]
    shapes: dict[str, dict[str, Any]] = {
        "claim": {},
        "free_text": {"expectation_id": 1, "held": True},
        "prediction": {"stance": "against"},
    }
    extra = shapes[kind]
    review_id = await _reading(
        archive.db,
        shot,
        claims=[ClaimWrite(kind=kind, text="A statement.", **extra)],  # type: ignore[arg-type]
    )
    assert (await _query(ctx, "SELECT kind FROM v_review_claims"))["rows"] == [[kind]]
    await _answer(archive.db, review_id, 0, keep=False)
    assert (await _query(ctx, "SELECT kind FROM v_review_claims"))["rows"] == []


# ── the review in force is the newest finished one, whatever came after ─────


async def _newer(archive: Fixture, shot: int, status: str) -> int:
    """A reading started after the one in force, in a state that is not finished `ok`."""
    repo = ShotReviewsRepository(archive.db)
    if status == "failed":
        return await _reading(archive.db, shot, status="failed")
    review = await repo.start(ReviewStart(shot_id=shot, model="newer-model"))
    if status == "interrupted":
        assert await repo.reconcile_running() == 1
    return review


@pytest.mark.parametrize("status", ["running", "failed", "interrupted"])
async def test_a_newer_review_that_did_not_finish_hides_nothing_of_the_one_in_force(
    ctx: ToolContext, set_ctx: ToolContext, archive: Fixture, status: str
) -> None:
    """Reviewing again changes the badge's state text and nothing the chat or a query is told."""
    from gaggiclanker.chat.context import opening_context
    from gaggiclanker.tools.scope import ToolScope

    shot = archive.shots[-1]
    expectation = await confirm_free_text(archive)
    claims = [
        ClaimWrite(kind="claim", text=CLAIM, fault="early yield", evidence=[_evidence()]),
        ClaimWrite(kind="claim", text=OTHER_CLAIM),
        ClaimWrite(
            kind="free_text",
            expectation_id=expectation,
            held=False,
            fault="unstable",
            text=ANSWER,
            start_s=12.0,
        ),
    ]
    in_force = await _reading(archive.db, shot, claims=claims)
    await _answer(archive.db, in_force, 1, keep=False)
    await _newer(archive, shot, status)

    for tool_context, tool in (
        (ctx, "get_shot"),
        (set_ctx, "get_shot_full"),
    ):
        text = (await call(tool_context, tool, shot_id=shot))["text"]
        assert "2 claims kept, 1 rejected" in text, (status, tool)
        assert CLAIM in text and ANSWER in text, (status, tool)
        assert OTHER_CLAIM not in text and SUMMARY not in text, (status, tool)
    context = await opening_context(archive.db, ToolScope.for_thread(archive.set_id))
    assert CLAIM in context and OTHER_CLAIM not in context

    # The view serves the same review's claims, not nothing and not a newer one's.
    served = await _query(ctx, "SELECT review_id, kind FROM v_review_claims ORDER BY position")
    assert served["rows"] == [[in_force, "claim"], [in_force, "free_text"]]
    # And a person may still restore what they rejected on it.
    repo = ShotReviewsRepository(archive.db)
    review = await repo.get(in_force)
    assert review is not None
    result = await repo.answer(in_force, review.claims[1].id, keep=True)
    assert result.refused is None and result.changed == 1


async def test_a_newer_review_replaces_the_one_in_force_only_when_it_finishes(
    ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    await _reading(archive.db, shot, claims=[ClaimWrite(kind="claim", text="First claim.")])
    running = await _newer(archive, shot, "running")
    assert "First claim." in (await call(ctx, "get_shot", shot_id=shot))["text"]

    second_claims = [ClaimWrite(kind="claim", text="Second claim.")]
    repo = ShotReviewsRepository(archive.db)
    await repo.finish(running, ReviewOutcome(status="ok", summary="s", claims=second_claims))

    text = (await call(ctx, "get_shot", shot_id=shot))["text"]
    assert "Second claim." in text and "First claim." not in text
