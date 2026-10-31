"""The rule that says Good, Neutral or Bad: the rating's band, and nothing else."""

from __future__ import annotations

import pytest

from gaggiclanker.domain.rating_band import rating_band, rating_of, rating_source
from gaggiclanker.domain.vocab import RatingBand


@pytest.mark.parametrize(
    ("rating", "expected"),
    [
        (None, None),
        (-1, None),
        (0, None),
        (1, "bad"),
        (2, "bad"),
        (3, "neutral"),
        (4, "good"),
        (5, "good"),
    ],
)
def test_four_and_five_stars_are_good_three_is_neutral_one_and_two_are_bad(
    rating: int | None, expected: RatingBand | None
) -> None:
    assert rating_band(rating) == expected


def test_a_rating_is_the_first_one_present_and_only_none_is_skipped() -> None:
    assert rating_of(None, 4, 2) == 4
    assert rating_of(None, None, 2) == 2
    assert rating_of(None, None, None) is None
    # A zero is present: the verdict's zero hides the card's four, as COALESCE does.
    assert rating_of(0, 4, 2) == 0
    assert rating_band(rating_of(0, 4, 2)) is None


def test_the_rating_source_is_the_first_place_that_holds_one() -> None:
    assert rating_source(2, 4, 5) == "judgement"
    assert rating_source(None, 4, 5) == "machine_notes"
    assert rating_source(None, None, 5) == "machine_index"
    assert rating_source(None, None, None) is None
    # The same reading as rating_of: a zero is present, and hides the ones behind it.
    assert rating_source(0, 4, 5) == "judgement"
