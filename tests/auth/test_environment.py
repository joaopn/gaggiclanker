"""Credentials are configured in the database only, and the environment cannot touch them.

Two halves. The environment carries no credential at all — no auth or secret
registry key reads a variable, and the bootstrap settings have no auth field.
And because an install that used to configure sign-in through the environment
has its user and hash nowhere else, a boot that finds one of the old variables
set — a sign-in one or an LLM provider's key or token — refuses to start:
ignoring them would switch authentication off and open the app.
"""

from __future__ import annotations

import sqlite3

import pytest

from gaggiclanker.settings import (
    FORMER_SETTING_ENV_KEYS,
    RETIRED_AUTH_ENV_KEYS,
    SETTINGS_REGISTRY,
    EnvSettings,
    SettingDefinition,
    retired_auth_env_keys,
)
from tests.auth.conftest import sign_in
from tests.conftest import running_app

#: Something no log line or message could contain by accident.
SECRET_LOOKING_VALUE = "value-that-must-never-be-logged-7c1e"

NEW_PASSWORD = "the-password-after-recovery"
LOST_PASSWORD = "the-password-nobody-remembers"


# ---------------------------------------------------------------------------
# Nothing reads them
# ---------------------------------------------------------------------------


def test_no_auth_setting_reads_the_environment() -> None:
    auth_keys = [key for key in SETTINGS_REGISTRY if key.startswith("auth")]
    assert auth_keys == ["authUser", "authPasswordHash", "authTokenTtlSeconds"]
    # No registry key reads a variable at all now, auth or otherwise: the
    # declaration has nowhere to name one. The credential names are still
    # refused rather than merely inert, which is what the rest of this file is
    # about, and no former setting variable shares one of them.
    assert "env_key" not in SettingDefinition.__slots__
    assert {name.upper() for name in FORMER_SETTING_ENV_KEYS}.isdisjoint(
        {name.upper() for name in RETIRED_AUTH_ENV_KEYS}
    )


def test_the_bootstrap_settings_have_no_auth_field() -> None:
    for name, field in EnvSettings.model_fields.items():
        assert "auth" not in name.lower(), name
        choices = getattr(field.validation_alias, "choices", ())
        assert not any("AUTH" in str(choice).upper() for choice in choices), name


def test_empty_values_count_as_unset() -> None:
    environ = {"AUTH_USER": "", "AUTH_PASSWORD": "   "}
    dotenv: dict[str, str | None] = {"AUTH_PASSWORD_HASH": "", "AUTH_JWT_SECRET": None}
    assert retired_auth_env_keys(environ, dotenv) == []


def test_a_name_set_in_either_place_is_found_once() -> None:
    environ = {"AUTH_USER": "barista"}
    dotenv: dict[str, str | None] = {"AUTH_USER": "barista", "AUTH_TOKEN_TTL_S": "60"}
    assert retired_auth_env_keys(environ, dotenv) == ["AUTH_USER", "AUTH_TOKEN_TTL_S"]


# ---------------------------------------------------------------------------
# A boot that finds one refuses
# ---------------------------------------------------------------------------


def _assert_refused_by_name(
    name: str, caught: pytest.ExceptionInfo[RuntimeError], logged: str
) -> None:
    lines = [line for line in logged.splitlines() if "auth_env_refused" in line]
    assert len(lines) == 1, logged
    assert name in lines[0]
    assert "Settings" in str(caught.value)
    assert name in str(caught.value)
    assert SECRET_LOOKING_VALUE not in logged
    assert SECRET_LOOKING_VALUE not in str(caught.value)


@pytest.mark.parametrize("name", RETIRED_AUTH_ENV_KEYS)
async def test_each_retired_name_in_the_process_env_refuses_boot(
    name: str,
    env: EnvSettings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(name, SECRET_LOOKING_VALUE)
    with pytest.raises(RuntimeError) as caught:
        async with running_app(env):
            pass
    _assert_refused_by_name(name, caught, capsys.readouterr().out)
    # Refused before the database was created, let alone opened.
    assert not env.database_path.exists()


@pytest.mark.parametrize("name", RETIRED_AUTH_ENV_KEYS)
async def test_each_retired_name_in_dotenv_refuses_boot(
    name: str, env: EnvSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(RuntimeError) as caught:
        async with running_app(env, dotenv={name: SECRET_LOOKING_VALUE}):
            pass
    _assert_refused_by_name(name, caught, capsys.readouterr().out)


@pytest.mark.parametrize("name", ["auth_jwt_secret", "Openai_Api_Key", "claude_code_oauth_token"])
async def test_a_retired_name_in_another_case_refuses_boot(
    name: str,
    env: EnvSettings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """pydantic-settings read these in any case; dropping one silently would fail open."""
    monkeypatch.setenv(name, SECRET_LOOKING_VALUE)
    with pytest.raises(RuntimeError) as caught:
        async with running_app(env):
            pass
    _assert_refused_by_name(name, caught, capsys.readouterr().out)


PROXY_PASSWORD = "proxy-password-that-must-not-be-logged-91d"


@pytest.mark.parametrize("name", ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "https_proxy"])
async def test_a_proxy_with_credentials_in_the_process_env_refuses_boot(
    name: str,
    env: EnvSettings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(name, f"http://someone:{PROXY_PASSWORD}@proxy.test:3128")
    with pytest.raises(RuntimeError) as caught:
        async with running_app(env):
            pass
    logged = capsys.readouterr().out
    lines = [line for line in logged.splitlines() if "auth_env_refused" in line]
    assert len(lines) == 1 and name in lines[0]
    assert name in str(caught.value)
    assert PROXY_PASSWORD not in logged
    assert PROXY_PASSWORD not in str(caught.value)
    assert "proxy.test" not in str(caught.value)


async def test_a_proxy_with_credentials_in_dotenv_refuses_boot(env: EnvSettings) -> None:
    with pytest.raises(RuntimeError, match="HTTPS_PROXY"):
        async with running_app(env, dotenv={"HTTPS_PROXY": "user:pw@proxy.test:3128"}):
            pass


async def test_a_proxy_without_credentials_boots(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proxy is a route, not a credential: naming one is fine."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:3128")
    monkeypatch.setenv("http_proxy", "proxy.test:3128")
    async with running_app(env, dotenv={"ALL_PROXY": "socks5://proxy.test:1080"}) as (_app, client):
        assert (await client.get("/api/shots")).status_code == 200


def test_names_are_matched_without_regard_to_case_and_reported_as_spelled() -> None:
    environ = {"auth_user": "barista", "Https_Proxy": "http://u:p@proxy.test", "HTTP_PROXY": "p:1"}
    assert retired_auth_env_keys(environ, {}) == ["auth_user", "Https_Proxy"]


async def test_empty_retired_names_boot_normally(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old compose file passing `${AUTH_USER:-}` through must not brick a box."""
    for name in RETIRED_AUTH_ENV_KEYS:
        monkeypatch.setenv(name, "")
    dotenv: dict[str, str | None] = dict.fromkeys(RETIRED_AUTH_ENV_KEYS, "")
    async with running_app(env, dotenv=dotenv) as (_app, client):
        assert (await client.get("/api/shots")).status_code == 200
        status = (await client.get("/api/auth/status")).json()["data"]
        assert status["auth_required"] is False


# ---------------------------------------------------------------------------
# The lost-password recovery the README describes
# ---------------------------------------------------------------------------


async def test_deleting_the_hash_row_turns_auth_off_without_a_restart(env: EnvSettings) -> None:
    """Delete the `authPasswordHash` row, then set a new password in Settings.

    The deletion is made the way the README's one-liner makes it: from another
    process's connection to the same file, with the app still running.
    """
    async with running_app(env) as (_app, client):
        # Auth on, the way the Settings page turns it on.
        assert (
            await client.post("/api/auth/password", json={"new_password": LOST_PASSWORD})
        ).status_code == 200
        assert (await client.patch("/api/settings", json={"authUser": "barista"})).status_code == (
            200
        )
        assert (await client.get("/api/shots")).status_code == 401

        # The password is lost. Delete the stored hash out of band.
        connection = sqlite3.connect(env.database_path)
        try:
            connection.execute("DELETE FROM settings WHERE key='authPasswordHash'")
            connection.commit()
        finally:
            connection.close()

        # The very next request: auth is off, no restart.
        assert (await client.get("/api/shots")).status_code == 200
        status = (await client.get("/api/auth/status")).json()["data"]
        assert status["auth_required"] is False

        # Settings → Authentication sets a new one; no current password is asked for.
        response = await client.post("/api/auth/password", json={"new_password": NEW_PASSWORD})
        assert response.status_code == 200
        assert response.json()["data"]["auth_required"] is True

        assert (await client.get("/api/shots")).status_code == 401
        assert (await sign_in(client, "barista", LOST_PASSWORD)).status_code == 401
        issued = await sign_in(client, "barista", NEW_PASSWORD)
        assert issued.status_code == 200
        bearer = {"Authorization": f"Bearer {issued.json()['data']['token']}"}
        assert (await client.get("/api/shots", headers=bearer)).status_code == 200


async def test_a_signed_in_client_is_not_needed_to_start_the_recovery(
    env: EnvSettings,
) -> None:
    """The recovery must work for somebody with no session at all."""
    async with running_app(env) as (_app, client):
        await client.post("/api/auth/password", json={"new_password": LOST_PASSWORD})
        await client.patch("/api/settings", json={"authUser": "barista"})
    # A later boot, nobody signed in, the password forgotten.
    connection = sqlite3.connect(env.database_path)
    try:
        connection.execute("DELETE FROM settings WHERE key='authPasswordHash'")
        connection.commit()
    finally:
        connection.close()
    async with running_app(env) as (_app, client):
        assert (await client.get("/api/settings")).status_code == 200
