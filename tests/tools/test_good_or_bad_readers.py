"""Every reader that asks "Good or Bad?" reads the shared rule, not the stored decision.

A shot nobody labelled but somebody rated is Good from three stars and Bad from one or
two, whichever of the three ratings the Rating column shows it came from: this box's
verdict, the machine's notes card, or the index. Each test is written in two halves, as
the suite's rule asks of a test that something is counted: the precondition first (the
shot has no stored decision and holds the rating in the named place), then the behaviour.
Put a reader back on the stored decision and its test fails at the second half.
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
    """A shot filed under the version with a rating in one place and a decision nowhere."""
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


async def assert_unlabelled_with_rating(
    db: Database, shot_id: int, source: str, stars: int
) -> None:
    """The precondition: no stored decision, and the rating is exactly where it was put."""
    row = await db.fetch_one(
        """
        SELECT j.decision AS decision, j.rating AS judgement, n.rating AS device_notes,
               s.index_rating AS index_rating
          FROM shots s
          LEFT JOIN shot_judgements j ON j.shot_id = s.id
          LEFT JOIN device_shot_notes n ON n.shot_id = s.id
         WHERE s.id = ?
        """,
        (shot_id,),
    )
    assert row is not None
    assert row["decision"] is None
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
async def test_a_rated_shot_with_no_label_is_the_gold_standard(
    archive: Fixture, source: str
) -> None:
    version = await a_version(archive)
    # The gold standard names the versions that hold a Good shot: this one holds none yet.
    assert version.version_label not in await _gold(archive)
    shot_id = await rated_shot(archive, version.id, "001001", source, 4)
    await assert_unlabelled_with_rating(archive.db, shot_id, source, 4)

    counted = await RecipesRepository(archive.db).counted_shots(archive.recipe_id)
    assert [s.decision for s in counted if s.shot_id == shot_id] == ["keep"]
    assert version.version_label in await _gold(archive)


async def test_a_rated_shot_with_no_label_is_counted_in_the_spreads_counts(
    archive: Fixture,
) -> None:
    version = await a_version(archive)
    good = await rated_shot(archive, version.id, "001002", "judgement", 5)
    bad = await rated_shot(archive, version.id, "001003", "device_notes", 2)
    await assert_unlabelled_with_rating(archive.db, good, "judgement", 5)
    await assert_unlabelled_with_rating(archive.db, bad, "device_notes", 2)

    counted = [
        shot
        for shot in await RecipesRepository(archive.db).counted_shots(archive.recipe_id)
        if shot.version_id == version.id
    ]
    evidence = version_evidence(
        counted, pooled_spreads(counted), version_id=version.id, version_label=version.version_label
    )
    counts = evidence.this
    assert (counts.keep, counts.improve, counts.unlabelled) == (1, 1, 0)


# ── the grading guard ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("source", SOURCES)
async def test_a_rated_shot_lets_the_prediction_be_graded(archive: Fixture, source: str) -> None:
    version = await a_version(archive, prediction=True)
    shot_id = await rated_shot(archive, version.id, "001010", source, 3)
    await assert_unlabelled_with_rating(archive.db, shot_id, source, 3)

    recipes = RecipesRepository(archive.db)
    assert await recipes.gradable_shot_count(version.id) == 1
    result = await recipes.set_outcome(
        archive.recipe_id, version.id, VersionOutcomeWrite(outcome="held", note=NOTE)
    )
    assert result.refused is None


async def test_a_proposed_grade_counts_the_rated_shots_then_and_now(archive: Fixture) -> None:
    version = await a_version(archive, prediction=True)
    first = await rated_shot(archive, version.id, "001011", "judgement", 4)
    await assert_unlabelled_with_rating(archive.db, first, "judgement", 4)
    proposals = OutcomeProposalsRepository(archive.db)

    made = await proposals.create(
        archive.recipe_id,
        version.id,
        OutcomeProposalWrite.model_validate({"outcome": "held", "note": NOTE}),
    )
    assert made.refused is None and made.proposal is not None
    assert made.proposal.counted_shots == 1

    later = await rated_shot(archive, version.id, "001012", "device_notes", 1)
    await assert_unlabelled_with_rating(archive.db, later, "device_notes", 1)
    waiting = await proposals.waiting_for_version(version.id)
    assert waiting is not None
    assert (waiting.counted_shots, waiting.counted_shots_now) == (1, 2)


async def test_propose_outcome_accepts_a_version_whose_only_shot_is_rated(
    archive: Fixture, settings: SettingsService
) -> None:
    version = await a_version(archive, prediction=True)
    shot_id = await rated_shot(archive, version.id, "001013", "index_rating", 5)
    await assert_unlabelled_with_rating(archive.db, shot_id, "index_rating", 5)

    data = await call(
        in_version(archive, settings, version), "propose_outcome", outcome="held", note=NOTE
    )

    assert (data["status"], data["counted_shots"]) == ("proposed", 1)


async def test_propose_outcome_still_refuses_a_version_with_no_label_and_no_rating(
    archive: Fixture, settings: SettingsService
) -> None:
    version = await a_version(archive, prediction=True)
    shot_id = await make_shot(archive.db, "001014")
    assert await RecipesRepository(archive.db).assign_shot(shot_id, version.id)

    outcome = await registry.dispatch(
        in_version(archive, settings, version), "propose_outcome", {"outcome": "held", "note": NOTE}
    )

    assert not outcome.ok
    assert "has no Good or Bad shot yet" in outcome.data["detail"]


async def test_propose_outcome_says_a_discarded_shot_does_not_count_when_that_is_all_there_is(
    archive: Fixture, settings: SettingsService
) -> None:
    """A 5-star shot labelled Discard is rated and labelled, and is still not Good or Bad."""
    version = await a_version(archive, prediction=True)
    shot_id = await rated_shot(archive, version.id, "001015", "judgement", 5)
    recipes = RecipesRepository(archive.db)
    # The precondition: before the label, this very shot is what the grade would rest on.
    assert await recipes.gradable_shot_count(version.id) == 1
    await JudgementsRepository(archive.db).upsert(
        shot_id, JudgementWrite(rating=5, decision="discard")
    )
    assert await recipes.gradable_shot_count(version.id) == 0

    outcome = await registry.dispatch(
        in_version(archive, settings, version), "propose_outcome", {"outcome": "held", "note": NOTE}
    )

    assert not outcome.ok
    detail = outcome.data["detail"]
    assert f"{version.version_label} has no Good or Bad shot yet" in detail
    assert "a discarded shot does not count" in detail
    assert "rating a shot is enough" in detail
    assert "rated or labelled" not in detail


# ── the label counts ─────────────────────────────────────────────────────────────


async def test_the_label_counts_read_the_rating_when_nothing_was_chosen(archive: Fixture) -> None:
    version = await a_version(archive)
    shots = [
        await rated_shot(archive, version.id, "001020", "judgement", 5),
        await rated_shot(archive, version.id, "001021", "device_notes", 3),
        await rated_shot(archive, version.id, "001022", "index_rating", 2),
        await rated_shot(archive, version.id, "001023", "judgement", 1),
    ]
    for shot_id, source, stars in zip(
        shots, ("judgement", "device_notes", "index_rating", "judgement"), (5, 3, 2, 1), strict=True
    ):
        await assert_unlabelled_with_rating(archive.db, shot_id, source, stars)
    nothing = await make_shot(archive.db, "001024")
    assert await RecipesRepository(archive.db).assign_shot(nothing, version.id)

    counts = (await RecipesRepository(archive.db).label_counts(archive.recipe_id))[version.id]

    assert (counts.keep, counts.improve, counts.discard, counts.unlabelled) == (2, 2, 0, 1)


# ── the way back ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("source", SOURCES)
async def test_the_way_back_offers_a_version_whose_shot_is_only_rated_good(
    archive: Fixture, source: str
) -> None:
    recipes = RecipesRepository(archive.db)
    older = await a_version(archive)
    shot_id = await rated_shot(archive, older.id, "001030", source, 4)
    await assert_unlabelled_with_rating(archive.db, shot_id, source, 4)
    newer = await a_version(archive)
    assert (await recipes.current_version(archive.recipe_id)) is not None

    assert await recipes.rollback_target(archive.recipe_id) == older.id
    assert newer.id != older.id


async def test_the_way_back_does_not_offer_a_version_whose_shot_is_only_rated_bad(
    archive: Fixture,
) -> None:
    recipes = RecipesRepository(archive.db)
    older = await a_version(archive)
    shot_id = await rated_shot(archive, older.id, "001031", "judgement", 2)
    await assert_unlabelled_with_rating(archive.db, shot_id, "judgement", 2)
    await a_version(archive)

    target = await recipes.rollback_target(archive.recipe_id)

    assert target != older.id


# ── the shot search: the filter and the rows ─────────────────────────────────────


@pytest.mark.parametrize("source", SOURCES)
async def test_the_search_filter_finds_a_rated_shot_under_the_label_its_rating_gives(
    archive: Fixture, settings: SettingsService, source: str
) -> None:
    version = await a_version(archive)
    good = await rated_shot(archive, version.id, "001040", source, 4)
    bad = await rated_shot(archive, version.id, "001041", source, 2)
    await assert_unlabelled_with_rating(archive.db, good, source, 4)
    await assert_unlabelled_with_rating(archive.db, bad, source, 2)
    ctx = in_version(archive, settings, version)

    found_good = await call(ctx, "list_recipe_shots", version=version.version_label, label="good")
    found_bad = await call(ctx, "list_recipe_shots", version=version.version_label, label="bad")

    assert [shot["shot_id"] for shot in found_good["shots"]] == [good]
    assert [shot["shot_id"] for shot in found_bad["shots"]] == [bad]
    assert "Label: Good (from the 4★ rating)" in found_good["shots"][0]["text"]
    assert "Label: Bad (from the 2★ rating)" in found_bad["shots"][0]["text"]


async def test_the_repository_search_and_the_rows_agree_with_the_rule(archive: Fixture) -> None:
    recipes = RecipesRepository(archive.db)
    version = await a_version(archive)
    good = await rated_shot(archive, version.id, "001042", "device_notes", 5)
    await assert_unlabelled_with_rating(archive.db, good, "device_notes", 5)

    ids = await recipes.search_shot_ids(archive.recipe_id, version_id=version.id, decision="keep")
    rows = await recipes.recipe_shots(archive.recipe_id, version_id=version.id, decision="keep")

    assert ids == [good]
    assert [(row.shot_id, row.decision) for row in rows] == [(good, "keep")]
    assert (
        await recipes.search_shot_ids(archive.recipe_id, version_id=version.id, decision="improve")
        == []
    )


# ── the catalogue's label line ───────────────────────────────────────────────────


@pytest.mark.parametrize("source", SOURCES)
async def test_the_label_line_says_good_from_the_rating_and_where_it_came_from(
    archive: Fixture, source: str
) -> None:
    from gaggiclanker.shotinfo.catalogue import ITEMS

    version = await a_version(archive)
    shot_id = await rated_shot(archive, version.id, "001050", source, 4)
    await assert_unlabelled_with_rating(archive.db, shot_id, source, 4)

    (facts,) = await load_shots(archive.db, [shot_id])

    label = ITEMS["label"].shot
    assert label is not None
    assert label(facts) == "Good (from the 4★ rating)"


async def test_the_label_line_for_a_shot_with_neither_says_not_labelled(archive: Fixture) -> None:
    from gaggiclanker.shotinfo.catalogue import ITEMS

    version = await a_version(archive)
    shot_id = await make_shot(archive.db, "001051")
    assert await RecipesRepository(archive.db).assign_shot(shot_id, version.id)
    await archive.db.execute("UPDATE shots SET index_rating = 0 WHERE id = ?", (shot_id,))

    (facts,) = await load_shots(archive.db, [shot_id])

    label = ITEMS["label"].shot
    assert label is not None
    assert label(facts) == "not labelled"


# ── a stored decision beats the rating ───────────────────────────────────────────


async def test_a_stored_bad_with_five_stars_is_bad_everywhere(
    archive: Fixture, settings: SettingsService
) -> None:
    recipes = RecipesRepository(archive.db)
    version = await a_version(archive)
    shot_id = await rated_shot(archive, version.id, "001060", "judgement", 5)
    await JudgementsRepository(archive.db).upsert(
        shot_id, JudgementWrite(rating=5, decision="improve")
    )

    counts = (await recipes.label_counts(archive.recipe_id))[version.id]
    assert (counts.keep, counts.improve) == (0, 1)
    counted = [s for s in await recipes.counted_shots(archive.recipe_id) if s.shot_id == shot_id]
    assert [shot.decision for shot in counted] == ["improve"]
    assert (
        await recipes.search_shot_ids(archive.recipe_id, version_id=version.id, decision="keep")
        == []
    )
    found = await call(
        in_version(archive, settings, version),
        "list_recipe_shots",
        version=version.version_label,
        label="bad",
    )
    assert [shot["shot_id"] for shot in found["shots"]] == [shot_id]
    assert "Label: Bad\n" in found["shots"][0]["text"]
    assert await recipes.rollback_target(archive.recipe_id) != version.id


async def test_a_stored_discard_with_five_stars_stays_out_of_the_counted_shots(
    archive: Fixture,
) -> None:
    recipes = RecipesRepository(archive.db)
    version = await a_version(archive, prediction=True)
    shot_id = await rated_shot(archive, version.id, "001061", "judgement", 5)
    # Precondition: with no decision this very shot is counted and gradable.
    assert [
        s.decision for s in await recipes.counted_shots(archive.recipe_id) if s.shot_id == shot_id
    ] == ["keep"]
    assert await recipes.gradable_shot_count(version.id) == 1

    await JudgementsRepository(archive.db).upsert(
        shot_id, JudgementWrite(rating=5, decision="discard")
    )

    assert shot_id not in {s.shot_id for s in await recipes.counted_shots(archive.recipe_id)}
    assert await recipes.gradable_shot_count(version.id) == 0
    counts = (await recipes.label_counts(archive.recipe_id))[version.id]
    assert (counts.keep, counts.improve, counts.discard) == (0, 0, 1)
    assert (await ShotsRepository(archive.db).get(shot_id)) is not None


# ── the words ────────────────────────────────────────────────────────────────────


async def test_the_search_takes_good_bad_and_discard_and_no_longer_keep_or_improve(
    archive: Fixture, settings: SettingsService
) -> None:
    ctx = _context(archive, settings, ToolScope.for_thread(archive.recipe_id))
    for word in ("good", "bad", "discard"):
        await call(ctx, "list_recipe_shots", label=word)
    for word in ("keep", "improve"):
        outcome = await registry.dispatch(ctx, "list_recipe_shots", {"label": word})
        assert not outcome.ok


async def test_the_schema_notes_say_what_the_stored_words_mean_and_what_the_views_hold() -> None:
    from gaggiclanker.tools.builtin import SCHEMA_NOTES

    assert "keep means Good, improve means Bad, discard means Discard" in SCHEMA_NOTES
    assert "holds only what the person chose" in SCHEMA_NOTES
    assert "3 stars or more is Good" in SCHEMA_NOTES
    # What the views hold of the machine's card: a verdict made from it, not what only lives on
    # the card or the index, or arrived after a verdict was typed. A shot rated only on the
    # machine often does have a rating here, so the notes must not say it never does.
    assert (
        "copied from the machine's notes card when the verdict was created from it" in SCHEMA_NOTES
    )
    assert "a rating that lives only on the machine's index" in SCHEMA_NOTES
    assert "arrived after a verdict was already typed here" in SCHEMA_NOTES
    assert "has no rating here at all" not in SCHEMA_NOTES
