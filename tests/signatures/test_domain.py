"""The fault-word table, what a proposal must pass, and the merged order of a shot's checks.

Every expected value is a constant written here or computed from the constructed lever
shot's own constants (`tests/lever_shot.py`); nothing comes from a person's shot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from gaggiclanker.domain.metric_language import CHANNELS, OPS, Compare, Expression
from gaggiclanker.domain.signature import (
    ExpectationInput,
    SignatureRefused,
    build_checks,
    fault_for_failure,
    fault_words,
    validate_expectation,
)
from gaggiclanker.domain.warnings import FAULTS
from tests.lever_shot import LEVER_PROFILE, RAMP_END_G, SOAK_END_G, TARGET_YIELD_G, lever_shot
from tests.signatures.helpers import (
    LONG_NAME_SETS,
    derived_lever,
    long_name_profile,
    long_name_shot,
    shot_data,
    stored_phases,
    turbo_profile,
    turbo_shot,
    universal_warnings,
)

LEVER_PHASES = [p["name"] for p in LEVER_PROFILE["phases"]]


@dataclass
class Exp:
    """A stored expectation, as the evaluation reads one."""

    id: int
    position: int
    tier: str
    kind: str
    phase: str | None = None
    expression: Expression | None = None
    warning_fault: str | None = None
    text: str = ""
    fault: str | None = None
    sentence: str = ""


def _expr(**parts: Any) -> Expression:
    body: dict[str, Any] = {"channel": "cup_weight", "op": "at_end"}
    body.update(parts)
    return Expression.model_validate(body)


def _measure(exp_id: int, tier: str, phase: str | None, **parts: Any) -> Exp:
    expr = _expr(**parts)
    valid = validate_expectation(
        ExpectationInput(
            tier=tier,  # type: ignore[arg-type]
            kind="measure",
            phase=phase,
            expression=expr.model_dump(by_alias=True, exclude_none=True),
        ),
        LEVER_PHASES,
    )
    return Exp(
        exp_id,
        exp_id,
        tier,
        "measure",
        valid.phase,
        valid.expression,
        fault=valid.fault,
        sentence=valid.sentence,
    )


RAMP_CUP = {
    "window": {"phase": "ramp"},
    "relative_to": "target_yield",
    "compare": {"op": "<=", "value": 0.15},
}
SOAK_CUP = {
    "window": {"phase": "soak"},
    "relative_to": "target_yield",
    "compare": {"op": "<=", "value": 0.05},
}


def _reached(exp_id: int, tier: str, phase: str) -> Exp:
    return Exp(
        exp_id, exp_id, tier, "reached", phase, fault="skipped", sentence=f"the {phase} begins"
    )


def _free(exp_id: int, tier: str, text: str, fault: str) -> Exp:
    return Exp(exp_id, exp_id, tier, "free_text", None, text=text, fault=fault, sentence=text)


# ── the fault-word table ─────────────────────────────────────────────

#: (channel, op, whole shot?, word when under its limit, word when over). Everything not listed
#: has no word at all, and a measure that could only fail that way is refused.
MAPPED: list[tuple[str, str, bool, str | None, str | None]] = [
    # the cup in a phase or a span
    *[
        ("cup_weight", op, False, "little yield", "early yield")
        for op in ("mean", "min", "max", "at_start", "at_end", "gained", "change")
    ],
    # the whole shot's yield
    ("cup_weight", "at_end", True, "under target", "over target"),
    ("cup_weight", "max", True, "under target", "over target"),
    # flow
    *[
        (ch, op, whole, "slow flow", "fast flow")
        for ch in ("scale_flow", "puck_flow")
        for op in ("mean", "min", "max", "at_start", "at_end")
        for whole in (False, True)
    ],
    # pressure
    *[
        ("pressure", op, whole, "low pressure", "high pressure")
        for op in ("mean", "min", "max", "at_start", "at_end")
        for whole in (False, True)
    ],
    # temperature
    *[
        ("temperature", op, whole, "temperature", "temperature")
        for op in ("mean", "min", "max", "at_start", "at_end", "change")
        for whole in (False, True)
    ],
    # jitter on any channel, a time under its limit
    *[(ch, "jitter", whole, None, "unstable") for ch in CHANNELS for whole in (False, True)],
    *[(ch, "duration", whole, "cut short", None) for ch in CHANNELS for whole in (False, True)],
    *[(ch, "time_to", whole, "cut short", None) for ch in CHANNELS for whole in (False, True)],
]


def _build(channel: str, op: str, whole: bool) -> Expression | None:
    body: dict[str, Any] = {"channel": channel, "op": op}
    if not whole:
        body["window"] = {"phase": "ramp"}
    if op in ("time_to", "time_above", "time_below"):
        body["threshold"] = 1.0
    if op == "gained" and (channel not in ("cup_weight", "water_pumped") or whole):
        return None  # the language itself refuses it
    try:
        return Expression.model_validate(body)
    except ValueError:
        return None


def test_every_channel_op_and_window_maps_to_exactly_its_word() -> None:
    expected = {(c, o, w): (u, v) for c, o, w, u, v in MAPPED}
    seen = 0
    for channel in CHANNELS:
        for op in OPS:
            for whole in (False, True):
                expr = _build(channel, op, whole)
                if expr is None:
                    continue
                seen += 1
                words = fault_words(expr)
                assert (words.under, words.over) == expected.get(
                    (channel, op, whole), (None, None)
                ), (channel, op, whole)
    assert seen > 250
    # Every mapped row was reachable, so the table lists nothing the language cannot say.
    assert all(_build(c, o, w) is not None for c, o, w, _, _ in MAPPED)


def test_the_words_are_all_from_the_fixed_list() -> None:
    used = {w for *_, u, v in MAPPED for w in (u, v) if w}
    assert used <= set(FAULTS)


@pytest.mark.parametrize(
    ("compare", "value", "word"),
    [
        ({"op": "<=", "value": 0.15}, 0.2, "early yield"),
        ({"op": "<", "value": 0.15}, 0.15, "early yield"),
        ({"op": ">=", "value": 0.5}, 0.4, "little yield"),
        ({"op": ">", "value": 0.5}, 0.5, "little yield"),
        ({"op": "between", "low": 0.3, "high": 0.5}, 0.2, "little yield"),
        ({"op": "between", "low": 0.3, "high": 0.5}, 0.6, "early yield"),
    ],
)
def test_the_word_follows_the_side_it_failed(
    compare: dict[str, Any], value: float, word: str
) -> None:
    expr = _expr(window={"phase": "ramp"}, compare=compare)
    assert fault_for_failure(expr, value) == word


def test_the_scale_flow_and_pressure_and_temperature_words_by_direction() -> None:
    over = Compare(op="<=", value=3.0)
    under = Compare(op=">=", value=1.0)
    for channel, op, low, high in (
        ("scale_flow", "mean", "slow flow", "fast flow"),
        ("puck_flow", "max", "slow flow", "fast flow"),
        ("pressure", "at_end", "low pressure", "high pressure"),
        ("temperature", "mean", "temperature", "temperature"),
    ):
        base = {"channel": channel, "op": op, "window": {"phase": "ramp"}}
        assert (
            fault_for_failure(
                Expression.model_validate({**base, "compare": over.model_dump()}), 4.0
            )
            == high
        )
        assert (
            fault_for_failure(
                Expression.model_validate({**base, "compare": under.model_dump()}), 0.5
            )
            == low
        )


# ── validation ───────────────────────────────────────────────────────


def _measure_input(**parts: Any) -> ExpectationInput:
    return ExpectationInput.model_validate(
        {
            "tier": "critical",
            "kind": "measure",
            "expression": {"channel": "cup_weight", "op": "at_end", **parts},
        }
    )


def test_a_valid_measure_comes_back_with_the_profiles_spelling_and_a_sentence() -> None:
    valid = validate_expectation(
        _measure_input(
            window={"phase": "RAMP"},
            relative_to="target_yield",
            compare={"op": "<=", "value": 0.15},
        ),
        LEVER_PHASES,
    )
    assert valid.phase == "ramp"
    assert valid.fault == "early yield"
    assert valid.sentence == (
        "cup weight at the end of the ramp, as a share of the target yield, at most 0.15"
    )


@pytest.mark.parametrize(
    ("item", "words"),
    [
        # An unparseable expression.
        ({"kind": "measure", "expression": {"channel": "nonsense", "op": "at_end"}}, "channel"),
        ({"kind": "measure", "expression": {"channel": "cup_weight"}}, "op"),
        # No limit to hold it against.
        (
            {
                "kind": "measure",
                "expression": {
                    "channel": "cup_weight",
                    "op": "at_end",
                    "window": {"phase": "ramp"},
                },
            },
            "compare",
        ),
        # An unknown phase, in the window and in a span's anchors.
        (
            {
                "kind": "measure",
                "expression": {
                    "channel": "cup_weight",
                    "op": "at_end",
                    "window": {"phase": "bloom"},
                    "compare": {"op": "<=", "value": 1},
                },
            },
            "no phase 'bloom'",
        ),
        (
            {
                "kind": "measure",
                "expression": {
                    "channel": "pressure",
                    "op": "max",
                    "window": {"from": {"phase_start": "bloom"}, "to": "shot_end"},
                    "compare": {"op": "<=", "value": 9},
                },
            },
            "no phase 'bloom'",
        ),
        # A number, not a name.
        (
            {
                "kind": "measure",
                "expression": {
                    "channel": "cup_weight",
                    "op": "at_end",
                    "window": {"phase_number": 2},
                    "compare": {"op": "<=", "value": 1},
                },
            },
            "name the phase",
        ),
        # A phase that disagrees with the window, and one on a whole-shot measure.
        (
            {
                "kind": "measure",
                "phase": "soak",
                "expression": {
                    "channel": "cup_weight",
                    "op": "at_end",
                    "window": {"phase": "ramp"},
                    "compare": {"op": "<=", "value": 1},
                },
            },
            "disagrees",
        ),
        (
            {
                "kind": "measure",
                "phase": "soak",
                "expression": {
                    "channel": "cup_weight",
                    "op": "at_end",
                    "compare": {"op": "<=", "value": 40},
                },
            },
            "whole shot",
        ),
        # A direction with no word: resistance, a slope, a duration over its limit.
        (
            {
                "kind": "measure",
                "expression": {
                    "channel": "resistance",
                    "op": "mean",
                    "window": {"phase": "ramp"},
                    "compare": {"op": "<=", "value": 1},
                },
            },
            "no fault word",
        ),
        (
            {
                "kind": "measure",
                "expression": {
                    "channel": "scale_flow",
                    "op": "mean",
                    "window": {"phase": "ramp"},
                    "compare": {"op": "between", "low": 1, "high": 3},
                },
                "text": "x",
            },
            "takes no text",
        ),
        (
            {
                "kind": "measure",
                "expression": {
                    "channel": "cup_weight",
                    "op": "duration",
                    "window": {"phase": "ramp"},
                    "compare": {"op": "<=", "value": 10},
                },
            },
            "over",
        ),
        # `reached` on a phase the profile does not have, and with no phase.
        ({"kind": "reached", "phase": "bloom"}, "no phase 'bloom'"),
        ({"kind": "reached"}, "names the phase"),
        # A word outside the list, and a missing one.
        ({"kind": "free_text", "text": "pressure falls", "fault": "weird"}, "not one of"),
        ({"kind": "free_text", "text": "pressure falls"}, "not one of"),
        ({"kind": "free_text", "text": "   ", "fault": "unstable"}, "needs its text"),
        # expects_warning names a universal warning only, and a whole-shot one no phase.
        ({"kind": "expects_warning", "warning": "early yield"}, "not a universal warning"),
        ({"kind": "expects_warning", "warning": "over target", "phase": "ramp"}, "whole shot"),
        # A field the kind does not take.
        ({"kind": "reached", "phase": "ramp", "fault": "skipped"}, "takes no fault"),
    ],
)
def test_a_proposal_is_refused_with_the_reason(item: dict[str, Any], words: str) -> None:
    with pytest.raises(SignatureRefused) as refused:
        validate_expectation(
            ExpectationInput.model_validate({"tier": "critical", **item}), LEVER_PHASES
        )
    assert words in str(refused.value)


def test_a_good_example_of_each_other_kind_passes() -> None:
    reached = validate_expectation(
        ExpectationInput(tier="critical", kind="reached", phase="Decline"), LEVER_PHASES
    )
    assert (reached.phase, reached.fault, reached.sentence) == (
        "decline",
        "skipped",
        "the decline begins",
    )
    free = validate_expectation(
        ExpectationInput(
            tier="important",
            kind="free_text",
            text="pressure and flow fall together",
            fault="unstable",
        ),
        LEVER_PHASES,
    )
    assert free.sentence == "pressure and flow fall together"
    warns = validate_expectation(
        ExpectationInput(tier="context", kind="expects_warning", warning="fast flow", phase="ramp"),
        LEVER_PHASES,
    )
    assert warns.warning_fault == "fast flow"


# ── a shot's checks ──────────────────────────────────────────────────


def _lever_checks(expectations: list[Exp], override: tuple[int, Compare] | None = None):  # type: ignore[no-untyped-def]
    derived = derived_lever()
    return build_checks(
        warnings=universal_warnings(derived),
        expectations=expectations,
        override=override,
        data=shot_data(),
        phases=stored_phases(derived),
        duration_s=derived.shot.duration_ms / 1000,
    )


def _lever_signature() -> list[Exp]:
    return [
        _reached(1, "critical", "decline"),
        _measure(2, "critical", "ramp", **RAMP_CUP),
        _measure(3, "important", "soak", **SOAK_CUP),
        _measure(
            4,
            "context",
            None,
            channel="temperature",
            op="mean",
            compare={"op": "between", "low": 80, "high": 100},
        ),
        _free(5, "context", "pressure and flow fall together through the decline", "unstable"),
    ]


def test_the_lever_shot_reads_red_with_the_early_yield_first() -> None:
    result = _lever_checks(_lever_signature())

    assert [(c.badge if c.in_badge else None, c.color) for c in result.checks if c.in_badge] == [
        ("ramp: early yield", "red"),
        ("decline: skipped", "red"),
        ("soak: early yield", "amber"),
        ("ramp: fast flow", "amber"),
        ("Shot: over target", "amber"),
    ]
    first = result.checks[0]
    assert first.value == pytest.approx(RAMP_END_G / TARGET_YIELD_G, abs=1e-3)
    assert first.tier == "critical"
    assert result.state.text == "confirmed, 5 expectations"


def test_the_merged_order_is_red_amber_expected_held_context_free_text() -> None:
    held = _measure(
        6,
        "important",
        "ramp",
        channel="temperature",
        op="mean",
        window={"phase": "ramp"},
        compare={"op": "between", "low": 80, "high": 100},
    )
    unmeasured = _measure(7, "important", "decline", **{**SOAK_CUP, "window": {"phase": "decline"}})
    result = _lever_checks([*_lever_signature(), held, unmeasured])
    ranks = [c.rank for c in result.checks]
    assert ranks == sorted(ranks)
    assert {c.rank for c in result.checks} == {0, 1, 2, 4, 5, 6, 7}
    by_status = {c.expectation_id: c for c in result.checks if c.expectation_id}
    # Not measured is listed with its reason and is never held or failed.
    assert by_status[7].status == "unmeasured"
    assert by_status[7].held is None and by_status[7].value is None
    assert "did not reach" in (by_status[7].absent or "")
    assert by_status[4].status == "held" and by_status[4].rank == 6  # a context expectation
    assert by_status[5].status == "unchecked" and by_status[5].rank == 7


def test_within_a_group_the_order_is_by_time_with_the_whole_shot_last() -> None:
    # Three critical failures: one in the soak, one in the ramp, one over the whole shot.
    whole = _measure(
        1,
        "critical",
        None,
        window={},
        relative_to="target_yield",
        compare={"op": "<=", "value": 1.0},
    )
    soak = _measure(2, "critical", "soak", **SOAK_CUP)
    ramp = _measure(3, "critical", "ramp", **RAMP_CUP)
    result = _lever_checks([whole, ramp, soak])
    reds = [c for c in result.checks if c.rank == 0]
    assert [c.badge for c in reds] == [
        "soak: early yield",
        "ramp: early yield",
        "Shot: over target",
    ]
    assert [c.at_s for c in reds[:2]] == sorted(c.at_s for c in reds[:2])
    assert reds[-1].shot_wide


def test_a_failed_expectation_supersedes_the_universal_warning_that_says_the_same_thing() -> None:
    result = _lever_checks(_lever_signature())
    badges = [c.badge for c in result.checks if c.in_badge]
    # `decline: skipped` and `Shot: over target` are said once each: the first by the red
    # `reached`, the second still by the warning (nothing in the signature says it).
    assert badges.count("decline: skipped") == 1
    skipped = next(c for c in result.checks if c.badge == "decline: skipped")
    assert skipped.kind == "reached" and skipped.color == "red"


def test_a_shot_with_no_confirmed_signature_reads_as_its_warnings_and_says_so() -> None:
    result = _lever_checks([])
    assert [(c.badge, c.color) for c in result.checks] == [
        ("ramp: fast flow", "amber"),
        ("decline: skipped", "amber"),
        ("Shot: over target", "amber"),
    ]
    assert result.state.text == "read without a signature"
    assert not result.state.read_with_signature


def test_an_override_changes_only_that_expectations_limit() -> None:
    expectations = _lever_signature()
    loose = Compare(op="<=", value=1.3)
    result = _lever_checks(expectations, override=(2, loose))
    ramp = next(c for c in result.checks if c.expectation_id == 2)
    assert ramp.status == "held"
    assert "this Set version's limit; the profile's is at most 0.15" in ramp.detail
    # The soak expectation's own limit is untouched, and so is every other result.
    soak = next(c for c in result.checks if c.expectation_id == 3)
    assert soak.status == "failed"
    # An override of an expectation this signature does not hold changes nothing.
    other = _lever_checks(expectations, override=(99, loose))
    assert [c.status for c in other.checks] == [
        c.status for c in _lever_checks(expectations).checks
    ]


def test_a_missing_log_is_not_measured_never_held() -> None:
    derived = derived_lever()
    result = build_checks(
        warnings=[],
        expectations=_lever_signature(),
        override=None,
        data=None,
        phases=stored_phases(derived),
        duration_s=33.0,
    )
    statuses = {c.expectation_id: c.status for c in result.checks}
    assert statuses == {
        1: "unmeasured",
        2: "unmeasured",
        3: "unmeasured",
        4: "unmeasured",
        5: "unchecked",
    }


# ── expects_warning: the turbo ───────────────────────────────────────


def _turbo_checks(expectations: list[Exp]):  # type: ignore[no-untyped-def]
    derived = derived_lever(turbo_shot(), profile=turbo_profile())
    return build_checks(
        warnings=universal_warnings(derived, target=42.0),
        expectations=expectations,
        override=None,
        data=shot_data(turbo_shot(), profile=turbo_profile(), target=42.0),
        phases=stored_phases(derived),
        duration_s=derived.shot.duration_ms / 1000,
    )


def test_an_expected_warning_is_grey_and_a_turbo_shows_no_amber() -> None:
    without = _turbo_checks([])
    assert [(c.badge, c.color) for c in without.checks] == [("main: fast flow", "amber")]

    expecting = Exp(
        1,
        1,
        "important",
        "expects_warning",
        "main",
        warning_fault="fast flow",
        fault="fast flow",
        sentence="fast flow is expected in the main: it is part of the design",
    )
    result = _turbo_checks([expecting])
    assert [(c.badge, c.color, c.status) for c in result.checks] == [
        ("main: fast flow", "grey", "expected")
    ]
    assert not [c for c in result.checks if c.color == "amber"]
    assert result.checks[0].expectation_id == 1


def test_an_expectation_of_a_warning_in_another_phase_does_not_excuse_it() -> None:
    elsewhere = Exp(
        1,
        1,
        "important",
        "expects_warning",
        "fill",
        warning_fault="fast flow",
        fault="fast flow",
        sentence="fast flow is expected in the fill",
    )
    result = _turbo_checks([elsewhere])
    assert [(c.badge, c.color) for c in result.checks if c.in_badge] == [
        ("main: fast flow", "amber")
    ]
    # ... and the expectation, which was not needed, is listed as held and says so.
    held = next(c for c in result.checks if c.expectation_id == 1)
    assert held.status == "held" and "not raised" in held.detail


def test_the_lever_signature_applied_to_nothing_reads_the_same_lever_twice() -> None:
    assert [c.badge for c in _lever_checks(_lever_signature()).checks] == [
        c.badge for c in _lever_checks(_lever_signature()).checks
    ]


def test_the_lever_soak_cup_constant_is_what_the_checks_measure() -> None:
    result = _lever_checks([_measure(3, "important", "soak", **SOAK_CUP)])
    soak = next(c for c in result.checks if c.expectation_id == 3)
    assert soak.value == pytest.approx(SOAK_END_G / TARGET_YIELD_G, abs=1e-3)
    assert lever_shot() is not None


# ── a failed context expectation never changes the badge ─────────────


def test_a_failed_context_expectation_supersedes_no_warning() -> None:
    reached = Exp(
        1, 1, "context", "reached", "decline", fault="skipped", sentence="the decline begins"
    )
    whole_cup = _measure(
        2,
        "context",
        None,
        window={},
        relative_to="target_yield",
        compare={"op": "<=", "value": 1.0},
    )
    result = _lever_checks([reached, whole_cup])

    badge = [(c.badge, c.color) for c in result.checks if c.in_badge]
    assert badge == [
        ("ramp: fast flow", "amber"),
        ("decline: skipped", "amber"),
        ("Shot: over target", "amber"),
    ]
    # The context results themselves are listed, failed, and outside the badge.
    context = [c for c in result.checks if c.tier == "context"]
    assert [(c.status, c.rank, c.color) for c in context] == [("failed", 6, None)] * 2
    # An important failure of the same fact does supersede the warning.
    important = _lever_checks(
        [Exp(1, 1, "important", "reached", "decline", fault="skipped", sentence="x")]
    )
    first = next(c for c in important.checks if c.in_badge)
    assert (first.badge, first.color) == ("decline: skipped", "amber")
    assert next(c for c in important.checks if c.badge == "decline: skipped").kind == "reached"


# ── what a check serves ──────────────────────────────────────────────


def test_a_reached_check_serves_no_number() -> None:
    result = _lever_checks([_reached(1, "critical", "decline"), _reached(2, "critical", "ramp")])
    by_id = {c.expectation_id: c for c in result.checks if c.expectation_id}
    assert (by_id[1].status, by_id[1].held, by_id[1].value) == ("failed", False, None)
    # The last phase the shot reached has begun.
    assert (by_id[2].status, by_id[2].held, by_id[2].value) == ("held", True, None)


def test_a_check_serves_its_effective_limit_and_reads_a_share_as_a_percentage() -> None:
    expectations = _lever_signature()
    plain = next(c for c in _lever_checks(expectations).checks if c.expectation_id == 2)
    assert plain.compare == Compare(op="<=", value=0.15)
    assert (plain.relative_to, plain.limit_text) == ("target_yield", "at most 15 % of target")
    assert plain.detail.endswith("; this shot: 117.2 % of target.")

    loose = next(
        c
        for c in _lever_checks(expectations, override=(2, Compare(op="<=", value=1.3))).checks
        if c.expectation_id == 2
    )
    assert loose.compare == Compare(op="<=", value=1.3)
    assert loose.limit_text == "at most 130 % of target"
    assert "(this Set version's limit; the profile's is at most 0.15)" in loose.detail


def test_a_limit_that_is_not_a_share_names_its_unit() -> None:
    flow = _measure(
        1,
        "important",
        "ramp",
        channel="scale_flow",
        op="max",
        window={"phase": "ramp"},
        compare={"op": "<=", "value": 3},
    )
    check = next(c for c in _lever_checks([flow]).checks if c.expectation_id == 1)
    assert check.limit_text == "at most 3 g/s" and check.relative_to is None
    assert check.detail.endswith("this shot: 4 g/s.")


# ── expects_warning skipped names any skipped phase ──────────────────


def test_an_expectation_of_a_skipped_phase_matches_whichever_phase_was_skipped() -> None:
    from gaggiclanker.domain.warnings import ShotWarning

    both = ShotWarning(
        phase="decline",
        fault="skipped",
        severity="amber",
        detail="d",
        phase_number=3,
        at_s=33.0,
        phases=("decline", "finish"),
    )
    for phase, expected in (("finish", True), ("decline", True), ("soak", False), (None, True)):
        exp = Exp(
            1,
            1,
            "important",
            "expects_warning",
            phase,
            warning_fault="skipped",
            fault="skipped",
            sentence="skipped is expected",
        )
        result = build_checks(warnings=[both], expectations=[exp], override=None, data=None)
        assert [c.color for c in result.checks if c.kind == "warning"] == [
            "grey" if expected else "amber"
        ], phase


# ── one definition of a phase began ──────────────────────────────────


def test_a_transition_with_no_sample_after_it_is_skipped_on_both_readings() -> None:
    import dataclasses

    from gaggiclanker.domain.models import PhaseTransition

    slog = lever_shot()
    last = len(slog.samples)
    transitions = [
        *slog.transitions,
        PhaseTransition(
            sample_index=last, phase_number=3, transition_reason=1, phase_name="decline"
        ),
    ]
    slog = dataclasses.replace(
        slog, header=slog.header.model_copy(update={"transitions": transitions})
    )
    derived = derived_lever(slog)
    warnings = universal_warnings(derived)
    data = shot_data(slog)
    result = build_checks(
        warnings=warnings,
        expectations=[_reached(1, "critical", "decline")],
        override=None,
        data=data,
        phases=stored_phases(derived),
        duration_s=derived.shot.duration_ms / 1000,
    )
    # The universal warning says decline was skipped, and so does the expectation.
    assert any(w.fault == "skipped" and w.phase == "decline" for w in warnings)
    reached = next(c for c in result.checks if c.kind == "reached")
    assert (reached.status, reached.held) == ("failed", False)


# ── a profile phase name longer than the log keeps ───────────────────


@pytest.mark.parametrize("names", LONG_NAME_SETS)
def test_a_long_phase_name_is_matched_in_a_signature(names: tuple[str, ...]) -> None:
    slog = long_name_shot(names)
    profile = long_name_profile(names)
    derived = derived_lever(slog, profile=profile)
    expectations = [
        Exp(1, 1, "critical", "reached", names[1], fault="skipped", sentence="x"),
        Exp(
            2,
            2,
            "important",
            "expects_warning",
            names[2],
            warning_fault="fast flow",
            fault="fast flow",
            sentence="fast flow is expected",
        ),
        _long_cup(3, names[1]),
    ]
    result = build_checks(
        warnings=universal_warnings(derived, target=42.0),
        expectations=expectations,
        override=None,
        data=shot_data(slog, profile=profile, target=42.0),
        phases=stored_phases(derived),
        duration_s=derived.shot.duration_ms / 1000,
    )
    assert {(c.expectation_id, c.status) for c in result.checks} == {
        (1, "held"),
        (2, "expected"),
        (3, "held"),
    }
    assert not [c for c in result.checks if c.color in ("red", "amber")]


def _long_cup(exp_id: int, phase: str) -> Exp:
    expr = _expr(window={"phase": phase}, compare={"op": "<=", "value": 100})
    return Exp(
        exp_id, exp_id, "important", "measure", phase, expr, fault="early yield", sentence="c"
    )


def test_a_profile_with_phases_a_log_cannot_tell_apart_is_refused() -> None:
    names = ["a" * 24 + "one", "a" * 24 + "two", "other"]
    with pytest.raises(SignatureRefused, match="same in their first 24 bytes"):
        validate_expectation(
            ExpectationInput(tier="critical", kind="reached", phase="other"), names
        )


def test_an_override_changes_the_limit_and_keeps_the_window_the_expression_names() -> None:
    expectations = _lever_signature()
    plain = next(c for c in _lever_checks(expectations).checks if c.expectation_id == 2)
    loose = next(
        c
        for c in _lever_checks(expectations, override=(2, Compare(op="<=", value=1.3))).checks
        if c.expectation_id == 2
    )
    # The sentence is the profile's own up to the limit: same channel, window and share.
    assert plain.sentence.removesuffix("at most 0.15") == loose.sentence.removesuffix("at most 1.3")
    assert loose.phase == "ramp" and loose.value == plain.value
    assert (loose.status, plain.status) == ("held", "failed")


def test_a_phase_with_a_leading_space_is_proposed_by_its_clean_name() -> None:
    names = [" Ramp up to the full nine bar", "Pre  infusion with a long soak", "decline"]
    valid = validate_expectation(
        ExpectationInput(tier="critical", kind="reached", phase="Ramp up to the full nine bar"),
        names,
    )
    assert valid.phase == " Ramp up to the full nine bar"
    again = validate_expectation(
        ExpectationInput(tier="critical", kind="reached", phase="pre infusion with a long soak"),
        names,
    )
    assert again.phase == "Pre  infusion with a long soak"
