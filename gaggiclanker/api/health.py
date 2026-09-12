"""``GET /health`` — the container healthcheck and the UI's liveness probe.

Outside ``/api`` on purpose: when the auth guard arrives, every ``/api/*``
route needs a token, and a healthcheck that needs a token is not a healthcheck.

It reports the database too, because a process that is listening but cannot
reach its own SQLite file is not healthy in any sense the operator cares about.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gaggiclanker import __version__
from gaggiclanker.api.deps import DatabaseDep
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import ServiceUnavailable

__all__ = ["router"]

router = APIRouter(tags=["health"])


class HealthData(BaseModel):
    """The health payload."""

    status: str
    version: str
    database: str


@router.get("/health", response_model=ApiResponse[HealthData], summary="Liveness and readiness")
async def health(db: DatabaseDep) -> JSONResponse:
    try:
        await db.fetch_value("SELECT 1")
    except Exception:
        # ok:false, not a 503 wrapped in a success envelope. A probe that reads
        # `ok` (and the front end reads `ok` everywhere else) must not be told
        # the app is fine while the database is gone.
        raise ServiceUnavailable(
            "Database is unavailable",
            details={"version": __version__, "database": "unavailable"},
        ) from None

    return envelope_response(
        HealthData(status="ok", version=__version__, database="ok").model_dump()
    )
