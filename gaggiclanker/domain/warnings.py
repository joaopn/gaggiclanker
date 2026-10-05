"""The few things that are plainly wrong with a shot without knowing what its profile is for.

A shot is judged against what its profile intends: a turbo profile runs 4-5 g/s
on purpose, a lever profile's decline is meant to fall. Nothing in a shot's own
numbers says which. So the warnings here are only the ones that need no such
knowledge, and all of them are **amber**: a profile's own statement of intent
(a signature, later) can raise one to red or mark it expected, and until then a
reader is told the fact and left to weigh it.

* **over target** — the final weight is above 110 % of the target yield of the
  version the shot is filed under;
* **under target** — it is below 90 % of it;
* **skipped** — the shot stopped on a volumetric or pumped-water target before
  one or more of its profile's phases began;
* **fast flow** — the scale flow averaged over 1.0 s was above 3.0 g/s while the
  pressure stayed at 80 % of the shot's peak or more.

:func:`shot_warnings` is one pure function from what is stored about the shot
(its numbers, derived once from its bytes), the recipe of the version it is
filed under and the profile's phase names (stored with the shot's facts), to a
list of warnings. Nothing here is stored: where a shot is filed changes, so the
yield warnings are worked out whenever a shot is read, and filing, moving or
discarding a shot never needs a re-derivation.

The fault words are the fixed list of the shot's review (:data:`FAULTS`); the
type holds the whole list so a later check adds a producer, not a word.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from gaggiclanker.domain.models import PHASE_EXIT_REASONS
from gaggiclanker.domain.phase_metrics import (
    FAST_FLOW_PRESSURE_SHARE,
    FAST_FLOW_SCALE_FLOW_G_S,
)

__all__ = [
    "FAULTS",
    "OVER_TARGET_SHARE",
    "SHOT",
    "STOPPED_ON_A_TARGET",
    "UNDER_TARGET_SHARE",
    "Fault",
    "Severity",
    "ShotWarning",
    "badge_text",
    "fault_token",
    "percent_of_target",
    "review_order",
    "shot_warnings",
    "sort_warnings",
    "warning_order",
]

#: Every fault word, in the order the shot review lists them.
type Fault = Literal[
    "early yield",
    "little yield",
    "fast flow",
    "slow flow",
    "skipped",
    "cut short",
    "low pressure",
    "high pressure",
    "unstable",
    "temperature",
    "over target",
    "under target",
]
FAULTS: tuple[Fault, ...] = (
    "early yield",
    "little yield",
    "fast flow",
    "slow flow",
    "skipped",
    "cut short",
    "low pressure",
    "high pressure",
    "unstable",
    "temperature",
    "over target",
    "under target",
)

#: ``red`` is a critical expectation failed, ``amber`` an important one or a
#: warning that needs no knowledge of the profile (everything produced here).
type Severity = Literal["red", "amber"]
_SEVERITY_ORDER: dict[str, int] = {"red": 0, "amber": 1}

#: What a shot-wide warning is said to be about, where a phase warning names its phase.
SHOT = "Shot"

#: Above this share of the target yield the cup is over target; below the
#: second it is under. Both ends are strict: 110 % exactly is not over.
OVER_TARGET_SHARE = 1.10
UNDER_TARGET_SHARE = 0.90

#: The exit reasons that stop a shot on a quantity it was always going to reach
#: part-way through its profile: 1 volumetric (weight), 4 pumped water.
STOPPED_ON_A_TARGET = frozenset({1, 4})


@dataclass(frozen=True, slots=True)
class ShotWarning:
    """One thing worth saying about a shot."""

    #: The shot's own phase name, or :data:`SHOT` for a shot-wide fault.
    phase: str
    fault: Fault
    severity: Severity
    #: A sentence with the numbers in it.
    detail: str
    #: The phase's number in the profile, or ``None`` for a shot-wide fault.
    phase_number: int | None
    #: Where in the shot the warning counts, in seconds; a skipped phase counts
    #: at the moment the shot stopped.
    at_s: float

    @property
    def badge(self) -> str:
        """``ramp: fast flow``."""
        return f"{self.phase}: {self.fault}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "fault": self.fault,
            "severity": self.severity,
            "detail": self.detail,
            "phase_number": self.phase_number,
            "at_s": self.at_s,
        }


def percent_of_target(weight_g: float | None, target_g: float | None) -> float | None:
    """A weight as a percentage of the target yield, to a tenth; ``None`` without both."""
    if weight_g is None or target_g is None or target_g <= 0:
        return None
    return round(weight_g / target_g * 100, 1)


def _share_above(weight_g: float, target_g: float, share: float) -> bool:
    # Compared as 100 x weight against 100 x share x target, rounded: 44.0 g is
    # exactly 110 % of 40 g, and a float product must not decide it.
    return round(weight_g * 100, 6) > round(target_g * share * 100, 6)


def _share_below(weight_g: float, target_g: float, share: float) -> bool:
    return round(weight_g * 100, 6) < round(target_g * share * 100, 6)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _phase_names(phases: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    names: dict[int, str] = {}
    for phase in phases:
        number = phase.get("phase_number")
        if isinstance(number, int) and not isinstance(number, bool):
            names.setdefault(number, str(phase.get("name") or "").strip() or f"phase {number}")
    return names


def shot_warnings(
    *,
    final_weight_g: float | None,
    scale_connected: bool,
    final_exit_reason: int,
    duration_s: float,
    target_yield_g: float | None,
    phases: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any] | None,
) -> list[ShotWarning]:
    """Every warning that applies, most severe first, then in the order of the shot.

    ``phases`` are the shot's stored phases (name and number), ``metrics`` its
    stored shot-wide facts (``diagnostics["metrics"]``): the profile's phases it
    never began and the first window of fast scale flow. ``target_yield_g`` is
    the filed version's target, ``None`` for a shot in no Set or a version that
    has none. A phase's warning is ordered by where it falls in the shot, a
    skipped phase at the moment the shot stopped, and the shot-wide warnings come
    after every phase warning of the same severity.
    """
    found: list[ShotWarning] = []
    names = _phase_names(phases)
    facts = metrics if isinstance(metrics, Mapping) else {}

    window = facts.get("fast_flow")
    if isinstance(window, Mapping):
        number = window.get("phase_number")
        number = number if isinstance(number, int) and not isinstance(number, bool) else None
        if facts.get("per_phase") is False:
            # A log with no phase table is one stretch the app named "extraction":
            # that is no phase the profile has, so the warning is about the shot.
            number = None
        start = _number(window.get("start_s")) or 0.0
        mean = _number(window.get("mean_g_s")) or 0.0
        lowest = _number(window.get("pressure_min_bar")) or 0.0
        peak = _number(window.get("peak_pressure_bar")) or 0.0
        end = _number(window.get("end_s")) or start
        found.append(
            ShotWarning(
                phase=names.get(number, SHOT) if number is not None else SHOT,
                fault="fast flow",
                severity="amber",
                detail=(
                    f"The scale flow averaged {mean:.2f} g/s from {start:.2f} s to {end:.2f} s "
                    f"while the pressure stayed at or above {lowest:.1f} bar "
                    f"({FAST_FLOW_PRESSURE_SHARE * 100:.0f} % of the {peak:.1f} bar peak or "
                    f"more); above {FAST_FLOW_SCALE_FLOW_G_S:.1f} g/s over a second is fast flow, "
                    "which a profile built for it (a turbo shot) does on purpose."
                ),
                phase_number=number,
                at_s=start,
            )
        )

    skipped = facts.get("phases_not_reached")
    if (
        final_exit_reason in STOPPED_ON_A_TARGET
        and isinstance(skipped, Sequence)
        and not isinstance(skipped, str)
    ):
        left = [
            (int(item["phase_number"]), str(item.get("name") or "").strip())
            for item in skipped
            if isinstance(item, Mapping) and isinstance(item.get("phase_number"), int)
        ]
        if left:
            reason = PHASE_EXIT_REASONS.get(final_exit_reason, "a target").lower()
            listed = ", ".join(name or f"phase {number}" for number, name in left)
            first_number, first_name = left[0]
            found.append(
                ShotWarning(
                    phase=first_name or f"phase {first_number}",
                    fault="skipped",
                    severity="amber",
                    detail=(
                        f"The shot stopped on its {reason} before {listed} began: "
                        f"{len(left)} of the profile's phases never ran."
                    ),
                    phase_number=first_number,
                    at_s=duration_s,
                )
            )

    if scale_connected and final_weight_g is not None and final_weight_g > 0:
        if target_yield_g is not None and target_yield_g > 0:
            share = percent_of_target(final_weight_g, target_yield_g)
            if _share_above(final_weight_g, target_yield_g, OVER_TARGET_SHARE):
                found.append(
                    ShotWarning(
                        phase=SHOT,
                        fault="over target",
                        severity="amber",
                        detail=(
                            f"The final weight, {final_weight_g:.1f} g, is {share:.1f} % of the "
                            f"{target_yield_g:g} g target yield "
                            f"(over {OVER_TARGET_SHARE * 100:.0f} % is over target)."
                        ),
                        phase_number=None,
                        at_s=duration_s,
                    )
                )
            elif _share_below(final_weight_g, target_yield_g, UNDER_TARGET_SHARE):
                found.append(
                    ShotWarning(
                        phase=SHOT,
                        fault="under target",
                        severity="amber",
                        detail=(
                            f"The final weight, {final_weight_g:.1f} g, is {share:.1f} % of the "
                            f"{target_yield_g:g} g target yield (under "
                            f"{UNDER_TARGET_SHARE * 100:.0f} % is under target)."
                        ),
                        phase_number=None,
                        at_s=duration_s,
                    )
                )

    return sort_warnings(found)


def warning_order(warning: ShotWarning) -> tuple[int, bool, float, int]:
    """Where one warning stands: severity, then a phase's before a shot-wide one, then time."""
    return (
        _SEVERITY_ORDER[warning.severity],
        warning.phase_number is None,
        warning.at_s,
        FAULTS.index(warning.fault),
    )


def sort_warnings(warnings: Sequence[ShotWarning]) -> list[ShotWarning]:
    """Most severe first, then in the order of the shot, the shot-wide ones last."""
    return sorted(warnings, key=warning_order)


def review_order(warnings: Sequence[ShotWarning]) -> tuple[int, tuple[int, bool, float, int]]:
    """What the shots table's Review column sorts by: the order of the badge's own warning.

    A shot with warnings sorts by its first one, the one its badge names: severity,
    then a phase's warning before a shot-wide one, then the time in the shot. A shot
    with none sorts after every shot that has one. Smaller is worse. Shots with an
    equal key are put newest first by the caller (`ShotsRepository`).
    """
    if not warnings:
        return (1, (0, False, 0.0, 0))
    return (0, warning_order(sort_warnings(warnings)[0]))


def badge_text(warnings: Sequence[ShotWarning]) -> str | None:
    """The one-line badge for a list of warnings, built by code and never by a model.

    The first warning, ``ramp: fast flow``, and ``+N`` for the others; ``None``
    for a shot with none.
    """
    if not warnings:
        return None
    first = warnings[0].badge
    return first if len(warnings) == 1 else f"{first} +{len(warnings) - 1}"


def fault_token(fault: str) -> str:
    """``fast flow`` as ``fast_flow``: how a rule's selection names a fault."""
    return fault.replace(" ", "_")
