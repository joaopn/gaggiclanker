"""A shot's review as shot information: what the chat reads, at which tier.

A review writes three things to a shot, and the chat reads them through the
one renderer, like every other item: the Review group of the catalogue, at the
extended tier by default. These tests pin that the group is where the shot
tools say it is, that the person's tier choices move it like any other item,
that an unreviewed shot shows nothing, that the newest finished review is the
one shown, and that the glossary says each line was written by a model.
"""

from __future__ import annotations

from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.reviews import ReviewOutcome, ReviewStart, ShotReviewsRepository
from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite
from gaggiclanker.shotinfo import CATALOGUE, default_tiers
from gaggiclanker.shotinfo.catalogue import REVIEW_GROUP
from gaggiclanker.shotinfo.glossary import render_glossary
from gaggiclanker.tools.registry import ToolContext, registry
from tests.review.conftest import Fixture

REVIEW_KEYS = [item.key for item in CATALOGUE if item.group == REVIEW_GROUP]


async def call(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert outcome.ok, outcome.data
    return outcome.data


async def _review(
    db: Database, shot_id: int, *, summary: str, status: str = "ok", model: str = "careful"
) -> int:
    """A finished review, written through the repository the service writes through."""
    repo = ShotReviewsRepository(db)
    review_id = await repo.start(ReviewStart(shot_id=shot_id, model=model))
    if status == "ok":
        outcome = ReviewOutcome(
            status="ok",
            taste_balance="bitter",
            taste_body="heavy",
            taste_confidence="low",
            description="Slow and hot: 38 s at 95 °C against a 28 s target.",
            summary=summary,
        )
    else:
        outcome = ReviewOutcome(status="failed", error="auth: bad key")
    await repo.finish(review_id, outcome)
    return review_id


def test_the_review_group_is_seven_extended_items_none_locked() -> None:
    tiers = default_tiers()
    assert REVIEW_KEYS == [
        "review_taste_balance",
        "review_taste_body",
        "review_taste_confidence",
        "review_description",
        "review_summary",
        "review_written_at",
        "review_model",
    ]
    assert {tiers[key] for key in REVIEW_KEYS} == {"extended"}
    assert not [item.key for item in CATALOGUE if item.key in REVIEW_KEYS and item.locked]


async def test_an_unreviewed_shot_shows_nothing_of_a_review(
    ctx: ToolContext, archive: Fixture
) -> None:
    for tool in ("get_shot_extended", "get_shot_full"):
        text = (await call(ctx, tool, shot_id=archive.shots[-1]))["text"]
        assert f"[{REVIEW_GROUP}]" not in text, tool
        assert "Predicted balance" not in text, tool


async def test_a_review_is_in_the_extended_and_full_renderings_and_not_in_base(
    ctx: ToolContext, set_ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    await _review(archive.db, shot, summary="Slow and hot; likely bitter.")

    base = (await call(ctx, "get_shot", shot_id=shot))["text"]
    extended = (await call(ctx, "get_shot_extended", shot_id=shot))["text"]
    full = (await call(set_ctx, "get_shot_full", shot_id=shot))["text"]
    compared = (await call(ctx, "compare_shots", shot_ids=[shot, archive.shots[0]]))["shots"]

    assert f"[{REVIEW_GROUP}]" not in base
    assert "Review summary" not in base
    for text in (extended, full, compared[0]["text"]):
        assert f"[{REVIEW_GROUP}]" in text
        assert 'Review summary: "Slow and hot; likely bitter."' in text
        assert "Predicted balance: Bitter" in text
        assert "Predicted body: heavy" in text
        assert "Prediction confidence: low" in text
        assert 'Review description: "Slow and hot: 38 s at 95 °C against a 28 s target."' in text
        assert "Review model: careful" in text
        assert "Review written: " in text
    # The other shot was never reviewed.
    assert f"[{REVIEW_GROUP}]" not in compared[1]["text"]


async def test_the_newest_finished_review_is_the_one_shown(
    ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    await _review(archive.db, shot, summary="The first reading.")
    await _review(archive.db, shot, summary="The second reading.")
    await _review(archive.db, shot, summary="", status="failed")

    text = (await call(ctx, "get_shot_extended", shot_id=shot))["text"]

    assert "The second reading." in text
    assert "The first reading." not in text
    stored = await archive.db.fetch_value(
        "SELECT COUNT(*) FROM shot_reviews WHERE shot_id = ?", (shot,)
    )
    assert stored == 3


async def test_the_person_s_tiers_move_review_items_like_any_other(
    ctx: ToolContext, archive: Fixture
) -> None:
    shot = archive.shots[-1]
    await _review(archive.db, shot, summary="Slow and hot; likely bitter.")
    tiers = ShotInfoTiersRepository(archive.db)

    await tiers.set_tier(ShotInfoTierWrite(item_key="review_summary", tier="base"))
    await tiers.set_tier(ShotInfoTierWrite(item_key="review_description", tier="excluded"))

    base = (await call(ctx, "get_shot", shot_id=shot))["text"]
    extended = (await call(ctx, "get_shot_extended", shot_id=shot))["text"]
    assert 'Review summary: "Slow and hot; likely bitter."' in base
    assert "Review summary" not in extended
    assert "Review description" not in extended
    assert "Predicted balance: Bitter" in extended


def test_the_glossary_says_each_review_line_was_written_by_a_model() -> None:
    glossary = render_glossary(default_tiers(), "extended")
    section = glossary.split(f"[{REVIEW_GROUP}]\n", 1)[1]
    labels = [item.label for item in CATALOGUE if item.group == REVIEW_GROUP]
    for label in labels:
        entry = next(line for line in section.splitlines() if line.startswith(f"- {label} ["))
        assert "[extended]" in entry, label
        assert "Written by a model from this shot's data, without the person's judgement" in entry
        assert "not a measurement" in entry
    assert "weigh it below the measured numbers" in section
