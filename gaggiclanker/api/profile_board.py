"""`/api/profile-board` — the profiles the app means the machine to hold.

The board is edited here and written to the machine by the next sync, never by these routes:
nothing below sends a byte to the machine, so every route works with the writes switch off
and with no machine at all. What a route that reads the machine does is read.

* ``GET`` serves the board with each row's state on the machine and the actions the next sync
  would take, from a read made now (or, when the machine cannot be read, the archive's last
  mirror of it, and the response says so).
* ``POST`` puts an approved draft on the board: a new version of the profile it descends from,
  or a new profile.
* ``POST .../go-back`` makes a profile its previous version again (the app's own profiles
  only); the next sync puts that version on the machine and removes the newer copy.
* ``PUT .../home-screen`` and ``DELETE`` change one row.

Chat and MCP have no route here and no tool: a person's click is the only way a profile gets
onto the board, and the board is the only thing a sync pushes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from gaggiclanker.api.deps import BoardServiceDep, DeviceClientDep, ProfilesRepoDep, SetsRepoDep
from gaggiclanker.api.sets import version_refused
from gaggiclanker.db.repos.profile_board import BoardRow
from gaggiclanker.db.repos.sets import VersionRefused
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.drafts.board import BoardView, machine_from_mirror
from gaggiclanker.drafts.machine import MachineState, read_machine
from gaggiclanker.infra.envelope import ApiResponse, envelope_response

__all__ = ["router"]

router = APIRouter(prefix="/profile-board", tags=["profiles"])


class BoardPut(BaseModel):
    """Put an approved draft on the board."""

    model_config = ConfigDict(extra="forbid")

    draft_id: int
    #: Record the profile as this Set's next version when a sync puts it on the machine.
    set_id: int | None = None
    #: Whether that version is a major one; ``None`` leaves the default for a pushed draft.
    major: bool | None = None
    #: Required, and refused without, when the draft moves a stop condition: the person says
    #: they know it changes how much coffee ends up in the cup. Named for what it acknowledges
    #: so a client that sets every boolean to true has still said something specific.
    acknowledge_stop_changes: bool = False


class ResumeData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resumed: bool


class HomeScreenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: StrictBool


class TakeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_profile_id: str = Field(min_length=1, max_length=64)


@router.post(
    "/take",
    response_model=ApiResponse[BoardRow],
    status_code=201,
    summary="Take a profile the machine holds onto the board, as it is",
)
async def take_onto_board(body: TakeBody, board: BoardServiceDep) -> JSONResponse:
    """Person-only; the first adoption's rule, for one profile. Sends nothing to the machine.

    Refused (409) for a profile already on the board and before the board has been adopted,
    404 for one the last mirror does not show. No chat or MCP tool reaches it.
    """
    row = await board.take(body.device_profile_id)
    return envelope_response(row.model_dump(mode="json"), status_code=201)


@router.post(
    "/resume",
    response_model=ApiResponse[ResumeData],
    summary="Let syncs write again after the machine looked reset",
)
async def resume_board(board: BoardServiceDep) -> JSONResponse:
    """Person-only. Clears the pause; the next sync then pushes the board's app profiles.

    Sends nothing to the machine. There is no chat or MCP tool for it.
    """
    await board.resume()
    return envelope_response(ResumeData(resumed=True).model_dump(mode="json"))


@router.get(
    "",
    response_model=ApiResponse[BoardView],
    summary="The profile board, its state on the machine and what the next sync would do",
)
async def get_board(
    board: BoardServiceDep,
    client: DeviceClientDep,
    profiles: ProfilesRepoDep,
    live: Annotated[bool, Query(description="Read the machine now instead of the mirror")] = False,
) -> JSONResponse:
    """Read-only. The plan is the one a sync would execute.

    By default it is built from the archive's last mirror of the machine, which costs no
    request to it and is what a page that polls should use. ``?live=true`` reads the machine
    now (a list and one load per profile) and is for a preview someone is about to act on;
    ``machine_source`` says which one answered.
    """
    machine: MachineState | None = None
    source = "none"
    if live and client is not None and client.connected:
        try:
            machine = await read_machine(client)
            source = "machine"
        except DeviceError:
            machine = None
    if machine is None:
        machine = await machine_from_mirror(profiles)
        source = "mirror" if machine.profiles else "none"
    host = client.host if client is not None else ""
    view = await board.view(machine, source=source, host=host)
    return envelope_response(view.model_dump(mode="json"))


@router.post(
    "",
    response_model=ApiResponse[BoardRow],
    status_code=201,
    summary="Put an approved draft on the board",
)
async def put_on_board(body: BoardPut, board: BoardServiceDep, sets: SetsRepoDep) -> JSONResponse:
    """The next sync puts it on the machine. Nothing is sent to the machine now.

    One action for a proposal: a drafted draft is approved by it (with the stop-condition
    acknowledgement when its stop conditions moved). Refused (409) for a draft that is already
    on the machine, discarded or overtaken, one already on the board, a stop-condition change
    nobody acknowledged, a label the board already has, and for a Set that could no longer be
    given a version.
    """
    if body.set_id is not None:
        refusal = await sets.design_refusal(body.set_id)
        if refusal is not None:
            raise version_refused(VersionRefused(refusal))
    row = await board.put_draft(
        body.draft_id,
        set_id=body.set_id,
        major=body.major,
        acknowledge_stop_changes=body.acknowledge_stop_changes,
    )
    return envelope_response(row.model_dump(mode="json"), status_code=201)


@router.put(
    "/{row_id}/home-screen",
    response_model=ApiResponse[BoardRow],
    summary="Put a profile on, or take it off, the machine's home screen",
)
async def put_home_screen(
    row_id: int, body: HomeScreenBody, board: BoardServiceDep
) -> JSONResponse:
    row = await board.set_home_screen(row_id, body.on)
    return envelope_response(row.model_dump(mode="json"))


@router.post(
    "/{row_id}/go-back",
    response_model=ApiResponse[BoardRow],
    summary="Go back to a profile's previous version",
)
async def go_back_on_board(row_id: int, board: BoardServiceDep) -> JSONResponse:
    """Person-only. Sends nothing to the machine: the next sync does.

    Refused (409) for a profile of the person's, one with no earlier version, and when the
    earlier version would make two profiles share a label. No chat or MCP tool reaches it.
    """
    row = await board.go_back(row_id)
    return envelope_response(row.model_dump(mode="json"))


@router.delete(
    "/{row_id}",
    response_model=ApiResponse[BoardRow],
    summary="Delete a profile from the board",
)
async def delete_from_board(row_id: int, board: BoardServiceDep) -> JSONResponse:
    """A tombstone. The next sync removes the machine's copy only when the app wrote it."""
    row = await board.delete_row(row_id)
    return envelope_response(row.model_dump(mode="json"))
