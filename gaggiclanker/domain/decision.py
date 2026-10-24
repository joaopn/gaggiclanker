"""Good or Bad? The one rule that says what a shot's decision is.

A person labels a shot Good, Bad or Discard (stored as ``keep``, ``improve`` and
``discard``). Most shots are never labelled, but most are rated, and a rating says
the same thing: three stars or more is a shot worth repeating, one or two is a shot
that should be better. So the decision a reader sees is the stored one when there
is one, and what the rating says otherwise. Discard is only ever a person's word
and is never worked out.

Pure: the SQL twin lives next to the queries (``db/repos/shots.py::DECISION_SQL``)
and a test runs both over every combination.
"""

from __future__ import annotations

from typing import Literal

from gaggiclanker.domain.vocab import Decision

#: Where the rating a decision was read from sits: this box's verdict, the machine's notes card,
#: or the machine's index.
type RatingSource = Literal["judgement", "machine_notes", "machine_index"]

#: The lowest rating that reads as Good. Below it (but above zero) reads as Bad.
GOOD_FROM_RATING = 3


def effective_decision(decision: Decision | None, rating: int | None) -> Decision | None:
    """The stored decision when there is one, else what the rating says, else ``None``.

    ``rating`` is the one rating the Rating column shows (the person's verdict,
    then the machine's notes card, then the index's). The machine writes 0 for
    "not rated", so a rating of zero or less is no rating at all.
    """
    if decision is not None:
        return decision
    return derived_decision(rating)


def rating_of(*ratings: int | None) -> int | None:
    """The first rating that is present, as the Rating column reads it.

    The order is the caller's: this box's verdict, then the machine's notes card, then the
    index's figure. Only ``None`` is skipped, as in SQL's ``COALESCE``, so a verdict of zero
    still hides the card's rating (and then reads as no rating at all).
    """
    return next((rating for rating in ratings if rating is not None), None)


def rating_source(
    judgement: int | None, machine_notes: int | None, machine_index: int | None
) -> RatingSource | None:
    """Which of the three ratings :func:`rating_of` reads, in the same order and by the same
    rule: the first one that is present, whatever its value."""
    for source, rating in (
        ("judgement", judgement),
        ("machine_notes", machine_notes),
        ("machine_index", machine_index),
    ):
        if rating is not None:
            return source  # type: ignore[return-value]
    return None


def derived_decision(rating: int | None) -> Decision | None:
    """What a rating alone says: ``keep`` from three stars, ``improve`` from one or two."""
    if rating is None or rating <= 0:
        return None
    return "keep" if rating >= GOOD_FROM_RATING else "improve"
