"""``GET``/``PATCH /api/settings`` — the registry, resolved, over HTTP."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import RootModel

from gaggiclanker.api.deps import AuthServiceDep, DeviceConnectionDep, SettingsServiceDep
from gaggiclanker.device.connection import DEVICE_SETTING_KEYS, device_config
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.settings import ResolvedSetting

__all__ = ["router"]

router = APIRouter(prefix="/settings", tags=["settings"])

# A PATCH body is a flat object of registry key -> value, with null meaning
# "drop the stored row and fall back to the declared default". The
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
    body: SettingsPatchBody,
    service: SettingsServiceDep,
    auth: AuthServiceDep,
    connection: DeviceConnectionDep,
) -> JSONResponse:
    """Apply the patch, and end every session if it changed who may sign in.

    A patch touching the machine's connection settings applies them live: the
    connection is rebuilt from the new effective values, with no restart. If
    that would move the connection while something is using the machine — a
    profile push, a cleanup run, a notes send, a pull — the whole patch is a
    409 naming it and nothing is stored. Validation comes first, so a bad value
    is still a 400 whatever is running.

    `AuthService.verify` already refuses a token whose subject is not the
    configured user, so renaming the user locks the old tokens out on its own.
    Revoking is still worth doing: it leaves no live rows claiming a user who
    no longer exists, so `auth_sessions` answers "who is signed in" honestly,
    and it makes the rule one thing — **the credential changed, the sessions
    end** — rather than two behaviours that happen to coincide today.
    """
    before = {key: await service.get(key) for key in _AUTH_IDENTITY_KEYS}
    validated = await service.validate(body.root)
    if connection is not None and any(key in validated for key in DEVICE_SETTING_KEYS):
        proposed = device_config(await service.effective_after(validated, DEVICE_SETTING_KEYS))
        resolved = await connection.update(lambda: service.write(validated), proposed=proposed)
    else:
        resolved = await service.write(validated)
    changed = [key for key in _AUTH_IDENTITY_KEYS if resolved[key].value != before[key]]
    if changed:
        await auth.sessions.revoke_all()
    return envelope_response(_payload(resolved))
