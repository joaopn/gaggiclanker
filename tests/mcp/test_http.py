"""The MCP server over Streamable HTTP: the real client SDK against the real app.

In process rather than against a port: the MCP client's transport takes an
``httpx2.AsyncClient``, so an ``ASGITransport`` pointed at the application is a
complete, honest client — the same request path, the same middleware, the same
auth guard, and no port to collide with.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from gaggiclanker.db.connection import Database
from gaggiclanker.mcp.server import SERVER_NAME
from gaggiclanker.settings import EnvSettings
from tests.analyzer.conftest import Fixture, build_fixture

MCP_URL = "http://mcp.test/mcp"


@pytest.fixture
async def mcp_app(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FastAPI, Fixture]]:
    """A running application, with the endpoint switched on, and a seeded archive."""
    from gaggiclanker.main import create_app
    from tests.conftest import NO_WEB_DIST

    # `mcpEnabled` is off by default (see the module docstring); the environment
    # is how a deployment turns it on, so it is how a test does.
    monkeypatch.setenv("GAGGICLANKER_MCP_ENABLED", "true")
    app = create_app(env, web_dist=NO_WEB_DIST, dotenv={})
    async with app.router.lifespan_context(app):
        db: Database = app.state.db
        fixture = await build_fixture(db)
        yield app, fixture


def client_for(app: FastAPI, **kwargs: Any) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)


async def session_for(stack: Any, app: FastAPI, **kwargs: Any) -> ClientSession:
    http = await stack.enter_async_context(client_for(app, **kwargs))
    read, write = await stack.enter_async_context(streamable_http_client(MCP_URL, http_client=http))
    session: ClientSession = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


async def test_a_client_can_list_the_tools_and_the_resources(
    mcp_app: tuple[FastAPI, Fixture],
) -> None:
    from contextlib import AsyncExitStack

    app, _ = mcp_app
    async with AsyncExitStack() as stack:
        session = await session_for(stack, app)

        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert {"describe_schema", "query_shots", "get_shot", "search_knowledge"} <= tools

        resources = {str(row.uri) for row in (await session.list_resources()).resources}
        assert f"{SERVER_NAME}://knowledge/rules" in resources

        templates = {
            row.uri_template for row in (await session.list_resource_templates()).resource_templates
        }
        assert f"{SERVER_NAME}://knowledge/docs/{{slug}}" in templates
        assert f"{SERVER_NAME}://sets/{{set_id}}" in templates


async def test_mcp_advertises_exactly_the_chat_s_tools_even_with_device_writes_on(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read-only by design: the master write switch changes nothing an MCP client sees.

    `deviceWritesEnabled` gates this application's own buttons. It used to be
    one of two switches in front of MCP device-write tools too; there is no such
    class any more, so a server mounted with it on lists exactly the chat's tools.
    """
    from contextlib import AsyncExitStack

    from gaggiclanker.main import create_app
    from gaggiclanker.tools.registry import CHAT_PERMISSIONS, registry
    from tests.conftest import NO_WEB_DIST

    monkeypatch.setenv("GAGGICLANKER_MCP_ENABLED", "true")
    monkeypatch.setenv("GAGGICLANKER_DEVICE_WRITES_ENABLED", "true")
    app = create_app(env, web_dist=NO_WEB_DIST, dotenv={})
    async with app.router.lifespan_context(app), AsyncExitStack() as stack:
        assert await app.state.settings_service.get("deviceWritesEnabled") is True
        session = await session_for(stack, app)
        tools = {tool.name for tool in (await session.list_tools()).tools}

    assert tools == {spec.name for spec in registry.specs(CHAT_PERMISSIONS)}
    assert not any(name.startswith(("push_", "delete_", "save_shot_notes")) for name in tools)


async def test_calling_a_read_tool_returns_the_archive(
    mcp_app: tuple[FastAPI, Fixture],
) -> None:
    from contextlib import AsyncExitStack

    app, fixture = mcp_app
    async with AsyncExitStack() as stack:
        session = await session_for(stack, app)

        result = await session.call_tool("get_shot", {"shot_id": fixture.shots[0]})

        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["shot"]["shot_id"] == fixture.shots[0]


async def test_search_knowledge_comes_back_citable(
    mcp_app: tuple[FastAPI, Fixture],
) -> None:
    from contextlib import AsyncExitStack

    app, _ = mcp_app
    async with AsyncExitStack() as stack:
        session = await session_for(stack, app)

        result = await session.call_tool("search_knowledge", {"query": "channeling", "k": 2})

        assert result.structured_content is not None
        hits = result.structured_content["hits"]
        assert hits and all(hit["heading_path"] for hit in hits)


async def test_a_propose_tool_writes_and_is_audited(
    mcp_app: tuple[FastAPI, Fixture],
) -> None:
    from contextlib import AsyncExitStack

    from gaggiclanker.db.repos.chat import ToolCallsRepository

    app, fixture = mcp_app
    async with AsyncExitStack() as stack:
        session = await session_for(stack, app)

        result = await session.call_tool(
            "record_insight",
            {"text": "An MCP client learned something.", "grinder_id": fixture.grinder_id},
        )

    assert result.is_error is False
    rows = await ToolCallsRepository(app.state.db).recent()
    assert rows[0].tool == "record_insight"
    assert rows[0].caller == "mcp-http"
    assert rows[0].status == "ok"


async def test_a_refused_query_comes_back_as_an_error_result(
    mcp_app: tuple[FastAPI, Fixture],
) -> None:
    """An MCP client shows `isError` results to its model as failures, which is what this is."""
    from contextlib import AsyncExitStack

    app, _ = mcp_app
    async with AsyncExitStack() as stack:
        session = await session_for(stack, app)

        result = await session.call_tool("query_shots", {"sql": "DELETE FROM shots"})

    assert result.is_error is True


async def test_a_resource_can_be_read(mcp_app: tuple[FastAPI, Fixture]) -> None:
    from contextlib import AsyncExitStack

    app, fixture = mcp_app
    async with AsyncExitStack() as stack:
        session = await session_for(stack, app)

        rules = await session.read_resource(f"{SERVER_NAME}://knowledge/rules")
        one_set = await session.read_resource(f"{SERVER_NAME}://sets/{fixture.set_id}")

    assert "category" in getattr(rules.contents[0], "text", "")
    assert f'"id": {fixture.set_id}' in getattr(one_set.contents[0], "text", "")


def test_the_guard_covers_every_method_on_the_endpoint() -> None:
    """`/mcp` is not under `/api`, so the guard has to name it explicitly.

    The transport's own GET is why this matters: without the rule it would fall
    through to the SPA branch, which lets GETs past, and the whole archive would
    be readable without a token. Asserted on the predicate rather than over the
    wire because that GET opens a stream that stays open — the request never
    comes back, which is the point of it.
    """
    from gaggiclanker.auth.guard import requires_auth

    assert all(requires_auth(method, "/mcp") for method in ("GET", "POST", "DELETE"))


async def test_an_unauthenticated_request_is_refused(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from argon2 import PasswordHasher

    from gaggiclanker.main import create_app
    from tests.conftest import NO_WEB_DIST

    # `authUser` and `authPasswordHash` are registry keys, so the environment is
    # where a test turns auth on — the same route the deployment uses.
    monkeypatch.setenv("AUTH_USER", "barista")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", PasswordHasher().hash("secret"))
    monkeypatch.setenv("GAGGICLANKER_MCP_ENABLED", "true")
    app = create_app(env, web_dist=NO_WEB_DIST, dotenv={})
    async with app.router.lifespan_context(app):
        async with client_for(app) as http:
            # POST and DELETE answer immediately; the GET is the long-lived
            # event stream and is covered by the predicate test above.
            for method in ("POST", "DELETE"):
                response = await http.request(method, MCP_URL)
                assert response.status_code == 401, method
