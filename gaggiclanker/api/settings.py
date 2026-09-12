"""``GET``/``PATCH /api/settings`` — the registry, resolved, over HTTP."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import RootModel

from gaggiclanker.api.deps import AuthServiceDep, SettingsServiceDep
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
    """``{"gaggimateHost": "10.0.0.5", "modelAnalysis": null}``"""


class SettingsData(RootModel[dict[str, Any]]):
    """Registry key -> resolved setting. Secrets carry a hint, never a value."""


def _payload(resolved: dict[str, ResolvedSetting]) -> dict[str, Any]:
    return {key: setting.to_api() for key, setting in resolved.items()}


@router.get("", response_model=ApiResponse[SettingsData], summary="Read all runtime settings")
async def get_settings(service: SettingsServiceDep) -> JSONResponse:
    return envelope_response(_payload(await service.resolve_all()))


#: Changing one of these invalidates every issued token, so the sessions go.
_AUTH_IDENTITY_KEYS = ("authUser",)


@router.patch("", response_model=ApiResponse[SettingsData], summary="Update runtime settings")
async def patch_settings(
    body: SettingsPatchBody, service: SettingsServiceDep, auth: AuthServiceDep
) -> JSONResponse:
    """Apply the patch, and end every session if it changed who may sign in.

    `AuthService.verify` already refuses a token whose subject is not the
    configured user, so renaming the user locks the old tokens out on its own.
    Revoking is still worth doing: it leaves no live rows claiming a user who
    no longer exists, so `auth_sessions` answers "who is signed in" honestly,
    and it makes the rule one thing — **the credential changed, the sessions
    end** — rather than two behaviours that happen to coincide today.
    """
    before = {key: await service.get(key) for key in _AUTH_IDENTITY_KEYS}
    resolved = await service.apply(body.root)
    changed = [key for key in _AUTH_IDENTITY_KEYS if resolved[key].value != before[key]]
    if changed:
        await auth.sessions.revoke_all()
    return envelope_response(_payload(resolved))
