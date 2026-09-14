"""Startup preconditions: the data directory, the config checks, and failing loudly.

The bind-mount permission failure this guards against is the most common way a
first `docker compose up` fails, and SQLite's own message for it ("unable to
open database file") sends people looking for a corrupt database instead of a
directory owned by root.

The second half of this file is about what a *failed* startup leaves behind.
aiosqlite's worker is a plain `threading.Thread` with no `daemon=True`, so a
connection that is opened and never closed leaks a live thread; the only thing
that stops it is our own `close()`. Whether such a thread then holds the
interpreter open at `sys.exit` comes down to a `__del__` in aiosqlite's
`Connection` running during garbage collection — it does on 0.22.1, so the
process does still exit today, and that is not a thing to depend on. A container
that logged "Application startup failed" and then hung would never exit
non-zero, `restart: unless-stopped` would never fire, and a typo in a compose
file would become a box that is simply dead rather than one that restart-loops
with the reason in its log.

Two rules follow, and both are tested here: a check that needs only the
environment runs before anything is opened, and anything that raises after
`connect()` still closes the database on its way out.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from gaggiclanker.main import check_configuration, create_app, ensure_data_dir
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app


def aiosqlite_threads() -> list[threading.Thread]:
    """Live aiosqlite worker threads. The thing that must not be left behind.

    Identified by what they run, not by their name: aiosqlite constructs the
    worker as a bare ``Thread(target=_connection_worker_thread)``, so the name
    is whatever CPython's counter produced. ``_target`` is private, which is why
    :func:`test_the_thread_probe_actually_sees_them` exists — if a future
    aiosqlite renames it, that test fails rather than every assertion below
    quietly passing against an empty list.
    """
    found: list[threading.Thread] = []
    for thread in threading.enumerate():
        target = getattr(thread, "_target", None)
        module = getattr(target, "__module__", "") or ""
        if thread.is_alive() and module.startswith("aiosqlite"):
            found.append(thread)
    return found


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


# ---------------------------------------------------------------------------
# A failed startup has to leave nothing running
# ---------------------------------------------------------------------------


@pytest.fixture
def no_stray_threads() -> Iterator[None]:
    """Fail the test if it leaks an aiosqlite worker, whatever else it asserts."""
    before = {thread.ident for thread in aiosqlite_threads()}
    yield
    started = [thread for thread in aiosqlite_threads() if thread.ident not in before]
    # A closed connection's worker can exit *after* `close()` returns: the
    # worker hands the stop's result back to the event loop and only then leaves
    # its loop, and nothing joins it. On a busy machine (the suite runs in
    # parallel) the thread could still be winding down here, which read as a
    # leak. A worker that was never closed blocks on its queue for ever, so a
    # bounded join still catches the real thing.
    for thread in started:
        thread.join(timeout=5)
    leaked = [thread for thread in started if thread.is_alive()]
    assert not leaked, [thread.name for thread in leaked]


def test_the_thread_probe_actually_sees_them(env: EnvSettings) -> None:
    """Guard against the assertions below passing because the probe finds nothing."""
    import asyncio

    from gaggiclanker.db.connection import Database

    async def open_and_look() -> int:
        db = Database(env.database_path)
        await db.connect()
        try:
            return len(aiosqlite_threads())
        finally:
            await db.close()

    assert asyncio.run(open_and_look()) >= 1


async def test_a_failure_after_connect_closes_the_database_and_propagates(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch, no_stray_threads: None
) -> None:
    """The migration runner is the realistic one: a bad file, a checksum mismatch."""
    from gaggiclanker import main as main_module

    boom = RuntimeError("migration 0007_auth failed: no such column")

    async def explode(*_args: Any, **_kwargs: Any) -> None:
        raise boom

    monkeypatch.setattr(main_module, "run_migrations", explode)

    with pytest.raises(RuntimeError, match="migration 0007_auth failed"):
        async with running_app(env):
            pass

    # The `no_stray_threads` fixture is the real assertion: without the teardown
    # on the failure path, the worker aiosqlite started in `connect()` is still
    # alive here, and it is not a daemon.


async def test_a_failure_late_in_startup_still_closes_the_database(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch, no_stray_threads: None
) -> None:
    """Half the services built, and the database still has to be released."""
    from gaggiclanker import main as main_module

    async def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the device client would not start")

    monkeypatch.setattr(main_module, "start_device_client", explode)

    with pytest.raises(RuntimeError, match="device client"):
        async with running_app(env):
            pass


async def test_a_startup_failure_is_logged_with_its_cause(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from gaggiclanker import main as main_module
    from gaggiclanker.infra.logging import configure_logging

    async def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("something specific went wrong")

    monkeypatch.setattr(main_module, "run_migrations", explode)
    configure_logging("info", json_output=True)
    try:
        with pytest.raises(RuntimeError):
            async with running_app(env):
                pass
        logged = capsys.readouterr().out
    finally:
        configure_logging("warning", json_output=True)
    assert "app_startup_failed" in logged
    assert "something specific went wrong" in logged


# ---------------------------------------------------------------------------
# ... and a pure-config failure must not open anything in the first place
# ---------------------------------------------------------------------------


def test_a_retired_sign_in_variable_is_refused_by_the_config_check() -> None:
    env = EnvSettings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="AUTH_USER"):
        check_configuration(env, dotenv={}, environ={"AUTH_USER": "barista"})


def test_no_sign_in_variable_passes_the_config_check() -> None:
    check_configuration(EnvSettings(_env_file=None), dotenv={}, environ={})  # type: ignore[call-arg]


async def test_a_retired_sign_in_variable_fails_before_the_database_is_opened(
    make_env: Callable[..., EnvSettings],
    monkeypatch: pytest.MonkeyPatch,
    no_stray_threads: None,
) -> None:
    """Not merely "it fails" — it fails without leaving a thread to hang on.

    `connect` is replaced by something that shouts, so the test is about the
    *order* rather than about whether the cleanup happened to work.
    """
    from gaggiclanker.db.connection import Database

    async def must_not_run(self: Database) -> None:
        raise AssertionError("the database was opened before the config was checked")

    monkeypatch.setattr(Database, "connect", must_not_run)
    monkeypatch.setenv("AUTH_PASSWORD", "a-password-from-an-old-compose-file")

    with pytest.raises(RuntimeError, match="AUTH_PASSWORD"):
        async with running_app(make_env()):
            pass
