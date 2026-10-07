"""``POST /api/backup/restore/{token}/apply`` and the swap in the lifespan.

The real path throughout: an app is booted on one data directory, a file is
uploaded and applied, the app's lifespan ends (which is where the swap runs), and
a new app boots on the same directory. Only the process signal is replaced.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.backup import KEY_SETTING_KEYS, create_export
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations
from gaggiclanker.db.restore import RESTORE_MARKER, STAGING_GLOB, swap_in
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app
from tests.starting.conftest import build_fixture
from tests.test_restore_check import ROUTE, staged_files, upload

HASH = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$aGFzaGhhc2hoYXNoaGFzaGhhc2g"


def env_in(path: Path, level: str = "warning") -> EnvSettings:
    path.mkdir(parents=True, exist_ok=True)
    return EnvSettings(DATA_DIR=str(path), LOG_LEVEL=level, LOG_JSON=True)  # type: ignore[call-arg]


def no_signal(app: FastAPI) -> list[str]:
    """Replace the process signal; the list records each time it would have been sent."""
    sent: list[str] = []
    app.state.terminate_process = lambda: sent.append("SIGTERM")
    return sent


def table_hashes(path: Path, *, skip: set[str] = frozenset()) -> dict[str, str]:  # type: ignore[assignment]
    """A content hash per table, in rowid order, of everything a person could have stored."""
    conn = sqlite3.connect(path)
    try:
        names = [
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        hashes: dict[str, str] = {}
        for name in names:
            if name in skip:
                continue
            try:
                rows = conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()  # noqa: S608
            except sqlite3.OperationalError:
                continue  # a virtual table's shadow that cannot be read by rowid
            hashes[name] = hashlib.sha256(repr(rows).encode()).hexdigest()
        return hashes
    finally:
        conn.close()


async def populate(app: FastAPI) -> None:
    """Every kind of thing the archive holds, through the same repositories the app uses."""
    db: Database = app.state.db
    fixture = await build_fixture(db, seed_knowledge=True)
    await db.execute(
        "UPDATE prompts SET content = content || ' (edited)' "
        "WHERE name = (SELECT MIN(name) FROM prompts)"
    )
    await db.execute(
        "UPDATE knowledge_docs SET body = body || ' (edited)' "
        "WHERE id = (SELECT MIN(id) FROM knowledge_docs)"
    )
    await db.execute(
        "INSERT INTO knowledge_insights (text, source, confirmed) "
        "VALUES ('Confirmed fact', 'user', 1)"
    )
    await db.execute(
        "INSERT INTO device_writes (kind, host, result) "
        "VALUES ('profile_save', 'kitchen.local', 'ok')"
    )
    await db.execute("INSERT INTO chat_threads (title) VALUES ('A conversation')")
    await db.execute(
        "INSERT INTO profile_drafts (base_version_id, status) VALUES (?, 'draft')",
        (fixture.profile_version_id,),
    )
    await db.execute("INSERT INTO settings (key, value) VALUES ('gaggimateHost', '10.1.2.3')")
    await db.execute("INSERT INTO settings (key, value) VALUES ('deviceWritesEnabled', 'true')")


async def restore_into(
    target_env: EnvSettings, file: bytes, *, then: Callable[[FastAPI], Any] | None = None
) -> list[str]:
    """Boot an app on ``target_env``, upload and apply ``file``, end the lifespan (the swap)."""
    async with running_app(target_env) as (app, client):
        signals = no_signal(app)
        response = await upload(client, file)
        assert response.status_code == 200, response.text
        token = response.json()["data"]["token"]
        applied = await client.post(f"{ROUTE}/{token}/apply")
        assert applied.status_code == 202, applied.text
        if then is not None:
            then(app)
    return signals


async def download(app: FastAPI, client: httpx.AsyncClient, *, include_keys: bool) -> bytes:
    response = await client.get("/api/backup", params={"include_keys": str(include_keys).lower()})
    assert response.status_code == 200
    return response.content


async def test_the_round_trip_restores_every_table(tmp_path: Path) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        await populate(app)
        content = await download(app, client, include_keys=True)
    source_hashes = table_hashes(source_env.database_path, skip={"auth_sessions"})

    target_env = env_in(tmp_path / "target")
    signals = await restore_into(target_env, content)

    assert signals == ["SIGTERM"]
    assert staged_files(target_env.data_dir) == []
    restored = table_hashes(target_env.database_path, skip={"auth_sessions"})
    # The one deliberate difference: the Writes switch was on in the source.
    conn = sqlite3.connect(target_env.database_path)
    assert conn.execute(
        "SELECT value FROM settings WHERE key = 'deviceWritesEnabled'"
    ).fetchone() == ("false",)
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = 'backup_manifest'"
    ).fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone() == (0,)
    conn.close()
    assert "backup_manifest" not in restored
    assert set(restored) == set(source_hashes) - {"backup_manifest"} | set()
    differing = {t for t in restored if restored[t] != source_hashes.get(t)}
    # `settings` differs by exactly the switch, checked below; no other table differs.
    assert differing <= {"settings"}, differing
    src = sqlite3.connect(source_env.database_path)
    src_rows = dict(src.execute("SELECT key, value FROM settings").fetchall())
    src.close()
    dst = sqlite3.connect(target_env.database_path)
    dst_rows = dict(dst.execute("SELECT key, value FROM settings").fetchall())
    dst.close()
    src_rows["deviceWritesEnabled"] = "false"
    assert dst_rows == src_rows

    # And it opens: a boot on the restored directory serves the data.
    async with running_app(target_env) as (booted, client):
        assert await booted.state.db.fetch_value("SELECT COUNT(*) FROM shots") > 0
        assert not (target_env.data_dir / RESTORE_MARKER).exists()


async def test_the_restore_is_logged_at_the_swap_and_at_the_next_boot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = await download(app, client, include_keys=False)
    target_env = env_in(tmp_path / "target", "info")
    capsys.readouterr()

    await restore_into(target_env, content)
    swap_log = capsys.readouterr().out
    assert '"event": "backup_restored"' in swap_log
    assert (target_env.data_dir / RESTORE_MARKER).exists()

    async with running_app(target_env):
        pass
    assert '"event": "restore_booted"' in capsys.readouterr().out
    assert not (target_env.data_dir / RESTORE_MARKER).exists()


async def test_without_keys_the_apps_own_keys_are_kept_and_signin_stays_on(tmp_path: Path) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        db = app.state.db
        for key in KEY_SETTING_KEYS:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, 'source-secret')", (key,)
            )
        await db.execute("INSERT INTO settings (key, value) VALUES ('authUser', 'barista')")
        await db.execute(
            "INSERT INTO settings (key, value) VALUES ('authPasswordHash', ?)", (HASH,)
        )
        # Auth is on now, so the download goes through the service rather than the route.
        export = await create_export(db, source_env.data_dir, include_keys=False)
        content = export.path.read_bytes()
        export.discard()
    assert b"source-secret" not in content

    # An app that has keys of its own keeps them (and its own signing secret is untouched).
    with_keys = env_in(tmp_path / "with-keys")
    async with running_app(with_keys) as (app, _client):
        for key in KEY_SETTING_KEYS:
            await app.state.db.execute(
                "INSERT INTO settings (key, value) VALUES (?, 'mine')", (key,)
            )
    await restore_into(with_keys, content)
    conn = sqlite3.connect(with_keys.database_path)
    stored = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    conn.close()
    assert {k: stored[k] for k in KEY_SETTING_KEYS} == dict.fromkeys(KEY_SETTING_KEYS, "mine")
    assert stored["authUser"] == "barista"
    assert stored["authPasswordHash"] == HASH

    # An app with none ends with none, and sign-in is on after the restore.
    without = env_in(tmp_path / "without")
    await restore_into(without, content)
    conn = sqlite3.connect(without.database_path)
    stored = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    conn.close()
    assert not [k for k in KEY_SETTING_KEYS if stored.get(k)]
    async with running_app(without) as (_app, client):
        assert (await client.get("/api/settings")).status_code == 401
        assert (await client.get("/api/auth/status")).json()["data"]["auth_required"] is True


async def test_an_older_backup_is_restored_and_migrated_at_the_next_boot(tmp_path: Path) -> None:
    """A database made by an older release: its migrations are this tree's earlier files."""
    older = tmp_path / "older-migrations"
    older.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "0041":
            shutil.copy(path, older / path.name)
    source = tmp_path / "older.db"
    db = Database(source)
    await db.connect()
    await run_migrations(db, older)
    await db.execute("INSERT INTO beans (name) VALUES ('From long ago')")
    await db.close()

    target_env = env_in(tmp_path / "target")
    await restore_into(target_env, source.read_bytes())

    conn = sqlite3.connect(target_env.database_path)
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == ("0040",)
    conn.close()
    async with running_app(target_env) as (booted, _client):
        latest = max(p.name[:4] for p in MIGRATIONS_DIR.glob("*.sql"))
        assert (
            await booted.state.db.fetch_value("SELECT MAX(version) FROM schema_migrations")
            == latest
        )
        assert await booted.state.db.fetch_value("SELECT name FROM beans") == "From long ago"


async def test_a_task_in_flight_refuses_the_apply_and_changes_nothing(tmp_path: Path) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = await download(app, client, include_keys=False)
    target_env = env_in(tmp_path / "target")
    async with running_app(target_env) as (app, client):
        signals = no_signal(app)
        before = hashlib.sha256(target_env.database_path.read_bytes()).hexdigest()
        token = (await upload(client, content)).json()["data"]["token"]
        gate = asyncio.Event()
        app.state.tasks.spawn("review-1", gate.wait())

        response = await client.post(f"{ROUTE}/{token}/apply")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RESTORE_BUSY"
        assert "running" in response.json()["error"]["message"]
        assert signals == []
        assert app.state.restore_pending is None
        assert staged_files(target_env.data_dir) == []
        assert hashlib.sha256(target_env.database_path.read_bytes()).hexdigest() == before
        gate.set()


async def test_a_second_apply_is_refused_while_the_first_is_pending(tmp_path: Path) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = await download(app, client, include_keys=False)
    async with running_app(env_in(tmp_path / "target")) as (app, client):
        no_signal(app)
        token = (await upload(client, content)).json()["data"]["token"]
        assert (await client.post(f"{ROUTE}/{token}/apply")).status_code == 202

        second = await client.post(f"{ROUTE}/{token}/apply")

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "RESTORE_PENDING"
        assert len(staged_files(app.state.env.data_dir)) == 1


async def test_apply_of_an_unknown_token_is_404(client: httpx.AsyncClient) -> None:
    response = await client.post(f"{ROUTE}/{'0' * 32}/apply")
    assert response.status_code == 404


async def test_a_failed_preparation_leaves_the_app_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = await download(app, client, include_keys=False)

    def boom(*_a: object, **_k: object) -> str:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr("gaggiclanker.api.backup.prepare_staged", boom)
    target_env = env_in(tmp_path / "target")
    async with running_app(target_env) as (app, client):
        signals = no_signal(app)
        token = (await upload(client, content)).json()["data"]["token"]

        response = await client.post(f"{ROUTE}/{token}/apply")

        assert response.status_code == 500
        assert app.state.restore_pending is None
        assert signals == []


async def test_the_signal_is_sent_only_after_the_response_is_on_the_wire(tmp_path: Path) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = await download(app, client, include_keys=False)
    async with running_app(env_in(tmp_path / "target")) as (app, client):
        token = (await upload(client, content)).json()["data"]["token"]
        events: list[str] = []
        app.state.terminate_process = lambda: events.append("signal")

        async def receive() -> MutableMapping[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

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
            "path": f"{ROUTE}/{token}/apply",
            "raw_path": f"{ROUTE}/{token}/apply".encode(),
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
            "app": app,
        }
        await app(scope, receive, send)

    assert events == ["start 202", "body end", "signal"]


async def test_the_swap_runs_only_after_the_database_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = await download(app, client, include_keys=False)
    seen: list[bool] = []
    real = swap_in

    def watching(*args: Any, **kwargs: Any) -> bool:
        seen.append(holder["app"].state.db.is_connected)
        return real(*args, **kwargs)

    holder: dict[str, FastAPI] = {}
    monkeypatch.setattr("gaggiclanker.main.swap_in", watching)
    target_env = env_in(tmp_path / "target")
    async with running_app(target_env) as (app, client):
        holder["app"] = app
        no_signal(app)
        token = (await upload(client, content)).json()["data"]["token"]
        await client.post(f"{ROUTE}/{token}/apply")
        assert seen == []  # not while the app is serving

    assert seen == [False]


async def test_a_swap_over_an_open_database_does_not_happen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaggiclanker.db.restore import PendingRestore
    from gaggiclanker.main import _swap_in_restore

    async with running_app(env_in(tmp_path / "t")) as (app, _client):
        staged = app.state.env.data_dir / f"restore-staging-{'c' * 32}.db"
        staged.write_bytes(b"x")
        app.state.restore_pending = PendingRestore(path=staged, schema_version="0050")
        before = app.state.env.database_path.read_bytes()

        await _swap_in_restore(app, app.state.db)  # still connected

        assert app.state.env.database_path.read_bytes() == before
        assert staged.exists()
        app.state.restore_pending = None


@pytest.mark.parametrize("crash", ["before", "after"])
async def test_a_crash_around_the_replace_leaves_the_old_or_the_new_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash: str
) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        await app.state.db.execute("INSERT INTO beans (name) VALUES ('From the file')")
        content = await download(app, client, include_keys=False)

    def interrupted(*args: Any, **kwargs: Any) -> bool:
        def die() -> None:
            raise OSError("power cut")

        if crash == "before":
            return swap_in(*args, before_replace=die, **kwargs)
        return swap_in(*args, after_replace=die, **kwargs)

    monkeypatch.setattr("gaggiclanker.main.swap_in", interrupted)
    target_env = env_in(tmp_path / "target")
    async with running_app(target_env) as (app, _client):
        await app.state.db.execute("INSERT INTO beans (name) VALUES ('Already here')")
    await restore_into(target_env, content)
    monkeypatch.undo()

    # There is never a moment without a database: the file is the old one or the new one.
    assert target_env.database_path.exists()
    async with running_app(target_env) as (booted, _client):
        names = [r["name"] for r in await booted.state.db.fetch_all("SELECT name FROM beans")]
    assert staged_files(target_env.data_dir) == []
    assert names == (["Already here"] if crash == "before" else ["From the file"])


async def test_a_wal_left_behind_stops_the_swap(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    live = data / "gaggiclanker.db"
    live.write_bytes(b"old")
    Path(f"{live}-wal").write_bytes(b"committed work")
    staged = data / f"restore-staging-{'d' * 32}.db"
    staged.write_bytes(b"new")

    assert swap_in(data, live, staged, schema_version="1") is False

    assert live.read_bytes() == b"old"
    assert staged.exists()
    assert Path(f"{live}-wal").exists()


async def test_the_sidecars_of_the_old_file_do_not_survive_the_swap(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    live = data / "gaggiclanker.db"
    live.write_bytes(b"old")
    Path(f"{live}-wal").write_bytes(b"")
    Path(f"{live}-shm").write_bytes(b"shm")
    staged = data / f"restore-staging-{'e' * 32}.db"
    staged.write_bytes(b"new")

    assert swap_in(data, live, staged, schema_version="7") is True

    assert live.read_bytes() == b"new"
    assert sorted(p.name for p in data.iterdir()) == sorted(["gaggiclanker.db", RESTORE_MARKER])
    assert json.loads((data / RESTORE_MARKER).read_text())["schema_version"] == "7"
    assert not list(data.glob(STAGING_GLOB))


def _stored(path: Path) -> dict[str, str]:
    conn = sqlite3.connect(path)
    try:
        return {str(k): str(v) for k, v in conn.execute("SELECT key, value FROM settings")}
    finally:
        conn.close()


async def test_each_key_is_decided_on_its_own_and_the_files_own_key_wins(tmp_path: Path) -> None:
    """The file holds key A but not key B; the app holds both. A is the file's, B is the app's."""
    file_has, file_lacks, *_rest = KEY_SETTING_KEYS
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, _client):
        await app.state.db.execute(
            "INSERT INTO settings (key, value) VALUES (?, 'from-the-file')", (file_has,)
        )
        export = await create_export(app.state.db, source_env.data_dir, include_keys=True)
        content = export.path.read_bytes()
        export.discard()
    target_env = env_in(tmp_path / "target")
    async with running_app(target_env) as (app, _client):
        for key in KEY_SETTING_KEYS:
            await app.state.db.execute(
                "INSERT INTO settings (key, value) VALUES (?, 'from-the-app')", (key,)
            )

    await restore_into(target_env, content)

    stored = _stored(target_env.database_path)
    assert stored[file_has] == "from-the-file"
    assert stored[file_lacks] == "from-the-app"
    for key in _rest:
        assert stored[key] == "from-the-app"


async def test_a_sync_pass_in_progress_refuses_the_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = await download(app, client, include_keys=False)
    target_env = env_in(tmp_path / "target")
    async with running_app(target_env) as (app, client):
        signals = no_signal(app)
        monkeypatch.setattr(app.state.connection, "busy", lambda: "a sync")
        token = (await upload(client, content)).json()["data"]["token"]

        response = await client.post(f"{ROUTE}/{token}/apply")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RESTORE_BUSY"
        assert signals == [] and app.state.restore_pending is None
        assert staged_files(target_env.data_dir) == []


async def test_a_claude_code_install_running_refuses_the_apply(tmp_path: Path) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = await download(app, client, include_keys=False)
    target_env = env_in(tmp_path / "target")
    async with running_app(target_env) as (app, client):
        signals = no_signal(app)
        # The installer's own job, with nothing in the task registry: the check must read it.
        app.state.claude_cli.begin("stable")
        token = (await upload(client, content)).json()["data"]["token"]

        response = await client.post(f"{ROUTE}/{token}/apply")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RESTORE_BUSY"
        assert signals == []


async def test_two_applies_at_once_give_one_restart(tmp_path: Path) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = await download(app, client, include_keys=False)
    async with running_app(env_in(tmp_path / "target")) as (app, client):
        signals = no_signal(app)
        token = (await upload(client, content)).json()["data"]["token"]

        first, second = await asyncio.gather(
            client.post(f"{ROUTE}/{token}/apply"), client.post(f"{ROUTE}/{token}/apply")
        )

        assert sorted([first.status_code, second.status_code]) == [202, 409]
        assert signals == ["SIGTERM"]


async def test_a_restore_deletes_the_sessions_the_file_carries(tmp_path: Path) -> None:
    """A download has none; a plain copy of a live database can, and they must not survive."""
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, _client):
        await app.state.db.execute(
            "INSERT INTO auth_sessions (id, subject, expires_at) "
            "VALUES ('jti-x', 'barista', 99999999)"
        )
    plain_copy = source_env.database_path.read_bytes()
    assert sqlite3.connect(source_env.database_path).execute(
        "SELECT COUNT(*) FROM auth_sessions"
    ).fetchone() == (1,)

    target_env = env_in(tmp_path / "target")
    await restore_into(target_env, plain_copy)

    assert sqlite3.connect(target_env.database_path).execute(
        "SELECT COUNT(*) FROM auth_sessions"
    ).fetchone() == (0,)


async def test_cancel_and_a_new_upload_are_refused_once_the_apply_is_accepted(
    tmp_path: Path,
) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, client):
        content = await download(app, client, include_keys=False)
    target_env = env_in(tmp_path / "target")
    async with running_app(target_env) as (app, client):
        no_signal(app)
        token = (await upload(client, content)).json()["data"]["token"]
        assert (await client.post(f"{ROUTE}/{token}/apply")).status_code == 202
        prepared = staged_files(target_env.data_dir)
        assert len(prepared) == 1

        cancel = await client.delete(f"{ROUTE}/{token}")
        again = await upload(client, content)

        for refused in (cancel, again):
            assert refused.status_code == 409
            assert refused.json()["error"]["code"] == "RESTORE_PENDING"
        assert staged_files(target_env.data_dir) == prepared


async def test_a_database_from_before_sign_in_existed_can_be_restored(tmp_path: Path) -> None:
    """The check accepts older files, so preparing one with no session table must work too."""
    older = tmp_path / "older-migrations"
    older.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "0007":
            shutil.copy(path, older / path.name)
    source = tmp_path / "ancient.db"
    db = Database(source)
    await db.connect()
    await run_migrations(db, older)
    await db.execute("INSERT INTO beans (name) VALUES ('From before sign-in')")
    await db.close()
    conn = sqlite3.connect(source)
    assert (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'auth_sessions'").fetchone() is None
    )
    conn.close()

    target_env = env_in(tmp_path / "target")
    await restore_into(target_env, source.read_bytes())

    async with running_app(target_env) as (booted, _client):
        assert await booted.state.db.fetch_value("SELECT name FROM beans") == "From before sign-in"
        assert await booted.state.db.fetch_value("SELECT COUNT(*) FROM auth_sessions") == 0


async def test_the_switch_row_moves_its_updated_at_like_the_repository_does(
    tmp_path: Path,
) -> None:
    source_env = env_in(tmp_path / "source")
    async with running_app(source_env) as (app, _client):
        await app.state.db.execute(
            "INSERT INTO settings (key, value, updated_at) "
            "VALUES ('deviceWritesEnabled', 'true', '2020-01-01T00:00:00.000Z')"
        )
    target_env = env_in(tmp_path / "target")
    await restore_into(target_env, source_env.database_path.read_bytes())

    row = (
        sqlite3.connect(target_env.database_path)
        .execute("SELECT value, updated_at FROM settings WHERE key = 'deviceWritesEnabled'")
        .fetchone()
    )
    assert row[0] == "false"
    assert row[1] > "2020-01-01T00:00:00.000Z"
