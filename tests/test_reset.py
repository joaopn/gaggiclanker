"""``POST /api/reset`` and the boot that finishes it.

The real path: an app is booted on a populated data directory, the route is called, the
lifespan ends, and a new app boots on the same directory. Only the process signal is
replaced.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.auth.passwords import hash_password
from gaggiclanker.db import reset as reset_module
from gaggiclanker.db.reset import RESET_MARKER, complete_reset
from gaggiclanker.db.restore import RESTORE_MARKER, STAGING_GLOB
from gaggiclanker.llm.claude_cli import INSTALL_DIR
from tests.auth.conftest import PASSWORD, USERNAME, enable_auth, sign_in
from tests.conftest import running_app
from tests.test_restore_apply import env_in, no_signal, populate
from tests.test_restore_check import ROUTE as RESTORE_ROUTE
from tests.test_restore_check import staged_files, upload

ROUTE = "/api/reset"
BODY = {"confirm": "reset"}


def listing(data_dir: Path) -> set[str]:
    return {p.name for p in data_dir.iterdir()}


def plant_files(data_dir: Path) -> None:
    """Every kind of file the app writes under the data directory, besides the database."""
    (data_dir / "restore-staging-aaaa.db").write_bytes(b"staged")
    (data_dir / "restore-staging-aaaa.db-wal").write_bytes(b"staged wal")
    export = data_dir / "backup-export-xyz"
    export.mkdir()
    (export / "backup.db").write_bytes(b"a copy with keys")
    program = data_dir / INSTALL_DIR / "2.1.0"
    program.mkdir(parents=True)
    (program / "claude").write_bytes(b"#!/bin/sh\n")
    (program / "claude").chmod(0o755)
    (data_dir / INSTALL_DIR / "current").write_text("2.1.0\n")
    (data_dir / INSTALL_DIR / ".staging-2.1.1-1").mkdir()
    (data_dir / RESTORE_MARKER).write_text("{}")


def table_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        names = [
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts: dict[str, int] = {}
        for name in names:
            try:
                counts[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]  # noqa: S608
            except sqlite3.OperationalError:
                continue  # a virtual table whose module is not loaded here
        return counts
    finally:
        conn.close()


async def test_a_reset_leaves_only_a_fresh_database(tmp_path: Path) -> None:
    fresh_env = env_in(tmp_path / "fresh")
    async with running_app(fresh_env):
        pass
    fresh_counts = table_counts(fresh_env.database_path)
    fresh_settings = (
        sqlite3.connect(fresh_env.database_path)
        .execute("SELECT key, value FROM settings ORDER BY key")
        .fetchall()
    )

    env = env_in(tmp_path / "used")
    async with running_app(env) as (app, client):
        signals = no_signal(app)
        await populate(app)
        await app.state.db.execute(
            "INSERT INTO settings (key, value) VALUES ('anthropicApiKey', 'sk-secret')"
        )
        plant_files(env.data_dir)
        response = await client.post(ROUTE, json=BODY)
        assert response.status_code == 202, response.text
        assert response.json()["data"] == {"restarting": True}
        assert signals == ["SIGTERM"]
    # The route only committed: the shutdown deleted nothing.
    assert (env.data_dir / RESET_MARKER).exists()
    assert env.database_path.exists()

    async with running_app(env) as (booted, client):
        status = (await client.get("/api/auth/status")).json()["data"]
        assert status["auth_required"] is False
        assert await booted.state.db.fetch_value("SELECT COUNT(*) FROM shots") == 0
        assert (await client.get("/api/settings")).status_code == 200
    assert listing(env.data_dir) == {"gaggiclanker.db"}
    assert table_counts(env.database_path) == fresh_counts
    conn = sqlite3.connect(env.database_path)
    assert conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall() == fresh_settings
    assert conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge_docs").fetchone()[0] > 0


async def test_a_reset_removes_the_sign_in_the_keys_and_the_sessions(tmp_path: Path) -> None:
    hashed = hash_password(PASSWORD)
    env = env_in(tmp_path / "secured")
    async with running_app(env) as (app, client):
        no_signal(app)
        await enable_auth(app, hashed)
        await app.state.db.execute(
            "INSERT INTO settings (key, value) VALUES ('anthropicApiKey', 'sk-secret')"
        )
        login = await sign_in(client, USERNAME, PASSWORD)
        headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
        assert await app.state.db.fetch_value("SELECT COUNT(*) FROM auth_sessions") == 1
        # Sign-in is on: the route needs the token.
        assert (await client.post(ROUTE, json=BODY)).status_code == 401
        assert (await client.post(ROUTE, json=BODY, headers=headers)).status_code == 202
    async with running_app(env) as (booted, client):
        assert (await client.get("/api/auth/status")).json()["data"]["auth_required"] is False
        stored = dict(await booted.state.db.fetch_all("SELECT key, value FROM settings"))
        assert "anthropicApiKey" not in stored or stored["anthropicApiKey"] == ""
        assert await booted.state.db.fetch_value("SELECT COUNT(*) FROM auth_sessions") == 0
        assert (
            await booted.state.db.fetch_value(
                "SELECT COUNT(*) FROM settings WHERE key = 'authUser'"
            )
            == 0
        )


async def test_a_marker_at_boot_with_the_database_in_place_finishes_the_reset(
    tmp_path: Path,
) -> None:
    """The kill came after the 202 and before anything was deleted."""
    env = env_in(tmp_path / "crashed")
    async with running_app(env) as (app, _client):
        await populate(app)
    plant_files(env.data_dir)
    (env.data_dir / "gaggiclanker.db-wal").write_bytes(b"an old log")
    (env.data_dir / RESET_MARKER).write_text("reset\n")

    async with running_app(env) as (booted, _client):
        assert await booted.state.db.fetch_value("SELECT COUNT(*) FROM shots") == 0

    assert listing(env.data_dir) == {"gaggiclanker.db"}


async def test_a_half_finished_deletion_is_finished_by_the_next_boot(tmp_path: Path) -> None:
    """The database is gone but the rest, and the marker, are still there."""
    env = env_in(tmp_path / "half")
    plant_files(env.data_dir)
    for name in ("", "-wal", "-shm", "-journal"):
        (env.data_dir / f"gaggiclanker.db{name}").write_bytes(b"old")
    (env.data_dir / RESET_MARKER).write_text("reset\n")

    assert complete_reset(env.data_dir) is True

    assert listing(env.data_dir) == set()


async def test_a_boot_without_a_marker_deletes_nothing(tmp_path: Path) -> None:
    env = env_in(tmp_path / "plain")
    async with running_app(env) as (app, _client):
        await populate(app)
    before = table_counts(env.database_path)
    program = env.data_dir / INSTALL_DIR
    program.mkdir()
    (program / "current").write_text("2.1.0\n")
    (env.data_dir / "notes.txt").write_text("not the app's")

    assert complete_reset(env.data_dir) is False
    assert (program / "current").exists()
    async with running_app(env):
        pass

    assert (env.data_dir / "notes.txt").exists()
    assert table_counts(env.database_path) == before


def test_the_marker_goes_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crash while deleting leaves the marker, so the next boot repeats the reset."""
    (tmp_path / "gaggiclanker.db").write_bytes(b"db")
    (tmp_path / RESET_MARKER).write_text("reset\n")
    (tmp_path / INSTALL_DIR).mkdir()

    def boom(path: str) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr("shutil.rmtree", boom)
    with pytest.raises(OSError, match="disk went away"):
        complete_reset(tmp_path)

    assert (tmp_path / RESET_MARKER).exists()
    monkeypatch.undo()
    assert complete_reset(tmp_path) is True
    assert listing(tmp_path) == set()


async def test_the_marker_is_on_disk_before_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = env_in(tmp_path / "ordered")
    async with running_app(env) as (app, client):
        events: list[str] = []
        real = reset_module.write_reset_marker

        def marked(data_dir: Path) -> None:
            real(data_dir)
            events.append("marker")

        monkeypatch.setattr("gaggiclanker.api.reset.write_reset_marker", marked)
        app.state.terminate_process = lambda: events.append("signal")

        response = await client.post(ROUTE, json=BODY)

        assert response.status_code == 202
        assert events == ["marker", "signal"]
        assert (env.data_dir / RESET_MARKER).exists()


def _break_file_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(_fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("os.fsync", failing)


def _break_directory_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(_path: Path, *, directory: bool = False) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(reset_module, "_fsync", failing)


def _break_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(_src: object, _dst: object) -> None:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr("os.replace", failing)


def test_the_final_name_appears_only_after_the_marker_is_flushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A process killed mid-write cannot be caused by a test, so the order is pinned
    # instead: while the file is being flushed the final name must not exist yet, and
    # the rename comes after that flush. Otherwise a death in between leaves a marker
    # the person was never told about.
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def spying_fsync(fd: int) -> None:
        if not events:
            assert not (tmp_path / RESET_MARKER).exists()
        events.append("fsync")
        real_fsync(fd)

    def spying_replace(src: Any, dst: Any) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr("os.fsync", spying_fsync)
    monkeypatch.setattr("os.replace", spying_replace)
    reset_module.write_reset_marker(tmp_path)
    assert events[:2] == ["fsync", "replace"]
    assert (tmp_path / RESET_MARKER).exists()


@pytest.mark.parametrize(
    "break_it",
    [_break_file_fsync, _break_directory_fsync, _break_replace],
    ids=lambda f: f.__name__,
)
async def test_a_failed_marker_write_leaves_no_marker_and_the_data_survives_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, break_it: Callable[[pytest.MonkeyPatch], None]
) -> None:
    env = env_in(tmp_path / "unwritable")
    async with running_app(env) as (app, client):
        signals = no_signal(app)
        await populate(app)
        shots = await app.state.db.fetch_value("SELECT COUNT(*) FROM shots")
        assert shots > 0
        break_it(monkeypatch)

        response = await client.post(ROUTE, json=BODY)
        monkeypatch.undo()

        assert response.status_code == 500
        assert "Nothing was changed" in response.json()["error"]["message"]
        assert signals == []
        assert app.state.reset_pending is False
        assert not (env.data_dir / RESET_MARKER).exists()
        assert not (env.data_dir / f"{RESET_MARKER}.tmp").exists()
        # And the app is not stuck: a later attempt works (and is undone for the next check).
        assert (await client.post(ROUTE, json=BODY)).status_code == 202
        (env.data_dir / RESET_MARKER).unlink()
    # An ordinary restart afterwards keeps everything.
    async with running_app(env) as (booted, _client):
        assert await booted.state.db.fetch_value("SELECT COUNT(*) FROM shots") == shots


async def test_a_request_cancelled_around_the_marker_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Cancelled(BaseException):
        """Stands for a cancellation: not an Exception, so the route's first clause misses it."""

    env = env_in(tmp_path / "cancelled")
    async with running_app(env) as (app, client):
        signals = no_signal(app)

        def interrupted(data_dir: Path) -> None:
            (data_dir / RESET_MARKER).write_text("reset\n")
            raise Cancelled

        monkeypatch.setattr("gaggiclanker.api.reset.write_reset_marker", interrupted)
        with pytest.raises(Cancelled):
            await client.post(ROUTE, json=BODY)

        assert app.state.reset_pending is False
        assert signals == []
        monkeypatch.undo()
        assert (await client.post(ROUTE, json=BODY)).status_code == 202


async def test_a_marker_written_by_a_failed_write_is_removed_by_the_write_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _break_directory_fsync(monkeypatch)
    with pytest.raises(OSError, match="Input/output"):
        reset_module.write_reset_marker(tmp_path)
    assert listing(tmp_path) == set()


async def test_a_leftover_temporary_marker_never_counts(tmp_path: Path) -> None:
    env = env_in(tmp_path / "stale-tmp")
    async with running_app(env) as (app, _client):
        await populate(app)
    (env.data_dir / f"{RESET_MARKER}.tmp").write_text("reset\n")

    async with running_app(env) as (booted, _client):
        assert await booted.state.db.fetch_value("SELECT COUNT(*) FROM shots") > 0

    assert not (env.data_dir / f"{RESET_MARKER}.tmp").exists()


def test_the_reset_deletes_the_directory_the_claude_cli_installs_into(tmp_path: Path) -> None:
    from gaggiclanker.llm.claude_cli import ClaudeCliManager

    root = ClaudeCliManager(data_dir=str(tmp_path)).root
    (root / "2.1.0").mkdir(parents=True)
    (root / "2.1.0" / "claude").write_bytes(b"x")
    (tmp_path / RESET_MARKER).write_text("reset\n")

    complete_reset(tmp_path)

    assert not root.exists()
    assert listing(tmp_path) == set()


@pytest.mark.parametrize(
    "body", [None, {}, {"confirm": "yes"}, {"confirm": "RESET"}, {"confirm": "reset", "x": 1}]
)
async def test_a_missing_or_wrong_confirm_is_rejected_and_writes_nothing(
    tmp_path: Path, body: dict[str, Any] | None
) -> None:
    env = env_in(tmp_path / "stray")
    async with running_app(env) as (app, client):
        signals = no_signal(app)
        response = (
            await client.post(ROUTE, json=body) if body is not None else await client.post(ROUTE)
        )

        # The app answers every body that fails validation with 400 and its envelope.
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST"
        assert signals == []
        assert app.state.reset_pending is False
        assert not (env.data_dir / RESET_MARKER).exists()


async def test_a_task_in_flight_refuses_the_reset(tmp_path: Path) -> None:
    env = env_in(tmp_path / "busy-task")
    async with running_app(env) as (app, client):
        signals = no_signal(app)
        gate = asyncio.Event()
        app.state.tasks.spawn("review-1", gate.wait())

        response = await client.post(ROUTE, json=BODY)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RESET_BUSY"
        assert "running" in response.json()["error"]["message"]
        assert signals == [] and app.state.reset_pending is False
        assert not (env.data_dir / RESET_MARKER).exists()
        gate.set()


async def test_a_sync_pass_refuses_the_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = env_in(tmp_path / "busy-sync")
    async with running_app(env) as (app, client):
        signals = no_signal(app)
        monkeypatch.setattr(app.state.connection, "busy", lambda: "a sync")

        response = await client.post(ROUTE, json=BODY)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RESET_BUSY"
        assert signals == [] and not (env.data_dir / RESET_MARKER).exists()


async def test_a_claude_code_install_refuses_the_reset(tmp_path: Path) -> None:
    env = env_in(tmp_path / "busy-install")
    async with running_app(env) as (app, client):
        signals = no_signal(app)
        app.state.claude_cli.begin("stable")

        response = await client.post(ROUTE, json=BODY)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RESET_BUSY"
        assert signals == [] and not (env.data_dir / RESET_MARKER).exists()


async def test_two_resets_at_once_give_one_restart(tmp_path: Path) -> None:
    env = env_in(tmp_path / "double")
    async with running_app(env) as (app, client):
        signals = no_signal(app)

        first, second = await asyncio.gather(
            client.post(ROUTE, json=BODY), client.post(ROUTE, json=BODY)
        )

        assert sorted([first.status_code, second.status_code]) == [202, 409]
        loser = first if first.status_code == 409 else second
        assert loser.json()["error"]["code"] == "RESET_PENDING"
        assert signals == ["SIGTERM"]


async def test_a_reset_is_refused_while_a_restore_is_pending(tmp_path: Path) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = (await client.get("/api/backup")).content
    env = env_in(tmp_path / "target")
    async with running_app(env) as (app, client):
        signals = no_signal(app)
        token = (await upload(client, content)).json()["data"]["token"]
        assert (await client.post(f"{RESTORE_ROUTE}/{token}/apply")).status_code == 202

        response = await client.post(ROUTE, json=BODY)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RESTORE_PENDING"
        assert signals == ["SIGTERM"]
        assert not (env.data_dir / RESET_MARKER).exists()


async def test_restore_routes_are_refused_while_a_reset_is_pending(tmp_path: Path) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = (await client.get("/api/backup")).content
    env = env_in(tmp_path / "target")
    async with running_app(env) as (app, client):
        no_signal(app)
        token = (await upload(client, content)).json()["data"]["token"]
        assert (await client.post(ROUTE, json=BODY)).status_code == 202

        apply = await client.post(f"{RESTORE_ROUTE}/{token}/apply")
        cancel = await client.delete(f"{RESTORE_ROUTE}/{token}")
        again = await upload(client, content)

        for refused in (apply, cancel, again):
            assert refused.status_code == 409
            assert refused.json()["error"]["code"] == "RESET_PENDING"
        # Nothing the reset needs was touched: the staged file is still there for the boot.
        assert len(staged_files(env.data_dir)) == 1
        assert app.state.restore_pending is None


async def test_the_signal_is_sent_only_after_the_response_is_on_the_wire(tmp_path: Path) -> None:
    async with running_app(env_in(tmp_path / "wire")) as (app, _client):
        events: list[str] = []
        app.state.terminate_process = lambda: events.append("signal")
        raw = b'{"confirm": "reset"}'

        async def receive() -> MutableMapping[str, Any]:
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                events.append(f"start {message['status']}")
            elif message["type"] == "http.response.body" and not message.get("more_body"):
                events.append("body end")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "path": ROUTE,
            "raw_path": ROUTE.encode(),
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
            ],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
            "app": app,
        }
        await app(scope, receive, send)

    assert events == ["start 202", "body end", "signal"]


async def test_a_restore_then_a_reset_leaves_no_staging_file(tmp_path: Path) -> None:
    """A staged upload that was never applied does not survive the reset either."""
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = (await client.get("/api/backup")).content
    env = env_in(tmp_path / "target")
    async with running_app(env) as (app, client):
        no_signal(app)
        await upload(client, content)
        assert len(list(env.data_dir.glob(STAGING_GLOB))) == 1
        # Cancel is not called: the file is left for the reset to remove.
        assert (await client.post(ROUTE, json=BODY)).status_code == 202
    async with running_app(env):
        pass
    assert listing(env.data_dir) == {"gaggiclanker.db"}
