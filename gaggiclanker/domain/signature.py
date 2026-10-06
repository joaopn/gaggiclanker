"""What a profile is for, and the checks a shot gets from it.

A **signature** is a list of expectations of one profile version. Each has a tier
(``critical``, ``important`` or ``context``; no weights and no sum), a phase the
profile names (or none, for the whole shot) and one of four kinds:

* ``measure``: an expression of the metric language with a comparison, checked
  deterministically on every shot;
* ``reached``: the phase must begin (the language cannot say "a phase that never
  began": that is an absence, not a window statistic);
* ``expects_warning``: a universal warning is part of the design (``fast flow``
  on a turbo's main phase), so on a shot that raises it the warning is shown as
  expected and the expectation holds;
* ``free_text``: what no expression says; never checked here (the per-shot
  reading is what reads it), shown apart as "checked by the reading".

This module is pure. It holds the fault-word table (what a measure's channel and
the direction it failed *mean*), the validation a proposal passes, and the one
function that turns a shot's facts, its universal warnings and the **confirmed**
expectations of its profile version into one ordered list of checks. It is told
nothing about unconfirmed expectations and so cannot let one teach anything: the
callers hand it the confirmed ones only (``SignatureRepository.confirmed_for_versions``
is the one reader of that).

**The order of the list** is the order a person reads and an agent is told, merged
from the signature's results and the warnings:

0. failed ``critical`` expectations, red;
1. failed ``important`` expectations, amber;
2. universal warnings nothing marked expected, amber;
3. expected warnings, grey;
4. a measure that could not be measured (an absent value, with its reason: never
   held and never failed);
5. held expectations;
6. ``context`` expectations, whatever they came to;
7. ``free_text`` ones, unchecked.

Within a group by where it falls in the shot, a whole-shot one last. The badge is
groups 0 to 3.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gaggiclanker.domain.metric_language import (
    Compare,
    Expression,
    PhaseEndAnchor,
    PhaseStartAnchor,
    Result,
    ShotData,
    compare_words,
    evaluate,
    render,
)
from gaggiclanker.domain.phase_metrics import phase_began
from gaggiclanker.domain.phase_names import clashing_names, phase_key, same_phase
from gaggiclanker.domain.warnings import FAULTS, SHOT, Fault, ShotWarning

__all__ = [
    "BADGE_RANKS",
    "FREE_TEXT_MAX",
    "UNIVERSAL_WARNING_FAULTS",
    "Check",
    "ExpectationInput",
    "ExpectationLike",
    "FaultWords",
    "ShotChecks",
    "SignatureRefused",
    "SignatureState",
    "ValidExpectation",
    "build_checks",
    "expectation_line",
    "fault_for_failure",
    "fault_words",
    "phase_key",
    "validate_compare",
    "validate_expectation",
]

#: The warnings that need no knowledge of the profile (:mod:`gaggiclanker.domain.warnings`):
#: the ones an ``expects_warning`` expectation can name.
UNIVERSAL_WARNING_FAULTS: tuple[str, ...] = ("fast flow", "skipped", "over target", "under target")
#: The two that are about the whole shot, so no phase is named for them.
_SHOT_WIDE_WARNINGS = frozenset({"over target", "under target"})

#: How long a free-text expectation may be.
FREE_TEXT_MAX = 300

#: The groups of the ordered list the badge is made of.
BADGE_RANKS = frozenset({0, 1, 2, 3})

# ── the fault words ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FaultWords:
    """The word a measure fails with when it comes out under its limit, and over it.

    ``None`` is a direction the language has no word for.
    """

    under: Fault | None
    over: Fault | None


_NONE = FaultWords(None, None)

#: The ops that read one number out of a window: a level, not a rate or a time.
_LEVEL_OPS = frozenset({"mean", "min", "max", "at_start", "at_end"})


def fault_words(expr: Expression) -> FaultWords:
    """What a measure's channel, window and op *mean* when it fails.

    * ``jitter`` over its limit is ``unstable``;
    * a ``duration`` or a ``time_to`` under its limit is ``cut short``;
    * the cup: over its limit is ``early yield`` and under is ``little yield`` in a phase
      or a span, and ``over target`` / ``under target`` for the whole shot's yield;
    * the scale's or the puck's flow: ``fast flow`` over, ``slow flow`` under;
    * pressure: ``high pressure`` over, ``low pressure`` under;
    * temperature: ``temperature`` either way.

    Everything else (a slope, an integral, the machine's commanded values, water pumped,
    resistance) has no word, and an expression whose failing direction has none is refused
    when it is proposed: a result must be a fault from the fixed list, never a word the
    model made up.
    """
    op, channel = expr.op, expr.channel
    if op == "jitter":
        return FaultWords(None, "unstable")
    if op in ("duration", "time_to"):
        return FaultWords("cut short", None)
    whole_shot = not expr.window.is_phase and expr.window.start is None
    if channel == "cup_weight":
        if whole_shot:
            return FaultWords("under target", "over target") if op in ("at_end", "max") else _NONE
        if op in _LEVEL_OPS or op in ("gained", "change"):
            return FaultWords("little yield", "early yield")
        return _NONE
    if op not in _LEVEL_OPS:
        if channel == "temperature" and op == "change":
            return FaultWords("temperature", "temperature")
        return _NONE
    if channel in ("scale_flow", "puck_flow"):
        return FaultWords("slow flow", "fast flow")
    if channel == "pressure":
        return FaultWords("low pressure", "high pressure")
    if channel == "temperature":
        return FaultWords("temperature", "temperature")
    return _NONE


def _failing_directions(compare: Compare) -> tuple[bool, bool]:
    """Which ways a comparison can fail: (under its limit, over its limit)."""
    if compare.op in ("<", "<="):
        return (False, True)
    if compare.op in (">", ">="):
        return (True, False)
    return (True, True)


def fault_for_failure(expr: Expression, value: float) -> Fault | None:
    """The fault word of a measure that failed with ``value``."""
    assert expr.compare is not None
    words = fault_words(expr)
    under, over = _failing_directions(expr.compare)
    if under and over:
        assert expr.compare.low is not None
        return words.under if value < expr.compare.low else words.over
    return words.under if under else words.over


def _single_fault(expr: Expression) -> Fault | None:
    """The one word a one-sided measure can fail with, or ``None`` when it depends on the side."""
    assert expr.compare is not None
    under, over = _failing_directions(expr.compare)
    words = fault_words(expr)
    if under and over:
        return words.under if words.under == words.over else None
    return words.under if under else words.over


# ── the proposal ────────────────────────────────────────────────────


class SignatureRefused(ValueError):
    """A proposed expectation that is not valid, in words the proposer can act on."""


class ExpectationInput(BaseModel):
    """One expectation as an agent writes it. Validated into a :class:`ValidExpectation`."""

    model_config = ConfigDict(extra="forbid")

    tier: Literal["critical", "important", "context"] = Field(
        description="How much it matters: critical, important or context."
    )
    kind: Literal["measure", "reached", "expects_warning", "free_text"] = Field(
        description=(
            "measure: an expression of the metric language with a compare; reached: the "
            "phase must begin; expects_warning: a universal warning is part of this "
            "profile's design; free_text: what no expression says."
        )
    )
    phase: str | None = Field(
        default=None,
        max_length=80,
        description=(
            "The profile's phase name, exactly as the profile spells it; leave it out for the "
            "whole shot. A measure over one phase takes it from its window."
        ),
    )
    expression: dict[str, Any] | None = Field(
        default=None,
        description=(
            "For a measure only: an expression {channel, op, window, relative_to, compare}. "
            "It must carry a compare, and its failing direction must map to a fault word."
        ),
    )
    warning: str | None = Field(
        default=None,
        description=("For expects_warning only: fast flow, skipped, over target or under target."),
    )
    text: str = Field(
        default="",
        max_length=FREE_TEXT_MAX,
        description="For free_text only: the expectation in one sentence.",
    )
    fault: str | None = Field(
        default=None,
        description=(
            f"For free_text only: the fault word it fails with, one of {', '.join(FAULTS)}."
        ),
    )


@dataclass(frozen=True, slots=True)
class ValidExpectation:
    """What validation produced: the canonical phase spelling, the expression and the words."""

    tier: str
    kind: str
    phase: str | None
    expression: Expression | None
    warning_fault: str | None
    text: str
    fault: str | None
    sentence: str


_SPACES = re.compile(r"\s+")


def _canonical_phase(name: str, profile_phases: Sequence[str], where: str) -> str:
    # The name as a person or a model writes it (spaces and case folded) first, so a profile
    # phase that starts with a space is found by its clean name; then by what a log would hold.
    folded = " ".join(name.split()).casefold()
    for phase in profile_phases:
        if " ".join(phase.split()).casefold() == folded:
            return phase
    wanted = phase_key(name)
    for phase in profile_phases:
        if phase_key(phase) == wanted:
            return phase
    known = ", ".join(repr(p) for p in profile_phases) or "none"
    raise SignatureRefused(
        f"{where}: the profile has no phase {name!r}. Its phases are {known}; name one of "
        "them exactly."
    )


def _window_phases(expr: Expression) -> list[str]:
    names: list[str] = []
    window = expr.window
    if window.phase is not None:
        names.append(window.phase)
    for anchor in (window.start, window.end):
        if isinstance(anchor, PhaseStartAnchor):
            names.append(anchor.phase_start)
        elif isinstance(anchor, PhaseEndAnchor):
            names.append(anchor.phase_end)
    return names


def _spelled(expr: Expression, profile_phases: Sequence[str]) -> Expression:
    """The expression with every phase it names spelled as the profile spells it, so the
    stored form and its sentence read the same however the proposer cased a name."""
    window = expr.window
    updates: dict[str, Any] = {}
    if window.phase is not None:
        updates["phase"] = _canonical_phase(window.phase, profile_phases, "window")
    for side in ("start", "end"):
        anchor = getattr(window, side)
        if isinstance(anchor, PhaseStartAnchor):
            updates[side] = anchor.model_copy(
                update={
                    "phase_start": _canonical_phase(anchor.phase_start, profile_phases, "window")
                }
            )
        elif isinstance(anchor, PhaseEndAnchor):
            updates[side] = anchor.model_copy(
                update={"phase_end": _canonical_phase(anchor.phase_end, profile_phases, "window")}
            )
    if not updates:
        return expr
    return expr.model_copy(update={"window": window.model_copy(update=updates)})


def _validation_lines(exc: ValidationError) -> str:
    # Field and problem, never the value: the same house rule as every other refusal.
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'expression'}: {error['msg']}"
        for error in exc.errors()
    )


def validate_compare(raw: Mapping[str, Any]) -> Compare:
    """A comparison written by an agent, or a refusal in words."""
    try:
        return Compare.model_validate(dict(raw))
    except ValidationError as exc:
        raise SignatureRefused(f"compare is not valid: {_validation_lines(exc)}") from None


def validate_expectation(
    item: ExpectationInput, profile_phases: Sequence[str], *, where: str = "expectation"
) -> ValidExpectation:
    """Check one proposed expectation against the profile version it is for.

    Every ``measure`` must parse and carry a comparison, every phase it names must be one
    the profile has (spelled the profile's way afterwards), every fault word must be in
    the fixed list, and a measure whose failing direction has no word is refused. The
    refusal names what was wrong, so the proposer can try again.
    """
    stray = [
        name
        for name, present in (
            ("expression", item.expression is not None and item.kind != "measure"),
            ("warning", item.warning is not None and item.kind != "expects_warning"),
            ("text", bool(item.text.strip()) and item.kind != "free_text"),
            ("fault", item.fault is not None and item.kind != "free_text"),
        )
        if present
    ]
    if stray:
        raise SignatureRefused(f"{where}: a {item.kind} takes no {', '.join(stray)}.")
    clashes = clashing_names(profile_phases)
    if clashes:
        first, second = clashes[0]
        raise SignatureRefused(
            f"{where}: the profile's phases {first!r} and {second!r} are the same in their first "
            "24 bytes, which is all the machine logs of a phase's name, so a shot's log cannot "
            "tell them apart. Rename one of them in the profile first."
        )
    phase = (
        _canonical_phase(item.phase, profile_phases, where)
        if item.phase is not None and item.phase.strip()
        else None
    )

    if item.kind == "reached":
        if phase is None:
            raise SignatureRefused(
                f"{where}: a reached expectation names the phase that must begin."
            )
        return ValidExpectation(
            tier=item.tier,
            kind="reached",
            phase=phase,
            expression=None,
            warning_fault=None,
            text="",
            fault="skipped",
            sentence=f"the {phase} begins",
        )

    if item.kind == "free_text":
        text = _SPACES.sub(" ", item.text).strip()
        if not text:
            raise SignatureRefused(f"{where}: a free_text expectation needs its text.")
        if item.fault not in FAULTS:
            raise SignatureRefused(
                f"{where}: the fault word {item.fault!r} is not one of {', '.join(FAULTS)}."
            )
        return ValidExpectation(
            tier=item.tier,
            kind="free_text",
            phase=phase,
            expression=None,
            warning_fault=None,
            text=text,
            fault=item.fault,
            sentence=text,
        )

    if item.kind == "expects_warning":
        if item.warning not in UNIVERSAL_WARNING_FAULTS:
            raise SignatureRefused(
                f"{where}: {item.warning!r} is not a universal warning; expects_warning names "
                f"one of {', '.join(UNIVERSAL_WARNING_FAULTS)}."
            )
        if item.warning in _SHOT_WIDE_WARNINGS and phase is not None:
            raise SignatureRefused(
                f"{where}: {item.warning} is about the whole shot; name no phase."
            )
        where_words = f" in the {phase}" if phase else ""
        return ValidExpectation(
            tier=item.tier,
            kind="expects_warning",
            phase=phase,
            expression=None,
            warning_fault=item.warning,
            text="",
            fault=item.warning,
            sentence=f"{item.warning} is expected{where_words}: it is part of the design",
        )

    # A measure.
    if item.expression is None:
        raise SignatureRefused(f"{where}: a measure carries an expression.")
    try:
        expr = Expression.model_validate(item.expression)
    except ValidationError as exc:
        raise SignatureRefused(
            f"{where}: the expression is not valid: {_validation_lines(exc)}"
        ) from None
    if expr.compare is None:
        raise SignatureRefused(
            f"{where}: a measure needs a compare (the limit it is held against), for example "
            '{"op": "<=", "value": 0.15}.'
        )
    if expr.window.phase_number is not None:
        raise SignatureRefused(
            f'{where}: name the phase in the window ({{"phase": "ramp"}}), not its number: '
            "an expectation is keyed by the phase's name."
        )
    named = [_canonical_phase(name, profile_phases, where) for name in _window_phases(expr)]
    if expr.window.phase is not None:
        if phase is not None and phase_key(phase) != phase_key(named[0]):
            raise SignatureRefused(
                f"{where}: phase {phase!r} disagrees with the expression's window "
                f"({named[0]!r}). Leave the phase out; the window says it."
            )
        phase = named[0]
    elif expr.window.start is None and phase is not None:
        raise SignatureRefused(
            f"{where}: a measure over the whole shot has no phase; name the phase in the "
            "window to measure one."
        )
    expr = _spelled(expr, profile_phases)
    limit = expr.compare
    assert limit is not None
    words = fault_words(expr)
    under, over = _failing_directions(limit)
    missing = [
        side
        for side, needed, word in (("under", under, words.under), ("over", over, words.over))
        if needed and word is None
    ]
    if missing:
        raise SignatureRefused(
            f"{where}: when this measure fails {' or '.join(missing)} its limit, no fault word "
            f"applies ({expr.channel} {expr.op}). A check must fail with one of "
            f"{', '.join(FAULTS)}: bound the other side, choose a channel and op that have a "
            "word, or write it as free_text."
        )
    return ValidExpectation(
        tier=item.tier,
        kind="measure",
        phase=phase,
        expression=expr,
        warning_fault=None,
        text="",
        fault=_single_fault(expr),
        sentence=render(expr),
    )


# ── the checks of one shot ──────────────────────────────────────────


class ExpectationLike(Protocol):
    """What the evaluation reads of a stored expectation (``ExpectationRow`` is one)."""

    @property
    def id(self) -> int: ...
    @property
    def position(self) -> int: ...
    @property
    def tier(self) -> str: ...
    @property
    def phase(self) -> str | None: ...
    @property
    def kind(self) -> str: ...
    @property
    def expression(self) -> Expression | None: ...
    @property
    def warning_fault(self) -> str | None: ...
    @property
    def text(self) -> str: ...
    @property
    def fault(self) -> str | None: ...
    @property
    def sentence(self) -> str: ...


type CheckColor = Literal["red", "amber", "grey"]
type CheckStatus = Literal["failed", "held", "unmeasured", "expected", "warning", "unchecked"]
type CheckKind = Literal["measure", "reached", "expects_warning", "free_text", "warning"]


_COLORS: Mapping[int, CheckColor] = {0: "red", 1: "amber", 2: "amber", 3: "grey"}


@dataclass(frozen=True, slots=True)
class Check:
    """One entry of a shot's ordered list: a signature result or a universal warning."""

    kind: CheckKind
    status: CheckStatus
    #: ``None`` for a universal warning nothing marked expected.
    tier: str | None
    #: The shot's own phase name, or :data:`SHOT` for the whole shot.
    phase: str
    phase_number: int | None
    #: A word from the fixed list; ``None`` for a check that neither failed nor was raised.
    fault: str | None
    sentence: str
    detail: str
    value: float | None
    unit: str
    held: bool | None
    #: Why a value is absent, in words; ``None`` when there is one.
    absent: str | None
    at_s: float
    expectation_id: int | None
    rank: int
    #: Where within the group it stands: the expectation's position, a warning's fault.
    seq: int
    shot_wide: bool
    #: The limit the check was held against, after any Set version override, as the language
    #: states it; ``None`` for a check with no limit.
    compare: Compare | None = None
    #: What a share is a share of (``target_yield``, ``dose``, ``final_weight``).
    relative_to: str | None = None
    #: The limit as a person reads it: ``at most 15 % of target``, ``at most 3 g/s``.
    limit_text: str = ""
    #: A free-text expectation answered by a reading nobody has confirmed yet. Only the person's
    #: view carries one (the web draws it outlined); a chat is never given one.
    unverified: bool = False

    @property
    def color(self) -> CheckColor | None:
        return _COLORS.get(self.rank)

    @property
    def in_badge(self) -> bool:
        return self.rank in BADGE_RANKS

    @property
    def badge(self) -> str:
        """``ramp: early yield``."""
        return f"{self.phase}: {self.fault}"

    def order(self) -> tuple[int, bool, float, int]:
        return (self.rank, self.shot_wide, self.at_s, self.seq)

    def as_dict(self) -> dict[str, Any]:
        """The shape the lists and the badge are served in (``severity`` is the colour)."""
        return {
            "phase": self.phase,
            "fault": self.fault or "",
            "severity": self.color or "grey",
            "detail": self.detail,
            "phase_number": self.phase_number,
            "at_s": self.at_s,
            "tier": self.tier,
            "status": self.status,
            "expectation_id": self.expectation_id,
            "unverified": self.unverified,
        }


@dataclass(frozen=True, slots=True)
class SignatureState:
    """Whether the shot was read against a confirmed signature, and how big it is."""

    profile_version_id: int | None = None
    confirmed: int = 0

    @property
    def read_with_signature(self) -> bool:
        return self.confirmed > 0

    @property
    def text(self) -> str:
        if self.confirmed == 0:
            return "read without a signature"
        noun = "expectation" if self.confirmed == 1 else "expectations"
        return f"confirmed, {self.confirmed} {noun}"


@dataclass(frozen=True, slots=True)
class ShotChecks:
    """A shot's ordered checks and the signature state they were made under."""

    checks: tuple[Check, ...] = ()
    state: SignatureState = field(default_factory=SignatureState)

    @property
    def badge_entries(self) -> list[Check]:
        """The entries that make the badge and the list's ``warnings``, in order."""
        return [c for c in self.checks if c.in_badge]

    @classmethod
    def from_warnings(cls, warnings: Sequence[ShotWarning]) -> ShotChecks:
        """The checks of a shot read without a signature: its universal warnings, as they are."""
        return build_checks(warnings=warnings, expectations=[], override=None, data=None)


def _warning_check(warning: ShotWarning, *, expected_by: ExpectationLike | None) -> Check:
    if expected_by is None:
        rank, tier = 2, None
        detail = warning.detail
        status: CheckStatus = "warning"
    else:
        rank, tier, status = 3, expected_by.tier, "expected"
        detail = f"{warning.detail} This is expected: {expected_by.sentence}."
    return Check(
        kind="warning",
        status=status,
        tier=tier,
        phase=warning.phase,
        phase_number=warning.phase_number,
        fault=warning.fault,
        sentence=warning.detail,
        detail=detail,
        value=None,
        unit="",
        held=None,
        absent=None,
        at_s=warning.at_s,
        expectation_id=expected_by.id if expected_by is not None else None,
        rank=rank,
        seq=FAULTS.index(warning.fault),
        shot_wide=warning.phase_number is None,
    )


def _timing(
    phase: str | None, phases: Sequence[Mapping[str, Any]], duration_s: float
) -> tuple[str, int | None, float, bool]:
    """(the phase's name for the list, its number, where it falls in the shot, shot-wide)."""
    if phase is None:
        return SHOT, None, 0.0, True
    wanted = phase_key(phase)
    for row in phases:
        if phase_key(str(row.get("name") or "")) == wanted:
            number = row.get("phase_number")
            start = row.get("start_time_seconds")
            return (
                phase,
                number if isinstance(number, int) and not isinstance(number, bool) else None,
                float(start)
                if isinstance(start, int | float) and not isinstance(start, bool)
                else 0.0,
                False,
            )
    # A phase the shot never began counts at the moment the shot stopped, as `skipped` does.
    return phase, None, duration_s, False


_OF_WORDS = {
    "target_yield": "of target",
    "dose": "of the dose",
    "final_weight": "of the final weight",
}


def _number_text(value: float) -> str:
    return f"{value:g}"


def _scaled(compare: Compare, factor: float) -> Compare:
    def scale(bound: float | None) -> float | None:
        return None if bound is None else round(bound * factor, 6)

    return Compare(
        op=compare.op, value=scale(compare.value), low=scale(compare.low), high=scale(compare.high)
    )


def limit_text(expr: Expression, unit: str) -> str:
    """An expression's limit as a person reads it: ``at most 15 % of target``."""
    if expr.compare is None:
        return ""
    if expr.relative_to is not None:
        return f"{compare_words(_scaled(expr.compare, 100))} % {_OF_WORDS[expr.relative_to]}"
    return f"{compare_words(expr.compare)}{(' ' + unit) if unit else ''}"


def value_text(value: float, unit: str, relative_to: str | None) -> str:
    """A result as a person reads it: ``117.2 % of target``, ``4.2 g/s``."""
    if unit == "share":
        percent = _number_text(round(value * 100, 1))
        return f"{percent} % {_OF_WORDS.get(relative_to or '', '')}".rstrip()
    return f"{_number_text(value)} {unit}".rstrip()


def _result_check(
    exp: ExpectationLike,
    result: Result | None,
    *,
    expr: Expression | None,
    note: str,
    unreadable: str | None,
    timing: tuple[str, int | None, float, bool],
    held: bool | None,
    fault: str | None,
) -> Check:
    phase, number, at_s, shot_wide = timing
    unit = result.unit if result is not None else ""
    sentence = render(expr) if expr is not None else exp.sentence
    if held is None:
        why = unreadable or (result.why if result is not None else None) or "no value"
        status: CheckStatus = "unmeasured"
        detail = f"{sentence}: not measured ({why})."
        rank = 6 if exp.tier == "context" else 4
        value = None
        absent: str | None = why
    else:
        absent = None
        if exp.kind == "reached":
            # A phase either began or it did not: there is no number to serve.
            value = None
            detail = f"{sentence}; " + ("it did." if held else "it never began.")
        else:
            assert result is not None and result.value is not None
            value = result.value
            shown = value_text(value, unit, expr.relative_to if expr is not None else None)
            detail = f"{sentence}; this shot: {shown}.{note}"
        status = "held" if held else "failed"
        if exp.tier == "context":
            rank = 6
        elif held:
            rank = 5
        else:
            rank = 0 if exp.tier == "critical" else 1
    return Check(
        kind=exp.kind,  # type: ignore[arg-type]
        status=status,
        tier=exp.tier,
        phase=phase,
        phase_number=number,
        fault=fault if status == "failed" else None,
        sentence=sentence,
        detail=detail,
        value=value,
        unit=unit,
        held=held,
        absent=absent,
        at_s=at_s,
        expectation_id=exp.id,
        rank=rank,
        seq=exp.position,
        shot_wide=shot_wide,
        compare=expr.compare if expr is not None else None,
        relative_to=expr.relative_to if expr is not None else None,
        limit_text=limit_text(expr, unit) if expr is not None else "",
    )


def _profile_number(data: ShotData, name: str) -> int | None:
    """The phase's number: its index in the profile, else its row in the log's phase table."""
    if data.profile_phases:
        for index, profile_name in enumerate(data.profile_phases):
            if same_phase(profile_name, name):
                return index
    for span in data.phases:
        if same_phase(span.name, name):
            return span.number
    return None


def build_checks(
    *,
    warnings: Sequence[ShotWarning],
    expectations: Sequence[ExpectationLike],
    override: tuple[int, Compare] | None,
    data: ShotData | None,
    phases: Sequence[Mapping[str, Any]] = (),
    duration_s: float = 0.0,
    profile_version_id: int | None = None,
) -> ShotChecks:
    """One shot's ordered checks: the universal warnings and the confirmed expectations' results.

    ``expectations`` are the **confirmed** ones of the shot's profile version; the caller
    never hands an unconfirmed one, and nothing here could tell it from a confirmed one.
    ``override`` is the filed Set version's confirmed override, as (expectation id, the
    compare to use instead): applied only to that expectation and only to a measure.
    ``data`` is what the metric language reads of the shot, ``None`` when the log is not
    available (every measure is then not measured, with that reason).
    """
    checks: list[Check] = []
    expected_by: dict[int, ExpectationLike] = {}
    failed_words: set[tuple[str, str]] = set()
    unreadable_log = "the shot's log could not be read"

    for exp in expectations:
        if exp.kind == "free_text":
            phase, number, at_s, shot_wide = _timing(exp.phase, phases, duration_s)
            checks.append(
                Check(
                    kind="free_text",
                    status="unchecked",
                    tier=exp.tier,
                    phase=phase,
                    phase_number=number,
                    fault=exp.fault,
                    sentence=exp.sentence,
                    detail=f"{exp.sentence} (checked by the reading, not by a number).",
                    value=None,
                    unit="",
                    held=None,
                    absent=None,
                    at_s=at_s,
                    expectation_id=exp.id,
                    rank=7,
                    seq=exp.position,
                    shot_wide=shot_wide,
                )
            )
            continue
        timing = _timing(exp.phase, phases, duration_s)
        if exp.kind == "expects_warning":
            raised = [
                w
                for w in warnings
                if w.fault == exp.warning_fault
                and (
                    exp.phase is None
                    # A skipped warning names the first skipped phase and lists them all: the
                    # phase an expectation names may be any of them.
                    or any(same_phase(name, exp.phase) for name in (w.phases or (w.phase,)))
                )
            ]
            if raised:
                for w in raised:
                    expected_by.setdefault(id(w), exp)
                continue
            checks.append(
                Check(
                    kind="expects_warning",
                    status="held",
                    tier=exp.tier,
                    phase=timing[0],
                    phase_number=timing[1],
                    fault=None,
                    sentence=exp.sentence,
                    detail=f"{exp.sentence}; it was not raised on this shot.",
                    value=None,
                    unit="",
                    held=True,
                    absent=None,
                    at_s=timing[2],
                    expectation_id=exp.id,
                    rank=6 if exp.tier == "context" else 5,
                    seq=exp.position,
                    shot_wide=timing[3],
                )
            )
            continue
        if exp.kind == "reached":
            if data is None:
                reason: str | None = unreadable_log
            elif not data.phases:
                reason = "the log has no phase table"
            else:
                reason = None
            held: bool | None = None
            if data is not None and reason is None:
                number = _profile_number(data, exp.phase or "")
                held = number is not None and phase_began(number, data.samples)
            check = _result_check(
                exp,
                None,
                expr=None,
                note="",
                unreadable=reason,
                timing=timing,
                held=held,
                fault="skipped",
            )
            checks.append(check)
            if check.status == "failed" and exp.tier != "context":
                failed_words.add((phase_key(check.phase), "skipped"))
            continue
        # A measure.
        expr = exp.expression
        if expr is None:
            checks.append(
                _result_check(
                    exp,
                    None,
                    expr=None,
                    note="",
                    unreadable="the stored expression can no longer be read",
                    timing=timing,
                    held=None,
                    fault=None,
                )
            )
            continue
        note = ""
        if override is not None and override[0] == exp.id and expr.compare is not None:
            original = compare_words(expr.compare)
            expr = expr.model_copy(update={"compare": override[1]})
            note = f" (this Set version's limit; the profile's is {original})"
        if data is None:
            checks.append(
                _result_check(
                    exp,
                    None,
                    expr=expr,
                    note=note,
                    unreadable=unreadable_log,
                    timing=timing,
                    held=None,
                    fault=None,
                )
            )
            continue
        result = evaluate(expr, data)
        held_now = None if result.value is None else result.held
        fault = (
            fault_for_failure(expr, result.value)
            if held_now is False and result.value is not None
            else None
        )
        check = _result_check(
            exp,
            result,
            expr=expr,
            note=note,
            unreadable=None,
            timing=timing,
            held=held_now,
            fault=fault,
        )
        checks.append(check)
        if check.status == "failed" and check.fault is not None and exp.tier != "context":
            failed_words.add((phase_key(check.phase) if check.phase != SHOT else SHOT, check.fault))

    for warning in warnings:
        marked = expected_by.get(id(warning))
        if marked is None:
            key = (phase_key(warning.phase) if warning.phase != SHOT else SHOT, warning.fault)
            if key in failed_words:
                # A failed expectation that says the same thing about the same phase supersedes
                # the universal warning: one fact is listed once, at the tier the profile gave it.
                continue
        checks.append(_warning_check(warning, expected_by=marked))

    checks.sort(key=Check.order)
    return ShotChecks(
        checks=tuple(checks),
        state=SignatureState(profile_version_id=profile_version_id, confirmed=len(expectations)),
    )


def _fault_phrase(exp: ExpectationLike) -> str:
    """What a failure of the expectation is called, for a line a model or a person reads."""
    if exp.kind == "expects_warning":
        return f"{exp.warning_fault} is part of the design"
    if exp.kind == "measure":
        expr = exp.expression
        if expr is None or expr.compare is None:
            return "cannot be read"
        under, over = _failing_directions(expr.compare)
        words = fault_words(expr)
        found = [
            w
            for w in dict.fromkeys((words.under if under else None, words.over if over else None))
            if w
        ]
        return "fails as " + " or ".join(found)
    if exp.kind == "free_text":
        return f"checked by the reading, fails as {exp.fault}"
    return f"fails as {exp.fault}"


def expectation_line(exp: ExpectationLike, override: Compare | None = None) -> str:
    """One expectation as one line, tier first.

    ``critical, ramp, measure (fails as early yield): the sentence``.

    ``override`` is the filed Set version's confirmed limit, which the line then states with
    the profile's own beside it.
    """
    sentence = exp.sentence
    note = ""
    if override is not None and exp.kind == "measure" and exp.expression is not None:
        if exp.expression.compare is not None:
            note = (
                f" (this version's limit; the profile's is {compare_words(exp.expression.compare)})"
            )
        sentence = render(exp.expression.model_copy(update={"compare": override}))
    where = exp.phase if exp.phase else "whole shot"
    return f"{exp.tier}, {where}, {exp.kind} ({_fault_phrase(exp)}): {sentence}{note}"
