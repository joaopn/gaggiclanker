"""Test harness: a real app, on a real SQLite file, in a temp DATA_DIR.

No in-memory database and no mocked repositories. A test exercises the same
connection pragmas, the same migration runner and the same file layout as the
container, because the things most likely to break here (WAL, foreign keys,
``VACUUM INTO``, the migration ledger) only exist on a real file.

Isolation comes from ``DATA_DIR``: each test gets its own ``tmp_path`` and its
own app object, so nothing is shared between tests but the process.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings

# Environment variables that would leak a developer's real configuration into a
# test. Cleared for every test; a test that wants one sets it explicitly.
LEAKY_ENV_PREFIXES = ("GAGGICLANKER_",)
# A path that does not exist, used as the default ``web_dist`` for every app a
# test builds. Without it the suite's behaviour depends on whether the developer
# happened to run `npm run build`: ``create_app`` falls back to ``<repo>/web/dist``,
# and a present bundle turns "unknown route -> JSON 404" into "unknown route ->
# index.html" for half of test_api_contract.py. A test that wants the SPA passes
# its own fixture directory; a test that wants the real fallback chain (the
# ``WEB_DIST`` environment variable) passes ``web_dist=None`` explicitly.
NO_WEB_DIST = Path(__file__).resolve().parent / "fixtures" / "_no-web-dist"

# Sentinel separating "caller said nothing" from "caller said None", which for
# ``web_dist`` mean opposite things.
_UNSET: Any = object()

LEAKY_ENV_KEYS = (
    "DATA_DIR",
    "LOG_LEVEL",
    "LOG_JSON",
    "HOST",
    "PORT",
    "CORS_ORIGINS",
    "GAGGIMATE_HOST",
    "GAGGIMATE_PROTOCOL",
    "GAGGIMATE_TIMEOUT_S",
    # Credentials that used to be read from the environment under the names the
    # tools themselves use, and the CLI's binary and effort. The credential
    # names now refuse boot outright, so a developer with a real
    # ANTHROPIC_API_KEY exported would otherwise see every test fail to start.
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_BIN",
    "CLAUDE_CODE_EFFORT",
    # The retired sign-in variables. A developer with these exported would have
    # every app in the suite refuse to boot, and the failure would read as
    # "everything is broken" rather than as "your shell has AUTH_USER in it".
    "AUTH_USER",
    "AUTH_PASSWORD",
    "AUTH_PASSWORD_HASH",
    "AUTH_TOKEN_TTL_S",
    "AUTH_JWT_SECRET",
    # Credential variables the SDKs underneath would honour, and proxies (which
    # refuse boot when they carry a password). Offline tests need neither.
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_CUSTOM_HEADERS",
    "OPENAI_API_KEY",
    "OPENAI_ADMIN_KEY",
    "OPENAI_CUSTOM_HEADERS",
    "OPENROUTER_API_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def pytest_addoption(parser: pytest.Parser) -> None:
    """``--update-golden`` rewrites the committed golden files instead of failing.

    The analyzer's prompt is a golden test (`tests/analyzer/test_context.py`):
    a fixture Set of six shots renders to a file in the repository, and any
    change to the assembly shows up as a diff in review rather than as a
    passing test nobody read. Regenerating it has to be one flag, or the
    temptation is to loosen the assertion instead.
    """
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite golden files from the current output instead of comparing",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip inherited configuration so tests start from the declared defaults."""
    for key in list(os.environ):
        if key in LEAKY_ENV_KEYS or key.startswith(LEAKY_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """The per-test DATA_DIR."""
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def env(data_dir: Path) -> EnvSettings:
    """Bootstrap settings pointed at the temp data directory.

    ``_env_file=None`` so a ``.env`` in the working directory cannot reach a
    test run.
    """
    return EnvSettings(
        DATA_DIR=str(data_dir),
        LOG_LEVEL="warning",
        LOG_JSON=True,
        # pydantic-settings takes _env_file per instance; it is not in the
        # generated __init__ signature, hence the ignore.
        _env_file=None,  # type: ignore[call-arg]
    )


@asynccontextmanager
async def running_app(
    env: EnvSettings,
    *,
    web_dist: Path | None = _UNSET,
    dotenv: Mapping[str, str | None] | None = None,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """Build an app, run its lifespan, and hand back a client speaking ASGI to it.

    ``ASGITransport`` calls the app in-process: no socket, no port to collide
    with another test, and a traceback that points at the failing handler.

    ``web_dist`` defaults to a directory that does not exist, so no test sees a
    mounted SPA by accident (see :data:`NO_WEB_DIST`). Pass a fixture directory
    to mount one, or ``None`` to exercise the real ``WEB_DIST``/default chain.

    ``dotenv`` defaults to empty rather than to parsing ``./.env``, so a file in
    the working directory cannot reach a test the way it reaches production.
    """
    resolved = NO_WEB_DIST if web_dist is _UNSET else web_dist
    app = create_app(env, web_dist=resolved, dotenv={} if dotenv is None else dotenv)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield app, client


@pytest.fixture
async def app_and_client(env: EnvSettings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """The default app plus its client."""
    async with running_app(env) as pair:
        yield pair


@pytest.fixture
async def client(
    app_and_client: tuple[FastAPI, httpx.AsyncClient],
) -> httpx.AsyncClient:
    """An HTTP client speaking to the app."""
    return app_and_client[1]


@pytest.fixture
async def app(app_and_client: tuple[FastAPI, httpx.AsyncClient]) -> FastAPI:
    """The app object, for reaching ``app.state``."""
    return app_and_client[0]


@pytest.fixture
def make_env(data_dir: Path) -> Callable[..., EnvSettings]:
    """Build an ``EnvSettings`` on the same temp data dir with overrides."""

    def factory(**overrides: object) -> EnvSettings:
        values: dict[str, object] = {
            "DATA_DIR": str(data_dir),
            "LOG_LEVEL": "warning",
        }
        values.update(overrides)
        return EnvSettings(_env_file=None, **values)  # type: ignore[call-arg,arg-type]

    return factory
