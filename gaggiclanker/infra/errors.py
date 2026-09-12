"""The application error hierarchy.

Every failure that reaches the client is an :class:`AppError`. One exception
handler (``gaggiclanker.infra.envelope.register_exception_handlers``) turns it
into the response envelope, so routes and services raise rather than build
responses. Ported from cvclanker's ``infra/errors.ts``.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AppError",
    "BadRequest",
    "Conflict",
    "Forbidden",
    "GatewayTimeout",
    "InternalError",
    "MethodNotAllowed",
    "NotFound",
    "RequestTimeout",
    "ServiceUnavailable",
    "Unauthorized",
    "Unprocessable",
    "UpstreamError",
    "status_to_code",
    "to_app_error",
]

# The canonical status -> code mapping. Anything not listed is an internal
# error: a status we did not choose deliberately is a bug, not a contract.
_CODE_BY_STATUS: dict[int, str] = {
    400: "INVALID_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    408: "REQUEST_TIMEOUT",
    409: "CONFLICT",
    422: "UNPROCESSABLE_ENTITY",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


def status_to_code(status: int) -> str:
    """Map an HTTP status onto the envelope's error code."""
    return _CODE_BY_STATUS.get(status, "INTERNAL_ERROR")


class AppError(Exception):
    """An error with an HTTP status, a stable machine code and optional details.

    ``details`` is free-form JSON-serialisable context for the client (the
    per-field errors of a validation failure, the upstream body of a device
    call). It must never carry a secret: it is echoed verbatim in the response.
    """

    status: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        elif status is not None:
            self.code = status_to_code(status)
        self.details = details

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"{type(self).__name__}(status={self.status}, "
            f"code={self.code!r}, message={self.message!r})"
        )


class BadRequest(AppError):
    """The request is malformed or fails validation."""

    status = 400
    code = "INVALID_REQUEST"


class Unauthorized(AppError):
    """No credentials, or credentials that do not verify."""

    status = 401
    code = "UNAUTHORIZED"

    def __init__(self, message: str = "Authentication required", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class Forbidden(AppError):
    """Authenticated, but not allowed."""

    status = 403
    code = "FORBIDDEN"

    def __init__(self, message: str = "Forbidden", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class NotFound(AppError):
    """No such route or resource."""

    status = 404
    code = "NOT_FOUND"

    def __init__(self, message: str = "Not found", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class MethodNotAllowed(AppError):
    """The route exists but not for this verb."""

    status = 405
    code = "METHOD_NOT_ALLOWED"

    def __init__(self, message: str = "Method not allowed", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class RequestTimeout(AppError):
    """The *client* took too long to send its request."""

    status = 408
    code = "REQUEST_TIMEOUT"

    def __init__(self, message: str = "Request timed out", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class Conflict(AppError):
    """The request contradicts the current state (duplicate id, concurrent edit)."""

    status = 409
    code = "CONFLICT"


class Unprocessable(AppError):
    """Well-formed, but semantically impossible to carry out."""

    status = 422
    code = "UNPROCESSABLE_ENTITY"


class UpstreamError(AppError):
    """Something we depend on (the machine, an LLM provider) answered badly."""

    status = 502
    code = "UPSTREAM_ERROR"


class ServiceUnavailable(AppError):
    """A dependency is temporarily gone (device offline, OTA in progress)."""

    status = 503
    code = "SERVICE_UNAVAILABLE"


class GatewayTimeout(AppError):
    """Something we were waiting on took too long (the machine, an LLM provider)."""

    status = 504
    code = "GATEWAY_TIMEOUT"

    def __init__(self, message: str = "Upstream request timed out", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class InternalError(AppError):
    """An unexpected failure. The message is what the client sees."""

    status = 500
    code = "INTERNAL_ERROR"


def to_app_error(error: BaseException) -> AppError:
    """Coerce any exception into an :class:`AppError`.

    Unknown exceptions become a generic 500 whose message is deliberately
    opaque: an arbitrary ``str(exc)`` can carry a file path, a connection
    string or a token. The real exception is logged with its traceback.
    """
    if isinstance(error, AppError):
        return error
    if isinstance(error, TimeoutError):
        # 504, not 408: the client's request was fine — something we called
        # took too long. This is the generic fallback; with a device client, a device
        # fetch that times out raises ServiceUnavailable or GatewayTimeout with
        # a message naming the machine, rather than relying on this.
        return GatewayTimeout()
    return InternalError("Internal server error")
