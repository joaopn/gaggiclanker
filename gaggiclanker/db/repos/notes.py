"""`device_shot_notes` — the machine's own notes card, mirrored.

Stored verbatim *and* parsed. The firmware writes every number as a string
("18", "36.5", "" for "not filled in") and stores whatever object it was handed,
so the raw document is the record and the typed columns are a convenience that
is allowed to be ``None``. `ParsedNotes` in the domain layer does the reading;
nothing here re-implements it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonObject, dumps, utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.models import ShotNotes

__all__ = ["DeviceShotNotesRow", "NotesRepository"]


class DeviceShotNotesRow(BaseModel):
    """One row of `device_shot_notes`, as read back.

    ``document`` is the device's own JSON, decoded — the record of what the
    machine actually stored, numbers-as-strings and all. The typed columns
    beside it are the convenience, and every one of them is allowed to be
    ``None``: the firmware stores whatever object it was handed.
    """

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    document: JsonObject = Field(default=None, validation_alias="raw_json")
    rating: int | None = None
    bean_type: str | None = None
    dose_in_g: float | None = None
    dose_out_g: float | None = None
    ratio: float | None = None
    grind_setting: str | None = None
    balance_taste: str | None = None
    notes: str = ""
    device_timestamp: int | None = None
    synced_rating: int | None = None
    synced_volume_g: float | None = None
    fetched_at: str


class NotesRepository(Repository):
    """Reads and writes the notes mirror."""

    async def upsert(
        self,
        shot_id: int,
        notes: ShotNotes,
        *,
        index_rating: int | None = None,
        index_volume_g: float | None = None,
    ) -> DeviceShotNotesRow:
        """Store the device's notes for a shot, replacing anything we had.

        ``index_rating``/``index_volume_g`` are what the index said at the
        moment of the pull, not what the notes say. They are the re-pull
        trigger: the firmware rewrites the index entry in place when a rating or
        a dose changes, so a later index whose figures differ from these is the
        only cheap signal that the document behind them has been edited.
        """
        parsed = notes.parsed
        document = notes.model_dump(by_alias=True, mode="json")
        values: dict[str, Any] = {
            "shot_id": shot_id,
            "raw_json": dumps(document),
            "rating": notes.rating or None,
            "bean_type": notes.bean_type,
            "dose_in_g": parsed.dose_in,
            "dose_out_g": parsed.dose_out,
            "ratio": parsed.ratio,
            "grind_setting": notes.grind_setting or None,
            "balance_taste": notes.balance_taste,
            "notes": notes.notes,
            "device_timestamp": notes.timestamp,
            "synced_rating": index_rating,
            "synced_volume_g": index_volume_g,
            "fetched_at": utc_now(),
        }
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        assignments = ", ".join(f"{name} = excluded.{name}" for name in values if name != "shot_id")
        await self.db.execute(
            f"""
            INSERT INTO device_shot_notes ({columns}) VALUES ({placeholders})
            ON CONFLICT(shot_id) DO UPDATE SET {assignments}
            """,  # noqa: S608 - column names are the literal keys above, values are bound
            values,
        )
        stored = await self.get(shot_id)
        if stored is None:  # pragma: no cover - the upsert above guarantees it
            raise RuntimeError(f"notes for shot {shot_id} vanished between write and read")
        return stored

    async def get(self, shot_id: int) -> DeviceShotNotesRow | None:
        row = await self.db.fetch_one(
            "SELECT * FROM device_shot_notes WHERE shot_id = ?", (shot_id,)
        )
        return self.to_model(DeviceShotNotesRow, row)

    async def stale_shot_ids(self) -> dict[str, tuple[int, int | None, float | None]]:
        """Device id → (shot row id, synced rating, synced volume) for shots we hold notes for.

        The caller compares those two figures with the current index entry and
        re-pulls where they differ.
        """
        rows = await self.db.fetch_all(
            """
            SELECT s.device_id, n.shot_id, n.synced_rating, n.synced_volume_g
            FROM device_shot_notes n
            JOIN shots s ON s.id = n.shot_id
            """
        )
        return {
            str(row["device_id"]): (
                int(row["shot_id"]),
                row["synced_rating"],
                row["synced_volume_g"],
            )
            for row in rows
        }
