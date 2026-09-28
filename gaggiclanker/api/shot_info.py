"""`/api/shot-information` — what a chat is told about each shot, and moving it.

Settings → Shot information reads the whole document and changes one item at a
time. Every route answers the whole document, so the page's estimates follow
its tiers from one response. A change applies from the next chat turn: the
tiers are read per turn and per tool call, never cached.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.deps import ShotInformationServiceDep
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.shotinfo.catalogue import Tier
from gaggiclanker.shotinfo.service import ShotInformation

__all__ = ["router"]

router = APIRouter(prefix="/shot-information", tags=["shot-information"])


class TierBody(BaseModel):
    """The tier one item moves to."""

    model_config = ConfigDict(extra="forbid")

    tier: Tier


@router.get(
    "",
    response_model=ApiResponse[ShotInformation],
    summary="Every item of shot information, its tier, an example and the cost",
)
async def get_shot_information(service: ShotInformationServiceDep) -> JSONResponse:
    return envelope_response((await service.document()).model_dump(mode="json"))


@router.post(
    "/reset",
    response_model=ApiResponse[ShotInformation],
    summary="Put every item back in its default tier",
)
async def reset_shot_information(service: ShotInformationServiceDep) -> JSONResponse:
    return envelope_response((await service.reset()).model_dump(mode="json"))


@router.put(
    "/{key}",
    response_model=ApiResponse[ShotInformation],
    summary="Move one item to a tier",
)
async def put_shot_information_tier(
    key: str, body: TierBody, service: ShotInformationServiceDep
) -> JSONResponse:
    return envelope_response((await service.set_tier(key, body.tier)).model_dump(mode="json"))
