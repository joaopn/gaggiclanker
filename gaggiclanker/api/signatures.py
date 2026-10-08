"""`/api/profile-versions/{id}/signature` and the answers a person gives to expectations.

A signature is what one profile version is for. An agent **proposes** expectations
(`propose_signature`, a draft that carries some, or the carrying of a signature to a new
version) and they are in force at once; everything here is a person's press: read the
signature, reject an expectation, restore a rejected one, move one to another tier, and reject
or restore a Set version's override. There is no tool for any of it, in the chat or over MCP.

Every answer is one guarded write (`SignatureRepository`): the first press wins, a second tab
finds the expectation answered and gets a 409, never a second write. Only a **confirmed**
(in force) expectation is ever checked, shown as a verdict or told to an agent.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from gaggiclanker.api.deps import DatabaseDep, ProfilesRepoDep, SetsRepoDep
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.signatures import (
    ExpectationRow,
    OverrideRow,
    SignatureRepository,
)
from gaggiclanker.domain.metric_language import Compare, compare_words, expression_unit
from gaggiclanker.domain.phase_metrics import profile_phase_names
from gaggiclanker.domain.signature import fault_words, limit_text
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import Conflict, NotFound

__all__ = ["router"]

router = APIRouter(tags=["signatures"])

REASON_MAX = 300


def get_signature_repo(db: DatabaseDep) -> SignatureRepository:
    return SignatureRepository(db)


SignatureRepoDep = Annotated[SignatureRepository, Depends(get_signature_repo)]


class ExpectationOut(BaseModel):
    """One expectation, with everything a card shows and who proposed it."""

    model_config = ConfigDict(extra="forbid")

    id: int
    profile_version_id: int
    position: int
    tier: Literal["critical", "important", "context"]
    #: The profile's phase name, or ``None`` for the whole shot.
    phase: str | None
    kind: Literal["measure", "reached", "expects_warning", "free_text"]
    #: The metric-language expression of a measure (its limit is ``compare`` inside it).
    expression: JsonValue | None = None
    #: ``False`` when the stored expression can no longer be read: shown, never checked.
    readable: bool = True
    #: The universal warning an ``expects_warning`` names.
    warning: str | None = None
    #: The language's own sentence for a measure, a fixed one for the other kinds.
    sentence: str
    #: The one fault word it fails with; ``None`` for a measure bounded on both sides.
    fault: str | None
    #: Every word it can fail with, under its limit first (two when bounded both ways).
    faults: list[str]
    #: The proposer's reason.
    reason: str
    status: Literal["proposed", "confirmed", "rejected"]
    #: Why a person rejected it, when they said.
    reject_reason: str
    #: A carried expectation whose phase no longer exists: shown, rejectable, never in force.
    #: (A ``proposed`` row without this flag is one from before expectations were in force at once.)
    needs_a_new_phase: bool
    #: The conversation that proposed it, when a chat did.
    proposed_by_thread_id: int | None
    #: The draft that carried it, when a draft did.
    proposed_by_draft_id: int | None
    #: The expectation of an earlier profile version it was carried from.
    carried_from_id: int | None
    carried_from_version_id: int | None
    proposed_at: str
    answered_at: str | None


class SetUsingOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    set_id: int
    set_name: str
    archived: bool
    #: The newest version of that Set on this profile version.
    version_id: int
    version_label: str
    is_current: bool


class SignatureData(BaseModel):
    """`GET /api/profile-versions/{id}/signature`."""

    model_config = ConfigDict(extra="forbid")

    profile_version_id: int
    profile_label: str
    #: The profile's phase names, in order: what an expectation's phase is one of.
    phases: list[str]
    #: How many expectations are in force, and how many the person rejected.
    confirmed: int
    #: Rows shown and checked on no shot: a carried one whose phase is gone, or one proposed
    #: before expectations were in force at once. They can be rejected, never restored.
    not_in_force: int
    rejected: int
    #: Tier order (critical, important, context), then in the order they were written.
    expectations: list[ExpectationOut]
    #: The Sets with a version on this profile version, for "ask for a signature" links.
    sets: list[SetUsingOut]


class SignatureAnswer(BaseModel):
    """What answering one expectation did, and the signature as it now stands."""

    model_config = ConfigDict(extra="forbid")

    #: The expectations this call changed, in order.
    changed: list[ExpectationOut]
    signature: SignatureData


class RejectBody(BaseModel):
    """`POST .../reject`: an optional one-line reason, which the proposing conversation is told."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=REASON_MAX)


class TierBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: Literal["critical", "important", "context"]


def _compare_json(compare: Compare | None) -> dict[str, Any] | None:
    return None if compare is None else compare.model_dump(mode="json", exclude_none=True)


def expectation_out(row: ExpectationRow) -> ExpectationOut:
    words: list[str] = []
    if row.expression is not None and row.expression.compare is not None:
        found = fault_words(row.expression)
        op = row.expression.compare.op
        wanted = (
            (found.under, found.over)
            if op == "between"
            else ((None, found.over) if op in ("<", "<=") else (found.under, None))
        )
        words = [w for w in dict.fromkeys(wanted) if w is not None]
    elif row.fault is not None:
        words = [row.fault]
    return ExpectationOut(
        id=row.id,
        profile_version_id=row.profile_version_id,
        position=row.position,
        tier=row.tier,
        phase=row.phase,
        kind=row.kind,
        expression=(
            row.expression.model_dump(mode="json", by_alias=True, exclude_none=True)
            if row.expression is not None
            else None
        ),
        readable=row.readable,
        warning=row.warning_fault,
        sentence=row.sentence,
        fault=row.fault,
        faults=words,
        reason=row.reason,
        status=row.status,
        reject_reason=row.reject_reason,
        needs_a_new_phase=row.needs_phase,
        proposed_by_thread_id=row.proposed_by_thread_id,
        proposed_by_draft_id=row.proposed_by_draft_id,
        carried_from_id=row.carried_from_id,
        carried_from_version_id=row.carried_from_version_id,
        proposed_at=row.proposed_at,
        answered_at=row.answered_at,
    )


async def _signature(
    version_id: int, signatures: SignatureRepository, profiles: ProfilesRepository
) -> SignatureData:
    version = await profiles.get_version(version_id)
    if version is None:
        raise NotFound(f"No profile version {version_id}")
    rows = await signatures.for_version(version_id)
    return SignatureData(
        profile_version_id=version_id,
        profile_label=version.label,
        phases=profile_phase_names(version.profile) or [],
        confirmed=sum(1 for r in rows if r.status == "confirmed"),
        not_in_force=sum(1 for r in rows if r.status == "proposed"),
        rejected=sum(1 for r in rows if r.status == "rejected"),
        expectations=[expectation_out(r) for r in rows],
        sets=[SetUsingOut.model_validate(item) for item in await signatures.sets_using(version_id)],
    )


@router.get(
    "/profile-versions/{version_id}/signature",
    response_model=ApiResponse[SignatureData],
    summary="What a profile version is for: its expectations and their status",
)
async def get_signature(
    version_id: int, signatures: SignatureRepoDep, profiles: ProfilesRepoDep
) -> JSONResponse:
    """Every expectation, proposed, confirmed and rejected, with who proposed it.

    A profile version nobody proposed anything for answers with no expectations (and the Sets
    that use it, for a link to ask the agent), not a 404.
    """
    return envelope_response(
        (await _signature(version_id, signatures, profiles)).model_dump(mode="json")
    )


async def _answered(
    expectation_id: int,
    changed: list[ExpectationRow],
    signatures: SignatureRepository,
    profiles: ProfilesRepository,
) -> JSONResponse:
    current = await signatures.get(expectation_id) if not changed else changed[0]
    assert current is not None
    return envelope_response(
        SignatureAnswer(
            changed=[expectation_out(row) for row in changed],
            signature=await _signature(current.profile_version_id, signatures, profiles),
        ).model_dump(mode="json")
    )


def _refusal(refused: str, expectation_id: int) -> Exception:
    if refused == "already_rejected":
        return Conflict(
            f"Expectation {expectation_id} has already been rejected",
            code="EXPECTATION_REJECTED",
            details={"field": "status", "message": "it is rejected"},
        )
    if refused == "not_in_force":
        return Conflict(
            f"Expectation {expectation_id} is not in force, so it has no tier to move",
            code="EXPECTATION_NOT_IN_FORCE",
            details={"field": "status", "message": "only an expectation in force has a tier"},
        )
    if refused == "not_rejected":
        return Conflict(
            f"Expectation {expectation_id} is not rejected, so there is nothing to restore",
            code="EXPECTATION_NOT_REJECTED",
            details={"field": "status", "message": "only a rejected expectation can be restored"},
        )
    if refused == "needs_phase":
        return Conflict(
            f"Expectation {expectation_id} names a phase this profile version no longer has",
            code="NEEDS_A_NEW_PHASE",
            details={
                "field": "phase",
                "message": "ask the agent to propose it again with a phase the profile has",
            },
        )
    return NotFound(f"No expectation {expectation_id}")


@router.post(
    "/signature-expectations/{expectation_id}/reject",
    response_model=ApiResponse[SignatureAnswer],
    summary="Reject one expectation: it stops being checked, with the reason the agent is told",
)
async def reject_expectation(
    expectation_id: int, body: RejectBody, signatures: SignatureRepoDep, profiles: ProfilesRepoDep
) -> JSONResponse:
    """Nothing is checked against it any more. The reason is what the proposing conversation
    is told. Works on one in force and on a carried one that needs a new phase; 409 when it is
    already rejected."""
    result = await signatures.reject(expectation_id, reason=" ".join(body.reason.split()))
    if result.row is None:
        raise _refusal(result.refused or "no_expectation", expectation_id)
    return await _answered(expectation_id, [result.row], signatures, profiles)


@router.post(
    "/signature-expectations/{expectation_id}/restore",
    response_model=ApiResponse[SignatureAnswer],
    summary="Restore a rejected expectation: it is in force again",
)
async def restore_expectation(
    expectation_id: int, signatures: SignatureRepoDep, profiles: ProfilesRepoDep
) -> JSONResponse:
    """A person's press, and the only way a rejected expectation returns (an agent cannot put
    it back). 409 when it is not rejected, or names a phase the profile no longer has."""
    result = await signatures.restore(expectation_id)
    if result.row is None:
        raise _refusal(result.refused or "no_expectation", expectation_id)
    return await _answered(expectation_id, [result.row], signatures, profiles)


@router.post(
    "/signature-expectations/{expectation_id}/tier",
    response_model=ApiResponse[SignatureAnswer],
    summary="Move an expectation to another tier",
)
async def set_expectation_tier(
    expectation_id: int, body: TierBody, signatures: SignatureRepoDep, profiles: ProfilesRepoDep
) -> JSONResponse:
    """An expectation in force; a rejected one is restored first, and one that is not in force
    (it needs a phase, or is from before) cannot be moved: 409."""
    result = await signatures.set_tier(expectation_id, body.tier)
    if result.row is None:
        raise _refusal(result.refused or "no_expectation", expectation_id)
    return await _answered(expectation_id, [result.row], signatures, profiles)


# ── a Set version's override ────────────────────────────────────────


class OverrideOut(BaseModel):
    """An override of one expectation's limit on one Set version, with what it overrides."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_version_id: int
    expectation_id: int
    #: ``withdrawn``: a newer override for the same expectation replaced it (not a rejection).
    status: Literal["proposed", "confirmed", "rejected", "withdrawn"]
    reason: str
    reject_reason: str
    proposed_by_thread_id: int | None
    proposed_at: str
    answered_at: str | None
    #: The limit this Set version would read: ``{op, value}`` or ``{op, low, high}``.
    compare: JsonValue | None
    compare_text: str
    #: What it overrides: the profile's limit, and the expectation it is a limit of.
    profile_compare: JsonValue | None
    profile_compare_text: str
    #: The two limits as a person reads them, with their unit: ``at most 20 % of target``,
    #: ``at most 4 g/s``. ``compare_text`` is the bare comparison in the language's own numbers
    #: (a share is a fraction there).
    limit_text: str
    profile_limit_text: str
    phase: str | None
    tier: str
    sentence: str
    #: Whether the expectation is in force: an override of one that is not cannot be restored.
    expectation_status: Literal["proposed", "confirmed", "rejected"]


class OverrideListData(BaseModel):
    """`GET /api/sets/{id}/versions/{id}/signature-overrides`: newest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[OverrideOut]


class OverrideAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    override: OverrideOut


async def _override_out(row: OverrideRow, signatures: SignatureRepository) -> OverrideOut:
    target = await signatures.get(row.expectation_id)
    base = target.expression.compare if target is not None and target.expression else None
    expression = target.expression if target is not None else None
    unit = expression_unit(expression) if expression is not None else ""
    return OverrideOut(
        id=row.id,
        set_version_id=row.set_version_id,
        expectation_id=row.expectation_id,
        status=row.status,
        reason=row.reason,
        reject_reason=row.reject_reason,
        proposed_by_thread_id=row.proposed_by_thread_id,
        proposed_at=row.proposed_at,
        answered_at=row.answered_at,
        compare=_compare_json(row.compare),
        compare_text=compare_words(row.compare) if row.compare is not None else "",
        profile_compare=_compare_json(base),
        profile_compare_text=compare_words(base) if base is not None else "",
        limit_text=(
            limit_text(expression.model_copy(update={"compare": row.compare}), unit)
            if expression is not None and row.compare is not None
            else ""
        ),
        profile_limit_text=limit_text(expression, unit) if expression is not None else "",
        phase=target.phase if target is not None else None,
        tier=target.tier if target is not None else "",
        sentence=target.sentence if target is not None else "",
        expectation_status=target.status if target is not None else "rejected",
    )


@router.get(
    "/sets/{set_id}/versions/{version_id}/signature-overrides",
    response_model=ApiResponse[OverrideListData],
    summary="The overrides proposed for one Set version's signature limits",
)
async def list_overrides(
    set_id: int, version_id: int, signatures: SignatureRepoDep, sets: SetsRepoDep
) -> JSONResponse:
    """In force, rejected and replaced, newest first, so a card read later tells the truth."""
    if await sets.version_of_set(set_id, version_id) is None:
        raise NotFound(f"No version {version_id} in Set {set_id}")
    rows = await signatures.overrides_for_set_version(version_id)
    items = [await _override_out(row, signatures) for row in reversed(rows)]
    return envelope_response(OverrideListData(items=items).model_dump(mode="json"))


def _override_refusal(refused: str, override_id: int) -> Exception:
    if refused == "already_rejected":
        return Conflict(
            f"Override {override_id} has already been rejected",
            code="OVERRIDE_REJECTED",
            details={"field": "status", "message": "it is rejected"},
        )
    if refused == "not_rejected":
        return Conflict(
            f"Override {override_id} is not rejected, so there is nothing to restore",
            code="OVERRIDE_NOT_REJECTED",
            details={"field": "status", "message": "only a rejected override can be restored"},
        )
    if refused == "another_in_force":
        return Conflict(
            f"Override {override_id} cannot be restored: this version has another in force",
            code="OVERRIDE_ANOTHER_IN_FORCE",
            details={
                "field": "status",
                "message": "a version has one override at a time: reject the other first",
            },
        )
    return Conflict(
        f"Override {override_id} is for an expectation that is not in force",
        code="EXPECTATION_NOT_CONFIRMED",
        details={
            "field": "expectation_id",
            "message": "restore the profile's expectation first",
        },
    )


async def _owned_override(signatures: SignatureRepository, set_id: int, override_id: int) -> None:
    row = await signatures.get_override(override_id)
    if row is None or await signatures.set_of_version(row.set_version_id) != set_id:
        raise NotFound(f"No signature override {override_id} in Set {set_id}")


@router.post(
    "/sets/{set_id}/signature-overrides/{override_id}/reject",
    response_model=ApiResponse[OverrideAnswer],
    summary="Reject an override: this version reads the profile's own limit again",
)
async def reject_override(
    set_id: int, override_id: int, body: RejectBody, signatures: SignatureRepoDep
) -> JSONResponse:
    await _owned_override(signatures, set_id, override_id)
    result = await signatures.reject_override(override_id, reason=" ".join(body.reason.split()))
    if result.row is None:
        raise _override_refusal(result.refused or "no_override", override_id)
    return envelope_response(
        OverrideAnswer(override=await _override_out(result.row, signatures)).model_dump(mode="json")
    )


@router.post(
    "/sets/{set_id}/signature-overrides/{override_id}/restore",
    response_model=ApiResponse[OverrideAnswer],
    summary="Restore a rejected override: this version's shots read its limit again",
)
async def restore_override(
    set_id: int, override_id: int, signatures: SignatureRepoDep
) -> JSONResponse:
    """A person's press. It applies to this Set version's shots and to no other."""
    await _owned_override(signatures, set_id, override_id)
    result = await signatures.restore_override(override_id)
    if result.row is None:
        raise _override_refusal(result.refused or "no_override", override_id)
    return envelope_response(
        OverrideAnswer(override=await _override_out(result.row, signatures)).model_dump(mode="json")
    )
