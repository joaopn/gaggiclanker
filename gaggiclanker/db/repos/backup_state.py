"""What the live database holds, as the restore preview compares it with an uploaded file."""

from __future__ import annotations

from gaggiclanker.db.backup import KEY_SETTING_KEYS
from gaggiclanker.db.repository import Repository
from gaggiclanker.db.restore import RestoreCounts

__all__ = ["BackupStateRepository"]


class BackupStateRepository(Repository):
    """Read-only questions about the live archive that the backup screens ask."""

    async def counts(self) -> RestoreCounts:
        """How many shots, Sets and beans the app holds now."""
        return RestoreCounts(
            shots=int(await self.db.fetch_value("SELECT COUNT(*) FROM shots") or 0),
            sets=int(await self.db.fetch_value("SELECT COUNT(*) FROM sets") or 0),
            beans=int(await self.db.fetch_value("SELECT COUNT(*) FROM beans") or 0),
        )

    async def keys_held(self) -> bool:
        """Whether any API key or token the backup box covers is set."""
        marks = ",".join("?" for _ in KEY_SETTING_KEYS)
        count = await self.db.fetch_value(
            f"SELECT COUNT(*) FROM settings WHERE key IN ({marks}) AND value <> ''",  # noqa: S608
            KEY_SETTING_KEYS,
        )
        return int(count or 0) > 0
