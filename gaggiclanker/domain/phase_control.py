"""What each phase of a profile steers the pump by.

The `.slog` logs a pressure target (`tp`) and a flow target (`tf`) on every
sample of an advanced pump phase, but only one of them is the target: the other
is a soft limit the firmware also writes into the log. A simple (power) phase
logs 0/0. The log alone cannot say which is which, so the answer comes from the
profile the shot was brewed with: `phase.pump` is an integer (a duty-cycle
percent) or an object whose `target` is `pressure` or `flow`
(firmware v1.9.0, `profile.h` and `BrewProcess.h`).

Pure Python and lenient on purpose: the profile arrives as the stored canonical
document, and a document that cannot be read this way must leave a shot
*ungraded*, never fail its derivation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

__all__ = ["PhaseControl", "phase_controls"]

#: What one phase drives the pump by. ``power`` is the simple form: a fixed
#: duty cycle, with no pressure or flow target to follow.
PhaseControl = Literal["pressure", "flow", "power"]


def phase_controls(profile: Mapping[str, Any] | None) -> tuple[PhaseControl, ...] | None:
    """One control mode per phase, in the profile's order, or ``None`` when unreadable.

    ``None`` is "we do not know": no profile, no phase list, or a phase whose
    `pump` is neither an integer nor an object naming `pressure` or `flow`. A
    caller must read it as "grade nothing", not as "no phase has a target".
    """
    if profile is None:
        return None
    phases = profile.get("phases")
    if not isinstance(phases, list):
        return None
    controls: list[PhaseControl] = []
    for phase in phases:
        pump = phase.get("pump") if isinstance(phase, Mapping) else None
        # `bool` is an `int` in Python and never a firmware duty cycle.
        if isinstance(pump, int) and not isinstance(pump, bool):
            controls.append("power")
        elif isinstance(pump, Mapping) and pump.get("target") in ("pressure", "flow"):
            controls.append("pressure" if pump["target"] == "pressure" else "flow")
        else:
            return None
    return tuple(controls)
