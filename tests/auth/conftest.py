"""Fixtures for the auth suite: an app with auth on, and a client holding a token.

Auth is switched on through the *database*, because that is the only place it
can be configured: the environment carries no sign-in settings at all, and a
boot that finds one of the old variables set refuses to start. The fixtures
store the user and the hash through the settings service, the same way
``POST /api/auth/password`` and the Settings page do.

The password hash is computed once for the whole session. argon2 is deliberately
slow — that is the point of it — and hashing per test would add a visible second
to the suite for no coverage at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.auth.passwords import hash_password
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app

USERNAME = "barista"
PASSWORD = "a-long-enough-passphrase"


@pytest.fixture(scope="session")
def password_hash() -> str:
    return hash_password(PASSWORD)


async def enable_auth(app: FastAPI, password_hash: str, user: str = USERNAME) -> None:
    """Store a user and a password hash, the way the Settings page ends up doing it.

    The hash first and the user second, the order the page asks for: the user is
    what turns the lock.
    """
    settings = app.state.settings_service
    await settings.store("authPasswordHash", password_hash)
    await settings.store("authUser", user)


@pytest.fixture
async def auth_configured(env: EnvSettings, password_hash: str) -> None:
    """A data directory whose database already has auth on, for a test that boots its own app.

    One throwaway boot stores the credentials; the test's own app — built from
    ``env`` or from ``make_env``, which share the data directory — then starts
    with auth on from its first request.
    """
    async with running_app(env) as (app, _client):
        await enable_auth(app, password_hash)


@pytest.fixture
async def secured(
    env: EnvSettings, auth_configured: None
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """An app with auth enabled, and an *unauthenticated* client for it."""
    async with running_app(env) as pair:
        yield pair


@pytest.fixture
async def secured_client(secured: tuple[FastAPI, httpx.AsyncClient]) -> httpx.AsyncClient:
    return secured[1]


async def sign_in(
    client: httpx.AsyncClient, username: str = USERNAME, password: str = PASSWORD
) -> httpx.Response:
    return await client.post("/api/auth/login", json={"username": username, "password": password})


@pytest.fixture
async def token(secured_client: httpx.AsyncClient) -> str:
    response = await sign_in(secured_client)
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["token"])


@pytest.fixture
def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
