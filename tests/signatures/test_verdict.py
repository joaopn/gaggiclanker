"""What the Curve check cell says of a shot: its entries, a pass, or that nothing was checked.

Every shot is the constructed lever (or turbo) shot, built from a real fixture. A shot that
raised no warning is the same shot read with an empty list of universal warnings.
"""

from __future__ import annotations

from gaggiclanker.domain.signature import Check, ShotChecks, build_checks, check_key
from tests.signatures.helpers import (
    derived_lever,
    shot_data,
    stored_phases,
    turbo_profile,
    turbo_shot,
    universal_warnings,
)
from tests.signatures.test_domain import (
    SOAK_CUP,
    Exp,
    _free,
    _measure,
    _reached,
)

#: The mean temperature of the lever shot is inside this range.
TEMPERATURE_HELD = {
    "channel": "temperature",
    "op": "mean",
    "compare": {"op": "between", "low": 80, "high": 100},
}


def _held(exp_id: int, tier: str = "important") -> Exp:
    return _measure(exp_id, tier, None, **TEMPERATURE_HELD)


def _quiet_lever(expectations: list[Exp]) -> ShotChecks:
    """The lever shot as a shot that raised no universal warning."""
    derived = derived_lever()
    return build_checks(
        warnings=[],
        expectations=expectations,
        override=None,
        data=shot_data(),
        phases=stored_phases(derived),
        duration_s=derived.shot.duration_ms / 1000,
    )


def _lever(expectations: list[Exp]) -> ShotChecks:
    derived = derived_lever()
    return build_checks(
        warnings=universal_warnings(derived),
        expectations=expectations,
        override=None,
        data=shot_data(),
        phases=stored_phases(derived),
        duration_s=derived.shot.duration_ms / 1000,
    )


def test_no_signature_and_no_warning_is_unchecked() -> None:
    checks = _quiet_lever([])
    assert checks.checks == () and not checks.state.read_with_signature
    assert checks.verdict == "unchecked"


def test_a_signature_in_force_with_every_expectation_held_is_a_pass() -> None:
    checks = _quiet_lever([_held(1), _reached(2, "critical", "ramp")])
    assert [c.status for c in checks.checks] == ["held", "held"]
    assert checks.verdict == "pass"


def test_a_warning_nothing_marks_as_expected_keeps_the_entries() -> None:
    checks = _lever([_held(1)])
    assert checks.badge_entries and checks.verdict == "entries"
    # Without a signature the same warnings are the same entries.
    assert _lever([]).verdict == "entries"


def test_an_expected_grey_warning_is_an_entry_and_never_a_pass() -> None:
    expecting = Exp(
        2,
        2,
        "important",
        "expects_warning",
        "main",
        warning_fault="fast flow",
        fault="fast flow",
        sentence="fast flow is expected in the main: it is part of the design",
    )
    derived = derived_lever(turbo_shot(), profile=turbo_profile())
    checks = build_checks(
        warnings=universal_warnings(derived, target=42.0),
        expectations=[_held(1), expecting],
        override=None,
        data=shot_data(turbo_shot(), profile=turbo_profile(), target=42.0),
        phases=stored_phases(derived),
        duration_s=derived.shot.duration_ms / 1000,
    )
    assert [c.status for c in checks.badge_entries] == ["expected"]
    assert any(c.status == "held" for c in checks.checks)
    assert checks.verdict == "entries"


def test_a_signature_whose_checks_measured_nothing_is_unchecked() -> None:
    unmeasured = _measure(1, "important", "decline", **{**SOAK_CUP, "window": {"phase": "decline"}})
    free = _free(2, "context", "pressure and flow fall together", "unstable")
    checks = _quiet_lever([unmeasured, free])
    assert checks.state.read_with_signature
    assert sorted(c.status for c in checks.checks) == ["unchecked", "unmeasured"]
    assert checks.verdict == "unchecked"


def test_one_held_beside_unmeasured_and_free_text_is_a_pass() -> None:
    unmeasured = _measure(1, "important", "decline", **{**SOAK_CUP, "window": {"phase": "decline"}})
    free = _free(2, "context", "pressure and flow fall together", "unstable")
    assert _quiet_lever([unmeasured, free, _held(3)]).verdict == "pass"


def test_a_failed_context_expectation_does_not_stop_a_pass() -> None:
    failing = _reached(2, "context", "decline")
    # The shot is a lever: the decline never begins.
    checks = _quiet_lever([_held(1), failing])
    assert {c.status for c in checks.checks} == {"held", "failed"}
    assert not checks.badge_entries
    assert checks.verdict == "pass"


def test_a_failed_context_expectation_alone_is_a_pass() -> None:
    # It was measured, and a failed context check is context, not a fault: nothing in the badge.
    checks = _quiet_lever([_reached(1, "context", "decline")])
    assert [c.status for c in checks.checks] == ["failed"]
    assert not checks.badge_entries
    assert checks.verdict == "pass"


def test_a_failed_important_expectation_is_entries_whatever_else_held() -> None:
    checks = _quiet_lever([_held(1), _reached(2, "important", "decline")])
    assert [c.badge for c in checks.badge_entries] == ["decline: skipped"]
    assert checks.verdict == "entries"


def test_a_shot_without_checks_at_all_is_unchecked() -> None:
    # A quarantined shot has no checks and no signature state.
    assert ShotChecks().verdict == "unchecked"


def test_the_sort_key_orders_entries_then_pass_then_unchecked() -> None:
    entries = _lever([])
    passing = _quiet_lever([_held(1)])
    unchecked = _quiet_lever([])
    keys = [check_key(entries), check_key(passing), check_key(unchecked)]
    assert keys == sorted(keys)
    assert len(set(keys)) == 3
    assert isinstance(entries.checks[0], Check)
