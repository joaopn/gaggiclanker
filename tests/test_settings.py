"""Settings precedence, the API shape, and secret handling.

The precedence rule (database > environment > default) is the part most likely
to be broken by a future change, and the least likely to be noticed: a setting
changed in the UI that silently does nothing looks like a UI bug for weeks.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.settings import SETTINGS_REGISTRY, EnvSettings, secret_hint
from tests.conftest import running_app


async def get_settings(client: httpx.AsyncClient) -> dict[str, dict[str, object]]:
    response = await client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


async def test_defaults_are_returned_when_nothing_is_configured(
    client: httpx.AsyncClient,
) -> None:
    settings = await get_settings(client)
    assert set(settings) == set(SETTINGS_REGISTRY)

    poll = settings["devicePollIntervalSeconds"]
    assert poll["value"] == 60
    assert poll["default"] == 60
    assert poll["override"] is None
    assert poll["source"] == "default"


async def test_every_registry_key_documents_itself(client: httpx.AsyncClient) -> None:
    """A setting with no prose is a setting nobody can configure correctly."""
    settings = await get_settings(client)
    for key, payload in settings.items():
        assert payload["description"], f"{key} has no description"
        assert payload["type"] in {"string", "int", "float", "bool"}


async def test_environment_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch, env: EnvSettings
) -> None:
    monkeypatch.setenv("GAGGIMATE_HOST", "10.0.0.42")
    async with running_app(env) as (_app, client):
        settings = await get_settings(client)
        assert settings["gaggimateHost"]["value"] == "10.0.0.42"
        assert settings["gaggimateHost"]["source"] == "environment"
        assert settings["gaggimateHost"]["override"] is None


async def test_database_override_beats_the_environment(
    monkeypatch: pytest.MonkeyPatch, env: EnvSettings
) -> None:
    """The value the maintainer set in the UI wins over the compose file."""
    monkeypatch.setenv("GAGGIMATE_HOST", "10.0.0.42")
    async with running_app(env) as (_app, client):
        patch = await client.patch("/api/settings", json={"gaggimateHost": "gaggimate.local"})
        assert patch.status_code == 200

        settings = await get_settings(client)
        assert settings["gaggimateHost"]["value"] == "gaggimate.local"
        assert settings["gaggimateHost"]["override"] == "gaggimate.local"
        assert settings["gaggimateHost"]["source"] == "database"


async def test_clearing_an_override_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch, env: EnvSettings
) -> None:
    monkeypatch.setenv("GAGGIMATE_HOST", "10.0.0.42")
    async with running_app(env) as (_app, client):
        await client.patch("/api/settings", json={"gaggimateHost": "gaggimate.local"})
        await client.patch("/api/settings", json={"gaggimateHost": None})

        settings = await get_settings(client)
        assert settings["gaggimateHost"]["value"] == "10.0.0.42"
        assert settings["gaggimateHost"]["source"] == "environment"


async def test_override_survives_a_restart(env: EnvSettings) -> None:
    """The override lives in the database file, not in process memory."""
    async with running_app(env) as (_app, client):
        await client.patch("/api/settings", json={"devicePollIntervalSeconds": 15})

    async with running_app(env) as (_app, client):
        settings = await get_settings(client)
        assert settings["devicePollIntervalSeconds"]["value"] == 15
        assert settings["devicePollIntervalSeconds"]["source"] == "database"


async def test_unparseable_environment_value_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, env: EnvSettings
) -> None:
    """A typo in the compose file must not take the container down."""
    monkeypatch.setenv("GAGGICLANKER_DEVICE_POLL_INTERVAL_SECONDS", "sixty")
    async with running_app(env) as (_app, client):
        settings = await get_settings(client)
        assert settings["devicePollIntervalSeconds"]["value"] == 60
        assert settings["devicePollIntervalSeconds"]["source"] == "default"


async def test_empty_environment_value_is_not_an_override(
    monkeypatch: pytest.MonkeyPatch, env: EnvSettings
) -> None:
    """``GAGGIMATE_HOST=`` in a compose file means "unset", not "empty host"."""
    monkeypatch.setenv("GAGGIMATE_HOST", "")
    async with running_app(env) as (_app, client):
        settings = await get_settings(client)
        assert settings["gaggimateHost"]["source"] == "default"


async def test_booleans_round_trip(client: httpx.AsyncClient) -> None:
    response = await client.patch("/api/settings", json={"deviceSyncEnabled": False})
    assert response.status_code == 200
    assert response.json()["data"]["deviceSyncEnabled"]["value"] is False

    settings = await get_settings(client)
    assert settings["deviceSyncEnabled"]["value"] is False
    assert settings["deviceSyncEnabled"]["override"] is False


async def test_boolean_is_rejected_for_an_int_setting(client: httpx.AsyncClient) -> None:
    """JSON ``true`` is a Python bool and ``int(True) == 1``; that must not store 1."""
    response = await client.patch("/api/settings", json={"devicePollIntervalSeconds": True})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


async def test_non_numeric_value_for_an_int_setting_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    response = await client.patch("/api/settings", json={"devicePollIntervalSeconds": "often"})
    assert response.status_code == 400
    details = response.json()["error"]["details"]
    assert any(item["field"] == "devicePollIntervalSeconds" for item in details)


async def test_unknown_key_is_rejected_and_nothing_is_written(
    client: httpx.AsyncClient,
) -> None:
    """A whole PATCH is validated before anything is stored."""
    response = await client.patch(
        "/api/settings",
        json={"gaggimateHost": "10.0.0.9", "nonsenseKey": "x"},
    )
    assert response.status_code == 400

    settings = await get_settings(client)
    assert settings["gaggimateHost"]["source"] == "default"


async def test_empty_patch_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.patch("/api/settings", json={})
    assert response.status_code == 400


async def test_secret_is_never_returned_only_hinted(client: httpx.AsyncClient) -> None:
    secret = "sk-proj-abcdef0123456789"
    response = await client.patch("/api/settings", json={"llmApiKey": secret})
    assert response.status_code == 200
    assert secret not in response.text

    settings = await get_settings(client)
    entry = settings["llmApiKey"]
    assert entry["secret"] is True
    assert entry["configured"] is True
    assert entry["hint"] == "sk-p"
    assert "value" not in entry
    assert "override" not in entry
    assert "default" not in entry


async def test_unset_secret_reports_no_hint(client: httpx.AsyncClient) -> None:
    entry = (await get_settings(client))["llmApiKey"]
    assert entry["configured"] is False
    assert entry["hint"] is None


async def test_secret_from_the_environment_is_hinted(
    monkeypatch: pytest.MonkeyPatch, env: EnvSettings
) -> None:
    monkeypatch.setenv("GAGGICLANKER_LLM_API_KEY", "or-v1-deadbeef")
    async with running_app(env) as (_app, client):
        entry = (await get_settings(client))["llmApiKey"]
        assert entry["hint"] == "or-v"
        assert entry["source"] == "environment"


def test_short_secrets_are_masked_rather_than_hinted() -> None:
    """Four characters or fewer would be the whole secret, so mask instead."""
    assert secret_hint("abc") == "***"
    assert secret_hint("abcd") == "****"
    assert secret_hint("abcde") == "abcd"
    assert secret_hint("") is None
    assert secret_hint(None) is None


async def test_settings_service_is_reachable_from_app_state(app: FastAPI) -> None:
    """Other layers read settings through the service, not through the HTTP API."""
    service = app.state.settings_service
    assert await service.get("devicePollIntervalSeconds") == 60
    await service.apply({"devicePollIntervalSeconds": 30})
    assert await service.get("devicePollIntervalSeconds") == 30


async def test_rejected_value_is_never_echoed_back(client: httpx.AsyncClient) -> None:
    """int('sk-live-...') raises with the string in its message.

    Passing that ValueError's text through to error.details put whatever the
    user typed into the response body — and a settings PATCH is exactly where
    someone pastes an API key into the wrong field.
    """
    leaked = "sk-live-DO-NOT-ECHO-THIS"
    response = await client.patch("/api/settings", json={"devicePollIntervalSeconds": leaked})
    assert response.status_code == 400
    assert leaked not in response.text
    details = response.json()["error"]["details"]
    assert details == [{"field": "devicePollIntervalSeconds", "message": "expected an integer"}]


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("devicePollIntervalSeconds", "nope", "expected an integer"),
        ("devicePollIntervalSeconds", True, "expected an integer"),
        ("deviceSyncEnabled", "maybe", "expected a boolean"),
        ("deviceSyncEnabled", 7, "expected a boolean"),
        ("gaggimateHost", 1234, "expected a string"),
    ],
)
async def test_rejection_messages_are_fixed_strings(
    client: httpx.AsyncClient, key: str, value: object, message: str
) -> None:
    response = await client.patch("/api/settings", json={key: value})
    assert response.status_code == 400
    assert response.json()["error"]["details"] == [{"field": key, "message": message}]


async def test_dotenv_is_part_of_the_environment(env: EnvSettings) -> None:
    """A key set in .env must work for registry settings, not only bootstrap ones.

    EnvSettings reads .env through pydantic-settings, so DATA_DIR from that file
    worked while GAGGIMATE_HOST from the same file was ignored: one file, two
    behaviours, and nothing telling the user which keys were which.
    """
    async with running_app(env, dotenv={"GAGGIMATE_HOST": "from-dotenv"}) as (_app, client):
        settings = await get_settings(client)
        assert settings["gaggimateHost"]["value"] == "from-dotenv"
        assert settings["gaggimateHost"]["source"] == "environment"


async def test_process_environment_beats_dotenv(
    monkeypatch: pytest.MonkeyPatch, env: EnvSettings
) -> None:
    """Same precedence pydantic-settings applies to the bootstrap keys."""
    monkeypatch.setenv("GAGGIMATE_HOST", "from-process-env")
    async with running_app(env, dotenv={"GAGGIMATE_HOST": "from-dotenv"}) as (_app, client):
        settings = await get_settings(client)
        assert settings["gaggimateHost"]["value"] == "from-process-env"


async def test_database_beats_dotenv_too(env: EnvSettings) -> None:
    async with running_app(env, dotenv={"GAGGIMATE_HOST": "from-dotenv"}) as (_app, client):
        await client.patch("/api/settings", json={"gaggimateHost": "from-ui"})
        settings = await get_settings(client)
        assert settings["gaggimateHost"]["value"] == "from-ui"
        assert settings["gaggimateHost"]["source"] == "database"
