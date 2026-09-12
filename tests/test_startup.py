"""Startup preconditions: the data directory, and what happens when it is wrong.

The bind-mount permission failure this guards against is the most common way a
first `docker compose up` fails, and SQLite's own message for it ("unable to
open database file") sends people looking for a corrupt database instead of a
directory owned by root.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from gaggiclanker.main import create_app, ensure_data_dir
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app


def test_ensure_data_dir_creates_it(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "data"
    ensure_data_dir(target)
    assert target.is_dir()
    assert not (target / ".write-test").exists()


def test_ensure_data_dir_is_idempotent(tmp_path: Path) -> None:
    ensure_data_dir(tmp_path / "data")
    ensure_data_dir(tmp_path / "data")


@pytest.mark.skipif(os.getuid() == 0, reason="root can write anything")
def test_unwritable_data_dir_fails_with_an_actionable_message(tmp_path: Path) -> None:
    """The message has to name the path, the uid and the fix."""
    target = tmp_path / "data"
    target.mkdir()
    target.chmod(0o555)
    try:
        with pytest.raises(RuntimeError) as caught:
            ensure_data_dir(target)
    finally:
        target.chmod(0o755)

    message = str(caught.value)
    assert str(target) in message
    assert f"uid {os.getuid()}" in message
    assert "chown" in message


@pytest.mark.skipif(os.getuid() == 0, reason="root can write anything")
async def test_app_refuses_to_start_on_an_unwritable_data_dir(tmp_path: Path) -> None:
    """Fail fast at boot, not on the first request that touches the database."""
    target = tmp_path / "data"
    target.mkdir()
    target.chmod(0o555)
    app = create_app(EnvSettings(DATA_DIR=str(target), _env_file=None), dotenv={})  # type: ignore[call-arg]
    try:
        with pytest.raises(RuntimeError, match="not writable"):
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover - the lifespan raises before this runs
    finally:
        target.chmod(0o755)


async def test_health_reports_ok_when_the_database_is_reachable(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"] == {
        "status": "ok",
        "version": body["data"]["version"],
        "database": "ok",
    }


async def test_health_is_not_ok_when_the_database_is_gone(env: EnvSettings) -> None:
    """A 503 must not arrive wrapped in ok:true — probes and the UI read `ok`."""
    async with running_app(env) as (app, client):
        await app.state.db.close()

        response = await client.get("/health")
        assert response.status_code == 503
        body = response.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert body["error"]["details"]["database"] == "unavailable"
        assert body["meta"]["request_id"]
