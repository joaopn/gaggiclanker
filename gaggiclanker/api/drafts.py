"""`/api/profile-drafts` — the only route in this app that can change a machine.

Everything here is a thin wrapper over
:class:`~gaggiclanker.drafts.service.ProfileDraftService`, which is where the
rules live. Two things about the shape of the surface are worth saying here,
because they are choices rather than consequences:

**Approve and push are separate calls.** They could be one. They are not,
because approving is where a person says "yes, this is the profile I want" and
pushing is where the machine gets written to, and a UI that made those the same
click would have no place to put the stop-condition acknowledgement — which is
the whole reason this feature has an approval step at all.

**Rollback is a POST on the draft, not a DELETE on the profile.** The thing
being undone is the push, and the draft is what knows which profile on the
machine that produced. A route that took a device id would happily delete
something nobody here created; this one can only reach what the audit says we
wrote.

Every route runs its work inside the request. A draft call is one LLM turn — ten
seconds, not the analysis's two minutes — and a push is three WebSocket frames,
so there is nothing here worth the second place an outcome could get lost.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.api.deps import DraftServiceDep
from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow
from gaggiclanker.db.repos.sets import SetVersionRow
from gaggiclanker.drafts.models import DraftPreview, ProfileDraftDetail
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import BadRequest

__all__ = ["router"]

router = APIRouter(prefix="/profile-drafts", tags=["profiles"])


class DraftListData(BaseModel):
    """The queue. Rows only — the documents are on the detail route."""

    model_config = ConfigDict(extra="forbid")

    items: list[ProfileDraftRow]


class DraftCreate(BaseModel):
    """Ask for a draft. Either the model writes it, or you did.

    `profile` present means "this document, validated" and no provider is
    contacted. `profile` absent means "draft one from the advice", which needs
    at least one of `analysis_id` or `suggestion_id` — a draft with nothing to
    go on is a model rewriting somebody's profile for no stated reason.
    """

    model_config = ConfigDict(extra="forbid")

    base_version_id: int
    #: A complete profile document, for the manual editor. Loosely typed here
    #: on purpose: it is validated against the strict `Profile` model by the
    #: service, and a 422 naming the field is more useful than FastAPI's own
    #: rendering of a deeply nested union.
    profile: dict[str, Any] | None = None
    analysis_id: int | None = None
    suggestion_id: int | None = None
    #: What the barista asked for, in their words. Goes into the prompt.
    notes: str = Field(default="", max_length=4000)
    change_summary: str = Field(default="", max_length=1000)
    #: Model override, as everywhere else. Empty takes `modelDraft`.
    model: str = Field(default="", max_length=200)


class DraftRefine(BaseModel):
    """A new draft from an existing one, with something more to go on."""

    model_config = ConfigDict(extra="forbid")

    notes: str = Field(default="", max_length=4000)
    model: str = Field(default="", max_length=200)


class DraftApprove(BaseModel):
    """The approval, and the acknowledgement it may require."""

    model_config = ConfigDict(extra="forbid")

    #: Required — and refused without — when the draft moved a stop condition.
    #: Named for what it acknowledges rather than `confirm`, so a client that
    #: sets every boolean to true has still said something specific.
    acknowledge_stop_changes: bool = False


class DraftPush(BaseModel):
    """Where the push should land, beyond the machine."""

    model_config = ConfigDict(extra="forbid")

    #: When given, a new Set version is created pointing at the pushed profile.
    #: Opt-in: a person may push a draft to try it without saying that this is
    #: now what the Set means.
    set_id: int | None = None
    #: Push even though the profile this was drafted from has been edited on the
    #: display since. Refused without it, because the diff that was approved is
    #: then a diff against something the machine no longer holds.
    allow_stale_base: bool = False


class DraftPreviewRequest(BaseModel):
    """A document the editor has not saved, for live validation."""

    model_config = ConfigDict(extra="forbid")

    base_version_id: int
    profile: dict[str, Any]


class PushedData(BaseModel):
    """What a push produced: the draft, and the Set version if one was asked for."""

    model_config = ConfigDict(extra="forbid")

    draft: ProfileDraftRow
    set_version: SetVersionRow | None = None


@router.get(
    "",
    response_model=ApiResponse[DraftListData],
    summary="The draft queue",
)
async def list_drafts(
    drafts: DraftServiceDep,
    status: Annotated[
        Literal["draft", "approved", "pushed", "failed", "discarded", "superseded"] | None,
        Query(),
    ] = None,
    open_only: Annotated[bool, Query(alias="open")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> JSONResponse:
    """Newest first. `open=true` is the queue: drafted, approved, or failed.

    `failed` counts as open on purpose. A push that did not verify left a
    profile on the machine that somebody has to decide about, and filing it
    under "done" is how it stays there.
    """
    items = await drafts.drafts.list_drafts(status=status, open_only=open_only, limit=limit)
    return envelope_response(DraftListData(items=items).model_dump(mode="json"))


@router.post(
    "",
    response_model=ApiResponse[ProfileDraftRow],
    status_code=201,
    summary="Draft a profile, from advice or by hand",
)
async def create_draft(body: DraftCreate, drafts: DraftServiceDep) -> JSONResponse:
    if body.profile is not None:
        row = await drafts.create_manual(
            base_version_id=body.base_version_id,
            document=body.profile,
            change_summary=body.change_summary,
            notes=body.notes,
        )
        return envelope_response(row.model_dump(mode="json"), status_code=201)
    if body.analysis_id is None and body.suggestion_id is None and not body.notes.strip():
        raise BadRequest(
            "A draft needs something to go on: an analysis, a suggestion, notes of your own, "
            "or a complete profile document to validate.",
            details={"field": "analysis_id", "message": "one of these is required"},
        )
    row = await drafts.generate(
        base_version_id=body.base_version_id,
        analysis_id=body.analysis_id,
        suggestion_id=body.suggestion_id,
        notes=body.notes,
        model=body.model,
    )
    return envelope_response(row.model_dump(mode="json"), status_code=201)


@router.post(
    "/preview",
    response_model=ApiResponse[DraftPreview],
    summary="Validate a profile document without storing it",
)
async def preview_draft(body: DraftPreviewRequest, drafts: DraftServiceDep) -> JSONResponse:
    """Live validation for the JSON editor: schema, policy and what would clamp.

    Answers 200 whatever it finds — "this is not valid yet" is the normal state
    of a document somebody is halfway through typing, not an error.
    """
    preview = await drafts.preview(body.base_version_id, body.profile)
    return envelope_response(preview.model_dump(mode="json"))


@router.get(
    "/{draft_id}",
    response_model=ApiResponse[ProfileDraftDetail],
    summary="One draft with both documents",
)
async def get_draft(draft_id: int, drafts: DraftServiceDep) -> JSONResponse:
    detail = await drafts.detail(draft_id)
    return envelope_response(detail.model_dump(mode="json"))


@router.post(
    "/{draft_id}/refine",
    response_model=ApiResponse[ProfileDraftRow],
    status_code=201,
    summary="Draft again, with more to go on; the old draft is superseded",
)
async def refine_draft(draft_id: int, body: DraftRefine, drafts: DraftServiceDep) -> JSONResponse:
    row = await drafts.refine(draft_id, notes=body.notes, model=body.model)
    return envelope_response(row.model_dump(mode="json"), status_code=201)


@router.post(
    "/{draft_id}/approve",
    response_model=ApiResponse[ProfileDraftRow],
    summary="Mark a draft ready to push",
)
async def approve_draft(draft_id: int, body: DraftApprove, drafts: DraftServiceDep) -> JSONResponse:
    """Refused with a 409 when the draft moves a stop condition and nobody said so.

    The refusal carries the list of changes in `details`, so a client that sent
    the approval without the acknowledgement can show exactly what it is asking
    the person to confirm.
    """
    row = await drafts.approve(draft_id, acknowledge_stop_changes=body.acknowledge_stop_changes)
    return envelope_response(row.model_dump(mode="json"))


@router.post(
    "/{draft_id}/push",
    response_model=ApiResponse[PushedData],
    summary="Save the draft to the machine as a new profile, and read it back",
)
async def push_draft(draft_id: int, body: DraftPush, drafts: DraftServiceDep) -> JSONResponse:
    """A 200 does **not** mean the push verified — read `draft.status`.

    `pushed` means the machine served back what we sent. `failed` means it did
    not, and the draft then carries both documents and a device id the rollback
    route can delete. Both are outcomes of a completed request; only a refusal
    (writes disabled, no machine, the draft not approved, a stale base) is an
    error status.
    """
    row, version = await drafts.push(
        draft_id, set_id=body.set_id, allow_stale_base=body.allow_stale_base
    )
    return envelope_response(PushedData(draft=row, set_version=version).model_dump(mode="json"))


@router.post(
    "/{draft_id}/rollback",
    response_model=ApiResponse[ProfileDraftRow],
    summary="Delete the machine's copy of a push that did not verify",
)
async def rollback_draft(draft_id: int, drafts: DraftServiceDep) -> JSONResponse:
    row = await drafts.rollback(draft_id)
    return envelope_response(row.model_dump(mode="json"))


@router.post(
    "/{draft_id}/discard",
    response_model=ApiResponse[ProfileDraftRow],
    summary="Turn a draft down",
)
async def discard_draft(draft_id: int, drafts: DraftServiceDep) -> JSONResponse:
    row = await drafts.discard(draft_id)
    return envelope_response(row.model_dump(mode="json"))
