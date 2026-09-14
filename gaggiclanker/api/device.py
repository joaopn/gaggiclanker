"""`/api/device` — what the machine is, and what this box has asked it to do.

One poll for the facts that change rarely: configured, connected, identity, the
last status frame the socket happened to carry. There is no telemetry stream
here any more. The machine's own web UI draws the shot that is happening now,
over the same socket, and a second copy of it was a worse copy that cost a
subscription per open tab and a re-render twice a second — so this application
answers the question it is better placed to answer, which is what the archive
holds.

The web UI builds the real device page on top of these. Profile push adds a third: the
audit of every write this box has ever asked the machine to make, which is the
one page that can answer "what has this thing done to my machine". Storage cleanup adds
the storage-cleanup trio (plan, run, history) and the notes push, and
both follow the same shape as the analyzer's routes: the **plan** is computed in
the request because it is a database read, and the **run** is 202 plus a
background task, because it is a sequence of WebSocket frames paced at two a
second and `docker stop` allows ten seconds in total.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.deps import (
    CleanupServiceDep,
    DeviceWritesRepoDep,
    NotesWritebackServiceDep,
    SettingsServiceDep,
)
from gaggiclanker.cleanup.service import CleanupPlan, CleanupService, cleanup_task_name
from gaggiclanker.db.repos.cleanup import CleanupRepository, CleanupRunRow
from gaggiclanker.db.repos.device_writes import DeviceWriteRow
from gaggiclanker.db.repos.judgements import PendingWritebackRow
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import Conflict, ServiceUnavailable
from gaggiclanker.notes.writeback import NotesWritebackService, writeback_task_name

__all__ = ["router"]

router = APIRouter(prefix="/device", tags=["device"])


class DeviceStatusData(BaseModel):
    """What `GET /api/device/status` answers.

    ``configured`` and ``connected`` are separate facts and the UI needs both:
    "no machine configured" is a setup step, "configured but not connected" is a
    problem to go and look at.
    """

    configured: bool
    connected: bool
    host: str | None = None
    identity: dict[str, Any] | None = None
    last_status: dict[str, Any] | None = None


def get_device(request: Request) -> GaggimateClient | None:
    """The app's device client, or ``None`` when no host is configured."""
    client: GaggimateClient | None = getattr(request.app.state, "device", None)
    return client


@router.get(
    "/status",
    response_model=ApiResponse[DeviceStatusData],
    summary="Connection state, identity and the last known live status",
)
async def get_device_status(request: Request) -> JSONResponse:
    client = get_device(request)
    if client is None:
        return envelope_response(
            DeviceStatusData(configured=False, connected=False).model_dump(mode="json")
        )
    return envelope_response(
        DeviceStatusData(
            configured=True,
            connected=client.connected,
            host=client.host,
            identity=client.identity.model_dump(by_alias=True) if client.identity else None,
            last_status=(
                client.last_status.model_dump(by_alias=True) if client.last_status else None
            ),
        ).model_dump(mode="json")
    )


class DeviceWritesData(BaseModel):
    """The write audit, plus whether the switch that allows them is on.

    Both in one response because they are read together: a list of refusals
    means one thing when writes are off and something quite different when they
    are on, and a page that had to make two requests to say which would render
    the wrong sentence for a moment every time.
    """

    enabled: bool
    items: list[DeviceWriteRow]


@router.get(
    "/writes",
    response_model=ApiResponse[DeviceWritesData],
    summary="Every write this box has asked the machine to make",
)
async def list_device_writes(
    writes: DeviceWritesRepoDep,
    settings: SettingsServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> JSONResponse:
    """Newest first, refusals included.

    A refused write never reached the wire and is the most useful row here: it
    is what "something tried to write while this was switched off" looks like.
    """
    items = await writes.list_writes(limit=limit)
    enabled = bool(await settings.get("deviceWritesEnabled"))
    return envelope_response(DeviceWritesData(enabled=enabled, items=items).model_dump(mode="json"))


# ── storage cleanup ──────────────────────────────────────────────────


def _require_cleanup(service: CleanupService | None) -> CleanupService:
    if service is None:
        raise ServiceUnavailable(
            "No machine is configured, so there is nothing to clean up. "
            "Set `gaggimateHost` (and leave `deviceSyncEnabled` on) in settings."
        )
    return service


class CleanupRunRequest(BaseModel):
    """The plan a person confirmed, by the shot ids the preview showed them."""

    model_config = ConfigDict(extra="forbid")

    #: Every planned shot id from the preview that was confirmed. Required, and
    #: compared with a fresh plan: a run deletes what was approved or nothing.
    shot_ids: list[int]


class CleanupRunAccepted(BaseModel):
    """What was queued. Nothing has been deleted when this is sent."""

    model_config = ConfigDict(extra="forbid")

    planned: int
    task: str


class CleanupRunsData(BaseModel):
    """The ledger of past runs, newest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[CleanupRunRow]


@router.get(
    "/cleanup/plan",
    response_model=ApiResponse[CleanupPlan],
    summary="What a cleanup would delete from the machine right now",
)
async def get_cleanup_plan(cleanup: CleanupServiceDep) -> JSONResponse:
    """A dry run. Reads the archive and the last identity frame; writes nothing.

    This is the preview a person approves, and it lists what it will **not**
    delete as well as what it will — a shot that is quarantined or whose stored
    bytes do not match its header is named with the reason, because "why is that
    shot still on my machine" is otherwise unanswerable from this page.
    """
    plan = await _require_cleanup(cleanup).plan()
    return envelope_response(plan.model_dump(mode="json"))


@router.post(
    "/cleanup/run",
    response_model=ApiResponse[CleanupRunAccepted],
    status_code=202,
    summary="Delete the approved plan's shots from the machine, oldest first",
)
async def post_cleanup_run(
    body: CleanupRunRequest, request: Request, cleanup: CleanupServiceDep
) -> JSONResponse:
    """202, and the work happens in a background task — but only for the plan shown.

    The body names the shots the preview showed and the person confirmed. A
    fresh plan that differs is a 409 and nothing is queued, so what runs is
    exactly what was approved; with writes off it is a 403, audited, naming the
    switch. This route is the only way a cleanup starts: nothing runs one
    automatically.

    Not in the request, for the same reason an analysis is not: a run is one
    WebSocket frame per shot paced at two a second, so a hundred shots is most
    of a minute and `docker stop` allows ten seconds. The task is named
    `cleanup`, claimed synchronously, so a second tab pressing the button gets a
    409 rather than a second pass fighting this one over the device's two HTTP
    slots.

    Every delete is still authorised by the write gate on its way out, which is
    what makes this route safe to expose at all: it cannot delete anything the
    archive does not already hold intact.
    """
    service = _require_cleanup(cleanup)
    tasks = request.app.state.tasks
    # Checked before the plan as well as by the claim below: a run in progress
    # is moving shots out of the plan, and "the plan changed" would be the wrong
    # sentence for "wait for the one already going".
    if tasks.get(cleanup_task_name()) is not None:
        raise _cleanup_running()
    plan = await service.approve(body.shot_ids)
    if not service.spawn(tasks, plan, trigger="manual"):
        raise _cleanup_running()
    return envelope_response(
        CleanupRunAccepted(planned=len(plan.planned), task=cleanup_task_name()).model_dump(
            mode="json"
        ),
        status_code=202,
    )


def _cleanup_running() -> Conflict:
    return Conflict(
        "A cleanup is already running. Wait for it to finish; its result appears "
        "under Clean up storage on the Sync page."
    )


@router.get(
    "/cleanup/runs",
    response_model=ApiResponse[CleanupRunsData],
    summary="Every cleanup pass this box has run",
)
async def list_cleanup_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> JSONResponse:
    """Newest first. A run that stopped early shows both figures: planned and deleted."""
    items = await CleanupRepository(request.app.state.db).list_runs(limit=limit)
    return envelope_response(CleanupRunsData(items=items).model_dump(mode="json"))


# ── notes write-back ─────────────────────────────────────────────────


def _require_writeback(service: NotesWritebackService | None) -> NotesWritebackService:
    if service is None:
        raise ServiceUnavailable("No machine is configured, so there is nowhere to write notes to.")
    return service


class PendingNotesData(BaseModel):
    """The verdicts this box holds that the machine does not, and what a send would write."""

    model_config = ConfigDict(extra="forbid")

    writes_enabled: bool
    fields: list[str]
    items: list[PendingWritebackRow]


class NotesPushRequest(BaseModel):
    """What a person chose to send. ``shot_ids`` omitted or null means every pending one."""

    model_config = ConfigDict(extra="forbid")

    shot_ids: list[int] | None = None


class NotesPushAccepted(BaseModel):
    """What was queued for the send."""

    model_config = ConfigDict(extra="forbid")

    pending: int


@router.get(
    "/notes/pending",
    response_model=ApiResponse[PendingNotesData],
    summary="Judgements the machine's own notes cards do not have yet",
)
async def get_pending_notes(notes: NotesWritebackServiceDep) -> JSONResponse:
    """The backlog, plus the write switch — a page has to say *why* Send is idle."""
    service = _require_writeback(notes)
    policy = await service.policy()
    return envelope_response(
        PendingNotesData(
            writes_enabled=policy.writes_enabled,
            fields=policy.fields,
            items=await service.pending_rows(),
        ).model_dump(mode="json")
    )


@router.post(
    "/notes/push",
    response_model=ApiResponse[NotesPushAccepted],
    status_code=202,
    summary="Send the selected pending judgements to the machine's notes cards",
)
async def post_notes_push(
    request: Request,
    notes: NotesWritebackServiceDep,
    body: NotesPushRequest | None = None,
) -> JSONResponse:
    """202: one frame per shot, in a background task, stopping on the first device error.

    The only way a judgement reaches the machine: a person selects shots on the
    Sync page and confirms. Every selected id must still be pending (409
    otherwise, nothing queued); writes off is a 403, audited. Saving a judgement
    never sends one.
    """
    service = _require_writeback(notes)
    tasks = request.app.state.tasks
    if tasks.get(writeback_task_name()) is not None:
        raise Conflict("A notes send is already running.")
    selected = await service.approve_push(body.shot_ids if body is not None else None)
    if not service.spawn_push(tasks, selected):
        raise Conflict("A notes send is already running.")
    return envelope_response(
        NotesPushAccepted(pending=len(selected)).model_dump(mode="json"),
        status_code=202,
    )
