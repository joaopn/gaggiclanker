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
from gaggiclanker.api import api_router, health_router
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.infra.envelope import register_exception_handlers
from gaggiclanker.infra.logging import configure_logging, get_logger
from gaggiclanker.infra.middleware import RequestContextMiddleware
from gaggiclanker.infra.sse import EventBus
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.settings import EnvSettings, load_dotenv_values
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.static import mount_spa

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
    app.state.settings_service = SettingsService(SettingsRepository(db), dotenv=app.state.dotenv)
    app.state.events = EventBus()
    app.state.tasks = TaskRegistry()

    # The device reader and the sync loop are registered here:
    #   app.state.tasks.spawn("device", device_client.run())

    log.info("app_started", version=__version__, data_dir=str(env.data_dir))
    try:
        yield
    finally:
        await app.state.tasks.cancel_all()
        await db.close()
        log.info("app_stopped")


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
