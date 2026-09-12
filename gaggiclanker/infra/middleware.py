"""Request ids, the access log line, and the outermost error boundary.

Pure ASGI rather than ``BaseHTTPMiddleware``, for two reasons:

1. **The error boundary has to be inside the request context.** Starlette puts
   its ``ServerErrorMiddleware`` — where a handler registered for bare
   ``Exception`` lives — *outside* every application middleware. An unhandled
   exception therefore escapes ``BaseHTTPMiddleware`` before the handler runs,
   by which point the request-id context variable has been reset: the client
   got ``"request_id": "unknown"``, no ``x-request-id`` header, and no access
   log line for the one request that most needed one. Catching ``Exception``
   here, inside the context, fixes all three.
2. ``BaseHTTPMiddleware`` buffers through an anyio task group, which interferes
   with server-sent events — and the live shot view is an SSE stream
   that must not be held up.

``CancelledError`` is deliberately not caught: it is a ``BaseException``, and a
disconnected client is not an error to report.
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gaggiclanker.infra.envelope import error_payload
from gaggiclanker.infra.errors import to_app_error
from gaggiclanker.infra.request_context import request_context

__all__ = ["MAX_REQUEST_ID_LENGTH", "REQUEST_ID_HEADER", "RequestContextMiddleware"]

REQUEST_ID_HEADER = "x-request-id"

# A client-supplied id is echoed in a header and in the response body, so an
# unbounded caller-controlled string is a header-injection and log-flooding
# vector. Cap it and require it to be printable.
MAX_REQUEST_ID_LENGTH = 128

log = structlog.get_logger(__name__)


def _resolve_request_id(scope: Scope) -> str:
    incoming = (Headers(scope=scope).get(REQUEST_ID_HEADER) or "").strip()
    if incoming and incoming.isprintable():
        return incoming[:MAX_REQUEST_ID_LENGTH]
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Give every request an id, echo it, log it, and never let one escape untagged."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope)
        started = time.perf_counter()
        response_started = False
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started, status
            if message["type"] == "http.response.start":
                response_started = True
                status = int(message["status"])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with request_context(request_id):
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception as exc:
                log.exception("unhandled_exception", error_type=type(exc).__name__)
                if response_started:
                    # Headers are already on the wire; there is no envelope to
                    # send any more. Let it propagate so the server closes the
                    # connection rather than appending garbage to a half-sent
                    # body.
                    raise
                error = to_app_error(exc)
                status = error.status
                response = JSONResponse(
                    status_code=error.status,
                    content=error_payload(error),
                    headers={REQUEST_ID_HEADER: request_id},
                )
                await response(scope, receive, send)

            log.info(
                "http_request",
                method=scope.get("method", ""),
                path=scope.get("path", ""),
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
