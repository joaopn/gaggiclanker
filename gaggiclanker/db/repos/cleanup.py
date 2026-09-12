"""`cleanup_runs`, and the rows a cleanup decision is made from.

Two things live here and they are different shapes of the same subject. A
:class:`CleanupCandidate` is one shot as the *eligibility rule* needs to see it —
enough of the header to recompute how long the file should be, plus the two
flags that disqualify it — and never carries the blob itself: a plan over a
thousand shots would otherwise pull a few megabytes of `.slog` through Python to
answer a question about lengths. A :class:`CleanupRunRow` is one pass over the
machine, which is what the Device page lists.

The rule that reads a candidate is deliberately **not** here; it is in
:mod:`gaggiclanker.cleanup.eligibility`, because the write gate and the plan step
both have to apply exactly the same one and neither of them is a repository.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository

__all__ = [
    "CleanupCandidate",
    "CleanupRepository",
    "CleanupRunRow",
    "CleanupRunUpdate",
]

#: What the policy may be set to. `off` is a real stored value rather than the
#: absence of one: a run recorded under it is a run somebody asked for by hand
#: while the automatic policy was switched off, and that is worth telling apart.
type CleanupMode = Literal["off", "keep_newest", "free_space"]


class CleanupCandidate(BaseModel):
    """One archived shot, as the eligibility rule needs to see it.

    ``raw_bytes`` is ``LENGTH(raw_slog)`` rather than the blob: the rule is
    about how long the file is, and reading a thousand of them to measure them
    would be the slowest possible way to ask.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    device_id: str
    machine_id: int
    started_at: str | None = None
    start_epoch: int = 0
    quarantined: bool = False
    deleted_on_device: bool = False
    incomplete: bool = False
    sample_count: int = 0
    slog_version: int | None = None
    fields_mask: int | None = None
    raw_bytes: int = 0
    profile_name_on_device: str = ""


class CleanupRunRow(BaseModel):
    """One cleanup pass, as the Device page renders it."""

    model_config = ConfigDict(extra="forbid")

    id: int
    machine_id: int
    mode: str
    target: int = 0
    trigger: str = "manual"
    status: str = "running"
    planned: int = 0
    deleted: int = 0
    errors: int = 0
    error: str | None = None
    free_before: int | None = None
    free_after: int | None = None
    started_at: str
    finished_at: str | None = None


class CleanupRunUpdate(BaseModel):
    """The counters a run accumulates, applied once when it closes."""

    model_config = ConfigDict(extra="forbid")

    deleted: int = 0
    errors: int = 0
    error: str | None = None
    free_after: int | None = None


class CleanupRepository(Repository):
    """Candidate rows to delete, and the ledger of what was deleted."""

    async def candidates(self, machine_id: int) -> list[CleanupCandidate]:
        """Every shot still on the machine, **oldest first**.

        Oldest first, and "oldest" means **by id**, not by timestamp: that is
        the order the firmware itself deletes in (`cleanupHistory()` walks `/h/`
        in filename order, which is zero-padded id order), and a cleanup in a
        different order would leave the machine's own retention working against
        ours. It also happens to be the only ordering that is always right — a
        machine whose clock had not reached NTP when a shot was pulled writes a
        `startEpoch` near zero, so sorting by time would put a handful of recent
        shots at the very front of the queue.

        `deleted_on_device` rows are excluded: the machine has already dropped
        them and asking it to do so again would be a write with nothing behind
        it. Quarantined and short-file rows are **not** excluded here — they are
        candidates the rule then refuses, and a plan that silently omitted them
        could not explain why a shot it can see is never cleaned up.
        """
        rows = await self.db.fetch_all(
            """
            SELECT id, device_id, machine_id, started_at, start_epoch, quarantined,
                   deleted_on_device, incomplete, sample_count, slog_version, fields_mask,
                   LENGTH(raw_slog) AS raw_bytes, profile_name_on_device
            FROM shots
            WHERE machine_id = ? AND deleted_on_device = 0
            ORDER BY device_id ASC
            """,
            (machine_id,),
        )
        return self.to_models(CleanupCandidate, rows)

    async def candidate(self, machine_id: int, device_id: str) -> CleanupCandidate | None:
        """One shot by the id the machine knows it as, for this machine only.

        Scoped to the machine on purpose: shot ids are a per-device counter, so
        `000129` exists on every GaggiMate that has pulled a hundred and
        twenty-nine shots. A delete authorised against another machine's row
        would be a delete of somebody else's shot.
        """
        row = await self.db.fetch_one(
            """
            SELECT id, device_id, machine_id, started_at, start_epoch, quarantined,
                   deleted_on_device, incomplete, sample_count, slog_version, fields_mask,
                   LENGTH(raw_slog) AS raw_bytes, profile_name_on_device
            FROM shots
            WHERE machine_id = ? AND device_id = ?
            """,
            (machine_id, device_id),
        )
        return self.to_model(CleanupCandidate, row)

    async def on_device_count(self, machine_id: int) -> int:
        """How many shots the archive believes the machine still holds."""
        value = await self.db.fetch_value(
            "SELECT count(*) FROM shots WHERE machine_id = ? AND deleted_on_device = 0",
            (machine_id,),
        )
        return int(value or 0)

    async def start_run(
        self,
        machine_id: int,
        *,
        mode: str,
        target: int,
        trigger: str,
        planned: int,
        free_before: int | None,
    ) -> int:
        cursor = await self.db.execute(
            """
            INSERT INTO cleanup_runs
                (machine_id, mode, target, trigger, status, planned, free_before, started_at)
            VALUES (?, ?, ?, ?, 'running', ?, ?, ?)
            """,
            (machine_id, mode, target, trigger, planned, free_before, utc_now()),
        )
        return int(cursor.lastrowid or 0)

    async def finish_run(self, run_id: int, update: CleanupRunUpdate) -> None:
        await self.db.execute(
            """
            UPDATE cleanup_runs
            SET status = ?, deleted = ?, errors = ?, error = ?, free_after = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                "error" if update.errors else "ok",
                update.deleted,
                update.errors,
                update.error,
                update.free_after,
                utc_now(),
                run_id,
            ),
        )

    async def get_run(self, run_id: int) -> CleanupRunRow | None:
        row = await self.db.fetch_one("SELECT * FROM cleanup_runs WHERE id = ?", (run_id,))
        return self.to_model(CleanupRunRow, row)

    async def list_runs(
        self, machine_id: int | None = None, *, limit: int = 20
    ) -> list[CleanupRunRow]:
        """Newest first. The Device page's history, and nothing else reads it."""
        if machine_id is None:
            rows = await self.db.fetch_all(
                "SELECT * FROM cleanup_runs ORDER BY started_at DESC, id DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = await self.db.fetch_all(
                """
                SELECT * FROM cleanup_runs WHERE machine_id = ?
                ORDER BY started_at DESC, id DESC LIMIT ?
                """,
                (machine_id, limit),
            )
        return self.to_models(CleanupRunRow, rows)

    async def reconcile_running(self) -> int:
        """Close runs a restart cut off. Called from the lifespan, like the others.

        A row is only `running` while a process holds it, and no process
        survives a boot; leaving one behind would make the Device page show a
        cleanup in progress for ever and the name guard refuse the next one.
        """
        cursor = await self.db.execute(
            """
            UPDATE cleanup_runs
            SET status = 'error', errors = errors + 1, finished_at = ?,
                error = COALESCE(error, 'interrupted by a restart')
            WHERE status = 'running'
            """,
            (utc_now(),),
        )
        return int(cursor.rowcount or 0)
