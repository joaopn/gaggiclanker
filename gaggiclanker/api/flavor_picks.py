"""`/api/flavor-picks` — the notes the shot panel offers, for taste and for aroma.

Read by every open shot row and replaced whole by the Taste wheel page. The
wheel the notes come from is served with the rest of the vocabulary at
`/api/vocab`.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from gaggiclanker.api.deps import FlavorPicksRepoDep
from gaggiclanker.db.repos.flavor_picks import FlavorPicks
from gaggiclanker.infra.envelope import ApiResponse, envelope_response

__all__ = ["router"]

router = APIRouter(prefix="/flavor-picks", tags=["flavor-picks"])


@router.get(
    "",
    response_model=ApiResponse[FlavorPicks],
    summary="The flavour-wheel notes the shot panel offers",
)
async def get_flavor_picks(picks: FlavorPicksRepoDep) -> JSONResponse:
    return envelope_response((await picks.get()).model_dump(mode="json"))


@router.put(
    "",
    response_model=ApiResponse[FlavorPicks],
    summary="Replace both lists of flavour-wheel notes",
)
async def put_flavor_picks(body: FlavorPicks, picks: FlavorPicksRepoDep) -> JSONResponse:
    stored = await picks.replace(body)
    return envelope_response(stored.model_dump(mode="json"))
