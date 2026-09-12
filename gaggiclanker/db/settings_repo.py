"""Data access for the ``settings`` table (runtime overrides)."""

from __future__ import annotations

from gaggiclanker.db.repository import Repository

__all__ = ["SettingsRepository"]


class SettingsRepository(Repository):
    """Reads and writes the stored override strings, keyed by registry key."""

    async def get_all(self) -> dict[str, str]:
        """Every stored override. The table has one row per overridden key."""
        rows = await self.db.fetch_all("SELECT key, value FROM settings")
        return {str(row["key"]): str(row["value"]) for row in rows}

    async def get(self, key: str) -> str | None:
        value = await self.db.fetch_value("SELECT value FROM settings WHERE key = ?", (key,))
        return None if value is None else str(value)

    async def set(self, key: str, value: str) -> None:
        await self.db.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (key, value),
        )

    async def delete(self, key: str) -> bool:
        """Remove an override so the key falls back to env/default."""
        cursor = await self.db.execute("DELETE FROM settings WHERE key = ?", (key,))
        return cursor.rowcount > 0
