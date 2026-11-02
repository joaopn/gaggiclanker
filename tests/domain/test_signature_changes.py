"""Whether a change loosens a check: one pure function, a test for each case.

Put back and tightening are not loosening; a retirement, a lower tier, a looser compare on the
same expression and any replacement that differs in more than its compare are.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.domain.metric_language import Compare, Expression
from gaggiclanker.domain.signature_changes import (
    PutBackChange,
    PutBackOverrideChange,
    ReplaceChange,
    RetireChange,
    StoredChanges,
    StoredReplacement,
    TierChange,
    change_loosens,
    compare_loosens,
    request_loosens,
)


class Row:
    """What the rule reads of the row a change is about."""

    def __init__(self, tier: str, expression: Expression | None, *, kind: str = "measure") -> None:
        self.id = 1
        self.position = 0
        self.tier = tier
        self.phase = "hold"
        self.kind = kind
        self.expression = expression
        self.warning_fault = None
        self.text = ""
        self.fault = None
        self.sentence = "s"


def expression(compare: dict[str, Any], **extra: Any) -> Expression:
    return Expression.model_validate(
        {
            "channel": "temperature",
            "op": "min",
            "window": {"phase": "hold"},
            "compare": compare,
            **extra,
        }
    )


def replace(compare: dict[str, Any], *, tier: str = "important", **extra: Any) -> ReplaceChange:
    return ReplaceChange(
        expectation_id=1,
        reason="a reason that is long enough",
        expectation=StoredReplacement(
            tier=tier,  # type: ignore[arg-type]
            kind="measure",
            phase="hold",
            expression=expression(compare, **extra),
            sentence="new",
        ),
    )


GE = {"op": ">=", "value": 93}
OLD = Row("important", expression(GE))


@pytest.mark.parametrize(
    ("old", "new", "loosens"),
    [
        ({"op": ">=", "value": 93}, {"op": ">=", "value": 91.5}, True),
        ({"op": ">=", "value": 93}, {"op": ">=", "value": 94}, False),
        ({"op": ">", "value": 93}, {"op": ">", "value": 92}, True),
        ({"op": ">", "value": 93}, {"op": ">", "value": 94}, False),
        ({"op": "<=", "value": 0.15}, {"op": "<=", "value": 0.2}, True),
        ({"op": "<=", "value": 0.15}, {"op": "<=", "value": 0.1}, False),
        ({"op": "<", "value": 4}, {"op": "<", "value": 5}, True),
        ({"op": "<", "value": 4}, {"op": "<", "value": 3}, False),
        ({"op": "between", "low": 4, "high": 6}, {"op": "between", "low": 3, "high": 6}, True),
        ({"op": "between", "low": 4, "high": 6}, {"op": "between", "low": 4, "high": 7}, True),
        ({"op": "between", "low": 4, "high": 6}, {"op": "between", "low": 5, "high": 6}, False),
        ({"op": "between", "low": 4, "high": 6}, {"op": "between", "low": 4, "high": 5}, False),
        # Wider on one side and narrower on the other still lets in what was refused.
        ({"op": "between", "low": 4, "high": 6}, {"op": "between", "low": 3, "high": 5}, True),
        # The same number: the inclusive form is the looser one.
        ({"op": ">", "value": 93}, {"op": ">=", "value": 93}, True),
        ({"op": ">=", "value": 93}, {"op": ">", "value": 93}, False),
        ({"op": "<", "value": 4}, {"op": "<=", "value": 4}, True),
        ({"op": "<=", "value": 4}, {"op": "<", "value": 4}, False),
        # A limit that changes direction or shape cannot be called tighter.
        ({"op": ">=", "value": 93}, {"op": "<=", "value": 93}, True),
        ({"op": ">=", "value": 93}, {"op": "between", "low": 93, "high": 95}, True),
    ],
)
def test_a_compare_loosens_by_the_direction_it_moves(
    old: dict[str, Any], new: dict[str, Any], loosens: bool
) -> None:
    assert compare_loosens(Compare.model_validate(old), Compare.model_validate(new)) is loosens
    assert change_loosens(replace(new), Row("important", expression(old))) is loosens


def test_a_retirement_loosens() -> None:
    change = RetireChange(expectation_id=1, reason="a reason that is long enough")
    assert change_loosens(change, OLD)


def test_a_lower_tier_loosens_and_a_higher_one_does_not() -> None:
    def tier(to: str) -> TierChange:
        return TierChange(expectation_id=1, tier=to, reason="a reason that is long enough")  # type: ignore[arg-type]

    assert change_loosens(tier("context"), OLD)
    assert change_loosens(tier("context"), Row("critical", expression(GE)))
    assert change_loosens(tier("important"), Row("critical", expression(GE)))
    assert not change_loosens(tier("critical"), OLD)
    assert not change_loosens(tier("critical"), Row("context", expression(GE)))


def test_a_replacement_that_differs_in_more_than_its_compare_loosens() -> None:
    # The same compare, another channel / window / op / phase / kind.
    assert change_loosens(replace(GE, window={"phase": "ramp"}), OLD)
    assert change_loosens(replace(GE, op="mean"), OLD)
    assert change_loosens(replace(GE, offset_from="profile_temperature"), OLD)
    other_phase = replace(GE)
    other_phase.expectation.phase = "ramp"
    assert change_loosens(other_phase, OLD)
    free = ReplaceChange(
        expectation_id=1,
        reason="a reason that is long enough",
        expectation=StoredReplacement(
            tier="important",
            kind="free_text",
            phase="hold",
            text="x",
            fault="unstable",
            sentence="x",
        ),
    )
    assert change_loosens(free, OLD)


def test_a_replacement_to_a_lower_tier_loosens_even_with_a_tighter_compare() -> None:
    assert change_loosens(replace({"op": ">=", "value": 94}, tier="context"), OLD)
    assert not change_loosens(replace({"op": ">=", "value": 94}, tier="critical"), OLD)


def test_putting_back_is_not_loosening() -> None:
    back = PutBackChange(expectation_id=1, reason="a reason that is long enough")
    assert not change_loosens(back, OLD)
    override = PutBackOverrideChange(
        override_id=1, recipe_version_id=1, reason="a reason that is long enough"
    )
    assert not change_loosens(override, None)


def test_a_request_loosens_when_any_change_does() -> None:
    tight = replace({"op": ">=", "value": 94})
    loose = RetireChange(expectation_id=2, reason="a reason that is long enough")
    rows = {1: OLD, 2: Row("important", expression(GE))}
    assert not request_loosens(StoredChanges(changes=[tight]), rows)
    assert request_loosens(StoredChanges(changes=[tight, loose]), rows)


# ── the words a card says, and the rows a request names ──────────────


def test_a_sentence_says_its_limit_in_its_unit_once_for_every_card() -> None:
    from gaggiclanker.domain.metric_language import render
    from gaggiclanker.domain.signature import sentence_with_unit

    def words(channel: str, op: str, compare: dict[str, Any], **extra: Any) -> tuple[str, str]:
        expression = Expression.model_validate(
            {"channel": channel, "op": op, "window": {"phase": "hold"}, "compare": compare, **extra}
        )
        return render(expression), sentence_with_unit(render(expression), expression)

    assert words("temperature", "min", {"op": ">=", "value": 93}) == (
        "lowest value of temperature in the hold, at least 93",
        "lowest value of temperature in the hold, at least 93 °C",
    )
    assert words("pressure", "mean", {"op": "between", "low": 6, "high": 9})[1].endswith(
        "between 6 and 9 bar"
    )
    # A limit that follows the setpoint already says its unit; a row with no comparison has none.
    following = words(
        "temperature", "min", {"op": ">=", "value": -2}, offset_from="profile_temperature"
    )
    assert following[0] == following[1]
    assert sentence_with_unit("pressure wanders", None) == "pressure wanders"


def test_a_put_back_override_names_its_expectation_as_well_as_itself() -> None:
    from gaggiclanker.domain.signature_changes import rows_named

    change = PutBackOverrideChange(
        reason="x" * 20, override_id=9, recipe_version_id=3, expectation_id=5
    )
    assert rows_named(change) == [("o", 9), ("e", 5)]
    assert StoredChanges(changes=[change]).expectation_ids() == [5]
    assert StoredChanges(changes=[change]).override_ids() == [9]
    retire = RetireChange(reason="x" * 20, expectation_id=5)
    assert rows_named(retire) == [("e", 5)]


def test_a_limit_bounds_the_side_its_operator_says() -> None:
    from gaggiclanker.domain.signature_changes import compare_sides

    assert compare_sides(Compare(op=">=", value=1)) == {"lower"}
    assert compare_sides(Compare(op=">", value=1)) == {"lower"}
    assert compare_sides(Compare(op="<=", value=1)) == {"upper"}
    assert compare_sides(Compare(op="<", value=1)) == {"upper"}
    assert compare_sides(Compare(op="between", low=1, high=2)) == {"lower", "upper"}
    assert compare_sides(None) == frozenset()
