"""``GET /api/backup`` and the ``VACUUM INTO`` path underneath it."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.backup import KEY_SETTING_KEYS, SIGNING_KEY_ROW, create_export
from gaggiclanker.infra.envelope import file_download
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app

SECRET = "sk-DISTINCTIVE-0123456789-key-value"
HASH = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$aGFzaGhhc2hoYXNoaGFzaGhhc2g"


async def _seed_keys(app: FastAPI) -> None:
    db = app.state.db
    for key in KEY_SETTING_KEYS:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)", (key, f"{SECRET}-{key}")
        )
    await db.execute("INSERT INTO settings (key, value) VALUES ('authUser', 'barista')")
    await db.execute("INSERT INTO settings (key, value) VALUES ('authPasswordHash', ?)", (HASH,))
    await db.execute(
        "INSERT OR REPLACE INTO runtime_secrets (key, value) VALUES (?, ?)",
        (SIGNING_KEY_ROW, f"{SECRET}-signing"),
    )
    await db.execute(
        "INSERT INTO auth_sessions (id, subject, expires_at) VALUES ('jti-1', 'barista', 99999999)"
    )


def _rows(path: Path, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


async def _export(app: FastAPI, data_dir: Path, *, include_keys: bool) -> bytes:
    """The file as a client would receive it. Direct, not over HTTP: seeding the sign-in pair
    turns the lock on, and the route is covered with the lock off elsewhere in this file."""
    export = await create_export(app.state.db, data_dir, include_keys=include_keys)
    try:
        return export.path.read_bytes()
    finally:
        export.discard()


def _save(content: bytes, tmp_path: Path) -> Path:
    path = tmp_path / "downloaded.db"
    path.write_bytes(content)
    return path


def test_the_keys_the_box_covers_are_the_three_secret_settings() -> None:
    """Derived from the registry, and pinned by name: a new secret setting must be a decision."""
    assert sorted(KEY_SETTING_KEYS) == ["anthropicApiKey", "claudeCodeOauthToken", "llmApiKey"]
    assert "authPasswordHash" not in KEY_SETTING_KEYS
    assert SIGNING_KEY_ROW == "jwt_secret"


async def test_download_is_a_readable_database_with_a_manifest(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    await client.patch("/api/settings", json={"gaggimateHost": "10.0.0.7"})

    response = await client.get("/api/backup")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="gaggiclanker-')
    assert disposition.endswith('.db"')
    path = _save(response.content, tmp_path)
    assert _rows(path, "SELECT value FROM settings WHERE key = 'gaggimateHost'") == [("10.0.0.7",)]
    assert _rows(path, "PRAGMA integrity_check") == [("ok",)]
    ((fmt, version, schema, created, keys),) = _rows(
        path,
        "SELECT format_version, app_version, schema_version, created_at, keys_included "
        "FROM backup_manifest",
    )
    assert fmt == 1 and keys == 0
    assert version
    assert schema == _rows(path, "SELECT MAX(version) FROM schema_migrations")[0][0]
    assert str(created).endswith("Z")


async def test_without_keys_the_keys_are_gone_from_the_rows_and_from_the_bytes(
    app: FastAPI, data_dir: Path, tmp_path: Path
) -> None:
    await _seed_keys(app)

    content = await _export(app, data_dir, include_keys=False)

    path = _save(content, tmp_path)
    stored = {str(k): str(v) for k, v in _rows(path, "SELECT key, value FROM settings")}
    assert not set(KEY_SETTING_KEYS) & set(stored)
    assert stored["authUser"] == "barista"
    assert stored["authPasswordHash"] == HASH
    assert _rows(path, "SELECT 1 FROM runtime_secrets WHERE key = 'jwt_secret'") == []
    assert _rows(path, "SELECT COUNT(*) FROM auth_sessions") == [(0,)]
    # The deleted row's bytes would sit in a free page without the second vacuum.
    assert SECRET.encode() not in content
    assert _rows(path, "SELECT keys_included FROM backup_manifest") == [(0,)]


async def test_with_keys_they_are_in_the_file(app: FastAPI, data_dir: Path, tmp_path: Path) -> None:
    await _seed_keys(app)

    content = await _export(app, data_dir, include_keys=True)

    path = _save(content, tmp_path)
    stored = {str(k): str(v) for k, v in _rows(path, "SELECT key, value FROM settings")}
    for key in KEY_SETTING_KEYS:
        assert stored[key] == f"{SECRET}-{key}"
    assert _rows(path, "SELECT value FROM runtime_secrets WHERE key = 'jwt_secret'") == [
        (f"{SECRET}-signing",)
    ]
    assert SECRET.encode() in content
    # Sessions are never data, with the keys or without.
    assert _rows(path, "SELECT COUNT(*) FROM auth_sessions") == [(0,)]
    assert _rows(path, "SELECT keys_included FROM backup_manifest") == [(1,)]


async def test_include_keys_reaches_the_route(client: httpx.AsyncClient, tmp_path: Path) -> None:
    plain = _save((await client.get("/api/backup")).content, tmp_path)
    assert _rows(plain, "SELECT keys_included FROM backup_manifest") == [(0,)]
    with_keys = _save((await client.get("/api/backup?include_keys=true")).content, tmp_path)
    assert _rows(with_keys, "SELECT keys_included FROM backup_manifest") == [(1,)]


async def test_the_live_database_is_untouched_by_an_export(app: FastAPI, data_dir: Path) -> None:
    await _seed_keys(app)

    await _export(app, data_dir, include_keys=False)

    db = app.state.db
    assert await db.fetch_value("SELECT COUNT(*) FROM auth_sessions") == 1
    assert await db.fetch_value("SELECT COUNT(*) FROM settings WHERE key = 'llmApiKey'") == 1
    assert (
        await db.fetch_value("SELECT COUNT(*) FROM sqlite_master WHERE name = 'backup_manifest'")
        == 0
    )


async def test_a_download_boots_in_a_fresh_data_dir(
    app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """The documented restore is still a file copy; the manifest table is harmless to a boot."""
    await app.state.db.execute("INSERT INTO beans (name) VALUES ('Copied bean')")
    content = (await client.get("/api/backup")).content

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "gaggiclanker.db").write_bytes(content)
    env = EnvSettings(DATA_DIR=str(fresh), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
    async with running_app(env) as (booted, _client):
        assert await booted.state.db.fetch_value("SELECT name FROM beans") == "Copied bean"


async def test_a_download_works_while_the_sync_engine_is_writing(
    app: FastAPI, data_dir: Path
) -> None:
    """The regression `scripts/repro_backup_during_sync.py` reproduces.

    The whole app shares one SQLite connection, and once sync is running that connection
    spends its time inside ``BEGIN IMMEDIATE`` — the sync engine wraps each shot
    and its samples so they land together. ``VACUUM`` cannot run inside a
    transaction, so a backup taken during a backfill (exactly when somebody
    reaches for one) used to answer 500 about one time in four. The export now
    runs on its own connection.
    """
    db = app.state.db
    async with db.transaction():
        await db.execute("INSERT INTO beans (name) VALUES (?)", ("Backup bean",))
        result = await create_export(db, data_dir, include_keys=False)
    try:
        assert result.path.is_file()
        assert result.size_bytes > 0
        assert _rows(result.path, "PRAGMA integrity_check") == [("ok",)]
        # The row was still uncommitted when the snapshot was taken, so it is
        # correctly absent: a backup is a consistent copy, not a peek at
        # somebody else's open transaction.
        assert _rows(result.path, "SELECT COUNT(*) FROM beans") == [(0,)]
    finally:
        result.discard()


def _export_dirs(data_dir: Path) -> list[Path]:
    return list(data_dir.glob("backup-export-*"))


async def test_the_temp_file_is_removed_after_the_response(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    response = await client.get("/api/backup")
    assert response.status_code == 200
    assert _export_dirs(data_dir) == []
    assert not (data_dir / "backups").exists()


async def test_the_temp_file_is_removed_when_the_client_goes_away(
    app: FastAPI, data_dir: Path
) -> None:
    export = await create_export(app.state.db, data_dir, include_keys=False)
    assert len(_export_dirs(data_dir)) == 1
    response = file_download(export.path, filename=export.filename, cleanup=export.discard)

    sent: list[str] = []

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(str(message["type"]))
        if message["type"] == "http.response.body":
            raise OSError("client closed the connection")

    with pytest.raises(OSError, match="client closed"):
        await response({"type": "http", "method": "GET", "headers": []}, receive, send)  # type: ignore[arg-type]

    assert "http.response.start" in sent
    assert _export_dirs(data_dir) == []


async def test_a_failed_export_leaves_nothing_behind(
    app: FastAPI, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error at /secret/path")

    monkeypatch.setattr("gaggiclanker.db.backup._prepare_copy", boom)

    with pytest.raises(Exception, match="Backup failed") as caught:
        await create_export(app.state.db, data_dir, include_keys=False)

    assert "/secret/path" not in str(caught.value)
    assert _export_dirs(data_dir) == []


async def test_an_export_without_room_is_refused_with_its_own_code(
    client: httpx.AsyncClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = shutil.disk_usage(data_dir)
    monkeypatch.setattr(
        "gaggiclanker.db.backup.shutil.disk_usage",
        lambda _path: shutil._ntuple_diskusage(real.total, real.total, 0),
    )

    response = await client.get("/api/backup")

    assert response.status_code == 507
    assert response.json()["error"]["code"] == "INSUFFICIENT_STORAGE"
    assert _export_dirs(data_dir) == []


async def test_an_export_left_by_a_stopped_process_is_removed_at_boot(
    env: EnvSettings, data_dir: Path
) -> None:
    """A download cut off between the copy and the stripping is a file full of credentials."""
    leftover = data_dir / "backup-export-abc123"
    leftover.mkdir()
    (leftover / "backup.db").write_bytes(b"a full copy, keys and sessions included")
    unrelated = data_dir / "keep-me"
    unrelated.mkdir()

    async with running_app(env):
        assert not leftover.exists()
        assert unrelated.exists()
