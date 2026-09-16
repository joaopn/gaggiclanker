"""Settings precedence, the API shape, and secret handling.

The precedence rule (database > default) is the part most likely to be broken
by a future change, and the least likely to be noticed: a setting changed in the
UI that silently does nothing looks like a UI bug for weeks. The environment is
no longer part of that rule at all, which is a behaviour in its own right — a
variable that used to configure a setting must now be inert and *said* to be
inert, and the one that used to allow writes to the machine must stop the boot.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.settings import (
    DEVICE_WRITES_ENV_KEY,
    FORMER_SETTING_ENV_KEYS,
    REMOVED_SETTINGS,
    SETTINGS_REGISTRY,
    EnvSettings,
    secret_hint,
)
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

    keep = settings["deviceCleanupKeepNewest"]
    assert keep["value"] == 50
    assert keep["default"] == 50
    assert keep["override"] is None
    assert keep["source"] == "default"


async def test_every_registry_key_documents_itself(client: httpx.AsyncClient) -> None:
    """A setting with no prose is a setting nobody can configure correctly."""
    settings = await get_settings(client)
    for key, payload in settings.items():
        assert payload["description"], f"{key} has no description"
        assert payload["type"] in {"string", "int", "float", "bool"}


async def test_a_former_variable_does_not_configure_a_setting(
    monkeypatch: pytest.MonkeyPatch, env: EnvSettings
) -> None:
    """The environment is inert: a setting is what the database says, or its default.

    This is the rule the rest of the layer rests on. A variable that still works
    is a second configuration surface that can disagree with the Settings page,
    and disagreeing silently is how a value edited in the UI appears to do
    nothing.
    """
    monkeypatch.setenv("GAGGIMATE_HOST", "10.0.0.42")
    async with running_app(env) as (_app, client):
        settings = await get_settings(client)
        assert settings["gaggimateHost"]["value"] == ""
        assert settings["gaggimateHost"]["source"] == "default"
        assert settings["gaggimateHost"]["override"] is None


async def test_a_stored_value_beats_the_default(env: EnvSettings) -> None:
    """What the maintainer saved in the UI is what the app runs on."""
    async with running_app(env) as (_app, client):
        patch = await client.patch("/api/settings", json={"gaggimateHost": "gaggimate.local"})
        assert patch.status_code == 200

        settings = await get_settings(client)
        assert settings["gaggimateHost"]["value"] == "gaggimate.local"
        assert settings["gaggimateHost"]["override"] == "gaggimate.local"
        assert settings["gaggimateHost"]["source"] == "database"


async def test_clearing_an_override_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, env: EnvSettings
) -> None:
    """Even with the old variable set: there is nothing between the row and the default."""
    monkeypatch.setenv("GAGGICLANKER_DEVICE_CLEANUP_KEEP_NEWEST", "12")
    async with running_app(env) as (_app, client):
        await client.patch("/api/settings", json={"deviceCleanupKeepNewest": 15})
        await client.patch("/api/settings", json={"deviceCleanupKeepNewest": None})

        settings = await get_settings(client)
        assert settings["deviceCleanupKeepNewest"]["value"] == 50
        assert settings["deviceCleanupKeepNewest"]["source"] == "default"


async def test_override_survives_a_restart(env: EnvSettings) -> None:
    """The override lives in the database file, not in process memory."""
    async with running_app(env) as (_app, client):
        await client.patch("/api/settings", json={"deviceCleanupKeepNewest": 15})

    async with running_app(env) as (_app, client):
        settings = await get_settings(client)
        assert settings["deviceCleanupKeepNewest"]["value"] == 15
        assert settings["deviceCleanupKeepNewest"]["source"] == "database"


async def test_a_row_for_a_key_that_no_longer_exists_is_ignored(env: EnvSettings) -> None:
    """A setting that is dropped leaves its row behind; the app must not care.

    The database outlives the registry: an archive configured a year ago holds
    override rows for keys later releases removed. Resolution walks the
    registry, so the row is simply never looked at — but that is a property
    worth pinning, because the alternative (a startup that fails on a key it
    does not recognise) would be a container that will not boot after an
    upgrade, on the machine of the one person who had configured that key.
    """
    async with running_app(env) as (app, client):
        await app.state.db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("devicePollIntervalSeconds", "60"),
        )
        settings = await get_settings(client)
        assert "devicePollIntervalSeconds" not in settings

    # And again from cold, because "ignored" has to survive the boot that reads
    # the table rather than only the request that follows the write.
    async with running_app(env) as (_app, client):
        settings = await get_settings(client)
        assert set(settings) == set(SETTINGS_REGISTRY)


async def test_former_setting_variables_are_named_once_at_boot_and_do_nothing(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Boot succeeds, one line names every variable, and no value is logged.

    Two kinds in one list: a variable that used to configure a setting that
    still exists, and one whose setting was removed outright. Both do nothing
    now, and an owner with either in a compose file should hear that rather
    than find out by its silence.
    """
    from gaggiclanker.infra.logging import configure_logging

    assert set(REMOVED_SETTINGS) == {
        "mcpDeviceWrites",
        "deviceCleanupAuto",
        "notesWritebackEnabled",
        "mcpEnabled",
    }
    named = ("GAGGIMATE_HOST", "GAGGICLANKER_MODEL_ANALYSIS", *REMOVED_SETTINGS.values())
    for env_key in named:
        monkeypatch.setenv(env_key, "true-and-secret-looking")
    configure_logging("info", json_output=True)
    try:
        async with running_app(env) as (_app, client):
            settings = await get_settings(client)
            logged = capsys.readouterr().out
            response = await client.patch("/api/settings", json={"deviceCleanupAuto": True})
    finally:
        configure_logging("warning", json_output=True)

    assert set(settings) == set(SETTINGS_REGISTRY)
    assert not set(REMOVED_SETTINGS) & set(settings)
    assert settings["gaggimateHost"]["source"] == "default"
    warnings = [line for line in logged.splitlines() if "setting_env_ignored" in line]
    assert len(warnings) == 1, logged
    for env_key in named:
        assert env_key in warnings[0], env_key
    assert "true-and-secret-looking" not in logged
    # And a removed key cannot be set back through the API either.
    assert response.status_code == 400


async def test_no_warning_when_no_former_variable_is_set(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An old compose file passing ``GAGGIMATE_HOST=`` through says nothing at all."""
    from gaggiclanker.infra.logging import configure_logging

    for env_key in FORMER_SETTING_ENV_KEYS:
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setenv("GAGGICLANKER_DEVICE_CLEANUP_AUTO", "  ")
    monkeypatch.setenv("GAGGIMATE_HOST", "")
    configure_logging("info", json_output=True)
    try:
        async with running_app(env) as (_app, client):
            logged = capsys.readouterr().out
            settings = await get_settings(client)
    finally:
        configure_logging("warning", json_output=True)
    assert "setting_env_ignored" not in logged
    assert settings["gaggimateHost"]["source"] == "default"


@pytest.mark.parametrize(
    "name",
    [DEVICE_WRITES_ENV_KEY, DEVICE_WRITES_ENV_KEY.lower(), "Gaggiclanker_Device_Writes_Enabled"],
)
async def test_the_retired_device_writes_switch_refuses_the_boot(
    name: str,
    env: EnvSettings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The one former variable that stops the boot instead of being ignored.

    It used to open the only path from this box to the machine. Ignoring it with
    a line in a log nobody reads would leave its owner believing a file they
    control still decides whether a profile can be written.
    """
    monkeypatch.setenv(name, "true")
    with pytest.raises(RuntimeError) as caught:
        async with running_app(env):
            pass
    logged = capsys.readouterr().out
    assert name in str(caught.value)
    assert "Settings" in str(caught.value)
    assert [line for line in logged.splitlines() if "device_writes_env_refused" in line]
    # Refused before the database was created, let alone opened.
    assert not env.database_path.exists()


async def test_an_empty_device_writes_switch_boots_with_writes_still_off(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GAGGICLANKER_DEVICE_WRITES_ENABLED=`` means unset, the rule everywhere here."""
    monkeypatch.setenv(DEVICE_WRITES_ENV_KEY, "")
    async with running_app(env) as (_app, client):
        settings = await get_settings(client)
        assert settings["deviceWritesEnabled"]["value"] is False
        assert settings["deviceWritesEnabled"]["source"] == "default"


async def test_booleans_round_trip(client: httpx.AsyncClient) -> None:
    response = await client.patch("/api/settings", json={"deviceSyncEnabled": False})
    assert response.status_code == 200
    assert response.json()["data"]["deviceSyncEnabled"]["value"] is False

    settings = await get_settings(client)
    assert settings["deviceSyncEnabled"]["value"] is False
    assert settings["deviceSyncEnabled"]["override"] is False


async def test_boolean_is_rejected_for_an_int_setting(client: httpx.AsyncClient) -> None:
    """JSON ``true`` is a Python bool and ``int(True) == 1``; that must not store 1."""
    response = await client.patch("/api/settings", json={"deviceCleanupKeepNewest": True})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


async def test_non_numeric_value_for_an_int_setting_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    response = await client.patch("/api/settings", json={"deviceCleanupKeepNewest": "often"})
    assert response.status_code == 400
    details = response.json()["error"]["details"]
    assert any(item["field"] == "deviceCleanupKeepNewest" for item in details)


@pytest.mark.parametrize(
    "typed", ["https://10.0.0.9", "wss://10.0.0.9", "10.0.0.9/gaggimate", "user@10.0.0.9"]
)
async def test_a_machine_address_the_client_cannot_use_is_refused(
    client: httpx.AsyncClient, typed: str
) -> None:
    response = await client.patch("/api/settings", json={"gaggimateHost": typed})
    assert response.status_code == 400
    assert typed not in response.text

    settings = await get_settings(client)
    assert settings["gaggimateHost"]["source"] == "default"


@pytest.mark.parametrize("typed", ["http://10.0.0.9/", "ws://10.0.0.9:8080", "gaggimate.local"])
async def test_a_machine_address_with_a_plain_scheme_is_accepted(
    client: httpx.AsyncClient, typed: str
) -> None:
    response = await client.patch("/api/settings", json={"gaggimateHost": typed})
    assert response.status_code == 200, response.text


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


def test_no_setting_declares_an_environment_variable_at_all() -> None:
    """Not "no secret reads one": nothing in the registry can read one.

    The field is gone from the declaration, so a setting added later cannot
    acquire an environment layer by filling one in — which is what made this
    worth removing rather than emptying.
    """
    from gaggiclanker.settings import SettingDefinition

    assert "env_key" not in SettingDefinition.__slots__
    assert [d.key for d in SETTINGS_REGISTRY.values() if d.secret], (
        "the registry has no secrets, so this test is checking nothing"
    )


def test_the_variables_that_stop_a_boot_are_never_merely_ignored() -> None:
    """The three lists are disjoint: a name is refused, or reported, never both."""
    from gaggiclanker.settings import RETIRED_AUTH_ENV_KEYS

    former = {name.upper() for name in FORMER_SETTING_ENV_KEYS}
    assert former.isdisjoint({name.upper() for name in RETIRED_AUTH_ENV_KEYS})
    assert DEVICE_WRITES_ENV_KEY.upper() not in former
    # And every removed setting's variable is in the reported list.
    assert {name.upper() for name in REMOVED_SETTINGS.values()} <= former


def test_every_retired_credential_variable_is_refused_at_boot() -> None:
    """The names secret keys used to read are exactly the ones a boot refuses."""
    from gaggiclanker.settings import RETIRED_AUTH_ENV_KEYS

    for name in ("GAGGICLANKER_LLM_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert name in RETIRED_AUTH_ENV_KEYS


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
    assert await service.get("deviceCleanupKeepNewest") == 50
    await service.apply({"deviceCleanupKeepNewest": 30})
    assert await service.get("deviceCleanupKeepNewest") == 30


async def test_rejected_value_is_never_echoed_back(client: httpx.AsyncClient) -> None:
    """int('sk-live-...') raises with the string in its message.

    Passing that ValueError's text through to error.details put whatever the
    user typed into the response body — and a settings PATCH is exactly where
    someone pastes an API key into the wrong field.
    """
    leaked = "sk-live-DO-NOT-ECHO-THIS"
    response = await client.patch("/api/settings", json={"deviceCleanupKeepNewest": leaked})
    assert response.status_code == 400
    assert leaked not in response.text
    details = response.json()["error"]["details"]
    assert details == [{"field": "deviceCleanupKeepNewest", "message": "expected an integer"}]


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("deviceCleanupKeepNewest", "nope", "expected an integer"),
        ("deviceCleanupKeepNewest", True, "expected an integer"),
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
