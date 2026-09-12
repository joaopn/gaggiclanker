"""Data access for the two auth tables: ``runtime_secrets`` and ``auth_sessions``.

The session rows are what make a signed JWT revocable. A token carries its own
expiry, but nothing in it can be withdrawn; the guard therefore looks up the
row named by the token's ``jti`` on every request, and "sign out" is an UPDATE
rather than a hope that the browser forgot.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository

__all__ = ["AuthSessionRow", "AuthSessionsRepository", "RuntimeSecretsRepository"]

#: Enough to tell a phone from a laptop in the session list, and short enough
#: that a hostile client cannot use it to grow the database.
MAX_USER_AGENT = 200


class AuthSessionRow(BaseModel):
    """One issued token. ``id`` is the JWT's ``jti`` claim."""

    model_config = ConfigDict(extra="forbid")

    id: str
    subject: str
    created_at: str
    #: Epoch seconds, mirroring the token's ``exp``. See migration 0007 for why
    #: this one column is not ISO text.
    expires_at: int
    revoked_at: str | None = None
    user_agent: str = ""

    def is_live(self, now: int) -> bool:
        return self.revoked_at is None and self.expires_at > now


class RuntimeSecretsRepository(Repository):
    """Secrets the process generates for itself, as opposed to configures."""

    async def get(self, key: str) -> str | None:
        value = await self.db.fetch_value("SELECT value FROM runtime_secrets WHERE key = ?", (key,))
        return None if value is None else str(value)

    async def get_or_create(self, key: str, factory_value: str) -> str:
        """Return the stored secret, inserting ``factory_value`` if there is none.

        ``INSERT OR IGNORE`` then ``SELECT``, rather than "check, then insert":
        two workers booting against the same file would otherwise each generate
        a secret and the second would overwrite the first, invalidating every
        token the first had already signed.
        """
        await self.db.execute(
            "INSERT OR IGNORE INTO runtime_secrets (key, value) VALUES (?, ?)",
            (key, factory_value),
        )
        stored = await self.get(key)
        if stored is None:  # pragma: no cover - the insert above guarantees a row
            raise RuntimeError(f"runtime secret {key!r} vanished immediately after insert")
        return stored


class AuthSessionsRepository(Repository):
    """One row per issued token, which is what makes logout mean something."""

    async def create(self, *, jti: str, subject: str, expires_at: int, user_agent: str) -> None:
        await self.db.execute(
            """
            INSERT INTO auth_sessions (id, subject, created_at, expires_at, user_agent)
            VALUES (?, ?, ?, ?, ?)
            """,
            (jti, subject, utc_now(), int(expires_at), user_agent[:MAX_USER_AGENT]),
        )

    async def get(self, jti: str) -> AuthSessionRow | None:
        row = await self.db.fetch_one("SELECT * FROM auth_sessions WHERE id = ?", (jti,))
        return self.to_model(AuthSessionRow, row)

    async def revoke(self, jti: str) -> bool:
        """Mark one session revoked. Idempotent: revoking twice is not an error."""
        cursor = await self.db.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (utc_now(), jti),
        )
        return cursor.rowcount > 0

    async def revoke_all(self) -> int:
        """Every live session, gone. What a changed password has to do."""
        cursor = await self.db.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE revoked_at IS NULL",
            (utc_now(),),
        )
        return cursor.rowcount

    async def delete_stale(self, now: int) -> int:
        """Drop expired and revoked rows. Runs at boot; nothing needs them."""
        cursor = await self.db.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
            (now,),
        )
        return cursor.rowcount

    async def live_count(self, now: int) -> int:
        value = await self.db.fetch_value(
            "SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL AND expires_at > ?",
            (now,),
        )
        return int(value or 0)
