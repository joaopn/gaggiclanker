"""``POST /api/reset`` — put the app back to a fresh install and restart it.

The route only commits: it writes the marker the next boot acts on (see
:mod:`gaggiclanker.db.reset`), answers 202, and after the answer is sent stops the process
the one way the app restarts (the restore's path). The deleting happens at the next start.
"""

from __future__ import annotations

from typing import Literal

import structlog
from fastapi import APIRouter, BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.backup import refuse_while_busy, refuse_while_pending
from gaggiclanker.api.deps import EnvSettingsDep
from gaggiclanker.db.reset import write_reset_marker
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import InternalError

log = structlog.get_logger(__name__)

__all__ = ["router"]

router = APIRouter(prefix="/reset", tags=["reset"])


class ResetRequest(BaseModel):
    """The button sends the word; it only guards against a stray request."""

    model_config = ConfigDict(extra="forbid")

    confirm: Literal["reset"]


class ResetData(BaseModel):
    restarting: bool


@router.post(
    "",
    response_model=ApiResponse[ResetData],
    status_code=202,
    summary="Delete everything in the app and restart it as a fresh install",
)
async def reset_app(
    body: ResetRequest,
    request: Request,
    background: BackgroundTasks,
    env: EnvSettingsDep,
) -> JSONResponse:
    app: FastAPI = request.app
    # No await between these checks and the mark: a second reset, or a restore, finds this
    # one pending and is refused instead of racing it.
    refuse_while_pending(app)
    refuse_while_busy(app, code="RESET_BUSY")
    app.state.reset_pending = True
    try:
        # Synchronous, a few bytes, no await: a request cancelled around it can neither leave
        # a marker it did not report nor abandon a thread that finishes one.
        write_reset_marker(env.data_dir)
    except Exception as exc:
        # The marker is the commitment, and it is gone: nothing was decided, the app goes on.
        app.state.reset_pending = False
        log.error("reset_marker_failed", exc_info=True)
        if isinstance(exc, OSError):
            raise InternalError("The reset could not be started. Nothing was changed.") from None
        raise
    except BaseException:
        app.state.reset_pending = False
        raise
    log.info("reset_requested")
    background.add_task(app.state.terminate_process)
    return envelope_response(ResetData(restarting=True).model_dump(), status_code=202)
