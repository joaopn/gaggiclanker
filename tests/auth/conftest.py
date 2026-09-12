"""Fixtures for the auth suite: an app with auth on, and a client holding a token.

Auth is switched on through the *environment*, not through a PATCH, because
that is the path the README documents and the one an operator actually uses.
The registry resolves ``AUTH_USER`` and ``AUTH_PASSWORD_HASH`` from
``os.environ`` (``SettingsService._raw_env``), so ``monkeypatch.setenv`` is
enough and works for the in-process client and the real-socket one alike.

The password hash is computed once for the whole session. argon2 is deliberately
slow — that is the point of it — and hashing per test would add a visible second
to the suite for no coverage at all; the one test that cares about hashing
(``test_bootstrap``) does its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

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


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch, password_hash: str) -> Iterator[None]:
    """Turn auth on for this test, the way the environment does in production."""
    monkeypatch.setenv("AUTH_USER", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD_HASH", password_hash)
    yield


@pytest.fixture
async def secured(
    env: EnvSettings, auth_env: None
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
