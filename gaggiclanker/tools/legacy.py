"""Tool names a stored conversation may still carry.

Four tools had the old word in their names. Conversations are history: their stored tool calls
and results keep the names they were made under, and nothing rewrites them. Whatever reads a
stored name to decide something (the transcript, which keeps a registered tool's result whole and
shrinks a shot rendering's) first asks for the name the tool has now.
"""

from __future__ import annotations

__all__ = ["LEGACY_TOOL_NAMES", "current_tool_name"]

#: The name a tool was stored under, to the name it has now.
LEGACY_TOOL_NAMES: dict[str, str] = {
    "get_set": "get_recipe",
    "list_sets": "list_recipes",
    "list_set_shots": "list_recipe_shots",
    "propose_set_version": "propose_recipe_version",
}


def current_tool_name(name: str) -> str:
    """``name`` as the registry knows the tool today (unchanged when it never changed)."""
    return LEGACY_TOOL_NAMES.get(name, name)
