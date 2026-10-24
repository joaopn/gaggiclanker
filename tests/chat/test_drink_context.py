"""What a conversation is told about the drink: the lines, and the knowledge entry for it.

A Recipe conversation, a design conversation, a starting point and a review all say the drink
the same way (`Drink: Cappuccino (a little milk: ...)`, `Drink notes: ...`) and hand over the
drink's knowledge entry whole, as the knowledge base stores it, as reference material. A fact
nobody stated is not written: no drink, no line and no block.
"""

from __future__ import annotations

import pytest

from gaggiclanker.chat.context import opening_context
from gaggiclanker.chat.design_context import design_context
from gaggiclanker.db.repos.knowledge_docs import KnowledgeDocsRepository
from gaggiclanker.db.repos.recipes import DesignBrief, RecipesRepository, RecipeVersionPatch
from gaggiclanker.domain.drinks import DRINK_TERMS, drink_line
from gaggiclanker.knowledge.drinks import drink_lines, drink_reference
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.tools.scope import ToolScope
from tests.chat.test_context import Experiment, experiment
from tests.chat.test_design_context import _forked_design, kitchen
from tests.starting.conftest import Fixture

__all__ = ["experiment", "kitchen"]  # the fixtures, re-exported for this module

COVERS = "Cappuccino (a little milk: macchiato, cortado, flat white, cappuccino)"
NOTES = "oat milk (Oatly Barista), ~150 ml"


async def _render(exp: Experiment) -> str:
    return await opening_context(exp.db, ToolScope.for_thread(exp.recipe_id, exp.v5))


async def _set(exp: Experiment, drink: str | None, notes: str) -> None:
    await exp.db.execute(
        "UPDATE recipe_versions SET drink = ?, drink_notes = ? WHERE recipe_id = ?",
        (drink, notes, exp.recipe_id),
    )


async def _entry(exp: Experiment, slug: str) -> str:
    doc = await KnowledgeDocsRepository(exp.db).get_doc(slug)
    assert doc is not None
    return doc.body.strip()


def test_the_lines_are_only_what_is_stated() -> None:
    assert drink_lines("cappuccino", NOTES) == [f"Drink: {COVERS}", f"Drink notes: {NOTES}"]
    assert drink_lines("latte", "  ") == ["Drink: Latte (a lot of milk: latte, iced latte, mocha)"]
    assert drink_lines(None, "") == []
    # Notes are a stated fact whatever the drink.
    assert drink_lines(None, "ice") == ["Drink notes: ice"]
    assert drink_line("other") == "Other (anything else: tonic, affogato, shakerato)"


async def test_the_lines_sit_under_the_version_numbers(experiment: Experiment) -> None:
    lines = (await _render(experiment)).split("\n")

    numbers = next(i for i, line in enumerate(lines) if line.startswith("Version numbers:"))
    assert lines[numbers + 1] == f"Drink: {COVERS}"
    assert lines[numbers + 2] == f"Drink notes: {NOTES}"
    assert lines[numbers + 3].startswith("Changed against")


async def test_the_block_is_the_entry_whole_once_and_inside_the_protected_prefix(
    experiment: Experiment,
) -> None:
    text = await _render(experiment)
    entry = await _entry(experiment, "CAPPUCCINO")

    assert text.count(entry) == 1
    block = text.index("DRINK: CAPPUCCINO")
    assert text.index("THE PROFILE") < block < text.index("THIS CONVERSATION IS ABOUT")
    assert "Reference material about the drink in general" in text
    assert entry.startswith("# Cappuccino") and "## What the shot has to do in this cup" in entry


#: One drink with a document and the one without: every document's existence and slug are pinned in
#: tests/knowledge/test_drink_documents.py, and rendering is one lookup by slug.
SAMPLED_TERMS = [term for term in DRINK_TERMS if term.value in ("espresso", "other")]


@pytest.mark.parametrize("term", SAMPLED_TERMS, ids=lambda term: term.value)
async def test_each_drink_renders_its_own_document_and_other_has_no_block(
    experiment: Experiment, term: object
) -> None:
    drink, slug = term.value, term.document  # type: ignore[attr-defined]
    await _set(experiment, drink, "")

    text = await _render(experiment)

    assert f"Drink: {drink_line(drink)}" in text
    if slug is None:
        assert "DRINK: " not in text
        return
    assert text.count(await _entry(experiment, slug)) == 1
    assert text.count("DRINK: ") == 1
    others = [t.document for t in DRINK_TERMS if t.document and t.document != slug]
    for other in others:
        assert await _entry(experiment, other) not in text


async def test_an_entry_the_person_edited_is_what_is_read(experiment: Experiment) -> None:
    mine = "# Cappuccino\n\n## Mine\n\nMy own words about a cappuccino, edited by me."
    assert await KnowledgeService(experiment.db).set_doc_markdown("CAPPUCCINO", mine) is not None

    text = await _render(experiment)

    assert "My own words about a cappuccino, edited by me." in text
    assert "What the shot has to do in this cup" not in text


async def test_no_drink_stated_says_nothing_at_all(experiment: Experiment) -> None:
    await _set(experiment, None, "")

    text = await _render(experiment)

    assert "Drink" not in text and "DRINK" not in text
    assert "drink" not in text.lower().replace("drip", "")


async def test_notes_without_a_drink_are_told_and_have_no_block(experiment: Experiment) -> None:
    await _set(experiment, None, "over ice")

    text = await _render(experiment)

    assert "Drink notes: over ice" in text
    assert "Drink: " not in text and "DRINK: " not in text


async def test_a_deleted_entry_gives_no_block_and_the_lines_stay(experiment: Experiment) -> None:
    await experiment.db.execute("DELETE FROM knowledge_docs WHERE slug = 'CAPPUCCINO'")

    text = await _render(experiment)

    assert f"Drink: {COVERS}" in text and "DRINK: " not in text
    assert await drink_reference(experiment.db, "cappuccino") == ""


async def test_a_version_change_of_drink_is_a_line_of_what_changed(experiment: Experiment) -> None:
    recipes = RecipesRepository(experiment.db)
    changed = await recipes.add_version(
        experiment.recipe_id,
        RecipeVersionPatch(
            drink="latte", drink_notes="whole milk, 250 ml", intent="A bigger drink."
        ),
    )
    assert changed is not None

    text = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.recipe_id, changed.id)
    )

    assert "Drink Cappuccino → Latte" in text
    assert "Drink notes oat milk (Oatly Barista), ~150 ml → whole milk, 250 ml" in text
    assert "DRINK: LATTE" in text and "DRINK: CAPPUCCINO" not in text


# ── the design conversation ──────────────────────────────────────────────


async def test_the_design_chat_is_told_the_briefs_drink_and_notes_and_the_entry(
    kitchen: Fixture,
) -> None:
    designed = await _forked_design(kitchen)

    text = await design_context(kitchen.db, designed.id)

    brief = text.index("WHAT THEY ASKED FOR")
    assert f"Drink: {COVERS}" in text and f"Drink notes: {NOTES}" in text
    assert brief < text.index(f"Drink: {COVERS}") < text.index("Goal:")
    doc = await KnowledgeDocsRepository(kitchen.db).get_doc("CAPPUCCINO")
    assert doc is not None and text.count(doc.body.strip()) == 1
    assert text.index("DRINK: CAPPUCCINO") > brief


async def test_a_brief_from_before_drinks_states_nothing(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)
    old = DesignBrief(goal="More body.").model_dump_json(exclude={"drink", "drink_notes"})
    await kitchen.db.execute("UPDATE recipes SET design_brief = ? WHERE id = ?", (old, designed.id))

    text = await design_context(kitchen.db, designed.id)

    assert "Drink: " not in text and "DRINK: " not in text and "Drink notes" not in text


async def test_a_design_for_other_has_the_line_and_no_block(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)
    brief = designed.design_brief.model_copy(update={"drink": "other", "drink_notes": ""})
    await kitchen.db.execute(
        "UPDATE recipes SET design_brief = ? WHERE id = ?", (brief.model_dump_json(), designed.id)
    )

    text = await design_context(kitchen.db, designed.id)

    assert "Drink: Other (anything else: tonic, affogato, shakerato)" in text
    assert "DRINK: " not in text


async def test_the_milk_rule_follows_the_briefs_drink(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)

    async def rendered(drink: str | None) -> str:
        brief = designed.design_brief.model_copy(update={"drink": drink})
        await kitchen.db.execute(
            "UPDATE recipes SET design_brief = ? WHERE id = ?",
            (brief.model_dump_json(), designed.id),
        )
        return await design_context(kitchen.db, designed.id)

    for drink in ("cappuccino", "latte"):
        assert "[ristretto_and_milk]" in await rendered(drink)
    for other in ("espresso", "americano", "other", None):
        assert "[ristretto_and_milk]" not in await rendered(other)


async def _brief_with(kitchen: Fixture, designed_id: int, drink: str | None, notes: str) -> None:
    designed = await RecipesRepository(kitchen.db).get(designed_id)
    assert designed is not None
    brief = designed.design_brief.model_copy(update={"drink": drink, "drink_notes": notes})
    await kitchen.db.execute(
        "UPDATE recipes SET design_brief = ? WHERE id = ?", (brief.model_dump_json(), designed_id)
    )


@pytest.mark.parametrize("term", SAMPLED_TERMS, ids=lambda term: term.value)
async def test_the_design_chat_has_each_drinks_block_and_other_has_none(
    kitchen: Fixture, term: object
) -> None:
    drink, slug = term.value, term.document  # type: ignore[attr-defined]
    designed = await _forked_design(kitchen)
    await _brief_with(kitchen, designed.id, drink, "")

    text = await design_context(kitchen.db, designed.id)

    assert f"Drink: {drink_line(drink)}" in text
    if slug is None:
        assert "DRINK: " not in text
        return
    doc = await KnowledgeDocsRepository(kitchen.db).get_doc(slug)
    assert doc is not None and text.count(doc.body.strip()) == 1
    assert text.count("DRINK: ") == 1
    for other in (t.document for t in DRINK_TERMS if t.document and t.document != slug):
        other_doc = await KnowledgeDocsRepository(kitchen.db).get_doc(other)
        assert other_doc is not None and other_doc.body.strip() not in text


async def test_the_design_chat_with_no_drink_has_no_line_and_no_block(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)
    await _brief_with(kitchen, designed.id, None, "")

    text = await design_context(kitchen.db, designed.id)

    assert "Drink" not in text.split("WHAT THEY ASKED FOR")[1].split("Goal:")[0]
    assert "DRINK: " not in text


async def test_an_entry_the_person_edited_is_what_the_design_chat_reads(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)
    mine = "# Cappuccino\n\n## Mine\n\nMy own words about a cappuccino, edited by me."
    assert await KnowledgeService(kitchen.db).set_doc_markdown("CAPPUCCINO", mine) is not None

    text = await design_context(kitchen.db, designed.id)

    assert "My own words about a cappuccino, edited by me." in text
    assert "What the shot has to do in this cup" not in text
