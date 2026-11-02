"""The two places an agent is told the shape of an expression both say the setpoint offset."""

from __future__ import annotations

from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.review.context import _render_language
from gaggiclanker.tools.builtin import ProposeSignatureInput


def test_the_review_language_says_what_the_offset_is_and_to_prefer_it() -> None:
    text = _render_language()
    assert (
        '`offset_from`: "profile_temperature" (temperature channels, over a level such as '
        "min, max or mean) turns the value into its distance from the shot's own profile "
        "temperature in °C, so -2 means 2 °C below the setpoint; prefer it to an absolute "
        "temperature, which stops fitting when the profile's temperature changes. It cannot "
        "be combined with `relative_to`."
    ) in text


def test_the_proposal_tool_and_its_expression_say_to_prefer_it_for_temperature() -> None:
    schema = ProposeSignatureInput.model_json_schema()
    described = schema["properties"]["expectations"]["description"]
    assert (
        'For a temperature, prefer "offset_from": "profile_temperature" (the value is then its '
        "distance from the profile's own temperature, so a floor of -2 holds when the setpoint "
        "moves) to an absolute limit"
    ) in described
    expression = ExpectationInput.model_json_schema()["properties"]["expression"]["description"]
    assert 'prefer offset_from: "profile_temperature"' in expression
