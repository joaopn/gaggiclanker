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

Archiving is how a coffee is retired. A coffee you have stopped buying is still
the coffee a hundred shots were pulled with, and a Set that points at it must
keep resolving. Deleting is for a bean nobody used — a typo, a duplicate — and
is refused while any Set points at it.
"""

from __future__ import annotations

from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.vocab import Process, RoastLevel

__all__ = [
    "BEAN_SCALES",
    "BeanDeletion",
    "BeanRow",
    "BeanScale",
    "BeanWrite",
    "BeansRepository",
    "taste_scales",
]

#: How the coffee tastes, on a scale of 1 to 5, the way many bags print it: the
#: person's reading of the coffee, not a measurement. `None` is "not stated".
type BeanScale = Annotated[int, Field(ge=1, le=5)]

#: The three scales, in the order every screen and prompt lists them.
BEAN_SCALES: tuple[str, ...] = ("acidity", "intensity", "sweetness")


class _HasScales(Protocol):
    @property
    def acidity(self) -> int | None: ...
    @property
    def intensity(self) -> int | None: ...
    @property
    def sweetness(self) -> int | None: ...


def taste_scales(bean: _HasScales | None) -> str | None:
    """The scales the person filled in, as one phrase for a prompt, or ``None``.

    ``"acidity 4, sweetness 3 (1 low to 5 high)"``: one phrase rather than a
    line per scale, so the direction is said once and every prompt says it the
    same way. An unstated scale is left out rather than named, and a bean with
    none gets nothing: "acidity: not stated" reads to a model as something
    known about the coffee.
    """
    if bean is None:
        return None
    parts = [
        f"{name} {value}" for name in BEAN_SCALES if (value := getattr(bean, name)) is not None
    ]
    return f"{', '.join(parts)} (1 low to 5 high)" if parts else None


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
    acidity: BeanScale | None = None
    intensity: BeanScale | None = None
    sweetness: BeanScale | None = None
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


class BeanDeletion(BaseModel):
    """What a delete did: nothing to delete, refused because Sets use it, or done."""

    model_config = ConfigDict(extra="forbid")

    found: bool
    deleted: bool
    #: The Sets that pointed at the bean when the delete was asked for; a
    #: refusal names the number so the person knows why.
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
    *BEAN_SCALES,
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

    async def delete(self, bean_id: int) -> BeanDeletion:
        """Delete a bean no Set uses.

        The count and the delete run in one transaction: `Database.transaction()`
        serialises writers, so a Set created between the check and the delete
        cannot be orphaned. `sets.bean_id` has no `ON DELETE`, so the foreign key
        would refuse anyway; the check is what turns that into a reason a person
        can act on. Starting-point runs about the bean cascade with it (0014).
        """
        async with self.db.transaction():
            if await self.db.fetch_value("SELECT 1 FROM beans WHERE id = ?", (bean_id,)) is None:
                return BeanDeletion(found=False, deleted=False)
            set_count = int(
                await self.db.fetch_value("SELECT COUNT(*) FROM sets WHERE bean_id = ?", (bean_id,))
            )
            if set_count > 0:
                return BeanDeletion(found=True, deleted=False, set_count=set_count)
            await self.db.execute("DELETE FROM beans WHERE id = ?", (bean_id,))
        return BeanDeletion(found=True, deleted=True)

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
