"""Two small pure-ASGI middlewares: a body-size limit and the security headers.

Both are here rather than in ``middleware.py`` because that module is about the
request id and the error boundary, and both of these are about what an
unauthenticated stranger on the LAN can do to the process before a route ever
sees them.

Pure ASGI for the reason the rest of this app's middleware is
(``infra/middleware.py``): ``BaseHTTPMiddleware`` buffers the response through a
task group, and the live views are SSE streams that must not be held up.
"""

from __future__ import annotations

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gaggiclanker.infra.envelope import error_payload
from gaggiclanker.infra.errors import PayloadTooLarge

__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "SECURITY_HEADERS",
    "UPLOAD_MAX_BODY_BYTES",
    "BodyLimitMiddleware",
    "SecurityHeadersMiddleware",
    "limit_for_path",
]

log = structlog.get_logger(__name__)

#: Every JSON body this API takes is a handful of fields. One megabyte is
#: already three orders of magnitude more than the largest of them (a prompt
#: edit) and small enough that a hostile client cannot make the process hold
#: anything interesting in memory.
DEFAULT_MAX_BODY_BYTES = 1024 * 1024

#: The importer's backstop, not its limit. ``POST /api/import`` has its own
#: 50 MB rule (``gaggiclanker.imports.service.MAX_EXPANDED_BYTES``) and a message
#: that names the offending file, and that is the one the user should see — so
#: this sits deliberately above it, with room for multipart framing on top of a
#: legitimate 50 MB payload. What it stops is a 500 MB upload being read into
#: memory at all before anything gets the chance to be helpful about it.
UPLOAD_MAX_BODY_BYTES = 64 * 1024 * 1024

#: Path prefixes that get the larger limit.
_UPLOAD_PREFIXES: tuple[str, ...] = ("/api/import",)

#: Applied to every response, including the SPA and an error envelope.
#:
#: There is no Content-Security-Policy here on purpose. The SPA is a Vite bundle
#: with its own hashed assets and no inline script, so a CSP would be easy to
#: write and easy to get subtly wrong on the next dependency that inlines a
#: style; the three headers below are the ones that pay for themselves with no
#: way to break the app.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    # The archive serves `.slog` bytes and JSON exports. Without this, a browser
    # that decides a downloaded file "looks like" HTML will render it.
    ("x-content-type-options", "nosniff"),
    # Nothing here is meant to be framed, and the API is same-origin with the
    # SPA, so a frame is only ever somebody else's idea.
    ("x-frame-options", "DENY"),
    # Shot ids and Set names are in the path. They do not belong in the Referer
    # header of an outbound link.
    ("referrer-policy", "no-referrer"),
)


def limit_for_path(path: str) -> int:
    """The body limit that applies to ``path``."""
    if path.startswith(_UPLOAD_PREFIXES):
        return UPLOAD_MAX_BODY_BYTES
    return DEFAULT_MAX_BODY_BYTES


class SecurityHeadersMiddleware:
    """Stamp :data:`SECURITY_HEADERS` on every response that has headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    # setdefault: a route that deliberately set its own (the
                    # binary download already sets nosniff) keeps it.
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodyLimitMiddleware:
    """Refuse a request body larger than the limit for its path.

    Two checks, because either alone has a hole. ``Content-Length`` is refused
    before a single byte is read, which is what makes a 200 MB upload cheap to
    reject — but it is absent on a chunked request, and it is a claim by the
    client either way. So the bytes are counted as they are consumed too, and
    the stream is cut short the moment it goes over.

    Cutting it short means sending ``http.disconnect`` downstream: the route is
    mid-parse and the honest thing to tell it is that the client went away.

    What the route then *does* about that is not something to let through.
    Starlette turns a disconnect mid-body into an empty body, FastAPI validates
    that and answers 400 — which would tell the caller their JSON was malformed
    when in fact it was simply too big. So once the limit is passed, downstream
    output is swallowed and this middleware sends the 413 itself. The swallow is
    conditional on nothing having been sent yet: a route that had already
    started streaming a response keeps it, because appending a second response
    to a half-sent one is worse than any status code.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = limit_for_path(str(scope.get("path", "")))
        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._refuse(scope, receive, send, limit)
            return

        received = 0
        over = False

        async def limited_receive() -> Message:
            nonlocal received, over
            if over:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    over = True
                    return {"type": "http.disconnect"}
            return message

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if over and not response_started:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        await self.app(scope, limited_receive, send_wrapper)
        if over and not response_started:
            await self._refuse(scope, receive, send, limit)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        megabytes = limit // (1024 * 1024)
        log.warning("request_body_too_large", path=scope.get("path", ""), limit_bytes=limit)
        error = PayloadTooLarge(
            f"Request body exceeds the {megabytes} MB limit for this endpoint",
            details={"limit_bytes": limit},
        )
        response = JSONResponse(status_code=error.status, content=error_payload(error))
        await response(scope, receive, send)
