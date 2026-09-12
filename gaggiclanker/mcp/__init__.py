"""MCP exposure of the tool surface: Streamable HTTP, and stdio.

The package name shadows the SDK's ``mcp`` only for a relative import, and this
codebase uses none; ``from mcp.server.mcpserver import MCPServer`` inside
:mod:`gaggiclanker.mcp.server` resolves to the installed SDK.
"""

from __future__ import annotations

from gaggiclanker.mcp.server import MCP_INSTRUCTIONS, SERVER_NAME, build_mcp_server
from gaggiclanker.mcp.stdio import add_mcp_parser, mcp_command, serve_stdio

__all__ = [
    "MCP_INSTRUCTIONS",
    "SERVER_NAME",
    "add_mcp_parser",
    "build_mcp_server",
    "mcp_command",
    "serve_stdio",
]
