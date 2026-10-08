"""``POST /api/backup/restore`` and ``DELETE /api/backup/restore/{token}``: the check half.

Everything here is about what happens before anything changes: a file is staged
and looked at, and every refusal leaves the live database byte-identical and no
staging file behind.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.backup import KEY_SETTING_KEYS, create_export
from gaggiclanker.db.restore import STAGING_GLOB
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app

ROUTE = "/api/backup/restore"


async def make_file(
    app: FastAPI, tmp_path: Path, *, include_keys: bool = True, name: str = "good.db"
) -> Path:
    """A real download of ``app``'s database, as the file a person would later upload."""
    export = await create_export(app.state.db, app.state.env.data_dir, include_keys=include_keys)
    target = tmp_path / name
    target.write_bytes(export.path.read_bytes())
    export.discard()
    return target


def edit(path: Path, sql: str, *, foreign_keys: bool = True) -> Path:
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
        conn.executescript(sql)
    finally:
        conn.close()
    return path


async def upload(
    client: httpx.AsyncClient, path_or_bytes: Path | bytes, name: str = "backup.db"
) -> httpx.Response:
    content = path_or_bytes.read_bytes() if isinstance(path_or_bytes, Path) else path_or_bytes
    return await client.post(
        ROUTE,
        content=content,
        headers={"content-type": "application/octet-stream", "x-filename": name},
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def staged_files(data_dir: Path) -> list[Path]:
    return list(data_dir.glob(STAGING_GLOB))


async def test_a_good_file_is_staged_and_described(
    app: FastAPI, client: httpx.AsyncClient, data_dir: Path, tmp_path: Path
) -> None:
    await app.state.db.execute("INSERT INTO beans (name) VALUES ('Kept bean')")
    path = await make_file(app, tmp_path)
    await app.state.db.execute("INSERT INTO beans (name) VALUES ('Newer bean')")
    await app.state.db.execute(
        "INSERT INTO settings (key, value) VALUES ('llmApiKey', 'sk-here-key')"
    )

    response = await upload(client, path, "C:\\Users\\me\\gaggiclanker-20261001T101500Z.db")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["token"]) == 32
    assert data["filename"] == "gaggiclanker-20261001T101500Z.db"
    assert data["size_bytes"] == path.stat().st_size
    assert data["manifest"]["keys_included"] is True
    assert data["manifest"]["format_version"] == 1
    assert data["in_file"] == {"shots": 0, "sets": 0, "beans": 1}
    assert data["now"] == {"shots": 0, "sets": 0, "beans": 2}
    assert data["keys_in_file"] is False
    assert data["keys_here"] is True
    # Staged under DATA_DIR (the final rename is atomic on one filesystem).
    (staged,) = staged_files(data_dir)
    assert staged.name == f"restore-staging-{data['token']}.db"
    assert staged.stat().st_size == path.stat().st_size


async def test_a_non_ascii_file_name_arrives_percent_encoded_and_is_shown_decoded(
    app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    from urllib.parse import quote

    path = await make_file(app, tmp_path)

    data = (await upload(client, path, quote("café ☕ backup.db"))).json()["data"]

    assert data["filename"] == "café ☕ backup.db"


async def test_a_plain_copy_has_no_manifest(
    app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    path = edit(await make_file(app, tmp_path), "DROP TABLE backup_manifest;")

    data = (await upload(client, path)).json()["data"]

    assert data["manifest"] is None


async def test_keys_in_the_file_are_reported(
    app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    await app.state.db.execute(
        "INSERT INTO settings (key, value) VALUES (?, 'sk-abc')", (KEY_SETTING_KEYS[0],)
    )
    path = await make_file(app, tmp_path)

    data = (await upload(client, path)).json()["data"]

    assert data["keys_in_file"] is True


async def test_cancel_deletes_the_staged_file(
    app: FastAPI, client: httpx.AsyncClient, data_dir: Path, tmp_path: Path
) -> None:
    token = (await upload(client, await make_file(app, tmp_path))).json()["data"]["token"]
    assert len(staged_files(data_dir)) == 1

    response = await client.delete(f"{ROUTE}/{token}")

    assert response.status_code == 200
    assert response.json()["data"] == {"deleted": True}
    assert staged_files(data_dir) == []
    assert (await client.delete(f"{ROUTE}/{token}")).json()["data"] == {"deleted": False}


async def test_a_token_that_is_not_a_token_names_no_file(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    (data_dir / "precious.txt").write_text("keep", encoding="utf-8")

    response = await client.delete(f"{ROUTE}/..%2Fprecious.txt")

    assert response.status_code in (404, 422)
    assert (data_dir / "precious.txt").exists()
    response = await client.delete(f"{ROUTE}/not-a-token")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RESTORE_NOT_STAGED"


async def test_a_new_upload_replaces_the_staged_one(
    app: FastAPI, client: httpx.AsyncClient, data_dir: Path, tmp_path: Path
) -> None:
    path = await make_file(app, tmp_path)
    first = (await upload(client, path)).json()["data"]["token"]
    second = (await upload(client, path)).json()["data"]["token"]

    assert first != second
    assert [p.name for p in staged_files(data_dir)] == [f"restore-staging-{second}.db"]


def _not_sqlite(_: Path) -> bytes:
    return b"this is a text file, not a database" * 100


def _only_other_tables(tmp_path: Path) -> Callable[[Path], Path]:
    def build(_: Path) -> Path:
        path = tmp_path / "other.db"
        edit(path, "CREATE TABLE unrelated (a); INSERT INTO unrelated VALUES (1);")
        return path

    return build


def _extra_column(good: Path) -> Path:
    return edit(good, "ALTER TABLE sets ADD COLUMN surprise TEXT;")


def _changed_view(good: Path) -> Path:
    return edit(good, "DROP VIEW v_judgements; CREATE VIEW v_judgements AS SELECT 1 AS shot_id;")


def _missing_table(good: Path) -> Path:
    return edit(good, "DROP TABLE signature_expectations;", foreign_keys=False)


def _damaged(good: Path) -> Path:
    raw = bytearray(good.read_bytes())
    # Whole pages of garbage past the schema pages: the header still says SQLite.
    for start in range(4096 * 8, min(len(raw), 4096 * 40), 4096):
        raw[start : start + 4096] = b"\xff" * 4096
    good.write_bytes(bytes(raw))
    return good


def _index_out_of_step(good: Path) -> Path:
    """Damage only `PRAGMA integrity_check` sees: an index that no longer matches its table.

    Every ordinary read still works (the table's pages are fine), so the file passes the
    other checks and is refused only because the integrity check ran.
    """
    return edit(
        good,
        """
        CREATE TABLE zz_probe (a INTEGER);
        CREATE INDEX zz_probe_i ON zz_probe (a);
        INSERT INTO zz_probe VALUES (1), (2), (3);
        PRAGMA writable_schema = ON;
        UPDATE sqlite_master SET sql = 'CREATE INDEX zz_probe_i ON zz_probe (a + 1)'
         WHERE name = 'zz_probe_i';
        PRAGMA writable_schema = OFF;
        """,
    )


def _dangling(good: Path) -> Path:
    return edit(good, "INSERT INTO shot_judgements (shot_id) VALUES (987654);", foreign_keys=False)


@pytest.mark.parametrize(
    ("build", "status", "code"),
    [
        (_not_sqlite, 422, "RESTORE_NOT_A_DATABASE"),
        ("other_tables", 422, "RESTORE_NOT_A_DATABASE"),
        (_extra_column, 422, "RESTORE_SCHEMA_DIFFERS"),
        (_changed_view, 422, "RESTORE_SCHEMA_DIFFERS"),
        (_missing_table, 422, "RESTORE_SCHEMA_DIFFERS"),
        (_damaged, 422, "RESTORE_DAMAGED"),
        (_index_out_of_step, 422, "RESTORE_DAMAGED"),
        (_dangling, 422, "RESTORE_DAMAGED"),
    ],
    ids=[
        "not-sqlite",
        "missing-tables",
        "extra-column",
        "changed-view",
        "missing-table",
        "damaged",
        "integrity-only",
        "foreign-key",
    ],
)
async def test_every_refusal_changes_nothing(
    app: FastAPI,
    client: httpx.AsyncClient,
    data_dir: Path,
    tmp_path: Path,
    build: object,
    status: int,
    code: str,
) -> None:
    good = await make_file(app, tmp_path)
    if build == "other_tables":
        body: Path | bytes = _only_other_tables(tmp_path)(good)
    else:
        assert callable(build)
        made = build(good)
        body = made
    live = app.state.env.database_path
    before = digest(live)

    response = await upload(client, body)

    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert "details" not in error or not error["details"]
    assert str(tmp_path) not in response.text
    assert staged_files(data_dir) == []
    assert digest(live) == before


async def test_a_different_schema_is_refused_with_the_plain_reason(
    app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    different = _extra_column(await make_file(app, tmp_path))

    response = await upload(client, different)

    assert response.status_code == 422
    assert response.json()["error"]["message"] == (
        "This backup was made by a different version of gaggiclanker, with a different "
        "database. Restore it with the version that made it."
    )


async def test_a_file_from_the_previous_version_with_the_same_database_is_accepted(
    app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """The ledger table the previous version kept is not part of the schema."""
    good = await make_file(app, tmp_path)
    previous = edit(
        good,
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        ) STRICT;
        INSERT INTO schema_migrations VALUES ('0050', 'drop_judgement_grind', 'x', 'then');
        """,
    )

    assert (await upload(client, previous)).status_code == 200


async def test_a_declared_size_over_the_limit_is_413_and_stages_nothing(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    response = await client.post(ROUTE, content=b"x", headers={"content-length": str(2 * 1024**3)})

    # httpx sends the declared length; the middleware refuses before reading a byte.
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert staged_files(data_dir) == []


async def test_a_streamed_body_without_content_length_is_held_to_the_limit(
    client: httpx.AsyncClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gaggiclanker.infra.security.RESTORE_MAX_BODY_BYTES", 5000)
    monkeypatch.setattr("gaggiclanker.db.restore.MAX_RESTORE_BYTES", 5000)

    async def body() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"x" * 1000

    response = await client.post(ROUTE, content=body())

    assert response.status_code == 413
    assert staged_files(data_dir) == []


async def test_a_stale_staging_file_is_removed_at_boot(env: EnvSettings, data_dir: Path) -> None:
    (data_dir / f"restore-staging-{'a' * 32}.db").write_bytes(b"half an upload")
    (data_dir / f"restore-staging-{'b' * 32}.db-wal").write_bytes(b"x")

    async with running_app(env):
        assert staged_files(data_dir) == []


async def test_not_enough_disk_refuses_before_staging(
    app: FastAPI,
    client: httpx.AsyncClient,
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    path = await make_file(app, tmp_path)
    real = shutil.disk_usage(data_dir)
    monkeypatch.setattr(
        "gaggiclanker.db.backup.shutil.disk_usage",
        lambda _p: shutil._ntuple_diskusage(real.total, real.total, 0),
    )

    response = await upload(client, path)

    assert response.status_code == 507
    assert response.json()["error"]["code"] == "INSUFFICIENT_STORAGE"
    assert staged_files(data_dir) == []


def test_only_the_restore_route_gets_the_gigabyte_limit() -> None:
    from gaggiclanker.infra.security import DEFAULT_MAX_BODY_BYTES, limit_for_path

    assert limit_for_path("/api/backup/restore") == 1024**3
    assert limit_for_path("/api/backup/restore/" + "a" * 32) == 1024**3
    assert limit_for_path("/api/backup") == DEFAULT_MAX_BODY_BYTES
    assert limit_for_path("/api/backup/restorex") == DEFAULT_MAX_BODY_BYTES
