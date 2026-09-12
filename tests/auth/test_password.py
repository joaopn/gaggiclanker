"""Changing the password, and the two ways it must not be changeable.

The hole this file exists for: `authPasswordHash` used to be an ordinary
secret in the registry, so the Settings page rendered it as a masked text box
labelled "Auth password hash". Typing the *password* into that box stored a
value argon2 cannot verify — and the old `config()` read that as "not
configured" and let every request through with no token. Three things now have
to hold, and each has a test below:

* the settings API refuses the key outright, naming the endpoint that owns it;
* that endpoint hashes on the server, so a browser never holds a hash at all;
* a value that is somehow stored anyway leaves auth **on** and shut.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.auth.passwords import hash_password, verify_password
from gaggiclanker.settings import SETTINGS_REGISTRY
from gaggiclanker.settings_service import READ_ONLY_MESSAGE
from tests.auth.conftest import PASSWORD, USERNAME, sign_in

NEW_PASSWORD = "a-completely-different-passphrase"


# ---------------------------------------------------------------------------
# The settings API will not take it
# ---------------------------------------------------------------------------


def test_the_hash_is_declared_read_only_and_validated() -> None:
    definition = SETTINGS_REGISTRY["authPasswordHash"]
    assert definition.readonly is True
    assert definition.secret is True
    assert definition.validate is not None
    assert definition.validate("hunter2")
    assert definition.validate(hash_password("x")) is None
    # Empty is how "no password" is spelled, and has to stay allowed.
    assert definition.validate("") is None


async def test_patching_the_hash_is_refused_and_names_the_endpoint(
    client: httpx.AsyncClient,
) -> None:
    response = await client.patch("/api/settings", json={"authPasswordHash": hash_password("x")})
    assert response.status_code == 400
    details = response.json()["error"]["details"]
    assert details == [{"field": "authPasswordHash", "message": READ_ONLY_MESSAGE}]


async def test_a_refused_patch_writes_nothing_at_all(client: httpx.AsyncClient) -> None:
    """One bad key fails the whole body — the registry's existing rule."""
    response = await client.patch(
        "/api/settings",
        json={"authUser": "barista", "authPasswordHash": "hunter2"},
    )
    assert response.status_code == 400
    settings = (await client.get("/api/settings")).json()["data"]
    assert settings["authUser"]["value"] == ""


async def test_the_hash_still_reports_its_state(client: httpx.AsyncClient) -> None:
    """Read-only is not invisible: the UI has to be able to say "a password is set"."""
    settings = (await client.get("/api/settings")).json()["data"]
    entry = settings["authPasswordHash"]
    assert entry["readonly"] is True
    assert entry["secret"] is True
    assert entry["configured"] is False
    assert "value" not in entry


async def test_an_ordinary_setting_is_not_read_only(client: httpx.AsyncClient) -> None:
    settings = (await client.get("/api/settings")).json()["data"]
    assert settings["gaggimateHost"]["readonly"] is False


# ---------------------------------------------------------------------------
# The endpoint that does own it
# ---------------------------------------------------------------------------


async def test_setting_a_password_on_an_open_server_turns_nothing_on(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """A password with no user configured is stored, and auth stays off.

    That ordering is what the Settings page uses: set the password, then the
    username. The reverse would lock the box the moment the username landed.
    """
    response = await client.post("/api/auth/password", json={"new_password": NEW_PASSWORD})
    assert response.status_code == 200
    assert response.json()["data"] == {"auth_required": False, "sessions_revoked": True}

    stored = await app.state.settings_service.get("authPasswordHash")
    assert verify_password(stored, NEW_PASSWORD)
    assert await app.state.auth.enabled() is False

    # And now the username switches it on.
    assert (await client.patch("/api/settings", json={"authUser": USERNAME})).status_code == 200
    assert await app.state.auth.enabled() is True
    assert (await client.get("/api/shots")).status_code == 401
    assert (await sign_in(client, password=NEW_PASSWORD)).status_code == 200


async def test_the_endpoint_needs_a_token_once_auth_is_on(
    secured_client: httpx.AsyncClient,
) -> None:
    response = await secured_client.post("/api/auth/password", json={"new_password": NEW_PASSWORD})
    assert response.status_code == 401


async def test_changing_the_password_needs_the_current_one(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    """The guard has already authenticated; this stops a borrowed tab locking its owner out."""
    without = await secured_client.post(
        "/api/auth/password", json={"new_password": NEW_PASSWORD}, headers=bearer
    )
    assert without.status_code == 401

    wrong = await secured_client.post(
        "/api/auth/password",
        json={"current_password": "not-it", "new_password": NEW_PASSWORD},
        headers=bearer,
    )
    assert wrong.status_code == 401
    assert "current password" in wrong.json()["error"]["message"]

    # And nothing changed.
    assert (await sign_in(secured_client)).status_code == 200


async def test_changing_the_password_revokes_every_session(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    """A password is changed because the old one may be known to somebody else."""
    assert (await secured_client.get("/api/shots", headers=bearer)).status_code == 200

    response = await secured_client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=bearer,
    )
    assert response.status_code == 200
    assert response.json()["data"]["sessions_revoked"] is True

    # The caller's own token included: the tokens are the credential now.
    assert (await secured_client.get("/api/shots", headers=bearer)).status_code == 401
    assert (await sign_in(secured_client, password=PASSWORD)).status_code == 401

    fresh = await sign_in(secured_client, password=NEW_PASSWORD)
    assert fresh.status_code == 200
    assert (
        await secured_client.get(
            "/api/shots",
            headers={"Authorization": f"Bearer {fresh.json()['data']['token']}"},
        )
    ).status_code == 200


async def test_a_short_new_password_is_refused(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    response = await secured_client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=bearer,
    )
    assert response.status_code == 400


async def test_the_new_password_is_never_echoed_or_logged(
    secured_client: httpx.AsyncClient, bearer: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    from gaggiclanker.infra.logging import configure_logging

    configure_logging("info", json_output=True)
    try:
        await secured_client.post(
            "/api/auth/password",
            json={"current_password": "wrong-on-purpose", "new_password": NEW_PASSWORD},
            headers=bearer,
        )
        logged = capsys.readouterr().out
    finally:
        configure_logging("warning", json_output=True)
    assert NEW_PASSWORD not in logged
    assert "wrong-on-purpose" not in logged


# ---------------------------------------------------------------------------
# Changing who may sign in
# ---------------------------------------------------------------------------


async def test_renaming_the_user_revokes_every_session(
    secured: tuple[FastAPI, httpx.AsyncClient], bearer: dict[str, str]
) -> None:
    app, client = secured
    assert (await client.get("/api/shots", headers=bearer)).status_code == 200

    response = await client.patch(
        "/api/settings", json={"authUser": "somebody-else"}, headers=bearer
    )
    assert response.status_code == 200

    assert (await client.get("/api/shots", headers=bearer)).status_code == 401
    # Not merely refused by the subject check — the row itself is gone.
    assert await app.state.auth.sessions.live_count(2**31) == 0


async def test_an_unrelated_patch_leaves_sessions_alone(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    response = await secured_client.patch(
        "/api/settings", json={"gaggimateHost": "10.0.0.5"}, headers=bearer
    )
    assert response.status_code == 200
    assert (await secured_client.get("/api/shots", headers=bearer)).status_code == 200
