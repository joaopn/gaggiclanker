"""Duplicate a Set from one of its versions, and preview what the duplicate would start from;
and duplicate a profile from one of its versions (Duplicate Profile).

Person-only: these routes are the only way in. The duplicate service is built over the board
service, which the chat tools and the MCP server are never handed, and nothing here writes to the
machine (the next sync with Writes on does, through the ordinary profile-list machinery).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from gaggiclanker.api.deps import DuplicateServiceDep
from gaggiclanker.db.repos.profile_board import BoardRow
from gaggiclanker.drafts.duplicates import (
    DuplicateAnswer,
    DuplicatePreview,
    DuplicateRequest,
    ProfileDuplicateRequest,
)
from gaggiclanker.infra.envelope import ApiResponse, envelope_response

__all__ = ["router"]

router = APIRouter(tags=["sets"])


@router.get(
    "/sets/{set_id}/versions/{version_id}/duplicate-preview",
    response_model=ApiResponse[DuplicatePreview],
    summary="What duplicating this Set version would start from, without doing it",
)
async def preview_duplicate(
    set_id: int, version_id: int, duplicates: DuplicateServiceDep
) -> JSONResponse:
    """The suggested Set name, the bean and grinder, the recipe as a line, whether the version has
    a profile, and the source Set's own confirmed insights. 404 for a Set or version that is not
    there, 409 `DESIGNING` for a Set still being designed."""
    return envelope_response((await duplicates.preview(set_id, version_id)).model_dump(mode="json"))


@router.post(
    "/sets/{set_id}/versions/{version_id}/duplicate",
    response_model=ApiResponse[DuplicateAnswer],
    status_code=201,
    summary="Start a new Set from this version's recipe, on a new profile, with no history",
)
async def duplicate_version(
    set_id: int, version_id: int, body: DuplicateRequest, duplicates: DuplicateServiceDep
) -> JSONResponse:
    """The new profile (the version's profile under `profile_name`, put on the list switched on and
    starred), the new Set with its first version and the chosen insights, in one transaction.
    Refused, nothing written: 404; 422 for an empty Set name, an empty profile name when the
    version has a profile, a profile name when it has none, an insight that is not one of the
    preview's, or a bean or grinder that is not there; 409 `DESIGNING`, `PROFILE_NAME_TAKEN` and
    `PROFILE_POLICY_REFUSED`."""
    answer = await duplicates.duplicate(set_id, version_id, body)
    return envelope_response(answer.model_dump(mode="json"), status_code=201)


@router.post(
    "/profile-versions/{version_id}/duplicate",
    response_model=ApiResponse[BoardRow],
    status_code=201,
    tags=["profiles"],
    summary="Make a new profile from this version, under a new name, on the profile list",
)
async def duplicate_profile(
    version_id: int, body: ProfileDuplicateRequest, duplicates: DuplicateServiceDep
) -> JSONResponse:
    """The new profile: the version's document (or the edited one in `profile`) under `name`, put
    on the list switched on, in one transaction. Nothing is sent to the machine now. Refused,
    nothing written: 404; 422 for an empty name or a document that is not a profile; 409
    `PROFILE_IS_A_PIN`, `PROFILE_NAME_TAKEN` (the original's name included) and
    `PROFILE_POLICY_REFUSED`."""
    row = await duplicates.duplicate_profile(version_id, body)
    return envelope_response(row.model_dump(mode="json"), status_code=201)
