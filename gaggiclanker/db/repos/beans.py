"""`beans` — the coffees, not the bags.

A row here is a *type*: this coffee, from this roaster, this process, this roast
level. Buying the same coffee again is the same row. Nothing here describes an
individual bag — no roast date, no weight, no ageing — because an attribute of
one bag written onto the type is wrong for every other bag of it, and advice
derived from a date nobody maintains is worse than no advice.

The machine knows nothing about this table and never will: the whole of its
notes card is one free-text `beanType` string. Everything the knowledge tier
reasons from — roast level, process, origin — lives here, typed and from a
closed vocabulary (`gaggiclanker/domain/vocab.py`), because a rule keyed on
"medium-light" cannot match "med light".

Beans are archived rather than deleted. A coffee you have stopped buying is
still the coffee a hundred shots were pulled with, and a Set that points at it
must keep resolving.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.vocab import Process, RoastLevel

__all__ = ["BeanRow", "BeanWrite", "BeansRepository"]


class BeanWrite(BaseModel):
    """A bean as the API accepts it. The only way a row reaches `beans`.

    Every field but the name is optional, because a coffee with nothing on it
    but a name is still one worth recording, and a `None` is an honest "not
    stated" where a default would be a claim about the coffee.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    roaster: str | None = Field(default=None, max_length=200)
    origin: str | None = Field(default=None, max_length=200)
    process: Process | None = None
    roast_level: RoastLevel | None = None
    decaf: bool = False
    #: A free-form description of the coffee in the person's words: what the
    #: bag or the roaster says, tasting notes, anything worth knowing about the
    #: bean. Both prompts get it as written, under `description`.
    description: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=2000)


class BeanRow(BeanWrite):
    """One row of `beans`, as read back."""

    model_config = ConfigDict(extra="forbid")

    id: int
    archived: bool = False
    created_at: str
    #: How many Sets point at this bean. The archive button asks before it hides
    #: a bean that is still in use, and a delete would have to refuse.
    set_count: int = 0


#: The columns an insert or an update writes, in one place so the two SQL
#: statements cannot drift apart.
_WRITABLE = (
    "name",
    "roaster",
    "origin",
    "process",
    "roast_level",
    "decaf",
    "description",
    "notes",
)

#: `beans` plus the count the UI shows. A correlated subquery rather than a
#: GROUP BY join: there are tens of beans, the count is usually zero, and a
#: LEFT JOIN with a GROUP BY would have to repeat every column of `beans` in the
#: grouping clause for no measurable gain.
_SELECT = """
    SELECT b.*, (SELECT COUNT(*) FROM sets s WHERE s.bean_id = b.id) AS set_count
    FROM beans b
"""


def _values(bean: BeanWrite) -> dict[str, Any]:
    payload = bean.model_dump()
    payload["decaf"] = int(payload["decaf"])
    return {name: payload[name] for name in _WRITABLE}


class BeansRepository(Repository):
    """Reads and writes `beans`."""

    async def create(self, bean: BeanWrite) -> BeanRow:
        values = _values(bean)
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        cursor = await self.db.execute(
            f"INSERT INTO beans ({columns}, created_at) VALUES ({placeholders}, :created_at)",  # noqa: S608 - column names are the module constant above
            {**values, "created_at": utc_now()},
        )
        stored = await self.get(int(cursor.lastrowid or 0))
        if stored is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("the bean vanished between write and read")
        return stored

    async def update(self, bean_id: int, bean: BeanWrite) -> BeanRow | None:
        values = _values(bean)
        assignments = ", ".join(f"{name} = :{name}" for name in values)
        cursor = await self.db.execute(
            f"UPDATE beans SET {assignments} WHERE id = :id",  # noqa: S608 - column names are the module constant above
            {**values, "id": bean_id},
        )
        return None if cursor.rowcount == 0 else await self.get(bean_id)

    async def set_archived(self, bean_id: int, *, archived: bool) -> BeanRow | None:
        cursor = await self.db.execute(
            "UPDATE beans SET archived = ? WHERE id = ?", (int(archived), bean_id)
        )
        return None if cursor.rowcount == 0 else await self.get(bean_id)

    async def get(self, bean_id: int) -> BeanRow | None:
        row = await self.db.fetch_one(f"{_SELECT} WHERE b.id = ?", (bean_id,))
        return self.to_model(BeanRow, row)

    async def list_all(self, *, include_archived: bool = False) -> list[BeanRow]:
        """Every bean, by name.

        Alphabetical because nothing here ages: a coffee is a type, so there is
        no "most recent" to sort by and a list you can find a name in beats one
        ordered by when it was typed. The id breaks ties, so two coffees with
        the same name keep a stable order.
        """
        clause = "" if include_archived else " WHERE b.archived = 0"
        rows = await self.db.fetch_all(f"{_SELECT}{clause} ORDER BY b.name COLLATE NOCASE, b.id")
        return self.to_models(BeanRow, rows)
