"""`/api/profile-drafts` — what a profile draft is, before a person makes it active.

Everything here is a thin wrapper over
:class:`~gaggiclanker.drafts.service.ProfileDraftService`, which is where the rules live.
**Nothing here reaches the machine, and nothing here approves a draft**: a draft becomes
approved, and goes onto the profile board, in one action on ``/api/profile-board`` (which
carries the stop-condition acknowledgement), and the board's write phase puts it on the
machine at the next sync. This router drafts, refines, validates, reads and discards.

Every route runs its work inside the request. A draft call is one LLM turn — ten seconds, not
a review's minute or two — so there is nothing here worth a second place an outcome could get
lost.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.api.deps import DraftServiceDep
from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow
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
    contacted. `profile` absent means "draft one from the notes", which needs
    notes — a draft with nothing to go on is a model rewriting somebody's
    profile for no stated reason.
    """

    model_config = ConfigDict(extra="forbid")

    base_version_id: int
    #: A complete profile document, for the manual editor. Loosely typed here
    #: on purpose: it is validated against the strict `Profile` model by the
    #: service, and a 422 naming the field is more useful than FastAPI's own
    #: rendering of a deeply nested union.
    profile: dict[str, Any] | None = None
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


class DraftPreviewRequest(BaseModel):
    """A document the editor has not saved, for live validation."""

    model_config = ConfigDict(extra="forbid")

    base_version_id: int
    profile: dict[str, Any]


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
    if not body.notes.strip():
        raise BadRequest(
            "A draft needs something to go on: notes of your own, "
            "or a complete profile document to validate.",
            details={"field": "notes", "message": "one of these is required"},
        )
    row = await drafts.generate(
        base_version_id=body.base_version_id,
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
    "/{draft_id}/discard",
    response_model=ApiResponse[ProfileDraftRow],
    summary="Turn a draft down",
)
async def discard_draft(draft_id: int, drafts: DraftServiceDep) -> JSONResponse:
    row = await drafts.discard(draft_id)
    return envelope_response(row.model_dump(mode="json"))
