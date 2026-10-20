"""The drinks a Recipe version can be made for: the one list every layer reads.

A **drink** is what is added to the shot after it leaves the portafilter. It belongs to a
Recipe version, the next version inherits it, and a version made before drinks were recorded
states none (``NULL``: no drink stated, which is not Espresso).

This module is the source of truth. The database's ``CHECK`` on ``recipe_versions.drink``, the
``Literal`` that becomes the API's enum and ``GET /api/vocab`` are all written against
:data:`DRINKS`, and a test reads each of them back against it, so the web never keeps a list of
its own. The order is the order of the table the person picks from.

Each drink also names the knowledge document that describes it (:attr:`DrinkTerm.document`, a
seed document's slug), which is the text the agent is handed whole beside a Recipe of that drink.
Other has none: nothing is assumed about it.

Pure: no database, no FastAPI.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DRINKS",
    "DRINK_TERMS",
    "Drink",
    "DrinkTerm",
    "drink_label",
    "drink_line",
    "drink_term",
]

type Drink = Literal["espresso", "americano", "cappuccino", "latte", "other"]

#: In the order of the table the person picks from.
DRINKS: tuple[str, ...] = get_args(Drink.__value__)


class DrinkTerm(BaseModel):
    """One drink: its value, the word a person reads and what it covers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: Drink
    label: str
    #: What the type covers, in the words the New Recipe dialog shows beside the name, so a person
    #: filing a cortado knows to pick Cappuccino.
    covers: str
    #: The slug of the knowledge document that describes the drink, ``None`` for Other.
    document: str | None = None


DRINK_TERMS: tuple[DrinkTerm, ...] = (
    DrinkTerm(
        value="espresso",
        label="Espresso",
        covers="the shot as it comes out, any ratio",
        document="ESPRESSO",
    ),
    DrinkTerm(
        value="americano",
        label="Americano",
        covers="topped with hot water (long black too)",
        document="AMERICANO",
    ),
    DrinkTerm(
        value="cappuccino",
        label="Cappuccino",
        covers="a little milk: macchiato, cortado, flat white, cappuccino",
        document="CAPPUCCINO",
    ),
    DrinkTerm(
        value="latte",
        label="Latte",
        covers="a lot of milk: latte, iced latte, mocha",
        document="LATTE",
    ),
    DrinkTerm(
        value="other",
        label="Other",
        covers="anything else: tonic, affogato, shakerato",
        document=None,
    ),
)

_BY_VALUE: dict[str, DrinkTerm] = {term.value: term for term in DRINK_TERMS}


def drink_term(value: str | None) -> DrinkTerm | None:
    """The term for a stored value; ``None`` for no drink stated (or a value this build lacks)."""
    return None if value is None else _BY_VALUE.get(value)


def drink_label(value: str | None) -> str | None:
    """The word a person reads for a stored value, ``None`` when no drink is stated."""
    term = drink_term(value)
    return None if term is None else term.label


def drink_line(value: str | None) -> str | None:
    """``Cappuccino (a little milk: macchiato, cortado, flat white, cappuccino)``, or ``None``.

    The one sentence a model reads for a drink, so the chat, the review and the starting point
    say it identically.
    """
    term = drink_term(value)
    return None if term is None else f"{term.label} ({term.covers})"
