"""`/api/machine` — the one machine this archive belongs to.

A singleton, not a list. The archive holds one machine and the row describes
whatever host is configured now: pointing the container at a new address moves
the machine rather than forking the archive, so there is nothing here to choose
between and nothing that takes an id.

The row always exists, empty host and all, so the Hardware page has something to
render before the first pull instead of an absence to model.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.api.deps import MachineRepoDep, ShotsRepoDep
from gaggiclanker.db.repos.machines import MachineRow
from gaggiclanker.db.repos.shots import ShotCounts
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import NotFound

__all__ = ["router"]

router = APIRouter(prefix="/machine", tags=["machine"])


class MachineData(BaseModel):
    """The machine, and how much of the archive came off it."""

    model_config = ConfigDict(extra="forbid")

    machine: MachineRow
    counts: ShotCounts


class MachinePatch(BaseModel):
    """The two fields a person owns on the machine row.

    Everything else — the hardware string, the firmware versions, the capability
    flags, the device's own settings document — is the machine's account of
    itself and is rewritten by the next sync pass. Accepting an edit to one of
    those would be accepting an edit that silently reverts, so the body forbids
    extras rather than ignoring them.

    The host is not here either: it is a setting (`gaggimateHost`), and the sync
    engine writes it onto this row when it connects.

    ``None`` means "leave it alone", so a rename does not have to resend the
    notes.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


@router.get("", response_model=ApiResponse[MachineData], summary="The machine")
async def get_machine(machines: MachineRepoDep, shots: ShotsRepoDep) -> JSONResponse:
    row = await machines.get()
    # Unreachable through the app: 0016 guarantees the row and `CHECK (id = 1)`
    # refuses a second. Answered anyway rather than asserted, because a database
    # somebody has been editing by hand is a thing that happens, and saying the
    # row is missing beats a 500 from an attribute on `None`. A comment rather
    # than a docstring: a route's docstring is its OpenAPI description, and this
    # is about the inside.
    if row is None:
        raise NotFound("The machine row is missing from this archive")
    data = MachineData(machine=row, counts=await shots.counts())
    return envelope_response(data.model_dump(mode="json"))


@router.patch(
    "",
    response_model=ApiResponse[MachineRow],
    summary="Rename the machine or annotate it",
)
async def patch_machine(body: MachinePatch, machines: MachineRepoDep) -> JSONResponse:
    """Name and notes only. Identity comes from the device and stays there."""
    row = await machines.update_editable(name=body.name, notes=body.notes)
    if row is None:
        raise NotFound("The machine row is missing from this archive")
    return envelope_response(row.model_dump(mode="json"))
