"""`shot_info_tiers` — where the person moved an item of shot information.

The catalogue (`shotinfo/catalogue.py`) gives every item a default tier; this
table holds only the items somebody moved away from it, edited on Settings →
Shot information and read through `effective_tiers` at the start of every chat
turn and every shot tool call.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from gaggiclanker.db.repository import Repository
from gaggiclanker.shotinfo.catalogue import ITEMS, Tier

__all__ = ["ShotInfoTierRow", "ShotInfoTierWrite", "ShotInfoTiersRepository"]


class ShotInfoTierWrite(BaseModel):
    """One item moved to a tier, as the settings page sends it.

    Checked against the catalogue on the way in, so a row can only ever name
    an item that exists and that a person may move. A locked item (the shot
    id, the Set version, whether the shot counts) is what the agent needs to
    search and cite at all; it is not a choice, and a row for one is refused
    here as well as by the route.
    """

    model_config = ConfigDict(extra="forbid")

    item_key: str
    tier: Tier

    @field_validator("item_key")
    @classmethod
    def _a_movable_item(cls, value: str) -> str:
        item = ITEMS.get(value)
        if item is None:
            raise ValueError("not an item of the shot information catalogue")
        if item.locked:
            raise ValueError("a locked item stays in its tier")
        return value


class ShotInfoTierRow(BaseModel):
    """A stored override, as read back.

    The key is **not** checked against the catalogue here: a row written for
    an item a later release removed must still read, so that the reader can
    ignore it, rather than fail every chat turn that reads the tiers.
    """

    model_config = ConfigDict(extra="forbid")

    item_key: str
    tier: Tier
    updated_at: str


class ShotInfoTiersRepository(Repository):
    """Reads and writes `shot_info_tiers`."""

    async def rows(self) -> list[ShotInfoTierRow]:
        """Every stored override, by key."""
        rows = await self.db.fetch_all(
            "SELECT item_key, tier, updated_at FROM shot_info_tiers ORDER BY item_key"
        )
        return self.to_models(ShotInfoTierRow, rows)

    async def overrides(self) -> dict[str, Tier]:
        """Every stored override as ``{item_key: tier}``, in one query."""
        return {row.item_key: row.tier for row in await self.rows()}

    async def set_tier(self, write: ShotInfoTierWrite) -> None:
        """Put an item in a tier.

        Moving an item back to its default deletes its row instead of storing
        the default: the table says only what differs, so a default a later
        release changes still reaches an item the person had put back.
        """
        if write.tier == ITEMS[write.item_key].default_tier:
            await self.db.execute(
                "DELETE FROM shot_info_tiers WHERE item_key = ?", (write.item_key,)
            )
            return
        await self.db.execute(
            """
            INSERT INTO shot_info_tiers (item_key, tier) VALUES (?, ?)
            ON CONFLICT(item_key) DO UPDATE SET
                tier = excluded.tier,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (write.item_key, write.tier),
        )

    async def reset(self) -> int:
        """Every item back at its default. Returns how many rows went."""
        cursor = await self.db.execute("DELETE FROM shot_info_tiers")
        return cursor.rowcount
