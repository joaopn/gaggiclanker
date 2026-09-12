"""The guard: one middleware in front of every ``/api/*`` request.

Pure ASGI, for the same two reasons ``RequestContextMiddleware`` is
(``gaggiclanker/infra/middleware.py``): ``BaseHTTPMiddleware`` buffers through an
anyio task group, which is death to an SSE stream, and this has to sit inside
the request context so a 401 still carries a request id.

**A middleware, not a router dependency.** A dependency has to be attached to
every router, and the failure mode of forgetting one is a route that is quietly
public — which is exactly the sort of thing that is noticed after it matters.
Here the rule is "under ``/api``", stated once, and
``tests/auth/test_guard.py`` enumerates every route in the application and
asserts each one answers 401 without a token.

What stays public, and why:

``/health``                 a healthcheck that needs a token is not a
                            healthcheck; it is how Docker decides the container
                            is alive.
``GET /api/auth/status``    the probe the sign-in page reads *before* it has a
                            token, so the UI can show a login form instead of
                            bouncing off a 401.
``POST /api/auth/login``    the door.
``OPTIONS``                 a CORS preflight carries no credentials by
                            definition; rejecting it makes the real request
                            never happen.
the SPA (GET/HEAD off       the bundle is public files; everything it can
``/api``)                   actually *do* is an API call, and those are guarded.

``/api/docs`` and ``/api/openapi.json`` are under ``/api`` and therefore need a
token too — deliberately: the schema lists every route and every field name.

``/mcp`` is named here as well. It is not under ``/api``, and the SPA
rule below lets an unauthenticated GET through — which would have made the
Streamable HTTP transport's own GET (the server-to-client event stream) a public
read of the whole archive. A prefix, not an exact match, because the transport
also answers ``DELETE /mcp`` and may grow sub-paths.
"""

from __future__ import annotations

import structlog
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from gaggiclanker.auth.service import AuthService
from gaggiclanker.infra.envelope import error_payload
from gaggiclanker.infra.errors import Unauthorized

__all__ = ["PUBLIC_API_PATHS", "AuthGuardMiddleware", "bearer_token"]

log = structlog.get_logger(__name__)

#: Reachable with no token, and the only paths under ``/api`` that are.
PUBLIC_API_PATHS: frozenset[str] = frozenset({"/api/auth/status", "/api/auth/login"})

#: Outside ``/api`` entirely, and public for every method.
_PUBLIC_PATHS: frozenset[str] = frozenset({"/health"})

#: Outside ``/api`` and guarded for every method. See the module docstring.
GUARDED_PREFIXES: tuple[str, ...] = ("/mcp",)

_BEARER = "bearer "


def bearer_token(headers: Headers) -> str:
    """The token from an ``Authorization: Bearer ...`` header, or ``""``.

    ``Basic`` is not accepted, even though the feature is colloquially "basic
    auth": the wire protocol is a bearer token, and quietly accepting a second
    credential format would mean a second code path to keep safe.
    """
    value = headers.get("authorization") or ""
    if value[: len(_BEARER)].lower() != _BEARER:
        return ""
    return value[len(_BEARER) :].strip()


def requires_auth(method: str, path: str) -> bool:
    """Whether this request needs a token, before we know if auth is even on."""
    if method == "OPTIONS":
        return False
    if path in _PUBLIC_PATHS:
        return False
    if path in PUBLIC_API_PATHS:
        return False
    if path == "/api" or path.startswith("/api/"):
        return True
    if any(path == prefix or path.startswith(f"{prefix}/") for prefix in GUARDED_PREFIXES):
        return True
    # The SPA and its assets. A GET is a file; anything else at a non-API path
    # is either the SPA fallback's 404 or an attempt at something, and both can
    # wait behind the token.
    return method not in {"GET", "HEAD"}


class AuthGuardMiddleware:
    """401s any request that needs a token and does not have a valid one."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", ""))
        path = str(scope.get("path", ""))
        if not requires_auth(method, path):
            await self.app(scope, receive, send)
            return

        auth: AuthService | None = getattr(scope["app"].state, "auth", None)
        if auth is None:  # pragma: no cover - no HTTP is served before the lifespan runs
            await self.app(scope, receive, send)
            return

        # Re-read per request rather than at startup: turning auth on from the
        # Settings page has to take effect immediately, because the person doing
        # it usually cannot restart the container.
        if not await auth.enabled():
            await self.app(scope, receive, send)
            return

        token = bearer_token(Headers(scope=scope))
        subject = await auth.verify(token)
        if subject is None:
            log.info("auth_rejected", path=path, method=method, had_token=bool(token))
            error = Unauthorized("Authentication required")
            response = JSONResponse(status_code=error.status, content=error_payload(error))
            await response(scope, receive, send)
            return

        # Downstream (the analysis rate limit) keys on the caller; with auth on
        # that is the user, not the proxy's idea of an address.
        scope["auth_subject"] = subject
        await self.app(scope, receive, send)
