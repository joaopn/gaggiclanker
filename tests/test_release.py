"""The release contract: the version, the configuration surface, and boot reconciliation.

There is one place to configure this application — the Settings page, backed by
the database — and a handful of variables the process reads before there is a
database to read. Documentation rots silently, so both halves are pinned here: a
bootstrap variable with no line in the README is invisible to whoever deploys
this, and a tracked file of variables would be a second surface that looks like
it works and does not. A clean checkout must boot on its defaults with nothing
copied or edited first.
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
from gaggiclanker.settings import (
    DEVICE_WRITES_ENV_KEY,
    FORMER_SETTING_ENV_KEYS,
    RETIRED_AUTH_ENV_KEYS,
    SETTINGS_REGISTRY,
    EnvSettings,
    SettingDefinition,
)
from tests.conftest import running_app

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
COMPOSE = REPO_ROOT / "compose.yml"

#: The declared bind port, and the only number any of the files below may use
#: for it. Read from the model rather than instantiated, so the test says what
#: the code ships rather than what this process happens to have in `PORT`.
DEFAULT_PORT = EnvSettings.model_fields["port"].default


def bootstrap_variable_names() -> list[str]:
    """The primary name of every :class:`EnvSettings` field."""
    names = []
    for name, field in EnvSettings.model_fields.items():
        alias = field.validation_alias
        names.append(str(alias.choices[0]) if hasattr(alias, "choices") else name.upper())  # type: ignore[union-attr]
    return names


def test_no_setting_is_configured_from_the_environment() -> None:
    """The registry declares values, never variable names.

    A setting that could name one would bring back the second configuration
    surface this release removed: a compose file that disagrees with the
    Settings page, silently, with no way for the person looking at either to
    tell which one the app is running on.
    """
    assert "env_key" not in SettingDefinition.__slots__
    assert SETTINGS_REGISTRY, "an empty registry would make this test vacuous"


def test_the_bootstrap_variables_are_documented_in_the_readme() -> None:
    """The seven the process reads before it can open the database.

    They are documented in the README's Configuration section rather than in a
    file to copy, because a tracked file of variables is what this release got
    rid of.
    """
    readme = README.read_text(encoding="utf-8")
    for name in bootstrap_variable_names():
        assert name in readme, f"{name} is not documented in README.md"


def test_the_repository_ships_no_env_file() -> None:
    """A checkout has nothing to copy, edit, or conflict with on the next pull."""
    assert not (REPO_ROOT / ".env").exists()
    assert not (REPO_ROOT / ".env.example").exists()


def test_an_env_file_is_ignored_by_git_wherever_it_is() -> None:
    import subprocess

    def ignored(path: str) -> bool:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "check-ignore", "-q", path],  # noqa: S607 - git from PATH is the point
            cwd=REPO_ROOT,
            check=False,
        )
        return result.returncode == 0

    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert ignored(".env")
    assert ignored("web/.env")
    assert ignored(".envrc")


def test_compose_passes_the_bind_port_and_otherwise_only_a_tripwire() -> None:
    """One setting, and the names a boot refuses. Nothing else.

    `PORT` is the setting, and the only one that cannot be a row in the
    database: the process binds a socket before it can open a file. So it is
    given at spawn, carries the same default the code declares, and is
    remembered nowhere.

    The rest is a tripwire. The app reads no `.env`, but Compose still
    substitutes `${VAR}` from one in the project directory as well as from the
    invoking shell. Forwarding exactly the names
    :func:`~gaggiclanker.main.check_configuration` refuses means a box upgrading
    from the era of that file cannot come up with the sign-in it used to
    configure silently off, or with the retired device-writes switch silently
    ignored — while nothing else in a stale file reaches the container, so a
    leftover `DATA_DIR` in it does not quietly take effect.

    Pinned in both directions. Short of the refusal list, a name stops tripping
    and an upgrade goes quiet. Beyond it, `compose.yml` becomes the second place
    to configure this application that the database was meant to end — so a
    registry key, or a bootstrap variable other than `PORT`, fails this test.
    Every tripwire entry is `${NAME:-}`: it forwards, it carries no value of its
    own (a credential written in here would be a credential in a file), and an
    unset variable substitutes to empty, which counts as unset, so a clean box
    boots.
    """
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["gaggiclanker"]

    # No `env_file`: that would let everything in a stale file through, not just
    # the names worth refusing.
    assert "env_file" not in service

    forwarded: dict[str, str] = service["environment"]
    assert set(forwarded) == {"PORT", *RETIRED_AUTH_ENV_KEYS, DEVICE_WRITES_ENV_KEY}

    # The one real setting, defaulted from the same number the code declares.
    assert forwarded["PORT"] == f"${{PORT:-{DEFAULT_PORT}}}"

    tripwire = {name: value for name, value in forwarded.items() if name != "PORT"}
    assert set(tripwire) == {*RETIRED_AUTH_ENV_KEYS, DEVICE_WRITES_ENV_KEY}
    assert all(value == f"${{{name}:-}}" for name, value in tripwire.items()), tripwire
    assert set(forwarded).isdisjoint(FORMER_SETTING_ENV_KEYS)
    assert set(forwarded) & set(bootstrap_variable_names()) == {"PORT"}


def test_the_bind_port_default_is_the_same_number_everywhere() -> None:
    """One default, in four files that cannot import each other.

    The declaration in `EnvSettings` is the source; the image bakes it in, its
    healthcheck falls back to it, and Compose's healthcheck and bridge mapping
    repeat it. A mix would show up as a container reported unhealthy while it
    served perfectly well on another port, which is a bad afternoon.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose_text = COMPOSE.read_text(encoding="utf-8")

    assert f"PORT={DEFAULT_PORT}" in dockerfile
    assert f"EXPOSE {DEFAULT_PORT}" in dockerfile
    assert f"'PORT','{DEFAULT_PORT}'" in dockerfile
    assert f"'PORT','{DEFAULT_PORT}'" in compose_text
    assert f'"${{HOST_PORT:-{DEFAULT_PORT}}}:{DEFAULT_PORT}"' in compose_text


def test_compose_configures_no_setting_anywhere_in_the_file() -> None:
    """A former setting variable assigned here would do nothing, which is worse than absent.

    Assignments, not mentions: the prose in that file legitimately names the
    variables a boot refuses, which is how somebody reading it learns why.
    """
    text = COMPOSE.read_text(encoding="utf-8")
    assigned = set(re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*[:=]", text, re.MULTILINE))
    forbidden = assigned & set(FORMER_SETTING_ENV_KEYS)
    assert not forbidden, f"compose.yml sets retired setting variables: {sorted(forbidden)}"


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
        await db.execute("UPDATE machines SET host = 'sim', name = 'test'")
        await db.execute(
            "INSERT INTO shots (device_id, started_at, raw_slog) VALUES (?, ?, ?)",
            ("000001", "2026-01-01T00:00:00.000Z", b"SHOT"),
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
