"""A small fixed language for "a number about a shot", and the one evaluator that computes it.

An expression names a channel (one column of the samples, or one derived from
them), a window of the shot, an operation and, optionally, what to divide by
and a threshold to compare with. It is a JSON object, validated strictly; the
person reads its one-line rendering. It is not a scripting language (no
arithmetic between expressions, no variables), not a store, and not a verdict:
a comparison answers held, failed or not measured, and what a failure *means*
belongs to whoever wrote the comparison.

Two numbers written as the same expression mean the same thing on every shot,
so the canonical form of an expression is its identity (:func:`method_id`).
The per-phase metrics the derivation stores are computed through
:func:`evaluate_in_phase`, so there is one code path for them and for anyone
who asks the language, not two.

**Absent sensors are zeros.** The firmware writes every field of every sample,
so a machine with no scale or no pressure sensor reaches here as columns of
zeros. The gates are the shot's own flags (``scale_connected``,
``has_pressure``), never the zeros, and a value the shot cannot have is absent
with a reason, never 0. **Measured, estimated and commanded channels are never
mixed**: the cup weight and the scale flow come from the scale alone, with no
fallback to the machine's own estimate, and every result carries its channel's
kind.

Pure Python over the dict-shaped samples the diagnostics engine works on; it
needs no database.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gaggiclanker.domain.models import PhaseTransition
from gaggiclanker.domain.phase_names import phase_key, same_phase

__all__ = [
    "ABSENT_REASONS",
    "CHANNELS",
    "OPS",
    "Compare",
    "Expression",
    "Result",
    "ShotData",
    "Window",
    "canonical_form",
    "compare_words",
    "evaluate",
    "evaluate_in_phase",
    "expression_unit",
    "method_id",
    "per_phase_method",
    "render",
    "water_by_sample",
]

type ChannelName = Literal[
    "cup_weight",
    "scale_flow",
    "puck_flow",
    "pump_flow",
    "target_flow",
    "pressure",
    "target_pressure",
    "temperature",
    "target_temperature",
    "water_pumped",
    "resistance",
]
type OpName = Literal[
    "mean",
    "min",
    "max",
    "at_start",
    "at_end",
    "change",
    "gained",
    "slope",
    "integral",
    "jitter",
    "duration",
    "time_to",
    "time_above",
    "time_below",
]
type Kind = Literal["measured", "estimated", "commanded"]
type AbsentReason = Literal[
    "not_recorded",
    "phase_not_reached",
    "no_such_phase",
    "no_target",
    "empty_window",
    "never_reached",
    "no_phase_table",
]

CHANNELS: tuple[str, ...] = (
    "cup_weight",
    "scale_flow",
    "puck_flow",
    "pump_flow",
    "target_flow",
    "pressure",
    "target_pressure",
    "temperature",
    "target_temperature",
    "water_pumped",
    "resistance",
)
OPS: tuple[str, ...] = (
    "mean",
    "min",
    "max",
    "at_start",
    "at_end",
    "change",
    "gained",
    "slope",
    "integral",
    "jitter",
    "duration",
    "time_to",
    "time_above",
    "time_below",
)
ABSENT_REASONS: tuple[str, ...] = (
    "not_recorded",
    "phase_not_reached",
    "no_such_phase",
    "no_target",
    "empty_window",
    "never_reached",
    "no_phase_table",
)

#: What each channel is: the sample column (or ``None`` where it is derived), its
#: unit, the decimals a result is rounded to (the ones the stored per-phase
#: numbers and the curve use), and where the number comes from.
_CHANNEL_SPEC: Mapping[str, tuple[str | None, str, int, Kind]] = {
    "cup_weight": ("v", "g", 1, "measured"),
    "scale_flow": ("vf", "g/s", 2, "measured"),
    "puck_flow": ("pf", "ml/s", 2, "estimated"),
    "pump_flow": ("fl", "ml/s", 2, "estimated"),
    "target_flow": ("tf", "ml/s", 2, "commanded"),
    "pressure": ("cp", "bar", 1, "measured"),
    "target_pressure": ("tp", "bar", 1, "commanded"),
    "temperature": ("ct", "°C", 1, "measured"),
    "target_temperature": ("tt", "°C", 1, "commanded"),
    "water_pumped": ("wp", "ml", 1, "estimated"),
    "resistance": (None, "", 2, "estimated"),
}
_NEEDS_SCALE = frozenset({"cup_weight", "scale_flow"})
_NEEDS_PRESSURE = frozenset({"puck_flow", "pump_flow", "target_flow", "pressure", "resistance"})
_TIME_OPS = frozenset({"duration", "time_to", "time_above", "time_below"})
_THRESHOLD_OPS = frozenset({"time_to", "time_above", "time_below"})

#: A share is the filed weight over its target, to a thousandth (a tenth of a percent).
_SHARE_DECIMALS = 3

#: A flow at or below this is not a flow a puck's resistance can be read from
#: (as in ``diagnostics._build_resistance``).
_RESISTANCE_FLOW_FLOOR = 0.1
#: The machine's own resistance at or above this is its clamp, not a reading (the same number
#: as in the diagnostics engine, which this module cannot import: it imports this one's caller).
_MACHINE_RESISTANCE_MAX = 100.0

# ── the expression ───────────────────────────────────────────────────

_STRICT = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

type NamedAnchorName = Literal["shot_start", "shot_end", "first_drip", "peak_pressure"]


class NamedAnchor(BaseModel):
    """A moment of the shot with a name, moved by an offset."""

    model_config = _STRICT
    anchor: NamedAnchorName
    offset_s: float = 0.0


class PhaseStartAnchor(BaseModel):
    """The first sample of a phase, moved by an offset."""

    model_config = _STRICT
    phase_start: str = Field(min_length=1, max_length=80)
    offset_s: float = 0.0


class PhaseEndAnchor(BaseModel):
    """The last sample of a phase, moved by an offset."""

    model_config = _STRICT
    phase_end: str = Field(min_length=1, max_length=80)
    offset_s: float = 0.0


class SecondsAnchor(BaseModel):
    """A time into the shot, moved by an offset."""

    model_config = _STRICT
    at_s: float
    offset_s: float = 0.0


type Anchor = NamedAnchorName | NamedAnchor | PhaseStartAnchor | PhaseEndAnchor | SecondsAnchor


class Window(BaseModel):
    """Which samples to read: the whole shot (``{}``), one phase, or a span between anchors."""

    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, populate_by_name=True
    )

    phase: str | None = Field(default=None, min_length=1, max_length=80)
    phase_number: int | None = Field(default=None, ge=0, le=255)
    start: Anchor | None = Field(default=None, alias="from")
    end: Anchor | None = Field(default=None, alias="to")

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        given = [
            self.phase is not None,
            self.phase_number is not None,
            self.start is not None or self.end is not None,
        ]
        if sum(given) > 1:
            raise ValueError("a window is the whole shot, one phase, or a span, not several")
        if (self.start is None) != (self.end is None):
            raise ValueError("a span needs both from and to")
        return self

    @property
    def is_phase(self) -> bool:
        return self.phase is not None or self.phase_number is not None


class Compare(BaseModel):
    """What a result is held against: one bound, or an inclusive range."""

    model_config = _STRICT

    op: Literal["<", "<=", ">", ">=", "between"]
    value: float | None = None
    low: float | None = None
    high: float | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.op == "between":
            if self.low is None or self.high is None or self.value is not None:
                raise ValueError("between takes low and high")
            if self.low > self.high:
                raise ValueError("low must not exceed high")
        elif self.value is None or self.low is not None or self.high is not None:
            raise ValueError(f"{self.op} takes one value")
        return self


class Expression(BaseModel):
    """A number about a shot, written down once."""

    model_config = _STRICT

    channel: ChannelName
    op: OpName
    window: Window = Field(default_factory=Window)
    relative_to: Literal["target_yield", "dose", "final_weight"] | None = None
    compare: Compare | None = None
    threshold: float | None = None
    direction: Literal["rising", "falling"] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.op in _THRESHOLD_OPS:
            if self.threshold is None:
                raise ValueError(f"{self.op} needs a threshold")
        elif self.threshold is not None:
            raise ValueError(f"{self.op} takes no threshold")
        if self.direction is not None and self.op != "time_to":
            raise ValueError("only time_to has a direction")
        if self.op == "gained":
            if self.channel not in ("cup_weight", "water_pumped"):
                raise ValueError("gained is for cup_weight and water_pumped only")
            if not self.window.is_phase:
                raise ValueError("gained is read over one phase")
        return self


class Result(BaseModel):
    """What an expression came to on one shot: a value, or an absence with its reason."""

    model_config = ConfigDict(extra="forbid")

    #: The expression, rendered as one sentence.
    sentence: str
    #: The canonical form of the expression (its identity).
    method: str
    value: float | None
    unit: str
    kind: Kind
    #: Whether the comparison held; ``None`` with no comparison, or on an absent value.
    held: bool | None = None
    absent: AbsentReason | None = None
    #: Why the value is absent, in words; ``None`` when there is a value.
    why: str | None = None


# ── canonical form ───────────────────────────────────────────────────


def _norm_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def _num(value: float) -> int | float:
    """A number with one spelling: ``6.0`` and ``6`` are the same threshold."""
    as_float = float(value)
    if as_float == 0:
        return 0
    return int(as_float) if as_float.is_integer() and abs(as_float) < 1e15 else as_float


def _anchor_dict(anchor: Anchor) -> str | dict[str, Any]:
    if isinstance(anchor, str):
        return anchor
    offset = anchor.offset_s
    if isinstance(anchor, NamedAnchor):
        if offset == 0:
            return anchor.anchor
        return {"anchor": anchor.anchor, "offset_s": _num(offset)}
    out: dict[str, Any]
    if isinstance(anchor, PhaseStartAnchor):
        out = {"phase_start": _norm_name(anchor.phase_start)}
    elif isinstance(anchor, PhaseEndAnchor):
        out = {"phase_end": _norm_name(anchor.phase_end)}
    else:
        out = {"at_s": _num(anchor.at_s)}
    if offset != 0:
        out["offset_s"] = _num(offset)
    return out


def _window_dict(window: Window) -> dict[str, Any]:
    if window.phase is not None:
        return {"phase": _norm_name(window.phase)}
    if window.phase_number is not None:
        return {"phase_number": window.phase_number}
    if window.start is not None and window.end is not None:
        return {"from": _anchor_dict(window.start), "to": _anchor_dict(window.end)}
    return {}


def _canonical_dict(expr: Expression) -> dict[str, Any]:
    out: dict[str, Any] = {"channel": expr.channel, "op": expr.op}
    window = _window_dict(expr.window)
    if window:
        out["window"] = window
    if expr.relative_to is not None:
        out["relative_to"] = expr.relative_to
    if expr.threshold is not None:
        out["threshold"] = _num(expr.threshold)
    if expr.op == "time_to" and expr.direction == "falling":
        out["direction"] = "falling"
    if expr.compare is not None:
        compare: dict[str, Any] = {"op": expr.compare.op}
        for name in ("value", "low", "high"):
            bound = getattr(expr.compare, name)
            if bound is not None:
                compare[name] = _num(bound)
        out["compare"] = compare
    return out


def _dump(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_form(expr: Expression) -> str:
    """The expression as one string: key order, defaults, case of a phase name and spelling
    of a number never change it, and two different expressions never share one."""
    return _dump(_canonical_dict(expr))


def method_id(expr: Expression) -> str:
    """The id of the computation behind an expression: its canonical form."""
    return canonical_form(expr)


def per_phase_method(channel: ChannelName, op: OpName) -> str:
    """The id of a per-phase metric: the expression read over each phase in turn."""
    return _dump({"channel": channel, "op": op, "window": "each_phase"})


# ── the shot, as the language reads it ───────────────────────────────


type Sample = Mapping[str, float]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def water_by_sample(samples: Sequence[Sample]) -> list[float | None]:
    """The pumped-water counter, with every sample at or after a reset left out.

    The controller zeroes the counter when it stops the pump on a weight target
    (tens of millilitres, then next to nothing on the following sample), so
    the samples that follow a fall are a counter for another thing and are not
    read. ``None`` marks a sample that is not read: a counter that was not
    recorded, or one that has been reset. A counter that never rose over the
    samples before any reset is no measurement (a board that does not fill it
    sends zeros), so every sample is ``None`` then.
    """
    values: list[float | None] = []
    previous: float | None = None
    reset = False
    for sample in samples:
        value = _number(sample.get("wp"))
        if value is None or value < 0:
            values.append(None)
            continue
        if reset or (previous is not None and value < previous):
            reset = True
            values.append(None)
            continue
        previous = value
        values.append(value)
    counted = [v for v in values if v is not None]
    if not counted or max(counted) <= 0:
        return [None] * len(values)
    return values


@dataclass(frozen=True, slots=True)
class PhaseSpan:
    """One row of the transition table, and the samples it holds."""

    number: int
    name: str
    start: int
    end: int


@dataclass(frozen=True)
class ShotData:
    """What the language reads of a shot: its samples, its phases, its gates and its filing."""

    samples: Sequence[Sample]
    phases: tuple[PhaseSpan, ...] = ()
    #: The linked profile's phase names, in order, or ``None`` with no profile.
    profile_phases: Sequence[str] | None = None
    has_pressure: bool = True
    scale_connected: bool = True
    #: The shot's final weight, for the dropout rule and ``relative_to: final_weight``.
    final_weight_g: float | None = None
    #: What ``relative_to`` divides by, from the shot's filing. ``None`` when not filed.
    target_yield_g: float | None = None
    dose_g: float | None = None
    _water: list[float | None] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_water", water_by_sample(self.samples))

    @classmethod
    def build(
        cls,
        samples: Sequence[Sample],
        transitions: Sequence[PhaseTransition],
        *,
        profile_phases: Sequence[str] | None = None,
        has_pressure: bool = True,
        scale_connected: bool = True,
        final_weight_g: float | None = None,
        target_yield_g: float | None = None,
        dose_g: float | None = None,
    ) -> ShotData:
        count = len(samples)
        spans: list[PhaseSpan] = []
        for index, transition in enumerate(transitions):
            start = min(transition.sample_index, count)
            end = (
                min(transitions[index + 1].sample_index, count)
                if index + 1 < len(transitions)
                else count
            )
            name = transition.phase_name.strip()
            if not name and profile_phases and transition.phase_number < len(profile_phases):
                name = str(profile_phases[transition.phase_number]).strip()
            spans.append(PhaseSpan(transition.phase_number, name, start, end))
        return cls(
            samples=samples,
            phases=tuple(spans),
            profile_phases=profile_phases,
            has_pressure=has_pressure,
            scale_connected=scale_connected,
            final_weight_g=final_weight_g,
            target_yield_g=target_yield_g,
            dose_g=dose_g,
        )


# ── windows ──────────────────────────────────────────────────────────


class _Absent(Exception):
    """An absence with its reason, raised inside the evaluator and turned into a result."""

    def __init__(self, reason: AbsentReason, why: str) -> None:
        super().__init__(why)
        self.reason: AbsentReason = reason
        self.why = why


@dataclass(frozen=True, slots=True)
class _Resolved:
    """A window: the sample indices it holds, where it starts in time, and which phase it is."""

    indices: range
    #: Its start, in milliseconds: the first sample's time for a phase or the whole shot,
    #: the from-anchor for a span.
    start_ms: float
    #: The position in the transition table when the window is one phase.
    phase_position: int | None = None
    #: Whether the window's last sample is the shot's last: the cup's dropout rule applies.
    ends_shot: bool = False


def _t(sample: Sample) -> float | None:
    return _number(sample.get("t"))


def _timed(data: ShotData) -> list[int]:
    return [i for i, s in enumerate(data.samples) if _t(s) is not None]


def _find_phase(data: ShotData, name: str | None, number: int | None) -> int:
    """The position in the transition table of the phase meant, or the reason it is not there."""
    if not data.phases:
        raise _Absent("no_phase_table", "the log has no phase table")
    if number is not None:
        for position, span in enumerate(data.phases):
            if span.number == number:
                return position
        profile = data.profile_phases
        if profile is not None and 0 <= number < len(profile):
            raise _Absent(
                "phase_not_reached",
                f"phase {number} is in the profile but the shot did not reach it",
            )
        raise _Absent("no_such_phase", f"there is no phase {number}")
    assert name is not None
    # The log holds the first 24 bytes of a phase's name, the profile all of it: one rule.
    for position, span in enumerate(data.phases):
        if same_phase(span.name, name):
            return position
    if data.profile_phases is not None and any(same_phase(n, name) for n in data.profile_phases):
        raise _Absent(
            "phase_not_reached", f"the profile has a phase {name!r} the shot did not reach"
        )
    raise _Absent("no_such_phase", f"there is no phase {name!r}")


def phase_window(data: ShotData, position: int) -> _Resolved:
    """The window of the phase at ``position`` in the transition table."""
    span = data.phases[position]
    indices = range(span.start, span.end)
    timed = [i for i in indices if _t(data.samples[i]) is not None]
    start = _t(data.samples[timed[0]]) if timed else None
    return _Resolved(
        indices=indices,
        start_ms=start if start is not None else 0.0,
        phase_position=position,
        ends_shot=span.end >= len(data.samples) > 0,
    )


def _anchor_ms(data: ShotData, anchor: Anchor) -> float:
    offset = 0.0
    if isinstance(anchor, str):
        kind: str = anchor
        arg: Any = None
    elif isinstance(anchor, NamedAnchor):
        kind, arg, offset = anchor.anchor, None, anchor.offset_s
    elif isinstance(anchor, PhaseStartAnchor):
        kind, arg, offset = "phase_start", anchor.phase_start, anchor.offset_s
    elif isinstance(anchor, PhaseEndAnchor):
        kind, arg, offset = "phase_end", anchor.phase_end, anchor.offset_s
    else:
        kind, arg, offset = "at_s", anchor.at_s, anchor.offset_s
    base = _anchor_base_ms(data, kind, arg)
    return base + offset * 1000.0


def _anchor_base_ms(data: ShotData, kind: str, arg: Any) -> float:
    timed = _timed(data)
    if kind == "at_s":
        return float(arg) * 1000.0
    if kind in ("shot_start", "shot_end"):
        if not timed:
            raise _Absent("empty_window", "the shot has no samples")
        return float(_t(data.samples[timed[0 if kind == "shot_start" else -1]]) or 0.0)
    if kind == "first_drip":
        if not data.has_pressure:
            raise _Absent("not_recorded", "the shot has no pressure sensor, so no puck flow")
        for i in timed:
            if data.samples[i].get("pf", 0.0) > 0.0:
                return float(_t(data.samples[i]) or 0.0)
        raise _Absent("empty_window", "no puck flow was ever recorded")
    if kind == "peak_pressure":
        if not data.has_pressure:
            raise _Absent("not_recorded", "the shot has no pressure sensor")
        best: int | None = None
        for i in timed:
            # The first sample of the highest pressure, as the diagnostics engine picks it.
            if "cp" in data.samples[i] and (
                best is None or data.samples[i]["cp"] > data.samples[best]["cp"]
            ):
                best = i
        if best is None or data.samples[best]["cp"] <= 0:
            raise _Absent("empty_window", "no pressure was recorded")
        return float(_t(data.samples[best]) or 0.0)
    position = _find_phase(data, arg, None)
    resolved = phase_window(data, position)
    in_phase = [i for i in resolved.indices if _t(data.samples[i]) is not None]
    if not in_phase:
        raise _Absent("empty_window", "the phase holds no samples")
    return float(_t(data.samples[in_phase[0 if kind == "phase_start" else -1]]) or 0.0)


def _resolve(data: ShotData, window: Window) -> _Resolved:
    if window.is_phase:
        return phase_window(data, _find_phase(data, window.phase, window.phase_number))
    if window.start is not None and window.end is not None:
        lo = _anchor_ms(data, window.start)
        hi = _anchor_ms(data, window.end)
        indices = [i for i in _timed(data) if lo <= float(_t(data.samples[i]) or 0.0) <= hi]
        found = _as_range(indices)
        return _Resolved(
            indices=found, start_ms=lo, ends_shot=bool(indices) and found.stop >= len(data.samples)
        )
    timed = _timed(data)
    if not timed:
        return _Resolved(indices=range(0), start_ms=0.0)
    return _Resolved(
        indices=range(0, len(data.samples)),
        start_ms=float(_t(data.samples[timed[0]]) or 0.0),
        ends_shot=True,
    )


def _as_range(indices: list[int]) -> range:
    # Samples are in time order, so a span's samples are one run of indices.
    return range(indices[0], indices[-1] + 1) if indices else range(0)


# ── series ───────────────────────────────────────────────────────────


type Series = list[tuple[float, float]]


def _gate(data: ShotData, channel: str) -> None:
    if channel in _NEEDS_SCALE and not data.scale_connected:
        raise _Absent("not_recorded", "the shot has no scale, and the weight is never estimated")
    if channel in _NEEDS_PRESSURE and not data.has_pressure:
        raise _Absent("not_recorded", "the shot has no pressure sensor")


def _value(data: ShotData, channel: str, index: int) -> float | None:
    """One sample's value of a channel, or ``None`` where it was not recorded."""
    sample = data.samples[index]
    if channel == "water_pumped":
        return data._water[index]
    if channel == "resistance":
        flow = sample.get("pf", 0.0)
        if "pf" not in sample or flow <= _RESISTANCE_FLOW_FLOOR:
            return None
        machine = sample.get("pr", 0.0)
        if 0.0 < machine < _MACHINE_RESISTANCE_MAX:
            return machine * machine
        if "cp" not in sample:
            return None
        return sample["cp"] / (flow * flow)
    column = _CHANNEL_SPEC[channel][0]
    assert column is not None
    value = sample.get(column)
    if value is None:
        return None
    if channel == "target_temperature" and value <= 0:
        # A target of 0 °C is the firmware's "none set", not a commanded temperature.
        return None
    return float(value)


def _series(data: ShotData, channel: str, indices: Sequence[int]) -> Series:
    out: Series = []
    for i in indices:
        t = _t(data.samples[i])
        value = _value(data, channel, i)
        if t is not None and value is not None:
            out.append((t, value))
    return out


# ── the operations ───────────────────────────────────────────────────


def _at_end_of_phase(data: ShotData, channel: str, position: int) -> float | None:
    """The last recorded value of a channel in one phase, with the weight's dropout rule."""
    resolved = phase_window(data, position)
    series = _series(data, channel, resolved.indices)
    if not series:
        return None
    last = series[-1][1]
    if (
        channel == "cup_weight"
        and resolved.ends_shot
        and last <= 0
        and (data.final_weight_g or 0.0) > 0
    ):
        # The scale reads 0 once the cup is lifted or it resets, so a shot can end in
        # zeros: the last phase then ends at the weight the yield is, not at a cup that
        # vanished at the last sample.
        return float(data.final_weight_g or 0.0)
    return last


def _gained(data: ShotData, channel: str, position: int) -> float:
    here = _at_end_of_phase(data, channel, position)
    if here is None:
        raise _Absent("empty_window", "no sample of the channel was recorded in the phase")
    before = 0.0
    for earlier in range(position - 1, -1, -1):
        found = _at_end_of_phase(data, channel, earlier)
        if found is not None:
            before = found
            break
    return here - before


def _slope(series: Series) -> float:
    n = len(series)
    xs = [t / 1000.0 for t, _ in series]
    ys = [v for _, v in series]
    mean_x = math.fsum(xs) / n
    mean_y = math.fsum(ys) / n
    denominator = math.fsum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        raise _Absent("empty_window", "the samples are all at one moment")
    return math.fsum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator


def _ms_beyond(series: Series, threshold: float, *, above: bool) -> float:
    """Milliseconds spent beyond a threshold, over intervals with both ends beyond it."""
    total = 0.0
    for (t0, v0), (t1, v1) in pairwise(series):
        beyond = (
            (v0 > threshold and v1 > threshold) if above else (v0 < threshold and v1 < threshold)
        )
        if beyond:
            total += t1 - t0
    return total


def _seconds(milliseconds: float) -> float:
    """Milliseconds as seconds to two decimals, rounded once from the whole milliseconds.

    Times are logged in whole milliseconds, so a sum of them is exact; rounding the
    hundredths of a second from it, not from a float of seconds, keeps a value at a
    .xx5 boundary from being decided by how the float happens to be written.
    """
    return round(milliseconds / 10) / 100


def _round(value: float, decimals: int) -> float:
    return round(value, decimals)


def _unit(expr: Expression) -> str:
    channel_unit = _CHANNEL_SPEC[expr.channel][1]
    op = expr.op
    if expr.relative_to is not None:
        return "share"
    if op in _TIME_OPS:
        return "s"
    if op == "slope":
        return f"{channel_unit}/s" if channel_unit else "/s"
    if op == "integral":
        return f"{channel_unit}·s" if channel_unit else "s"
    return channel_unit


def expression_unit(expr: Expression) -> str:
    """The unit an expression's value is in (``share`` for one relative to something).

    Known from the expression alone, with no shot: a limit is worded in it before any shot has
    been read (a Set version's override, shown beside the profile's own limit).
    """
    return _unit(expr)


def _compute(expr: Expression, data: ShotData, resolved: _Resolved) -> float:
    channel, op = expr.channel, expr.op
    decimals = _CHANNEL_SPEC[channel][2]
    if op != "duration":
        _gate(data, channel)

    if op == "duration":
        times = [t for i in resolved.indices if (t := _t(data.samples[i])) is not None]
        if not times:
            raise _Absent("empty_window", "no sample falls in the window")
        return _seconds(times[-1] - times[0])

    if op == "gained":
        assert resolved.phase_position is not None
        _water_gate(data, channel)
        return _round(_gained(data, channel, resolved.phase_position), decimals)

    series = _series(data, channel, resolved.indices)
    if not series:
        if channel == "water_pumped" and not any(v is not None for v in data._water):
            raise _Absent("not_recorded", "the shot has no pumped-water counter")
        raise _Absent("empty_window", "no recorded sample falls in the window")
    values = [v for _, v in series]
    end_value = values[-1]
    if (
        channel == "cup_weight"
        and resolved.ends_shot
        and end_value <= 0
        and (data.final_weight_g or 0.0) > 0
    ):
        # The scale reads 0 once the cup is lifted or it resets, so a shot can end in
        # zeros: a window that ends with the shot ends at the weight the yield is, not at a
        # cup that vanished at the last sample.
        end_value = float(data.final_weight_g or 0.0)

    if op == "mean":
        # The plain sum, not an exact one: the per-phase numbers stored since before the
        # language were summed this way, and a mean must not move in its last digit.
        result = sum(values) / len(values)
    elif op == "min":
        result = min(values)
    elif op == "max":
        result = max(values)
    elif op == "at_start":
        result = values[0]
    elif op == "at_end":
        result = end_value
    elif op == "change":
        result = end_value - values[0]
    elif op == "slope":
        if len(series) < 2:
            raise _Absent("empty_window", "a slope needs two recorded samples")
        result = _slope(series)
    elif op == "integral":
        # Summed over milliseconds and divided by 1000 once.
        result = (
            math.fsum((t1 - t0) * (v0 + v1) / 2 for (t0, v0), (t1, v1) in pairwise(series)) / 1000.0
        )
    elif op == "jitter":
        if len(series) < 2:
            raise _Absent("empty_window", "jitter needs two recorded samples")
        result = statistics.pstdev([v1 - v0 for (_, v0), (_, v1) in pairwise(series)])
    elif op == "time_to":
        assert expr.threshold is not None
        rising = expr.direction != "falling"
        for t, v in series:
            if (v >= expr.threshold) if rising else (v <= expr.threshold):
                return _seconds(max(0.0, t - resolved.start_ms))
        raise _Absent("never_reached", "the channel never reached the threshold")
    else:
        assert expr.threshold is not None
        return _seconds(_ms_beyond(series, expr.threshold, above=op == "time_above"))
    return _round(result, decimals)


def _water_gate(data: ShotData, channel: str) -> None:
    if channel == "water_pumped" and not any(v is not None for v in data._water):
        raise _Absent("not_recorded", "the shot has no pumped-water counter")


def _relative(expr: Expression, data: ShotData, value: float) -> float:
    which = expr.relative_to
    divisor = {
        "target_yield": data.target_yield_g,
        "dose": data.dose_g,
        "final_weight": data.final_weight_g,
    }[which or "target_yield"]
    if divisor is None or divisor <= 0:
        raise _Absent(
            "no_target", f"the shot has no {(which or '').replace('_', ' ')} to divide by"
        )
    return _round(value / divisor, _SHARE_DECIMALS)


def _held(compare: Compare, value: float) -> bool:
    # Compared on a rounded difference: 44.0 against 40 x 1.1 must not be decided by a
    # float product.
    def diff(bound: float) -> float:
        return round(value - bound, 9)

    if compare.op == "between":
        assert compare.low is not None and compare.high is not None
        return diff(compare.low) >= 0 and diff(compare.high) <= 0
    assert compare.value is not None
    d = diff(compare.value)
    return {"<": d < 0, "<=": d <= 0, ">": d > 0, ">=": d >= 0}[compare.op]


def _finish(expr: Expression, data: ShotData, resolved: _Resolved) -> Result:
    kind = _CHANNEL_SPEC[expr.channel][3]
    sentence = render(expr, data)
    method = canonical_form(expr)
    unit = _unit(expr)
    try:
        value = _compute(expr, data, resolved)
        if expr.relative_to is not None:
            value = _relative(expr, data, value)
    except _Absent as gone:
        return _absent(sentence, method, unit, kind, gone)
    held = _held(expr.compare, value) if expr.compare is not None else None
    return Result(sentence=sentence, method=method, value=value, unit=unit, kind=kind, held=held)


def _absent(sentence: str, method: str, unit: str, kind: Kind, gone: _Absent) -> Result:
    return Result(
        sentence=sentence,
        method=method,
        value=None,
        unit=unit,
        kind=kind,
        held=None,
        absent=gone.reason,
        why=gone.why,
    )


def evaluate(expr: Expression, data: ShotData) -> Result:
    """The expression's value on one shot, or its absence with a reason. Never raises on a shot."""
    try:
        resolved = _resolve(data, expr.window)
    except _Absent as gone:
        return _absent(
            render(expr, data),
            canonical_form(expr),
            _unit(expr),
            _CHANNEL_SPEC[expr.channel][3],
            gone,
        )
    return _finish(expr, data, resolved)


def evaluate_in_phase(expr: Expression, data: ShotData, position: int) -> Result:
    """An expression over the phase at ``position`` of the transition table.

    How the derivation reads each phase in turn, including phases that share a
    name, which a name cannot tell apart.
    """
    return _finish(expr, data, phase_window(data, position))


# ── the one-line rendering ───────────────────────────────────────────

_CHANNEL_WORDS: Mapping[str, str] = {
    "cup_weight": "cup weight",
    "scale_flow": "scale flow",
    "puck_flow": "the machine's estimate of puck flow",
    "pump_flow": "the machine's estimate of pump flow",
    "target_flow": "the flow the profile commanded",
    "pressure": "pressure",
    "target_pressure": "the pressure the profile commanded",
    "temperature": "temperature",
    "target_temperature": "the temperature the profile commanded",
    "water_pumped": "the machine's estimate of water pumped",
    "resistance": "the puck resistance worked out from the machine's estimate of puck flow",
}

_RELATIVE_WORDS: Mapping[str, str] = {
    "target_yield": "the target yield",
    "dose": "the dose",
    "final_weight": "the shot's own final weight",
}

_COMPARE_WORDS: Mapping[str, str] = {
    "<": "under",
    "<=": "at most",
    ">": "over",
    ">=": "at least",
}


def _g(value: float) -> str:
    return f"{_num(value)}"


def _anchor_words(anchor: Anchor) -> str:
    base: str
    offset: float
    if isinstance(anchor, str):
        base, offset = anchor, 0.0
    elif isinstance(anchor, NamedAnchor):
        base, offset = anchor.anchor, anchor.offset_s
    elif isinstance(anchor, PhaseStartAnchor):
        base, offset = f"phase_start:{anchor.phase_start.strip()}", anchor.offset_s
    elif isinstance(anchor, PhaseEndAnchor):
        base, offset = f"phase_end:{anchor.phase_end.strip()}", anchor.offset_s
    else:
        base, offset = f"at_s:{_g(anchor.at_s)}", anchor.offset_s
    words = {
        "shot_start": "the start of the shot",
        "shot_end": "the end of the shot",
        "first_drip": "the first drip",
        "peak_pressure": "the moment of peak pressure",
    }.get(base)
    if words is None:
        kind, _, arg = base.partition(":")
        words = {
            "phase_start": f"the start of the {arg}",
            "phase_end": f"the end of the {arg}",
            "at_s": f"{arg} s into the shot",
        }[kind]
    if offset > 0:
        return f"{_g(offset)} s after {words}"
    if offset < 0:
        return f"{_g(-offset)} s before {words}"
    return words


def _duplicate_names(data: ShotData | None) -> set[str]:
    if data is None:
        return set()
    seen: set[str] = set()
    duplicated: set[str] = set()
    for span in data.phases:
        name = phase_key(span.name)
        (duplicated if name in seen else seen).add(name)
    return duplicated


def _window_words(window: Window, duplicated: set[str]) -> str:
    if window.phase is not None:
        name = window.phase.strip()
        return f"the first {name}" if phase_key(name) in duplicated else f"the {name}"
    if window.phase_number is not None:
        return f"phase {window.phase_number}"
    if window.start is not None and window.end is not None:
        return f"the span from {_anchor_words(window.start)} to {_anchor_words(window.end)}"
    return "the whole shot"


def render(expr: Expression, data: ShotData | None = None) -> str:
    """The expression as one sentence a person reads.

    With the shot, a phase name that two phases share is read as "the first ramp",
    which is the one the language means.
    """
    duplicated = _duplicate_names(data)
    channel = _CHANNEL_WORDS[expr.channel]
    where = _window_words(expr.window, duplicated)
    # "in" reads a whole shot or a span; a phase takes "in the ramp" too.
    op = expr.op
    threshold = _g(expr.threshold) if expr.threshold is not None else ""
    unit = _CHANNEL_SPEC[expr.channel][1]
    suffix = f" {unit}" if unit else ""
    text = {
        "mean": f"mean of {channel} over {where}",
        "min": f"lowest value of {channel} in {where}",
        "max": f"highest value of {channel} in {where}",
        "at_start": f"{channel} at the start of {where}",
        "at_end": f"{channel} at the end of {where}",
        "change": f"change in {channel} across {where}",
        "gained": f"{channel} gained in {where}",
        "slope": f"slope of {channel} over {where}",
        "integral": f"integral of {channel} over {where}",
        "jitter": f"jitter of {channel} (the spread of its sample-to-sample changes) over {where}",
        "duration": f"duration of {where}",
        "time_to": (
            f"time from the start of {where} until {channel} "
            f"{'falls to' if expr.direction == 'falling' else 'rises to'} {threshold}{suffix}"
        ),
        "time_above": f"time {channel} was above {threshold}{suffix} in {where}",
        "time_below": f"time {channel} was below {threshold}{suffix} in {where}",
    }[op]
    if expr.relative_to is not None:
        text += f", as a share of {_RELATIVE_WORDS[expr.relative_to]}"
    if expr.compare is not None:
        text += f", {compare_words(expr.compare)}"
    return text


def compare_words(compare: Compare) -> str:
    """A comparison as words: "at most 0.15", "between 4 and 6"."""
    if compare.op == "between":
        return f"between {_g(compare.low or 0.0)} and {_g(compare.high or 0.0)}"
    return f"{_COMPARE_WORDS[compare.op]} {_g(compare.value or 0.0)}"
