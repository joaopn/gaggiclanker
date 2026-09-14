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
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Route

from gaggiclanker import __version__
from gaggiclanker.analyzer.service import AnalyzerService
from gaggiclanker.api import api_router, health_router
from gaggiclanker.auth.guard import AuthGuardMiddleware
from gaggiclanker.auth.service import AuthService
from gaggiclanker.chat.runner import ChatRunner
from gaggiclanker.cleanup.service import CleanupService
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.analyses import AnalysesRepository
from gaggiclanker.db.repos.chat import ChatRepository
from gaggiclanker.db.repos.cleanup import CleanupRepository
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.llm import LlmCallsRepository, PromptsRepository
from gaggiclanker.db.repos.starting import StartingPointRunsRepository
from gaggiclanker.db.repos.sync import SyncRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.drafts.gate import SettingsWriteGate
from gaggiclanker.drafts.service import ProfileDraftService
from gaggiclanker.infra.envelope import register_exception_handlers
from gaggiclanker.infra.logging import configure_logging, get_logger
from gaggiclanker.infra.middleware import RequestContextMiddleware
from gaggiclanker.infra.ratelimit import RateLimiter
from gaggiclanker.infra.security import BodyLimitMiddleware, SecurityHeadersMiddleware
from gaggiclanker.infra.sse import EventBus, SseEvent
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.knowledge.rules import seed_rules
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.llm.observer import LlmCallObserver
from gaggiclanker.llm.prompts import PromptService, seed_prompts
from gaggiclanker.llm.service import LlmService
from gaggiclanker.mcp.http import MCP_PATH, McpMount, start_mcp, stop_mcp
from gaggiclanker.mcp.server import build_mcp_server
from gaggiclanker.notes.writeback import NotesWritebackService
from gaggiclanker.settings import EnvSettings, load_dotenv_values, retired_auth_env_keys
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.starting.service import StartingPointService
from gaggiclanker.static import mount_spa
from gaggiclanker.sync.engine import SyncEngine
from gaggiclanker.tools import registry as tool_registry
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext

__all__ = ["app", "check_configuration", "create_app"]

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


def check_configuration(
    env: EnvSettings,
    dotenv: Mapping[str, str | None] | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Every check that needs nothing but the environment. Runs first, always.

    "First" is the whole point. Anything that raises **after** ``db.connect()``
    leaves a live aiosqlite worker thread behind: aiosqlite builds it as a plain
    ``threading.Thread`` with no ``daemon=True``, so the only thing that stops
    it is our ``close()``. Whether a leaked one actually holds the interpreter
    open at ``sys.exit`` comes down to a ``__del__`` in aiosqlite's own
    ``Connection`` happening to run during garbage collection — which it does on
    0.22.1, and which is not a thing to depend on. A container that logged
    "Application startup failed" and then hung would never exit non-zero,
    ``restart: unless-stopped`` would never fire, and a typo in a compose file
    would become a box that is simply dead rather than one that restart-loops
    with the reason in its log.

    So a check that can be made without opening anything is made here, and the
    teardown in :func:`lifespan` covers the ones that cannot.
    ``tests/test_startup.py`` holds both halves in place.

    The one check today: no credential variable may be set — not the sign-in
    settings, not an LLM provider's key or token, not a variable the SDKs would
    read a credential from, and no proxy variable carrying a user or password. Credentials are
    configured in the database alone, and an install that used to configure
    sign-in through the environment has its user and hash only there — starting
    with those variables silently ignored would switch authentication off and
    open every route. Refusing is the closed failure; the log names the
    variables (never a value) and says where credentials live now. ``environ`` and ``dotenv`` are
    parameters so a test can hand in its own; they default to the process
    environment and to nothing.
    """
    retired = retired_auth_env_keys(
        os.environ if environ is None else environ, {} if dotenv is None else dotenv
    )
    if retired:
        log.error(
            "auth_env_refused",
            env_keys=retired,
            fix=(
                "enter sign-in under Settings → Authentication and provider keys under "
                "Settings → LLM, remove these variables, and name any proxy without a "
                "user or password"
            ),
        )
        raise RuntimeError(
            "Credentials for external services live only in the database, and these variables "
            f"carry one: {', '.join(retired)}. Remove them from the environment, compose.yml and "
            ".env (a proxy may stay if it names no user or password), then enter sign-in under "
            "Settings → Authentication and provider keys under Settings → LLM. Refusing to start "
            "rather than start with authentication silently switched off or a credential taken "
            "from somewhere other than the database."
        )


async def _shutdown(app: FastAPI, db: Database) -> None:
    """Release everything the lifespan acquired, in reverse, tolerating gaps.

    Called from two places: the ordinary shutdown, and a failed startup — where
    only some of these exist. Hence ``getattr`` throughout rather than direct
    attribute access: a startup that died building the analyzer must still close
    the database, and a teardown that raised ``AttributeError`` on the way would
    leave the very thread this exists to reap.

    The order is the acquisition order reversed. The device client first: its
    supervisor owns a socket and an aiohttp session, and both have to be closed
    before the loop stops accepting callbacks. Then the registry, so nothing is
    mid-write. Then the LLM service, whose provider owns an HTTP client whose
    connections must be released on the loop that made them. The database last.
    """
    device: GaggimateClient | None = getattr(app.state, "device", None)
    if device is not None:
        with suppress(Exception):
            await device.stop()
    app.state.sync = None
    app.state.drafts = None
    app.state.starting = None
    app.state.cleanup = None
    app.state.notes_writeback = None

    # Before the task registry: the MCP session manager owns a task group whose
    # children hold open response streams, and cancelling the app's own tasks
    # out from under it would leave those streams half-written.
    with suppress(Exception):
        await stop_mcp(app)

    tasks: TaskRegistry | None = getattr(app.state, "tasks", None)
    if tasks is not None:
        with suppress(Exception):
            await tasks.cancel_all()

    llm: LlmService | None = getattr(app.state, "llm", None)
    if llm is not None:
        with suppress(Exception):
            await llm.aclose()

    await db.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database, migrate, wire the services, and tear it all down."""
    env: EnvSettings = app.state.env

    # Before anything is opened: see check_configuration for why the order is
    # not a matter of taste.
    check_configuration(env, app.state.dotenv)
    ensure_data_dir(env.data_dir)

    db = Database(env.database_path)
    await db.connect()
    try:
        await _start(app, db)
    except BaseException:
        # BaseException, not Exception: a cancellation or a Ctrl-C during
        # startup leaves the same open connection behind, and the same
        # non-daemon thread holding the interpreter open.
        log.exception("app_startup_failed")
        try:
            await _shutdown(app, db)
        except BaseException:
            # Best effort. A teardown that is itself cancelled must neither
            # mask the failure that caused it nor stop us re-raising.
            log.warning("app_shutdown_after_failed_startup_incomplete", exc_info=True)
        # Re-raised so uvicorn reports the failure and exits non-zero, rather
        # than serving an app that is half built.
        raise

    log.info("app_started", version=__version__, data_dir=str(env.data_dir))
    try:
        yield
    finally:
        await _shutdown(app, db)
        log.info("app_stopped")


async def _start(app: FastAPI, db: Database) -> None:
    """Migrate, wire every service onto ``app.state``, and start the loops."""
    env: EnvSettings = app.state.env
    await run_migrations(db)

    app.state.db = db
    settings_service = SettingsService(SettingsRepository(db), dotenv=app.state.dotenv)
    app.state.settings_service = settings_service
    # One line per retired variable still set, naming it and never its value:
    # each of these used to switch on a write to the machine that nothing does
    # automatically any more, and whoever set it should hear that it is inert.
    for env_key in settings_service.removed_env_keys():
        log.warning("setting_removed_env_ignored", env_key=env_key)
    app.state.events = EventBus[SseEvent]()
    app.state.tasks = TaskRegistry()
    app.state.rate_limits = RateLimiter()

    # Built before anything can serve a request, and before reconciliation:
    # the guard reads `app.state.auth` on every request, and boot work runs in
    # the lifespan precisely so it happens with no request in flight.
    auth = AuthService(db, settings_service)
    app.state.auth = auth
    forgotten = await auth.cleanup()

    # Prompts are seeded before anything can call one: the rules in
    # gaggiclanker/llm/prompts.py are what let a shipped wording reach an
    # unedited row while leaving an edited one alone, and they only run here.
    await seed_prompts(PromptsRepository(db))
    # The knowledge tier, on the same three-way upsert and for the same reason
    # (gaggiclanker/knowledge/rules.py). Tier 2's documents go through the same
    # rule at document level, and re-chunk whatever they move.
    await seed_rules(RulesRepository(db))
    app.state.knowledge = KnowledgeService(db)
    await app.state.knowledge.seed_docs()

    # Boot reconciliation. A row is only `running` while a process holds it, and
    # no process survives a boot: anything still in that state was cut off
    # mid-flight. Saying so turns a spinner nobody can clear into a row with an
    # error on it, for an analysis and for a sync run alike.
    #
    # One line either way, even when the counts are zero, because "what did the
    # last restart interrupt" is the first question after an unexpected one and
    # an absent log line does not answer it.
    interrupted_analyses = await AnalysesRepository(db).reconcile_running()
    interrupted_syncs = await SyncRepository(db).reconcile_running()
    # The cleanup ledger gets the same treatment and for the same reason: a
    # `running` cleanup row nothing closes would show a deletion in progress for
    # ever on the Sync page.
    interrupted_cleanups = await CleanupRepository(db).reconcile_running()
    # The chat's runs, for the reason an analysis's are: a `running` chat run
    # nobody owns is a spinner and a cancel button that cancels nothing.
    interrupted_chats = await ChatRepository(db).reconcile_running()
    # The wizard's runs. Same rule again: the wizard renders a `running` row as a
    # spinner, and nothing else would ever clear one left by a restart.
    interrupted_starts = await StartingPointRunsRepository(db).reconcile_running()
    log.info(
        "boot_reconciled",
        analyses_interrupted=interrupted_analyses,
        sync_runs_interrupted=interrupted_syncs,
        cleanup_runs_interrupted=interrupted_cleanups,
        chat_runs_interrupted=interrupted_chats,
        starting_points_interrupted=interrupted_starts,
        auth_sessions_expired=forgotten,
        auth_enabled=await auth.enabled(),
    )

    app.state.llm = LlmService(
        settings_service,
        observer=LlmCallObserver(app.state.events),
        calls_repo=LlmCallsRepository(db),
        # Only `claude_code` reads it, and only for the chat: it is what the
        # generated `--mcp-config` points the CLI's own MCP client at, so the
        # tool loop inside Claude Code reads this database.
        data_dir=str(env.data_dir),
    )

    # App-scoped, not per request: it holds the "being opened right now" map
    # that makes one analysis per shot an invariant across concurrent requests.
    app.state.analyzer = AnalyzerService(
        db,
        app.state.llm,
        PromptService(PromptsRepository(db)),
        bus=app.state.events,
    )

    # Before the device client, deliberately. Starting a task group is tens of
    # milliseconds, and the sync engine's first profiles sweep is timed from
    # the moment its loops start: anything slow wedged between the two shifts
    # that sweep relative to whatever a caller does next. MCP depends on none
    # of it, so it goes where it costs nothing.
    await _start_mcp_if_enabled(app, db, settings_service)

    app.state.device = await start_device_client(settings_service, db)
    app.state.sync = await start_sync_engine(app)

    # Cleanup and notes write-back. Both are app-scoped for the reason the draft service
    # is: they hold the one client that can change a machine, and a per-request
    # copy would have to build its own — which would mean a second gate, or a
    # client with none. Neither is wired to anything that runs on its own: they
    # act only when a person confirms an action on the Sync page.
    app.state.cleanup = CleanupService(
        db, settings_service, client=app.state.device, bus=app.state.events
    )
    app.state.notes_writeback = NotesWritebackService(
        db, settings_service, client=app.state.device, bus=app.state.events
    )

    # App-scoped because it holds the one client that can change a machine. A
    # per-request service would have to build its own — and a client built
    # without the gate cannot write at all, which is the right default and the
    # wrong thing to discover from a push that silently refused.
    app.state.drafts = ProfileDraftService(
        db,
        app.state.llm,
        PromptService(PromptsRepository(db)),
        settings_service,
        client=app.state.device,
    )

    # After the draft service, because it hands one to `accept`: an option that
    # carries a whole profile becomes a draft through the same four layers a
    # hand-typed one goes through, and a starting point built without that would
    # be the one path around the write gate.
    app.state.starting = StartingPointService(
        db,
        app.state.llm,
        PromptService(PromptsRepository(db)),
        drafts=app.state.drafts,
        bus=app.state.events,
    )

    # App-scoped for the reason the analyzer is: it holds the cancel event of
    # every run in flight, and a per-request copy would make the cancel button a
    # no-op. Built last, because it reaches into the analyzer, the draft service
    # and the starting-point service for the three tools that queue work.
    app.state.chat = ChatRunner(
        db,
        app.state.llm,
        PromptService(PromptsRepository(db)),
        tools=tool_registry,
        bus=app.state.events,
        analyzer=app.state.analyzer,
        drafts=app.state.drafts,
        starting=app.state.starting,
        knowledge=app.state.knowledge,
        tasks=app.state.tasks,
        rate_limits=app.state.rate_limits,
    )


async def _start_mcp_if_enabled(app: FastAPI, db: Database, settings: SettingsService) -> None:
    """Mount the MCP endpoint, unless the switch says not to.

    The permission set is the chat's, and it is a constant rather than a
    setting: an MCP client reads and proposes exactly as the in-app chat does,
    and nothing it calls can write to the machine. ``ToolContext`` still carries
    the set, so the dispatcher re-checks it on every call.
    """
    app.state.mcp_manager = None
    app.state.mcp_stack = None
    if not bool(await settings.get("mcpEnabled")):
        log.info("mcp_disabled")
        return
    permissions = CHAT_PERMISSIONS

    async def context() -> ToolContext:
        # Read off `app.state` per call rather than captured: this runs before
        # the draft service exists, and a factory that closed over the values
        # would hand every MCP client a `None` for the life of the process.
        return ToolContext(
            db=db,
            settings=settings,
            knowledge=getattr(app.state, "knowledge", None),
            analyzer=getattr(app.state, "analyzer", None),
            drafts=getattr(app.state, "drafts", None),
            starting=getattr(app.state, "starting", None),
            tasks=getattr(app.state, "tasks", None),
            rate_limits=getattr(app.state, "rate_limits", None),
            caller="mcp-http",
            permissions=permissions,
        )

    await start_mcp(
        app,
        server=build_mcp_server(
            context, registry=tool_registry, permissions=permissions, db_for_resources=db
        ),
    )


async def start_device_client(settings: SettingsService, db: Database) -> GaggimateClient | None:
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
        # The one place the gate is attached. Without it every write method
        # refuses, which is what a client built anywhere else in this codebase
        # gets — see `gaggiclanker/device/writes.py`. The database goes in with
        # it because the `shot_delete` branch has to look the shot up in
        # the archive before it will allow the machine to lose it.
        write_gate=SettingsWriteGate(settings, DeviceWritesRepository(db), db=db),
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

    # Middleware is applied outermost-last, so this block reads bottom-up. The
    # resulting order, outermost first:
    #
    #   RequestContextMiddleware   request id, the access log line, the error
    #                              boundary — everything below it is inside a
    #                              request context and lands in the envelope
    #   SecurityHeadersMiddleware  stamps every response, error envelopes and
    #                              the SPA included
    #   CORSMiddleware             only when origins are configured; off by
    #                              default, because the SPA is same-origin
    #   BodyLimitMiddleware        refuses an oversized body before the guard
    #                              spends argon2 time on it
    #   AuthGuardMiddleware        401s everything under /api without a token
    #
    # **CORS has to be outside the guard**, and that is not a detail. A browser
    # cannot read a cross-origin response that carries no
    # `Access-Control-Allow-Origin` — it does not see a 401, it sees a network
    # error. With CORS inside, the guard's own 401 came back bare, so the Vite
    # dev server's SPA could not tell "your token expired" from "the backend is
    # down" and never redirected to the sign-in page. Outside, the rejection is
    # a readable 401 and `fetchApi`'s handler does its job. (A preflight never
    # reaches the guard anyway: `requires_auth` lets OPTIONS through, because a
    # preflight carries no credentials by definition.)
    app.add_middleware(AuthGuardMiddleware)
    app.add_middleware(BodyLimitMiddleware)

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

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router)

    # Before the SPA fallback, which is a catch-all: a route appended after it
    # is unreachable. A `Route` rather than a `mount`, because a mount at
    # `/mcp` answers `/mcp` itself with a 307 to `/mcp/` — and the endpoint URL
    # is what every client has in its configuration file, verbatim. The shim
    # resolves the session manager off `app.state` at request time, so a request
    # that arrives before the lifespan has built one gets a 503 rather than a
    # stack trace. See gaggiclanker/mcp/http.py.
    app.router.routes.append(Route(MCP_PATH, endpoint=McpMount(app)))

    # Last: the SPA fallback is a catch-all and would shadow the routers above.
    app.state.spa_mounted = mount_spa(app, web_dist or env.web_dist)

    return app


app = create_app()
