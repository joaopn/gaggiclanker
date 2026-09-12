"""The single response shape every API route answers in.

    {"ok": true,  "data": <payload>,                     "meta": {"request_id": "..."}}
    {"ok": false, "error": {"code", "message", "details"}, "meta": {"request_id": "..."}}

Routes return ``data`` and FastAPI wraps it (see :func:`envelope_response`), or
raise an :class:`~gaggiclanker.infra.errors.AppError` and the registered
handlers wrap that. Nothing writes a bare ``JSONResponse``; ``tests/test_api_contract.py``
pins the shape and greps the routers to keep it that way.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from gaggiclanker.infra.errors import AppError, status_to_code, to_app_error
from gaggiclanker.infra.request_context import get_request_id

__all__ = [
    "ApiError",
    "ApiMeta",
    "ApiResponse",
    "binary_response",
    "envelope_response",
    "error_payload",
    "register_exception_handlers",
    "success_payload",
]

log = structlog.get_logger(__name__)


class ApiMeta(BaseModel):
    """Envelope metadata. Always carries the request id the server logged under."""

    request_id: str


class ApiError(BaseModel):
    """The error half of the envelope."""

    code: str
    message: str
    details: Any = None


class ApiResponse[T](BaseModel):
    """The envelope, as documented in OpenAPI.

    Routes return :func:`envelope_response`, which builds the same shape by
    hand; this model exists so the schema (and therefore the generated
    front-end client) knows what comes back.
    """

    ok: bool
    data: T | None = None
    error: ApiError | None = None
    meta: ApiMeta = Field(...)


def _meta() -> dict[str, Any]:
    return {"request_id": get_request_id() or "unknown"}


def success_payload(data: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the success envelope around ``data``."""
    return {"ok": True, "data": data, "meta": {**_meta(), **(meta or {})}}


def error_payload(error: AppError, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the failure envelope around ``error``."""
    body: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.details is not None:
        body["details"] = jsonable_encoder(error.details)
    return {"ok": False, "error": body, "meta": {**_meta(), **(meta or {})}}


def envelope_response(
    data: Any, status_code: int = 200, meta: dict[str, Any] | None = None
) -> JSONResponse:
    """A ``JSONResponse`` carrying the success envelope."""
    return JSONResponse(
        status_code=status_code, content=jsonable_encoder(success_payload(data, meta))
    )


def binary_response(
    body: bytes, *, media_type: str, filename: str, request_id: str | None = None
) -> Response:
    """The one response shape that is deliberately **not** the envelope: a file.

    `GET /api/shots/{id}/raw` hands back the `.slog` bytes the machine wrote.
    Those bytes are the archive's product — the thing every derived column can
    be rebuilt from — and wrapping them in JSON would mean base64 and a client
    that has to decode before it can hash them against the device's copy.

    It lives here rather than in the router because the rule stands: routers do
    not build responses by hand (``tests/test_api_contract.py`` greps for it).
    The exception is one function, in the module that owns the contract, with
    the reason written down.

    The request id still travels, in the header, so a download that goes wrong
    is as traceable as any other call.
    """
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    }
    if request_id:
        headers["x-request-id"] = request_id
    return Response(content=body, media_type=media_type, headers=headers)


def _fail(error: AppError) -> JSONResponse:
    return JSONResponse(status_code=error.status, content=error_payload(error))


def _validation_details(exc: RequestValidationError | ValidationError) -> list[dict[str, Any]]:
    """Flatten pydantic errors into ``[{field, message, type}]``.

    ``loc`` is a tuple like ``("body", "profile", 0, "pump")``; the leading
    location kind ("body"/"query"/"path") is kept so the client can tell a bad
    query string from a bad body. ``ctx`` and ``input`` are dropped: ``input``
    echoes whatever the caller sent, which for a settings PATCH is a secret.
    """
    details: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        details.append(
            {"field": loc, "message": err.get("msg", "invalid value"), "type": err.get("type", "")}
        )
    return details


def register_exception_handlers(app: FastAPI) -> None:
    """Install the four handlers that keep every error inside the envelope."""

    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: Exception) -> JSONResponse:
        error = to_app_error(exc)
        log.warning(
            "request_failed", code=error.code, status=error.status, error_message=error.message
        )
        return _fail(error)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_request: Request, exc: Exception) -> JSONResponse:
        # 400, not FastAPI's default 422: a malformed request body is a bad
        # request. 422 is reserved for a well-formed body we cannot act on.
        assert isinstance(exc, RequestValidationError)
        return _fail(
            AppError(
                "Request validation failed",
                status=400,
                code="INVALID_REQUEST",
                details=_validation_details(exc),
            )
        )

    @app.exception_handler(ValidationError)
    async def _model_validation(_request: Request, exc: Exception) -> JSONResponse:
        # A pydantic model validated inside a service (a device document, a DB
        # row on write). The caller's input was not necessarily at fault, so
        # this is 422 rather than 400.
        assert isinstance(exc, ValidationError)
        log.warning("model_validation_failed", model=exc.title)
        return _fail(
            AppError(
                f"Validation failed for {exc.title}",
                status=422,
                code="UNPROCESSABLE_ENTITY",
                details=_validation_details(exc),
            )
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return _fail(AppError(detail, status=exc.status_code, code=status_to_code(exc.status_code)))

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        # Last resort. The real exception goes to the log with its traceback;
        # the client gets an opaque 500 so nothing leaks through str(exc).
        log.exception("unhandled_exception", error_type=type(exc).__name__)
        return _fail(to_app_error(exc))
