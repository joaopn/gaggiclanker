"""The response-envelope contract.

Every route in this codebase answers `{ok, data | error, meta.request_id}`.
This file is the guardrail: it pins the shape on success, on a 404 and on a
validation failure, and it greps the routers so a future route cannot quietly
return a bare ``JSONResponse`` with some other shape. cvclanker has the same
test (``api-contract.test.ts``) and it is the reason its envelope survived
three years of routes being added.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings
from tests.conftest import NO_WEB_DIST

API_DIR = Path(__file__).resolve().parent.parent / "gaggiclanker" / "api"


def assert_envelope(body: Any, request_id: str | None = None) -> None:
    """Every response body, success or failure, satisfies this."""
    assert isinstance(body, dict), f"body is {type(body).__name__}, not an object"
    assert set(body) <= {"ok", "data", "error", "meta"}, f"unexpected envelope keys: {set(body)}"
    assert isinstance(body["ok"], bool)
    assert "meta" in body
    assert isinstance(body["meta"]["request_id"], str)
    assert body["meta"]["request_id"]

    if body["ok"]:
        assert "data" in body
        assert "error" not in body or body["error"] is None
    else:
        assert "error" in body
        error = body["error"]
        assert isinstance(error["code"], str) and error["code"]
        assert isinstance(error["message"], str) and error["message"]
        assert set(error) <= {"code", "message", "details"}

    if request_id is not None:
        assert body["meta"]["request_id"] == request_id


async def test_health_success_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert_envelope(body)
    assert body["ok"] is True
    assert body["data"]["status"] == "ok"
    assert body["data"]["version"]


async def test_request_id_is_generated_and_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    header_id = response.headers["x-request-id"]
    assert header_id
    assert_envelope(response.json(), request_id=header_id)


async def test_request_id_from_client_is_honoured(client: httpx.AsyncClient) -> None:
    """A caller-supplied id flows into the envelope and the logs."""
    response = await client.get("/health", headers={"x-request-id": "trace-me-123"})
    assert response.headers["x-request-id"] == "trace-me-123"
    assert_envelope(response.json(), request_id="trace-me-123")


async def test_unprintable_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    """A caller cannot inject control characters into the echoed header."""
    response = await client.get("/health", headers={"x-request-id": "bad\tid"})
    assert response.headers["x-request-id"] != "bad\tid"
    assert_envelope(response.json())


async def test_unknown_api_route_returns_envelope_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert_envelope(body, request_id=response.headers["x-request-id"])
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"


async def test_unknown_non_api_route_returns_envelope_404(client: httpx.AsyncClient) -> None:
    """With no SPA build present, a browser path is a JSON 404, not an HTML page."""
    response = await client.get("/shots/42")
    assert response.status_code == 404
    assert_envelope(response.json())


async def test_wrong_method_returns_envelope_405(client: httpx.AsyncClient) -> None:
    response = await client.delete("/api/settings")
    assert response.status_code == 405
    assert_envelope(response.json())


async def test_validation_error_is_400_with_field_details(client: httpx.AsyncClient) -> None:
    """A body that does not match the schema is a 400 listing the offending fields."""
    response = await client.patch("/api/settings", json=["not", "an", "object"])
    assert response.status_code == 400
    body = response.json()
    assert_envelope(body)
    assert body["error"]["code"] == "INVALID_REQUEST"
    details = body["error"]["details"]
    assert isinstance(details, list) and details
    assert all({"field", "message", "type"} <= set(item) for item in details)
    assert any(item["field"].startswith("body") for item in details)


async def test_validation_details_do_not_echo_the_input(client: httpx.AsyncClient) -> None:
    """Field details must not carry the value: a settings PATCH body holds secrets."""
    response = await client.patch("/api/settings", json={"llmApiKey": {"leaked": "sk-secret"}})
    assert response.status_code == 400
    assert "sk-secret" not in response.text


async def test_malformed_json_is_400(client: httpx.AsyncClient) -> None:
    response = await client.patch(
        "/api/settings",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert_envelope(response.json())


@pytest.mark.parametrize("path", sorted(p.name for p in API_DIR.glob("*.py")))
def test_routers_do_not_build_responses_by_hand(path: str) -> None:
    """Routes go through ``envelope_response`` or raise; nothing else.

    A hand-built ``JSONResponse`` or a returned dict is how an app ends up with
    two response shapes, so the rule is enforced here rather than in review.
    """
    source = (API_DIR / path).read_text(encoding="utf-8")
    offenders = [
        line
        for line in source.splitlines()
        if re.search(r"\breturn\s+(JSONResponse|PlainTextResponse|Response)\s*\(", line)
    ]
    assert not offenders, f"{path} builds a response by hand: {offenders}"


async def test_unhandled_exception_keeps_the_request_id_and_the_header(
    env: EnvSettings,
) -> None:
    """The one request that most needs tracing must not lose its id.

    Starlette's ServerErrorMiddleware, where a handler for bare Exception is
    installed, sits OUTSIDE every application middleware — so an unhandled
    exception used to escape the request context before the envelope was built
    and the client got request_id "unknown" with no x-request-id header.
    """
    # NO_WEB_DIST: with a built web/dist present, the SPA catch-all is registered
    # before the route added below and would answer this request with a 404.
    app = create_app(env, web_dist=NO_WEB_DIST)

    @app.get("/api/_boom")
    async def boom() -> None:
        raise RuntimeError("secret-bearing failure at /data/private/db")

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/_boom", headers={"x-request-id": "trace-boom"})

    assert response.status_code == 500
    body = response.json()
    assert_envelope(body)
    assert response.headers["x-request-id"] == "trace-boom"
    assert body["meta"]["request_id"] == "trace-boom"
    assert body["error"]["code"] == "INTERNAL_ERROR"
    # The traceback goes to the log; the client gets nothing from str(exc).
    assert "secret-bearing" not in response.text
    assert "/data/private" not in response.text


async def test_method_not_allowed_has_its_own_code(client: httpx.AsyncClient) -> None:
    """405 used to fall through to INTERNAL_ERROR, which reads as a server bug."""
    response = await client.delete("/api/settings")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
