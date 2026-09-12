"""API routers. ``api_router`` is mounted at ``/api``; health sits outside it."""

from __future__ import annotations

from fastapi import APIRouter

from gaggiclanker.api import (
    backup,
    device,
    health,
    imports,
    machines,
    profiles,
    settings,
    shots,
    sync,
)

__all__ = ["api_router", "health_router"]

api_router = APIRouter(prefix="/api")
api_router.include_router(settings.router)
api_router.include_router(backup.router)
api_router.include_router(device.router)
api_router.include_router(sync.router)
api_router.include_router(shots.router)
api_router.include_router(profiles.router)
api_router.include_router(profiles.versions_router)
api_router.include_router(machines.router)
api_router.include_router(imports.router)

health_router = health.router
