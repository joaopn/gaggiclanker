"""API routers. ``api_router`` is mounted at ``/api``; health sits outside it."""

from __future__ import annotations

from fastapi import APIRouter

from gaggiclanker.api import (
    auth,
    backup,
    beans,
    chat,
    device,
    drafts,
    flavor_picks,
    grinders,
    health,
    imports,
    knowledge,
    llm,
    machine,
    patterns,
    profile_board,
    profiles,
    prompts,
    reviews,
    sets,
    settings,
    shot_info,
    shots,
    starting,
    sync,
    vocab,
)

__all__ = ["api_router", "health_router"]

api_router = APIRouter(prefix="/api")
# First, so the two public auth routes are impossible to miss when reading the
# mount order. The guard keeps them public by path, not by position.
api_router.include_router(auth.router)
api_router.include_router(settings.router)
api_router.include_router(backup.router)
api_router.include_router(device.router)
api_router.include_router(sync.router)
api_router.include_router(shots.router)
api_router.include_router(profiles.router)
api_router.include_router(profiles.versions_router)
api_router.include_router(drafts.router)
api_router.include_router(profile_board.router)
api_router.include_router(machine.router)
api_router.include_router(vocab.router)
api_router.include_router(flavor_picks.router)
api_router.include_router(shot_info.router)
api_router.include_router(beans.router)
api_router.include_router(grinders.router)
api_router.include_router(sets.router)
api_router.include_router(imports.router)
api_router.include_router(llm.router)
api_router.include_router(prompts.router)
api_router.include_router(knowledge.router)
api_router.include_router(patterns.router)
api_router.include_router(reviews.router)
api_router.include_router(starting.router)
api_router.include_router(chat.router)

health_router = health.router
