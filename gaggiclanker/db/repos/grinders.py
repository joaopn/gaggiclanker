"""`grinders` — the other half of the hardware a Recipe names.

Small table, one job: record the grinder in enough detail that advice can be
given in **its own units**. `step_unit` is why this exists at all — "two clicks
finer" is something a person can act on, "fifteen microns finer" is not, and the
chat's prompts are handed this field so a model stops inventing a scale.

No archive flag, unlike beans: a grinder is not consumed. One that leaves the
kitchen stays in the list, because the shots it ground still point at it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.vocab import BurrType, StepUnit

__all__ = [
    "GrinderRow",
    "GrinderWrite",
    "GrindersRepository",
    "grinder_lines",
    "kitchen_lines",
    "step_sentence",
]


class GrinderWrite(BaseModel):
    """A grinder as the API accepts it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    #: 'unknown' is the default on purpose: most people do not know, and
    #: guessing here would hand a model a fact it would then reason from.
    burr_type: BurrType = "unknown"
    step_unit: StepUnit = "clicks"
    notes: str = Field(default="", max_length=2000)


class GrinderRow(GrinderWrite):
    """One row of `grinders`, as read back."""

    model_config = ConfigDict(extra="forbid")

    id: int
    created_at: str
    recipe_count: int = 0


#: Which way the dial runs, as a sentence the agent reads. Spelled out, because a model told
#: only "numbers" said "assuming lower number means finer" even with the person's notes saying
#: so in the grinder's record.
_STEP_SENTENCES: dict[str, str] = {
    "clicks": "It is adjusted in clicks: give a grind change as a number of clicks finer or "
    "coarser.",
    "numbers_lower_finer": "Its dial is numbered, and a LOWER number grinds FINER (a higher "
    "number grinds coarser).",
    "numbers_lower_coarser": "Its dial is numbered, and a LOWER number grinds COARSER (a higher "
    "number grinds finer).",
    "microns": "Its setting is in microns: a smaller number is a narrower gap and grinds finer.",
    "free": "It is stepless with no scale to read: give a grind change in words.",
}


def step_sentence(step_unit: str) -> str:
    """Which way the dial runs, as the sentence every conversation is told."""
    return _STEP_SENTENCES.get(step_unit, f"Its grind setting is in {step_unit}.")


def grinder_lines(grinder: GrinderRow | None) -> list[str]:
    """The grinder as its record describes it, notes whole, or nothing.

    One rendering for every conversation that is handed a grinder (a Recipe's, a design's, a
    general one's list, the starting point's), so they cannot disagree about which way the dial
    runs. The notes are the person's own words about the grinder, capped at 2000 characters on
    input, and never cut.
    """
    if grinder is None:
        return []
    facts = [
        grinder.model or "",
        f"{grinder.burr_type} burrs" if grinder.burr_type != "unknown" else "",
    ]
    described = ", ".join(fact for fact in facts if fact)
    lines = [
        f"Grinder {grinder.id}: {grinder.name}" + (f" ({described})" if described else "") + ".",
        step_sentence(grinder.step_unit),
    ]
    if grinder.notes:
        lines.append(f"Their notes on the grinder: {grinder.notes}")
    return lines


def kitchen_lines(grinders: list[GrinderRow]) -> list[str]:
    """Every grinder in the kitchen, for a conversation that is about no one Recipe."""
    if not grinders:
        return []
    lines = ["THE GRINDERS IN THIS KITCHEN, AS THEIR RECORDS DESCRIBE THEM"]
    for grinder in grinders:
        lines += ["", *grinder_lines(grinder)]
    return lines


_WRITABLE = ("name", "model", "burr_type", "step_unit", "notes")

_SELECT = """
    SELECT g.*, (SELECT COUNT(*) FROM recipes s WHERE s.grinder_id = g.id) AS recipe_count
    FROM grinders g
"""


def _values(grinder: GrinderWrite) -> dict[str, Any]:
    payload = grinder.model_dump()
    return {name: payload[name] for name in _WRITABLE}


class GrindersRepository(Repository):
    """Reads and writes `grinders`."""

    async def create(self, grinder: GrinderWrite) -> GrinderRow:
        values = _values(grinder)
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        cursor = await self.db.execute(
            f"INSERT INTO grinders ({columns}, created_at) VALUES ({placeholders}, :created_at)",  # noqa: S608 - column names are the module constant above
            {**values, "created_at": utc_now()},
        )
        stored = await self.get(int(cursor.lastrowid or 0))
        if stored is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("the grinder vanished between write and read")
        return stored

    async def update(self, grinder_id: int, grinder: GrinderWrite) -> GrinderRow | None:
        values = _values(grinder)
        assignments = ", ".join(f"{name} = :{name}" for name in values)
        cursor = await self.db.execute(
            f"UPDATE grinders SET {assignments} WHERE id = :id",  # noqa: S608 - column names are the module constant above
            {**values, "id": grinder_id},
        )
        return None if cursor.rowcount == 0 else await self.get(grinder_id)

    async def get(self, grinder_id: int) -> GrinderRow | None:
        row = await self.db.fetch_one(f"{_SELECT} WHERE g.id = ?", (grinder_id,))
        return self.to_model(GrinderRow, row)

    async def list_all(self) -> list[GrinderRow]:
        rows = await self.db.fetch_all(f"{_SELECT} ORDER BY g.name, g.id")
        return self.to_models(GrinderRow, rows)
