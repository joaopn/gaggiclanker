"""The rest of the hardening: headers, body limits, the log sanitiser, the rate limit."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import structlog
from fastapi import FastAPI

from gaggiclanker.infra.logging import configure_logging
from gaggiclanker.infra.ratelimit import ANALYSIS_RATE_LIMIT, RateLimiter
from gaggiclanker.infra.redact import REDACTED, redact_value
from gaggiclanker.infra.security import (
    DEFAULT_MAX_BODY_BYTES,
    SECURITY_HEADERS,
    UPLOAD_MAX_BODY_BYTES,
    limit_for_path,
)
from tests.auth.conftest import PASSWORD, USERNAME, sign_in

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/health"),
        ("GET", "/api/shots"),
        ("GET", "/api/nope"),  # a 404 envelope
        ("GET", "/api/openapi.json"),
    ],
)
async def test_every_response_carries_the_security_headers(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path)
    for name, value in SECURITY_HEADERS:
        assert response.headers[name] == value


async def test_a_401_carries_them_too(secured_client: httpx.AsyncClient) -> None:
    """The error paths are where a header is most often quietly missing."""
    response = await secured_client.get("/api/shots")
    assert response.status_code == 401
    for name, value in SECURITY_HEADERS:
        assert response.headers[name] == value


async def test_the_request_id_is_echoed_and_matches_the_envelope(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/shots", headers={"x-request-id": "chosen-by-the-client"})
    assert response.headers["x-request-id"] == "chosen-by-the-client"
    assert response.json()["meta"]["request_id"] == "chosen-by-the-client"


# ---------------------------------------------------------------------------
# CORS is off unless asked for
# ---------------------------------------------------------------------------


async def test_no_cors_headers_by_default(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/shots", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers


async def test_cors_headers_appear_when_an_origin_is_configured(
    make_env: object,
) -> None:
    from tests.conftest import running_app

    env = make_env(CORS_ORIGINS="http://localhost:5173")  # type: ignore[operator]
    async with running_app(env) as (_app, client):
        response = await client.get("/api/shots", headers={"Origin": "http://localhost:5173"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_a_guard_401_carries_cors_headers_too(make_env: object, auth_env: None) -> None:
    """Otherwise the dev SPA sees a network error instead of "sign in again".

    A browser will not hand a cross-origin response to JavaScript without an
    `Access-Control-Allow-Origin` on it, whatever the status is. With
    `CORSMiddleware` inside the guard, the guard's own 401 came back bare, the
    Vite dev server's SPA could not read it, and `fetchApi`'s UNAUTHORIZED
    branch — the one that redirects to `/sign-in` — never ran.
    """
    from tests.conftest import running_app

    env = make_env(CORS_ORIGINS="http://localhost:5173")  # type: ignore[operator]
    async with running_app(env) as (_app, client):
        response = await client.get("/api/shots", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        # The rest of the stack is still outside it.
        assert response.headers["x-request-id"]
        assert response.headers["x-content-type-options"] == "nosniff"


async def test_a_preflight_is_answered_without_a_token(make_env: object, auth_env: None) -> None:
    """A preflight carries no credentials by definition; refusing it just stops
    the real (guarded) request from ever being sent."""
    from tests.conftest import running_app

    env = make_env(CORS_ORIGINS="http://localhost:5173")  # type: ignore[operator]
    async with running_app(env) as (_app, client):
        response = await client.request(
            "OPTIONS",
            "/api/shots",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


# ---------------------------------------------------------------------------
# Body limits
# ---------------------------------------------------------------------------


def test_the_upload_route_gets_the_bigger_limit() -> None:
    assert limit_for_path("/api/settings") == DEFAULT_MAX_BODY_BYTES
    assert limit_for_path("/api/import") == UPLOAD_MAX_BODY_BYTES
    assert limit_for_path("/api/import/files") == UPLOAD_MAX_BODY_BYTES


async def test_an_oversized_json_body_is_413_in_the_envelope(
    client: httpx.AsyncClient,
) -> None:
    body = json.dumps({"gaggimateHost": "x" * (DEFAULT_MAX_BODY_BYTES + 1)})
    response = await client.patch(
        "/api/settings", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert payload["error"]["details"]["limit_bytes"] == DEFAULT_MAX_BODY_BYTES


async def test_a_body_under_the_limit_is_untouched(client: httpx.AsyncClient) -> None:
    response = await client.patch("/api/settings", json={"gaggimateHost": "10.0.0.5"})
    assert response.status_code == 200


async def test_a_chunked_body_over_the_limit_is_refused_too(
    client: httpx.AsyncClient,
) -> None:
    """No Content-Length to check, so the counting half of the guard has to work."""

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(3):
            yield b"x" * (DEFAULT_MAX_BODY_BYTES // 2)

    response = await client.patch(
        "/api/settings",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


async def test_the_body_limit_runs_before_the_guard(
    secured_client: httpx.AsyncClient,
) -> None:
    """A 100 MB login attempt must not cost an argon2 hash to refuse."""
    body = json.dumps({"username": "x", "password": "y" * (DEFAULT_MAX_BODY_BYTES + 1)})
    response = await secured_client.post(
        "/api/auth/login", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


# ---------------------------------------------------------------------------
# The log sanitiser
# ---------------------------------------------------------------------------


def test_redaction_by_key_and_by_shape() -> None:
    assert redact_value({"password": "hunter2"}) == {"password": REDACTED}
    assert redact_value({"apiKey": "sk-live-1"}) == {"apiKey": REDACTED}
    assert redact_value({"Authorization": "Bearer abc"}) == {"Authorization": REDACTED}
    assert redact_value({"anthropic_api_key": "x"}) == {"anthropic_api_key": REDACTED}
    # By shape, under an innocent name.
    assert redact_value({"header": "Bearer abc"}) == {"header": REDACTED}
    assert redact_value({"stored": "$argon2id$v=19$..."}) == {"stored": REDACTED}
    # Nested, which is the case that actually happens.
    assert redact_value({"body": {"password": "x", "user": "b"}}) == {
        "body": {"password": REDACTED, "user": "b"}
    }
    assert redact_value({"creds": [{"token": "t"}]}) == {"creds": [{"token": REDACTED}]}


def test_a_list_of_setting_names_survives() -> None:
    """`settings_updated keys=["llmApiKey"]` is the audit line, not a secret.

    The rule is on the *field name* and on the value's *shape*; a list of names
    is neither, and redacting it would destroy the record of what changed while
    protecting nothing.
    """
    assert redact_value({"keys": ["llmApiKey", "authPasswordHash"]}) == {
        "keys": ["llmApiKey", "authPasswordHash"]
    }


def test_a_bare_string_is_left_alone() -> None:
    assert redact_value({"event": "auth_login_failed"}) == {"event": "auth_login_failed"}


async def test_a_login_attempt_never_logs_the_password(
    secured_client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("info", json_output=True)
    try:
        await sign_in(secured_client, password="a-password-nobody-should-see")
        await sign_in(secured_client, password=PASSWORD)
        logged = capsys.readouterr().out
    finally:
        configure_logging("warning", json_output=True)
    assert "a-password-nobody-should-see" not in logged
    assert PASSWORD not in logged
    # And the attempt *was* logged — otherwise this passes by logging nothing.
    assert "auth_login_failed" in logged


async def test_a_settings_patch_never_logs_the_key(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("info", json_output=True)
    try:
        response = await client.patch("/api/settings", json={"llmApiKey": "sk-do-not-log-me"})
        logged = capsys.readouterr().out
    finally:
        configure_logging("warning", json_output=True)
    assert response.status_code == 200
    assert "sk-do-not-log-me" not in logged
    # The name of the setting that changed is the point of the audit line and
    # stays; only values are secret.
    assert "llmApiKey" in logged


def test_the_sanitiser_is_installed_in_the_real_chain(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("info", json_output=True)
    try:
        structlog.get_logger("test").info("probe", password="hunter2", note="kept")
        logged = capsys.readouterr().out
    finally:
        configure_logging("warning", json_output=True)
    assert "hunter2" not in logged
    assert REDACTED in logged
    assert "kept" in logged


# ---------------------------------------------------------------------------
# The rate limit on the routes that spend money
# ---------------------------------------------------------------------------


def test_the_limiter_slides_rather_than_resetting_on_a_boundary() -> None:
    limiter = RateLimiter()
    for _ in range(3):
        limiter.check("b", "caller", limit=3, window=60.0)
    with pytest.raises(Exception, match="Rate limit"):
        limiter.check("b", "caller", limit=3, window=60.0)
    # A different caller has its own allowance.
    limiter.check("b", "someone-else", limit=3, window=60.0)


async def test_the_analysis_route_is_rate_limited(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Eleven requests in a minute; the eleventh is a 429 envelope.

    It runs against a shot that does not exist, so nothing is queued and no
    provider is contacted — the point is that the limit is checked before the
    handler, which is what stops a loop from spending money.
    """
    seen: list[int] = []
    for _ in range(ANALYSIS_RATE_LIMIT + 1):
        response = await client.post("/api/shots/999999/analyses", json={})
        seen.append(response.status_code)
    assert seen[-1] == 429
    assert seen.count(429) == 1
    body = (await client.post("/api/shots/999999/analyses", json={})).json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert body["error"]["details"]["limit"] == ANALYSIS_RATE_LIMIT


async def test_both_analysis_routes_share_one_bucket(client: httpx.AsyncClient) -> None:
    """Alternating between them must not buy twice the provider spend."""
    for _ in range(ANALYSIS_RATE_LIMIT):
        await client.post("/api/shots/999999/analyses", json={})
    assert (await client.post("/api/sets/999999/analyse", json={})).status_code == 429


async def test_the_limit_is_per_user_when_auth_is_on(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    for _ in range(ANALYSIS_RATE_LIMIT):
        await secured_client.post("/api/shots/999999/analyses", json={}, headers=bearer)
    over = await secured_client.post("/api/shots/999999/analyses", json={}, headers=bearer)
    assert over.status_code == 429
    assert over.headers["retry-after"]


# ---------------------------------------------------------------------------
# The device is still read-only
# ---------------------------------------------------------------------------


def test_the_device_client_surface_is_still_read_only() -> None:
    """A second copy of `tests/device/test_public_surface.py`'s question.

    The release checklist asks it again rather than trusting that the earlier
    test is still being run, because "the prototype writes nothing to the
    machine" is the claim the README makes to whoever installs this.
    """
    import inspect

    from gaggiclanker.device.client import READ_ONLY_METHODS, GaggimateClient

    public = {
        name
        for name, value in inspect.getmembers(GaggimateClient)
        if not name.startswith("_") and inspect.isfunction(value)
    }
    lifecycle = {"start", "stop", "wait_connected", "subscribe"}
    assert public == set(READ_ONLY_METHODS) | lifecycle
    assert all(not name.startswith(("save", "delete", "write", "set_")) for name in public)


def test_the_unauthenticated_surface_is_three_routes(app: FastAPI) -> None:
    """What a stranger on the LAN can reach with auth on, listed in one place."""
    from gaggiclanker.auth.guard import PUBLIC_API_PATHS, requires_auth

    reachable = {
        path
        for path in app.openapi()["paths"]
        if path.startswith("/api") and not requires_auth("GET", path)
    }
    assert reachable == set(PUBLIC_API_PATHS)
    assert USERNAME  # keep the import honest: the fixtures module is the source
