"""`/api/sync` — what the engine is doing, and a way to ask it to do it now.

Three endpoints with three different tempos: a **poll** for the ledger
(`/status`), a **stream** for the feed (`/events`), and a **nudge** that returns
before any device I/O happens (`/run`).

`/run` is deliberately fire-and-forget. Sync is background work owned by the app
lifespan — that is the chunk's acceptance criterion, and it is also the only way
`POST /api/sync/run` can answer while a two-hundred-shot backfill is under way.
The caller watches `/events` or polls `/status`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from gaggiclanker.api.deps import (
    DeviceClientDep,
    EventBusDep,
    ShotsRepoDep,
    SyncEngineDep,
    SyncRepoDep,
)
from gaggiclanker.db.repos.shots import ShotCounts
from gaggiclanker.db.repos.sync import SyncEventRow, SyncRunRow
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import ServiceUnavailable
from gaggiclanker.infra.sse import SseEvent, sse_response
from gaggiclanker.sync.engine import SyncEngine

__all__ = ["router"]

router = APIRouter(prefix="/sync", tags=["sync"])

#: What `POST /api/sync/run` accepts. `notes` and `backfill` are spellings of
#: the shot pass rather than passes of their own: notes are pulled as the tail
#: of an index diff, because the diff is what learns which entries have any.
type RunKind = Literal["all", "shots", "backfill", "notes", "profiles", "identity"]


class SyncRunRequest(BaseModel):
    """Which pass to ask for. Omitting ``kind`` runs all of them."""

    model_config = ConfigDict(extra="forbid")

    kind: RunKind = "all"


class SyncRunAccepted(BaseModel):
    """What was queued. Nothing has touched the machine yet when this is sent."""

    model_config = ConfigDict(extra="forbid")

    queued: list[str]


class SyncStatusData(BaseModel):
    """The sync ledger in one object."""

    model_config = ConfigDict(extra="forbid")

    configured: bool
    connected: bool
    machine_id: int | None = None
    running: bool = False
    #: The most recent run of each kind, keyed by kind.
    last_runs: dict[str, SyncRunRow] = Field(default_factory=dict)
    #: The most recent *failed* run of any kind, so a red banner has something
    #: to say beyond "something went wrong".
    last_error: SyncRunRow | None = None
    counts: ShotCounts
    recent_events: list[SyncEventRow] = Field(default_factory=list)


def _require_engine(engine: SyncEngine | None) -> SyncEngine:
    if engine is None:
        raise ServiceUnavailable(
            "No machine is configured, so there is nothing to sync. "
            "Set `gaggimateHost` (and leave `deviceSyncEnabled` on) in settings."
        )
    return engine


@router.post(
    "/run",
    response_model=ApiResponse[SyncRunAccepted],
    status_code=202,
    summary="Ask the sync engine to run now",
)
async def post_sync_run(engine: SyncEngineDep, body: SyncRunRequest | None = None) -> JSONResponse:
    """Nudge the background loops. Returns 202 without waiting for the machine.

    202 rather than 200 because nothing has happened yet: the loops are woken,
    they take the engine's lock in turn, and the work shows up on `/events` and
    in `/status`. A synchronous variant would block the request for as long as a
    backfill takes and would let two callers start two passes over a device with
    two HTTP slots.
    """
    sync = _require_engine(engine)
    kind = (body or SyncRunRequest()).kind
    queued: list[str] = []
    if kind in ("all", "shots", "backfill", "notes"):
        sync.request_shot_sync("manual")
        queued.append("shots")
    if kind in ("all", "profiles"):
        sync.request_profile_sync("manual")
        queued.append("profiles")
    if kind in ("all", "identity"):
        sync.request_identity_sync("manual")
        queued.append("identity")
    return envelope_response(
        SyncRunAccepted(queued=queued).model_dump(mode="json"), status_code=202
    )


@router.get(
    "/status",
    response_model=ApiResponse[SyncStatusData],
    summary="Last run per kind, archive counts and the last error",
)
async def get_sync_status(
    runs: SyncRepoDep,
    shots: ShotsRepoDep,
    engine: SyncEngineDep,
    client: DeviceClientDep,
) -> JSONResponse:
    """Everything a "is the archive keeping up" panel needs, in one request."""
    last_runs = await runs.last_runs()
    machine = engine.machine if engine is not None else None
    return envelope_response(
        SyncStatusData(
            configured=client is not None,
            connected=bool(client is not None and client.connected),
            machine_id=machine.id if machine is not None else None,
            running=any(run.status == "running" for run in last_runs.values()),
            last_runs=last_runs,
            last_error=await runs.last_error(),
            counts=await shots.counts(),
            recent_events=await runs.recent_events(),
        ).model_dump(mode="json")
    )


@router.get(
    "/events",
    summary="Server-sent stream of sync progress",
    response_class=EventSourceResponse,
)
async def get_sync_events(request: Request, bus: EventBusDep) -> EventSourceResponse:
    """Run started/finished, shot ingested, shot quarantined, profile changed.

    The same lossy bus as everything else: an event means "this family of
    queries is stale, go and re-read", never "here is the new value". A tab that
    misses one under backpressure re-reads on the next, and `GET /api/sync/status`
    is the backstop behind both — there is no pass on a timer to be one.
    """
    _ = request  # the stream ends when the client disconnects, which cancels us
    return sse_response(_stream(bus.stream()))


async def _stream(source: AsyncIterator[SseEvent]) -> AsyncIterator[SseEvent]:
    """The app bus, forwarded verbatim.

    A pass-through rather than a direct hand-off so the stream has somewhere to
    grow a filter when a second producer starts publishing onto this bus.
    """
    async for event in source:
        yield event
