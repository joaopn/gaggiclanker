"""The starting point is told the drink the Recipe is for, and its knowledge entry whole."""

from __future__ import annotations

from gaggiclanker.db.repos.knowledge_docs import KnowledgeDocsRepository
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.starting.context import build_context
from gaggiclanker.starting.service import StartingPointService
from tests.starting.conftest import AS_OF, Fixture

NOTES = "oat milk (Oatly Barista), 200 ml"


async def _build(fixture: Fixture, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "bean_id": fixture.new_bean_id,
        "grinder_id": fixture.grinder_id,
        "usual_grind": "22",
        "as_of": AS_OF,
    }
    kwargs.update(overrides)
    return await build_context(fixture.db, **kwargs)  # type: ignore[arg-type]


async def test_the_drink_lines_and_the_entry_come_before_the_kit(fixture: Fixture) -> None:
    context = await _build(fixture, drink="latte", drink_notes=f"  {NOTES} ")
    doc = await KnowledgeDocsRepository(fixture.db).get_doc("LATTE")
    assert doc is not None

    rendered = context.render()["drink"]  # type: ignore[attr-defined]

    assert rendered.startswith("THE DRINK THIS RECIPE IS FOR\nDrink: Latte (a lot of milk")
    assert f"Drink notes: {NOTES}\n" in rendered
    assert rendered.count(doc.body.strip()) == 1
    assert rendered.endswith("\n\n"), "so the prompt can follow it with the next heading"
    assert context.drink == "latte" and context.drink_notes == NOTES  # type: ignore[attr-defined]


async def test_the_entry_is_not_also_an_excerpt_and_is_stored_with_the_run(
    fixture: Fixture,
) -> None:
    context = await _build(fixture, drink="latte")

    assert "LATTE" not in {e["doc_slug"] for e in context.excerpts}  # type: ignore[attr-defined]
    assert context.drink_reference.startswith("DRINK: LATTE")  # type: ignore[attr-defined]
    assert context.model_dump(mode="json")["drink_reference"].startswith("DRINK: LATTE")  # type: ignore[attr-defined]


async def test_other_has_the_line_and_no_entry(fixture: Fixture) -> None:
    context = await _build(fixture, drink="other")

    rendered = context.render()["drink"]  # type: ignore[attr-defined]

    assert rendered == (
        "THE DRINK THIS RECIPE IS FOR\nDrink: Other (anything else: tonic, affogato, shakerato)\n\n"
    )


async def test_a_run_with_no_drink_says_nothing_about_one(fixture: Fixture) -> None:
    context = await _build(fixture)

    assert context.render()["drink"] == ""  # type: ignore[attr-defined]
    assert context.drink is None and context.drink_reference == ""  # type: ignore[attr-defined]


async def test_the_prompt_puts_the_section_before_the_kit_and_leaves_no_gap_without_it(
    fixture: Fixture,
) -> None:
    from gaggiclanker.db.repos.llm import PromptsRepository
    from gaggiclanker.llm.prompts import PromptService

    prompts = PromptService(PromptsRepository(fixture.db))
    with_drink = await _build(fixture, drink="latte", drink_notes=NOTES)
    without = await _build(fixture)

    rendered = await prompts.load("starting_point-user", with_drink.render())  # type: ignore[attr-defined]
    plain = await prompts.load("starting_point-user", without.render())  # type: ignore[attr-defined]

    assert rendered.user.index("THE DRINK THIS RECIPE IS FOR") < rendered.user.index(
        "THE KIT IT WILL BE BREWED ON"
    )
    assert "\n\nTHE KIT IT WILL BE BREWED ON" in rendered.user
    assert "DRINK" not in plain.user
    assert "\n\n\n" not in plain.user


MILK_RULE = "[ristretto_and_milk]"


async def test_the_milk_rule_is_selected_for_a_milk_drink_run_and_for_no_other(
    fixture: Fixture,
) -> None:
    for drink in ("cappuccino", "latte"):
        context = await _build(fixture, drink=drink)
        assert f"drink:{drink}" in context.signals  # type: ignore[attr-defined]
        assert MILK_RULE in context.render()["knowledge_rules"]  # type: ignore[attr-defined]
    for other in ("espresso", "americano", "other", None):
        context = await _build(fixture, drink=other)
        assert (other is None) == (not [s for s in context.signals if s.startswith("drink:")])  # type: ignore[attr-defined]
        assert MILK_RULE not in context.render()["knowledge_rules"]  # type: ignore[attr-defined]


# ── the drink's document is kept out of the excerpts, and only when it is handed over ─────────


async def _rig_the_document(fixture: Fixture) -> None:
    """Make the drink's own document what retrieval would pick for a new coffee first.

    The starting point asks the knowledge base for a first shot and a first dial-in, and a
    document about milk drinks would never answer that on its own, so it is rewritten (an edited
    copy is a supported thing) to answer exactly that. Without this the exclusion could be
    removed and nothing would notice.
    """
    body = (
        "# Latte\n\n## Starting point for a new coffee\n\n"
        + "starting point for a new coffee, first shot dial in. " * 40
    )
    assert await KnowledgeService(fixture.db).set_doc_markdown("LATTE", body) is not None


def _slugs(context: object) -> set[str]:
    return {e["doc_slug"] for e in context.excerpts}  # type: ignore[attr-defined]


async def test_retrieval_would_pick_the_drinks_document_and_the_exclusion_is_what_stops_it(
    fixture: Fixture,
) -> None:
    await _rig_the_document(fixture)

    without_exclusion = await _build(fixture, drink="latte", drink_in_prompt=False)
    with_exclusion = await _build(fixture, drink="latte")

    assert "LATTE" in _slugs(without_exclusion)
    assert "LATTE" not in _slugs(with_exclusion)
    assert with_exclusion.drink_reference.startswith("DRINK: LATTE")  # type: ignore[attr-defined]
    assert without_exclusion.drink_reference == ""  # type: ignore[attr-defined]


async def test_an_edited_drink_document_is_the_block_and_still_not_an_excerpt(
    fixture: Fixture,
) -> None:
    await _rig_the_document(fixture)

    context = await _build(fixture, drink="latte")

    assert "first shot dial in. starting point" in context.render()["drink"]  # type: ignore[attr-defined]
    assert "LATTE" not in _slugs(context)


async def test_the_block_takes_none_of_the_excerpt_budget(fixture: Fixture) -> None:
    plain = await _build(fixture)
    assert plain.excerpts and "LATTE" not in _slugs(plain)  # type: ignore[attr-defined]
    tight = sum(int(e["tokens_estimate"]) for e in plain.excerpts)  # type: ignore[attr-defined,misc]

    plain = await _build(fixture, chunk_token_budget=tight)
    with_block = await _build(fixture, drink="latte", chunk_token_budget=tight)

    assert with_block.drink_reference  # type: ignore[attr-defined]
    assert with_block.excerpts == plain.excerpts  # type: ignore[attr-defined]


async def test_the_service_hands_over_the_entry_unless_the_prompt_was_edited_to_drop_it(
    starting: StartingPointService, fixture: Fixture
) -> None:
    await _rig_the_document(fixture)

    handed = await starting.propose(
        bean_id=fixture.new_bean_id, grinder_id=fixture.grinder_id, drink="latte", as_of=AS_OF
    )
    assert handed.input["drink_reference"].startswith("DRINK: LATTE")
    assert "LATTE" not in {e["doc_slug"] for e in handed.input["excerpts"]}

    prompts = PromptService(PromptsRepository(fixture.db))
    current = await prompts.get("starting_point-user")
    await prompts.save("starting_point-user", current["content"].replace("{{drink}}", ""))
    assert not await prompts.uses_variable("starting_point-user", "drink")

    dropped = await starting.propose(
        bean_id=fixture.new_bean_id, grinder_id=fixture.grinder_id, drink="latte", as_of=AS_OF
    )
    assert dropped.input["drink_reference"] == ""
    assert "LATTE" in {e["doc_slug"] for e in dropped.input["excerpts"]}


async def test_a_similar_recipes_card_shows_its_drink_when_stated_and_nothing_when_not(
    fixture: Fixture,
) -> None:
    guji = fixture.versions["guji"]
    await fixture.db.execute("UPDATE recipe_versions SET drink = NULL WHERE id != ?", (guji,))
    context = await _build(fixture)
    rendered = context.render()["similar_recipes"]  # type: ignore[attr-defined]

    assert "  drink: Cappuccino; oat milk (Oatly Barista), ~150 ml\n" in rendered
    cards = rendered.split("\n\n")
    stated = [card for card in cards if "drink:" in card]
    assert len(stated) == 1 and "Ethiopia Guji" in stated[0]
    assert all("drink" not in card for card in cards if card not in stated)
    # Nothing is scored on it: the same cards come in the same order whatever their drink.
    await fixture.db.execute("UPDATE recipe_versions SET drink = NULL, drink_notes = ''")
    plain = (await _build(fixture)).render()["similar_recipes"]  # type: ignore[attr-defined]
    assert "drink" not in plain
    assert [c.split("\n")[0] for c in plain.split("\n\n")] == [c.split("\n")[0] for c in cards]
