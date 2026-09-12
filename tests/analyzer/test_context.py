"""Context assembly, and the golden prompt it renders to.

The golden file is `golden/analysis-prompt.txt`, and it is the real check on
this module: everything else here asserts one fact at a time, while the golden
asserts that the whole document — the shot, the Set, five earlier shots with
their verdicts and the advice that followed them, the judgement, and forty-odd
selected rules — comes out exactly as it did last time. A change to any of it is
a diff in review.

Regenerate with `uv run pytest tests/analyzer -k golden --update-golden` and read
the diff before committing it.
"""

from __future__ import annotations

from pathlib import Path

from gaggiclanker.analyzer.context import build_context
from tests.analyzer.conftest import Fixture

GOLDEN = Path(__file__).resolve().parent / "golden" / "analysis-prompt.txt"


async def test_the_rendered_prompt_matches_the_golden_file(
    fixture: Fixture, update_golden: bool
) -> None:
    context = await build_context(fixture.db, fixture.shots[-1])
    rendered = "\n\n".join(
        f"=== {name} ===\n{value}" for name, value in sorted(context.render().items())
    )

    if update_golden:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(rendered + "\n", encoding="utf-8")
        return

    assert GOLDEN.exists(), "run with --update-golden to create it"
    assert rendered + "\n" == GOLDEN.read_text(encoding="utf-8")


async def test_two_builds_are_identical(fixture: Fixture) -> None:
    """Determinism, asserted directly rather than inferred from the golden.

    Nothing here may read the clock or iterate a set. The golden would catch a
    regression too, but only once — this catches one that is intermittent.
    """
    first = await build_context(fixture.db, fixture.shots[-1])
    second = await build_context(fixture.db, fixture.shots[-1])
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.render() == second.render()


async def test_the_trajectory_carries_five_shots_with_their_verdicts(fixture: Fixture) -> None:
    """The chunk's third acceptance criterion."""
    context = await build_context(fixture.db, fixture.shots[-1])
    assert [entry.device_id for entry in context.trajectory] == [
        "000101",
        "000102",
        "000103",
        "000104",
        "000105",
    ]
    assert [entry.rating for entry in context.trajectory] == [2, 3, 3, 4, 3]
    assert [entry.balance for entry in context.trajectory] == [
        "sour",
        "sour",
        "sour",
        "balanced",
        "sour",
    ]

    rendered = context.render()["trajectory"]
    assert "grind finer 2 grinder_steps -> accepted" in rendered
    assert "temperature increase 1 c -> rejected" in rendered
    assert "yield increase 5 g -> open" in rendered


async def test_the_trajectory_never_sees_the_future(fixture: Fixture) -> None:
    """Re-analysing an old shot must not be told what happened afterwards.

    Otherwise its advice is unaccountable: it would look prescient, and the
    reason would be that it had read the answer.
    """
    context = await build_context(fixture.db, fixture.shots[1])
    assert [entry.device_id for entry in context.trajectory] == ["000101"]


async def test_the_set_context_names_the_grinder_s_own_unit(fixture: Fixture) -> None:
    """The field that stops the model inventing a scale (migration 0005)."""
    rendered = (await build_context(fixture.db, fixture.shots[-1])).render()["set_context"]
    assert "numbers" in rendered
    assert "never in microns" in rendered
    assert "22 numbers" in rendered
    assert "1:2.00" in rendered


async def test_days_off_roast_comes_from_the_shot_not_from_now(fixture: Fixture) -> None:
    """Reading the clock would make this test fail once a day, for ever."""
    context = await build_context(fixture.db, fixture.shots[-1])
    assert context.set is not None
    # Roasted 2026-02-27, pulled 2026-03-03.
    assert context.set.days_off_roast == 4


async def test_a_shot_with_no_set_says_so(fixture: Fixture) -> None:
    """An absent section reads as forgotten; a stated absence does not."""
    from gaggiclanker.db.repos.sets import SetsRepository

    await SetsRepository(fixture.db).assign_shot(fixture.shots[-1], None)
    context = await build_context(fixture.db, fixture.shots[-1])

    assert context.set is None
    assert "not attached to a Set" in context.no_set_reason
    assert context.render()["set_context"].startswith("NO SET.")
    assert context.trajectory == []


async def test_the_curve_is_downsampled_to_forty_points(fixture: Fixture) -> None:
    context = await build_context(fixture.db, fixture.shots[-1])
    assert len(context.shot.curve) == 40
    # First and last are always kept, so the model sees where the shot started
    # and where it ended rather than an interior window.
    assert context.shot.curve[0]["t"] == 0.0
    assert context.shot.curve[-1]["t"] == 27.75


async def test_the_signals_drive_rule_selection(fixture: Fixture) -> None:
    context = await build_context(fixture.db, fixture.shots[-1])
    assert "channeling_risk:LOW" in context.signals
    assert "taste:sour" in context.signals
    assert "balance:sour" in context.signals
    assert "style:bloom" in context.signals
    # And the rules those signals select are in the document.
    assert "sour" in context.rule_keys
    assert "channeling_risk:LOW" in context.rule_keys


async def test_sour_and_bitter_together_gets_its_own_token(fixture: Fixture) -> None:
    """One token for the channeling case, so the rule cannot fire on a sour cup."""
    from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite

    plain = await build_context(fixture.db, fixture.shots[-1])
    assert "taste:sour" in plain.signals
    assert "taste:sour_and_bitter" not in plain.signals

    await JudgementsRepository(fixture.db).upsert(
        fixture.shots[-1], JudgementWrite(taste_tags=["sour", "harsh"])
    )
    both = await build_context(fixture.db, fixture.shots[-1])

    assert "taste:sour_and_bitter" in both.signals
    assert "sour_and_bitter_is_channeling" in both.rule_keys


async def test_the_judgement_is_marked_as_ground_truth(fixture: Fixture) -> None:
    rendered = (await build_context(fixture.db, fixture.shots[-1])).render()["judgement"]
    assert "rating: 3/5" in rendered
    assert "balance: sour" in rendered
    assert "Sharp up front" in rendered


async def test_a_rerun_is_told_what_it_said_before(fixture: Fixture) -> None:
    from gaggiclanker.db.repos.analyses import AnalysesRepository, AnalysisStart

    analyses = AnalysesRepository(fixture.db)
    analysis_id = await analyses.start(
        AnalysisStart(shot_id=fixture.shots[-1], set_version_id=fixture.version_id)
    )
    await analyses.finish(analysis_id, status="ok", output={"diagnosis": "It ran fast."})

    plain = await build_context(fixture.db, fixture.shots[-1])
    assert plain.render()["previous_analysis"].startswith("This is the first analysis")

    rerun = await build_context(fixture.db, fixture.shots[-1], rerun_of=analysis_id)
    assert rerun.previous_analysis is not None
    assert "It ran fast." in rerun.render()["previous_analysis"]
    assert "Do not simply repeat it" in rerun.render()["previous_analysis"]


async def test_a_failed_previous_analysis_is_not_carried(fixture: Fixture) -> None:
    """There is nothing to not-repeat: a failed run said nothing."""
    from gaggiclanker.db.repos.analyses import AnalysesRepository, AnalysisStart

    analyses = AnalysesRepository(fixture.db)
    analysis_id = await analyses.start(
        AnalysisStart(shot_id=fixture.shots[-1], set_version_id=fixture.version_id)
    )
    await analyses.finish(analysis_id, status="failed", error="rate_limited: no")

    context = await build_context(fixture.db, fixture.shots[-1], rerun_of=analysis_id)
    assert context.previous_analysis is None


async def test_the_context_carries_reference_excerpts_with_citable_paths(
    fixture: Fixture,
) -> None:
    """Tier 2 reaches the prompt, and every excerpt is followable."""
    context = await build_context(fixture.db, fixture.shots[-1])
    assert context.excerpts
    for excerpt in context.excerpts:
        assert excerpt["heading_path"].startswith(f"{excerpt['doc_slug']}#")
        assert excerpt["body"]
        assert excerpt["query"]
    # At most one per document: two sections of one guide are one opinion twice.
    slugs = [excerpt["doc_slug"] for excerpt in context.excerpts]
    assert len(slugs) == len(set(slugs))
    assert context.excerpt_paths == {excerpt["heading_path"] for excerpt in context.excerpts}

    rendered = context.render()["knowledge_excerpts"]
    for path in context.excerpt_paths:
        assert f"[{path}]" in rendered


async def test_the_excerpt_budget_is_a_parameter_and_zero_turns_it_off(
    fixture: Fixture,
) -> None:
    generous = await build_context(fixture.db, fixture.shots[-1], chunk_token_budget=6000)
    frugal = await build_context(fixture.db, fixture.shots[-1], chunk_token_budget=400)
    off = await build_context(fixture.db, fixture.shots[-1], chunk_token_budget=0)

    assert len(generous.excerpts) >= len(frugal.excerpts) > 0
    assert sum(int(item["tokens_estimate"]) for item in frugal.excerpts) <= 400
    assert off.excerpts == []
    assert "no reference excerpts" in off.render()["knowledge_excerpts"]


async def test_only_confirmed_insights_whose_scope_matches_reach_the_context(
    fixture: Fixture,
) -> None:
    """The fixture holds three: one matching, one scoped elsewhere, one unconfirmed."""
    context = await build_context(fixture.db, fixture.shots[-1])
    assert [insight["text"] for insight in context.insights] == [
        "Naturals on this grinder want two numbers finer than a washed bean of the same roast."
    ]
    rendered = context.render()["learned_insights"]
    assert "process=natural" in rendered
    assert "unconfirmed" not in rendered.lower()


async def test_a_shot_with_no_set_still_builds_a_context(fixture: Fixture) -> None:
    """Retrieval must not need a Set: the taste and the bands are enough."""
    await fixture.db.execute(
        "UPDATE shots SET set_version_id = NULL WHERE id = ?", (fixture.shots[-1],)
    )
    context = await build_context(fixture.db, fixture.shots[-1])
    assert context.set is None
    assert context.excerpts
    assert context.insights == []
