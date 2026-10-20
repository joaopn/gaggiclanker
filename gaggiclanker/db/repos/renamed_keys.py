"""Stored JSON keys the Recipe rename moved, and the names the code still reads them under.

The upgrade step (``db/upgrades/level_004.py``) rewrote these keys in stored documents, so the
documents now say ``recipe_id`` where the code's models still say ``set_id``. Until the models
are renamed, a repository translates at the one place it writes a document and the one place it
reads one back. Nothing outside the repositories sees a stored key.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DROPPED", "SKIPPED", "SOURCES", "STARTING_OUTPUT", "from_stored", "to_stored"]

#: Code name -> stored name, for each document.
SOURCES = {"set_id": "recipe_id", "set_name": "recipe_name"}
SKIPPED = {"set_id": "recipe_id"}
DROPPED = {"one_set": "one_recipe"}
STARTING_OUTPUT = {"similar_set_version_ids": "similar_recipe_version_ids"}


def _rename(node: Any, names: dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {names.get(key, key): _rename(value, names) for key, value in node.items()}
    if isinstance(node, list):
        return [_rename(item, names) for item in node]
    return node


def to_stored(node: Any, names: dict[str, str]) -> Any:
    """The document with the code's key names replaced by the stored ones."""
    return _rename(node, names)


def from_stored(node: Any, names: dict[str, str]) -> Any:
    """The stored document with its key names replaced by the ones the code reads."""
    return _rename(node, {stored: code for code, stored in names.items()})
