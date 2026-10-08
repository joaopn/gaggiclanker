"""What a reading is told, and the golden prompt it renders to.

The golden file is `golden/review-prompt.txt`: the whole input for the fixture's
subject shot — its information as the shot renderer writes it, the signature's
free-text expectations, the prediction, the phases, the profile it brewed, the
style and the selected rules and excerpts — exactly as it came out last time. A
change to any of it is a diff in review.

The other tests here pin the rules the golden cannot state: a reading never sees
the person's judgement, the machine's note, the label, another shot or an earlier
reading (however they are stored), it does see the Set version's recipe and
prediction and the confirmed signature's free-text expectations, it reads every
item it is allowed whatever the person's tier settings, and two builds are
byte-identical.

Regenerate with `uv run pytest tests/review -k golden --update-golden` and read
the diff before committing it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

import gaggiclanker.review.context as context_module
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.reviews import (
    ClaimWrite,
    ReviewOutcome,
    ReviewStart,
    ShotReviewsRepository,
)
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch
from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.db.repos.signatures import ExpectationWrite, SignatureRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.domain.models import ShotNotes
from gaggiclanker.review.context import (
    REVIEW_EXCLUDED_KEYS,
    build_review_input,
    review_keys,
)
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.shotinfo import CATALOGUE, ITEMS, ShotFacts, ShotTier, Tier, render_shot
from tests.review.conftest import FREE_TEXT, Fixture, confirm_free_text, predict

GOLDEN = Path(__file__).resolve().parent / "golden" / "review-prompt.txt"

#: The prediction the blind test files the subject shot under. Distinctive, so
#: finding any part of it in the input is unambiguous.
PREDICTION = "Expect a sweeter cup with a longer finish than the first recipe gave us."


def _rendered(variables: dict[str, str]) -> str:
    return "\n\n".join(f"=== {name} ===\n{value}" for name, value in sorted(variables.items()))


async def test_the_rendered_input_matches_the_golden_file(
    fixture: Fixture, update_golden: bool
) -> None:
    review = await build_review_input(fixture.db, fixture.shots[-1])
    rendered = _rendered(review.render())

    if update_golden:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(rendered + "\n", encoding="utf-8")
        return

    assert GOLDEN.exists(), "run with --update-golden to create it"
    assert rendered + "\n" == GOLDEN.read_text(encoding="utf-8")


async def test_two_builds_are_identical(fixture: Fixture) -> None:
    """Determinism, asserted directly rather than inferred from the golden."""
    first = await build_review_input(fixture.db, fixture.shots[-1])
    second = await build_review_input(fixture.db, fixture.shots[-1])
    assert first.model_dump_json() == second.model_dump_json()
    assert first.render() == second.render()


async def test_a_reading_never_sees_the_judgement_the_note_the_label_or_other_shots(
    fixture: Fixture,
) -> None:
    """The rule the whole feature rests on.

    The subject shot carries a full judgement (rating, balance, taste and aroma
    notes, notes text, a decision), a note typed on the machine, an earlier reading and
    is filed under a version with a prediction in a named Set that has five other shots
    and a confirmed insight. None of the person's side may reach the stored input or the
    rendered prompt; the version's recipe and prediction do.
    """
    db = fixture.db
    subject = fixture.shots[-1]
    sets = SetsRepository(db)
    predicted = await sets.add_version(
        fixture.set_id,
        SetVersionPatch(prediction=PREDICTION, compares_to_version_id=fixture.version_id),
    )
    assert predicted is not None
    assert await sets.assign_shot(subject, predicted.id)
    await NotesRepository(db).upsert(
        subject,
        ShotNotes.model_validate(
            {
                "id": "106",
                "rating": 2,
                "beanType": "Machine-typed bean name",
                "balanceTaste": "bitter",
                "notes": "Typed at the machine: harsh finish",
            }
        ),
    )
    # And an earlier reading of the same shot: a reading is never shown another.
    earlier = ShotReviewsRepository(db)
    earlier_id = await earlier.start(ReviewStart(shot_id=subject, model="earlier-model"))
    await earlier.finish(
        earlier_id,
        ReviewOutcome(
            status="ok",
            summary="An earlier one-line reading.",
            claims=[ClaimWrite(kind="claim", text="An earlier claim about this shot.")],
        ),
    )
    # Every row the leak test looks for is really there to leak.
    stored = await db.fetch_one(
        "SELECT rating, balance, notes, decision FROM shot_judgements WHERE shot_id = ?",
        (subject,),
    )
    assert stored is not None and stored["decision"] == "improve"

    review = await build_review_input(db, subject)
    stored_input = review.model_dump_json()
    prompt = _rendered(review.render())

    # Distinctive text, looked for everywhere: the stored input and the whole
    # prompt, rules and excerpts included.
    distinctive = [
        "Sharp up front, nothing behind it",  # the judgement's notes
        "sour_fermented.sour.citric_acid",  # its taste notes, as stored
        "fruity.citrus_fruit.lemon",
        "floral.floral.jasmine",  # its aroma note
        "Machine-typed bean name",  # the note typed on the machine
        "Typed at the machine: harsh finish",
        "Guji natural on the Niche",  # the Set
        "Baseline for this bag",  # the first version's intent
        "Naturals on this grinder",  # the confirmed insight
        "drifts coarser as it warms up",  # the other two
        "An unconfirmed proposal",
        "An earlier claim about this shot.",  # an earlier reading of it
        "An earlier one-line reading.",
        "earlier-model",
        "peach, jasmine, lemon",  # the Set's bean
        "Niche Zero",  # and its grinder
    ]
    for text in (stored_input, prompt):
        leaked = [needle for needle in distinctive if needle in text]
        assert not leaked, leaked
        for other in fixture.shots[:-1]:
            assert f"shot {other}" not in text
            assert f'"shot_id":{other},' not in text

    # The lines that would carry the judgement and the label, looked for in the shot's own
    # information (prose in an excerpt may say "rating").
    lines = [
        "Rating:",
        "Balance:",
        "Taste notes:",
        "Aroma notes:",
        "Notes:",
        "Dose in:",
        "Dose out:",
        "Ratio:",
        "Label:",
        "Counted:",
        "Machine note",
        "Review:",
        "Improve",
        "3/5",
        "Citric acid",
    ]
    leaked = [needle for needle in lines if needle in review.shot]
    assert not leaked, leaked

    # What it does see: the shot, the version it is filed under with its recipe, the
    # prediction and what it is measured against.
    assert prompt.startswith("=== knowledge_excerpts ===")
    assert f"shot {subject}" in prompt
    assert "Shot time: 24.0 s" in prompt
    assert review.version == "v1.1"
    assert review.prediction == PREDICTION
    assert review.compares_to == "v1"
    assert f"v1.1 of Set {fixture.set_id}" in review.shot
    assert "Recipe " in review.shot or "recipe" in review.shot.lower()
    rendered = review.render()["prediction"]
    assert PREDICTION in rendered and "measured against v1" in rendered


async def test_with_no_prediction_or_no_set_version_it_says_so(fixture: Fixture) -> None:
    review = await build_review_input(fixture.db, fixture.shots[-1])
    assert review.prediction == "" and review.version == "v1"
    assert "was filed with no prediction" in review.render()["prediction"]
    assert "`prediction` key" in review.render()["prediction"]

    shot_id = await ShotsRepository(fixture.db).insert(
        ShotInsert(
            device_id="000778",
            raw_slog=b"fixture",
            started_at="2026-03-04T08:00:00.000Z",
            duration_ms=31_000,
        )
    )
    unfiled = await build_review_input(fixture.db, shot_id)
    assert unfiled.version == "" and unfiled.prediction == ""
    assert "not filed under a Set version" in unfiled.render()["prediction"]


async def test_a_prediction_measured_against_nothing_says_so(fixture: Fixture) -> None:
    await predict(fixture, compares_to=None)
    review = await build_review_input(fixture.db, fixture.shots[-1])
    assert review.compares_to == ""
    assert "measured against nothing earlier" in review.render()["prediction"]


async def test_the_confirmed_signatures_free_text_expectations_are_listed_with_their_ids(
    fixture: Fixture,
) -> None:
    review = await build_review_input(fixture.db, fixture.shots[-1])
    assert review.expectations == [] and review.signature_confirmed == 0
    assert "no signature in force" in review.render()["signature"]

    first = await confirm_free_text(fixture, fault="unstable")
    second = await confirm_free_text(
        fixture, tier="important", phase=None, fault="slow flow", text="It took its time"
    )
    # A rejected one is not the signature in force, and neither is a measure.
    repo = SignatureRepository(fixture.db)
    (gone,) = await repo.add(
        fixture.profile_version_id,
        [
            ExpectationWrite(
                status="confirmed",
                tier="context",
                kind="free_text",
                text="Not confirmed",
                fault="unstable",
                sentence="Not confirmed",
            )
        ],
    )
    await repo.reject(gone.id, reason="no")

    review = await build_review_input(fixture.db, fixture.shots[-1])

    assert [e.id for e in review.expectations] == [first, second]
    assert review.signature_confirmed == 2
    text = review.render()["signature"]
    assert f"id {first} · critical · Pressurise · fails as unstable: {FREE_TEXT}" in text
    assert f"id {second} · important · whole shot · fails as slow flow: It took its time" in text
    assert "Not confirmed" not in text


async def test_the_person_s_tiers_do_not_narrow_what_a_review_reads(fixture: Fixture) -> None:
    """Tiers govern the chat's loaded context, never a review's input."""
    before = await build_review_input(fixture.db, fixture.shots[-1])
    tiers = ShotInfoTiersRepository(fixture.db)
    for key in ("shot_time", "yield", "curve_pressure", "phase_name"):
        await tiers.set_tier(ShotInfoTierWrite(item_key=key, tier="excluded"))

    after = await build_review_input(fixture.db, fixture.shots[-1])

    assert after.shot == before.shot
    assert "Shot time: 24.0 s" in after.shot
    assert "Yield: 37.5 g" in after.shot


def test_a_review_reads_every_item_but_the_judgement_the_note_the_label_and_itself() -> None:
    keys = review_keys()
    groups_left_out = {ITEMS[key].group for key in ("rating", "note_text", "review_state")}

    assert REVIEW_EXCLUDED_KEYS == {"label", "counted"}
    for item in CATALOGUE:
        excluded = item.group in groups_left_out or item.key in REVIEW_EXCLUDED_KEYS
        assert (item.key not in keys) == excluded, item.key
    assert not [key for key in keys if key.startswith("reading_")]
    # The Set version's recipe and where the shot is filed are read: the checks' shares of the
    # target need them.
    assert {"recipe_grind", "recipe_yield", "set_version", "checks"} <= keys
    # Excluded-by-default items are read too: the curve's extra channels.
    assert {"curve_pump_flow", "curve_target_temperature", "machine_shot_number"} <= keys


async def test_the_curve_is_cut_to_the_review_s_own_budget_whatever_the_chat_says(
    fixture: Fixture,
) -> None:
    # The chat's curve budget is the person's to tune; a review's input must not
    # move with it, or the same shot would be read differently from one week to
    # the next.
    settings = SettingsService(SettingsRepository(fixture.db))
    await settings.store("chatCurvePoints", 10)
    few = await build_review_input(fixture.db, fixture.shots[-1])
    await settings.store("chatCurvePoints", 200)
    many = await build_review_input(fixture.db, fixture.shots[-1])
    assert few.shot == many.shot

    lines = few.shot.splitlines()
    counted = next(
        line for line in lines if line.endswith(" of 112 samples") or " of 112 samples," in line
    )
    assert counted.startswith("44 of 112 samples, shape-preserving")
    header = lines[lines.index(counted) + 1]
    # Every channel recorded, the ones the chat's defaults leave out included.
    assert header.startswith("t (s),pressure (bar),target pressure (bar),puck flow (ml/s)")
    assert "target temperature (°C)" in header


async def test_the_profile_is_the_one_the_shot_brewed(fixture: Fixture) -> None:
    review = await build_review_input(fixture.db, fixture.shots[-1])
    assert review.profile is not None
    assert review.profile_label
    rendered = review.render()["profile"]
    assert rendered.splitlines()[0] == review.profile_label
    assert json.loads(rendered.splitlines()[1]) == review.profile


async def test_a_shot_with_no_profile_and_no_set_still_builds(fixture: Fixture) -> None:
    shot_id = await ShotsRepository(fixture.db).insert(
        ShotInsert(
            device_id="000777",
            raw_slog=b"fixture",
            started_at="2026-03-04T08:00:00.000Z",
            duration_ms=31_000,
            profile_name_on_device="Mystery",
        )
    )
    review = await build_review_input(fixture.db, shot_id)
    assert review.profile is None
    assert "holds no copy of the profile" in review.render()["profile"]
    assert review.shot.startswith(f"shot {shot_id}")


async def test_the_signals_come_from_the_telemetry_only(fixture: Fixture) -> None:
    """No taste, balance or aroma token: a review has no judgement to make one from."""
    review = await build_review_input(fixture.db, fixture.shots[-1])
    assert not [token for token in review.signals if token.split(":")[1].isupper()], "no band"
    assert "style:" + review.style in review.signals
    assert not [
        token for token in review.signals if token.startswith(("taste:", "aroma:", "balance:"))
    ]


async def test_no_rule_is_chosen_for_the_set_s_bean(fixture: Fixture) -> None:
    """Rule selection is given no Set attributes, so a bean-keyed rule never fires.

    The fixture's bean is a light natural; `natural.light` is the rule the
    pressure matrix keys on exactly that, and a review must not be handed it.
    """
    review = await build_review_input(fixture.db, fixture.shots[-1])
    assert "natural.light" not in review.rule_keys
    assert "hierarchy" in review.rule_keys


async def test_the_input_carries_reference_excerpts_with_citable_paths(fixture: Fixture) -> None:
    review = await build_review_input(fixture.db, fixture.shots[-1])
    assert review.excerpts
    for excerpt in review.excerpts:
        assert excerpt["heading_path"] in review.render()["knowledge_excerpts"]
    assert review.excerpt_paths == {str(e["heading_path"]) for e in review.excerpts}


async def test_the_excerpt_budget_is_a_parameter_and_zero_turns_it_off(fixture: Fixture) -> None:
    review = await build_review_input(fixture.db, fixture.shots[-1], chunk_token_budget=0)
    assert review.excerpts == []


async def test_the_loader_s_judgement_is_dropped_whatever_the_exclusions_say(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent twice over: the judgement, the note and an earlier reading are gone first.

    The exclusion list keeps their lines out; the facts a reading renders carry no judgement,
    note or earlier reading, and the shot row none of its copies of the person's verdict, to
    begin with. With the list widened to every item, nothing of them may appear either, and
    the facts handed to the renderer say why. The Set version stays: the checks need it.
    """
    subject = fixture.shots[-1]
    earlier = ShotReviewsRepository(fixture.db)
    earlier_id = await earlier.start(ReviewStart(shot_id=subject, model="earlier-model"))
    await earlier.finish(
        earlier_id,
        ReviewOutcome(
            status="ok",
            summary="An earlier one-line reading.",
            claims=[ClaimWrite(kind="claim", text="An earlier claim about this shot.")],
        ),
    )
    everything = frozenset(item.key for item in CATALOGUE)
    monkeypatch.setattr("gaggiclanker.review.context.review_keys", lambda: everything)
    rendered: list[ShotFacts] = []

    def spy(
        facts: ShotFacts, tier: ShotTier, tiers: Mapping[str, Tier], *, curve_points: int
    ) -> str:
        rendered.append(facts)
        return render_shot(facts, tier, tiers, curve_points=curve_points)

    monkeypatch.setattr(context_module, "render_shot", spy)

    review = await build_review_input(fixture.db, subject)

    assert "Sharp up front, nothing behind it" not in review.shot
    assert "Rating:" not in review.shot
    assert "Balance:" not in review.shot
    assert "An earlier claim about this shot." not in review.shot
    assert "An earlier one-line reading." not in review.shot
    assert "earlier-model" not in review.shot
    assert "Review: not reviewed" in review.shot, (
        "the widened list rendered the reading group, empty"
    )
    assert "not labelled" in review.shot, "the widened list really did render the label"

    [facts] = rendered
    assert (facts.judgement, facts.note, facts.reading) == (None, None, None)
    assert facts.version is not None, "the version's recipe is what the checks measure against"
    assert facts.shot.judgement_rating is None
    assert facts.shot.judgement_notes is None
    assert facts.shot.judgement_decision is None
