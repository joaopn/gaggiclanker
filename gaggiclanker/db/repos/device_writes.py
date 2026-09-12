"""`device_writes` — every byte this box ever asked a machine to change.

One row per attempt, written by the device client itself through
:class:`~gaggiclanker.device.writes.DeviceWriteGate`, and written for refusals
as well as for successes. The refusals are the half people forget and the half
that answers the question actually asked of an audit: *did anything try to write
while this was switched off?*

The other job this table does is provenance. `delete_profile` will only remove a
profile the audit says we created, which is what
:meth:`DeviceWritesRepository.created_by_us` answers. A label can be edited on
the machine; a row here cannot.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository

__all__ = ["DeviceWriteRow", "DeviceWriteWrite", "DeviceWritesRepository"]


class DeviceWriteWrite(BaseModel):
    """The row the gate appends. No dict reaches SQL, here as everywhere."""

    model_config = ConfigDict(extra="forbid")

    #: Spelled out rather than imported from
    #: :data:`gaggiclanker.device.writes.WriteKind`, so that the database layer
    #: still imports nothing from the device layer. The two lists are held
    #: equal by `tests/drafts/test_device_writes.py`, which is cheaper than the
    #: import edge it replaces — and the CHECK constraint in migration 0010 is
    #: a third copy that a mismatch fails loudly against.
    kind: Literal[
        "profile_save",
        "profile_delete",
        "profile_select",
        "profile_favorite",
        "profile_unfavorite",
        "shot_delete",
        "notes_save",
    ]
    host: str = ""
    device_id: str | None = None
    payload_hash: str = ""
    result: Literal["ok", "refused", "failed"]
    error: str = ""


class DeviceWriteRow(BaseModel):
    """One audit row, as the Device page renders it."""

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: str
    host: str = ""
    device_id: str | None = None
    payload_hash: str = ""
    result: str
    error: str = ""
    created_at: str


class DeviceWritesRepository(Repository):
    """Appends to the audit, and answers the one question it is asked."""

    async def record(self, write: DeviceWriteWrite) -> int:
        cursor = await self.db.execute(
            """
            INSERT INTO device_writes
                (kind, host, device_id, payload_hash, result, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                write.kind,
                write.host,
                write.device_id,
                write.payload_hash,
                write.result,
                write.error,
                utc_now(),
            ),
        )
        return int(cursor.lastrowid or 0)

    async def created_by_us(self, device_id: str, *, host: str | None = None) -> bool:
        """Whether this box successfully saved a profile under this id.

        Half of the delete guard; the label suffix is the other half. Scoped to
        the host when one is given, because a person may point this box at a
        second machine and an id is only unique within one of them.
        """
        if not device_id:
            return False
        sql = """
            SELECT 1 FROM device_writes
            WHERE device_id = ? AND kind = 'profile_save' AND result = 'ok'
        """
        params: list[object] = [device_id]
        if host:
            sql += " AND host = ?"
            params.append(host)
        row = await self.db.fetch_one(f"{sql} LIMIT 1", params)
        return row is not None

    async def list_writes(self, *, limit: int = 100) -> list[DeviceWriteRow]:
        """Newest first. The Device page's audit list, and nothing else reads it."""
        rows = await self.db.fetch_all(
            "SELECT * FROM device_writes ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        return self.to_models(DeviceWriteRow, rows)
