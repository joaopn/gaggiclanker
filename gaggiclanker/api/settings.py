"""``GET``/``PATCH /api/settings`` — the registry, resolved, over HTTP."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import RootModel

from gaggiclanker.api.deps import SettingsServiceDep
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.settings import ResolvedSetting

__all__ = ["router"]

router = APIRouter(prefix="/settings", tags=["settings"])

# A PATCH body is a flat object of registry key -> value, with null meaning
# "drop the override and fall back to the environment or the default". The
# union is deliberately wide: per-key type checking belongs to the registry,
# which produces a message naming the expected type, rather than to a schema
# that can only say "not a valid integer".
type SettingValue = str | int | float | bool | None


class SettingsPatchBody(RootModel[dict[str, SettingValue]]):
    """``{"gaggimateHost": "10.0.0.5", "llmModel": null}``"""


class SettingsData(RootModel[dict[str, Any]]):
    """Registry key -> resolved setting. Secrets carry a hint, never a value."""


def _payload(resolved: dict[str, ResolvedSetting]) -> dict[str, Any]:
    return {key: setting.to_api() for key, setting in resolved.items()}


@router.get("", response_model=ApiResponse[SettingsData], summary="Read all runtime settings")
async def get_settings(service: SettingsServiceDep) -> JSONResponse:
    return envelope_response(_payload(await service.resolve_all()))


@router.patch("", response_model=ApiResponse[SettingsData], summary="Update runtime settings")
async def patch_settings(body: SettingsPatchBody, service: SettingsServiceDep) -> JSONResponse:
    return envelope_response(_payload(await service.apply(body.root)))
