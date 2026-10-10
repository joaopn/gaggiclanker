"""Pin a Set version to the machine, preview the pin, unpin it.

Person-only: these routes are the only way in. The pin service is built over the board service,
which the chat tools and the MCP server are never handed, and nothing here writes to the machine
(the next sync with Writes on does, through the ordinary profile-list machinery).
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.api.deps import PinServiceDep
from gaggiclanker.drafts.pins import PinAnswer, PinPreview
from gaggiclanker.infra.envelope import ApiResponse, envelope_response

__all__ = ["router"]

router = APIRouter(tags=["pins"])

#: The longest name a pin may be given; a machine profile's label is short, and the shot log keeps
#: only 47 bytes of it.
LABEL_MAX = 200


class PinBody(BaseModel):
    """`POST .../pin`: the name for the copy, when the person does not want the default."""

    model_config = ConfigDict(extra="forbid")

    #: Stripped. Left out, the copy is called "<the version's profile> <the version's name>".
    label: str | None = Field(default=None, max_length=LABEL_MAX)


@router.get(
    "/sets/{set_id}/versions/{version_id}/pin-preview",
    response_model=ApiResponse[PinPreview],
    summary="What pinning this Set version would do, without doing it",
)
async def preview_pin(
    set_id: int,
    version_id: int,
    pins: PinServiceDep,
    label: str | None = Query(default=None, max_length=LABEL_MAX),
) -> JSONResponse:
    """The pin's own questions, asked and not acted on: the name the copy would have, whether it
    is taken, and the sentence a refusal would carry (any refusal, not only a taken name)."""
    return envelope_response(
        (await pins.preview(set_id, version_id, label)).model_dump(mode="json")
    )


@router.post(
    "/sets/{set_id}/versions/{version_id}/pin",
    response_model=ApiResponse[PinAnswer],
    summary="Keep this Set version on the machine as a profile of its own",
)
async def pin_version(
    set_id: int, version_id: int, pins: PinServiceDep, body: PinBody | None = None
) -> JSONResponse:
    """Copy the version's profile under a new name onto the profile list, switched on and starred,
    and record the pin, in one transaction. Nothing is sent to the machine: the next sync with
    Writes on puts it there. Refused (409, one code each, nothing written): `SET_ARCHIVED`,
    `DESIGNING`, `VERSION_HAS_NO_PROFILE`, `ALREADY_PINNED`, `PIN_NAME_TAKEN`,
    `PIN_POLICY_REFUSED`; 404 for a Set or version that is not there; 422 for a blank label."""
    answer = await pins.pin(set_id, version_id, None if body is None else body.label)
    return envelope_response(answer.model_dump(mode="json"))


@router.post(
    "/pins/{pin_id}/unpin",
    response_model=ApiResponse[PinAnswer],
    summary="Take a pinned copy off the machine at the next sync",
)
async def unpin(pin_id: int, pins: PinServiceDep) -> JSONResponse:
    """Stamp the pin and tombstone its profile-list row in one transaction; the next sync removes
    the file through the fresh-load check. The shots already filed stay where they are. 404 for an
    unknown pin, 409 `NOT_PINNED` for one already unpinned."""
    return envelope_response((await pins.unpin(pin_id)).model_dump(mode="json"))
