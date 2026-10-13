"""The version-name rules: the label, the next name, the parse.

Pure, so every case is a line. What the repository does with them on a real
database is in `tests/sets/test_version_names.py`.
"""

from __future__ import annotations

import pytest

from gaggiclanker.domain.sets import (
    next_version_name,
    parse_version_label,
    version_label,
)


def test_the_label_leaves_a_zero_minor_off() -> None:
    assert [version_label(*pair) for pair in [(1, 0), (1, 1), (2, 0), (2, 10), (12, 3)]] == [
        "v1",
        "v1.1",
        "v2",
        "v2.10",
        "v12.3",
    ]


def test_a_minor_stays_in_the_current_major_and_a_major_starts_the_next() -> None:
    assert next_version_name(
        current_major=1, current_major_minor_max=2, highest_major=1, major=False
    ) == (1, 3)
    assert next_version_name(
        current_major=1, current_major_minor_max=2, highest_major=1, major=True
    ) == (2, 0)
    # The first version of a Set is a major from nothing.
    assert next_version_name(
        current_major=0, current_major_minor_max=0, highest_major=0, major=True
    ) == (1, 0)


def test_an_empty_set_starts_at_v1_whatever_the_change_is_called() -> None:
    """A minor from nothing would be v0.1, a name `parse_version_label` refuses."""
    assert next_version_name(
        current_major=0, current_major_minor_max=0, highest_major=0, major=False
    ) == (1, 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v1.1", (1, 1)),
        ("1.1", (1, 1)),
        ("V2", (2, 0)),
        ("2", (2, 0)),
        (" v3.12 ", (3, 12)),
        ("v2.0", (2, 0)),
        ("v0", None),
        ("", None),
        ("v", None),
        ("1.2.3", None),
        ("v-1", None),
        ("two", None),
        ("1.", None),
        # Leading zeros name nothing: "01" is not v1 and "v1.01" is not v1.1.
        ("01", None),
        ("v01", None),
        ("v1.01", None),
        ("1.00", None),
        ("00", None),
        ("1.10", (1, 10)),
        ("v10.0", (10, 0)),
    ],
)
def test_a_typed_name_is_parsed_or_refused(text: str, expected: tuple[int, int] | None) -> None:
    assert parse_version_label(text) == expected
