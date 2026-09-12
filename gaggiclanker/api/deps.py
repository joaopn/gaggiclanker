"""FastAPI dependencies: the app-scoped objects routes ask for.

Everything long-lived (the database handle, the settings service, the event
bus) is built once in the lifespan and stored on ``app.state``. Routes reach it
through these dependencies rather than a module-level singleton, which is what
lets a test build a second app on a different data directory in the same
process.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from gaggiclanker.db.connection import Database
from gaggiclanker.infra.sse import EventBus
from gaggiclanker.settings import EnvSettings
from gaggiclanker.settings_service import SettingsService

__all__ = ["DatabaseDep", "EnvSettingsDep", "EventBusDep", "SettingsServiceDep"]


def get_database(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_settings_service(request: Request) -> SettingsService:
    service: SettingsService = request.app.state.settings_service
    return service


def get_env_settings(request: Request) -> EnvSettings:
    env: EnvSettings = request.app.state.env
    return env


def get_event_bus(request: Request) -> EventBus:
    bus: EventBus = request.app.state.events
    return bus


DatabaseDep = Annotated[Database, Depends(get_database)]
SettingsServiceDep = Annotated[SettingsService, Depends(get_settings_service)]
EnvSettingsDep = Annotated[EnvSettings, Depends(get_env_settings)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
