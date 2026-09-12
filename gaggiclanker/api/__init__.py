"""API routers. ``api_router`` is mounted at ``/api``; health sits outside it."""

from __future__ import annotations

from fastapi import APIRouter

from gaggiclanker.api import (
    backup,
    beans,
    device,
    grinders,
    health,
    imports,
    llm,
    machines,
    profiles,
    prompts,
    sets,
    settings,
    shots,
    sync,
    vocab,
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
api_router.include_router(vocab.router)
api_router.include_router(beans.router)
api_router.include_router(grinders.router)
api_router.include_router(sets.router)
api_router.include_router(imports.router)
api_router.include_router(llm.router)
api_router.include_router(prompts.router)

health_router = health.router
