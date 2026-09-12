"""`/api/machines` — which machines this archive has ever been pointed at.

Usually one row. It is a list rather than a singleton because a `shots.machine_id`
foreign key outlives any one configuration: point the container at a second
GaggiMate, or at a replacement display board on a new address, and the shots from
the first one must keep resolving to the machine that pulled them.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.deps import MachinesRepoDep, ShotsRepoDep
from gaggiclanker.db.repos.machines import MachineRow
from gaggiclanker.db.repos.shots import ShotCounts
from gaggiclanker.infra.envelope import ApiResponse, envelope_response

__all__ = ["router"]

router = APIRouter(prefix="/machines", tags=["machines"])


class MachineWithCounts(BaseModel):
    """A machine and how much of the archive came from it."""

    model_config = ConfigDict(extra="forbid")

    machine: MachineRow
    counts: ShotCounts


class MachineListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MachineWithCounts]


@router.get(
    "",
    response_model=ApiResponse[MachineListData],
    summary="The machines this archive knows about",
)
async def list_machines(machines: MachinesRepoDep, shots: ShotsRepoDep) -> JSONResponse:
    rows = await machines.list_all()
    items = [MachineWithCounts(machine=row, counts=await shots.counts(row.id)) for row in rows]
    return envelope_response(MachineListData(items=items).model_dump(mode="json"))
