"""`flavor_picks` — which flavour-wheel notes the shot panel offers.

Two short lists, one for the Taste row and one for the Aroma row, edited on the
Taste wheel page and read by every open shot row. The wheel itself is
vocabulary (`domain/vocab.py`); this is only the person's choice from it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.vocab import FLAVOR_NOTES, in_wheel_order

__all__ = ["FlavorPicks", "FlavorPicksRepository"]


class FlavorPicks(BaseModel):
    """Both lists, as the API takes them and gives them back.

    One model for reading and writing: the page always sends both lists, and a
    write that replaced one while leaving the other would need a second verb
    for no gain.
    """

    model_config = ConfigDict(extra="forbid")

    taste: list[str] = Field(default_factory=list, max_length=len(FLAVOR_NOTES))
    aroma: list[str] = Field(default_factory=list, max_length=len(FLAVOR_NOTES))

    @field_validator("taste", "aroma")
    @classmethod
    def _known_notes(cls, value: list[str]) -> list[str]:
        unknown = [note for note in value if note not in FLAVOR_NOTES]
        if unknown:
            raise ValueError(f"unknown flavour notes: {', '.join(sorted(set(unknown)))}")
        # Wheel order, whatever order they arrived in: the panel shows them the
        # way they sit on the wheel, so neighbours stay neighbours, and storing
        # that order means nothing downstream sorts again.
        return in_wheel_order(value)


class FlavorPicksRepository(Repository):
    """Reads and replaces `flavor_picks`."""

    async def get(self) -> FlavorPicks:
        rows = await self.db.fetch_all(
            "SELECT kind, note FROM flavor_picks ORDER BY kind, position, note"
        )
        lists: dict[str, list[str]] = {"taste": [], "aroma": []}
        for row in rows:
            lists[str(row["kind"])].append(str(row["note"]))
        # Through the model, like every other read: a slug the wheel no longer
        # has is an error here rather than a chip that 422s when clicked.
        return FlavorPicks.model_validate(lists)

    async def replace(self, picks: FlavorPicks) -> FlavorPicks:
        """Both lists, whole. One transaction, so a reader never sees half of them."""
        rows = [
            (kind, note, position)
            for kind, notes in (("taste", picks.taste), ("aroma", picks.aroma))
            for position, note in enumerate(notes)
        ]
        async with self.db.transaction():
            await self.db.execute("DELETE FROM flavor_picks")
            await self.db.execute_many(
                "INSERT INTO flavor_picks (kind, note, position) VALUES (?, ?, ?)", rows
            )
        return await self.get()
