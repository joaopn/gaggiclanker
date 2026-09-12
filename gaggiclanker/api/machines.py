"""`/api/machines` — which machines this archive has ever been pointed at.

Usually one row. It is a list rather than a singleton because a `shots.machine_id`
foreign key outlives any one configuration: point the container at a second
GaggiMate, or at a replacement display board on a new address, and the shots from
the first one must keep resolving to the machine that pulled them.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.api.deps import MachinesRepoDep, ShotsRepoDep
from gaggiclanker.db.repos.machines import MachineRow
from gaggiclanker.db.repos.shots import ShotCounts
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import NotFound

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


class MachinePatch(BaseModel):
    """The two fields a person owns on a machine row.

    Everything else — the hardware string, the firmware versions, the capability
    flags, the device's own settings document — is the machine's account of
    itself and is rewritten by the next sync pass. Accepting an edit to one of
    those would be accepting an edit that silently reverts, so the body forbids
    extras rather than ignoring them.

    ``None`` means "leave it alone", so a rename does not have to resend the
    notes.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


@router.get(
    "",
    response_model=ApiResponse[MachineListData],
    summary="The machines this archive knows about",
)
async def list_machines(machines: MachinesRepoDep, shots: ShotsRepoDep) -> JSONResponse:
    rows = await machines.list_all()
    items = [MachineWithCounts(machine=row, counts=await shots.counts(row.id)) for row in rows]
    return envelope_response(MachineListData(items=items).model_dump(mode="json"))


@router.patch(
    "/{machine_id}",
    response_model=ApiResponse[MachineRow],
    summary="Rename a machine or annotate it",
)
async def patch_machine(
    machine_id: int, body: MachinePatch, machines: MachinesRepoDep
) -> JSONResponse:
    """Name and notes only. Identity comes from the device and stays there."""
    row = await machines.update_editable(machine_id, name=body.name, notes=body.notes)
    if row is None:
        raise NotFound(f"No machine {machine_id}")
    return envelope_response(row.model_dump(mode="json"))
