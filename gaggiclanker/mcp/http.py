"""Mounting the MCP server on the FastAPI app, behind the same bearer token.

Two things make this less obvious than ``app.mount("/mcp", mcp_app)``.

**The session manager has a lifecycle.** ``streamable_http_app()`` returns a
Starlette app whose *own* lifespan starts the manager's task group, and a
mounted sub-application's lifespan is never run by the parent. So the manager is
built here, entered from our lifespan alongside everything else, and the mount
is a thin ASGI shim that forwards to it.

**The mount has to exist before the SPA fallback.** ``mount_spa`` installs a
catch-all, and a route appended after it is unreachable. So the shim is mounted
in ``create_app`` and resolves the manager off ``app.state`` at request time —
which also gives the honest answer (503) for a request that arrives while the
app is starting, rather than an AttributeError in the middle of a stream.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

import structlog
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from gaggiclanker.infra.envelope import error_payload
from gaggiclanker.infra.errors import ServiceUnavailable

__all__ = ["MCP_PATH", "McpMount", "start_mcp", "stop_mcp"]

log = structlog.get_logger(__name__)

#: Where the Streamable HTTP transport lives. Hard-coded rather than settable:
#: it is in every client's configuration file and in the README, and a movable
#: endpoint is one more thing that can be wrong in a Claude Desktop config.
MCP_PATH = "/mcp"


class McpMount:
    """Forwards ``/mcp`` to the session manager the lifespan started, if any."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        manager = getattr(self._app.state, "mcp_manager", None)
        if manager is None:
            error = ServiceUnavailable(
                "The MCP endpoint is not enabled. Switch on mcpEnabled in Settings."
            )
            response = JSONResponse(status_code=error.status, content=error_payload(error))
            await response(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)


async def start_mcp(app: FastAPI, *, server: Any) -> None:
    """Build the session manager for ``server`` and enter its task group.

    ``stateless_http`` because there is nothing to keep between requests: every
    tool call reads the archive through the app's own connection, and a stateful
    session would add an idle-timeout sweeper and a session table for no gain on
    a single-user appliance.

    The SDK's DNS-rebinding protection is switched **off**, deliberately. It
    allow-lists ``Host: localhost`` and ``127.0.0.1``, which is right for a
    server a browser could be tricked into reaching and wrong for this one: it
    is reached as ``gaggiclanker.local:8000`` from a laptop on the same LAN, and
    the thing standing between a stranger and the archive is the bearer token
    the guard checks before any of this is entered.
    """
    # Building the Starlette app is what constructs and attaches the session
    # manager; the app itself is discarded because our shim owns the routing.
    server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    manager = server.session_manager
    stack = AsyncExitStack()
    await stack.enter_async_context(manager.run())
    app.state.mcp_manager = manager
    app.state.mcp_stack = stack
    log.info("mcp_mounted", path=MCP_PATH)


async def stop_mcp(app: FastAPI) -> None:
    """Close the manager's task group. Safe when it was never started."""
    stack: AsyncExitStack | None = getattr(app.state, "mcp_stack", None)
    app.state.mcp_manager = None
    app.state.mcp_stack = None
    if stack is not None:
        await stack.aclose()
