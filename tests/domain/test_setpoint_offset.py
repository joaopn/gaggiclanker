"""A limit that follows the profile's temperature: ``offset_from: "profile_temperature"``.

Every value is worked out here from the constructed lever shot (a real fixture rewritten) with
the temperature samples of the soak edited, never read back from the evaluator.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from gaggiclanker.domain.diagnostics import as_sample_dicts
from gaggiclanker.domain.metric_language import (
    SETPOINT_UNIT,
    Expression,
    ShotData,
    canonical_form,
    evaluate,
    method_id,
    render,
)
from gaggiclanker.domain.signature import (
    ExpectationInput,
    limit_text,
    validate_expectation,
    value_text,
)
from tests.lever_shot import LEVER_PROFILE, lever_shot
from tests.signatures.helpers import shot_data

#: The soak runs from sample 29 to 68; its lowest temperature is made 91.9 here.
SOAK = range(29, 69)


def sagging(setpoint: float | None = 93.0) -> ShotData:
    base = shot_data()
    samples = [dict(s) for s in as_sample_dicts(lever_shot())]
    for index in SOAK:
        samples[index]["ct"] = 92.5
    samples[40]["ct"] = 91.9
    return ShotData.build(
        samples,
        lever_shot().transitions,
        profile_phases=base.profile_phases,
        has_pressure=True,
        scale_connected=True,
        final_weight_g=base.final_weight_g,
        target_yield_g=36.0,
        dose_g=18.0,
        setpoint_c=setpoint,
    )


def floor(value: float, op: str = ">=", **extra: Any) -> Expression:
    return Expression.model_validate(
        {
            "channel": "temperature",
            "op": "min",
            "window": {"phase": "soak"},
            "offset_from": "profile_temperature",
            "compare": {"op": op, "value": value},
            **extra,
        }
    )


def test_the_value_is_the_measure_minus_the_setpoint_rounded_to_the_channels_decimals() -> None:
    result = evaluate(floor(-2.0), sagging())
    assert result.value == -1.1  # 91.9 - 93, not -1.0999999999999943
    assert result.held is True
    assert result.unit == "°C from the setpoint"


def test_the_same_floor_holds_at_another_setpoint_and_fails_where_the_shot_sags_under_it() -> None:
    # Moved with the profile: a floor 2 below the setpoint, on a shot whose low is 1.1 below.
    assert evaluate(floor(-2.0), sagging(93.0)).held is True
    # Written as an absolute limit it would fail at 93; as an offset it follows the setpoint
    # up to 95 and the same shot is 3.1 below it.
    result = evaluate(floor(-2.0), sagging(95.0))
    assert result.value == -3.1
    assert result.held is False
    # Without the offset the same compare is an absolute temperature, and the value is 91.9.
    plain = Expression.model_validate(
        {
            "channel": "temperature",
            "op": "min",
            "window": {"phase": "soak"},
            "compare": {"op": ">=", "value": -2.0},
        }
    )
    assert evaluate(plain, sagging()).value == 91.9


@pytest.mark.parametrize("setpoint", [None, 0.0])
def test_a_profile_with_no_temperature_is_absent_with_no_target(setpoint: float | None) -> None:
    result = evaluate(floor(-2.0), sagging(setpoint))
    assert result.value is None
    assert result.absent == "no_target"
    assert result.why == "the profile states no temperature"
    assert result.held is None


def test_an_offset_is_refused_off_the_temperature_channels_and_beside_relative_to() -> None:
    with pytest.raises(ValidationError, match="temperature channels only"):
        Expression.model_validate(
            {"channel": "pressure", "op": "min", "offset_from": "profile_temperature"}
        )
    with pytest.raises(ValidationError, match="cannot be combined"):
        Expression.model_validate(
            {
                "channel": "temperature",
                "op": "min",
                "offset_from": "profile_temperature",
                "relative_to": "dose",
            }
        )
    with pytest.raises(ValidationError, match="a level of the temperature"):
        Expression.model_validate(
            {"channel": "temperature", "op": "change", "offset_from": "profile_temperature"}
        )
    # The target channel is allowed.
    Expression.model_validate(
        {"channel": "target_temperature", "op": "max", "offset_from": "profile_temperature"}
    )


def test_the_canonical_form_and_the_method_id_carry_the_offset() -> None:
    with_offset, without = floor(-2.0), floor(-2.0)
    without = without.model_copy(update={"offset_from": None})
    assert '"offset_from":"profile_temperature"' in canonical_form(with_offset)
    assert method_id(with_offset) != method_id(without)
    assert "offset_from" not in canonical_form(without)


def test_the_words_on_each_compare() -> None:
    assert (
        render(floor(-2.0))
        == "lowest value of temperature in the soak, at least 2 °C below the setpoint"
    )
    assert render(floor(1.5, "<=")) == (
        "lowest value of temperature in the soak, at most 1.5 °C above the setpoint"
    )
    assert render(floor(0.0)).endswith("at least the setpoint")
    between = Expression.model_validate(
        {
            "channel": "temperature",
            "op": "max",
            "window": {},
            "offset_from": "profile_temperature",
            "compare": {"op": "between", "low": -2, "high": 1},
        }
    )
    assert render(between) == (
        "highest value of temperature in the whole shot, between 2 °C below the setpoint "
        "and 1 °C above the setpoint"
    )
    expr = floor(-2.0)
    assert limit_text(expr, "°C from the setpoint") == "at least 2 °C below the setpoint"
    assert limit_text(floor(1.5, "<="), "°C from the setpoint") == (
        "at most 1.5 °C above the setpoint"
    )
    assert limit_text(between, "°C from the setpoint") == (
        "between 2 °C below the setpoint and 1 °C above the setpoint"
    )
    assert value_text(-0.8, "°C from the setpoint", None) == "0.8 °C below the setpoint"
    assert value_text(1.2, "°C from the setpoint", None) == "1.2 °C above the setpoint"
    assert value_text(0.0, "°C from the setpoint", None) == "at the setpoint"
    # No limit or value of an offset ever carries a minus sign or "from the setpoint".
    for words in (
        limit_text(expr, ""),
        limit_text(between, ""),
        value_text(-0.8, SETPOINT_UNIT, None),
    ):
        assert "-" not in words and "from the setpoint" not in words


def test_two_offsets_are_two_expectations_and_validate_like_any_measure() -> None:
    valid = validate_expectation(
        ExpectationInput(
            tier="important",
            kind="measure",
            phase="soak",
            expression=floor(-2.0).model_dump(exclude_none=True, by_alias=True),
        ),
        ["preinfusion", "soak", "ramp", "decline"],
    )
    assert valid.expression is not None and valid.expression.offset_from == "profile_temperature"
    assert valid.sentence.endswith("at least 2 °C below the setpoint")


def test_the_setpoint_comes_from_the_profile_rule() -> None:
    assert LEVER_PROFILE["temperature"] == 93.0
