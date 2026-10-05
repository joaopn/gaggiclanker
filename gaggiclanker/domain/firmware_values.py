"""The values GaggiMate firmware v1.9.0's own shot analyzer shows for a shot.

The firmware's web UI (its shot analyzer) reports, for the whole shot and for
every phase, the machine's puck resistance ``pr`` (s·√bar/mL) and the liquid
resistance ``lr = pr · √cp`` (bar·s/mL) as start, end, min, max and a
time-weighted average, and the water the pump moved. They are computed here the
way the firmware computes them so that the numbers in gaggiclanker match the
ones a person sees on the machine's own page: validity bound, time weighting,
which samples each stat is taken over.

These are **not** the resistance level of :mod:`gaggiclanker.domain.diagnostics`
(``pr²`` over the flowing brew samples). They are informational: nothing grades,
searches or filters on them.

The reference is the analyzer's ``puckResistance.js`` and ``waterIntegration.js``
at firmware v1.9.0; ``tests/fixtures/firmware_values/`` pins numbers produced by
running that very code over the fixture shots.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import TypedDict

from gaggiclanker.domain.diagnostics import SampleDict

__all__ = [
    "MAX_VALID_PUCK_RESISTANCE",
    "FirmwareValues",
    "ResistanceStats",
    "compute_firmware_values",
    "resistance_stats",
    "water_pumped_ml",
]

#: The analyzer's ``MAX_VALID_PUCK_RESISTANCE``. The log encodes "no estimate yet"
#: as 0 or clamps it to 655.35, so anything at or above this is not a reading.
MAX_VALID_PUCK_RESISTANCE = 100.0

#: Decimals kept. ``pr`` itself has 0.01 resolution; three keeps the time-weighted
#: mean honest without printing float noise.
DECIMALS = 3


class ResistanceStats(TypedDict):
    start: float
    end: float
    min: float
    max: float
    avg: float


class PhaseFirmwareValues(TypedDict):
    phase_number: int
    pr: ResistanceStats | None
    lr: ResistanceStats | None


class FirmwareValues(TypedDict):
    """Stored as its own block of ``diagnostics_json``.

    ``pr`` and ``lr`` are ``None`` when the stream has no valid sample (the
    analyzer's empty stats). The water fields are ``None`` unless the shot
    recorded a complete cumulative ``wp`` (``.slog`` v7).
    """

    pr: ResistanceStats | None
    lr: ResistanceStats | None
    phases: list[PhaseFirmwareValues]
    water_pumped_ml: float | None
    water_minus_weight_g: float | None


def _pr(sample: Mapping[str, float]) -> float | None:
    value = sample.get("pr")
    if value is None or not math.isfinite(value):
        return None
    return value if 0.0 < value < MAX_VALID_PUCK_RESISTANCE else None


def _lr(sample: Mapping[str, float]) -> float | None:
    pr = _pr(sample)
    pressure = sample.get("cp")
    if pr is None or pressure is None or not math.isfinite(pressure) or pressure <= 0.0:
        return None
    return pr * math.sqrt(pressure)


def resistance_stats(
    samples: Sequence[Mapping[str, float]],
    getter: Callable[[Mapping[str, float]], float | None] = _pr,
) -> ResistanceStats | None:
    """Start, end, min, max and time-weighted average of a per-sample value.

    As the analyzer's ``getPuckResistanceStats``: a sample with no valid value
    breaks the weighting (the next valid sample has no predecessor, so the gap
    is not spanned), the weight of a valid sample is the time since the
    previous valid one, and with no positive weight at all the average is the
    first value. Time ``t`` is in milliseconds.
    """
    start: float | None = None
    end = 0.0
    low = math.inf
    high = -math.inf
    weighted = 0.0
    total = 0.0
    previous_t: float | None = None
    for sample in samples:
        value = getter(sample)
        if value is None:
            previous_t = None
            continue
        if start is None:
            start = value
        end = value
        low = min(low, value)
        high = max(high, value)
        t = sample.get("t")
        if previous_t is not None and t is not None:
            dt = (t - previous_t) / 1000.0
            if dt > 0:
                weighted += value * dt
                total += dt
        # The analyzer remembers the sample even when its time is not a number;
        # a sample without ``t`` therefore cannot anchor the next interval.
        previous_t = t
    if start is None:
        return None
    avg = weighted / total if total > 0 else start
    return ResistanceStats(
        start=round(start, DECIMALS),
        end=round(end, DECIMALS),
        min=round(low, DECIMALS),
        max=round(high, DECIMALS),
        avg=round(avg, DECIMALS),
    )


def water_pumped_ml(samples: Sequence[Mapping[str, float]]) -> float | None:
    """The pump's water for the shot, from the recorded cumulative ``wp``.

    ``wp`` is the controller's running total of what the pump has moved since
    the process started (reset at start, never decreasing), so the shot's total
    is the last reading less the first, as the analyzer takes it. The analyzer
    trusts the counter only when every sample carries it and it never goes
    down; otherwise it integrates flow, which is an estimate and not the
    machine's own count, so here the value is left out instead. Files before
    v7 do not record ``wp``.

    Every v7 sample carries ``wp``, but the controller fills it only on a board
    with a pressure sensor and a dimmed pump (it reads the pump's count inside its
    pressure branch) and sends 0 otherwise, and an all-zero counter would pass
    for "complete and non-decreasing". A counter that never rose over the shot is
    no measurement, so it is absent, not 0 ml (the analyzer itself shows 0 there:
    a deliberate departure, since a zero here reads as a fact about the shot).
    """
    if not samples:
        return None
    previous: float | None = None
    first: float | None = None
    for sample in samples:
        value = sample.get("wp")
        if value is None or not math.isfinite(value) or value < 0:
            return None
        if previous is not None and value < previous:
            return None
        if first is None:
            first = value
        previous = value
    assert first is not None and previous is not None
    if previous - first <= 0:
        return None
    return round(previous - first, DECIMALS)


def compute_firmware_values(
    samples: Sequence[SampleDict], final_weight_g: float | None, *, has_pressure: bool = True
) -> FirmwareValues:
    """The analyzer's numbers for the whole recording and for each phase.

    The whole shot is every recorded sample, the extended-recording tail after
    the pump stopped included; a phase is every sample stamped with its number
    (a phase number that appears twice is one group, as the analyzer groups).
    ``final_weight_g`` is the beverage weight the archive stores for the shot.
    ``has_pressure`` is the same gate the engine's own diagnostics use: on a board with
    no pressure sensor ``pr`` and ``cp`` are not measurements, so no resistance is
    reported. The water count is judged by its own samples instead: the controller
    sends a real count only from a board with a pressure sensor and a dimmed pump,
    and an all-zero counter is left out (see :func:`water_pumped_ml`).
    """
    if not has_pressure:
        samples = [{k: v for k, v in s.items() if k not in ("pr", "cp")} for s in samples]
    by_phase: dict[int, list[SampleDict]] = defaultdict(list)
    for sample in samples:
        by_phase[int(sample.get("phase", 0.0))].append(sample)
    phases: list[PhaseFirmwareValues] = [
        PhaseFirmwareValues(
            phase_number=number,
            pr=resistance_stats(group, _pr),
            lr=resistance_stats(group, _lr),
        )
        for number, group in sorted(by_phase.items())
    ]
    water = water_pumped_ml(samples)
    minus_weight = (
        round(water - final_weight_g, DECIMALS)
        if water is not None and final_weight_g is not None
        else None
    )
    return FirmwareValues(
        pr=resistance_stats(samples, _pr),
        lr=resistance_stats(samples, _lr),
        phases=phases,
        water_pumped_ml=water,
        water_minus_weight_g=minus_weight,
    )
