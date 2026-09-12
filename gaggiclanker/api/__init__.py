"""API routers. ``api_router`` is mounted at ``/api``; health sits outside it."""

from __future__ import annotations

from fastapi import APIRouter

from gaggiclanker.api import backup, device, health, settings

__all__ = ["api_router", "health_router"]

api_router = APIRouter(prefix="/api")
api_router.include_router(settings.router)
api_router.include_router(backup.router)
api_router.include_router(device.router)

health_router = health.router
