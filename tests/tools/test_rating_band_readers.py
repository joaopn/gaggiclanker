"""Every reader that asks "Good, Neutral or Bad?" reads the shared rule, and every counting
reader leaves a discarded shot out first.

A rated shot is Good from four stars, Neutral at three and Bad at one or two, whichever of the
three ratings the Rating column shows it came from: this box's verdict, the machine's notes
card, or the index. Discarded is a separate fact that a shot keeps its stars through. Each test
is written in two halves, as the suite's rule asks of a test that something is counted: the
precondition first (the shot holds the rating in the named place and is not discarded, or is
discarded with the stars that would otherwise count), then the behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.chat.context import _gold_standard
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.outcome_proposals import (
    OutcomeProposalsRepository,
    OutcomeProposalWrite,
)
from gaggiclanker.db.repos.recipes import (
    RecipesRepository,
    RecipeVersionPatch,
    RecipeVersionRow,
    VersionOutcomeWrite,
)
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.models import ShotNotes
from gaggiclanker.domain.spread import pooled_spreads, version_evidence
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.shotinfo.render import load_shots
from gaggiclanker.tools.registry import ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.recipes.conftest import make_shot
from tests.review.conftest import Fixture
from tests.tools.conftest import _context

NOTE = "Time held at 31 s against 28 s; the sourness did not move; the yield held at 36 g."

#: Where a shot's rating can sit: the three sources of the Rating column.
SOURCES = ("judgement", "device_notes", "index_rating")


async def rated_shot(
    archive: Fixture, version_id: int, device_id: str, source: str, stars: int
) -> int:
    """A shot filed under the version with a rating in one place and not discarded."""
    db = archive.db
    shot_id = await make_shot(db, device_id)
    assert await RecipesRepository(db).assign_shot(shot_id, version_id)
    if source == "judgement":
        await JudgementsRepository(db).upsert(shot_id, JudgementWrite(rating=stars))
    elif source == "device_notes":
        await NotesRepository(db).upsert(shot_id, ShotNotes(id=device_id, rating=stars))
    else:
        await db.execute("UPDATE shots SET index_rating = ? WHERE id = ?", (stars, shot_id))
    return shot_id


async def assert_only_rated(db: Database, shot_id: int, source: str, stars: int) -> None:
    """The precondition: not discarded, and the rating is exactly where it was put."""
    row = await db.fetch_one(
        """
        SELECT COALESCE(j.discarded, 0) AS discarded, j.rating AS judgement,
               n.rating AS device_notes, s.index_rating AS index_rating
          FROM shots s
          LEFT JOIN shot_judgements j ON j.shot_id = s.id
          LEFT JOIN device_shot_notes n ON n.shot_id = s.id
         WHERE s.id = ?
        """,
        (shot_id,),
    )
    assert row is not None
    assert row["discarded"] == 0
    for name in SOURCES:
        assert row[name] == (stars if name == source else None)


async def a_version(archive: Fixture, *, prediction: bool = False) -> RecipeVersionRow:
    patch: dict[str, Any] = {"intent": "one click finer", "grind_setting": "21"}
    if prediction:
        patch.update(prediction="Expect two seconds longer.", compares_to_version_id=None)
    version = await RecipesRepository(archive.db).add_version(
        archive.recipe_id, RecipeVersionPatch.model_validate(patch)
    )
    assert version is not None
    return version


async def call(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert outcome.ok, outcome.data
    return outcome.data


def in_version(
    archive: Fixture, settings: SettingsService, version: RecipeVersionRow
) -> ToolContext:
    ctx = _context(archive, settings, ToolScope.for_thread(archive.recipe_id, version.id))
    ctx.thread_id = None
    return ctx


# ── the counted shots: the gold standard and the spread's counts ─────────────────


async def _gold(archive: Fixture) -> str:
    recipes = RecipesRepository(archive.db)
    counted = await recipes.counted_shots(archive.recipe_id)
    lines = _gold_standard(counted, await recipes.versions(archive.recipe_id), set())
    return "\n".join(lines)


@pytest.mark.parametrize("source", SOURCES)
async def test_a_four_star_shot_is_the_gold_standard_wherever_the_rating_sits(
    archive: Fixture, source: str
) -> None:
    version = await a_version(archive)
    # The gold standard names the versions that hold a Good shot: this one holds none yet.
    assert version.version_label not in await _gold(archive)
    shot_id = await rated_shot(archive, version.id, "001001", source, 4)
    await assert_only_rated(archive.db, shot_id, source, 4)

    counted = await RecipesRepository(archive.db).counted_shots(archive.recipe_id)
    assert [s.band for s in counted if s.shot_id == shot_id] == ["good"]
    assert version.version_label in await _gold(archive)


async def test_a_three_star_shot_is_counted_but_is_not_the_gold_standard(
    archive: Fixture,
) -> None:
    version = await a_version(archive)
    shot_id = await rated_shot(archive, version.id, "001004", "judgement", 3)
    await assert_only_rated(archive.db, shot_id, "judgement", 3)

    counted = await RecipesRepository(archive.db).counted_shots(archive.recipe_id)
    assert [s.band for s in counted if s.shot_id == shot_id] == ["neutral"]
    assert version.version_label not in await _gold(archive)


async def test_a_discarded_five_star_shot_is_neither_counted_nor_the_gold_standard(
    archive: Fixture,
) -> None:
    version = await a_version(archive)
    shot_id = await rated_shot(archive, version.id, "001005", "judgement", 5)
    recipes = RecipesRepository(archive.db)
    # The precondition: before the discard this very shot is counted and is the gold standard.
    ours = [s.band for s in await recipes.counted_shots(archive.recipe_id) if s.shot_id == shot_id]
    assert ours == ["good"]
    assert version.version_label in await _gold(archive)

    await JudgementsRepository(archive.db).upsert(shot_id, JudgementWrite(rating=5, discarded=True))

    assert shot_id not in {s.shot_id for s in await recipes.counted_shots(archive.recipe_id)}
    assert version.version_label not in await _gold(archive)


async def test_the_spreads_counts_split_good_neutral_bad_and_unrated(
    archive: Fixture,
) -> None:
    version = await a_version(archive)
    shots = [
        (await rated_shot(archive, version.id, "001002", "judgement", 5), "judgement", 5),
        (await rated_shot(archive, version.id, "001006", "index_rating", 3), "index_rating", 3),
        (await rated_shot(archive, version.id, "001003", "device_notes", 2), "device_notes", 2),
    ]
    for shot_id, source, stars in shots:
        await assert_only_rated(archive.db, shot_id, source, stars)
    unrated = await make_shot(archive.db, "001007")
    assert await RecipesRepository(archive.db).assign_shot(unrated, version.id)

    counted = [
        shot
        for shot in await RecipesRepository(archive.db).counted_shots(archive.recipe_id)
        if shot.version_id == version.id
    ]
    evidence = version_evidence(
        counted, pooled_spreads(counted), version_id=version.id, version_label=version.version_label
    )
    counts = evidence.this
    assert (counts.good, counts.neutral, counts.bad, counts.unrated) == (1, 1, 1, 1)


# ── the grading guard ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("source", SOURCES)
async def test_a_rated_shot_lets_the_prediction_be_graded(archive: Fixture, source: str) -> None:
    version = await a_version(archive, prediction=True)
    shot_id = await rated_shot(archive, version.id, "001010", source, 3)
    await assert_only_rated(archive.db, shot_id, source, 3)

    recipes = RecipesRepository(archive.db)
    assert await recipes.gradable_shot_count(version.id) == 1
    result = await recipes.set_outcome(
        archive.recipe_id, version.id, VersionOutcomeWrite(outcome="held", note=NOTE)
    )
    assert result.refused is None


async def test_a_proposed_grade_counts_the_rated_shots_then_and_now(archive: Fixture) -> None:
    version = await a_version(archive, prediction=True)
    first = await rated_shot(archive, version.id, "001011", "judgement", 4)
    await assert_only_rated(archive.db, first, "judgement", 4)
    proposals = OutcomeProposalsRepository(archive.db)

    made = await proposals.create(
        archive.recipe_id,
        version.id,
        OutcomeProposalWrite.model_validate({"outcome": "held", "note": NOTE}),
    )
    assert made.refused is None and made.proposal is not None
    assert made.proposal.counted_shots == 1

    later = await rated_shot(archive, version.id, "001012", "device_notes", 1)
    await assert_only_rated(archive.db, later, "device_notes", 1)
    # A discarded shot, however it is rated, is not another one to grade on.
    thrown = await rated_shot(archive, version.id, "001016", "judgement", 5)
    assert (
        await OutcomeProposalsRepository(archive.db).waiting_for_version(version.id)
    ) is not None
    waiting_before = await proposals.waiting_for_version(version.id)
    assert waiting_before is not None and waiting_before.counted_shots_now == 3
    await JudgementsRepository(archive.db).upsert(thrown, JudgementWrite(rating=5, discarded=True))
    waiting = await proposals.waiting_for_version(version.id)
    assert waiting is not None
    assert (waiting.counted_shots, waiting.counted_shots_now) == (1, 2)


async def test_propose_outcome_accepts_a_version_whose_only_shot_is_rated(
    archive: Fixture, settings: SettingsService
) -> None:
    version = await a_version(archive, prediction=True)
    shot_id = await rated_shot(archive, version.id, "001013", "index_rating", 5)
    await assert_only_rated(archive.db, shot_id, "index_rating", 5)

    data = await call(
        in_version(archive, settings, version), "propose_outcome", outcome="held", note=NOTE
    )

    assert (data["status"], data["counted_shots"]) == ("proposed", 1)


async def test_propose_outcome_still_refuses_a_version_with_no_rating(
    archive: Fixture, settings: SettingsService
) -> None:
    version = await a_version(archive, prediction=True)
    shot_id = await make_shot(archive.db, "001014")
    assert await RecipesRepository(archive.db).assign_shot(shot_id, version.id)

    outcome = await registry.dispatch(
        in_version(archive, settings, version), "propose_outcome", {"outcome": "held", "note": NOTE}
    )

    assert not outcome.ok
    assert "has no rated shot yet" in outcome.data["detail"]


async def test_propose_outcome_says_a_discarded_shot_does_not_count_when_that_is_all_there_is(
    archive: Fixture, settings: SettingsService
) -> None:
    """A 5-star discarded shot is rated, and is still not one to grade on."""
    version = await a_version(archive, prediction=True)
    shot_id = await rated_shot(archive, version.id, "001015", "judgement", 5)
    recipes = RecipesRepository(archive.db)
    # The precondition: before the discard, this very shot is what the grade would rest on.
    assert await recipes.gradable_shot_count(version.id) == 1
    await JudgementsRepository(archive.db).upsert(shot_id, JudgementWrite(rating=5, discarded=True))
    assert await recipes.gradable_shot_count(version.id) == 0

    outcome = await registry.dispatch(
        in_version(archive, settings, version), "propose_outcome", {"outcome": "held", "note": NOTE}
    )

    assert not outcome.ok
    detail = outcome.data["detail"]
    assert f"{version.version_label} has no rated shot yet" in detail
    assert "a discarded shot does not count" in detail


# ── the rating counts ────────────────────────────────────────────────────────────


async def test_the_counts_split_by_band_and_a_discarded_shot_counts_only_as_discarded(
    archive: Fixture,
) -> None:
    version = await a_version(archive)
    shots = [
        await rated_shot(archive, version.id, "001020", "judgement", 5),
        await rated_shot(archive, version.id, "001021", "device_notes", 3),
        await rated_shot(archive, version.id, "001022", "index_rating", 2),
        await rated_shot(archive, version.id, "001023", "judgement", 4),
        await rated_shot(archive, version.id, "001025", "judgement", 1),
    ]
    for shot_id, source, stars in zip(
        shots,
        ("judgement", "device_notes", "index_rating", "judgement", "judgement"),
        (5, 3, 2, 4, 1),
        strict=True,
    ):
        await assert_only_rated(archive.db, shot_id, source, stars)
    nothing = await make_shot(archive.db, "001024")
    assert await RecipesRepository(archive.db).assign_shot(nothing, version.id)
    repo = RecipesRepository(archive.db)
    before = (await repo.label_counts(archive.recipe_id))[version.id]
    assert (before.good, before.neutral, before.bad, before.discarded, before.unrated) == (
        2,
        1,
        2,
        0,
        1,
    )

    # Discarding a Good shot moves it out of Good and into Discarded, and nowhere else.
    await JudgementsRepository(archive.db).upsert(
        shots[0], JudgementWrite(rating=5, discarded=True)
    )
    counts = (await repo.label_counts(archive.recipe_id))[version.id]

    assert (counts.good, counts.neutral, counts.bad, counts.discarded, counts.unrated) == (
        1,
        1,
        2,
        1,
        1,
    )


# ── the way back ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("source", SOURCES)
async def test_the_way_back_offers_a_version_whose_shot_is_only_rated_good(
    archive: Fixture, source: str
) -> None:
    recipes = RecipesRepository(archive.db)
    older = await a_version(archive)
    shot_id = await rated_shot(archive, older.id, "001030", source, 4)
    await assert_only_rated(archive.db, shot_id, source, 4)
    newer = await a_version(archive)
    assert (await recipes.current_version(archive.recipe_id)) is not None

    assert await recipes.rollback_target(archive.recipe_id) == older.id
    assert newer.id != older.id


@pytest.mark.parametrize("stars", [3, 2])
async def test_the_way_back_does_not_offer_a_version_whose_shot_is_neutral_or_bad(
    archive: Fixture, stars: int
) -> None:
    recipes = RecipesRepository(archive.db)
    older = await a_version(archive)
    shot_id = await rated_shot(archive, older.id, "001031", "judgement", stars)
    await assert_only_rated(archive.db, shot_id, "judgement", stars)
    await a_version(archive)

    target = await recipes.rollback_target(archive.recipe_id)

    assert target != older.id


async def test_the_way_back_does_not_offer_a_version_whose_good_shot_was_discarded(
    archive: Fixture,
) -> None:
    recipes = RecipesRepository(archive.db)
    older = await a_version(archive)
    shot_id = await rated_shot(archive, older.id, "001032", "judgement", 5)
    await a_version(archive)
    # The precondition: before the discard this version is the way back.
    assert await recipes.rollback_target(archive.recipe_id) == older.id

    await JudgementsRepository(archive.db).upsert(shot_id, JudgementWrite(rating=5, discarded=True))

    assert await recipes.rollback_target(archive.recipe_id) != older.id


# ── the shot search: the filter and the rows ─────────────────────────────────────


@pytest.mark.parametrize("source", SOURCES)
async def test_the_search_filter_finds_a_rated_shot_under_the_band_its_rating_gives(
    archive: Fixture, settings: SettingsService, source: str
) -> None:
    version = await a_version(archive)
    good = await rated_shot(archive, version.id, "001040", source, 4)
    neutral = await rated_shot(archive, version.id, "001043", source, 3)
    bad = await rated_shot(archive, version.id, "001041", source, 2)
    for shot_id, stars in ((good, 4), (neutral, 3), (bad, 2)):
        await assert_only_rated(archive.db, shot_id, source, stars)
    ctx = in_version(archive, settings, version)
    name = version.version_label

    found_good = await call(ctx, "list_recipe_shots", version=name, label="good")
    found_neutral = await call(ctx, "list_recipe_shots", version=name, label="neutral")
    found_bad = await call(ctx, "list_recipe_shots", version=name, label="bad")

    assert [shot["shot_id"] for shot in found_good["shots"]] == [good]
    assert [shot["shot_id"] for shot in found_neutral["shots"]] == [neutral]
    assert [shot["shot_id"] for shot in found_bad["shots"]] == [bad]
    machine = "" if source == "judgement" else ", from the machine's rating"
    assert f"Label: Good (4★{machine})" in found_good["shots"][0]["text"]
    assert f"Label: Neutral (3★{machine})" in found_neutral["shots"][0]["text"]
    assert f"Label: Bad (2★{machine})" in found_bad["shots"][0]["text"]


async def test_a_discarded_shot_is_found_under_discard_and_under_no_band(
    archive: Fixture, settings: SettingsService
) -> None:
    version = await a_version(archive)
    shot_id = await rated_shot(archive, version.id, "001044", "judgement", 5)
    ctx = in_version(archive, settings, version)
    name = version.version_label
    # The precondition: before the discard it is found under good.
    found = await call(ctx, "list_recipe_shots", version=name, label="good")
    assert [shot["shot_id"] for shot in found["shots"]] == [shot_id]

    await JudgementsRepository(archive.db).upsert(shot_id, JudgementWrite(rating=5, discarded=True))

    for label in ("good", "neutral", "bad"):
        found = await call(ctx, "list_recipe_shots", version=name, label=label)
        assert found["shots"] == [], label
    found = await call(ctx, "list_recipe_shots", version=name, label="discard")
    assert [shot["shot_id"] for shot in found["shots"]] == [shot_id]
    assert "Label: Discarded\n" in found["shots"][0]["text"]


async def test_the_repository_search_and_the_rows_agree_with_the_rule(archive: Fixture) -> None:
    recipes = RecipesRepository(archive.db)
    version = await a_version(archive)
    good = await rated_shot(archive, version.id, "001042", "device_notes", 5)
    await assert_only_rated(archive.db, good, "device_notes", 5)

    ids = await recipes.search_shot_ids(archive.recipe_id, version_id=version.id, label="good")
    rows = await recipes.recipe_shots(archive.recipe_id, version_id=version.id, label="good")

    assert ids == [good]
    assert [row.shot_id for row in rows] == [good]
    assert (
        await recipes.search_shot_ids(archive.recipe_id, version_id=version.id, label="bad") == []
    )


# ── the catalogue's label line ───────────────────────────────────────────────────


@pytest.mark.parametrize("source", SOURCES)
async def test_the_label_line_says_the_band_the_stars_and_where_the_rating_came_from(
    archive: Fixture, source: str
) -> None:
    from gaggiclanker.shotinfo.catalogue import ITEMS

    version = await a_version(archive)
    shot_id = await rated_shot(archive, version.id, "001050", source, 4)
    await assert_only_rated(archive.db, shot_id, source, 4)

    (facts,) = await load_shots(archive.db, [shot_id])

    label = ITEMS["label"].shot
    assert label is not None
    expected = "Good (4★)" if source == "judgement" else "Good (4★, from the machine's rating)"
    assert label(facts) == expected


async def test_the_label_line_for_a_shot_with_no_rating_says_not_rated(archive: Fixture) -> None:
    from gaggiclanker.shotinfo.catalogue import ITEMS

    version = await a_version(archive)
    shot_id = await make_shot(archive.db, "001051")
    assert await RecipesRepository(archive.db).assign_shot(shot_id, version.id)
    await archive.db.execute("UPDATE shots SET index_rating = 0 WHERE id = ?", (shot_id,))

    (facts,) = await load_shots(archive.db, [shot_id])

    label = ITEMS["label"].shot
    assert label is not None
    assert label(facts) == "not rated"


async def test_the_label_and_counted_lines_say_discarded_whatever_the_stars(
    archive: Fixture,
) -> None:
    from gaggiclanker.shotinfo.catalogue import ITEMS

    version = await a_version(archive)
    shot_id = await rated_shot(archive, version.id, "001052", "judgement", 5)
    (before,) = await load_shots(archive.db, [shot_id])
    label, counted = ITEMS["label"].shot, ITEMS["counted"].shot
    assert label is not None and counted is not None
    assert (label(before), counted(before)) == ("Good (5★)", "counted")

    await JudgementsRepository(archive.db).upsert(shot_id, JudgementWrite(rating=5, discarded=True))
    (after,) = await load_shots(archive.db, [shot_id])

    assert (label(after), counted(after)) == ("Discarded", "not counted: discarded")


# ── a discard beats the stars ────────────────────────────────────────────────────


async def test_a_discarded_five_star_shot_is_out_of_every_count(archive: Fixture) -> None:
    recipes = RecipesRepository(archive.db)
    version = await a_version(archive, prediction=True)
    shot_id = await rated_shot(archive, version.id, "001061", "judgement", 5)
    # Precondition: not discarded, this very shot is counted, gradable and Good.
    assert [
        s.band for s in await recipes.counted_shots(archive.recipe_id) if s.shot_id == shot_id
    ] == ["good"]
    assert await recipes.gradable_shot_count(version.id) == 1

    await JudgementsRepository(archive.db).upsert(shot_id, JudgementWrite(rating=5, discarded=True))

    assert shot_id not in {s.shot_id for s in await recipes.counted_shots(archive.recipe_id)}
    assert await recipes.gradable_shot_count(version.id) == 0
    counts = (await recipes.label_counts(archive.recipe_id))[version.id]
    assert (counts.good, counts.neutral, counts.bad, counts.discarded) == (0, 0, 0, 1)
    assert (await ShotsRepository(archive.db).get(shot_id)) is not None


# ── the words ────────────────────────────────────────────────────────────────────


async def test_the_search_takes_good_neutral_bad_and_discard_and_no_longer_keep_or_improve(
    archive: Fixture, settings: SettingsService
) -> None:
    ctx = _context(archive, settings, ToolScope.for_thread(archive.recipe_id))
    for word in ("good", "neutral", "bad", "discard"):
        await call(ctx, "list_recipe_shots", label=word)
    for word in ("keep", "improve"):
        outcome = await registry.dispatch(ctx, "list_recipe_shots", {"label": word})
        assert not outcome.ok


async def test_the_schema_notes_say_what_the_views_hold_and_the_band_rule() -> None:
    from gaggiclanker.tools.builtin import SCHEMA_NOTES

    assert "discarded (v_shots, v_judgements) is 1 when the person discarded the shot" in (
        SCHEMA_NOTES
    )
    assert "A rating is Good from 4 stars, Neutral at 3 and Bad at 1 or 2" in SCHEMA_NOTES
    assert "keep means Good" not in SCHEMA_NOTES
    assert "decision (v_shots" not in SCHEMA_NOTES
    # What the views hold of the machine's card: a verdict made from it, not what only lives on
    # the card or the index, or arrived after a verdict was typed. A shot rated only on the
    # machine often does have a rating here, so the notes must not say it never does.
    assert (
        "copied from the machine's notes card when the verdict was created from it" in SCHEMA_NOTES
    )
    # A copied balance is only Sour or Bitter: the card's Balanced is its default, not a choice.
    assert "a Sour or Bitter balance copied the same way" in SCHEMA_NOTES
    assert "a Balanced is never copied" in SCHEMA_NOTES
    assert "rating, balance and note copied" not in SCHEMA_NOTES
    assert "a rating that lives only on the machine's index" in SCHEMA_NOTES
    assert "arrived after a verdict was already typed here" in SCHEMA_NOTES
    assert "has no rating here at all" not in SCHEMA_NOTES


# ── v_shots serves a true 0 or 1 ─────────────────────────────────────────────────


async def test_v_shots_says_zero_for_a_shot_nobody_judged_through_the_agents_query(
    archive: Fixture, ctx: ToolContext
) -> None:
    """``discarded`` is a 0/1 fact about every shot, so ``discarded = 0`` finds the unjudged too."""
    unjudged = await make_shot(archive.db, "001070")
    thrown = await rated_shot(archive, (await a_version(archive)).id, "001071", "judgement", 5)
    # The precondition: one shot has no judgement row at all, and its view row would be NULL
    # without the COALESCE.
    assert (
        await archive.db.fetch_value(
            "SELECT COUNT(*) FROM shot_judgements WHERE shot_id = ?", (unjudged,)
        )
        == 0
    )

    before = await call(ctx, "query_shots", sql="SELECT shot_id, discarded FROM v_shots")
    assert {row[1] for row in before["rows"]} == {0}
    assert [row for row in before["rows"] if row[0] == unjudged] == [[unjudged, 0]]

    await JudgementsRepository(archive.db).upsert(thrown, JudgementWrite(rating=5, discarded=True))
    after = await call(ctx, "query_shots", sql="SELECT shot_id, discarded FROM v_shots")
    total = len(after["rows"])
    not_discarded = await call(
        ctx, "query_shots", sql="SELECT COUNT(*) FROM v_shots WHERE discarded = 0"
    )
    discarded = await call(
        ctx, "query_shots", sql="SELECT shot_id FROM v_shots WHERE discarded = 1"
    )
    assert not_discarded["rows"] == [[total - 1]]
    assert discarded["rows"] == [[thrown]]
    # v_judgements, an inner join, says the same about a judged shot.
    judged = await call(
        ctx,
        "query_shots",
        sql=f"SELECT discarded FROM v_judgements WHERE shot_id = {thrown}",  # noqa: S608
    )
    assert judged["rows"] == [[1]]


# ── the discarded and unrated parts of the counts ───────────────────────────────


async def test_a_discarded_unrated_shot_counts_as_discarded_and_not_as_unrated(
    archive: Fixture,
) -> None:
    version = await a_version(archive)
    repo = RecipesRepository(archive.db)
    shot_id = await make_shot(archive.db, "001072")
    assert await repo.assign_shot(shot_id, version.id)
    before = (await repo.label_counts(archive.recipe_id))[version.id]
    # The precondition: unrated and not discarded, it is counted as unrated.
    assert (before.unrated, before.discarded) == (1, 0)

    await JudgementsRepository(archive.db).upsert(shot_id, JudgementWrite(discarded=True))

    after = (await repo.label_counts(archive.recipe_id))[version.id]
    assert (after.unrated, after.discarded) == (0, 1)
    assert (after.good, after.neutral, after.bad) == (0, 0, 0)


# ── the words the agent is given ─────────────────────────────────────────────────


async def test_the_propose_outcome_description_says_what_counts() -> None:
    spec = registry.get("propose_outcome")
    assert spec is not None
    description = spec.description
    assert "graded against all of its counted shots (rated, not discarded)" in description
    assert "no rated shot (a discarded shot does not count, whatever its stars)" in description
    assert "Good or Bad" not in description
