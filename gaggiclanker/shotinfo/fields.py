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
from gaggiclanker.domain.signature import Check
from gaggiclanker.review.reading import ChecksBlock, ReviewBlock, serve_review
from gaggiclanker.shotinfo.catalogue import ALSO_SERVED, CATALOGUE, MEASURED_GROUPS, FieldValue
from gaggiclanker.shotinfo.facts import ShotFacts
from gaggiclanker.shotinfo.render import load_shots

__all__ = [
    "CheckOut",
    "FieldChecks",
    "FieldOut",
    "PhaseFields",
    "ShotFields",
    "SignatureStateOut",
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


class CheckOut(BaseModel):
    """One check of the shot's ordered list: a signature result or a universal warning."""

    model_config = ConfigDict(extra="forbid")

    #: ``measure``, ``reached``, ``expects_warning``, ``free_text`` or ``warning``.
    kind: str
    #: ``failed``, ``held``, ``unmeasured``, ``expected``, ``warning`` or ``unchecked``.
    status: str
    #: ``critical``, ``important`` or ``context``; ``None`` for a warning nothing marks as
    #: expected.
    tier: str | None
    #: ``red``, ``amber`` or ``grey`` for what the badge is made of; ``None`` otherwise.
    color: str | None
    #: The shot's own phase name, or ``Shot`` for the whole shot.
    phase: str
    phase_number: int | None
    #: The fault word, when the check failed or the warning was raised.
    fault: str | None
    #: The expectation's sentence (the language's own for a measure).
    sentence: str
    #: The sentence with this shot's number in it.
    detail: str
    #: A measure's value on this shot and its unit; a share of the target yield or the dose is
    #: served as a percentage (``117.2``, unit ``%``, with ``relative_to`` saying of what).
    #: ``None`` for a check with no number (a phase that must begin) or with the reason in
    #: ``absent``.
    value: float | None
    unit: str
    #: The limit the value was held against, **after** any Set version's override, as the metric
    #: language states it (``{"op": "<=", "value": 0.15}``: a share is a fraction here, not a
    #: percentage); ``None`` for a check with no limit.
    compare: JsonValue | None
    #: What a share is a share of: ``target_yield``, ``dose`` or ``final_weight``.
    relative_to: str | None
    #: The limit as a person reads it, with this version's override applied: ``at most 15 % of
    #: target``, ``at most 3 g/s``.
    limit_text: str
    #: Whether the comparison held; ``None`` when not measured or not compared.
    held: bool | None
    #: Why a value is absent, in words: a check that could not be measured is neither held
    #: nor failed.
    absent: str | None
    at_s: float
    expectation_id: int | None


class FieldChecks(ChecksBlock):
    """The Curve check of a shot with every check behind it.

    ``badge`` and ``entries`` are what the shots list serves; ``items`` is the whole ordered
    list, the held and the unmeasured and the free-text ones too (a free-text expectation is
    "checked by the review": the review's own claims say how it came out).
    """

    items: list[CheckOut]


class SignatureStateOut(BaseModel):
    """Whether the shot was read against a confirmed signature."""

    model_config = ConfigDict(extra="forbid")

    #: The profile version the shot brewed, whose signature applies (``None``: no profile).
    profile_version_id: int | None
    #: How many confirmed expectations that signature has; 0 reads as "without a signature".
    confirmed: int
    #: ``confirmed, 6 expectations`` or ``read without a signature``.
    text: str


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
    #: The Curve check: what the badge is made of (most severe first, then in the order of the
    #: shot: failed critical expectations, failed important ones, universal warnings, expected
    #: warnings) and every check in order. Worked out when read, and never changed by a review.
    checks: FieldChecks
    #: The review: what the model wrote, and whether there is one. The same block the shots list
    #: serves on every row.
    review: ReviewBlock
    #: Whether the shot was read against a confirmed signature.
    signature: SignatureStateOut
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


def _check_out(check: Check) -> CheckOut:
    value, unit = check.value, check.unit
    if unit == "share" and value is not None:
        value, unit = round(value * 100, 1), "%"
    return CheckOut(
        kind=check.kind,
        status=check.status,
        tier=check.tier,
        color=check.color,
        phase=check.phase,
        phase_number=check.phase_number,
        fault=check.fault,
        sentence=check.sentence,
        detail=check.detail,
        value=value,
        unit=unit,
        compare=(
            check.compare.model_dump(mode="json", exclude_none=True)
            if check.compare is not None
            else None
        ),
        relative_to=check.relative_to,
        limit_text=check.limit_text,
        held=check.held,
        absent=check.absent,
        at_s=check.at_s,
        expectation_id=check.expectation_id,
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

    served = serve_review(
        facts.signature_checks,
        facts.reading,
        reviewable=not facts.shot.quarantined and facts.shot.judgement_decision != "discard",
    )
    checks = served.checks
    share = facts.share_of_target(facts.shot.final_weight_g if facts.has_scale else None)
    return ShotFields(
        shot_id=facts.shot_id,
        checks=FieldChecks(
            badge=served.checks_block.badge,
            entries=served.checks_block.entries,
            items=[_check_out(c) for c in checks.checks],
        ),
        review=served.review,
        signature=SignatureStateOut(
            profile_version_id=checks.state.profile_version_id,
            confirmed=checks.state.confirmed,
            text=checks.state.text,
        ),
        target_yield_g=facts.target_yield_g,
        yield_share_pct=share,
        shot=shot_wide,
        phases=phases,
    )


async def shot_fields(db: Database, shot_id: int) -> ShotFields | None:
    """One shot's fields, or ``None`` when there is no such shot."""
    loaded = await load_shots(db, [shot_id])
    return shot_fields_of(loaded[0]) if loaded else None
