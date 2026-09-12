"""`beans` — what was in the hopper.

The machine knows nothing about this table and never will: the whole of its
notes card is one free-text `beanType` string. Everything the knowledge tier
reasons from — roast level, process, days off roast — lives here, typed and from
a closed vocabulary (`gaggiclanker/domain/vocab.py`), because a rule keyed on
"medium-light" cannot match "med light".

Beans are archived rather than deleted. A finished bag is still the bag a
hundred shots were pulled with, and a Set that points at it must keep resolving.
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

    Every field but the name is optional, because a bag with nothing on it but
    a name is still a bag worth recording, and a `None` is an honest "not
    stated" where a default would be a claim about the coffee.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    roaster: str | None = Field(default=None, max_length=200)
    origin: str | None = Field(default=None, max_length=200)
    variety: str | None = Field(default=None, max_length=200)
    #: Metres above sea level. An int because nobody knows it to the metre and
    #: a float would invite a decimal that means nothing.
    altitude_m: int | None = Field(default=None, ge=0, le=4000)
    process: Process | None = None
    roast_level: RoastLevel | None = None
    #: 'YYYY-MM-DD'. A date rather than a timestamp: roasters print a day.
    roast_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    decaf: bool = False
    #: What the bag claims it tastes of, verbatim.
    tasting_notes_bag: str = Field(default="", max_length=500)
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
    "variety",
    "altitude_m",
    "process",
    "roast_level",
    "roast_date",
    "decaf",
    "tasting_notes_bag",
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
        """Every bean, freshest roast first.

        Sorted by roast date rather than by name: the question asked of this
        list is "which bag am I on", and the answer is almost always the most
        recently roasted one. A bean with no roast date sorts last, which is
        where an undated bag belongs.
        """
        clause = "" if include_archived else " WHERE b.archived = 0"
        rows = await self.db.fetch_all(
            f"{_SELECT}{clause} ORDER BY COALESCE(b.roast_date, '') DESC, b.id DESC"
        )
        return self.to_models(BeanRow, rows)
