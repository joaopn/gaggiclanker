"""Shot id padding — the one conversion that sits between two device dialects."""

import pytest

from gaggiclanker.domain.ids import pad6, unpad


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (129, "000129"),
        ("129", "000129"),
        ("000129", "000129"),
        (0, "000000"),
        (1, "000001"),
        (999999, "999999"),
    ],
)
def test_pad6(value: int | str, expected: str) -> None:
    assert pad6(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("000129", 129), ("129", 129), (129, 129), ("000000", 0), (0, 0)],
)
def test_unpad(value: int | str, expected: int) -> None:
    assert unpad(value) == expected


def test_round_trip_is_stable() -> None:
    """pad6 and unpad are inverses in both directions."""
    for i in (0, 1, 42, 129, 999999):
        assert unpad(pad6(i)) == i
        assert pad6(unpad(pad6(i))) == pad6(i)


def test_ids_wider_than_six_digits_are_not_truncated() -> None:
    """A seven-digit counter must still name the right file, not a shorter one."""
    assert pad6(1234567) == "1234567"


@pytest.mark.parametrize("value", ["", "  ", "abc", "12a", "-1", -1, "1.5", True, None])
def test_rejects_non_ids(value: object) -> None:
    with pytest.raises(ValueError):
        unpad(value)  # type: ignore[arg-type]
