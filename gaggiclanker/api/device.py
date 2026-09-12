"""`/api/device` — what the machine is, and what it is doing right now.

Two endpoints, deliberately: a **poll** for the facts that change rarely
(configured, connected, identity, the last known status) and a **stream** for
the 2 Hz telemetry. Putting telemetry behind the poll would mean the UI either
refetched twice a second or showed a stale temperature; putting identity in the
stream would mean a tab that opened between two broadcasts knows nothing.

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

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from gaggiclanker.api.deps import (
    CleanupServiceDep,
    DeviceWritesRepoDep,
    NotesWritebackServiceDep,
    SettingsServiceDep,
)
from gaggiclanker.cleanup.service import CleanupPlan, CleanupService, cleanup_task_name
from gaggiclanker.db.repos.cleanup import CleanupRepository, CleanupRunRow
from gaggiclanker.db.repos.device_writes import DeviceWriteRow
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.events import Connected, DeviceEvent, Disconnected, StatusChanged
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import Conflict, ServiceUnavailable
from gaggiclanker.infra.sse import SseEvent, sse_response
from gaggiclanker.notes.writeback import NotesWritebackService

__all__ = ["router"]

router = APIRouter(prefix="/device", tags=["device"])

#: The high-frequency one. Deliberately *not* in the front end's
#: event-to-query-key map: it carries the whole status, so a consumer reads it
#: directly rather than invalidating a query twice a second.
LIVE_EVENT = "device.live"
#: The rare one. This is what tells the UI to re-read `/api/device/status`.
CONNECTION_EVENT = "device.connection"


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


class CleanupRunAccepted(BaseModel):
    """What was queued. Nothing has been deleted when this is sent."""

    model_config = ConfigDict(extra="forbid")

    machine_id: int
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
async def get_cleanup_plan(
    cleanup: CleanupServiceDep,
    machine_id: Annotated[int | None, Query()] = None,
) -> JSONResponse:
    """A dry run. Reads the archive and the last identity frame; writes nothing.

    This is the preview a person approves, and it lists what it will **not**
    delete as well as what it will — a shot that is quarantined or whose stored
    bytes do not match its header is named with the reason, because "why is that
    shot still on my machine" is otherwise unanswerable from this page.
    """
    plan = await _require_cleanup(cleanup).plan(machine_id)
    return envelope_response(plan.model_dump(mode="json"))


@router.post(
    "/cleanup/run",
    response_model=ApiResponse[CleanupRunAccepted],
    status_code=202,
    summary="Delete what the policy says, oldest first",
)
async def post_cleanup_run(
    request: Request,
    cleanup: CleanupServiceDep,
    machine_id: Annotated[int | None, Query()] = None,
) -> JSONResponse:
    """202, and the work happens in a background task.

    Not in the request, for the same reason an analysis is not: a run is one
    WebSocket frame per shot paced at two a second, so a hundred shots is most
    of a minute and `docker stop` allows ten seconds. The task is named
    `cleanup:<machine id>`, claimed synchronously, so a second tab pressing the
    button gets a 409 rather than a second pass fighting this one over the
    device's two HTTP slots.

    Every delete is still authorised by the write gate on its way out, which is
    what makes this route safe to expose at all: it cannot delete anything the
    archive does not already hold intact.
    """
    service = _require_cleanup(cleanup)
    plan = await service.plan(machine_id)
    if plan.machine_id is None:
        raise ServiceUnavailable(
            plan.blocked or "No machine has been synced yet, so there is nothing to clean up."
        )
    if not service.spawn(request.app.state.tasks, plan.machine_id, trigger="manual"):
        raise Conflict(
            "A cleanup is already running for this machine. Wait for it to finish; "
            "its result appears under Storage on the Device page."
        )
    return envelope_response(
        CleanupRunAccepted(
            machine_id=plan.machine_id,
            planned=len(plan.planned),
            task=cleanup_task_name(plan.machine_id),
        ).model_dump(mode="json"),
        status_code=202,
    )


@router.get(
    "/cleanup/runs",
    response_model=ApiResponse[CleanupRunsData],
    summary="Every cleanup pass this box has run",
)
async def list_cleanup_runs(
    request: Request,
    machine_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> JSONResponse:
    """Newest first. A run that stopped early shows both figures: planned and deleted."""
    items = await CleanupRepository(request.app.state.db).list_runs(machine_id, limit=limit)
    return envelope_response(CleanupRunsData(items=items).model_dump(mode="json"))


# ── notes write-back ─────────────────────────────────────────────────


def _require_writeback(service: NotesWritebackService | None) -> NotesWritebackService:
    if service is None:
        raise ServiceUnavailable("No machine is configured, so there is nowhere to write notes to.")
    return service


class PendingNotesData(BaseModel):
    """How many verdicts this box holds that the machine does not."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    writes_enabled: bool
    fields: list[str]
    shot_ids: list[int]


class NotesPushAccepted(BaseModel):
    """What was queued for the bulk push."""

    model_config = ConfigDict(extra="forbid")

    machine_id: int
    pending: int


@router.get(
    "/notes/pending",
    response_model=ApiResponse[PendingNotesData],
    summary="Judgements the machine's own notes cards do not have yet",
)
async def get_pending_notes(
    notes: NotesWritebackServiceDep,
    machine_id: Annotated[int | None, Query()] = None,
) -> JSONResponse:
    """The backlog, plus both switches — a page has to say *why* the list is idle."""
    service = _require_writeback(notes)
    policy = await service.policy()
    return envelope_response(
        PendingNotesData(
            enabled=policy.enabled,
            writes_enabled=policy.writes_enabled,
            fields=policy.fields,
            shot_ids=await service.pending(machine_id),
        ).model_dump(mode="json")
    )


@router.post(
    "/notes/push",
    response_model=ApiResponse[NotesPushAccepted],
    status_code=202,
    summary="Send every pending judgement to the machine's notes cards",
)
async def post_notes_push(
    request: Request,
    notes: NotesWritebackServiceDep,
    cleanup: CleanupServiceDep,
    machine_id: Annotated[int | None, Query()] = None,
) -> JSONResponse:
    """202: one frame per shot, in a background task, stopping on the first device error."""
    service = _require_writeback(notes)
    resolved = machine_id
    if resolved is None:
        plan = await _require_cleanup(cleanup).plan()
        resolved = plan.machine_id
    if resolved is None:
        raise ServiceUnavailable("No machine has been synced yet.")
    pending = await service.pending(resolved)
    if not service.spawn_bulk(request.app.state.tasks, resolved):
        raise Conflict("A notes push is already running for this machine.")
    return envelope_response(
        NotesPushAccepted(machine_id=resolved, pending=len(pending)).model_dump(mode="json"),
        status_code=202,
    )


@router.get(
    "/live",
    summary="Server-sent stream of the merged live status",
    response_class=EventSourceResponse,
)
async def get_device_live(request: Request) -> EventSourceResponse:
    """Every merged `evt:status`, plus connection changes.

    The status is already merged server-side (one socket, one merge) so each
    event is the whole picture and a tab that joins mid-shot is immediately
    correct. Heartbeat comments come from ``sse_response``; without them a
    reverse proxy closes an idle stream between shots.
    """
    client = get_device(request)
    return sse_response(_stream(client))


async def _stream(client: GaggimateClient | None) -> AsyncIterator[SseEvent]:
    if client is None:
        # No machine configured: say so once, then hold the connection open on
        # heartbeats alone. Answering 404 would put the browser's SSE helper
        # into a reconnect loop against a box working exactly as configured.
        # The wait ends when the request is cancelled, i.e. the tab closes.
        yield SseEvent(event=CONNECTION_EVENT, data={"connected": False, "configured": False})
        await asyncio.Event().wait()
        return

    # Whatever we already know, before the next frame arrives — otherwise a tab
    # opened while the machine is idle shows nothing until something changes.
    yield SseEvent(event=CONNECTION_EVENT, data={"connected": client.connected, "configured": True})
    if client.last_status is not None:
        yield SseEvent(event=LIVE_EVENT, data=client.last_status.model_dump(by_alias=True))

    async for event in client.subscribe():
        rendered = _render(event)
        if rendered is not None:
            yield rendered


def _render(event: DeviceEvent) -> SseEvent | None:
    """Only the two events a browser needs; the rest belong to the sync engine."""
    match event:
        case StatusChanged(status=status):
            return SseEvent(event=LIVE_EVENT, data=status.model_dump(by_alias=True))
        case Connected(host=host):
            return SseEvent(
                event=CONNECTION_EVENT, data={"connected": True, "configured": True, "host": host}
            )
        case Disconnected(reason=reason):
            return SseEvent(
                event=CONNECTION_EVENT,
                data={"connected": False, "configured": True, "reason": reason},
            )
        case _:
            return None
