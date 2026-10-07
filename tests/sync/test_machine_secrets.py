"""The machine's credentials never reach the archive, the API or a backup.

Firmware v1.9.0 returns the Wi-Fi, access-point and Home Assistant passwords in
`GET /api/settings`; the fake serves them too, so these tests walk the real path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.machines import identity_to_upsert
from gaggiclanker.device.fake import DEFAULT_DEVICE_SETTINGS, FakeDevice
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app, seed_settings
from tests.sync.conftest import SMALL_COUNT, Archive, build_archive_device

SETTINGS_SECRETS = [
    DEFAULT_DEVICE_SETTINGS[k] for k in ("wifiPassword", "apPassword", "haPassword")
]
IDENTITY_SECRETS = {"apPassword": "identity-ap-password", "authToken": "identity-token"}
ALL_SECRETS = [*SETTINGS_SECRETS, *IDENTITY_SECRETS.values()]


def _stored(text: str | None) -> dict[str, object]:
    return json.loads(text) if text else {}


def test_the_fake_serves_the_secrets_the_firmware_does() -> None:
    assert set(DEFAULT_DEVICE_SETTINGS) >= {"wifiPassword", "apPassword", "haPassword"}


def test_identity_to_upsert_strips_both_documents_and_keeps_the_readings() -> None:
    from gaggiclanker.domain.models import OtaSettings

    upsert = identity_to_upsert(
        "h",
        identity=OtaSettings.model_validate({"hardware": "Pro", **IDENTITY_SECRETS}),
        settings={
            "pid": "1,2",
            "brewDelay": 800,
            "temperatureOffset": 2.5,
            **{"wifiPassword": "w", "nested": {"haPassword": "h"}},
        },
    )

    assert upsert.settings_json is not None and upsert.identity_json is not None
    assert _stored(upsert.settings_json) == {
        "pid": "1,2",
        "brewDelay": 800,
        "temperatureOffset": 2.5,
        "nested": {},
    }
    assert "identity-" not in upsert.identity_json
    assert _stored(upsert.identity_json)["hardware"] == "Pro"
    assert (upsert.pid, upsert.brew_delay_ms, upsert.temperature_offset_c) == ("1,2", 800, 2.5)


async def test_the_client_hands_no_one_the_settings_secrets(small_archive: Archive) -> None:
    small_archive.device.device_settings["mqtt"] = {"host": "broker", "authToken": "nested-token"}
    small_archive.device.device_settings["clientSecret"] = "top-secret"
    settings = await small_archive.client.get_settings()

    assert settings["mqtt"] == {"host": "broker"}
    assert "clientSecret" not in settings

    assert not {"wifiPassword", "apPassword", "haPassword"} & set(settings)
    assert settings["mdnsName"] == DEFAULT_DEVICE_SETTINGS["mdnsName"]


async def test_an_identity_pass_against_the_fake_stores_no_secret(small_archive: Archive) -> None:
    small_archive.device.identity.update(IDENTITY_SECRETS)
    await small_archive.device.emit_ota_settings()
    await small_archive.client.get_ota_settings()

    await small_archive.engine.sync_identity(trigger="test")

    row = await small_archive.db.fetch_one("SELECT settings_json, identity_json FROM machines")
    assert row is not None
    for column in ("settings_json", "identity_json"):
        assert not [s for s in ALL_SECRETS if s in (row[column] or "")], column
    assert _stored(row["settings_json"])["temperatureOffset"] == 2.5
    assert small_archive.client.identity is not None
    assert not [
        s for s in IDENTITY_SECRETS.values() if s in small_archive.client.identity.model_dump_json()
    ]


@pytest.fixture
async def served(
    tmp_path: Path, data_dir: Path
) -> AsyncIterator[tuple[FakeDevice, FastAPI, httpx.AsyncClient]]:
    device = build_archive_device(SMALL_COUNT, header_only=False)
    device.identity.update(IDENTITY_SECRETS)
    await device.start()
    env = EnvSettings(DATA_DIR=str(data_dir), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
    await seed_settings(env, gaggimateHost=device.address)
    try:
        async with running_app(env) as (app, client):
            assert await app.state.connection.client.wait_connected(5.0)
            await app.state.connection.engine.sync_identity(trigger="test")
            yield device, app, client
    finally:
        await device.stop()


async def test_the_api_serves_the_machine_and_its_status_without_secrets(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served

    machine = await client.get("/api/machine")
    status = await client.get("/api/device/status")

    assert machine.status_code == status.status_code == 200
    for response in (machine, status):
        assert not [s for s in ALL_SECRETS if s in response.text]
    settings = machine.json()["data"]["machine"]["settings"]
    assert settings["temperatureOffset"] == 2.5
    assert "wifiPassword" not in settings


async def test_a_backup_carries_no_secret(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served
    # With the keys: the strongest form of the file, so a secret in it would show.
    response = await client.get("/api/backup", params={"include_keys": "true"})
    assert response.status_code == 200

    raw = response.content

    assert not [s for s in ALL_SECRETS if s.encode() in raw]
