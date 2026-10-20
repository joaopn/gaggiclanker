"""The four drink documents, and what moved out of the milk document to make them.

A drink with a knowledge entry has its document seeded; Other has none. The text that described
the milk drinks moved into the Cappuccino and Latte entries (moved, not copied), and the one line
that told the reader never to adjust the shot for milk was cut. The attribution file says so.
"""

from __future__ import annotations

import re
from pathlib import Path

from gaggiclanker.domain.drinks import DRINK_TERMS
from gaggiclanker.knowledge.service import DEFAULT_DOCS_DIR, KnowledgeService

DRINKS_DIR = DEFAULT_DOCS_DIR / "drinks"

#: The five sections each document has, in order.
SECTIONS = (
    "What this type covers",
    "What the shot has to do in this cup",
    "What it usually means for the shot's numbers",
    "What tastes wrong in this cup",
    "How the drink notes matter",
)

CUT_LINE = "Never adjust grind, ratio, pressure, or temperature"


def _text(name: str) -> str:
    return (DRINKS_DIR / name).read_text(encoding="utf-8")


async def test_each_drink_with_a_document_has_it_seeded_and_other_has_none(
    seeded_docs: KnowledgeService,
) -> None:
    for term in DRINK_TERMS:
        if term.document is None:
            assert term.value == "other"
            continue
        doc = await seeded_docs.docs.get_doc(term.document)
        assert doc is not None, term.value
        assert doc.title == term.label
        assert doc.chunk_count > 0
        assert doc.body == _text(f"{term.document}.md")
    assert {term.document for term in DRINK_TERMS if term.document} == {
        path.stem for path in DRINKS_DIR.glob("*.md")
    }


def test_every_document_has_its_five_sections_in_order_under_plain_headings() -> None:
    for term in DRINK_TERMS:
        if term.document is None:
            continue
        text = _text(f"{term.document}.md")
        assert text.startswith(f"# {term.label}\n")
        assert re.findall(r"^## (.+)$", text, re.MULTILINE) == list(SECTIONS), term.value
        assert not re.search(r"^#{4,}", text, re.MULTILINE)


def test_each_document_is_about_the_length_asked_for() -> None:
    """About 300 to 500 words of prose; the moved tables come on top for the milk drinks."""
    for term in DRINK_TERMS:
        if term.document is None:
            continue
        words = len(_text(f"{term.document}.md").split())
        assert 300 <= words <= 650, (term.value, words)


def test_each_document_opens_its_first_section_with_what_the_type_covers() -> None:
    for term in DRINK_TERMS:
        if term.document is None:
            continue
        first = _text(f"{term.document}.md").split("## What this type covers\n", 1)[1]
        assert first.strip().split("\n", 1)[0], term.value
    # The covers line the dialog shows is the idea each document starts from.
    assert "any ratio" in _text("ESPRESSO.md")
    assert "hot water" in _text("AMERICANO.md")
    assert "macchiato" in _text("CAPPUCCINO.md").lower()
    assert "mocha" in _text("LATTE.md")


def test_the_drink_notes_section_names_what_matters_for_that_drink() -> None:
    notes = {
        name: _text(name).split("## How the drink notes matter\n", 1)[1]
        for name in ("ESPRESSO.md", "AMERICANO.md", "CAPPUCCINO.md", "LATTE.md")
    }
    assert "water" in notes["AMERICANO.md"] and "ice" in notes["AMERICANO.md"]
    for name in ("CAPPUCCINO.md", "LATTE.md"):
        assert "oat" in notes[name] and "syrup" in notes[name].lower()
    assert "iced" in notes["LATTE.md"].lower()


def test_the_espresso_entry_names_the_ratio_document_instead_of_copying_it() -> None:
    espresso = _text("ESPRESSO.md")
    basics = (DEFAULT_DOCS_DIR / "ESPRESSO_BREWING_BASICS.md").read_text(encoding="utf-8")

    assert "Espresso Brewing Basics" in espresso
    assert "| Ratio | Style | Character |" not in espresso
    # The ratio table and the shot styles stay where they were.
    assert "| 1:1 to 1:1.5 | Ristretto |" in basics and "### Turbo Shot" in basics


def test_the_milk_drink_text_moved_out_of_the_milk_document_and_the_cut_line_is_gone() -> None:
    milk = (DEFAULT_DOCS_DIR / "MILK_AND_DRINKS.md").read_text(encoding="utf-8")
    cappuccino, latte = _text("CAPPUCCINO.md"), _text("LATTE.md")

    assert "## Espresso Drinks" not in milk
    assert "Drink Specifications" not in milk and "Drink Format Recommendations" not in milk
    for row in ("| Macchiato |", "| Cortado |", "| Piccolo |", "| Flat white |"):
        assert row in cappuccino and row not in milk
    assert "| Cappuccino (traditional) |" in cappuccino
    assert "| Latte | 2 oz (double) | 8-10 oz |" in latte
    assert "| Latte |" not in cappuccino and "| Cortado |" not in latte
    assert (
        "Extract for the bean's best expression, then match the right drink format." in cappuccino
    )
    # What stays: the steaming sections, the milk types, the single-boiler workflow, the table.
    for kept in (
        "## Milk Science & Steaming",
        "### Milk Types & Alternatives",
        "## Troubleshooting",
    ):
        assert kept in milk
    # Cut, in every document: the drink entries say the opposite.
    for path in DEFAULT_DOCS_DIR.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        if path.name != "ATTRIBUTION.md":
            assert CUT_LINE not in text, path.name


def test_the_attribution_says_what_moved_what_was_cut_and_where_it_went() -> None:
    attribution = (DEFAULT_DOCS_DIR / "ATTRIBUTION.md").read_text(encoding="utf-8")

    assert "gaggimate-barista" in attribution
    for name in ("ESPRESSO", "AMERICANO", "CAPPUCCINO", "LATTE"):
        assert f"drinks/{name}.md" in attribution
    assert "Espresso Drinks" in attribution and "MILK_AND_DRINKS.md" in attribution
    assert CUT_LINE in attribution and "Cut, and not moved" in attribution


def test_no_drink_document_stands_beside_the_others_by_accident() -> None:
    names = sorted(path.name for path in Path(DRINKS_DIR).iterdir())
    assert names == ["AMERICANO.md", "CAPPUCCINO.md", "ESPRESSO.md", "LATTE.md"]
