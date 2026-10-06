"""Every piece of information a shot carries, as one ordered catalogue.

A chat is told about a shot in two tiers. **Base** is what it sees without
asking — every shot in a Set conversation's opening context, every result of
the shot search, `get_shot` — and **extended** is what it asks for by name
(`get_shot_extended`; `get_shot_full` is both). An item may also be
**excluded**, and then no chat ever sees it. Which tier an item sits in is a
per-item choice with a default here; :func:`effective_tiers` is the one place
that choice is read, and everything that renders a shot goes through it.

Each :class:`Item` is one metric, written once: a stable key, its group and
name, what it **means** (the glossary the chat prompts carry is generated from
these texts, so an item and its explanation cannot drift apart), its default
tier, whether it is locked, and the function that renders it from the loaded
shot. Three kinds, because a shot has three shapes of information:

* a **shot** item has at most one value per shot (`Shot time: 54.6 s`);
* a **phase** item has one per phase that has it, and is skipped for a phase
  that does not (`taper -0.14 bar/s, 0.24 bar/s SMOOTH` on the decline);
* a **curve** item is one channel of the sample table — every channel of the
  tier is one column of one table, whose rows are a shape-preserving subset
  of the samples (:mod:`~gaggiclanker.shotinfo.downsample`), the same for
  every channel.

Two rules are load-bearing everywhere below.

**Absent, never zero.** The engine writes ``0.0`` for an average over no
samples and for a peak of an empty list.
A value the machine did not record — no scale, no pressure sensor, too short a
window — is left out of the rendering, never shown as a measurement of zero,
because a model reads "0.0 bar" as a fact about the shot. Each accessor says
which zero it is guarding against.

**The numbers are the engine's, and they are only numbers.** A value is shown at
the precision the diagnostics round it to (or the Set page's vocabulary gives
it, for the five measures the Set page shows), with no grade beside it: no band
label, no verdict. What a number means for a given profile is for the reader.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

import structlog

from gaggiclanker.domain import diagnostics as engine
from gaggiclanker.domain.models import PHASE_EXIT_REASONS
from gaggiclanker.domain.phase_metrics import (
    FAST_FLOW_PRESSURE_SHARE,
    FAST_FLOW_SCALE_FLOW_G_S,
    FAST_FLOW_WINDOW_MS,
)
from gaggiclanker.domain.ratio import brew_ratio
from gaggiclanker.domain.signature import Check
from gaggiclanker.domain.slog import (
    FLOW_SCALE,
    PRESSURE_SCALE,
    RESISTANCE_SCALE,
    TEMP_SCALE,
    WEIGHT_SCALE,
)
from gaggiclanker.domain.vocab import (
    FLAVOR_LABELS,
    SpreadMeasure,
    flavor_path,
    vocabulary,
)
from gaggiclanker.domain.warnings import (
    OVER_TARGET_SHARE,
    UNDER_TARGET_SHARE,
)
from gaggiclanker.shotinfo.facts import ShotFacts, number
from gaggiclanker.shotinfo.methods import METHODS

if TYPE_CHECKING:
    from gaggiclanker.db.connection import Database

__all__ = [
    "ALSO_SERVED",
    "CATALOGUE",
    "CHECKS_GROUP",
    "GROUPS",
    "GROUP_NOTES",
    "ITEMS",
    "MEASURED_GROUPS",
    "REVIEW_GROUP",
    "TIERS",
    "Channel",
    "FieldValue",
    "Item",
    "ShotTier",
    "Tier",
    "default_tiers",
    "effective_tiers",
    "keys_in",
]

log = structlog.get_logger(__name__)

#: Where an item sits. Excluded is a tier rather than an absence, because it
#: is a choice somebody made about an item that exists.
type Tier = Literal["base", "extended", "excluded"]
TIERS: tuple[Tier, ...] = ("base", "extended", "excluded")

#: What a rendering asks for: one tier, or both at once.
type ShotTier = Literal["base", "extended", "full"]

type ShotRender = Callable[[ShotFacts], str | None]
type PhaseRender = Callable[[ShotFacts, Mapping[str, Any]], str | None]

#: What a field's value is: a number, a word, a flag, or a small structure (the
#: warnings, the firmware's five numbers).
type Value = float | int | str | bool | list[Any] | dict[str, Any]
type ShotValue = Callable[[ShotFacts], Value | None]
type PhaseValue = Callable[[ShotFacts, Mapping[str, Any]], Value | None]

#: Where a field's value came from: computed from the shot's bytes, or (later)
#: written by an agent.
type Source = Literal["computed", "agent"]


@dataclass(frozen=True, slots=True)
class Channel:
    """One column of the sample table."""

    #: The `shot_samples` column it is read from.
    field: str
    #: The column header, unit included.
    header: str
    #: Decimals, from the firmware's own quantisation of the field: a value
    #: stored in tenths is shown in tenths, so nothing is invented or lost.
    decimals: int
    #: Only recorded with a scale, or only real with a pressure sensor. A
    #: channel gated off for a shot is left out of its table, since the
    #: firmware records such a channel as a column of zeros.
    needs: Literal["scale", "pressure"] | None = None


@dataclass(frozen=True, slots=True, eq=False)
class Item:
    """One metric: what it is, what it means, where it sits, how it renders."""

    key: str
    group: str
    #: The name a person reads on the settings page.
    name: str
    #: What a rendered line starts with, and what the glossary explains.
    label: str
    meaning: str
    default_tier: Tier
    locked: bool = False
    shot: ShotRender | None = None
    phase: PhaseRender | None = None
    channel: Channel | None = None
    #: The id of the computation behind the value (see :mod:`~gaggiclanker.shotinfo.methods`).
    #: A change to the computation changes the id, so a value read under one id
    #: is never compared with a value read under another as if they were one field.
    method: str = ""
    #: The unit of the value, as the structured accessor reports it.
    unit: str = ""
    source: Source = "computed"
    #: The structured accessor beside the text renderer: the value itself, not
    #: its sentence. Left out where the value is the sentence (a word, a date).
    shot_value: ShotValue | None = None
    phase_value: PhaseValue | None = None
    #: What a chat reads for a phase item where it is not the sentence the fields route serves
    #: beside the value (the cup at a phase's end, with its share of the target).
    chat_phase: PhaseRender | None = None

    @property
    def kind(self) -> Literal["shot", "phase", "curve"]:
        if self.channel is not None:
            return "curve"
        return "phase" if self.phase is not None else "shot"

    def field(self, facts: ShotFacts, phase: Mapping[str, Any] | None = None) -> FieldValue | None:
        """The item on one shot (or one of its phases) as a structured field, or ``None``.

        ``None`` exactly when the text renderer has nothing to say: a value the
        machine did not record is absent here, never zero. ``text`` is what a
        chat reads; ``value`` is the number (or word, or structure) behind it.
        """
        if phase is None:
            text = self.shot(facts) if self.shot is not None else None
            value = self.shot_value(facts) if self.shot_value is not None else None
        else:
            text = self.phase(facts, phase) if self.phase is not None else None
            value = self.phase_value(facts, phase) if self.phase_value is not None else None
        if not text:
            return None
        return FieldValue(
            key=self.key,
            value=value if value is not None else text,
            unit=self.unit,
            phase=_phase_label(phase),
            window=_phase_window(phase),
            method=self.method,
            source=self.source,
            text=text,
        )


@dataclass(frozen=True, slots=True)
class FieldValue:
    """One item's value on one shot: the field contract.

    ``phase`` and ``window`` say what the value is about (a phase of the shot,
    the span of seconds it covers), ``method`` which computation made it and
    ``source`` who. Two values with the same ``key`` and ``method`` are the same
    field and may be compared.
    """

    key: str
    value: Value
    unit: str
    #: The phase's number and name, or ``None`` for a shot-wide value.
    phase: dict[str, Any] | None
    #: ``{from_s, to_s}`` of the phase, or ``None`` for a shot-wide value.
    window: dict[str, float] | None
    method: str
    source: Source
    #: The sentence a chat is given.
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "unit": self.unit,
            "phase": self.phase,
            "window": self.window,
            "method": self.method,
            "source": self.source,
            "text": self.text,
        }


def _phase_label(phase: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if phase is None:
        return None
    index = phase.get("phase_number")
    return {
        "number": index if isinstance(index, int) and not isinstance(index, bool) else None,
        "name": str(phase.get("name") or "").strip(),
    }


def _phase_window(phase: Mapping[str, Any] | None) -> dict[str, float] | None:
    if phase is None:
        return None
    start = number(phase.get("start_time_seconds"))
    length = number(phase.get("duration_seconds"))
    if start is None or length is None:
        return None
    return {"from_s": start, "to_s": round(start + length, 1)}


# ── formatting ───────────────────────────────────────────────────────


def _fixed(value: float, decimals: int) -> str:
    """A number at a fixed precision, never "-0.00"."""
    text = f"{value:.{decimals}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def _qty(value: float | None, decimals: int, unit: str = "") -> str | None:
    if value is None:
        return None
    text = _fixed(value, decimals)
    return f"{text} {unit}" if unit else text


def _positive(value: float | None) -> float | None:
    """The engine's ``0.0`` for "nothing to average", read as nothing."""
    return value if value is not None and value > 0 else None


def _quote(text: str | None) -> str | None:
    written = (text or "").strip()
    return f'"{written}"' if written else None


def _notes(notes: list[str]) -> str | None:
    """Flavour-wheel notes, each with its path from the centre of the wheel."""
    paths = [flavor_path(note) if note in FLAVOR_LABELS else note for note in notes]
    return "; ".join(paths) if paths else None


# ── the Set page's vocabulary ────────────────────────────────────────

#: The five measures the Set page and its spread are read in, with the same
#: units and precision, so "Shot time: 33.0 s" here and on the page are one
#: reading.
_MEASURES = {term.value: term for term in vocabulary().spread_measures}
_DECISIONS = {term.value: term.label for term in vocabulary().decisions}
_BALANCES = {term.value: term.label for term in vocabulary().balances}


def _measure(measure: SpreadMeasure, value: float | None) -> str | None:
    term = _MEASURES[measure]
    return _qty(value, term.decimals, term.unit)


# ── the numbers the search filters and sorts on ──────────────────────
#
# One function per searchable base item. The renderer and the search both call
# these, so a shot found by "shot time over 30 s" shows a shot time over 30 s.


def shot_time(f: ShotFacts) -> float | None:
    """The header's duration; a zero is a header that never got a time."""
    return f.shot.duration_ms / 1000 if f.shot.duration_ms > 0 else None


def yield_g(f: ShotFacts) -> float | None:
    """The machine's final weight. A machine with no scale records none."""
    return _positive(f.shot.final_weight_g)


def first_drip(f: ShotFacts) -> float | None:
    """Already nullable in the engine, so a ``0.0`` here is a real reading.

    Needs a puck flow to see it in: a board with no pressure sensor has none.
    """
    return f.summary_value("flow", "time_to_first_drip_s") if f.puck_flow_recorded else None


def peak_pressure(f: ShotFacts) -> float | None:
    return _positive(f.summary_value("pressure", "max_bar")) if f.has_pressure else None


def brew_flow(f: ShotFacts) -> float | None:
    """A ``0.00`` is shown when puck flow was recorded: a choked puck is a reading."""
    return f.section_value("extraction", "flow_avg_brew_ml_s") if f.puck_flow_recorded else None


def resistance_level(f: ShotFacts) -> float | None:
    """The puck's average resistance; absent where the shot has no flow to divide by."""
    return _resistance_avg(f)


def pressure_adherence(f: ShotFacts) -> float | None:
    """RMSE of measured pressure against the profile's pressure target, in bar."""
    return _pressure_rmse(f)


def flow_adherence(f: ShotFacts) -> float | None:
    """RMSE of pump flow against the profile's flow target, in ml/s."""
    return _flow_rmse(f)


def rating(f: ShotFacts) -> float | None:
    value = f.judgement.rating if f.judgement is not None else None
    return float(value) if value is not None else None


def dose_in(f: ShotFacts) -> float | None:
    return f.judgement.dose_in_g if f.judgement is not None else None


def dose_out(f: ShotFacts) -> float | None:
    return f.judgement.dose_out_g if f.judgement is not None else None


def ratio(f: ShotFacts) -> float | None:
    """The yield over the dose, to two decimals.

    The dose is the judgement's when the person entered one and the filed
    version's otherwise; the yield is the judgement's dose out when entered and
    the scale's otherwise. No dose anywhere, or no yield, and there is no ratio.
    """
    return brew_ratio(
        judged_dose_g=dose_in(f),
        version_dose_g=f.version.dose_g if f.version is not None else None,
        judged_yield_g=dose_out(f),
        scale_yield_g=f.shot.volume_g,
    )


def balance(f: ShotFacts) -> str | None:
    return f.judgement.balance if f.judgement is not None else None


# ── accessors for the rest ───────────────────────────────────────────


def _counted(f: ShotFacts) -> str:
    decision = f.judgement.decision if f.judgement is not None else None
    if f.shot.quarantined:
        return "not counted: quarantined"
    if f.shot.incomplete:
        return "not counted: stopped early"
    if decision == "discard":
        return "not counted: discarded"
    return "counted"


def _exit_reason(f: ShotFacts) -> str | None:
    code = f.shot.final_exit_reason
    # 0 is the firmware's own "Unknown": nothing was recorded.
    return PHASE_EXIT_REASONS.get(code) if code else None


def _temperature(f: ShotFacts, key: str) -> float | None:
    """A temperature statistic; ``0.0`` means no temperature was recorded."""
    return _positive(f.summary_value("temperature", key))


def _flow_summary(f: ShotFacts, key: str) -> float | None:
    # With no puck flow recorded the engine averages and sums nothing into 0.0.
    return f.summary_value("flow", key) if f.puck_flow_recorded else None


def _pressure_summary(f: ShotFacts, key: str) -> float | None:
    return f.summary_value("pressure", key) if f.has_pressure else None


def _resistance_avg(f: ShotFacts) -> float | None:
    """Puck resistance; ``0.0`` is an average over no sample with any flow."""
    if f.full:
        return _positive(f.section_value("resistance", "avg"))
    return _positive(f.flat("resistance_avg"))


#: How a resistance level says where its number came from, in the rendering the
#: agent and the review read and (as words) on the shot page.
_RESISTANCE_SOURCE_TEXT: Mapping[str, str] = MappingProxyType(
    {"machine": "from the machine", "computed": "computed from pressure and flow"}
)


def _with_resistance_source(text: str | None, source: object) -> str | None:
    """A level's text with its source; a shot stored before the source existed has none."""
    said = _RESISTANCE_SOURCE_TEXT.get(source) if isinstance(source, str) else None
    return f"{text}, {said}" if text and said else text


def _resistance_level_text(f: ShotFacts) -> str | None:
    text = _qty(_resistance_avg(f), 2)
    if f.full:
        block = f.section("resistance")
        source = block.get("source") if block is not None else None
    else:
        source = f.diagnostics.get("resistance_source")
    return _with_resistance_source(text, source)


def _resistance_slope(f: ShotFacts) -> float | None:
    if _resistance_avg(f) is None:
        return None
    return f.section_value("resistance", "slope") if f.full else f.flat("resistance_slope")


def _pressure_rmse(f: ShotFacts) -> float | None:
    if f.full:
        return f.section_value("profile_compliance", "pressure_rmse_bar")
    return f.flat("pressure_rmse_bar")


def _flow_rmse(f: ShotFacts) -> float | None:
    if f.full:
        return f.section_value("profile_compliance", "flow_rmse_ml_s")
    return f.flat("flow_rmse_ml_s")


def _compliance(f: ShotFacts, full_key: str, flat_key: str | None) -> float | None:
    if f.full:
        return f.section_value("profile_compliance", full_key)
    return f.flat(flat_key) if flat_key is not None else None


# ── per-phase accessors ──────────────────────────────────────────────


def _phase_diag(phase: Mapping[str, Any]) -> Mapping[str, Any]:
    value = phase.get("diagnostics")
    return value if isinstance(value, dict) else {}


def _phase_number(phase: Mapping[str, Any], key: str) -> float | None:
    return number(phase.get(key))


def _phase_diag_number(phase: Mapping[str, Any], key: str) -> float | None:
    return number(_phase_diag(phase).get(key))


def _phase_name(f: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    # A log with no phase table has no phases of its own: the single one the diagnostics
    # engine makes of it is not a phase of the shot, and must not read as one.
    if f.metrics.get("per_phase") is False:
        return None
    index = phase.get("phase_number")
    name = str(phase.get("name") or "").strip()
    if isinstance(index, bool) or not isinstance(index, int):
        return name or None
    return f"{index} · {name}" if name else str(index)


def _phase_duration(f: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    # As the name: with no phase table the one phase is the engine's, not the shot's.
    if f.metrics.get("per_phase") is False:
        return None
    return _qty(_phase_number(phase, "duration_seconds"), 1, "s")


def _phase_type(_: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    value = _phase_diag(phase).get("phase_type")
    return value if isinstance(value, str) and value else None


def _phase_pressure(f: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    if not f.has_pressure:
        return None
    return _qty(_phase_number(phase, "avg_pressure_bar"), 1, "bar")


def _phase_pressure_adherence(_: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    return _qty(_phase_diag_number(phase, "pressure_rmse_bar"), 2, "bar")


def _phase_resistance_level(phase: Mapping[str, Any]) -> float | None:
    """A phase's resistance; ``0.0`` is an average over no sample with any flow."""
    return _positive(_phase_diag_number(phase, "resistance_avg"))


def _phase_taper(_: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    rate = _qty(_phase_diag_number(phase, "taper_rate_bar_s"), 2, "bar/s")
    smooth = _qty(_phase_diag_number(phase, "taper_smoothness"), 2, "bar/s")
    parts = [part for part in (rate, smooth) if part]
    return ", ".join(parts) if parts else None


def _phase_resistance(_: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    return _with_resistance_source(
        _qty(_phase_resistance_level(phase), 2), _phase_diag(phase).get("resistance_source")
    )


def _phase_resistance_slope(_: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    if _phase_resistance_level(phase) is None:
        return None
    return _qty(_phase_diag_number(phase, "resistance_slope"), 2, "/s")


def _firmware_stats(block: object) -> str | None:
    """The firmware analyzer's five numbers for one stream, its average first.

    Two decimals: the machine's own ``pr`` has a resolution of 0.01.
    """
    if not isinstance(block, dict):
        return None
    values = {key: number(block.get(key)) for key in ("avg", "start", "end", "min", "max")}
    text = {key: _fixed(value, 2) for key, value in values.items() if value is not None}
    if len(text) != len(values):
        return None
    return (
        f"avg {text['avg']} (start {text['start']}, end {text['end']}, "
        f"min {text['min']}, max {text['max']})"
    )


def _firmware_whole(f: ShotFacts, stream: str) -> str | None:
    return _firmware_stats(f.firmware.get(stream))


def _firmware_phase(f: ShotFacts, phase: Mapping[str, Any], stream: str) -> str | None:
    """A phase's stream from the firmware block, found by the phase's number."""
    number_ = number(phase.get("phase_number"))
    for entry in f.firmware.get("phases") or []:
        if isinstance(entry, dict) and number(entry.get("phase_number")) == number_:
            return _firmware_stats(entry.get(stream))
    return None


def _firmware_water(f: ShotFacts, key: str, unit: str) -> str | None:
    return _qty(number(f.firmware.get(key)), 1, unit)


def _decimals(scale: float) -> int:
    """Decimals a firmware field is recorded to: 10 → 1, 100 → 2."""
    return round(math.log10(scale))


# ── the warnings and the per-phase numbers ───────────────────────────


def _check_line(check: Check) -> str:
    """One check as a line: ``ramp: early yield (red, critical): the sentence with the numbers``."""
    if check.status in ("failed", "warning", "expected") and check.in_badge:
        tags: list[str] = [check.color or ""]
        if check.status == "expected":
            tags.append("expected")
        elif check.tier is not None:
            tags.append(check.tier)
        return f"{check.badge} ({', '.join(tags)}): {check.detail}"
    if check.status == "unmeasured":
        return f"{check.phase}: {check.detail}"
    if check.status == "unchecked":
        return f"{check.phase}: {check.detail} [{check.tier}]"
    state = "held" if check.status == "held" else check.status
    return f"{check.phase}: {state} [{check.tier}]: {check.detail}"


def _check_lines(f: ShotFacts, *, base: bool) -> list[Check]:
    # What the base item carries: failed checks, expected warnings and what could not be
    # measured. The extended item carries the held ones, the context ones and the free text.
    return [c for c in f.shot_checks.checks if (c.rank <= 4) == base]


def _checks_text(f: ShotFacts) -> str | None:
    """The signature's state, then every failed or unmeasured check, most severe first."""
    lines = [f"signature: {f.shot_checks.state.text}"]
    lines += [_check_line(c) for c in _check_lines(f, base=True)]
    return "\n".join(lines)


def _checks_value(f: ShotFacts) -> list[Any] | None:
    found = [c.as_dict() for c in _check_lines(f, base=True)]
    return found or None


def _checks_more_text(f: ShotFacts) -> str | None:
    """What held, the context expectations and the ones only the reading can check."""
    lines = [_check_line(c) for c in _check_lines(f, base=False)]
    return "\n".join(lines) if lines else None


def _checks_more_value(f: ShotFacts) -> list[Any] | None:
    found = [c.as_dict() for c in _check_lines(f, base=False)]
    return found or None


def _yield_share(f: ShotFacts) -> float | None:
    return f.share_of_target(yield_g(f)) if f.shot.scale_connected else None


def _yield_share_text(f: ShotFacts) -> str | None:
    share = _yield_share(f)
    target = f.target_yield_g
    if share is None or target is None:
        return None
    return f"{_fixed(share, 1)} % of the {target:g} g target yield"


def _phases_not_reached(f: ShotFacts) -> str | None:
    """The profile's phases the shot never began, by name; absent when it began them all."""
    left = f.metrics.get("phases_not_reached")
    if not isinstance(left, list):
        return None
    names = [
        str(item.get("name") or "").strip() or f"phase {item.get('phase_number')}"
        for item in left
        if isinstance(item, dict)
    ]
    return ", ".join(names) if names else None


def _phases_not_reached_value(f: ShotFacts) -> list[Any] | None:
    """The same phases as a list, in the profile's order, so a name with a comma stays one name."""
    left = f.metrics.get("phases_not_reached")
    if not isinstance(left, list):
        return None
    found = [
        {
            "phase_number": item.get("phase_number"),
            "name": str(item.get("name") or "").strip() or f"phase {item.get('phase_number')}",
        }
        for item in left
        if isinstance(item, dict)
    ]
    return found or None


def _phase_log_note(f: ShotFacts) -> str | None:
    """Said once, in the one place a reader will look: what this log cannot tell."""
    version = f.shot.slog_version
    if f.metrics.get("per_phase") is False:
        return (
            f"no phase table: this log (firmware log version {version}) records none, so "
            "there are no per-phase numbers, only shot-wide ones"
        )
    if f.metrics.get("exit_reasons") is False:
        return (
            f"firmware log version {version} does not record why a phase ended: every phase "
            "reads Unknown"
        )
    return None


def _phase_metric(phase: Mapping[str, Any], key: str) -> float | None:
    block = phase.get("metrics")
    return number(block.get(key)) if isinstance(block, dict) else None


def _phase_ended_by(_: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    code = _phase_metric(phase, "ended_by")
    return None if code is None else PHASE_EXIT_REASONS.get(int(code), "Unknown")


def _phase_ended_by_value(_: ShotFacts, phase: Mapping[str, Any]) -> int | None:
    code = _phase_metric(phase, "ended_by")
    return None if code is None else int(code)


def _phase_qty(key: str, decimals: int, unit: str, *, needs: str | None = None) -> PhaseRender:
    """A per-phase metric as ``value unit``, gated on the sensor it needs.

    ``scale`` reads the shot's own flag and ``pressure`` its pressure gate, never
    the value: a machine without either records the column as zeros.
    """

    def render(f: ShotFacts, phase: Mapping[str, Any]) -> str | None:
        if needs == "scale" and not f.shot.scale_connected:
            return None
        if needs == "pressure" and not f.has_pressure:
            return None
        if needs == "puck_flow" and not f.puck_flow_recorded:
            return None
        return _qty(_phase_metric(phase, key), decimals, unit)

    return render


def _phase_number_of(key: str, *, needs: str | None = None) -> PhaseValue:
    def value(f: ShotFacts, phase: Mapping[str, Any]) -> float | None:
        if needs == "scale" and not f.shot.scale_connected:
            return None
        if needs == "pressure" and not f.has_pressure:
            return None
        if needs == "puck_flow" and not f.puck_flow_recorded:
            return None
        return _phase_metric(phase, key)

    return value


def _phase_cup_share(f: ShotFacts, phase: Mapping[str, Any]) -> float | None:
    if not f.shot.scale_connected:
        return None
    return f.share_of_target(_phase_metric(phase, "cup_weight_end_g"))


def _phase_cup_with_share(f: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    """The cup at a phase's end, with its share of the target yield when the shot has one.

    One item for the chat (``42.2 g, 117.2 % of target``); the fields route serves the cup
    and its share apart, since a page lays them out as two numbers.
    """
    text = (
        _qty(_phase_metric(phase, "cup_weight_end_g"), 1, "g") if f.shot.scale_connected else None
    )
    share = _phase_cup_share(f, phase)
    return text if text is None or share is None else f"{text}, {_fixed(share, 1)} % of target"


def _phase_cup_share_text(f: ShotFacts, phase: Mapping[str, Any]) -> str | None:
    share = _phase_cup_share(f, phase)
    return None if share is None else f"{_fixed(share, 1)} %"


def _firmware_stats_value(block: object) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None
    values = {key: number(block.get(key)) for key in ("avg", "start", "end", "min", "max")}
    return None if any(v is None for v in values.values()) else dict(values)


# ── the meanings' shared sentences ───────────────────────────────────

#: "The brew phases" are every phase but pre-infusion: said once, in the
#: glossary's preamble and in the phase type's meaning.
_BREW = "over the brew phases"
#: For the items outside the groups whose note already says it.
_NEEDS_PRESSURE = "Needs a pressure sensor."

#: The group the checks are rendered under, first in every rendering: the signature's state,
#: what failed, and (extended) what held. It replaced the warnings group in place.
CHECKS_GROUP = "Checks"

#: The groups of measured numbers: what a shot page lists beside its curve. Left
#: out are the shot's identity, the person's judgement, the recipe, the machine's
#: own note, the review (each has a place of its own) and the curve (a table).
#: Items served with the measured ones though their group is not: the ratio is the
#: judgement's dose in against the yield, and a shot page repeats it beside the
#: yield, so it comes from here (the person's dose, else the version's) rather
#: than being worked out again in a browser.
ALSO_SERVED: frozenset[str] = frozenset({"ratio"})

MEASURED_GROUPS: frozenset[str] = frozenset(
    {
        "Outcome",
        "Timing",
        "Temperature",
        "Pressure",
        "Flow and volume",
        "Weight",
        "Puck resistance",
        "Profile compliance",
        "Phases",
    }
)

#: The group a shot's review is rendered under. Named once because Review's own
#: input leaves it out: a review is never shown an earlier review.
REVIEW_GROUP = "Review"

#: What a group says once for all its rows: a condition every row shares, or
#: a layout that is not the ordinary ``label: value``. Part of each row's
#: meaning as a reader meets it (the glossary writes it under the heading).
GROUP_NOTES: Mapping[str, str] = MappingProxyType(
    {
        CHECKS_GROUP: (
            "The first line says whether the shot was read against a confirmed signature "
            "(`signature: confirmed, 6 expectations` or `read without a signature`); only "
            "expectations a person confirmed count. Then one check per line, `phase: fault "
            "(colour, tier): the sentence with the numbers`, `Shot` for the whole shot, in this "
            "order: failed critical expectations (red), failed important ones (amber), warnings "
            "nothing marks as expected (amber), expected warnings (grey), what could not be "
            "measured (with its reason; neither held nor failed). Extended adds what held, "
            "context expectations and free text. Earlier in the shot first, the whole shot last."
        ),
        "Pressure": (
            "Measured by the pressure sensor: none of these exists on a machine without one."
        ),
        "Weight": "From the scale's readings: none of these exists without a scale.",
        "Puck resistance": (
            "Resistance R is the machine's own puck resistance squared (the firmware's "
            "pr² = pressure / puck flow²) when the shot recorded it, "
            "and pressure / flow² from the logged pressure and flow otherwise; the level says "
            "which. Both are taken over the brew phases' samples with flow over 0.1 ml/s, a "
            "unitless number. None of it exists without a pressure sensor. "
            "The machine puck resistance and liquid resistance are the firmware analyzer's, in "
            "its own units: not comparable with R."
        ),
        "Profile compliance": (
            "How closely the machine followed the target each phase steered by, read from the "
            "shot's profile. The pressure lines need a pressure sensor and cover the "
            "pressure-steered phases; the flow lines (pump flow against the flow target) exist "
            "only where the profile has a flow-steered phase. All are absent when the shot has "
            "no known profile."
        ),
        "Phases": (
            "One line per phase, headed by the phase: `phase 2 · ramp: duration 5.0 s; ended by "
            "Duration; …`, with only the values that phase has. The pressure lines need a "
            "pressure sensor; the scale lines (cup, scale flow) need a scale."
        ),
        REVIEW_GROUP: (
            "The newest finished review of the shot, or nothing when it was never reviewed. A "
            "review is a model's reading of this one shot's data, made without the person's "
            "judgement, the Set or any other shot: weigh it below the measured numbers and "
            "below the person's judgement."
        ),
        "Curve": (
            "One table: a line saying how many of the shot's samples it holds, a header naming "
            "each column and its unit, then one comma-separated row per sample in time order. A "
            "long shot is cut to a few dozen rows chosen to keep the curve's shape (peaks, dips "
            "and turns of pressure and puck flow), and the line says which moments are always "
            "kept: the first and last sample, each phase's first and last, peak pressure, first "
            "drip and both ends of the largest pressure drop. A short shot is whole. Every column "
            "is cut at the same rows. An empty cell was not recorded; a channel the machine did "
            "not record is left out."
        ),
    }
)


#: What every Review item's meaning says, so a model reading the glossary never
#: mistakes one for a measurement or for the person's own view.
_MODEL_WRITTEN = (
    "Written by a model from this shot's data, without the person's judgement: a reading, "
    "not a measurement."
)


def _items() -> tuple[Item, ...]:
    """The catalogue, in the order of the groups and of the rows within them."""
    identity = "Identity and status"
    outcome = "Outcome"
    timing = "Timing"
    temperature = "Temperature"
    pressure = "Pressure"
    flow = "Flow and volume"
    weight = "Weight"
    resistance = "Puck resistance"
    compliance = "Profile compliance"
    phases = "Phases"
    curve = "Curve"
    judgement = "Your judgement"
    recipe = "The version's recipe"
    note = "The note typed on the machine"
    review = REVIEW_GROUP
    checks = CHECKS_GROUP

    return (
        # ── checks ───────────────────────────────────────────────────
        Item(
            key="checks",
            group=checks,
            name="Checks",
            label="Checks",
            meaning=(
                "What failed against the signature, the profile's confirmed intent: "
                "expectations per phase with a tier (critical, important, context) and a kind "
                "(a measure held against a limit, a phase that must begin, a warning that is "
                "part of the design, free text only the reading checks). A failure is named "
                "`phase: fault` from a fixed list (early yield, little yield, fast flow, slow "
                "flow, skipped, cut short, low pressure, high pressure, unstable, temperature, "
                "over target, under target). The universal warnings need no signature: over "
                f"target (the final weight above {OVER_TARGET_SHARE * 100:.0f} % of the "
                f"filed version's target yield), under target (below "
                f"{UNDER_TARGET_SHARE * 100:.0f} %), both needing a scale; skipped (the shot "
                "stopped on its weight or pumped-water target before a phase began); fast flow "
                f"(scale flow averaged over {FAST_FLOW_WINDOW_MS / 1000:.1f} s above "
                f"{FAST_FLOW_SCALE_FLOW_G_S:.1f} g/s at {FAST_FLOW_PRESSURE_SHARE * 100:.0f} % "
                "of peak pressure or more; needs a scale and a pressure sensor). One the "
                "signature expects is grey; otherwise it is a fact to weigh, not a verdict."
            ),
            default_tier="base",
            shot=_checks_text,
            shot_value=_checks_value,
        ),
        Item(
            key="checks_more",
            group=checks,
            name="Checks that held, and the rest",
            label="Held checks",
            meaning=(
                "The expectations of the confirmed signature that held on this shot, the "
                "context expectations whatever they came to (they inform and never raise "
                "the badge), and the free-text ones, which only the per-shot reading checks "
                "and are never decided by a number. Said only when there is something to "
                "say."
            ),
            default_tier="extended",
            shot=_checks_more_text,
            shot_value=_checks_more_value,
        ),
        # ── identity and status ──────────────────────────────────────
        Item(
            key="shot_id",
            group=identity,
            name="Shot id",
            label="Shot id",
            meaning=(
                "This archive's id for the shot, written as the heading of every rendered shot "
                "(`shot 129`). Cite a shot by it and pass it to the shot tools."
            ),
            default_tier="base",
            locked=True,
            shot=lambda f: str(f.shot_id),
        ),
        Item(
            key="started_at",
            group=identity,
            name="Date and time",
            label="Date and time",
            meaning="When the shot started, in UTC, to the minute.",
            default_tier="base",
            shot=lambda f: (
                str(f.shot.started_at)[:16].replace("T", " ") if f.shot.started_at else None
            ),
        ),
        Item(
            key="set_version",
            group=identity,
            name="Set version",
            label="Set version",
            meaning=(
                "The version of the Set the shot is filed under, and that Set's id: which recipe "
                "it was brewed on. A shot in no Set says so."
            ),
            default_tier="base",
            locked=True,
            shot=lambda f: (
                f"{f.version_label} of Set {f.set_id}"
                if f.version_label is not None
                else "not filed in a Set"
            ),
        ),
        Item(
            key="label",
            group=identity,
            name="Label",
            label="Label",
            meaning=(
                "The person's verdict on the shot. Keep: this is what good tastes like here (the "
                "target). Improve: what is being worked on. Discard: the shot went wrong rather "
                "than the recipe (a knocked cup, a bad puck), so it is not evidence. A shot "
                "nobody labelled says not labelled."
            ),
            default_tier="base",
            shot=lambda f: _DECISIONS.get(
                (f.judgement.decision if f.judgement is not None else None) or "", "not labelled"
            ),
        ),
        Item(
            key="counted",
            group=identity,
            name="Counted, or why not (quarantined, incomplete, discarded)",
            label="Counted",
            meaning=(
                "Whether the shot's numbers count towards the Set's spread and a prediction's "
                "evidence. Not counted when it is quarantined (the file never parsed, so its "
                "numbers are whatever the header held), stopped early (its time measures the "
                "interruption) or discarded. Read such a shot, but never average it."
            ),
            default_tier="base",
            locked=True,
            shot=_counted,
        ),
        Item(
            key="profile_as_brewed",
            group=identity,
            name="Profile as brewed",
            label="Profile as brewed",
            meaning=(
                "The profile the machine actually ran, by its label, or by the name the machine "
                "reported when the archive holds no copy of it."
            ),
            default_tier="base",
            shot=lambda f: f.shot.profile_label or f.shot.profile_name_on_device or None,
        ),
        Item(
            key="machine_shot_number",
            group=identity,
            name="Machine's shot number",
            label="Machine's shot number",
            meaning=(
                "The number the espresso machine itself gave the shot, as its own history shows "
                "it. Not the id to cite: that is the shot id."
            ),
            default_tier="base",
            shot=lambda f: f.shot.device_id or None,
        ),
        # ── outcome ──────────────────────────────────────────────────
        Item(
            key="shot_time",
            group=outcome,
            name="Shot time",
            label="Shot time",
            meaning=(
                "How long the shot ran, in seconds, from the start of the brew to the moment the "
                "machine stopped recording it. The Set page's shot time, and one of the measures a "
                "prediction is graded on."
            ),
            default_tier="base",
            shot=lambda f: _measure("shot_time_s", shot_time(f)),
        ),
        Item(
            key="yield",
            group=outcome,
            name="Yield (final weight)",
            label="Yield",
            meaning=(
                "The weight in the cup when the shot ended, in grams, as the connected scale read "
                "it. Only a machine with a scale records it; the dose out the person typed is a "
                "separate line of their judgement."
            ),
            default_tier="base",
            shot=lambda f: _measure("yield_g", yield_g(f)),
        ),
        Item(
            key="yield_share",
            group=outcome,
            name="Yield against the target",
            label="Yield against target",
            meaning=(
                "The yield as a share of the target yield of the version the shot is filed "
                "under, in %. Worked out when the shot is read: refile the shot and it moves. "
                "Needs a scale and a version with a target."
            ),
            default_tier="extended",
            shot=_yield_share_text,
            shot_value=_yield_share,
        ),
        Item(
            key="exit_reason",
            group=outcome,
            name="Exit reason",
            label="Exit reason",
            meaning=(
                "Why the machine ended the shot's last phase: a volumetric (weight) target, a "
                "pressure, flow or pumped-water target, the phase's duration, a safety timeout, "
                "the person stopping it (Aborted), or a released hold."
            ),
            default_tier="base",
            shot=_exit_reason,
        ),
        Item(
            key="phases_not_reached",
            group=outcome,
            name="Profile phases not reached",
            label="Phases not reached",
            meaning=(
                "The phases of the shot's profile that never began, by name: the shot ended "
                "before them. Absent when every phase began, or when the shot has no profile."
            ),
            default_tier="extended",
            shot=_phases_not_reached,
        ),
        Item(
            key="phase_log_note",
            group=outcome,
            name="What the shot's log cannot say about its phases",
            label="Phase log",
            meaning=(
                "Said once when the log cannot tell how the phases went: a log from before "
                "firmware log version 5 has no phase table, so there are no per-phase numbers; "
                "version 5 has one but not why each phase ended."
            ),
            default_tier="extended",
            shot=_phase_log_note,
        ),
        # ── timing ───────────────────────────────────────────────────
        Item(
            key="first_drip",
            group=timing,
            name="First drip",
            label="First drip",
            meaning=(
                "Seconds from the start of the shot to the first sample with any puck flow: when "
                "coffee started to reach the cup. Later usually means a finer grind or a longer "
                "pre-infusion. The Set page's time to first drip."
            ),
            default_tier="base",
            shot=lambda f: _measure("first_drip_s", first_drip(f)),
        ),
        Item(
            key="preinfusion_time",
            group=timing,
            name="Pre-infusion time",
            label="Pre-infusion time",
            meaning=(
                "Seconds until pressure first reached half its peak, read off the pressure trace "
                f"rather than the phase names: when wetting ended and extraction began. "
                f"{_NEEDS_PRESSURE}"
            ),
            default_tier="extended",
            shot=lambda f: (
                _qty(f.summary_value("extraction", "preinfusion_time_s"), 1, "s")
                if f.has_pressure
                else None
            ),
        ),
        Item(
            key="main_extraction_time",
            group=timing,
            name="Main extraction time",
            label="Main extraction time",
            meaning=(
                "Seconds from the end of pre-infusion (as above) to the end of the shot. "
                f"{_NEEDS_PRESSURE}"
            ),
            default_tier="extended",
            shot=lambda f: (
                _qty(f.summary_value("extraction", "main_extraction_time_s"), 1, "s")
                if f.has_pressure
                else None
            ),
        ),
        # ── temperature ──────────────────────────────────────────────
        Item(
            key="average_temperature",
            group=temperature,
            name="Average temperature",
            label="Average temperature",
            meaning="The mean measured brew water temperature over the whole shot, in °C.",
            default_tier="extended",
            shot=lambda f: _qty(_temperature(f, "avg_c"), 1, "°C"),
        ),
        Item(
            key="target_temperature",
            group=temperature,
            name="Target temperature (average)",
            label="Target temperature",
            meaning=(
                "The mean temperature the profile asked for over the shot, in °C. The machine "
                "heats to the profile's temperature; the Set records none of its own."
            ),
            default_tier="extended",
            shot=lambda f: _qty(_temperature(f, "target_avg_c"), 1, "°C"),
        ),
        Item(
            key="minimum_temperature",
            group=temperature,
            name="Minimum temperature",
            label="Minimum temperature",
            meaning="The lowest measured temperature during the shot, in °C.",
            default_tier="extended",
            shot=lambda f: _qty(_temperature(f, "min_c"), 1, "°C"),
        ),
        Item(
            key="maximum_temperature",
            group=temperature,
            name="Maximum temperature",
            label="Maximum temperature",
            meaning="The highest measured temperature during the shot, in °C.",
            default_tier="extended",
            shot=lambda f: _qty(_temperature(f, "max_c"), 1, "°C"),
        ),
        # ── pressure ─────────────────────────────────────────────────
        Item(
            key="peak_pressure",
            group=pressure,
            name="Peak pressure",
            label="Peak pressure",
            meaning=(
                "The highest pressure measured at the puck during the shot, in bar. The Set "
                "page's peak pressure."
            ),
            default_tier="base",
            shot=lambda f: _measure("peak_pressure_bar", peak_pressure(f)),
        ),
        Item(
            key="average_pressure",
            group=pressure,
            name="Average pressure",
            label="Average pressure",
            meaning="The mean pressure over the whole shot, in bar.",
            default_tier="extended",
            shot=lambda f: _qty(_pressure_summary(f, "avg_bar"), 1, "bar"),
        ),
        Item(
            key="minimum_pressure",
            group=pressure,
            name="Minimum pressure",
            label="Minimum pressure",
            meaning="The lowest pressure over the whole shot, in bar.",
            default_tier="extended",
            shot=lambda f: _qty(_pressure_summary(f, "min_bar"), 1, "bar"),
        ),
        Item(
            key="peak_pressure_time",
            group=pressure,
            name="Time of peak pressure",
            label="Time of peak pressure",
            meaning="Seconds into the shot when the peak pressure was reached.",
            default_tier="extended",
            shot=lambda f: _qty(_pressure_summary(f, "peak_time_s"), 1, "s"),
        ),
        # ── flow and volume ──────────────────────────────────────────
        Item(
            key="brew_flow",
            group=flow,
            name="Average brew flow",
            label="Average brew flow",
            meaning=(
                f"The mean puck flow {_BREW}, in ml/s. Faster usually means a coarser grind or a "
                "lighter puck. The Set page's average brew flow."
            ),
            default_tier="base",
            shot=lambda f: _measure("brew_flow_ml_s", brew_flow(f)),
        ),
        Item(
            key="average_flow",
            group=flow,
            name="Average flow (whole shot)",
            label="Average flow",
            meaning=(
                "The mean puck flow over the whole shot, pre-infusion included, in ml/s. Puck "
                "flow is the machine's estimate of the water passing through the coffee."
            ),
            default_tier="extended",
            shot=lambda f: _qty(_flow_summary(f, "avg_flow_ml_s"), 1, "ml/s"),
        ),
        Item(
            key="peak_flow",
            group=flow,
            name="Peak flow",
            label="Peak flow",
            meaning="The highest puck flow during the shot, in ml/s.",
            default_tier="extended",
            shot=lambda f: _qty(_flow_summary(f, "peak_flow_ml_s"), 1, "ml/s"),
        ),
        Item(
            key="total_volume",
            group=flow,
            name="Total volume pumped",
            label="Total volume",
            meaning=(
                "Puck flow integrated over the shot, in ml: the water that passed through the "
                "puck. More than the yield, because the puck keeps some."
            ),
            default_tier="extended",
            shot=lambda f: _qty(_flow_summary(f, "total_volume_ml"), 1, "ml"),
        ),
        Item(
            key="water_pumped",
            group=flow,
            name="Water pumped (the pump's own count)",
            label="Water pumped",
            meaning=(
                "The water the pump moved, in ml, from the machine's own count; only for machines "
                "whose pump counts it."
            ),
            default_tier="extended",
            shot=lambda f: _firmware_water(f, "water_pumped_ml", "ml"),
        ),
        # ── weight ───────────────────────────────────────────────────
        Item(
            key="water_minus_weight",
            group=weight,
            name="Water pumped minus beverage weight",
            label="Water pumped minus beverage weight",
            meaning=("Water pumped less the beverage weight, in g; needs both."),
            default_tier="extended",
            shot=lambda f: _firmware_water(f, "water_minus_weight_g", "g"),
        ),
        Item(
            key="weight_rate",
            group=weight,
            name="Average weight rate",
            label="Average weight rate",
            meaning=(
                f"How fast weight arrived in the cup {_BREW}, in g/s, from the scale's readings "
                "(steps where the weight fell are left out)."
            ),
            default_tier="extended",
            shot=lambda f: _qty(f.section_value("weight", "rate_avg_g_s"), 2, "g/s"),
        ),
        # ── puck resistance ──────────────────────────────────────────
        Item(
            key="resistance_level",
            group=resistance,
            name="Resistance level (average)",
            label="Resistance level",
            meaning=(
                "The puck's average resistance, with where it came from: the machine's own "
                "measurement, or computed from pressure and flow when the shot has none. It folds "
                "grind, dose and puck prep into one reading: higher is a finer grind or a tighter "
                "puck. A number, not a grade: what is high or low depends on the profile and the "
                "basket."
            ),
            default_tier="base",
            shot=_resistance_level_text,
        ),
        Item(
            key="resistance_slope",
            group=resistance,
            name="Resistance slope",
            label="Resistance slope",
            meaning=(
                "The slope of the resistance over the brew phases, per second: negative is a "
                "resistance falling through the shot, positive one rising. A number, not a grade."
            ),
            default_tier="extended",
            shot=lambda f: _qty(_resistance_slope(f), 2, "/s"),
        ),
        Item(
            key="machine_puck_resistance",
            group=resistance,
            name="Machine puck resistance (firmware analyzer), whole shot",
            label="Machine puck resistance (s·√bar/mL)",
            meaning=(
                "The firmware analyzer's machine puck resistance pr (s·√bar/mL): average, then "
                "first, last, lowest, highest. The square root of the level's quantity, so not "
                "comparable with it."
            ),
            default_tier="extended",
            shot=lambda f: _firmware_whole(f, "pr"),
        ),
        Item(
            key="liquid_resistance",
            group=resistance,
            name="Liquid resistance (firmware analyzer), whole shot",
            label="Liquid resistance (bar·s/mL)",
            meaning=(
                "The firmware analyzer's liquid resistance, pr · √pressure (bar·s/mL): average, "
                "then first, last, lowest, highest."
            ),
            default_tier="extended",
            shot=lambda f: _firmware_whole(f, "lr"),
        ),
        # ── profile compliance ───────────────────────────────────────
        Item(
            key="pressure_adherence",
            group=compliance,
            name="Pressure adherence (RMSE)",
            label="Pressure adherence",
            meaning=(
                "The root-mean-square difference between measured and target pressure over the "
                "samples of pressure-steered phases, in bar; lower is closer to the profile. The "
                "machine's controller drives the pump to hold pressure, so this says how well it "
                "did, not what the puck did."
            ),
            default_tier="base",
            shot=lambda f: _qty(_pressure_rmse(f), 2, "bar"),
        ),
        Item(
            key="flow_adherence",
            group=compliance,
            name="Flow adherence (RMSE)",
            label="Flow adherence",
            meaning=(
                "The root-mean-square difference between pump flow and target flow over the "
                "samples of flow-steered phases, in ml/s; lower is closer. The pump flow is the "
                "machine's own estimate, not a measurement: it leaves the target only when the "
                "pump runs out of power or a pressure limit takes over, so it is not a grind "
                "signal."
            ),
            default_tier="base",
            shot=lambda f: _qty(_flow_rmse(f), 2, "ml/s"),
        ),
        Item(
            key="pressure_undershoot_max",
            group=compliance,
            name="Largest pressure undershoot",
            label="Largest pressure undershoot",
            meaning=(
                "The most measured pressure fell below the target pressure, in bar. A large "
                "one throughout is a puck too loose to build pressure."
            ),
            default_tier="extended",
            shot=lambda f: _qty(_compliance(f, "max_pressure_undershoot_bar", None), 2, "bar"),
        ),
        # ── phases ───────────────────────────────────────────────────
        Item(
            key="phase_name",
            group=phases,
            name="Phase name and number",
            label="phase",
            meaning=(
                "The phase's number in the profile (from 0) and its name, heading the phase's "
                "line (`phase 3 · decline 9-4`)."
            ),
            default_tier="base",
            phase=_phase_name,
        ),
        Item(
            key="phase_type",
            group=phases,
            name="Phase type",
            label="type",
            meaning=(
                "What the engine took the phase to be. preinfusion when its name contains "
                f"{', '.join(engine._PREINFUSION_KEYWORDS)}; decline when it contains "
                f"{', '.join(engine._DECLINE_KEYWORDS)}; otherwise read from the pressure trace "
                "(a first phase under 5 bar and rising is preinfusion, a last phase falling "
                "faster than 0.3 bar per sample is decline), else brew. A description only: "
                "every phase gets the same metrics. Every phase but a preinfusion one is one of "
                "the shot's brew phases."
            ),
            default_tier="extended",
            phase=_phase_type,
        ),
        Item(
            key="phase_start",
            group=phases,
            name="Phase start",
            label="start",
            meaning="Seconds into the shot when the phase began.",
            default_tier="extended",
            phase=lambda _, p: _qty(_phase_number(p, "start_time_seconds"), 1, "s"),
        ),
        Item(
            key="phase_duration",
            group=phases,
            name="Phase duration",
            label="duration",
            meaning="How long the phase ran, in seconds.",
            default_tier="base",
            phase=_phase_duration,
        ),
        Item(
            key="phase_ended_by",
            group=phases,
            name="How the phase ended",
            label="ended by",
            meaning=(
                "Why the phase ended: its duration, a target, a safety timeout or the person "
                "stopping the shot (Aborted). Unknown on a log that records none (firmware log "
                "version 5)."
            ),
            default_tier="base",
            phase=_phase_ended_by,
            phase_value=_phase_ended_by_value,
        ),
        Item(
            key="phase_pressure",
            group=phases,
            name="Phase average pressure",
            label="pressure",
            meaning="The mean pressure during the phase, in bar.",
            default_tier="extended",
            phase=_phase_pressure,
        ),
        Item(
            key="phase_pressure_peak",
            group=phases,
            name="Phase peak pressure",
            label="peak pressure",
            meaning="The highest pressure during the phase, in bar.",
            default_tier="extended",
            phase=_phase_qty("pressure_peak_bar", 1, "bar", needs="pressure"),
        ),
        Item(
            key="phase_pressure_end",
            group=phases,
            name="Phase pressure at its end",
            label="pressure at end",
            meaning="The pressure at the phase's last sample, in bar.",
            default_tier="extended",
            phase=_phase_qty("pressure_end_bar", 1, "bar", needs="pressure"),
        ),
        Item(
            key="phase_temperature",
            group=phases,
            name="Phase average temperature",
            label="temperature",
            meaning="The mean measured temperature during the phase, in °C.",
            default_tier="extended",
            phase=lambda _, p: _qty(_positive(_phase_number(p, "avg_temperature_c")), 1, "°C"),
        ),
        Item(
            key="phase_temperature_min",
            group=phases,
            name="Phase lowest temperature",
            label="lowest temperature",
            meaning="The lowest measured temperature during the phase, in °C.",
            default_tier="extended",
            phase=lambda _, p: _qty(_positive(_phase_metric(p, "temperature_min_c")), 1, "°C"),
        ),
        Item(
            key="phase_temperature_target",
            group=phases,
            name="Phase target temperature",
            label="target temperature",
            meaning="The mean temperature the profile asked for during the phase, in °C.",
            default_tier="extended",
            phase=lambda _, p: _qty(_phase_metric(p, "temperature_target_c"), 1, "°C"),
        ),
        Item(
            key="phase_volume",
            group=phases,
            name="Phase volume",
            label="volume",
            meaning="Puck flow integrated over the phase, in ml.",
            default_tier="extended",
            phase=lambda f, p: (
                _qty(_phase_number(p, "total_flow_ml"), 1, "ml") if f.puck_flow_recorded else None
            ),
        ),
        Item(
            key="phase_flow",
            group=phases,
            name="Phase average flow",
            label="flow",
            meaning="The mean puck flow during the phase, in ml/s.",
            default_tier="extended",
            phase=lambda f, p: (
                _qty(_phase_diag_number(p, "avg_flow_ml_s"), 2, "ml/s")
                if f.puck_flow_recorded
                else None
            ),
        ),
        Item(
            key="phase_flow_peak",
            group=phases,
            name="Phase peak puck flow",
            label="peak flow",
            meaning="The highest puck flow during the phase, in ml/s.",
            default_tier="extended",
            phase=_phase_qty("puck_flow_peak_ml_s", 2, "ml/s", needs="puck_flow"),
        ),
        Item(
            key="phase_scale_flow",
            group=phases,
            name="Phase average scale flow",
            label="scale flow",
            meaning=(
                "The mean of the scale's flow readings during the phase, in g/s: how fast the "
                "cup filled. Needs a scale."
            ),
            default_tier="extended",
            phase=_phase_qty("scale_flow_mean_g_s", 2, "g/s", needs="scale"),
        ),
        Item(
            key="phase_scale_flow_peak",
            group=phases,
            name="Phase peak scale flow",
            label="peak scale flow",
            meaning="The highest scale flow reading during the phase, in g/s. Needs a scale.",
            default_tier="extended",
            phase=_phase_qty("scale_flow_peak_g_s", 2, "g/s", needs="scale"),
        ),
        Item(
            key="phase_cup_end",
            group=phases,
            name="Cup weight at the phase's end",
            label="cup at end",
            meaning=(
                "The weight in the cup at the phase's last sample, in g, with its share of the "
                "filed version's target yield in % when there is one (over 100 % before the "
                "last phase means the cup was full before the profile was done). Needs a scale."
            ),
            default_tier="base",
            phase=_phase_qty("cup_weight_end_g", 1, "g", needs="scale"),
            chat_phase=_phase_cup_with_share,
        ),
        Item(
            key="phase_cup_gained",
            group=phases,
            name="Cup weight gained in the phase",
            label="cup gained",
            meaning=(
                "How much the cup gained during the phase, in g: its weight at the phase's end "
                "less its weight at the previous phase's end (0 g before the first). Needs a "
                "scale."
            ),
            default_tier="extended",
            phase=_phase_qty("cup_weight_gained_g", 1, "g", needs="scale"),
        ),
        Item(
            key="phase_cup_share",
            group=phases,
            name="Cup weight at the phase's end, as a share of the target yield",
            label="cup share of target",
            meaning=(
                "The cup's weight at the phase's end as a share of the filed version's target "
                "yield, in %. A chat reads it with the cup at the phase's end, as one item. "
                "Needs a scale and a version with a target."
            ),
            default_tier="excluded",
            phase=_phase_cup_share_text,
            phase_value=_phase_cup_share,
        ),
        Item(
            key="phase_water",
            group=phases,
            name="Water pumped in the phase",
            label="water pumped",
            meaning=(
                "How much the pump's own water counter rose during the phase, in ml. Samples "
                "after the counter is reset (it is zeroed when a weight stop ends the shot) "
                "are not read. Absent where the counter was not recorded."
            ),
            default_tier="extended",
            phase=_phase_qty("water_pumped_ml", 1, "ml"),
        ),
        Item(
            key="phase_first_drip",
            group=phases,
            name="First drip, in the phase that holds it",
            label="first drip",
            meaning=(
                "Seconds into the shot of the first sample with any puck flow, on the phase it "
                "fell in."
            ),
            default_tier="extended",
            phase=lambda f, p: (
                _qty(_phase_metric(p, "first_drip_s"), 1, "s") if f.puck_flow_recorded else None
            ),
        ),
        Item(
            key="phase_pressure_adherence",
            group=phases,
            name="Phase pressure adherence (RMSE)",
            label="pressure adherence",
            meaning=(
                "Pressure adherence (as above) within a pressure-steered phase, in bar. Needs a "
                "pressure sensor and the shot's profile."
            ),
            default_tier="extended",
            phase=_phase_pressure_adherence,
        ),
        Item(
            key="phase_flow_error",
            group=phases,
            name="Phase flow error (RMSE)",
            label="flow error",
            meaning=(
                "Flow-steered phases only: the root-mean-square difference between pump flow and "
                "the flow target within the phase, in ml/s."
            ),
            default_tier="extended",
            phase=lambda _, p: _qty(_phase_diag_number(p, "flow_rmse_ml_s"), 2, "ml/s"),
        ),
        Item(
            key="phase_ramp",
            group=phases,
            name="Phase ramp rate (pre-infusion)",
            label="ramp",
            meaning=(
                "Pre-infusion phases only: how fast pressure rose over the phase (its "
                "least-squares slope), in bar/s."
            ),
            default_tier="extended",
            phase=lambda _, p: _qty(_phase_diag_number(p, "ramp_rate_bar_s"), 2, "bar/s"),
        ),
        Item(
            key="phase_saturation",
            group=phases,
            name="Phase saturation time (pre-infusion)",
            label="saturation",
            meaning=(
                "Pre-infusion phases only: seconds into the phase until flow settled (three "
                "samples flowing over 0.1 ml/s and varying by under 0.15): when the puck was "
                "wet through. The whole phase when it never settled."
            ),
            default_tier="extended",
            phase=lambda _, p: _qty(_phase_diag_number(p, "saturation_time_s"), 2, "s"),
        ),
        Item(
            key="phase_taper",
            group=phases,
            name="Phase taper rate and smoothness (decline)",
            label="taper",
            meaning=(
                "Decline phases only: how fast pressure fell (its slope, bar/s), then how evenly "
                "(the standard deviation of its sample-to-sample rate, bar/s; lower is smoother)."
            ),
            default_tier="extended",
            phase=_phase_taper,
        ),
        Item(
            key="phase_resistance",
            group=phases,
            name="Phase resistance level",
            label="resistance",
            meaning=(
                "The puck resistance within the phase, as the shot's resistance level is, with "
                "where it came from (the machine's own measurement, or computed). Only a phase "
                "with flow to divide by has one."
            ),
            default_tier="extended",
            phase=_phase_resistance,
        ),
        Item(
            key="phase_resistance_slope",
            group=phases,
            name="Phase resistance slope",
            label="resistance slope",
            meaning=(
                "The slope of the puck resistance within the phase, per second, as the shot's "
                "resistance slope is."
            ),
            default_tier="extended",
            phase=_phase_resistance_slope,
        ),
        Item(
            key="phase_machine_resistance",
            group=phases,
            name="Phase machine puck resistance (firmware analyzer)",
            label="machine puck resistance (s·√bar/mL)",
            meaning=(
                "The machine puck resistance over this phase, as the shot's: average, then first, "
                "last, lowest, highest. Every phase."
            ),
            default_tier="extended",
            phase=lambda f, p: _firmware_phase(f, p, "pr"),
        ),
        Item(
            key="phase_liquid_resistance",
            group=phases,
            name="Phase liquid resistance (firmware analyzer)",
            label="liquid resistance (bar·s/mL)",
            meaning="The liquid resistance over this phase, as the shot's.",
            default_tier="extended",
            phase=lambda f, p: _firmware_phase(f, p, "lr"),
        ),
        Item(
            key="phase_samples",
            group=phases,
            name="Phase sample count",
            label="samples",
            meaning="How many samples the machine recorded during the phase.",
            default_tier="excluded",
            phase=lambda _, p: (
                str(int(value)) if (value := _phase_number(p, "sample_count")) is not None else None
            ),
        ),
        # ── curve ────────────────────────────────────────────────────
        *_channels(curve),
        # ── your judgement ───────────────────────────────────────────
        Item(
            key="rating",
            group=judgement,
            name="Rating",
            label="Rating",
            meaning="The person's rating of the cup, 1 to 5 stars. Higher is better.",
            default_tier="base",
            shot=lambda f: (
                f"{f.judgement.rating}/5"
                if f.judgement is not None and f.judgement.rating is not None
                else None
            ),
        ),
        Item(
            key="balance",
            group=judgement,
            name="Balance",
            label="Balance",
            meaning=(
                "The person's extraction verdict: Sour (under-extracted), Balanced or Bitter "
                "(over-extracted). It is ground truth for taste and outranks every number here."
            ),
            default_tier="base",
            shot=lambda f: _BALANCES.get(balance(f) or ""),
        ),
        Item(
            key="taste_notes",
            group=judgement,
            name="Taste notes, with their flavour-wheel path",
            label="Taste notes",
            meaning=(
                "What the person tasted, as SCA flavour-wheel notes written from the centre out "
                '("Fruity › Berry › Blackberry"). A note under Sour/Fermented › Sour points the '
                "way a sour balance does; one under Other › Chemical › Bitter or Roasted › Burnt "
                "the way a bitter one does."
            ),
            default_tier="base",
            shot=lambda f: _notes(f.judgement.taste_notes) if f.judgement is not None else None,
        ),
        Item(
            key="aroma_notes",
            group=judgement,
            name="Aroma notes, with their flavour-wheel path",
            label="Aroma notes",
            meaning="What the person smelled, as flavour-wheel notes written from the centre out.",
            default_tier="base",
            shot=lambda f: _notes(f.judgement.aroma_notes) if f.judgement is not None else None,
        ),
        Item(
            key="written_notes",
            group=judgement,
            name="Written notes",
            label="Notes",
            meaning="What the person wrote about the shot, in their own words.",
            default_tier="base",
            shot=lambda f: _quote(f.judgement.notes) if f.judgement is not None else None,
        ),
        Item(
            key="dose_in",
            group=judgement,
            name="Dose in",
            label="Dose in",
            meaning="The ground coffee the person put in the basket, in grams.",
            default_tier="base",
            shot=lambda f: _qty(dose_in(f), 1, "g"),
        ),
        Item(
            key="dose_out",
            group=judgement,
            name="Dose out",
            label="Dose out",
            meaning=(
                "The beverage weight the person recorded, in grams. Where it differs from the "
                "scale's yield, it is the person correcting the scale."
            ),
            default_tier="base",
            shot=lambda f: _qty(dose_out(f), 1, "g"),
        ),
        Item(
            key="ratio",
            group=judgement,
            name="Ratio",
            label="Ratio",
            meaning=(
                "Dose out divided by dose in, written 1:2.00. Longer ratios extract more. The dose "
                "in is the person's when entered, else the version's; the dose out is the "
                "person's when entered, else the scale's yield. Absent without both."
            ),
            default_tier="base",
            shot=lambda f: f"1:{_fixed(value, 2)}" if (value := ratio(f)) is not None else None,
        ),
        Item(
            key="grind_as_brewed",
            group=judgement,
            name="Grind as brewed",
            label="Grind as brewed",
            meaning=(
                "The grind setting the person says this shot was actually ground at, in the "
                "grinder's own units. It can differ from the recipe's."
            ),
            default_tier="base",
            shot=lambda f: (
                (f.judgement.grind_setting or "").strip() or None
                if f.judgement is not None
                else None
            ),
        ),
        # ── the version's recipe ─────────────────────────────────────
        Item(
            key="recipe_grind",
            group=recipe,
            name="Recipe grind",
            label="Recipe grind",
            meaning="The grind the shot's Set version prescribes, in the grinder's own units.",
            default_tier="excluded",
            shot=lambda f: (
                (f.version.grind_setting or "").strip() or None if f.version is not None else None
            ),
        ),
        Item(
            key="recipe_dose",
            group=recipe,
            name="Recipe dose",
            label="Recipe dose",
            meaning="The dose the shot's Set version prescribes, in grams.",
            default_tier="excluded",
            shot=lambda f: _qty(f.version.dose_g, 1, "g") if f.version is not None else None,
        ),
        Item(
            key="recipe_yield",
            group=recipe,
            name="Recipe target yield",
            label="Recipe target yield",
            meaning="The yield the shot's Set version aims for, in grams.",
            default_tier="excluded",
            shot=lambda f: (
                _qty(f.version.target_yield_g, 1, "g") if f.version is not None else None
            ),
        ),
        Item(
            key="recipe_profile",
            group=recipe,
            name="Recipe profile",
            label="Recipe profile",
            meaning="The profile the shot's Set version names.",
            default_tier="excluded",
            shot=lambda f: f.version.profile_label or None if f.version is not None else None,
        ),
        Item(
            key="profile_temperature",
            group=recipe,
            name="Profile temperature",
            label="Profile temperature",
            meaning="The brew temperature that profile states, in °C.",
            default_tier="excluded",
            shot=lambda f: (
                _qty(f.version.profile_temperature_c, 1, "°C") if f.version is not None else None
            ),
        ),
        # ── the note typed on the machine ────────────────────────────
        Item(
            key="note_rating",
            group=note,
            name="Machine note rating",
            label="Machine note rating",
            meaning="The rating typed on the machine's notes card, 1 to 5; it seeds the judgement.",
            default_tier="excluded",
            shot=lambda f: f"{f.note.rating}/5" if f.note is not None and f.note.rating else None,
        ),
        Item(
            key="note_balance",
            group=note,
            name="Machine note balance",
            label="Machine note balance",
            meaning="The balance typed on the machine's notes card.",
            default_tier="excluded",
            shot=lambda f: (
                (f.note.balance_taste or "").strip() or None if f.note is not None else None
            ),
        ),
        Item(
            key="note_doses",
            group=note,
            name="Machine note dose in / dose out / ratio",
            label="Machine note doses",
            meaning="The doses and ratio typed on the machine's notes card.",
            default_tier="excluded",
            shot=_note_doses,
        ),
        Item(
            key="note_grind",
            group=note,
            name="Machine note grind",
            label="Machine note grind",
            meaning="The grind typed on the machine's notes card.",
            default_tier="excluded",
            shot=lambda f: (
                (f.note.grind_setting or "").strip() or None if f.note is not None else None
            ),
        ),
        Item(
            key="note_bean",
            group=note,
            name="Machine note bean",
            label="Machine note bean",
            meaning="The bean typed on the machine's notes card.",
            default_tier="excluded",
            shot=lambda f: (f.note.bean_type or "").strip() or None if f.note is not None else None,
        ),
        Item(
            key="note_text",
            group=note,
            name="Machine note text",
            label="Machine note text",
            meaning="The free text typed on the machine's notes card.",
            default_tier="excluded",
            shot=lambda f: _quote(f.note.notes) if f.note is not None else None,
        ),
        # ── the review ───────────────────────────────────────────────
        Item(
            key="review_taste_balance",
            group=review,
            name="Review's predicted balance",
            label="Predicted balance",
            meaning=(
                "What a review expects the cup's balance to be: Sour, Balanced or Bitter. "
                + _MODEL_WRITTEN
            ),
            default_tier="extended",
            shot=lambda f: (
                _BALANCES.get(f.review.taste_balance)
                if f.review is not None and f.review.taste_balance
                else None
            ),
        ),
        Item(
            key="review_taste_body",
            group=review,
            name="Review's predicted body",
            label="Predicted body",
            meaning="What a review expects the cup's body to be: thin, medium or heavy. "
            + _MODEL_WRITTEN,
            default_tier="extended",
            shot=lambda f: f.review.taste_body if f.review is not None else None,
        ),
        Item(
            key="review_taste_confidence",
            group=review,
            name="Review's confidence in its prediction",
            label="Prediction confidence",
            meaning="How sure the review said it was of its taste prediction: low, medium or "
            "high. " + _MODEL_WRITTEN,
            default_tier="extended",
            shot=lambda f: f.review.taste_confidence if f.review is not None else None,
        ),
        Item(
            key="review_description",
            group=review,
            name="Review's description",
            label="Review description",
            meaning="A paragraph on what the shot's telemetry shows and why, with its figures. "
            + _MODEL_WRITTEN,
            default_tier="extended",
            shot=lambda f: _quote(f.review.description) if f.review is not None else None,
        ),
        Item(
            key="review_summary",
            group=review,
            name="Review's one-line summary",
            label="Review summary",
            meaning="The review in one sentence. " + _MODEL_WRITTEN,
            default_tier="extended",
            shot=lambda f: _quote(f.review.summary) if f.review is not None else None,
        ),
        Item(
            key="review_written_at",
            group=review,
            name="When the review was written",
            label="Review written",
            meaning="When the review finished, in UTC, to the minute. " + _MODEL_WRITTEN,
            default_tier="extended",
            shot=lambda f: (
                str(f.review.finished_at or f.review.created_at)[:16].replace("T", " ")
                if f.review is not None
                else None
            ),
        ),
        Item(
            key="review_model",
            group=review,
            name="Which model wrote the review",
            label="Review model",
            meaning="The model that wrote the review, as the provider named it. " + _MODEL_WRITTEN,
            default_tier="extended",
            shot=lambda f: (f.review.model or None) if f.review is not None else None,
        ),
    )


def _note_doses(f: ShotFacts) -> str | None:
    if f.note is None:
        return None
    parts = [
        f"{_fixed(f.note.dose_in_g, 1)} g in" if f.note.dose_in_g else "",
        f"{_fixed(f.note.dose_out_g, 1)} g out" if f.note.dose_out_g else "",
        f"ratio 1:{_fixed(f.note.ratio, 2)}" if f.note.ratio else "",
    ]
    return ", ".join(part for part in parts if part) or None


def _channels(group: str) -> tuple[Item, ...]:
    """The curve's channels, one item each, in the spec's order."""
    temp = _decimals(TEMP_SCALE)
    bar = _decimals(PRESSURE_SCALE)
    flow = _decimals(FLOW_SCALE)
    grams = _decimals(WEIGHT_SCALE)
    specs: tuple[tuple[str, str, Channel, Tier, str], ...] = (
        (
            "curve_pressure",
            "Pressure",
            Channel("cp", "pressure (bar)", bar, "pressure"),
            "extended",
            "Measured pressure at the puck, in bar. Absent without a pressure sensor.",
        ),
        (
            "curve_target_pressure",
            "Target pressure",
            Channel("tp", "target pressure (bar)", bar),
            "extended",
            "The pressure the profile commanded (or its limit, on a flow-led phase), in bar.",
        ),
        (
            "curve_puck_flow",
            "Puck flow",
            Channel("pf", "puck flow (ml/s)", flow, "pressure"),
            "extended",
            "The machine's estimate of water flowing through the puck, in ml/s.",
        ),
        (
            "curve_target_flow",
            "Target flow",
            Channel("tf", "target flow (ml/s)", flow, "pressure"),
            "extended",
            "The flow the profile commanded (or its limit, on a pressure-led phase), in ml/s.",
        ),
        (
            "curve_weight",
            "Weight",
            Channel("v", "weight (g)", grams, "scale"),
            "extended",
            "The weight in the cup as the scale read it, in grams. Absent without a scale.",
        ),
        (
            "curve_temperature",
            "Temperature",
            Channel("ct", "temperature (°C)", temp),
            "extended",
            "The measured brew water temperature, in °C.",
        ),
        (
            "curve_phase",
            "Phase marker",
            Channel("phase_number", "phase marker", 0),
            "extended",
            "The number of the profile phase running at that sample, as in the phase lines.",
        ),
        (
            "curve_pump_flow",
            "Pump flow",
            Channel("fl", "pump flow (ml/s)", flow, "pressure"),
            "excluded",
            "Water the pump delivered, in ml/s, before any is held back by the puck.",
        ),
        (
            "curve_scale_flow",
            "Scale flow",
            Channel("vf", "scale flow (g/s)", flow, "scale"),
            "excluded",
            "How fast the scale's weight rose, in g/s. Absent without a scale.",
        ),
        (
            "curve_estimated_weight",
            "Estimated weight",
            Channel("ev", "estimated weight (g)", grams),
            "excluded",
            "The machine's estimate of the weight in the cup from the flow, in grams.",
        ),
        (
            "curve_target_temperature",
            "Target temperature",
            Channel("tt", "target temperature (°C)", temp),
            "excluded",
            "The temperature the profile commanded, in °C.",
        ),
        (
            "curve_resistance",
            "Resistance",
            Channel("pr", "resistance", _decimals(RESISTANCE_SCALE), "pressure"),
            "excluded",
            "The machine's own puck resistance reading. Absent without a pressure sensor.",
        ),
        (
            "curve_water_pumped",
            "Water pumped (firmware v7 and later)",
            Channel("wp", "water pumped (ml)", grams, "pressure"),
            "excluded",
            "Water pumped since the start of the shot, in ml; recorded from firmware log v7.",
        ),
    )
    return tuple(
        Item(
            key=key,
            group=group,
            name=name,
            label=channel.header,
            meaning=meaning,
            default_tier=tier,
            channel=channel,
        )
        for key, name, channel, tier, meaning in specs
    )


#: The catalogue. A tuple, iterated in order: the groups and their rows are
#: the order a rendering and the glossary are written in.
#: The unit of each item's value, where it has one.
_UNITS: Mapping[str, str] = MappingProxyType(
    {
        "shot_time": "s",
        "yield": "g",
        "yield_share": "%",
        "first_drip": "s",
        "preinfusion_time": "s",
        "main_extraction_time": "s",
        "average_temperature": "°C",
        "target_temperature": "°C",
        "minimum_temperature": "°C",
        "maximum_temperature": "°C",
        "peak_pressure": "bar",
        "average_pressure": "bar",
        "minimum_pressure": "bar",
        "peak_pressure_time": "s",
        "brew_flow": "ml/s",
        "average_flow": "ml/s",
        "peak_flow": "ml/s",
        "total_volume": "ml",
        "water_pumped": "ml",
        "water_minus_weight": "g",
        "weight_rate": "g/s",
        "machine_puck_resistance": "s·√bar/mL",
        "liquid_resistance": "bar·s/mL",
        "resistance_slope": "1/s",
        "pressure_adherence": "bar",
        "flow_adherence": "ml/s",
        "pressure_undershoot_max": "bar",
        "rating": "stars",
        "dose_in": "g",
        "dose_out": "g",
        "phase_start": "s",
        "phase_duration": "s",
        "phase_pressure": "bar",
        "phase_pressure_peak": "bar",
        "phase_pressure_end": "bar",
        "phase_temperature": "°C",
        "phase_temperature_min": "°C",
        "phase_temperature_target": "°C",
        "phase_volume": "ml",
        "phase_flow": "ml/s",
        "phase_flow_peak": "ml/s",
        "phase_scale_flow": "g/s",
        "phase_scale_flow_peak": "g/s",
        "phase_cup_end": "g",
        "phase_cup_gained": "g",
        "phase_cup_share": "%",
        "phase_water": "ml",
        "phase_first_drip": "s",
        "phase_pressure_adherence": "bar",
        "phase_flow_error": "ml/s",
        "phase_ramp": "bar/s",
        "phase_saturation": "s",
        "phase_taper": "bar/s",
        "phase_resistance_slope": "1/s",
        "phase_machine_resistance": "s·√bar/mL",
        "phase_liquid_resistance": "bar·s/mL",
    }
)

#: The structured accessor of each shot item that has a number (or a structure)
#: behind its sentence. Every one is the very function the sentence is made from,
#: and ``tests/shotinfo/test_field_contract.py`` holds the two to each other.
_SHOT_VALUES: Mapping[str, ShotValue] = MappingProxyType(
    {
        "shot_time": shot_time,
        "yield": yield_g,
        "first_drip": first_drip,
        "preinfusion_time": lambda f: (
            f.summary_value("extraction", "preinfusion_time_s") if f.has_pressure else None
        ),
        "main_extraction_time": lambda f: (
            f.summary_value("extraction", "main_extraction_time_s") if f.has_pressure else None
        ),
        "average_temperature": lambda f: _temperature(f, "avg_c"),
        "target_temperature": lambda f: _temperature(f, "target_avg_c"),
        "minimum_temperature": lambda f: _temperature(f, "min_c"),
        "maximum_temperature": lambda f: _temperature(f, "max_c"),
        "peak_pressure": peak_pressure,
        "average_pressure": lambda f: _pressure_summary(f, "avg_bar"),
        "minimum_pressure": lambda f: _pressure_summary(f, "min_bar"),
        "peak_pressure_time": lambda f: _pressure_summary(f, "peak_time_s"),
        "brew_flow": brew_flow,
        "average_flow": lambda f: _flow_summary(f, "avg_flow_ml_s"),
        "peak_flow": lambda f: _flow_summary(f, "peak_flow_ml_s"),
        "total_volume": lambda f: _flow_summary(f, "total_volume_ml"),
        "water_pumped": lambda f: number(f.firmware.get("water_pumped_ml")),
        "water_minus_weight": lambda f: number(f.firmware.get("water_minus_weight_g")),
        "weight_rate": lambda f: f.section_value("weight", "rate_avg_g_s"),
        "shot_id": lambda f: f.shot_id,
        "phases_not_reached": _phases_not_reached_value,
        "resistance_level": _resistance_avg,
        "resistance_slope": _resistance_slope,
        "machine_puck_resistance": lambda f: _firmware_stats_value(f.firmware.get("pr")),
        "liquid_resistance": lambda f: _firmware_stats_value(f.firmware.get("lr")),
        "pressure_adherence": _pressure_rmse,
        "flow_adherence": _flow_rmse,
        "pressure_undershoot_max": lambda f: _compliance(f, "max_pressure_undershoot_bar", None),
        "rating": rating,
        "dose_in": dose_in,
        "dose_out": dose_out,
        "ratio": ratio,
    }
)

#: The same for the per-phase items.
_PHASE_VALUES: Mapping[str, PhaseValue] = MappingProxyType(
    {
        "phase_start": lambda _, p: _phase_number(p, "start_time_seconds"),
        "phase_duration": lambda _, p: _phase_number(p, "duration_seconds"),
        "phase_pressure": lambda f, p: (
            _phase_number(p, "avg_pressure_bar") if f.has_pressure else None
        ),
        "phase_pressure_peak": _phase_number_of("pressure_peak_bar", needs="pressure"),
        "phase_pressure_end": _phase_number_of("pressure_end_bar", needs="pressure"),
        "phase_temperature": lambda _, p: _positive(_phase_number(p, "avg_temperature_c")),
        "phase_temperature_min": lambda _, p: _positive(_phase_metric(p, "temperature_min_c")),
        "phase_temperature_target": lambda _, p: _phase_metric(p, "temperature_target_c"),
        "phase_volume": lambda f, p: (
            _phase_number(p, "total_flow_ml") if f.puck_flow_recorded else None
        ),
        "phase_flow": lambda f, p: (
            _phase_diag_number(p, "avg_flow_ml_s") if f.puck_flow_recorded else None
        ),
        "phase_flow_peak": _phase_number_of("puck_flow_peak_ml_s", needs="puck_flow"),
        "phase_scale_flow": _phase_number_of("scale_flow_mean_g_s", needs="scale"),
        "phase_scale_flow_peak": _phase_number_of("scale_flow_peak_g_s", needs="scale"),
        "phase_cup_end": _phase_number_of("cup_weight_end_g", needs="scale"),
        "phase_cup_gained": _phase_number_of("cup_weight_gained_g", needs="scale"),
        "phase_cup_share": _phase_cup_share,
        "phase_water": _phase_number_of("water_pumped_ml"),
        "phase_samples": lambda _, p: (
            int(count) if (count := _phase_number(p, "sample_count")) is not None else None
        ),
        "phase_first_drip": lambda _, p: _phase_metric(p, "first_drip_s"),
        "phase_resistance": lambda _, p: _phase_resistance_level(p),
        "phase_resistance_slope": lambda _, p: (
            _phase_diag_number(p, "resistance_slope")
            if _phase_resistance_level(p) is not None
            else None
        ),
        "phase_pressure_adherence": lambda _, p: _phase_diag_number(p, "pressure_rmse_bar"),
        "phase_flow_error": lambda _, p: _phase_diag_number(p, "flow_rmse_ml_s"),
        "phase_ramp": lambda _, p: _phase_diag_number(p, "ramp_rate_bar_s"),
        "phase_saturation": lambda _, p: _phase_diag_number(p, "saturation_time_s"),
        "phase_taper": lambda _, p: _phase_diag_number(p, "taper_rate_bar_s"),
        "phase_machine_resistance": lambda f, p: _firmware_phase_value(f, p, "pr"),
        "phase_liquid_resistance": lambda f, p: _firmware_phase_value(f, p, "lr"),
    }
)


def _firmware_phase_value(
    f: ShotFacts, phase: Mapping[str, Any], stream: str
) -> dict[str, Any] | None:
    index = number(phase.get("phase_number"))
    for entry in f.firmware.get("phases") or []:
        if isinstance(entry, dict) and number(entry.get("phase_number")) == index:
            return _firmware_stats_value(entry.get(stream))
    return None


def _finish(items: tuple[Item, ...]) -> tuple[Item, ...]:
    """Each item with its method id, its unit and its structured accessor.

    Kept in tables of their own, next to the ids in :mod:`~gaggiclanker.shotinfo.methods`,
    so a computation and its identity are read in one place.
    """
    return tuple(
        replace(
            item,
            method=METHODS[item.key],
            unit=_UNITS.get(item.key) or _channel_unit(item),
            shot_value=_SHOT_VALUES.get(item.key, item.shot_value),
            phase_value=_PHASE_VALUES.get(item.key, item.phase_value),
        )
        for item in items
    )


def _channel_unit(item: Item) -> str:
    """The unit in a curve column's header, ``pressure (bar)``."""
    if item.channel is None:
        return ""
    _, _, rest = item.channel.header.partition(" (")
    return rest.removesuffix(")") if rest else ""


CATALOGUE: tuple[Item, ...] = _finish(_items())

#: The same items by key.
ITEMS: Mapping[str, Item] = MappingProxyType({item.key: item for item in CATALOGUE})

#: The groups, in the order they are rendered.
GROUPS: tuple[str, ...] = tuple(dict.fromkeys(item.group for item in CATALOGUE))


def default_tiers() -> Mapping[str, Tier]:
    """Every item at its default tier."""
    return MappingProxyType({item.key: item.default_tier for item in CATALOGUE})


async def effective_tiers(db: Database) -> Mapping[str, Tier]:
    """The tier each item is in **now**: the one read everything renders through.

    The catalogue's defaults with the person's choices laid over them (the
    overrides Settings → Shot information stores), read in one query. Every
    caller asks at the moment it renders — the chat runner once per turn, each
    shot tool once per call, the stdio tool server the same way — so a tier
    changed on the settings page applies from the next turn, with no restart
    and no cache to go stale. Nothing reads an item's ``default_tier`` except
    through this.

    A stored choice this catalogue cannot honour is skipped rather than
    raised, since a chat turn is no place to fail over a setting: a key a
    later release removed, and a locked item, which the settings route
    refuses to move but a hand-written row could still name. Each is logged
    once per process, not on every turn that reads it.
    """
    # Imported here: the repository's model validates keys against this
    # module's catalogue, so importing it at the top would be a cycle.
    from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository

    tiers = dict(default_tiers())
    for key, tier in (await ShotInfoTiersRepository(db).overrides()).items():
        item = ITEMS.get(key)
        if item is None or item.locked:
            if key not in _IGNORED_LOGGED:
                _IGNORED_LOGGED.add(key)
                log.warning(
                    "shot_info_override_ignored",
                    item_key=key,
                    reason="unknown" if item is None else "locked",
                )
            continue
        tiers[key] = tier
    return MappingProxyType(tiers)


#: The stored choices already reported as ignored, so a stale row is one log
#: line per process rather than one per chat turn.
_IGNORED_LOGGED: set[str] = set()


def keys_in(tier: ShotTier, tiers: Mapping[str, Tier]) -> frozenset[str]:
    """The item keys a rendering at ``tier`` carries."""
    wanted: tuple[Tier, ...] = ("base", "extended") if tier == "full" else (tier,)
    return frozenset(key for key, sits in tiers.items() if sits in wanted and key in ITEMS)
