"""The application factory and its lifespan.

``create_app()`` builds everything; the module-level ``app`` is what uvicorn
imports (``uvicorn gaggiclanker.main:app``). Tests call the factory directly
with their own ``EnvSettings`` so each one gets a fresh data directory in the
same process — which is why nothing here is a module-level singleton.

Startup order matters and is fixed: configure logging, open the database, run
migrations, build the services, then start background tasks. Shutdown is the
reverse, and it waits for the background tasks before closing the connection so
nothing is mid-write when the file is released.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gaggiclanker import __version__
from gaggiclanker.analyzer.service import AnalyzerService
from gaggiclanker.api import api_router, health_router
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.analyses import AnalysesRepository
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.llm import LlmCallsRepository, PromptsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.infra.envelope import register_exception_handlers
from gaggiclanker.infra.logging import configure_logging, get_logger
from gaggiclanker.infra.middleware import RequestContextMiddleware
from gaggiclanker.infra.sse import EventBus, SseEvent
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.knowledge.rules import seed_rules
from gaggiclanker.llm.observer import LlmCallObserver
from gaggiclanker.llm.prompts import PromptService, seed_prompts
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings import EnvSettings, load_dotenv_values
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.static import mount_spa
from gaggiclanker.sync.engine import SyncEngine

__all__ = ["app", "create_app"]

log = get_logger(__name__)

DESCRIPTION = """
Archive, diagnose and analyse espresso shots from a GaggiMate machine.

Every response uses the envelope `{ok, data | error, meta}`. The `meta.request_id`
is the same id echoed in the `x-request-id` header and written on every log line
for that request.
"""


def ensure_data_dir(data_dir: Path) -> None:
    """Create ``DATA_DIR`` and prove it is writable, or explain exactly why not.

    This is the single most common deployment failure: Docker creates a missing
    bind-mount source as ``root:root``, the container runs as a non-root user,
    and SQLite reports "unable to open database file" — which says nothing about
    ownership and sends people looking for a corrupt database. Failing here,
    with the path, the uid and the fix, costs one boot instead of an evening.
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"DATA_DIR {data_dir} could not be created ({exc.strerror}). "
            f"This process runs as uid {os.getuid()}:{os.getgid()}; the parent "
            "directory must be writable by it."
        ) from exc

    probe = data_dir / ".write-test"
    try:
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"DATA_DIR {data_dir} is not writable ({exc.strerror}). "
            f"This process runs as uid {os.getuid()}:{os.getgid()}. With Docker, a "
            "bind-mount source that did not exist is created as root:root - either "
            "`mkdir -p ./data && chown $(id -u):$(id -g) ./data` before starting, or "
            "use the named volume the compose file documents."
        ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database, migrate, wire the services, and tear it all down."""
    env: EnvSettings = app.state.env

    ensure_data_dir(env.data_dir)

    db = Database(env.database_path)
    await db.connect()
    await run_migrations(db)

    app.state.db = db
    settings_service = SettingsService(SettingsRepository(db), dotenv=app.state.dotenv)
    app.state.settings_service = settings_service
    app.state.events = EventBus[SseEvent]()
    app.state.tasks = TaskRegistry()

    # Prompts are seeded before anything can call one: the rules in
    # gaggiclanker/llm/prompts.py are what let a shipped wording reach an
    # unedited row while leaving an edited one alone, and they only run here.
    await seed_prompts(PromptsRepository(db))
    # The knowledge tier, on the same three-way upsert and for the same reason
    # (gaggiclanker/knowledge/rules.py).
    await seed_rules(RulesRepository(db))

    # An analysis row is only `running` while a process holds it, and no process
    # survives a boot. Anything still in that state was cut off mid-call, and
    # saying so turns a spinner nobody can clear into a row with an error on it.
    interrupted = await AnalysesRepository(db).reconcile_running()
    if interrupted:
        log.info("analyses_reconciled", interrupted=interrupted)

    app.state.llm = LlmService(
        settings_service,
        observer=LlmCallObserver(app.state.events),
        calls_repo=LlmCallsRepository(db),
    )

    # App-scoped, not per request: it holds the "being opened right now" map
    # that makes one analysis per shot an invariant across concurrent requests.
    app.state.analyzer = AnalyzerService(
        db,
        app.state.llm,
        PromptService(PromptsRepository(db)),
        bus=app.state.events,
    )

    app.state.device = await start_device_client(settings_service)
    app.state.sync = await start_sync_engine(app)

    log.info("app_started", version=__version__, data_dir=str(env.data_dir))
    try:
        yield
    finally:
        # The device client first: its supervisor owns a socket and an aiohttp
        # session, and both have to be closed before the loop stops accepting
        # callbacks. Then the registry, then the database, so nothing is
        # mid-write when the file is released.
        if app.state.device is not None:
            await app.state.device.stop()
        app.state.sync = None
        await app.state.tasks.cancel_all()
        # After the tasks, before the database: the provider owns an HTTP
        # client whose connections must be released on the loop that made them.
        await app.state.llm.aclose()
        await db.close()
        log.info("app_stopped")


async def start_device_client(settings: SettingsService) -> GaggimateClient | None:
    """Build and start the device client, or return ``None`` if there is no machine.

    An unset ``gaggimateHost`` is a supported configuration, not an error: the
    app is an archive browser first and everything already imported works with
    the machine unplugged. ``deviceSyncEnabled`` is the same switch for someone
    who has a machine but is working on the archive and does not want to take
    one of the device's three WebSocket slots.

    Starting never blocks on the machine answering — ``start()`` spawns the
    supervisor and returns — so a box that boots before the espresso machine
    still serves in under a second.
    """
    host = str(await settings.get("gaggimateHost") or "").strip()
    if not host:
        log.info("device_not_configured")
        return None
    if not await settings.get("deviceSyncEnabled"):
        log.info("device_sync_disabled", host=host)
        return None

    client = GaggimateClient(
        host,
        protocol=str(await settings.get("gaggimateProtocol") or "ws"),
        timeout=float(await settings.get("gaggimateTimeoutSeconds")),
    )
    await client.start()
    log.info("device_client_started", host=host)
    return client


async def start_sync_engine(app: FastAPI) -> SyncEngine | None:
    """Build the sync engine and start its loops, or return ``None`` with no machine.

    Every loop it owns is registered with the app's :class:`TaskRegistry`, so
    shutdown cancels them in one call and nothing is mid-write when the database
    file is released. None of them blocks startup: the first thing each does is
    wait — for a device event, or for its own timer — and the machine may well be
    switched off.
    """
    client: GaggimateClient | None = app.state.device
    if client is None:
        return None
    engine = SyncEngine(client, app.state.db, app.state.events)
    await engine.start(app.state.tasks)
    return engine


def create_app(
    env: EnvSettings | None = None,
    *,
    web_dist: Path | None = None,
    dotenv: Mapping[str, str | None] | None = None,
) -> FastAPI:
    """Build the application.

    ``env`` defaults to reading the process environment and ``dotenv`` to
    parsing ``./.env``; both are parameters so a test gets neither by accident.
    ``web_dist`` overrides
    where the built SPA is looked for (falling back to ``WEB_DIST`` and then to
    ``<repo>/web/dist``); it is a parameter so a test can point at a fixture
    directory instead of the real build output.
    """
    env = env or EnvSettings()
    configure_logging(env.log_level, json_output=env.log_json)

    app = FastAPI(
        title="gaggiclanker",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.env = env
    app.state.dotenv = load_dotenv_values() if dotenv is None else dotenv

    # Middleware is applied outermost-last, so CORS goes on first and the
    # request context wraps it: a preflight response carries a request id too,
    # and an exception raised inside CORS handling still lands in the envelope.
    origins = env.cors_origin_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["x-request-id"],
        )

    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router)

    # Last: the SPA fallback is a catch-all and would shadow the routers above.
    app.state.spa_mounted = mount_spa(app, web_dist or env.web_dist)

    return app


app = create_app()
