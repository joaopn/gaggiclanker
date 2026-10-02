"""`/api/profile-board` — the profiles the app means the machine to hold.

The board is edited here and written to the machine by the next sync, never by these routes:
nothing below sends a byte to the machine, so every route works with the writes switch off
and with no machine at all. What a route that reads the machine does is read.

* ``GET`` serves the board with each row's state on the machine and the actions the next sync
  would take, from a read made now (or, when the machine cannot be read, the archive's last
  mirror of it, and the response says so).
* ``POST`` puts an approved draft on the board: a new version of the profile it descends from,
  or a new profile.
* ``PUT .../on-machine`` switches a profile on or off the machine and ``PUT .../starred`` stars
  it (the machine's home-screen carousel); ``PUT .../active-version`` makes one of its versions
  the active one (any version: there is no separate going back); ``GET .../versions`` lists its
  versions and the proposals that would join them; ``GET`` and ``POST .../conflict`` show and
  settle a profile whose file was edited outside the app. The next sync does what they say.

The list has no Delete and no Take: switching a profile off is enough, and a profile the machine
holds that the list has never seen joins it at the next sync.

Chat and MCP have no route here and no tool: a person's click is the only way a profile gets
onto the board, and the board is the only thing a sync pushes.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from gaggiclanker.api.deps import BoardServiceDep, DeviceClientDep, ProfilesRepoDep, SetsRepoDep
from gaggiclanker.api.sets import version_refused
from gaggiclanker.db.repos.profile_board import BoardRow
from gaggiclanker.db.repos.sets import VersionRefused
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.drafts.board import BoardView, machine_from_mirror
from gaggiclanker.drafts.board_versions import ConflictView, ProfileVersionsView
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
    #: Strict: "yes" is not an answer to a question that decides a version's name.
    major: StrictBool | None = None
    #: Required, and refused without, when the draft moves a stop condition: the person says
    #: they know it changes how much coffee ends up in the cup. Named for what it acknowledges
    #: so a client that sets every boolean to true has still said something specific.
    acknowledge_stop_changes: bool = False


class ResumeData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resumed: bool


class OnMachineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: StrictBool


class StarredBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starred: StrictBool


class ActiveVersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: StrictInt


class ConflictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep: Literal["app", "machine"]
    #: The content hash of the machine's file as the person saw it; a file that changed since is
    #: refused.
    content_hash: str = Field(min_length=1, max_length=128)


@router.post(
    "/resume",
    response_model=ApiResponse[ResumeData],
    summary="Let syncs write again after the machine looked reset",
)
async def resume_board(board: BoardServiceDep) -> JSONResponse:
    """Person-only. Clears the pause; the next sync then does what the page showed.

    Sends nothing to the machine. There is no chat or MCP tool for it.
    """
    await board.resume()
    return envelope_response(ResumeData(resumed=True).model_dump(mode="json"))


@router.get(
    "",
    response_model=ApiResponse[BoardView],
    summary="The profile list, its state on the machine and what the next sync would do",
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
    summary="Make a proposal active: a new version of a profile, or a new profile",
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
    "/{row_id}/starred",
    response_model=ApiResponse[BoardRow],
    summary="Star a profile (the machine's home-screen carousel), or take its star off",
)
async def put_starred(row_id: int, body: StarredBody, board: BoardServiceDep) -> JSONResponse:
    """Person-only. Stored now and applied by a sync only while the profile is on the machine;
    remembered while it is off. Nothing is sent to the machine here."""
    row = await board.set_home_screen(row_id, body.starred)
    return envelope_response(row.model_dump(mode="json"))


@router.put(
    "/{row_id}/on-machine",
    response_model=ApiResponse[BoardRow],
    summary="Switch a profile on or off the machine",
)
async def put_on_machine(row_id: int, body: OnMachineBody, board: BoardServiceDep) -> JSONResponse:
    """Person-only. The next sync puts the profile on the machine or takes it off; with the
    Writes switch off it is only stored. Nothing is sent to the machine here. No chat or MCP
    tool reaches it."""
    row = await board.set_on_machine(row_id, body.on)
    return envelope_response(row.model_dump(mode="json"))


@router.put(
    "/{row_id}/active-version",
    response_model=ApiResponse[BoardRow],
    summary="Make one of a profile's versions its active one",
)
async def put_active_version(
    row_id: int, body: ActiveVersionBody, board: BoardServiceDep
) -> JSONResponse:
    """Person-only. Any version the profile has had; the next sync puts it on the machine and
    takes the profile's other file off. Records nothing on a Set (only making a proposal active
    through ``POST /api/profile-board`` does). Refused (409) for a version of another profile,
    the empty baseline, a version outside the safety bounds, and a name another profile has."""
    row = await board.set_active_version(row_id, body.version_id)
    return envelope_response(row.model_dump(mode="json"))


@router.get(
    "/{row_id}/versions",
    response_model=ApiResponse[ProfileVersionsView],
    summary="A profile's versions, newest first, and the proposals that would join them",
)
async def get_versions(row_id: int, board: BoardServiceDep) -> JSONResponse:
    """Read-only, from the archive. Each version names the one before it in the list (the
    first has none: it is new, never a diff against anything else)."""
    view = await board.versions(row_id)
    return envelope_response(view.model_dump(mode="json"))


@router.get(
    "/{row_id}/conflict",
    response_model=ApiResponse[ConflictView | None],
    summary="Both sides of a profile's conflict, or null when it has none",
)
async def get_conflict(row_id: int, board: BoardServiceDep) -> JSONResponse:
    """Read-only, from the archive's mirror: the machine's file as last read and the profile's
    active version, for a side-by-side panel."""
    view = await board.conflict(row_id)
    return envelope_response(None if view is None else view.model_dump(mode="json"))


@router.post(
    "/{row_id}/conflict",
    response_model=ApiResponse[BoardRow],
    summary="Choose which side of a conflict to keep",
)
async def resolve_conflict(row_id: int, body: ConflictBody, board: BoardServiceDep) -> JSONResponse:
    """Person-only. ``machine``: the machine's version becomes the active one. ``app``: the next
    sync replaces the machine's file with the active version. Nothing is sent to the machine
    here. Refused (409) with no conflict, or when the file changed since ``content_hash``."""
    row = await board.resolve_conflict(row_id, keep=body.keep, content_hash=body.content_hash)
    return envelope_response(row.model_dump(mode="json"))
