"""The tool surface: one definition per tool, fed to the chat and to MCP alike.

Importing this package registers every tool in :data:`registry`. That import
side effect is deliberate and is the reason the tools live in one module: a
registry assembled by whoever remembered to call ``register()`` is a registry
that is missing a tool on exactly the code path nobody tested.
"""

from __future__ import annotations

from gaggiclanker.tools import builtin as _builtin  # noqa: F401 - registers the tools
from gaggiclanker.tools.registry import (
    CHAT_PERMISSIONS,
    READ_ONLY,
    Permission,
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
    registry,
    tool,
)

__all__ = [
    "CHAT_PERMISSIONS",
    "READ_ONLY",
    "Permission",
    "ToolContext",
    "ToolOutcome",
    "ToolRegistry",
    "ToolSpec",
    "registry",
    "tool",
]
