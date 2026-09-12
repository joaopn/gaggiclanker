"""`sync_runs` and `sync_events` — the ledger of what the engine did.

A run row per pass over the device answers "when did we last hear from the
machine, and what did it cost" with a query instead of a log grep, and it is
what `GET /api/sync/status` is built from. The event rows are the UI's feed: one
line per shot ingested, quarantined or reconciled.

The events table is trimmed by the engine rather than bounded by the schema. An
unbounded audit table on an appliance with a bind-mounted SQLite file is a
disk-full incident waiting for a slow week.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.repos.base import JsonText, dumps, utc_now
from gaggiclanker.db.repository import Repository

__all__ = [
    "SYNC_KINDS",
    "SyncEventRow",
    "SyncKind",
    "SyncRepository",
    "SyncRunRow",
    "SyncRunUpdate",
]

type SyncKind = Literal["backfill", "live", "profiles", "notes", "identity"]

#: Every kind the ledger accepts, matching the CHECK constraint in 0002.
SYNC_KINDS: tuple[str, ...] = ("backfill", "live", "profiles", "notes", "identity")

#: How many event rows to keep. Roughly a week of a busy household's shots plus
#: the reconciliation chatter around them.
EVENT_RETENTION = 2000


class SyncRunRow(BaseModel):
    """One pass over the device."""

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: str
    status: str = "running"
    trigger: str = ""
    started_at: str
    finished_at: str | None = None
    shots_seen: int = 0
    shots_inserted: int = 0
    shots_updated: int = 0
    shots_quarantined: int = 0
    profiles_changed: int = 0
    notes_synced: int = 0
    errors: int = 0
    error: str | None = None


class SyncRunUpdate(BaseModel):
    """The counters a finished run reports. Mutated in place while it runs."""

    model_config = ConfigDict(extra="forbid")

    shots_seen: int = 0
    shots_inserted: int = 0
    shots_updated: int = 0
    shots_quarantined: int = 0
    profiles_changed: int = 0
    notes_synced: int = 0
    errors: int = 0
    error: str | None = None


class SyncEventRow(BaseModel):
    """One line of the sync feed."""

    model_config = ConfigDict(extra="forbid")

    id: int
    run_id: int | None = None
    at: str
    kind: str
    shot_id: int | None = None
    device_id: str | None = None
    message: str = ""
    data_json: JsonText | None = None


class SyncRepository(Repository):
    """Reads and writes the sync ledger."""

    async def start_run(self, kind: str, trigger: str = "") -> int:
        if kind not in SYNC_KINDS:  # pragma: no cover - the CHECK would catch it anyway
            raise ValueError(f"unknown sync kind {kind!r}")
        cursor = await self.db.execute(
            "INSERT INTO sync_runs (kind, status, trigger, started_at) VALUES (?, 'running', ?, ?)",
            (kind, trigger, utc_now()),
        )
        return int(cursor.lastrowid or 0)

    async def finish_run(self, run_id: int, update: SyncRunUpdate) -> None:
        """Close a run. **Any** counted error makes it a failure.

        Not just a set ``error`` message. A pass that lost half its fetches to a
        machine that went away only bumped the counter, and the run was filed as
        "ok" — so `GET /api/sync/status` reported a healthy sync of an archive
        that had quietly stopped filling, which is the failure mode this ledger
        exists to make visible.
        """
        status = "error" if update.error or update.errors else "ok"
        await self.db.execute(
            """
            UPDATE sync_runs SET
                status = ?, finished_at = ?,
                shots_seen = ?, shots_inserted = ?, shots_updated = ?, shots_quarantined = ?,
                profiles_changed = ?, notes_synced = ?, errors = ?, error = ?
            WHERE id = ?
            """,
            (
                status,
                utc_now(),
                update.shots_seen,
                update.shots_inserted,
                update.shots_updated,
                update.shots_quarantined,
                update.profiles_changed,
                update.notes_synced,
                update.errors,
                update.error,
                run_id,
            ),
        )

    async def get_run(self, run_id: int) -> SyncRunRow | None:
        row = await self.db.fetch_one("SELECT * FROM sync_runs WHERE id = ?", (run_id,))
        return self.to_model(SyncRunRow, row)

    async def last_runs(self) -> dict[str, SyncRunRow]:
        """The most recent run of each kind, keyed by kind.

        One row per kind rather than a list: the status page's question is "is
        each kind of sync healthy", and a list would make the caller do the
        grouping.
        """
        rows = await self.db.fetch_all(
            """
            SELECT * FROM sync_runs WHERE id IN (
                SELECT MAX(id) FROM sync_runs GROUP BY kind
            ) ORDER BY kind
            """
        )
        return {run.kind: run for run in self.to_models(SyncRunRow, rows)}

    async def last_error(self) -> SyncRunRow | None:
        row = await self.db.fetch_one(
            "SELECT * FROM sync_runs WHERE status = 'error' ORDER BY id DESC LIMIT 1"
        )
        return self.to_model(SyncRunRow, row)

    async def recent_runs(self, limit: int = 20) -> list[SyncRunRow]:
        rows = await self.db.fetch_all("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,))
        return self.to_models(SyncRunRow, rows)

    # ── events ───────────────────────────────────────────────────────

    async def add_event(
        self,
        kind: str,
        *,
        run_id: int | None = None,
        shot_id: int | None = None,
        device_id: str | None = None,
        message: str = "",
        data: Any = None,
    ) -> int:
        cursor = await self.db.execute(
            """
            INSERT INTO sync_events (run_id, at, kind, shot_id, device_id, message, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                utc_now(),
                kind,
                shot_id,
                device_id,
                message,
                None if data is None else dumps(data),
            ),
        )
        return int(cursor.lastrowid or 0)

    async def recent_events(self, limit: int = 50) -> list[SyncEventRow]:
        rows = await self.db.fetch_all(
            "SELECT * FROM sync_events ORDER BY id DESC LIMIT ?", (limit,)
        )
        return self.to_models(SyncEventRow, rows)

    async def trim_events(self, keep: int = EVENT_RETENTION) -> int:
        """Drop everything but the newest ``keep`` events."""
        cursor = await self.db.execute(
            """
            DELETE FROM sync_events WHERE id NOT IN (
                SELECT id FROM sync_events ORDER BY id DESC LIMIT ?
            )
            """,
            (keep,),
        )
        return cursor.rowcount
