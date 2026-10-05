"""A constructed lever shot, built out of a real fixture, and the profile it was pulled on.

The case it stands for: a lever profile whose cup is already over its target
yield before the profile's decline phase begins, because the shot stopped on
its weight target during the ramp. Nothing here comes from a person's own
shot; the numbers are made up here and every expected value in the tests is one
of the constants below, or computed from them.

The skeleton is a real fixture (`shot_204_ramping_flow.slog`: its timestamps,
temperatures, pressure trace and phase boundaries); the weight, scale flow,
water counter, exit reasons and the end of the shot are rewritten, and the log
is promoted to version 7 so it carries what version 7 carries (the pumped-water
counter and the reason each phase ended). The result is encodable, so it goes
through the same ingest path as a shot read off a machine.

Phases, by sample index (250 ms apart):

* 0..28   "preinfusion", ends on its duration;
* 29..68  "soak", the cup rises 0.1 g per sample, ends on its duration;
* 69..94  "ramp", the cup rises 0.2 g per sample (0.8 g/s) ...
* 95..127 ... then 1.0 g per sample (4.0 g/s) while the pressure is at the top of
  its range; the shot stops on its volumetric target at sample 127;
* 128..133 the extended recording after the stop: the weight holds and the
  pumped-water counter has been reset by the controller (as it is when the
  weight stop ends the shot).

The profile has a fourth phase, "decline", which the shot never reaches. Its
phases are simple (power) phases, as a lever machine's are: there is no pressure
or flow target in them to grade the trace against.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from gaggiclanker.domain.models import PhaseTransition
from gaggiclanker.domain.slog import FIELDS_MASK_ALL, Slog, parse_slog

__all__ = [
    "BASE_FIXTURE",
    "LEVER_PROFILE",
    "RAMP_END_G",
    "SOAK_END_G",
    "TARGET_YIELD_G",
    "lever_shot",
    "without_pressure",
    "without_scale",
]

BASE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "slog" / "shot_204_ramping_flow.slog"

#: The version's target yield, and the weight the profile's decline phase stops on.
TARGET_YIELD_G = 36.0
#: The cup at the end of the soak and at the end of the ramp, in grams.
SOAK_END_G = 4.0
RAMP_END_G = 42.2

#: Where each phase starts and the samples the shot is made of.
PHASE_STARTS = (0, 29, 69)
LAST_RAMP_SAMPLE = 127
LAST_SAMPLE = 133
#: The sample the scale flow jumps to 4 g/s at.
FAST_FROM = 95

#: The pumped-water counter: half a millilitre per sample from the first
#: soak sample on, then reset by the controller once the shot has stopped.
WP_PER_SAMPLE = 0.5
WP_AFTER_RESET = 0.6

#: The extended recording's scale flow, decaying as the cup settles.
_TAIL_VF = (3.0, 2.0, 1.0, 0.5, 0.0, 0.0)

LEVER_PROFILE: dict[str, Any] = {
    "id": "constructed-lever",
    "label": "Constructed lever decline",
    "type": "pro",
    "description": "A lever profile with a decline phase the shot never reaches",
    "temperature": 93.0,
    "phases": [
        {
            "name": "preinfusion",
            "phase": "preinfusion",
            "valve": 1,
            "duration": 7,
            "temperature": 93.0,
            "transition": {"type": "instant", "duration": 0, "adaptive": False},
            "pump": 40,
        },
        {
            "name": "soak",
            "phase": "preinfusion",
            "valve": 1,
            "duration": 10,
            "temperature": 93.0,
            "transition": {"type": "instant", "duration": 0, "adaptive": False},
            "pump": 0,
        },
        {
            "name": "ramp",
            "phase": "brew",
            "valve": 1,
            "duration": 10,
            "temperature": 93.0,
            "transition": {"type": "ease-in-out", "duration": 10, "adaptive": True},
            "pump": 100,
        },
        {
            "name": "decline",
            "phase": "brew",
            "valve": 1,
            "duration": 50,
            "temperature": 93.0,
            "transition": {"type": "linear", "duration": 50, "adaptive": True},
            "pump": 60,
            "targets": [{"type": "volumetric", "operator": "gte", "value": TARGET_YIELD_G}],
        },
    ],
}


def lever_shot() -> Slog:
    """The constructed shot: a version 7 log whose cup passes its target in the ramp."""
    real = parse_slog(BASE_FIXTURE.read_bytes())
    samples = []
    for index, original in enumerate(real.samples[: LAST_SAMPLE + 1]):
        weight, flow = _weight_and_flow(index)
        update: dict[str, Any] = {"v": weight, "vf": flow, "wp": _water(index)}
        if index >= FAST_FROM:
            # Puck flow follows the scale from the fast part on, so the first
            # drip is in the ramp and the curve is not a column of zeros there.
            update["pf"] = round(0.9 * flow, 2)
        if index > LAST_RAMP_SAMPLE:
            update["tp"] = 0.0
            update["si"] = (original.si or 0) | 0x0010  # extended recording
        samples.append(original.model_copy(update=update))

    transitions = [
        PhaseTransition(
            sample_index=0, phase_number=0, transition_reason=0, phase_name="preinfusion"
        ),
        # Each row's reason is why the *previous* phase ended: 5 is a duration.
        PhaseTransition(sample_index=29, phase_number=1, transition_reason=5, phase_name="soak"),
        PhaseTransition(sample_index=69, phase_number=2, transition_reason=5, phase_name="ramp"),
    ]
    for index, sample in enumerate(samples):
        phase = max(i for i, start in enumerate(PHASE_STARTS) if index >= start)
        sample.phase = phase
        sample.phase_name = transitions[phase].phase_name
    header = real.header.model_copy(
        update={
            "version": 7,
            "fields_mask": FIELDS_MASK_ALL,
            "sample_count": len(samples),
            "duration_ms": samples[-1].t or 0,
            "final_weight_g": RAMP_END_G,
            "transitions": transitions,
            # 1 is "Volumetric target": the shot ended on its weight.
            "final_exit_reason": 1,
            "profile_id": LEVER_PROFILE["id"],
            "profile_name": LEVER_PROFILE["label"],
        }
    )
    return dataclasses.replace(real, header=header, samples=samples, incomplete=False)


def _weight_and_flow(index: int) -> tuple[float, float]:
    """The cup (g) and the scale flow (g/s) at one sample."""
    if index <= 28:
        return 0.0, 0.0
    if index <= 68:
        return round(0.1 * (index - 28), 1), 0.4
    if index < FAST_FROM:
        return round(SOAK_END_G + 0.2 * (index - 68), 1), 0.8
    if index <= LAST_RAMP_SAMPLE:
        slow_end = SOAK_END_G + 0.2 * (FAST_FROM - 1 - 68)
        return round(slow_end + 1.0 * (index - (FAST_FROM - 1)), 1), 4.0
    return RAMP_END_G, _TAIL_VF[index - LAST_RAMP_SAMPLE - 1]


def _water(index: int) -> float:
    if index > LAST_RAMP_SAMPLE:
        return WP_AFTER_RESET
    return round(WP_PER_SAMPLE * max(0, index - 28), 1)


# ── the machines that record less ────────────────────────────────────


def without_scale(slog: Slog) -> Slog:
    """The shot as a machine with no scale writes it: zero weight, flag cleared."""
    samples = [
        s.model_copy(update={"v": 0.0, "vf": 0.0, "si": (s.si or 0) & ~0x0004})
        for s in slog.samples
    ]
    header = slog.header.model_copy(update={"final_weight_g": None})
    return dataclasses.replace(slog, samples=samples, header=header)


def without_pressure(slog: Slog) -> Slog:
    """The shot as a Standard board writes it: no pressure, pump flow, puck flow or counter."""
    zeroed = ("cp", "tp", "pf", "tf", "fl", "pr", "wp")
    samples = [
        s.model_copy(update={name: 0.0 for name in zeroed if getattr(s, name) is not None})
        for s in slog.samples
    ]
    return dataclasses.replace(slog, samples=samples)
