"""Sign in, sign out, and every way sign-in is refused."""

from __future__ import annotations

import time

import httpx
import jwt
from fastapi import FastAPI

from gaggiclanker.auth.passwords import hash_password, verify_password
from gaggiclanker.auth.service import (
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    LOCKOUT_SECONDS,
    MAX_LOGIN_FAILURES,
    AuthService,
    LoginThrottle,
)
from gaggiclanker.db.repos.auth import AuthSessionsRepository, RuntimeSecretsRepository
from gaggiclanker.settings import EnvSettings
from tests.auth.conftest import PASSWORD, USERNAME, sign_in
from tests.conftest import running_app


async def test_login_returns_a_token_and_the_user(secured_client: httpx.AsyncClient) -> None:
    response = await sign_in(secured_client)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user"] == USERNAME
    assert data["expires_in"] == 2592000
    assert data["token"].count(".") == 2


async def test_the_token_opens_a_guarded_route(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    assert (await secured_client.get("/api/shots")).status_code == 401
    assert (await secured_client.get("/api/shots", headers=bearer)).status_code == 200


async def test_a_wrong_password_is_401_and_says_nothing_useful(
    secured_client: httpx.AsyncClient,
) -> None:
    response = await sign_in(secured_client, password="not-it")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    # The same message as a wrong username: naming which half was wrong halves
    # the work of guessing the other half.
    assert body["error"]["message"] == "Invalid username or password"


async def test_a_wrong_username_is_refused_the_same_way(
    secured_client: httpx.AsyncClient,
) -> None:
    response = await sign_in(secured_client, username="somebody-else")
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid username or password"


async def test_five_failures_lock_the_client_out_for_a_minute(
    secured_client: httpx.AsyncClient,
) -> None:
    for _ in range(MAX_LOGIN_FAILURES):
        assert (await sign_in(secured_client, password="wrong")).status_code == 401

    # The sixth is refused before the credentials are even looked at, which is
    # why the *right* password is used here.
    response = await sign_in(secured_client)
    assert response.status_code == 429
    body = response.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert 0 < body["error"]["details"]["retry_after_seconds"] <= LOCKOUT_SECONDS
    assert response.headers["retry-after"] == str(body["error"]["details"]["retry_after_seconds"])


async def test_a_successful_login_clears_the_failure_count(
    secured_client: httpx.AsyncClient,
) -> None:
    for _ in range(MAX_LOGIN_FAILURES - 1):
        await sign_in(secured_client, password="wrong")
    assert (await sign_in(secured_client)).status_code == 200
    for _ in range(MAX_LOGIN_FAILURES - 1):
        assert (await sign_in(secured_client, password="wrong")).status_code == 401
    # Still not locked: the counter went back to zero on the success above.
    assert (await sign_in(secured_client)).status_code == 200


def test_the_throttle_extends_its_own_lockout_on_every_further_guess() -> None:
    """A guesser must not get a fresh allowance a minute after its *first* try."""
    throttle = LoginThrottle()
    for _ in range(MAX_LOGIN_FAILURES):
        throttle.record_failure("10.0.0.9", now=0.0)
    assert throttle.retry_after("10.0.0.9", now=59.0) == 1

    throttle.record_failure("10.0.0.9", now=59.0)
    assert throttle.retry_after("10.0.0.9", now=60.5) > 0

    # And it does eventually expire.
    assert throttle.retry_after("10.0.0.9", now=200.0) == 0


def test_the_throttle_is_per_client() -> None:
    throttle = LoginThrottle()
    for _ in range(MAX_LOGIN_FAILURES):
        throttle.record_failure("10.0.0.9", now=0.0)
    assert throttle.retry_after("10.0.0.9", now=1.0) > 0
    assert throttle.retry_after("10.0.0.10", now=1.0) == 0


async def test_logout_revokes_the_session_immediately(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    assert (await secured_client.get("/api/shots", headers=bearer)).status_code == 200

    response = await secured_client.post("/api/auth/logout", headers=bearer)
    assert response.status_code == 200
    assert response.json()["data"]["revoked"] is True

    # Same token, same signature, same unexpired `exp` — and refused, which is
    # the whole point of the session row.
    assert (await secured_client.get("/api/shots", headers=bearer)).status_code == 401


async def test_logout_needs_a_token_of_its_own(secured_client: httpx.AsyncClient) -> None:
    """Revoking a session is something only its holder may ask for."""
    assert (await secured_client.post("/api/auth/logout")).status_code == 401


async def test_logging_out_twice_is_not_an_error(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    assert (await secured_client.post("/api/auth/logout", headers=bearer)).status_code == 200
    # The token is revoked now, so the guard refuses the second call. That is
    # the guard doing its job, not logout being non-idempotent: `AuthService.logout`
    # itself returns False rather than raising.
    assert (await secured_client.post("/api/auth/logout", headers=bearer)).status_code == 401


async def test_a_tampered_token_is_refused(secured_client: httpx.AsyncClient, token: str) -> None:
    forged = jwt.encode(
        {"sub": USERNAME, "jti": "made-up", "exp": 2**31}, "a" * 48, algorithm=JWT_ALGORITHM
    )
    response = await secured_client.get("/api/shots", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_a_token_whose_session_row_is_missing_is_refused(
    secured: tuple[FastAPI, httpx.AsyncClient], bearer: dict[str, str]
) -> None:
    """A restored backup from before the session existed, or a hand-deleted row."""
    app, client = secured
    await app.state.db.execute("DELETE FROM auth_sessions")
    assert (await client.get("/api/shots", headers=bearer)).status_code == 401


async def test_an_expired_token_is_refused(
    secured: tuple[FastAPI, httpx.AsyncClient], bearer: dict[str, str], token: str
) -> None:
    app, client = secured
    claims = jwt.decode(token, options={"verify_signature": False})
    # Expire the JWT and the row together, the way the passage of time would.
    await app.state.db.execute(
        "UPDATE auth_sessions SET expires_at = ? WHERE id = ?",
        (int(time.time()) - 1, claims["jti"]),
    )
    expired = jwt.encode(
        {**claims, "exp": int(time.time()) - 1},
        await app.state.auth.secret(),
        algorithm=JWT_ALGORITHM,
    )
    assert (
        await client.get("/api/shots", headers={"Authorization": f"Bearer {expired}"})
    ).status_code == 401


async def test_basic_auth_is_not_accepted(secured_client: httpx.AsyncClient) -> None:
    """The feature is colloquially "basic auth"; the wire protocol is not."""
    response = await secured_client.get(
        "/api/shots", headers={"Authorization": "Basic YmFyaXN0YTpzZWNyZXQ="}
    )
    assert response.status_code == 401


async def test_renaming_the_user_invalidates_tokens_issued_to_the_old_name(
    secured: tuple[FastAPI, httpx.AsyncClient],
    bearer: dict[str, str],
) -> None:
    app, client = secured
    # Stored behind the settings API's back, so the session revocation a PATCH
    # does cannot be what refuses the token: the subject check has to.
    await app.state.settings_service.store("authUser", "someone-else")
    assert (await client.get("/api/shots", headers=bearer)).status_code == 401


async def test_login_is_refused_when_auth_is_not_configured(
    client: httpx.AsyncClient,
) -> None:
    response = await sign_in(client)
    assert response.status_code == 401
    assert "not enabled" in response.json()["error"]["message"]


async def test_the_signing_secret_is_generated_once_and_kept(
    secured: tuple[FastAPI, httpx.AsyncClient],
    token: str,
) -> None:
    """`token` forces a login, which is what mints the secret the first time."""
    app, _client = secured
    stored = await RuntimeSecretsRepository(app.state.db).get(JWT_SECRET_KEY)
    assert stored
    assert len(stored) >= 32
    # A second service against the same database reads the same secret rather
    # than minting one, which is what keeps sessions alive across a restart.
    twin = AuthService(app.state.db, app.state.settings_service)
    assert await twin.secret() == stored


async def test_an_unusable_password_hash_fails_closed(
    env: EnvSettings,
) -> None:
    """Somebody puts the password into the row that wants its hash.

    This used to blank the hash, which read as "auth not configured" and opened
    every route to everybody — a configuration mistake taking the lock off the
    door. Auth stays ON, nobody signs in, and the log says what to do.
    """
    async with running_app(env) as (app, client):
        # Straight into the table: the settings service's own validation would
        # refuse it, which is exactly why the only way to get here is by hand.
        await app.state.settings_service.repo.set("authPasswordHash", "hunter2")
        await app.state.settings_service.store("authUser", USERNAME)
        config = await app.state.auth.config()
        assert config.enabled is True
        assert config.usable is False

        # Every route is still shut.
        assert (await client.get("/api/shots")).status_code == 401
        assert (await client.get("/api/openapi.json")).status_code == 401
        assert (await client.get("/health")).status_code == 200

        # And no password opens it, least of all the one that was pasted in.
        for attempt in ("hunter2", PASSWORD, ""):
            response = await sign_in(client, password=attempt or "x")
            assert response.status_code == 401
        assert "not an argon2 hash" in response.json()["error"]["message"]

        # The refusal is not a guess, so it must not spend the throttle: the
        # operator gets to keep trying while they fix it.
        assert app.state.auth.throttle.retry_after("testclient") == 0

        # `auth_required` is what the sign-in page reads, and it is true.
        status = (await client.get("/api/auth/status")).json()["data"]
        assert status["auth_required"] is True
        assert status["authenticated"] is False


async def test_expired_sessions_are_swept_at_boot(
    env: EnvSettings, auth_configured: None, password_hash: str
) -> None:
    async with running_app(env) as (app, _client):
        sessions = AuthSessionsRepository(app.state.db)
        await sessions.create(
            jti="stale", subject=USERNAME, expires_at=int(time.time()) - 10, user_agent="x"
        )
        await sessions.create(
            jti="live", subject=USERNAME, expires_at=int(time.time()) + 1000, user_agent="x"
        )
        assert await AuthService(app.state.db, app.state.settings_service).cleanup() == 1
        assert await sessions.get("stale") is None
        assert await sessions.get("live") is not None


def test_verify_password_never_raises_on_rubbish() -> None:
    assert verify_password("", "x") is False
    assert verify_password("not-a-hash", "x") is False
    assert verify_password(hash_password("right"), "wrong") is False
    assert verify_password(hash_password("right"), "right") is True
