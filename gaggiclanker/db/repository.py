"""The repository base every data-access module builds on.

Repositories are the only code that writes SQL. Routes call services, services
call repositories, repositories return pydantic models — so "every write goes
through a pydantic model" is enforced by where the
conversion happens, not by reviewer memory.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import aiosqlite
from pydantic import BaseModel

from gaggiclanker.db.connection import Database

__all__ = ["Repository", "row_to_dict"]


def row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    """An ``aiosqlite.Row`` as a plain dict (it is a mapping, but not a dict)."""
    return dict(zip(row.keys(), tuple(row), strict=True))


class Repository:
    """Holds the database handle and converts rows into models."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def to_model[M: BaseModel](model: type[M], row: aiosqlite.Row | None) -> M | None:
        """Validate one row into ``model``; ``None`` passes through."""
        return None if row is None else model.model_validate(row_to_dict(row))

    @staticmethod
    def to_models[M: BaseModel](model: type[M], rows: Sequence[aiosqlite.Row]) -> list[M]:
        """Validate many rows into ``model``."""
        return [model.model_validate(row_to_dict(row)) for row in rows]
