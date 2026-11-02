"""Which carried expectations a patch to a profile touches.

A draft carries its base's expectations to the new version by phase name alone, so a row
written under the old numbers (a temperature floor 2 °C under the old 95 °C setpoint) goes on
being checked against the new ones (93 °C) with nobody told. :func:`affected_by` is the pure
function that says, for a base document, the document the patch makes and the base's
expectations, which of them read a value the patch changed, and why, in words.

The table (one flag per expectation, the causes of every row that applies joined):

=========================== ================================================================
the patch changes           expectations flagged
=========================== ================================================================
the profile's temperature   measures on ``temperature`` or ``target_temperature`` with an
                            absolute limit (never one that follows the setpoint)
a phase's pressure          measures on ``pressure``, ``target_pressure``, ``resistance``
                            whose window is that phase, or the whole shot
a phase's flow              measures on ``target_flow``, ``pump_flow``, ``puck_flow``,
                            ``scale_flow`` in that window
a phase's volumetric target absolute ``cup_weight`` measures in that window (a share of the
                            target yield follows the filing already)
a phase's pumped target     ``water_pumped`` measures in that window
a phase's duration or exit  ``duration``, ``time_to``, ``time_above``, ``time_below`` in that
                            window
a phase renamed or removed  none: the expectation becomes "needs a new phase", as before
=========================== ================================================================

Only measures that read a **level or a time** are flagged: a measure of a change, a slope, an
integral, a jitter or a gain does not move with the number it is a difference of. A window is a
phase (by name or number), the whole shot (every phase), or a span: between the first and the
last phase its anchors name, and the whole shot when an anchor names none.

Pure Python over the documents as the profile model dumps them; no database.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gaggiclanker.domain.metric_language import (
    Expression,
    PhaseEndAnchor,
    PhaseStartAnchor,
)
from gaggiclanker.domain.phase_names import phase_key
from gaggiclanker.domain.signature import sentence_with_unit

__all__ = ["Affected", "affected_by"]

#: The ops whose value (or threshold) is a level of the channel or a time.
_LEVEL_OPS = frozenset({"mean", "min", "max", "at_start", "at_end"})
_TIME_OPS = frozenset({"duration", "time_to", "time_above", "time_below"})
_READS_A_LEVEL = _LEVEL_OPS | frozenset({"time_to", "time_above", "time_below"})

_TEMPERATURE = frozenset({"temperature", "target_temperature"})
_PRESSURE = frozenset({"pressure", "target_pressure", "resistance"})
_FLOW = frozenset({"target_flow", "pump_flow", "puck_flow", "scale_flow"})


@dataclass(frozen=True, slots=True)
class Affected:
    """One expectation the patch touches, and the cause in words."""

    expectation_id: int
    sentence: str
    cause: str


def _num(value: float) -> str:
    return f"{value:g}"


def _phases(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    phases = document.get("phases")
    return [p for p in phases if isinstance(p, Mapping)] if isinstance(phases, list) else []


def _name(phase: Mapping[str, Any]) -> str:
    return str(phase.get("name") or "")


def _pump(phase: Mapping[str, Any], field: str) -> float | None:
    pump = phase.get("pump")
    if isinstance(pump, Mapping) and isinstance(pump.get(field), int | float):
        return float(pump[field])
    return None


def _targets(phase: Mapping[str, Any], kinds: Sequence[str]) -> tuple[tuple[str, float], ...]:
    found: list[tuple[str, float]] = []
    for target in phase.get("targets") or []:
        if isinstance(target, Mapping) and target.get("type") in kinds:
            value = target.get("value")
            if isinstance(value, int | float):
                found.append((f"{target['type']} {target.get('operator', 'gte')}", float(value)))
    return tuple(sorted(found))


def _exit(phase: Mapping[str, Any]) -> tuple[tuple[str, float], ...]:
    return _targets(phase, ("volumetric", "pressure", "flow", "pumped"))


def _temperature(document: Mapping[str, Any]) -> float | None:
    value = document.get("temperature")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    # The firmware's 0 is "not set", as everywhere else (`profile_recipe`).
    return float(value) if value > 0 else None


def _positions_named(expression: Expression, names: Sequence[str]) -> list[int] | None:
    """The phases a window covers, as positions in the new document; ``None`` is every phase."""
    window = expression.window
    keys = [phase_key(n) for n in names]
    if window.phase is not None:
        wanted = phase_key(window.phase)
        return [keys.index(wanted)] if wanted in keys else []
    if window.phase_number is not None:
        return [window.phase_number] if window.phase_number < len(names) else []
    if window.start is not None and window.end is not None:
        anchored: list[int] = []
        for anchor in (window.start, window.end):
            if isinstance(anchor, PhaseStartAnchor):
                name = anchor.phase_start
            elif isinstance(anchor, PhaseEndAnchor):
                name = anchor.phase_end
            else:
                return None
            wanted = phase_key(name)
            if wanted not in keys:
                return []
            anchored.append(keys.index(wanted))
        return list(range(min(anchored), max(anchored) + 1))
    return None


def _names_a_missing_phase(expression: Expression, new_names: Sequence[str]) -> bool:
    """Whether a phase the expression's window names is gone (the row then needs a new phase)."""
    window = expression.window
    keys = {phase_key(n) for n in new_names}
    named = [window.phase] if window.phase is not None else []
    for anchor in (window.start, window.end):
        if isinstance(anchor, PhaseStartAnchor):
            named.append(anchor.phase_start)
        elif isinstance(anchor, PhaseEndAnchor):
            named.append(anchor.phase_end)
    return any(phase_key(n) not in keys for n in named)


def affected_by(
    base_document: Mapping[str, Any],
    new_document: Mapping[str, Any],
    expectations: Sequence[Any],
) -> list[Affected]:
    """The expectations (the base's, in force) that read a value the patch changed.

    ``expectations`` are rows with ``id``, ``kind``, ``expression`` and ``sentence``. One flag
    per expectation, in the order given, its causes joined with "; ".
    """
    base_phases, new_phases = _phases(base_document), _phases(new_document)
    new_names = [_name(p) for p in new_phases]
    new_keys = [phase_key(n) for n in new_names]

    temperature_cause: str | None = None
    before, after = _temperature(base_document), _temperature(new_document)
    if before != after:
        if before is not None and after is not None:
            temperature_cause = f"the temperature changed from {_num(before)} to {_num(after)} °C"
        elif before is None:
            temperature_cause = f"the temperature was not set and is now {_num(after or 0)} °C"
        else:
            temperature_cause = f"the temperature was {_num(before)} °C and is now not set"

    # What changed in each phase both documents have, by position in the new document.
    changes: dict[int, list[tuple[str, frozenset[str], bool, str]]] = {}
    base_by_key: dict[str, Mapping[str, Any]] = {}
    for phase in base_phases:
        base_by_key.setdefault(phase_key(_name(phase)), phase)
    for position, phase in enumerate(new_phases):
        old = base_by_key.get(new_keys[position])
        if old is None:
            continue
        name = _name(phase).strip()
        found: list[tuple[str, frozenset[str], bool, str]] = []
        # (group, channels, is_time, cause); `group` only keeps two causes apart.
        a, b = _pump(old, "pressure"), _pump(phase, "pressure")
        if a != b:
            found.append(
                (
                    "pressure",
                    _PRESSURE,
                    False,
                    f"the {name} pressure target changed from {_fmt(a, 'bar')} to {_fmt(b, 'bar')}",
                )
            )
        a, b = _pump(old, "flow"), _pump(phase, "flow")
        if a != b:
            found.append(
                (
                    "flow",
                    _FLOW,
                    False,
                    f"the {name} flow target changed from {_fmt(a, 'ml/s')} to {_fmt(b, 'ml/s')}",
                )
            )
        if _targets(old, ("volumetric",)) != _targets(phase, ("volumetric",)):
            found.append(
                (
                    "volumetric",
                    frozenset({"cup_weight"}),
                    False,
                    f"the {name} weight target changed",
                )
            )
        if _targets(old, ("pumped",)) != _targets(phase, ("pumped",)):
            found.append(
                ("pumped", frozenset({"water_pumped"}), False, f"the {name} pumped target changed")
            )
        causes: list[str] = []
        if old.get("duration") != phase.get("duration"):
            causes.append(
                f"the {name} duration changed from {_fmt(old.get('duration'), 's')} to "
                f"{_fmt(phase.get('duration'), 's')}"
            )
        if _exit(old) != _exit(phase):
            causes.append(f"what ends the {name} changed")
        if causes:
            found.append(("exit", frozenset(), True, "; ".join(causes)))
        if found:
            changes[position] = found

    flags: list[Affected] = []
    for row in expectations:
        expression: Expression | None = row.expression
        if row.kind != "measure" or expression is None:
            continue
        if _names_a_missing_phase(expression, new_names):
            continue
        causes = []
        if (
            temperature_cause is not None
            and expression.channel in _TEMPERATURE
            and expression.offset_from is None
            and expression.op in _READS_A_LEVEL
        ):
            causes.append(temperature_cause)
        covered = _positions_named(expression, new_names)
        for position in range(len(new_phases)) if covered is None else covered:
            for _group, channels, is_time, cause in changes.get(position, []):
                if is_time:
                    hit = expression.op in _TIME_OPS
                else:
                    hit = (
                        expression.channel in channels
                        and expression.op in _READS_A_LEVEL
                        # A share of a filed weight follows the filing, not the patch.
                        and expression.relative_to is None
                        and expression.offset_from is None
                    )
                if hit and cause not in causes:
                    causes.append(cause)
        if causes:
            flags.append(
                Affected(row.id, sentence_with_unit(row.sentence, expression), "; ".join(causes))
            )
    return flags


def _fmt(value: Any, unit: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "none"
    return f"{_num(float(value))} {unit}"
