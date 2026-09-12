"""Layer 2: a safety policy narrower than the firmware's own parser.

There are four layers between a profile
and the machine. Layer 1 is :class:`~gaggiclanker.domain.models.Profile` — the
strict schema, which is what rejects a float `pump`, an `operator` the firmware
would silently read as `lte`, a target type it would silently drop, and a
profile with no phases. This module is layer 2, and it exists because a schema
cannot tell that 140 °C is valid JSON and a ruined shot.

Two functions, and the difference between them is the whole design:

* :func:`clamp` **moves numbers into range** and says what it moved. Every
  change is reported, none is silent, and the UI shows the list beside the
  approval button.
* :func:`check` **reports what is still wrong** afterwards. A profile that
  cannot be made compliant by moving numbers — eleven phases, a profile that
  does not terminate — is *rejected*, never quietly rewritten. Truncating a
  profile to ten phases would change what it brews while claiming to have made
  it safe, which is worse than refusing.

:func:`enforce` is the two in order, and it is what every write path calls.

The bounds are settings-backed (:data:`POLICY_SETTING_KEYS`) so a person with a
lever machine and a 12-second ratio can move them without a rebuild. The
defaults are deliberately tighter than the firmware: it accepts 150 °C and 300 s
per phase, and neither is a number anybody should reach by accident.

Nothing here imports the database, FastAPI or the settings service — the domain
layer is pure, and :func:`bounds_from` takes the resolved values as a mapping so
the caller owns the lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from gaggiclanker.domain.models import (
    HOLD_MEASURED,
    Phase,
    Profile,
    Pump,
    Target,
    Transition,
    _normalise_numbers,
)

__all__ = [
    "DEFAULT_BOUNDS",
    "POLICY_SETTING_KEYS",
    "PolicyBounds",
    "PolicyChange",
    "ProfileRejected",
    "StopConditionChange",
    "TargetSpec",
    "Violation",
    "bounds_from",
    "check",
    "clamp",
    "diff_stop_conditions",
    "enforce",
]


@dataclass(frozen=True, slots=True)
class PolicyBounds:
    """What a profile is allowed to ask the machine for.

    Every field is narrower than the firmware's own limit, which is the point:
    the firmware accepts a temperature of 150 °C and a phase of 300 s, and a
    draft that reaches either is a mistake rather than an intention.
    """

    #: Below this the group never gets hot enough to extract; above it the
    #: boiler is into steam territory and the puck is scalded.
    temperature_min_c: float = 60.0
    temperature_max_c: float = 100.0
    #: The pump's own ceiling is 12 bar and the firmware takes that literally;
    #: the floor is 0, which means "no limit" on the non-target field.
    pressure_min_bar: float = 0.0
    pressure_max_bar: float = 12.0
    #: The firmware takes flow up to 15 g/s. Ten is already more than a 58 mm
    #: basket passes without channelling.
    flow_min_ml_s: float = 0.0
    flow_max_ml_s: float = 10.0
    #: 0.5 s is the schema's own floor; 120 s is a quarter of the firmware's
    #: per-phase cap and longer than any espresso phase anybody wants.
    phase_duration_min_s: float = 0.5
    phase_duration_max_s: float = 120.0
    #: The firmware has no limit at all. Ten phases is more than the machine's
    #: brew screen can show and more than a person can reason about.
    max_phases: int = 10


DEFAULT_BOUNDS = PolicyBounds()

#: The registry keys :func:`bounds_from` reads, in the order the Settings page
#: shows them. Declared here so the registry and this module cannot drift: the
#: settings entries are generated from nothing, but a key added there and not
#: here is dead, and this tuple is what the test asserts against.
POLICY_SETTING_KEYS: tuple[str, ...] = (
    "profilePolicyTemperatureMinC",
    "profilePolicyTemperatureMaxC",
    "profilePolicyPressureMaxBar",
    "profilePolicyFlowMaxMlS",
    "profilePolicyPhaseDurationMinS",
    "profilePolicyPhaseDurationMaxS",
    "profilePolicyMaxPhases",
)

#: Registry key -> :class:`PolicyBounds` field. The two names differ because one
#: is camelCase for the browser and the other is Python.
_KEY_TO_FIELD: dict[str, str] = {
    "profilePolicyTemperatureMinC": "temperature_min_c",
    "profilePolicyTemperatureMaxC": "temperature_max_c",
    "profilePolicyPressureMaxBar": "pressure_max_bar",
    "profilePolicyFlowMaxMlS": "flow_max_ml_s",
    "profilePolicyPhaseDurationMinS": "phase_duration_min_s",
    "profilePolicyPhaseDurationMaxS": "phase_duration_max_s",
    "profilePolicyMaxPhases": "max_phases",
}


def bounds_from(values: dict[str, Any]) -> PolicyBounds:
    """Build bounds from resolved settings, falling back per key to the default.

    A missing or unusable value takes the default rather than failing: the
    bounds are a safety net, and a typo in one of them must not be the thing
    that stops the net existing.
    """
    fields: dict[str, Any] = {}
    for key, field in _KEY_TO_FIELD.items():
        raw = values.get(key)
        if raw is None:
            continue
        try:
            fields[field] = int(raw) if field == "max_phases" else float(raw)
        except (TypeError, ValueError):
            continue
    return PolicyBounds(**fields)


class PolicyChange(BaseModel):
    """One number the policy moved, and what it was before.

    Rendered verbatim beside the approve button. ``path`` is the address inside
    the document (`phases[2].pump.pressure`) so the UI can point at the field
    rather than describing it.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    field: str
    before: float
    after: float
    reason: str


class Violation(BaseModel):
    """One thing :func:`clamp` could not fix, in words a person can act on."""

    model_config = ConfigDict(extra="forbid")

    path: str
    field: str
    message: str


class ProfileRejected(Exception):
    """A profile that cannot be made compliant. Carries every reason at once.

    A plain exception rather than an ``AppError``: the domain layer does not
    import ``infra``, and the service that calls :func:`enforce` is the right
    place to decide that this is a 422.
    """

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        detail = "; ".join(f"{v.path}: {v.message}" for v in violations)
        super().__init__(f"The profile breaks the safety policy: {detail}")


# ── clamping ─────────────────────────────────────────────────────────


def clamp(
    profile: Profile, bounds: PolicyBounds = DEFAULT_BOUNDS
) -> tuple[Profile, list[PolicyChange]]:
    """Move every out-of-range number into range. Returns a new profile.

    The input is never mutated: a draft has to be diffable against the document
    it came from, and an in-place clamp would make the "before" column of that
    diff the same as the "after" one.

    What is *not* clamped: the number of phases, and whether the profile
    terminates. Both are structural, and :func:`check` reports them.
    """
    changes: list[PolicyChange] = []
    temperature = _clamp_value(
        profile.temperature,
        bounds.temperature_min_c,
        bounds.temperature_max_c,
        path="temperature",
        field="temperature",
        reason=(
            f"profile temperature must be {bounds.temperature_min_c:g}-"
            f"{bounds.temperature_max_c:g} °C"
        ),
        changes=changes,
    )
    phases = [
        _clamp_phase(phase, index, bounds, changes) for index, phase in enumerate(profile.phases)
    ]
    return profile.model_copy(update={"temperature": temperature, "phases": phases}), changes


def _clamp_phase(
    phase: Phase, index: int, bounds: PolicyBounds, changes: list[PolicyChange]
) -> Phase:
    where = f"phases[{index}]"
    duration = _clamp_value(
        phase.duration,
        bounds.phase_duration_min_s,
        bounds.phase_duration_max_s,
        path=f"{where}.duration",
        field="duration",
        reason=(
            f"a phase runs for {bounds.phase_duration_min_s:g}-{bounds.phase_duration_max_s:g} s"
        ),
        changes=changes,
    )

    # 0 is the sentinel for "inherit the profile temperature" and is left
    # exactly as it is: clamping it to the minimum would turn "whatever the
    # profile says" into a hard 60 °C, which is a different profile.
    temperature = phase.temperature
    if temperature:
        temperature = _clamp_value(
            temperature,
            bounds.temperature_min_c,
            bounds.temperature_max_c,
            path=f"{where}.temperature",
            field="temperature",
            reason=(
                f"a phase temperature override must be {bounds.temperature_min_c:g}-"
                f"{bounds.temperature_max_c:g} °C"
            ),
            changes=changes,
        )

    return phase.model_copy(
        update={
            "duration": duration,
            "temperature": temperature,
            "transition": _clamp_transition(phase.transition, duration, where, changes),
            "pump": _clamp_pump(phase.pump, bounds, where, changes),
            "targets": [
                _clamp_target(target, position, bounds, where, changes)
                for position, target in enumerate(phase.targets)
            ],
        }
    )


def _clamp_transition(
    transition: Transition | None, duration: float, where: str, changes: list[PolicyChange]
) -> Transition | None:
    """A ramp longer than the phase it ramps into is a ramp that never arrives.

    The firmware clamps this itself (`profile.h:255-272`), silently. Doing it
    here means the person approving the draft sees that it happened.
    """
    if transition is None:
        return None
    ramp = _clamp_value(
        transition.duration,
        0.0,
        duration,
        path=f"{where}.transition.duration",
        field="transition.duration",
        reason="a transition cannot be longer than the phase it ramps into",
        changes=changes,
    )
    return transition.model_copy(update={"duration": ramp})


def _clamp_pump(
    pump: int | Pump, bounds: PolicyBounds, where: str, changes: list[PolicyChange]
) -> int | Pump:
    """The advanced pump object's two numbers. The simple form is a percent.

    An integer percent is already 0..100 by the schema, and it must *stay* an
    integer: the firmware branches on `p["pump"].is<int>()`, so a float is
    parsed as an advanced object with zero targets and the pump never runs.
    That is why nothing here ever produces a float from an int.
    """
    if isinstance(pump, int):
        return pump
    pressure = pump.pressure
    if pressure != HOLD_MEASURED:
        pressure = _clamp_value(
            pressure,
            bounds.pressure_min_bar,
            bounds.pressure_max_bar,
            path=f"{where}.pump.pressure",
            field="pump.pressure",
            reason=(
                f"pump pressure must be {bounds.pressure_min_bar:g}-{bounds.pressure_max_bar:g} bar"
            ),
            changes=changes,
        )
    flow = pump.flow
    if flow != HOLD_MEASURED:
        flow = _clamp_value(
            flow,
            bounds.flow_min_ml_s,
            bounds.flow_max_ml_s,
            path=f"{where}.pump.flow",
            field="pump.flow",
            reason=f"pump flow must be {bounds.flow_min_ml_s:g}-{bounds.flow_max_ml_s:g} ml/s",
            changes=changes,
        )
    return pump.model_copy(update={"pressure": pressure, "flow": flow})


def _clamp_target(
    target: Target, position: int, bounds: PolicyBounds, where: str, changes: list[PolicyChange]
) -> Target:
    """Only the two targets expressed in a unit the policy bounds.

    `volumetric` is grams in the cup and `pumped` is millilitres through the
    pump; neither is a pressure the machine has to reach, so neither has a
    policy ceiling. Clamping a 36 g yield to something would be the policy
    deciding how much coffee somebody wants.
    """
    path = f"{where}.targets[{position}]"
    if target.type == "pressure":
        value = _clamp_value(
            target.value,
            0.0,
            bounds.pressure_max_bar,
            path=path,
            field="target.value",
            reason=f"a pressure stop condition must be 0-{bounds.pressure_max_bar:g} bar",
            changes=changes,
        )
        return target.model_copy(update={"value": value})
    if target.type == "flow":
        value = _clamp_value(
            target.value,
            0.0,
            bounds.flow_max_ml_s,
            path=path,
            field="target.value",
            reason=f"a flow stop condition must be 0-{bounds.flow_max_ml_s:g} ml/s",
            changes=changes,
        )
        return target.model_copy(update={"value": value})
    return target


def _clamp_value(
    value: float,
    low: float,
    high: float,
    *,
    path: str,
    field: str,
    reason: str,
    changes: list[PolicyChange],
) -> float:
    """Clamp one number, appending a :class:`PolicyChange` when it moved."""
    if low > high:
        # Bounds a person mis-typed (min above max). Clamping to an empty range
        # would produce nonsense; leaving the value alone lets `check` say so.
        return value
    clamped = min(max(value, low), high)
    if clamped != value:
        changes.append(
            PolicyChange(path=path, field=field, before=value, after=clamped, reason=reason)
        )
    return clamped


# ── checking ─────────────────────────────────────────────────────────


def check(profile: Profile, bounds: PolicyBounds = DEFAULT_BOUNDS) -> list[Violation]:
    """Everything still wrong with a profile. Empty means it may be written.

    Run this *after* :func:`clamp`: what survives a clamp is what a clamp
    cannot fix, and that list is the refusal.
    """
    violations: list[Violation] = []

    if bounds.temperature_min_c > bounds.temperature_max_c:
        violations.append(
            Violation(
                path="(policy)",
                field="temperature",
                message="the configured temperature bounds are inverted; fix them in Settings",
            )
        )
    if len(profile.phases) > bounds.max_phases:
        violations.append(
            Violation(
                path="phases",
                field="phases",
                message=(
                    f"{len(profile.phases)} phases, and the policy allows {bounds.max_phases}. "
                    "Remove phases yourself — truncating a profile would change what it brews."
                ),
            )
        )
    if not _in_range(profile.temperature, bounds.temperature_min_c, bounds.temperature_max_c):
        violations.append(
            Violation(
                path="temperature",
                field="temperature",
                message=(
                    f"{profile.temperature:g} °C is outside "
                    f"{bounds.temperature_min_c:g}-{bounds.temperature_max_c:g} °C"
                ),
            )
        )

    for index, phase in enumerate(profile.phases):
        violations.extend(_check_phase(phase, index, bounds))

    violations.extend(_check_termination(profile, bounds))
    return violations


def _check_phase(phase: Phase, index: int, bounds: PolicyBounds) -> list[Violation]:
    where = f"phases[{index}]"
    out: list[Violation] = []
    if not _in_range(phase.duration, bounds.phase_duration_min_s, bounds.phase_duration_max_s):
        out.append(
            Violation(
                path=f"{where}.duration",
                field="duration",
                message=(
                    f"{phase.duration:g} s is outside {bounds.phase_duration_min_s:g}-"
                    f"{bounds.phase_duration_max_s:g} s"
                ),
            )
        )
    if phase.temperature and not _in_range(
        phase.temperature, bounds.temperature_min_c, bounds.temperature_max_c
    ):
        out.append(
            Violation(
                path=f"{where}.temperature",
                field="temperature",
                message=(
                    f"{phase.temperature:g} °C is outside "
                    f"{bounds.temperature_min_c:g}-{bounds.temperature_max_c:g} °C"
                ),
            )
        )
    if phase.transition is not None and phase.transition.duration > phase.duration:
        out.append(
            Violation(
                path=f"{where}.transition.duration",
                field="transition.duration",
                message=(
                    f"a {phase.transition.duration:g} s ramp into a {phase.duration:g} s phase "
                    "never reaches its setpoint"
                ),
            )
        )
    if isinstance(phase.pump, int):
        # Unreachable through the schema, which is `StrictInt`. Kept because
        # this list is also what `scripts/profile_gate.py` prints for a file
        # somebody hand-edited, and "the pump must be an integer percent" is
        # the single most useful sentence this project can say about a profile.
        if not isinstance(phase.pump, bool) and not 0 <= phase.pump <= 100:
            out.append(
                Violation(
                    path=f"{where}.pump",
                    field="pump",
                    message=f"a pump percentage must be 0-100, not {phase.pump}",
                )
            )
    else:
        out.extend(_check_pump(phase.pump, where, bounds))
    for position, target in enumerate(phase.targets):
        out.extend(_check_target(target, f"{where}.targets[{position}]", bounds))
    return out


def _check_pump(pump: Pump, where: str, bounds: PolicyBounds) -> list[Violation]:
    out: list[Violation] = []
    if pump.pressure != HOLD_MEASURED and not _in_range(
        pump.pressure, bounds.pressure_min_bar, bounds.pressure_max_bar
    ):
        out.append(
            Violation(
                path=f"{where}.pump.pressure",
                field="pump.pressure",
                message=(
                    f"{pump.pressure:g} bar is outside "
                    f"{bounds.pressure_min_bar:g}-{bounds.pressure_max_bar:g} bar"
                ),
            )
        )
    if pump.flow != HOLD_MEASURED and not _in_range(
        pump.flow, bounds.flow_min_ml_s, bounds.flow_max_ml_s
    ):
        out.append(
            Violation(
                path=f"{where}.pump.flow",
                field="pump.flow",
                message=(
                    f"{pump.flow:g} ml/s is outside "
                    f"{bounds.flow_min_ml_s:g}-{bounds.flow_max_ml_s:g} ml/s"
                ),
            )
        )
    return out


def _check_target(target: Target, path: str, bounds: PolicyBounds) -> list[Violation]:
    ceiling = {
        "pressure": bounds.pressure_max_bar,
        "flow": bounds.flow_max_ml_s,
    }.get(target.type)
    if ceiling is None or _in_range(target.value, 0.0, ceiling):
        return []
    unit = "bar" if target.type == "pressure" else "ml/s"
    return [
        Violation(
            path=path,
            field="target.value",
            message=(
                f"a {target.type} stop condition of {target.value:g} {unit} is above {ceiling:g}"
            ),
        )
    ]


def _check_termination(profile: Profile, bounds: PolicyBounds) -> list[Violation]:
    """Every profile must have something that ends it.

    Two ways to satisfy this, and either is enough:

    * the last brew phase carries a volumetric or pumped stop condition, which
      is a real end — the cup is full, or the pump has moved its water; or
    * every phase has a duration inside the policy's ceiling, so the worst case
      is bounded by the clock.

    The second is what a utility profile (a backflush) relies on and the first
    is what a volumetric espresso profile relies on. A profile with a 300 s
    phase and no target has neither, and 300 s of 9 bar into a full basket is
    the failure mode this rule exists for.
    """
    unbounded = [
        index
        for index, phase in enumerate(profile.phases)
        if phase.duration > bounds.phase_duration_max_s
    ]
    if not unbounded:
        return []
    last_brew = _last_brew_phase(profile)
    if last_brew is not None and any(
        target.type in ("volumetric", "pumped") and target.value > 0 for target in last_brew.targets
    ):
        return []
    return [
        Violation(
            path=f"phases[{unbounded[0]}].duration",
            field="targets",
            message=(
                "nothing ends this profile: its last brew phase has no volumetric or pumped "
                f"stop condition and phase {unbounded[0]} runs past the "
                f"{bounds.phase_duration_max_s:g} s ceiling"
            ),
        )
    ]


def _last_brew_phase(profile: Profile) -> Phase | None:
    for phase in reversed(profile.phases):
        if phase.phase == "brew":
            return phase
    return profile.phases[-1] if profile.phases else None


def _in_range(value: float, low: float, high: float) -> bool:
    return low <= value <= high


def enforce(
    profile: Profile, bounds: PolicyBounds = DEFAULT_BOUNDS
) -> tuple[Profile, list[PolicyChange]]:
    """Clamp, then check, then either hand back the profile or refuse.

    The one entry point every write path uses. Raising rather than returning a
    third value is deliberate: a caller that forgets to look at a violations
    list has written a profile to a machine.
    """
    clamped, changes = clamp(profile, bounds)
    violations = check(clamped, bounds)
    if violations:
        raise ProfileRejected(violations)
    return clamped, changes


# ── stop-condition diff ──────────────────────────────────────────────


class TargetSpec(BaseModel):
    """One stop condition, in the shape the diff renders."""

    model_config = ConfigDict(extra="forbid")

    type: str
    operator: str
    value: float


class StopConditionChange(BaseModel):
    """One stop condition that a draft added, removed or moved.

    crema's rule, and the reason this has its own type rather than being part
    of the general diff: a stop condition is the thing that decides when the
    machine stops pumping water into the cup. Changing a pressure setpoint
    makes a different shot; changing a stop condition can make a different
    *amount of coffee*, and the person approving the draft is told so and has
    to tick a box.
    """

    model_config = ConfigDict(extra="forbid")

    phase_index: int
    phase_name: str
    kind: Literal["added", "removed", "changed"]
    target_type: str
    before: TargetSpec | None = None
    after: TargetSpec | None = None


def diff_stop_conditions(base: Profile, draft: Profile) -> list[StopConditionChange]:
    """Per-phase `targets` diff, comparing canonical numbers.

    Numbers are normalised the way :func:`canonical_profile_json` normalises
    them, so ``9`` and ``9.0`` are the same stop condition and a draft that
    merely round-tripped through JSON does not demand an acknowledgement.

    Targets are matched **by type within a phase**, because that is how a person
    reads them: "the volumetric target moved from 36 to 40", not "the target at
    index 1 changed". A phase carrying two targets of the same type matches them
    in order.
    """
    changes: list[StopConditionChange] = []
    count = max(len(base.phases), len(draft.phases))
    for index in range(count):
        before_phase = base.phases[index] if index < len(base.phases) else None
        after_phase = draft.phases[index] if index < len(draft.phases) else None
        named = after_phase if after_phase is not None else before_phase
        name = named.name if named is not None else ""
        changes.extend(
            _diff_phase_targets(
                index,
                name,
                list(before_phase.targets) if before_phase else [],
                list(after_phase.targets) if after_phase else [],
            )
        )
    return changes


def _diff_phase_targets(
    index: int, name: str, before: list[Target], after: list[Target]
) -> list[StopConditionChange]:
    out: list[StopConditionChange] = []
    types = list(dict.fromkeys([t.type for t in before] + [t.type for t in after]))
    for target_type in types:
        olds = [t for t in before if t.type == target_type]
        news = [t for t in after if t.type == target_type]
        for position in range(max(len(olds), len(news))):
            old = olds[position] if position < len(olds) else None
            new = news[position] if position < len(news) else None
            if old is not None and new is not None:
                if _spec(old) == _spec(new):
                    continue
                kind: Literal["added", "removed", "changed"] = "changed"
            elif new is not None:
                kind = "added"
            else:
                kind = "removed"
            out.append(
                StopConditionChange(
                    phase_index=index,
                    phase_name=name,
                    kind=kind,
                    target_type=target_type,
                    before=_spec(old) if old is not None else None,
                    after=_spec(new) if new is not None else None,
                )
            )
    return out


def _spec(target: Target) -> TargetSpec:
    """A target with its number normalised, so 9 and 9.0 compare equal."""
    return TargetSpec(
        type=target.type,
        operator=target.operator,
        value=float(_normalise_numbers(target.value)),
    )
