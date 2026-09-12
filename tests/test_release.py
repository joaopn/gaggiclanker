"""The release contract: the version, `.env.example`, and boot reconciliation.

Documentation rots silently. A setting added to the registry with no line in
`.env.example` is invisible to everyone who configures this from a file, and a
line in `.env.example` naming a variable nothing reads is worse — it looks like
it works. Both are one test.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI

from gaggiclanker import __version__
from gaggiclanker.db.repos.analyses import AnalysesRepository, AnalysisStart
from gaggiclanker.db.repos.sync import SyncRepository
from gaggiclanker.settings import SETTINGS_REGISTRY, EnvSettings
from tests.conftest import running_app

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"
PYPROJECT = REPO_ROOT / "pyproject.toml"
COMPOSE = REPO_ROOT / "compose.yml"

#: `NAME=value` at the start of a line, commented or not. Both count: a
#: documented variable is documented whether or not the example sets it.
_ASSIGNMENT = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)

#: Variables `.env.example` documents that are not registry keys. Each one is
#: read by something — the bootstrap layer, Compose itself, or the image — and
#: naming them here is what keeps the parity test honest rather than loose.
_NON_REGISTRY_KEYS: frozenset[str] = frozenset(
    {
        # EnvSettings (gaggiclanker/settings.py), read once at startup.
        "DATA_DIR",
        "LOG_LEVEL",
        "LOG_JSON",
        "HOST",
        "PORT",
        "WEB_DIST",
        "CORS_ORIGINS",
        "AUTH_PASSWORD",
        "AUTH_JWT_SECRET",
        # Read by compose.yml, not by the app.
        "HOST_PORT",
        "APP_UID",
        "APP_GID",
    }
)


def env_example_keys() -> set[str]:
    return set(_ASSIGNMENT.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


def registry_env_keys() -> set[str]:
    return {d.env_key for d in SETTINGS_REGISTRY.values() if d.env_key}


def test_every_registry_setting_is_documented_in_env_example() -> None:
    missing = sorted(registry_env_keys() - env_example_keys())
    assert not missing, f"settings with no line in .env.example: {missing}"


def test_env_example_documents_nothing_that_is_not_read() -> None:
    unknown = sorted(env_example_keys() - registry_env_keys() - _NON_REGISTRY_KEYS)
    assert not unknown, f".env.example names variables nothing reads: {unknown}"


def test_the_bootstrap_settings_are_all_documented() -> None:
    """`EnvSettings`' own aliases, which the registry test above cannot see."""
    documented = env_example_keys()
    for name, field in EnvSettings.model_fields.items():
        alias = field.validation_alias
        primary = alias.choices[0] if hasattr(alias, "choices") else name.upper()  # type: ignore[union-attr]
        assert str(primary) in documented, f"{name} is not in .env.example"


def test_the_version_is_the_release_and_agrees_everywhere() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == "0.1.0"
    # `__version__` comes from the installed distribution's metadata, so this
    # also catches a venv running an older build than the tree it is in.
    assert __version__ == "0.1.0"


def test_the_compose_image_is_tagged_with_the_version() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert compose["services"]["gaggiclanker"]["image"] == f"gaggiclanker:{__version__}"


def test_the_web_package_carries_the_same_version() -> None:
    import json

    package = json.loads((REPO_ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == __version__


async def test_health_reports_the_release_version(app: FastAPI) -> None:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        assert (await client.get("/health")).json()["data"]["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# Boot reconciliation
# ---------------------------------------------------------------------------


@pytest.fixture
async def interrupted(env: EnvSettings) -> EnvSettings:
    """A data directory left as a killed process would leave it."""
    async with running_app(env) as (app, _client):
        db = app.state.db
        await db.execute("INSERT INTO machines (host, name) VALUES ('sim', 'test')")
        machine_id = int(await db.fetch_value("SELECT id FROM machines") or 0)
        await db.execute(
            """
            INSERT INTO shots (device_id, machine_id, started_at, raw_slog)
            VALUES (?, ?, ?, ?)
            """,
            ("000001", machine_id, "2026-01-01T00:00:00.000Z", b"SHOT"),
        )
        shot_id = int(await db.fetch_value("SELECT id FROM shots") or 0)
        # Through the repository, so the row is exactly the shape a killed
        # process would have left behind rather than a hand-written guess at it.
        await AnalysesRepository(db).start(
            AnalysisStart(shot_id=shot_id, provider="fake", model="fake")
        )
        await SyncRepository(db).start_run("backfill", trigger="test")
    return env


async def test_a_running_analysis_is_marked_interrupted_at_the_next_boot(
    interrupted: EnvSettings,
) -> None:
    async with running_app(interrupted) as (app, _client):
        row = await app.state.db.fetch_one("SELECT * FROM shot_analyses")
        assert row["status"] == "interrupted"
        assert row["error"]
        assert row["finished_at"]


async def test_a_running_sync_run_is_closed_at_the_next_boot(
    interrupted: EnvSettings,
) -> None:
    """Otherwise `GET /api/sync/status` shows a backfill running since Tuesday."""
    async with running_app(interrupted) as (app, _client):
        run = (await SyncRepository(app.state.db).recent_runs(limit=5))[0]
        assert run.status == "error"
        assert run.finished_at
        assert run.error and "stopped before" in run.error


async def test_reconciliation_is_idempotent(interrupted: EnvSettings) -> None:
    async with running_app(interrupted) as (app, _client):
        db = app.state.db
        assert await AnalysesRepository(db).reconcile_running() == 0
        assert await SyncRepository(db).reconcile_running() == 0


async def test_the_boot_summary_is_logged(
    interrupted: EnvSettings,
    make_env: Callable[..., EnvSettings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One line, every boot, so "what did the last restart interrupt" has an answer.

    ``LOG_LEVEL=info`` on the env rather than a call to ``configure_logging``:
    ``create_app`` configures logging from the env it is given, so a level set
    from outside is overwritten the moment the app is built.
    """
    async with running_app(make_env(LOG_LEVEL="info")):
        pass
    lines = [x for x in capsys.readouterr().out.splitlines() if "boot_reconciled" in x]
    assert len(lines) == 1, lines
    assert '"analyses_interrupted": 1' in lines[0]
    assert '"sync_runs_interrupted": 1' in lines[0]
    assert '"auth_enabled": false' in lines[0]
