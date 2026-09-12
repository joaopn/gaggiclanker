"""`/api/device` — what the machine is, and what it is doing right now.

Two endpoints, deliberately: a **poll** for the facts that change rarely
(configured, connected, identity, the last known status) and a **stream** for
the 2 Hz telemetry. Putting telemetry behind the poll would mean the UI either
refetched twice a second or showed a stale temperature; putting identity in the
stream would mean a tab that opened between two broadcasts knows nothing.

The web UI builds the real device page on top of these. Everything here is read-only,
like the client behind it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.events import Connected, DeviceEvent, Disconnected, StatusChanged
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.sse import SseEvent, sse_response

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
