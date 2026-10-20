"""A review of a shot filed under a version with a drink: the block, the signal and the rule.

The drink's knowledge entry is handed over whole as its own section, outside the retrieved
excerpts and their budget, and never again as one of them. The `drink:<value>` signal selects
the rule that is for milk drinks; a shot with no drink, or no version, gets neither.
"""

from __future__ import annotations

import pytest

from gaggiclanker.db.repos.knowledge_docs import KnowledgeDocsRepository
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.recipes import RecipesRepository
from gaggiclanker.db.repos.reviews import ShotReviewsRepository
from gaggiclanker.knowledge.service import KnowledgeService, RetrievalContext
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.review.context import build_review_input
from gaggiclanker.review.service import ReviewService
from gaggiclanker.shotinfo import load_shots
from tests.review.conftest import Fixture
from tests.review.test_context import _rendered

MILK_RULE = "ristretto_and_milk"


async def _rules(fixture: Fixture) -> set[str]:
    review = await build_review_input(fixture.db, fixture.shots[-1])
    return {rule["key"] for rule in review.rules}


async def _set(fixture: Fixture, drink: str | None) -> None:
    await fixture.db.execute(
        "UPDATE recipe_versions SET drink = ? WHERE id = ?", (drink, fixture.version_id)
    )


async def test_a_review_of_a_cappuccino_shot_has_the_block_once_and_no_excerpt_from_its_document(
    fixture: Fixture,
) -> None:
    review = await build_review_input(fixture.db, fixture.shots[-1])
    doc = await KnowledgeDocsRepository(fixture.db).get_doc("CAPPUCCINO")
    assert doc is not None

    rendered = _rendered(review.render())

    assert review.drink_reference.startswith("DRINK: CAPPUCCINO")
    assert rendered.count(doc.body.strip()) == 1
    assert "CAPPUCCINO" not in {excerpt["doc_slug"] for excerpt in review.excerpts}
    assert "=== drink ===\nDRINK: CAPPUCCINO" in rendered
    # The version's own lines come from the shot information, under the version's recipe.
    assert "Recipe drink: Cappuccino" in review.shot


async def test_the_block_is_outside_the_excerpt_budget(fixture: Fixture) -> None:
    starved = await build_review_input(fixture.db, fixture.shots[-1], chunk_token_budget=1)

    assert starved.excerpts == []
    assert starved.drink_reference.startswith("DRINK: CAPPUCCINO")


async def test_the_drinks_document_is_left_out_of_the_retrieval_and_takes_none_of_its_budget(
    fixture: Fixture,
) -> None:
    knowledge = KnowledgeService(fixture.db)
    asking = RetrievalContext(extra_queries=("cappuccino cortado flat white milk foam",))

    without = await knowledge.select_chunks(asking)
    excluded = await knowledge.select_chunks(asking, exclude_docs=frozenset({"CAPPUCCINO"}))

    assert "CAPPUCCINO" in {excerpt.doc_slug for excerpt in without}, "it would have been read"
    assert "CAPPUCCINO" not in {excerpt.doc_slug for excerpt in excluded}
    assert excluded, "the budget goes to other documents instead"


@pytest.mark.parametrize(
    ("drink", "slug"),
    [("espresso", "ESPRESSO"), ("americano", "AMERICANO"), ("latte", "LATTE")],
)
async def test_each_drinks_review_has_that_drinks_block_and_never_a_chunk_of_it(
    fixture: Fixture, drink: str, slug: str
) -> None:
    await _set(fixture, drink)
    review = await build_review_input(fixture.db, fixture.shots[-1])

    assert review.drink_reference.startswith(f"DRINK: {drink.upper()}")
    assert slug not in {excerpt["doc_slug"] for excerpt in review.excerpts}
    assert f"drink:{drink}" in review.signals


async def test_other_and_no_drink_have_no_block_and_read_as_before(fixture: Fixture) -> None:
    for drink in ("other", None):
        await _set(fixture, drink)
        review = await build_review_input(fixture.db, fixture.shots[-1])

        assert review.drink_reference == ""
        assert review.render()["drink"] == ""
        assert "DRINK" not in _rendered(review.render())
    assert not [signal for signal in review.signals if signal.startswith("drink:")]


async def test_a_shot_in_the_inbox_has_no_block_and_no_signal(fixture: Fixture) -> None:
    await fixture.db.execute(
        "UPDATE shots SET recipe_version_id = NULL WHERE id = ?", (fixture.shots[-1],)
    )

    review = await build_review_input(fixture.db, fixture.shots[-1])

    assert review.drink_reference == ""
    assert not [signal for signal in review.signals if signal.startswith("drink:")]


async def test_the_drink_signal_is_the_filed_versions_drink(fixture: Fixture) -> None:
    review = await build_review_input(fixture.db, fixture.shots[-1])
    loaded = (await load_shots(fixture.db, [fixture.shots[-1]]))[0]

    assert "drink:cappuccino" in review.signals
    assert loaded.version is not None and loaded.version.drink == "cappuccino"
    assert review.signals == sorted(review.signals)


async def test_the_milk_drink_rule_is_selected_for_a_cappuccino_shot_and_for_a_latte_one(
    fixture: Fixture,
) -> None:
    assert MILK_RULE in await _rules(fixture)
    await _set(fixture, "latte")
    assert MILK_RULE in await _rules(fixture)


@pytest.mark.parametrize("drink", ["espresso", "americano", "other", None])
async def test_the_milk_drink_rule_is_not_selected_for_a_shot_that_is_not_a_milk_drink(
    fixture: Fixture, drink: str | None
) -> None:
    await _set(fixture, drink)

    assert MILK_RULE not in await _rules(fixture)


async def test_a_later_version_of_another_drink_changes_nothing_for_the_shot_already_filed(
    fixture: Fixture,
) -> None:
    from gaggiclanker.db.repos.recipes import RecipeVersionPatch

    recipes = RecipesRepository(fixture.db)
    changed = await recipes.add_version(
        fixture.recipe_id, RecipeVersionPatch(drink="americano", intent="Topped with water.")
    )
    assert changed is not None

    review = await build_review_input(fixture.db, fixture.shots[-1])

    assert "drink:cappuccino" in review.signals and "drink:americano" not in review.signals
    assert review.drink_reference.startswith("DRINK: CAPPUCCINO")


# ── the drink's document is kept out of the excerpts, and only when it is handed over ─────────


async def _rig_the_document(fixture: Fixture) -> None:
    """Make the drink's own document what retrieval would pick for this shot first.

    The review's queries come from the shot's style, so the document is rewritten (an edited
    copy is a supported thing) to answer exactly that, as a document about milk drinks never
    would on its own. Without this the exclusion could be removed and nothing would notice.
    """
    style = (await build_review_input(fixture.db, fixture.shots[-1])).style
    body = f"# Cappuccino\n\n## {style} shot profile\n\n" + f"the {style} shot profile. " * 150
    assert await KnowledgeService(fixture.db).set_doc_markdown("CAPPUCCINO", body) is not None


async def _slugs(fixture: Fixture, **kwargs: object) -> set[str]:
    review = await build_review_input(fixture.db, fixture.shots[-1], **kwargs)  # type: ignore[arg-type]
    return {excerpt["doc_slug"] for excerpt in review.excerpts}


async def test_retrieval_would_pick_the_drinks_document_and_the_exclusion_is_what_stops_it(
    fixture: Fixture,
) -> None:
    await _rig_the_document(fixture)

    without_exclusion = await _slugs(fixture, drink_in_prompt=False)
    with_exclusion = await _slugs(fixture)

    assert "CAPPUCCINO" in without_exclusion
    assert "CAPPUCCINO" not in with_exclusion
    review = await build_review_input(fixture.db, fixture.shots[-1])
    assert review.drink_reference.startswith("DRINK: CAPPUCCINO")


async def test_an_edited_drink_document_is_the_block_and_still_not_an_excerpt(
    fixture: Fixture,
) -> None:
    await _rig_the_document(fixture)

    review = await build_review_input(fixture.db, fixture.shots[-1])

    assert "shot profile. the" in review.drink_reference
    assert "CAPPUCCINO" not in {excerpt["doc_slug"] for excerpt in review.excerpts}


async def test_the_block_takes_none_of_the_excerpt_budget(fixture: Fixture) -> None:
    await fixture.db.execute("UPDATE recipe_versions SET drink = NULL")
    plain = await build_review_input(fixture.db, fixture.shots[-1])
    assert plain.excerpts and "CAPPUCCINO" not in {e["doc_slug"] for e in plain.excerpts}
    tight = sum(int(excerpt["tokens_estimate"]) for excerpt in plain.excerpts)
    plain = await build_review_input(fixture.db, fixture.shots[-1], chunk_token_budget=tight)
    await _set(fixture, "cappuccino")

    with_block = await build_review_input(fixture.db, fixture.shots[-1], chunk_token_budget=tight)

    assert with_block.drink_reference
    assert with_block.excerpts == plain.excerpts


async def _edited_review_prompt(fixture: Fixture) -> PromptService:
    prompts = PromptService(PromptsRepository(fixture.db))
    current = await prompts.get("review-user")
    assert "{{drink}}" in current["content"]
    await prompts.save("review-user", current["content"].replace("{{drink}}", ""))
    return prompts


async def test_a_prompt_with_the_variable_hands_over_the_entry_and_leaves_it_out_of_the_excerpts(
    reviewer: ReviewService, fixture: Fixture
) -> None:
    await _rig_the_document(fixture)

    row = await reviewer.run_review(fixture.shots[-1])

    detail = await ShotReviewsRepository(fixture.db).detail(row.id)
    assert detail is not None and detail.input is not None
    assert detail.input["drink_reference"].startswith("DRINK: CAPPUCCINO")
    assert "CAPPUCCINO" not in {e["doc_slug"] for e in detail.input["excerpts"]}


async def test_an_edited_prompt_without_the_variable_keeps_the_document_in_ordinary_retrieval(
    reviewer: ReviewService, fixture: Fixture
) -> None:
    await _rig_the_document(fixture)
    prompts = await _edited_review_prompt(fixture)
    assert not await prompts.uses_variable("review-user", "drink")
    assert await prompts.uses_variable("review-user", "shot_information")

    row = await reviewer.run_review(fixture.shots[-1])

    detail = await ShotReviewsRepository(fixture.db).detail(row.id)
    assert detail is not None and detail.input is not None
    assert detail.input["drink_reference"] == ""
    assert "CAPPUCCINO" in {e["doc_slug"] for e in detail.input["excerpts"]}
