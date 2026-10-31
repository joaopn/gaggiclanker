"""Good, Neutral or Bad? The one rule that says which band a shot's rating is in.

The stars are the whole judgement. Four or five stars is a shot worth repeating (Good), three is
Neutral, one or two is a shot that should be better (Bad). The band is never stored: it is worked
out from the one rating the Rating column shows. Discarded is a different fact (the person said the
shot went wrong and must not be counted), is stored on its own, and is independent of the band; a
reader that counts shots leaves the discarded ones out before it reads the band.

Pure: the SQL twin lives next to the queries (``db/repos/shots.py::RATING_BAND_SQL``) and a test
runs both over every combination.
"""

from __future__ import annotations

from typing import Literal

from gaggiclanker.domain.vocab import RatingBand

#: Where the rating a band was read from sits: this box's verdict, the machine's notes card,
#: or the machine's index.
type RatingSource = Literal["judgement", "machine_notes", "machine_index"]

#: The lowest rating that reads as Good.
GOOD_FROM_RATING = 4
#: The rating that reads as Neutral. Below it (but above zero) reads as Bad.
NEUTRAL_RATING = 3


def rating_band(rating: int | None) -> RatingBand | None:
    """The band a rating is in: Good from four stars, Neutral at three, Bad at one or two.

    ``rating`` is the one rating the Rating column shows (the person's verdict, then the
    machine's notes card, then the index's). The machine writes 0 for "not rated", so a rating
    of zero or less is no rating at all and has no band.
    """
    if rating is None or rating <= 0:
        return None
    if rating >= GOOD_FROM_RATING:
        return "good"
    return "neutral" if rating == NEUTRAL_RATING else "bad"


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
