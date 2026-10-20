"""The drink block: the knowledge base's entry for a Recipe's drink, handed over whole.

A Recipe version states a drink (`domain/drinks.py`), and each drink but Other has a knowledge
document of its own. A conversation or a call about a Recipe of that drink is given the document
**whole, as the knowledge base stores it** (so a copy the person edited is what is read), under a
heading that says what it is: reference material, not evidence from this archive.

It is chosen, not searched. It is never one of the retrieved excerpts and is not counted in their
budget, and the document is passed to the retrieval as excluded (``exclude_docs``, from
:func:`drink_slug`) so the same text is not read twice.

Nothing is said when there is nothing to say: no drink stated, Other, or a document that has been
deleted from the knowledge base all give the empty string (unstated facts never reach a model).
"""

from __future__ import annotations

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.knowledge_docs import KnowledgeDocsRepository
from gaggiclanker.domain.drinks import drink_line, drink_term

__all__ = ["drink_lines", "drink_reference", "drink_slug"]


def drink_slug(drink: str | None) -> str | None:
    """The slug of the knowledge document that describes this drink, if it has one."""
    term = drink_term(drink)
    return None if term is None else term.document


async def drink_reference(db: Database, drink: str | None) -> str:
    """The drink block for this drink, or ``""`` when there is none to give."""
    term = drink_term(drink)
    if term is None or term.document is None:
        return ""
    doc = await KnowledgeDocsRepository(db).get_doc(term.document)
    if doc is None or not doc.body.strip():
        return ""
    return (
        f"DRINK: {term.label.upper()} — the knowledge base's entry, whole, as it is stored\n"
        "Reference material about the drink in general, not a record of this Recipe: what this "
        "archive's own shots show outranks it.\n\n"
        f"{doc.body.strip()}"
    )


def drink_lines(drink: str | None, notes: str) -> list[str]:
    """What the drink is, and how it is made around the shot: only what is stated.

    A fact nobody stated is not written as "none": a version from before drinks were recorded
    gets no line at all, and notes are told when there are some, whatever the drink.
    """
    lines: list[str] = []
    line = drink_line(drink)
    if line is not None:
        lines.append(f"Drink: {line}")
    if notes.strip():
        lines.append(f"Drink notes: {notes.strip()}")
    return lines
