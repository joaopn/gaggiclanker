"""FastAPI dependencies: the app-scoped objects routes ask for.

Everything long-lived (the database handle, the settings service, the event
bus, the sync engine) is built once in the lifespan and stored on
``app.state``. Routes reach it through these dependencies rather than a
module-level singleton, which is what lets a test build a second app on a
different data directory in the same process.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.machines import MachinesRepository
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.db.repos.sync import SyncRepository
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.infra.sse import SseEventBus
from gaggiclanker.settings import EnvSettings
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.sync.engine import SyncEngine

__all__ = [
    "DatabaseDep",
    "DeviceClientDep",
    "EnvSettingsDep",
    "EventBusDep",
    "MachinesRepoDep",
    "NotesRepoDep",
    "ProfilesRepoDep",
    "SettingsServiceDep",
    "ShotsRepoDep",
    "SyncEngineDep",
    "SyncRepoDep",
]


def get_database(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_settings_service(request: Request) -> SettingsService:
    service: SettingsService = request.app.state.settings_service
    return service


def get_env_settings(request: Request) -> EnvSettings:
    env: EnvSettings = request.app.state.env
    return env


def get_event_bus(request: Request) -> SseEventBus:
    bus: SseEventBus = request.app.state.events
    return bus


def get_device_client(request: Request) -> GaggimateClient | None:
    """The device client, or ``None`` when no machine is configured.

    ``None`` is a supported configuration, not a failure: the app is an archive
    browser first and everything already imported works with the machine
    unplugged.
    """
    client: GaggimateClient | None = getattr(request.app.state, "device", None)
    return client


def get_sync_engine(request: Request) -> SyncEngine | None:
    """The sync engine, or ``None`` when there is no machine to sync with."""
    engine: SyncEngine | None = getattr(request.app.state, "sync", None)
    return engine


# The repositories are built per request rather than held on app.state: they are
# a handle plus a few methods, constructing one is free, and a request-scoped
# object cannot accidentally cache a row across requests.
def get_shots_repo(request: Request) -> ShotsRepository:
    return ShotsRepository(get_database(request))


def get_profiles_repo(request: Request) -> ProfilesRepository:
    return ProfilesRepository(get_database(request))


def get_notes_repo(request: Request) -> NotesRepository:
    return NotesRepository(get_database(request))


def get_machines_repo(request: Request) -> MachinesRepository:
    return MachinesRepository(get_database(request))


def get_sync_repo(request: Request) -> SyncRepository:
    return SyncRepository(get_database(request))


DatabaseDep = Annotated[Database, Depends(get_database)]
SettingsServiceDep = Annotated[SettingsService, Depends(get_settings_service)]
EnvSettingsDep = Annotated[EnvSettings, Depends(get_env_settings)]
EventBusDep = Annotated[SseEventBus, Depends(get_event_bus)]
DeviceClientDep = Annotated["GaggimateClient | None", Depends(get_device_client)]
SyncEngineDep = Annotated["SyncEngine | None", Depends(get_sync_engine)]
ShotsRepoDep = Annotated[ShotsRepository, Depends(get_shots_repo)]
ProfilesRepoDep = Annotated[ProfilesRepository, Depends(get_profiles_repo)]
NotesRepoDep = Annotated[NotesRepository, Depends(get_notes_repo)]
MachinesRepoDep = Annotated[MachinesRepository, Depends(get_machines_repo)]
SyncRepoDep = Annotated[SyncRepository, Depends(get_sync_repo)]
