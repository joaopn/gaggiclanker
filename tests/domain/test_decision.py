"""The rule that says Good or Bad: a stored decision, else the rating, else nothing."""

from __future__ import annotations

import pytest

from gaggiclanker.domain.decision import (
    derived_decision,
    effective_decision,
    rating_of,
    rating_source,
)
from gaggiclanker.domain.vocab import Decision

DECISIONS: tuple[Decision | None, ...] = (None, "keep", "improve", "discard")
RATINGS: tuple[int | None, ...] = (None, 0, -1, 1, 2, 3, 4, 5)


@pytest.mark.parametrize(
    ("rating", "expected"),
    [
        (None, None),
        (-1, None),
        (0, None),
        (1, "improve"),
        (2, "improve"),
        (3, "keep"),
        (4, "keep"),
        (5, "keep"),
    ],
)
def test_the_rating_alone_says_bad_below_three_and_good_from_three(
    rating: int | None, expected: Decision | None
) -> None:
    assert derived_decision(rating) == expected
    assert effective_decision(None, rating) == expected


@pytest.mark.parametrize("decision", ["keep", "improve", "discard"])
@pytest.mark.parametrize("rating", RATINGS)
def test_a_stored_decision_always_wins_discard_included(
    decision: Decision, rating: int | None
) -> None:
    assert effective_decision(decision, rating) == decision


def test_discard_is_never_worked_out_from_a_rating() -> None:
    assert all(effective_decision(None, rating) != "discard" for rating in RATINGS)


def test_a_rating_is_the_first_one_present_and_only_none_is_skipped() -> None:
    assert rating_of(None, 4, 2) == 4
    assert rating_of(None, None, 2) == 2
    assert rating_of(None, None, None) is None
    # A zero is present: the verdict's zero hides the card's four, as COALESCE does.
    assert rating_of(0, 4, 2) == 0
    assert effective_decision(None, rating_of(0, 4, 2)) is None


def test_the_rating_source_is_the_first_place_that_holds_one() -> None:
    assert rating_source(2, 4, 5) == "judgement"
    assert rating_source(None, 4, 5) == "machine_notes"
    assert rating_source(None, None, 5) == "machine_index"
    assert rating_source(None, None, None) is None
    # The same reading as rating_of: a zero is present, and hides the ones behind it.
    assert rating_source(0, 4, 5) == "judgement"
