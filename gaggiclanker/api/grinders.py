"""`/api/grinders` — the other half of the hardware page.

Create, list and edit. No delete and no archive: a grinder is not consumed, and
one that leaves the kitchen is still the grinder that ground a year of shots.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.deps import GrindersRepoDep
from gaggiclanker.db.repos.grinders import GrinderRow, GrinderWrite
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import NotFound

__all__ = ["router"]

router = APIRouter(prefix="/grinders", tags=["grinders"])


class GrinderListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[GrinderRow]


@router.get("", response_model=ApiResponse[GrinderListData], summary="The grinders")
async def list_grinders(grinders: GrindersRepoDep) -> JSONResponse:
    rows = await grinders.list_all()
    return envelope_response(GrinderListData(items=rows).model_dump(mode="json"))


@router.post(
    "", response_model=ApiResponse[GrinderRow], status_code=201, summary="Record a grinder"
)
async def create_grinder(body: GrinderWrite, grinders: GrindersRepoDep) -> JSONResponse:
    row = await grinders.create(body)
    return envelope_response(row.model_dump(mode="json"), status_code=201)


@router.get("/{grinder_id}", response_model=ApiResponse[GrinderRow], summary="One grinder")
async def get_grinder(grinder_id: int, grinders: GrindersRepoDep) -> JSONResponse:
    row = await grinders.get(grinder_id)
    if row is None:
        raise NotFound(f"No grinder {grinder_id}")
    return envelope_response(row.model_dump(mode="json"))


@router.put("/{grinder_id}", response_model=ApiResponse[GrinderRow], summary="Edit a grinder")
async def update_grinder(
    grinder_id: int, body: GrinderWrite, grinders: GrindersRepoDep
) -> JSONResponse:
    row = await grinders.update(grinder_id, body)
    if row is None:
        raise NotFound(f"No grinder {grinder_id}")
    return envelope_response(row.model_dump(mode="json"))
