"""`/api/device` — what the machine is, and what this box has asked it to do.

One poll for the facts that change rarely: configured, connected, identity, the
last status frame the socket happened to carry. There is no telemetry stream
here any more. The machine's own web UI draws the shot that is happening now,
over the same socket, and a second copy of it was a worse copy that cost a
subscription per open tab and a re-render twice a second — so this application
answers the question it is better placed to answer, which is what the archive
holds.

The web UI's Device page reads the status and everything else here. The write
audit lists every write this box has ever asked the machine to make, which is
what answers "what has this thing done to my machine". The only
thing this box ever stores on the machine is a profile, so there is no route here
that deletes a shot or sends a note: what the audit lists is the profile board's
saves, deletes, selections and stars, and the older rows of the two history
writes that were removed. The two other writes are `/flush`, the top bar's Flush
button, which runs the machine's own flush once, and `/mode`, the top bar's
"Switch to Brew" / "Switch to Standby" button. Both need the Writes switch on,
store nothing on the machine and leave no audit row.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gaggiclanker.api.deps import (
    DeviceClientDep,
    DeviceWritesRepoDep,
    SettingsServiceDep,
)
from gaggiclanker.db.repos.device_writes import DeviceWriteRow
from gaggiclanker.device.errors import DeviceUnavailable
from gaggiclanker.infra.envelope import ApiResponse, envelope_response

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


@router.get(
    "/status",
    response_model=ApiResponse[DeviceStatusData],
    summary="Connection state, identity and the last known live status",
)
async def get_device_status(client: DeviceClientDep) -> JSONResponse:
    """The client as it is now: a settings change rebuilds it, and this follows."""
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


class FlushData(BaseModel):
    """What `POST /api/device/flush` answers once the machine accepted the flush."""

    started: bool


@router.post(
    "/flush",
    response_model=ApiResponse[FlushData],
    summary="Run the machine's flush once, for the duration set on the machine",
)
async def start_flush(client: DeviceClientDep) -> JSONResponse:
    """One click, one flush: the button on the machine's own web UI, from the top bar.

    Refused (nothing sent) with the Writes switch off, outside brew mode, or while
    a shot or a flush is running; the client says which.
    """
    if client is None:
        raise DeviceUnavailable("No machine is configured. Set its address in Settings.")
    await client.start_flush()
    return envelope_response(FlushData(started=True).model_dump(mode="json"))


class ModeRequest(BaseModel):
    """The body of `POST /api/device/mode`: the two modes the top bar offers, and no others."""

    mode: Literal["brew", "standby"]


class ModeData(BaseModel):
    """What `POST /api/device/mode` answers once the machine reports the mode asked for."""

    mode: Literal["brew", "standby"]


@router.post(
    "/mode",
    response_model=ApiResponse[ModeData],
    summary="Put the machine in brew mode or in standby",
)
async def change_mode(body: ModeRequest, client: DeviceClientDep) -> JSONResponse:
    """The top bar's mode button: what the mode buttons on the machine's own web UI do.

    Refused (nothing sent) with the Writes switch off, while a shot or a flush is
    running (the firmware's mode change would stop it), or while the machine is not
    ready; the client says which. Answers once the machine's status frame shows the
    new mode.
    """
    if client is None:
        raise DeviceUnavailable("No machine is configured. Set its address in Settings.")
    await client.change_mode(body.mode)
    return envelope_response(ModeData(mode=body.mode).model_dump(mode="json"))
