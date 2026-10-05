"""A shot's fields as the field contract serves them: structured, in catalogue order.

The shot page and anything after it read a shot here rather than out of the
diagnostics blob: every value is ``{value, unit, phase, window, method, source}``
with the sentence a chat reads beside it, in the order of the catalogue, the
shot-wide ones apart from the per-phase ones, and annotated with what depends on
where the shot is filed (the target yield, the share of it, the warnings). A
value the machine did not record is absent, never zero; a field is only ever
comparable with another of the same key and method.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue

from gaggiclanker.db.connection import Database
from gaggiclanker.domain.warnings import badge_text
from gaggiclanker.shotinfo.catalogue import ALSO_SERVED, CATALOGUE, MEASURED_GROUPS, FieldValue
from gaggiclanker.shotinfo.facts import ShotFacts
from gaggiclanker.shotinfo.render import load_shots

__all__ = [
    "FieldOut",
    "PhaseFields",
    "ShotFields",
    "WarningOut",
    "shot_fields",
    "shot_fields_of",
]


class PhaseRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int | None
    name: str


class WindowOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_s: float
    to_s: float


class FieldOut(BaseModel):
    """One item's value on the shot (or on one of its phases)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    group: str
    name: str
    label: str
    #: The number, word or structure itself.
    value: JsonValue
    unit: str
    #: The phase the value is about, or ``None`` for a shot-wide value.
    phase: PhaseRef | None
    #: The seconds of the shot the value covers, for a phase value.
    window: WindowOut | None
    #: The computation behind the value: two values are comparable when the key
    #: and the method are both the same.
    method: str
    source: str
    #: The sentence a chat is given for the same value.
    text: str


class WarningOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The shot's own phase name, or ``Shot`` for a fault of the whole shot.
    phase: str
    fault: str
    #: ``red`` or ``amber``.
    severity: str
    detail: str
    phase_number: int | None
    at_s: float


class PhaseFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int | None
    name: str
    start_s: float | None
    duration_s: float | None
    fields: list[FieldOut]


class ShotFields(BaseModel):
    """`GET /api/shots/{id}/fields`."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    #: Most severe first, then in the order of the shot. Worked out when read.
    warnings: list[WarningOut]
    #: The badge text, built by code from the warnings: ``ramp: fast flow +1``.
    badge: str | None
    #: The target yield of the version the shot is filed under, when it has one.
    target_yield_g: float | None
    #: The yield as a share of it, in %.
    yield_share_pct: float | None
    #: The shot-wide fields, in catalogue order.
    shot: list[FieldOut]
    #: Each phase's fields, in the shot's order, each in catalogue order.
    phases: list[PhaseFields]


def _out(item_group: str, item_name: str, item_label: str, value: FieldValue) -> FieldOut:
    return FieldOut.model_validate(
        {
            **value.as_dict(),
            "group": item_group,
            "name": item_name,
            "label": item_label,
        }
    )


def shot_fields_of(facts: ShotFacts) -> ShotFields:
    """The document for a loaded shot."""
    shot_wide: list[FieldOut] = []
    for item in CATALOGUE:
        if item.shot is None or (item.group not in MEASURED_GROUPS and item.key not in ALSO_SERVED):
            continue
        found = item.field(facts)
        if found is not None:
            shot_wide.append(_out(item.group, item.name, item.label, found))

    phases: list[PhaseFields] = []
    for phase in facts.phases:
        fields: list[FieldOut] = []
        for item in CATALOGUE:
            if item.group not in MEASURED_GROUPS or item.phase is None:
                continue
            found = item.field(facts, phase)
            if found is not None:
                fields.append(_out(item.group, item.name, item.label, found))
        number: Any = phase.get("phase_number")
        start: Any = phase.get("start_time_seconds")
        length: Any = phase.get("duration_seconds")
        phases.append(
            PhaseFields(
                number=number if isinstance(number, int) and not isinstance(number, bool) else None,
                name=str(phase.get("name") or "").strip(),
                start_s=float(start) if isinstance(start, int | float) else None,
                duration_s=float(length) if isinstance(length, int | float) else None,
                fields=fields,
            )
        )

    found_warnings = facts.warnings
    share = facts.share_of_target(facts.shot.final_weight_g if facts.shot.scale_connected else None)
    return ShotFields(
        shot_id=facts.shot_id,
        warnings=[WarningOut.model_validate(w.as_dict()) for w in found_warnings],
        badge=badge_text(found_warnings),
        target_yield_g=facts.target_yield_g,
        yield_share_pct=share,
        shot=shot_wide,
        phases=phases,
    )


async def shot_fields(db: Database, shot_id: int) -> ShotFields | None:
    """One shot's fields, or ``None`` when there is no such shot."""
    loaded = await load_shots(db, [shot_id])
    return shot_fields_of(loaded[0]) if loaded else None
