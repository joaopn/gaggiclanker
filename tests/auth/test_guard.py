"""The guard, applied to the whole application rather than to a sample of it.

The acceptance criterion for the auth guard is
"every `/api/*` route returns 401 without a token (test enumerates the router)",
and that is what :func:`test_every_api_route_is_guarded` does — it enumerates
the application's own OpenAPI document rather than a hand-written list, so a
router added in a later chunk is covered on the day it is mounted and not on the
day somebody remembers.
"""

from __future__ import annotations

import re

import httpx
import pytest
from fastapi import FastAPI
from starlette.datastructures import Headers

from gaggiclanker.auth.guard import PUBLIC_API_PATHS, bearer_token, requires_auth
from gaggiclanker.settings import EnvSettings
from tests.auth.conftest import PASSWORD, USERNAME
from tests.device.conftest import serving

#: A path parameter's placeholder. Any value will do: the guard runs before the
#: route does, so the request never gets far enough for the id to matter.
_PARAM = re.compile(r"\{[^}]+\}")

#: Verbs FastAPI adds for free and that say nothing about the guard.
_IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})


def api_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, concrete path) under ``/api`` that the guard should cover.

    Read off the OpenAPI document rather than walked out of ``app.routes``:
    since FastAPI 0.141 an included router appears there as an opaque
    ``_IncludedRouter`` wrapper rather than as its routes, so a naive walk finds
    three routes and passes vacuously. The document is the same information in
    a shape that is part of the framework's contract — and every route in this
    application is in it (nothing under ``/api`` sets ``include_in_schema=False``).
    """
    found: list[tuple[str, str]] = []
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api") or path in PUBLIC_API_PATHS:
            continue
        for method in operations:
            if method.upper() in _IGNORED_METHODS:
                continue
            found.append((method.upper(), _PARAM.sub("1", path)))
    # The document does not carry the two routes FastAPI adds for itself, and
    # they are exactly the ones worth being sure about.
    found.append(("GET", "/api/openapi.json"))
    found.append(("GET", "/api/docs"))
    return found


def test_the_enumeration_finds_a_realistic_number_of_routes(app: FastAPI) -> None:
    """Guard against the walk above silently matching nothing."""
    routes = api_routes(app)
    assert len(routes) > 40
    assert ("GET", "/api/shots") in routes
    assert ("POST", "/api/shots/1/analyses") in routes


async def test_every_api_route_is_guarded(secured: tuple[FastAPI, httpx.AsyncClient]) -> None:
    app, client = secured
    leaked: list[tuple[str, str, int]] = []
    for method, path in api_routes(app):
        response = await client.request(method, path)
        if response.status_code != 401:
            leaked.append((method, path, response.status_code))
    assert not leaked, leaked


async def test_the_openapi_document_and_its_ui_need_a_token(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    """The schema lists every route and every field name; it is not public."""
    for path in ("/api/openapi.json", "/api/docs"):
        assert (await secured_client.get(path)).status_code == 401
        assert (await secured_client.get(path, headers=bearer)).status_code == 200


async def test_the_public_routes_stay_public(secured_client: httpx.AsyncClient) -> None:
    assert (await secured_client.get("/health")).status_code == 200
    assert (await secured_client.get("/api/auth/status")).status_code == 200
    # login is public by being reachable at all; the credentials decide the rest
    assert (await secured_client.post("/api/auth/login", json={})).status_code == 400


async def test_status_says_whether_auth_is_required(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    anonymous = (await secured_client.get("/api/auth/status")).json()["data"]
    assert anonymous == {"auth_required": True, "authenticated": False, "user": None}

    signed_in = (await secured_client.get("/api/auth/status", headers=bearer)).json()["data"]
    assert signed_in == {"auth_required": True, "authenticated": True, "user": USERNAME}


async def test_status_with_auth_off_reports_an_open_server(client: httpx.AsyncClient) -> None:
    data = (await client.get("/api/auth/status")).json()["data"]
    assert data == {"auth_required": False, "authenticated": True, "user": None}


async def test_nothing_is_guarded_when_auth_is_off(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/shots")).status_code == 200
    assert (await client.get("/api/openapi.json")).status_code == 200


async def test_turning_auth_on_takes_effect_without_a_restart(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, password_hash: str
) -> None:
    """The person flipping this switch usually cannot restart the container."""
    assert (await client.get("/api/shots")).status_code == 200
    monkeypatch.setenv("AUTH_USER", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD_HASH", password_hash)
    assert (await client.get("/api/shots")).status_code == 401


def test_requires_auth_covers_the_cases_it_is_written_for() -> None:
    assert requires_auth("GET", "/api/shots") is True
    assert requires_auth("GET", "/api/openapi.json") is True
    assert requires_auth("GET", "/api") is True
    assert requires_auth("GET", "/health") is False
    assert requires_auth("POST", "/api/auth/login") is False
    assert requires_auth("GET", "/api/auth/status") is False
    # A preflight carries no credentials by definition; rejecting it just stops
    # the real (guarded) request from ever being sent.
    assert requires_auth("OPTIONS", "/api/shots") is False
    # The SPA bundle.
    assert requires_auth("GET", "/shots/12") is False
    assert requires_auth("GET", "/assets/index-abc.js") is False
    # Anything else off /api still needs the token.
    assert requires_auth("POST", "/whatever") is True
    # A path that merely starts with the same letters is not under /api.
    assert requires_auth("GET", "/apiary") is False


def test_bearer_token_parsing() -> None:
    def header(value: str | None = None) -> Headers:
        return Headers({} if value is None else {"authorization": value})

    assert bearer_token(header("Bearer abc")) == "abc"
    assert bearer_token(header("bearer  abc  ")) == "abc"
    assert bearer_token(header("Basic abc")) == ""
    assert bearer_token(header("abc")) == ""
    assert bearer_token(header()) == ""


@pytest.mark.parametrize(
    "stream", ["/api/sync/events", "/api/device/live", "/api/llm/calls/stream"]
)
async def test_sse_streams_are_guarded_and_open_with_a_bearer(
    env: EnvSettings, auth_env: None, stream: str
) -> None:
    """A real socket: `httpx.ASGITransport` deadlocks on a stream that never ends.

    The important half is the second one. The front end reads SSE with `fetch`
    rather than `EventSource` precisely so it can send the header
    (`web/src/lib/sse.ts`), and if the guard rejected the streams there would be
    no live view at all with auth on.
    """
    async with serving(env) as (_app, base_url):
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            async with client.stream("GET", stream) as refused:
                assert refused.status_code == 401

            issued = await client.post(
                "/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
            )
            assert issued.status_code == 200, issued.text
            header = {"Authorization": f"Bearer {issued.json()['data']['token']}"}

            async with client.stream("GET", stream, headers=header) as opened:
                assert opened.status_code == 200
                assert opened.headers["content-type"].startswith("text/event-stream")
