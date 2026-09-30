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

from gaggiclanker.auth.service import AuthService
from gaggiclanker.chat.runner import ChatRunner
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.flavor_picks import FlavorPicksRepository
from gaggiclanker.db.repos.grinders import GrindersRepository
from gaggiclanker.db.repos.judgements import JudgementsRepository
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.knowledge_docs import KnowledgeDocsRepository
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.machines import MachineRepository
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.reviews import ShotReviewsRepository
from gaggiclanker.db.repos.set_proposals import SetProposalsRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.db.repos.sync import SyncRepository
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.connection import DeviceConnection
from gaggiclanker.drafts.service import ProfileDraftService
from gaggiclanker.infra.sse import SseEventBus
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.review.service import ReviewService
from gaggiclanker.settings import EnvSettings
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.shotinfo.service import ShotInformationService
from gaggiclanker.starting.service import StartingPointService
from gaggiclanker.sync.engine import SyncEngine

__all__ = [
    "AuthServiceDep",
    "BeansRepoDep",
    "ChatRunnerDep",
    "DatabaseDep",
    "DeviceClientDep",
    "DeviceConnectionDep",
    "DeviceWritesRepoDep",
    "DraftServiceDep",
    "EnvSettingsDep",
    "EventBusDep",
    "FlavorPicksRepoDep",
    "GrindersRepoDep",
    "InsightsRepoDep",
    "JudgementsRepoDep",
    "KnowledgeDocsRepoDep",
    "KnowledgeServiceDep",
    "LlmServiceDep",
    "MachineRepoDep",
    "NotesRepoDep",
    "ProfilesRepoDep",
    "PromptServiceDep",
    "ReviewServiceDep",
    "ReviewsRepoDep",
    "RulesRepoDep",
    "SetProposalsRepoDep",
    "SetsRepoDep",
    "SettingsServiceDep",
    "ShotInformationServiceDep",
    "ShotsRepoDep",
    "StartingPointServiceDep",
    "SyncEngineDep",
    "SyncRepoDep",
]


def get_database(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_settings_service(request: Request) -> SettingsService:
    service: SettingsService = request.app.state.settings_service
    return service


def get_auth_service(request: Request) -> AuthService:
    """The auth policy. App-scoped, because the login throttle is in memory.

    A per-request instance would count every failed attempt against a fresh
    counter, which is a throttle that never throttles.
    """
    service: AuthService = request.app.state.auth
    return service


def get_env_settings(request: Request) -> EnvSettings:
    env: EnvSettings = request.app.state.env
    return env


def get_event_bus(request: Request) -> SseEventBus:
    bus: SseEventBus = request.app.state.events
    return bus


def get_llm_service(request: Request) -> LlmService:
    service: LlmService = request.app.state.llm
    return service


def get_prompt_service(request: Request) -> PromptService:
    """The prompt renderer, built per request over the shared database handle.

    Per request rather than on ``app.state`` because it memoises a parsed YAML
    document keyed on the row's ``updated_at``; one long-lived instance would
    be a cache shared between a writer and every reader, and the bug that
    causes shows up as "my edit did not take" long after the commit.
    """
    return PromptService(PromptsRepository(get_database(request)))


def get_review_service(request: Request) -> ReviewService:
    """The review service. App-scoped, and it has to be.

    It holds the map of shots whose review row is being opened right now,
    which is half of "one running review per shot" — the registry's name guard
    is the other half. A per-request copy would make that map empty for every
    caller and two browser tabs would each start their own review.

    Its prompt service can be long-lived safely: the cache is keyed on the
    row's ``updated_at`` and the row is re-read on every load, so an edit
    invalidates the entry by changing the key (see
    :class:`~gaggiclanker.llm.prompts.PromptService`).
    """
    service: ReviewService = request.app.state.reviews
    return service


def get_chat_runner(request: Request) -> ChatRunner:
    """The chat runner. App-scoped, and it has to be.

    It holds the cancel event of every run in flight. A per-request instance
    would make the cancel button a no-op against an empty map — the same class
    of bug as a per-request rate limiter.
    """
    runner: ChatRunner = request.app.state.chat
    return runner


def get_device_connection(request: Request) -> DeviceConnection[SyncEngine] | None:
    """The one owner of the machine connection, or ``None`` before the lifespan built it."""
    connection: DeviceConnection[SyncEngine] | None = getattr(request.app.state, "connection", None)
    return connection


def get_device_client(request: Request) -> GaggimateClient | None:
    """The device client as it is now, or ``None`` when no machine is configured.

    ``None`` is a supported configuration, not a failure: the app is an archive
    browser first and everything already imported works with the machine
    unplugged. Read from the connection on every request, because a settings
    change rebuilds the client without a restart.
    """
    connection = get_device_connection(request)
    return connection.client if connection is not None else None


def get_sync_engine(request: Request) -> SyncEngine | None:
    """The sync engine as it is now, or ``None`` when there is no machine to sync with."""
    connection = get_device_connection(request)
    return connection.engine if connection is not None else None


# The repositories are built per request rather than held on app.state: they are
# a handle plus a few methods, constructing one is free, and a request-scoped
# object cannot accidentally cache a row across requests.
def get_shots_repo(request: Request) -> ShotsRepository:
    return ShotsRepository(get_database(request))


def get_profiles_repo(request: Request) -> ProfilesRepository:
    return ProfilesRepository(get_database(request))


def get_notes_repo(request: Request) -> NotesRepository:
    return NotesRepository(get_database(request))


def get_machine_repo(request: Request) -> MachineRepository:
    return MachineRepository(get_database(request))


def get_sync_repo(request: Request) -> SyncRepository:
    return SyncRepository(get_database(request))


def get_beans_repo(request: Request) -> BeansRepository:
    return BeansRepository(get_database(request))


def get_grinders_repo(request: Request) -> GrindersRepository:
    return GrindersRepository(get_database(request))


def get_sets_repo(request: Request) -> SetsRepository:
    return SetsRepository(get_database(request))


def get_set_proposals_repo(request: Request) -> SetProposalsRepository:
    return SetProposalsRepository(get_database(request))


def get_judgements_repo(request: Request) -> JudgementsRepository:
    return JudgementsRepository(get_database(request))


def get_flavor_picks_repo(request: Request) -> FlavorPicksRepository:
    return FlavorPicksRepository(get_database(request))


def get_shot_information_service(request: Request) -> ShotInformationService:
    return ShotInformationService(get_database(request), get_settings_service(request))


def get_rules_repo(request: Request) -> RulesRepository:
    return RulesRepository(get_database(request))


def get_knowledge_docs_repo(request: Request) -> KnowledgeDocsRepository:
    return KnowledgeDocsRepository(get_database(request))


def get_insights_repo(request: Request) -> InsightsRepository:
    return InsightsRepository(get_database(request))


def get_knowledge_service(request: Request) -> KnowledgeService:
    """Tiers 2 and 3, as one object.

    Built per request over the shared database handle rather than taken off
    ``app.state``: it holds three repositories and no state of its own, and the
    lifespan's copy exists only because boot seeding needs one before any route
    is reachable. Two instances cannot disagree about anything.
    """
    return KnowledgeService(get_database(request))


def get_reviews_repo(request: Request) -> ShotReviewsRepository:
    return ShotReviewsRepository(get_database(request))


def get_device_writes_repo(request: Request) -> DeviceWritesRepository:
    return DeviceWritesRepository(get_database(request))


def get_starting_point_service(request: Request) -> StartingPointService:
    """The starting-point wizard. App-scoped, for the review service's reason.

    It holds the map of runs whose row is being opened right now, which is half
    of "one run per bag and kit at a time" — the registry's name guard is the
    other half. A per-request copy would make that map empty for every caller
    and two browser tabs would each spend a call on the same question.
    """
    service: StartingPointService = request.app.state.starting
    return service


def get_draft_service(request: Request) -> ProfileDraftService:
    """The profile-draft service. App-scoped, because it reaches the device client.

    That client is the one object in the app that can change a machine, and it
    is built by the app's connection with the write gate wired into it. A
    per-request service would have to build its own — which would mean building
    a second gate, or worse, a client with none.
    """
    service: ProfileDraftService = request.app.state.drafts
    return service


DatabaseDep = Annotated[Database, Depends(get_database)]
SettingsServiceDep = Annotated[SettingsService, Depends(get_settings_service)]
EnvSettingsDep = Annotated[EnvSettings, Depends(get_env_settings)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
EventBusDep = Annotated[SseEventBus, Depends(get_event_bus)]
LlmServiceDep = Annotated[LlmService, Depends(get_llm_service)]
PromptServiceDep = Annotated[PromptService, Depends(get_prompt_service)]
DeviceConnectionDep = Annotated[
    "DeviceConnection[SyncEngine] | None", Depends(get_device_connection)
]
DeviceClientDep = Annotated["GaggimateClient | None", Depends(get_device_client)]
SyncEngineDep = Annotated["SyncEngine | None", Depends(get_sync_engine)]
ShotsRepoDep = Annotated[ShotsRepository, Depends(get_shots_repo)]
ProfilesRepoDep = Annotated[ProfilesRepository, Depends(get_profiles_repo)]
NotesRepoDep = Annotated[NotesRepository, Depends(get_notes_repo)]
MachineRepoDep = Annotated[MachineRepository, Depends(get_machine_repo)]
SyncRepoDep = Annotated[SyncRepository, Depends(get_sync_repo)]
BeansRepoDep = Annotated[BeansRepository, Depends(get_beans_repo)]
GrindersRepoDep = Annotated[GrindersRepository, Depends(get_grinders_repo)]
SetsRepoDep = Annotated[SetsRepository, Depends(get_sets_repo)]
SetProposalsRepoDep = Annotated[SetProposalsRepository, Depends(get_set_proposals_repo)]
JudgementsRepoDep = Annotated[JudgementsRepository, Depends(get_judgements_repo)]
FlavorPicksRepoDep = Annotated[FlavorPicksRepository, Depends(get_flavor_picks_repo)]
ShotInformationServiceDep = Annotated[ShotInformationService, Depends(get_shot_information_service)]
RulesRepoDep = Annotated[RulesRepository, Depends(get_rules_repo)]
KnowledgeDocsRepoDep = Annotated[KnowledgeDocsRepository, Depends(get_knowledge_docs_repo)]
InsightsRepoDep = Annotated[InsightsRepository, Depends(get_insights_repo)]
KnowledgeServiceDep = Annotated[KnowledgeService, Depends(get_knowledge_service)]
ReviewsRepoDep = Annotated[ShotReviewsRepository, Depends(get_reviews_repo)]
ReviewServiceDep = Annotated[ReviewService, Depends(get_review_service)]
DeviceWritesRepoDep = Annotated[DeviceWritesRepository, Depends(get_device_writes_repo)]
DraftServiceDep = Annotated[ProfileDraftService, Depends(get_draft_service)]
StartingPointServiceDep = Annotated[StartingPointService, Depends(get_starting_point_service)]
ChatRunnerDep = Annotated[ChatRunner, Depends(get_chat_runner)]
