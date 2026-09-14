"""Plan and run a device cleanup: which shots go, in what order, and how fast.

The machine is a buffer. `cleanupHistory()` deletes its oldest `.slog` whenever
free space drops below 500 KB, whether or not anything has archived it, so shots
leave the device either way — this service only changes *when*, and adds the one
precondition the firmware cannot check: that this box already holds the bytes.

Three properties are worth stating before the code, because each is a decision
rather than a consequence.

**Oldest first, always.** The firmware walks `/h/` in filename order, which is id
order, so a cleanup that deleted newest-first would leave the machine's own
retention fighting ours — it would still delete the oldest shot the moment space
ran low, and the newest ones this box removed would have been removed for
nothing.

**One shot per frame, paced, stopping on the first error.** `req:history:delete`
is one message per shot and the display has about 300 KB of heap. A burst of
four hundred deletes is a burst of four hundred filesystem operations on a
cooperatively scheduled web server that is also drawing a UI. And a device error
mid-run means the machine is unhappy *now*; carrying on would turn one bad frame
into four hundred.

**Never automatic.** A run deletes exactly the shots a person was shown and
confirmed on the Sync page: the request carries the planned shot ids, and
:meth:`CleanupService.approve` refuses when a fresh plan no longer matches them
(a pull added a shot, a policy changed, a shot stopped being eligible). Nothing
in this application starts a cleanup on its own — no timer, no hook after a
sync pass.

**The plan is advisory, the gate is authoritative.** :meth:`CleanupService.plan`
calls exactly the same :func:`~gaggiclanker.cleanup.eligibility.ineligible_reason`
the write gate does, so a preview and a run agree — but if they ever disagreed,
the gate would win and the run would record a refusal. The preview is a
courtesy; the gate is the safety layer.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.cleanup.eligibility import ineligible_reason
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.cleanup import (
    CleanupCandidate,
    CleanupRepository,
    CleanupRunRow,
    CleanupRunUpdate,
)
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository, DeviceWriteWrite
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.errors import DeviceError, DeviceUnavailable
from gaggiclanker.device.writes import payload_hash
from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.models import IndexEntry
from gaggiclanker.drafts.gate import refuse_unless_writes_enabled
from gaggiclanker.infra.errors import BadRequest, Conflict, ServiceUnavailable
from gaggiclanker.infra.sse import SseEvent, SseEventBus
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.settings_service import SettingsService

__all__ = [
    "CLEANUP_EVENT",
    "CleanupPlan",
    "CleanupPolicy",
    "CleanupService",
    "PlannedShot",
    "SkippedShot",
    "cleanup_task_name",
]

log = structlog.get_logger(__name__)

#: Published on the sync bus when a run starts and when it finishes. The Device
#: page follows it the way it follows `sync.progress`: the event means "re-read",
#: never "here is the new state".
CLEANUP_EVENT = "cleanup.progress"

#: The floor on the interval between two deletes, in seconds — two a second at
#: most. The display's web server is pumped from its main loop and every delete
#: is three filesystem operations plus an index rewrite; a tight loop over four
#: hundred shots is how a machine stops answering its own UI.
MIN_DELETE_INTERVAL_S = 0.5


def cleanup_task_name() -> str:
    """The registry name a cleanup holds.

    One machine, so one name: two tabs pressing the button get one run rather
    than two passes fighting over the device's two HTTP slots.
    """
    return "cleanup"


class CleanupPolicy(BaseModel):
    """The settings as they are right now, resolved once per plan."""

    model_config = ConfigDict(extra="forbid")

    mode: str = "off"
    keep_newest: int = 50
    min_free_kb: int = 2048
    writes_enabled: bool = False

    @property
    def target(self) -> int:
        """The policy's one number, for the ledger: a count or a KB floor."""
        if self.mode == "keep_newest":
            return self.keep_newest
        if self.mode == "free_space":
            return self.min_free_kb
        return 0


class PlannedShot(BaseModel):
    """One shot the plan would delete, in the order it would go."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    device_id: str
    started_at: str | None = None
    raw_bytes: int = 0
    profile_name: str = ""
    #: Why the policy picks this shot, as the sentence the confirmation shows.
    #: Every planned shot has already passed the eligibility rule; this is the
    #: other half of "why this one": which part of the policy wants it gone.
    reason: str = ""


class SkippedShot(BaseModel):
    """One shot the plan would not delete, and the sentence saying why."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    device_id: str
    reason: str


class CleanupPlan(BaseModel):
    """What a run would do, if one were started now. Never touches the machine."""

    model_config = ConfigDict(extra="forbid")

    policy: CleanupPolicy
    #: What the archive believes is still on the machine — every shot it holds
    #: that the index has not flagged deleted.
    on_device_count: int = 0
    #: Free bytes from the last `res:ota-settings`, and which volume they are
    #: from. ``None`` when the machine has never broadcast one, which is the
    #: only honest answer and is why `free_space` mode refuses to act on it.
    free_bytes: int | None = None
    free_source: str | None = None
    #: Shots that would go, oldest first. Empty is a perfectly good plan.
    planned: list[PlannedShot] = Field(default_factory=list)
    #: Shots the rule refuses, with the reason. Shown rather than hidden: a
    #: plan that silently omitted them could not explain why a shot it can see
    #: is never cleaned up.
    skipped: list[SkippedShot] = Field(default_factory=list)
    #: Set when the policy wants to act but cannot. A sentence, not a code.
    blocked: str | None = None

    @property
    def bytes_freed(self) -> int:
        return sum(item.raw_bytes for item in self.planned)


class CleanupService:
    """Owns the plan, the run and the ledger. Holds the one gated client."""

    def __init__(
        self,
        db: Database,
        settings: SettingsService,
        *,
        client: GaggimateClient | None,
        bus: SseEventBus | None = None,
        pace_seconds: float = MIN_DELETE_INTERVAL_S,
    ) -> None:
        self.db = db
        self.settings = settings
        self.client = client
        self.bus = bus
        # A parameter so the suite can prove the pacing exists without spending
        # half a second per deleted shot proving it forty times.
        self.pace_seconds = pace_seconds
        self.cleanup = CleanupRepository(db)
        self.shots = ShotsRepository(db)
        self.notes = NotesRepository(db)
        self.writes = DeviceWritesRepository(db)

    # ── policy ───────────────────────────────────────────────────────

    async def policy(self) -> CleanupPolicy:
        """The three cleanup settings plus the master write switch, read fresh.

        Read on every plan and every run rather than cached, for the same reason
        the write gate re-reads `deviceWritesEnabled`: the person turning it off
        has usually just seen something they did not like.
        """
        return CleanupPolicy(
            mode=str(await self.settings.get("deviceCleanupMode") or "off"),
            keep_newest=int(await self.settings.get("deviceCleanupKeepNewest")),
            min_free_kb=int(await self.settings.get("deviceCleanupMinFreeKb")),
            writes_enabled=bool(await self.settings.get("deviceWritesEnabled")),
        )

    # ── the plan ─────────────────────────────────────────────────────

    async def plan(self) -> CleanupPlan:
        """What a run would delete right now. A dry run, and the UI's preview.

        There is one machine and one archive of its shots, so there is nothing
        to choose between: a cleanup is of the machine, and the only question
        this answers is which of the shots it still holds may go.
        """
        policy = await self.policy()
        free_bytes, free_source = self._free_space()
        candidates = await self.cleanup.candidates()
        eligible: list[CleanupCandidate] = []
        skipped: list[SkippedShot] = []
        for candidate in candidates:
            reason = ineligible_reason(candidate, device_id=candidate.device_id)
            if reason is None:
                eligible.append(candidate)
            else:
                skipped.append(
                    SkippedShot(shot_id=candidate.id, device_id=candidate.device_id, reason=reason)
                )

        chosen, blocked = self._choose(policy, eligible, len(candidates), free_bytes)
        reason = _planned_reason(policy)
        return CleanupPlan(
            policy=policy,
            on_device_count=len(candidates),
            free_bytes=free_bytes,
            free_source=free_source,
            planned=[
                PlannedShot(
                    shot_id=item.id,
                    device_id=item.device_id,
                    started_at=item.started_at,
                    raw_bytes=item.raw_bytes,
                    profile_name=item.profile_name_on_device,
                    reason=reason,
                )
                for item in chosen
            ],
            skipped=skipped,
            blocked=blocked,
        )

    def _choose(
        self,
        policy: CleanupPolicy,
        eligible: list[CleanupCandidate],
        on_device: int,
        free_bytes: int | None,
    ) -> tuple[list[CleanupCandidate], str | None]:
        """Apply the mode to the oldest-first eligible list. Pure, and tested as such."""
        if policy.mode == "off":
            return [], None
        if policy.mode == "keep_newest":
            surplus = on_device - policy.keep_newest
            if surplus <= 0:
                return [], None
            # From the oldest end. A shot the rule refuses is skipped rather
            # than substituted-for from the newest end: the point of the policy
            # is that the machine keeps the *newest* N, and deleting a newer
            # shot because an older one is quarantined would quietly break that.
            return eligible[:surplus], None
        if policy.mode == "free_space":
            if free_bytes is None:
                return [], (
                    "The machine has not reported its free space, so a free-space policy "
                    "has nothing to act on. It broadcasts that with res:ota-settings, "
                    "which needs a live WebSocket connection."
                )
            deficit = policy.min_free_kb * 1024 - free_bytes
            if deficit <= 0:
                return [], None
            chosen: list[CleanupCandidate] = []
            freed = 0
            for candidate in eligible:
                if freed >= deficit:
                    break
                chosen.append(candidate)
                freed += candidate.raw_bytes
            if freed < deficit:
                return chosen, (
                    "Deleting every eligible shot would still not reach the free-space "
                    "target; the rest of the flash is profiles and firmware."
                )
            return chosen, None
        return [], None  # pragma: no cover - the registry validates the mode

    def _free_space(self) -> tuple[int | None, str | None]:
        """Free bytes from the last identity frame, preferring the SD card.

        The firmware moves `/h/` onto an SD card when one is mounted
        (`ShotHistoryPlugin.cpp:83-86`), and only reports `sd*` keys when it
        did — so the presence of `sdFree` is itself the answer to "where does
        the history live".
        """
        identity = self.client.identity if self.client is not None else None
        if identity is None:
            return None, None
        if identity.sd_free is not None:
            return identity.sd_free, "sd"
        if identity.spiffs_free is not None:
            return identity.spiffs_free, "spiffs"
        return None, None

    # ── the run ──────────────────────────────────────────────────────

    async def approve(self, shot_ids: list[int]) -> CleanupPlan:
        """The plan a person confirmed, or a refusal if it is no longer the plan.

        The Sync page shows a preview and sends back the ids it showed. A fresh
        plan is computed here and compared as a set: if a pull added a shot, the
        policy changed, or a shot stopped being eligible in between, the run is
        refused rather than quietly deleting something nobody was shown. What
        runs is then exactly this plan — :meth:`run` does not recompute it.

        The master switch is checked first, and a refusal is audited, so a
        request made with writes off never gets as far as a plan. Order of
        checks: a machine, the switch, a connection, the plan.
        """
        if self.client is None:
            raise _no_machine()
        await refuse_unless_writes_enabled(
            self.settings, self.writes, kind="shot_delete", host=self.client.host
        )
        if not self.client.connected:
            raise DeviceUnavailable(
                "The machine is not connected, so nothing can be deleted from it now."
            )
        if not shot_ids:
            # Confirming an empty plan is not a run: it would leave an `ok 0/0`
            # row in the ledger for a deletion nobody asked for.
            raise BadRequest("Confirm at least one shot to delete.", details={"field": "shot_ids"})
        plan = await self.plan()
        if sorted(item.shot_id for item in plan.planned) != sorted(shot_ids):
            raise Conflict(
                "The cleanup plan has changed since it was previewed — a pull, a settings "
                "change or the archive moved a shot in or out of it. Nothing was deleted. "
                "Review the new plan and confirm again.",
                details={"field": "shot_ids", "planned": len(plan.planned)},
            )
        return plan

    def spawn(self, tasks: TaskRegistry, plan: CleanupPlan, *, trigger: str = "manual") -> bool:
        """Queue a run of an approved plan under the cleanup name. False if one is running.

        The name is claimed synchronously inside
        :meth:`~gaggiclanker.infra.tasks.TaskRegistry.spawn`, so two tabs
        pressing the button cannot both start a pass however the requests
        interleave — the loser is told a run is already going rather than
        getting a second one.
        """
        name = cleanup_task_name()
        if tasks.get(name) is not None:
            return False
        tasks.spawn(name, self.run(plan, trigger=trigger))
        return True

    async def run(self, plan: CleanupPlan, *, trigger: str = "manual") -> CleanupRunRow:
        """Delete what the approved plan says, oldest first, stopping on the first error.

        The plan is a parameter rather than recomputed here: it is the one a
        person confirmed, and a run that re-planned at the moment the task
        started could delete a shot that arrived in the half second between.
        Every delete still goes through the gated client, so every one of them
        is re-authorised against the archive and audited in `device_writes`
        whatever happens to it — a shot that stopped being eligible since the
        preview is refused there, and the run stops. `deleted_on_device` is set
        here rather than waiting for the next index diff: the row is how the
        archive stops offering the same shot again, and a run that deleted forty
        shots should not depend on a later pass to say so.
        """
        run_id = await self.cleanup.start_run(
            mode=plan.policy.mode,
            target=plan.policy.target,
            trigger=trigger,
            planned=len(plan.planned),
            free_before=plan.free_bytes,
        )
        self._publish(
            {
                "status": "started",
                "run_id": run_id,
                "planned": len(plan.planned),
                "trigger": trigger,
            }
        )
        update = CleanupRunUpdate()
        try:
            if self.client is None:
                update.errors += 1
                update.error = "No machine is configured, so nothing was deleted."
            else:
                await self._delete_all(plan, update)
        except Exception as exc:  # pragma: no cover - defensive; a run must always close
            update.errors += 1
            update.error = f"{type(exc).__name__}: {exc}"
            log.error("cleanup_run_crashed", run_id=run_id, exc_info=True)
        finally:
            update.free_after = self._free_space()[0]
            await self.cleanup.finish_run(run_id, update)

        finished = await self.cleanup.get_run(run_id)
        assert finished is not None  # finish_run wrote it
        self._publish(
            {
                "status": finished.status,
                "run_id": run_id,
                "deleted": finished.deleted,
                "planned": finished.planned,
                "error": finished.error,
            }
        )
        log.info(
            "cleanup_run_finished",
            run_id=run_id,
            planned=finished.planned,
            deleted=finished.deleted,
            errors=finished.errors,
        )
        return finished

    async def _delete_all(self, plan: CleanupPlan, update: CleanupRunUpdate) -> None:
        """One delete per shot, paced, stopping at the first thing the machine refuses.

        The index is re-read once here rather than taken from the plan, because
        the delete takes the notes file with it and the `HAS_NOTES` flag is the
        only thing that says there is one. A plan built a minute ago cannot know
        that somebody has since opened the notes card on the touchscreen.
        """
        assert self.client is not None
        entries = await self._index_by_id()
        last = 0.0
        for item in plan.planned:
            wait = self.pace_seconds - (time.monotonic() - last)
            if last and wait > 0:
                await asyncio.sleep(wait)
            last = time.monotonic()
            if not await self._notes_are_safe(item, entries.get(item.device_id), update):
                continue
            try:
                await self.client.delete_shot(item.device_id)
            except DeviceError as exc:
                # Including a refusal from the gate: it never reached the wire,
                # it is already audited with its reason, and it means the plan
                # and the gate disagree — which is a stop, not a skip.
                update.errors += 1
                update.error = str(exc)
                log.info(
                    "cleanup_delete_refused",
                    device_id=item.device_id,
                    error=str(exc),
                    deleted_so_far=update.deleted,
                )
                return
            await self.shots.mark_deleted_on_device(item.shot_id)
            update.deleted += 1

    async def _index_by_id(self) -> dict[str, IndexEntry]:
        """The machine's index right now, keyed by padded id.

        A failed read is not fatal here — an empty map means every shot is
        treated as "we cannot prove it has no notes", which
        :meth:`_notes_are_safe` turns into a pull attempt rather than into a
        delete. Being wrong in that direction costs one HTTP request.
        """
        assert self.client is not None
        try:
            index = await self.client.fetch_index()
        except DeviceError as exc:
            log.info("cleanup_index_unreadable", error=str(exc))
            return {}
        if index is None:
            return {}
        return {pad6(entry.id): entry for entry in index.entries}

    async def _notes_are_safe(
        self, item: PlannedShot, entry: IndexEntry | None, update: CleanupRunUpdate
    ) -> bool:
        """Pull the machine's notes card before the delete takes it away.

        `req:history:delete` removes `/h/<id>.json` along with the `.slog`, and
        the notes mirror is **not** kept current by the shot sync: notes are
        re-pulled only when the index's `rating` or `volume` changes, because
        those are the only two fields the firmware writes back into the index
        entry. Edit the *text* on the touchscreen and nothing in the index moves
        — so a shot whose note was reworded since the last pull would be deleted
        with the only copy of that wording on it.

        So: if the index still says `HAS_NOTES`, the card is fetched and
        mirrored first. A fetch that **fails** skips the shot with a reason and
        an audit row rather than stopping the run — the machine is answering,
        this one file is not, and the other three hundred shots are unaffected.
        A 404 is not a failure: the flag is stale and there is nothing to save.
        """
        assert self.client is not None
        if entry is not None and not entry.has_notes:
            return True
        try:
            notes = await self.client.fetch_notes_json(item.device_id)
        except DeviceError as exc:
            reason = (
                f"Shot {item.device_id} was not deleted: its notes card could not be read "
                f"first, and deleting would take the only copy with it ({exc})."
            )
            update.errors += 1
            update.error = reason
            await self._audit_refusal(item.device_id, reason)
            log.info("cleanup_notes_unreadable", device_id=item.device_id, error=str(exc))
            return False
        if notes is not None:
            await self.notes.upsert(
                item.shot_id,
                notes,
                index_rating=entry.rating if entry is not None else None,
                index_volume_g=entry.volume_g if entry is not None else None,
            )
            log.info("cleanup_notes_preserved", device_id=item.device_id, shot_id=item.shot_id)
        return True

    async def _audit_refusal(self, device_id: str, reason: str) -> None:
        """Record a delete this service declined, in the same table the gate uses.

        The gate audits what it refuses; this is the one refusal that happens
        above it, and leaving it out would make `device_writes` answer "nothing
        tried to delete that shot" when something did.
        """
        host = self.client.host if self.client is not None else ""
        with contextlib.suppress(Exception):  # bookkeeping must not break a run
            await self.writes.record(
                DeviceWriteWrite(
                    kind="shot_delete",
                    host=host,
                    device_id=device_id,
                    payload_hash=payload_hash(device_id),
                    result="refused",
                    error=reason[:500],
                )
            )

    def _publish(self, data: dict[str, Any]) -> None:
        if self.bus is not None:
            self.bus.publish(SseEvent(event=CLEANUP_EVENT, data=data))


def _no_machine() -> ServiceUnavailable:
    return ServiceUnavailable(
        "No machine is configured, so there is nothing to clean up. "
        "Set `gaggimateHost` (and leave `deviceSyncEnabled` on) in settings."
    )


def _planned_reason(policy: CleanupPolicy) -> str:
    """The sentence each planned shot carries, from the part of the policy that chose it.

    One sentence per policy rather than per shot: the policy picks from the
    oldest end, so every shot in a plan is there for the same reason, and the
    eligibility half ("archived here intact") is true of all of them by
    construction.
    """
    if policy.mode == "keep_newest":
        return (
            f"Older than the newest {policy.keep_newest} shots the policy keeps on the machine; "
            "archived here intact."
        )
    if policy.mode == "free_space":
        return (
            f"Among the oldest shots while free space is under {policy.min_free_kb} KB; "
            "archived here intact."
        )
    return ""
