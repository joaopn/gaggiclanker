"""A phase that ended before the machine logged its first sample.

The machine samples every 250 ms, and a phase can end sooner than that: a fill that exits on
a pressure target ends the moment it starts when the group is still pressurised from a flush
or the previous shot (4.8 bar was seen against a 2.8 bar exit). The log then holds no sample
of the phase, and the transition table has no row of its own for it either: its first row is
the *next* phase's, and the reason on that row is why the previous phase, the one that has no
sample, ended.

So a phase **ended before it was sampled** when its number (its index in the profile) is below
the last phase any sample carries and no sample carries it. The facts worked out here are the
only record of it; they are stored with the shot's other shot-wide facts, and not as a row of
the phase list, because every reader of that list divides by a sample count, draws a band for
a span or averages over one, and a phase with no samples has none of them.

Pure Python, like the rest of the derivation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NotRequired, TypedDict

from gaggiclanker.domain.models import PHASE_EXIT_REASONS, PhaseTransition

__all__ = [
    "EXIT_REASONS_FROM_VERSION",
    "PRESSURE_TARGET",
    "UnsampledPhase",
    "ended_before_sampled",
    "ended_clause",
    "phases_unsampled",
    "reason_words",
    "stored_unsampled",
    "subject_words",
]

#: The first log version whose transition table says why each phase ended (the field was
#: reserved padding in version 5, so it reads as Unknown there).
EXIT_REASONS_FROM_VERSION = 6

#: The exit reason code of a phase that ended on a pressure target.
PRESSURE_TARGET = 2


class UnsampledPhase(TypedDict):
    """One phase of the profile that ended before the machine logged a sample of it."""

    phase_number: int
    #: The profile's name for the phase, ``phase N`` without a profile.
    name: str
    #: The firmware's exit-reason code of why it ended (0 is Unknown: a log from before the
    #: firmware recorded it, or a run of unsampled phases of which this is not the last).
    ended_by: int
    #: When it ended, in seconds: the start of the next phase the log holds.
    at_s: float
    #: The pressure at the first sample after it, bar; absent without a pressure sensor.
    pressure_end_bar: NotRequired[float]


def phases_unsampled(
    names: Sequence[str] | None,
    samples: Sequence[Mapping[str, float]],
    transitions: Sequence[PhaseTransition],
    *,
    version: int,
    has_pressure: bool,
) -> list[UnsampledPhase]:
    """The profile's phases below the last one any sample carries that no sample carries.

    The reason is the transition reason of the first row whose phase is the successor
    (number + 1), which is why the phase before it ended; when the next phase logged is further
    on (two or more unsampled phases in a row), only the last of them has that row, and the
    others read Unknown. The time is the start of the next phase logged, and the pressure the
    ``cp`` of its first sample, the group's pressure the moment the phase was over.
    """
    sampled = {int(s["phase"]) for s in samples if "phase" in s}
    if not sampled:
        return []
    found: list[UnsampledPhase] = []
    for number in range(max(sampled)):
        if number in sampled:
            continue
        after = next(s for s in samples if "phase" in s and int(s["phase"]) > number)
        name = ""
        if names is not None and number < len(names):
            name = str(names[number]).strip()
        reason = 0
        if version >= EXIT_REASONS_FROM_VERSION:
            row = next((t for t in transitions if t.phase_number == number + 1), None)
            reason = row.transition_reason if row is not None else 0
        entry = UnsampledPhase(
            phase_number=number,
            name=name or f"phase {number}",
            ended_by=reason,
            at_s=round(after.get("t", 0.0) / 1000.0, 1),
        )
        if has_pressure and "cp" in after:
            entry["pressure_end_bar"] = round(after["cp"], 1)
        found.append(entry)
    return found


def reason_words(code: int) -> str | None:
    """The reason as it reads in a sentence (``pressure target``), ``None`` when unknown."""
    if code == 0 or code not in PHASE_EXIT_REASONS:
        return None
    return PHASE_EXIT_REASONS[code].lower()


def ended_before_sampled(entry: Mapping[str, Any]) -> str:
    """``the Fill ended on its pressure target before the first sample``, in lower case.

    Without a reason: ``the Fill ended before the first sample; the machine did not log why``.
    """
    return f"{subject_words(entry)} {ended_clause(entry)}"


def ended_clause(entry: Mapping[str, Any]) -> str:
    """``ended on its pressure target before the first sample``: what to say of the phase."""
    reason = reason_words(int(entry.get("ended_by") or 0))
    if reason is None:
        return "ended before the first sample; the machine did not log why"
    return f"ended on its {reason} before the first sample"


def subject_words(entry: Mapping[str, Any]) -> str:
    """``the Fill``, or ``phase 0`` for a phase the log and the profile give no name."""
    name = str(entry.get("name") or "").strip() or f"phase {entry.get('phase_number')}"
    return name if name.startswith("phase ") and name[6:].isdigit() else f"the {name}"


def stored_unsampled(metrics: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """The phases that ended before they were sampled, from a shot's stored shot-wide facts.

    Lenient like every reader of the stored blob: a shot derived before the fact existed, or
    a blob of another shape, has none.
    """
    found = metrics.get("phases_unsampled") if isinstance(metrics, Mapping) else None
    if not isinstance(found, list):
        return []
    return [entry for entry in found if isinstance(entry, Mapping)]
