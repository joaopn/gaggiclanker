"""The chat's database tool: the tool registry as an MCP server, over stdio.

It lives with the tools because it is one of their transports. The
``claude_code`` chat provider runs its tool loop inside the Claude Code CLI, and
the CLI can only call tools through an MCP server — so the provider spawns this
one (``gaggiclanker mcp``) with ``DATA_DIR`` pointing at the app's archive. It
opens that database and nothing else: no network endpoint, no machine
connection, no setting.

The package name shadows the SDK's ``mcp`` only for a relative import, and this
codebase uses none; ``from mcp.server.mcpserver import MCPServer`` inside
:mod:`gaggiclanker.tools.mcp.server` resolves to the installed SDK.
"""

from __future__ import annotations

from gaggiclanker.tools.mcp.server import MCP_INSTRUCTIONS, SERVER_NAME, build_mcp_server
from gaggiclanker.tools.mcp.stdio import add_mcp_parser, mcp_command, serve_stdio

__all__ = [
    "MCP_INSTRUCTIONS",
    "SERVER_NAME",
    "add_mcp_parser",
    "build_mcp_server",
    "mcp_command",
    "serve_stdio",
]
