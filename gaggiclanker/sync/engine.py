"""`SyncEngine` — the loop that keeps SQLite equal to the machine.

Four jobs, one lock:

* **identity** — on every connect, fold `res:ota-settings`, the status state
  frame's capability flags and `GET /api/settings` into one `machines` row.
* **shots** — diff `index.bin` against what we hold, fetch what is missing,
  parse it, derive diagnostics and a score, store the lot in one transaction;
  reconcile the entries whose rating, volume or deleted flag changed.
* **profiles** — mirror `/p/` as content-hashed versions plus a device-id map.
* **notes** — pull `/h/<id>.json` for entries flagged `HAS_NOTES`, and re-pull
  when the index says the rating or the dose behind them moved.

Three rules that are easy to get wrong and expensive to get wrong:

**`evt:history-shot-saved` means "go and look", never "here is a shot."** The
device event bus is lossy by design (`infra/sse.py`) and the socket is down
whenever the machine reboots or somebody opens its web UI as a fourth client.
So the push only *pokes* the same index diff that runs on a timer and on every
reconnect, and a missed push costs latency rather than a shot.

**The bytes are the product.** A `.slog` that does not parse is stored with
`quarantined = 1`, its reason and its raw bytes, and produces no sample rows.
The machine will have deleted its copy long before anyone fixes the parser.

**Two HTTP requests at a time, and writes are serial.** The display serves its
own web UI from the same 300 KB of heap; a third parallel fetch is how you get
HTML back instead of binary. Fetches are pipelined through two workers, and the
results are written by the single coroutine draining them — one connection
cannot have two transactions open at once.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import dumps
from gaggiclanker.db.repos.judgements import JudgementsRepository
from gaggiclanker.db.repos.machines import MachineRow, MachinesRepository, identity_to_upsert
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository, ShotState
from gaggiclanker.db.repos.sync import SyncRepository, SyncRunRow, SyncRunUpdate
from gaggiclanker.device.client import GaggimateClient, SlogFetch
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.device.events import (
    Connected,
    DeviceEvent,
    Disconnected,
    IdentityChanged,
    ShotSaved,
    StatusChanged,
)
from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.models import IndexEntry, LiveStatus
from gaggiclanker.infra.sse import SseEvent, SseEventBus
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.sync.derive import derive_shot, index_fields

__all__ = [
    "PROFILE_UPDATED_EVENT",
    "SHOT_INGESTED_EVENT",
    "SHOT_QUARANTINED_EVENT",
    "SHOT_UPDATED_EVENT",
    "SYNC_PROGRESS_EVENT",
    "SyncEngine",
    "downsample",
]

log = structlog.get_logger(__name__)

# ── the SSE vocabulary ───────────────────────────────────────────────
#
# These strings are a contract with the front end: `web/src/lib/invalidate.ts`
# maps each one to the query keys it invalidates. An event says "this family is
# stale, go and re-read" and never carries the new value, because the bus drops
# events under backpressure and a UI that trusted a payload would silently miss
# one.
SYNC_PROGRESS_EVENT = "sync.progress"
SHOT_INGESTED_EVENT = "shot.ingested"
SHOT_UPDATED_EVENT = "shot.updated"
SHOT_QUARANTINED_EVENT = "shot.quarantined"
PROFILE_UPDATED_EVENT = "profile.updated"

#: How often to re-diff the index and re-read the profile list with nothing
#: else prompting us. Fifteen minutes is the chunk spec's figure: long enough to
#: be invisible on the device's heap, short enough that a shot pulled while the
#: socket was down is archived before the machine's storage pressure notices it.
DEFAULT_INDEX_INTERVAL_S = 900.0
DEFAULT_PROFILE_INTERVAL_S = 900.0

#: How many consecutive transport failures mean "the machine has gone", rather
#: than "that shot was unreadable". Three: enough that a single dropped request
#: does not abandon a backfill, few enough that a machine switched off mid-pass
#: costs three timeouts instead of one per remaining shot.
MAX_CONSECUTIVE_DEVICE_ERRORS = 3

#: Two in-flight HTTP fetches, never more. The client
#: enforces the same bound with its own semaphore; this one keeps the *pipeline*
#: that deep so a slow parse does not leave both device slots idle.
FETCH_CONCURRENCY = 2


@dataclass(slots=True)
class _Poke:
    """A "go and look" signal carrying the reason it was raised.

    An :class:`asyncio.Event` alone would tell the loop to run but not why, and
    the why is what separates a `live` run from a `backfill` run in the ledger.
    """

    event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = "startup"

    def raise_(self, reason: str) -> None:
        # Last reason wins: several pushes arriving while a run is in flight
        # coalesce into one pass, which is the point of poking rather than
        # spawning.
        self.reason = reason
        self.event.set()

    async def wait(self, timeout: float, *, on_timeout: str) -> str:  # noqa: ASYNC109
        """Block for a poke, or ``timeout`` seconds, and say which happened.

        The timeout is a parameter rather than the caller's own
        ``asyncio.timeout`` block (what ASYNC109 asks for) because the *return
        value* is the point: "was this a poke or the periodic tick" is the
        answer, and a caller writing its own try/except would have to
        reconstruct it.
        """
        try:
            async with asyncio.timeout(timeout):
                await self.event.wait()
        except TimeoutError:
            return on_timeout
        self.event.clear()
        return self.reason


@dataclass(slots=True)
class _Fetched:
    """One shot's bytes coming off the fetch pipeline, or the reason there are none."""

    device_id: str
    entry: IndexEntry | None
    fetch: SlogFetch | None = None
    error: str | None = None
    #: The failure was a transport one (gone, timed out, mid-OTA) rather than
    #: this shot being unreadable. Several in a row mean the machine, not the
    #: shot — see :data:`MAX_CONSECUTIVE_DEVICE_ERRORS`.
    retryable: bool = False
    missing: bool = False


class SyncEngine:
    """Keeps the archive equal to one machine. Owns no HTTP surface of its own."""

    def __init__(
        self,
        client: GaggimateClient,
        db: Database,
        bus: SseEventBus,
        *,
        index_interval: float = DEFAULT_INDEX_INTERVAL_S,
        profile_interval: float = DEFAULT_PROFILE_INTERVAL_S,
        concurrency: int = FETCH_CONCURRENCY,
    ) -> None:
        self.client = client
        self.db = db
        self.bus = bus
        self.index_interval = index_interval
        self.profile_interval = profile_interval
        self.concurrency = max(1, concurrency)

        self.machines = MachinesRepository(db)
        self.profiles = ProfilesRepository(db)
        self.shots = ShotsRepository(db)
        self.notes = NotesRepository(db)
        self.runs = SyncRepository(db)
        self.sets = SetsRepository(db)
        self.judgements = JudgementsRepository(db)

        self._machine: MachineRow | None = None
        self._shot_poke = _Poke()
        self._profile_poke = _Poke()
        #: Set when somebody asks for a fresh identity read. Identity has no loop
        #: of its own — it runs on every `Connected`, which is the only moment
        #: the answer can have changed — so a manual request rides the profiles
        #: loop rather than growing a third timer that would never fire.
        self._identity_requested: str | None = None
        #: Ids pushed by `evt:history-shot-saved` that the index has not caught
        #: up with yet. The firmware writes the index entry and the event in
        #: quick succession but not atomically, so a push can beat its own row.
        self._pushed_ids: set[int] = set()
        #: The last capability flags seen on a status frame, and the selected
        #: profile id. `None` means "we have not seen a state frame", which is a
        #: different fact from "this board has no pressure sensor".
        self._capabilities: tuple[bool | None, ...] | None = None
        self._selected_profile_id: str | None = None
        #: Whether the last shot pass actually got an `index.bin` off the
        #: machine. ``False`` after a 404 or a failed fetch, which is a
        #: different fact from "the index listed nothing" — see `_read_index`.
        self._last_index_was_full = False
        #: One device pass at a time. The machine has two HTTP slots and one
        #: socket; two passes would fight over both, and SQLite's single
        #: connection cannot hold two transactions anyway.
        self._lock = asyncio.Lock()
        #: Called with the machine id after a shot pass that read a **full**
        #: index and finished cleanly, and always **outside** the lock above.
        #: The automatic cleanup hangs off it: deleting shots takes the
        #: machine's socket for as long as it runs, and doing that while holding
        #: the sync lock would block the next shot's ingest behind a retention
        #: policy. The engine neither knows nor decides what happens next —
        #: whether a cleanup is wanted at all is two settings read inside the
        #: task the app's callback spawns.
        self.on_index_synced: Callable[[int], None] | None = None

    # ── lifecycle ────────────────────────────────────────────────────

    @property
    def machine(self) -> MachineRow | None:
        """The machine row, once a pass has created it."""
        return self._machine

    async def start(self, tasks: TaskRegistry) -> None:
        """Spawn the three background loops. Never blocks on the machine.

        Called from the lifespan, which must return in well under a second
        whether or not the espresso machine is powered on: the archive browser
        works with the machine unplugged, and a box that boots first is the
        normal case.
        """
        tasks.spawn("sync-events", self._events_loop())
        tasks.spawn("sync-shots", self._shots_loop())
        tasks.spawn("sync-profiles", self._profiles_loop())
        # Ask for everything once, now. `Connected` is what normally triggers a
        # pass, and the client is started before the engine — so if the socket
        # came up in between (which on a loopback fake it always has) that event
        # is already gone and nothing would happen until the 15 minute timer.
        # Both pokes are coalescing, so a `Connected` arriving a moment later
        # costs nothing.
        self.request_identity_sync("startup")
        self.request_shot_sync("startup")
        self.request_profile_sync("startup")
        log.info("sync_engine_started", host=self.client.host)

    def request_shot_sync(self, reason: str = "manual") -> None:
        """Ask for an index diff on the next turn of the loop."""
        self._shot_poke.raise_(reason)

    def request_profile_sync(self, reason: str = "manual") -> None:
        """Ask for a profile mirror on the next turn of the loop."""
        self._profile_poke.raise_(reason)

    def request_identity_sync(self, reason: str = "manual") -> None:
        """Ask for a fresh read of what the machine is, on the profiles loop."""
        self._identity_requested = reason
        self._profile_poke.raise_(reason)

    # ── the loops ────────────────────────────────────────────────────

    async def _events_loop(self) -> None:
        """Turn device events into pokes. It does nothing else, ever.

        This coroutine is the only consumer of the client's event stream, and
        that stream is **lossy under backpressure**: a subscriber that falls
        behind drops its oldest events (`infra/sse.py`). At 2 Hz telemetry a
        256-deep queue is about two minutes — so anything awaited here that can
        take minutes silently eats a `ShotSaved`.

        It used to await `sync_identity()`, which takes the engine lock. During
        a backfill the lock is held for as long as the fetches take, the loop
        parked behind it, the queue filled with status frames, and the shot the
        user had just pulled was dropped and not archived until the next
        fifteen-minute re-diff. So: every handler here is synchronous, sets
        in-memory state, and raises a poke. The worker loops do the waiting.
        """
        async for event in self.client.subscribe():
            try:
                self._handle_device_event(event)
            except Exception:  # a bad frame must not end the subscription
                log.warning("sync_event_failed", event=type(event).__name__, exc_info=True)

    def _handle_device_event(self, event: DeviceEvent) -> None:
        """Synchronous by contract — see :meth:`_events_loop`."""
        match event:
            case Connected():
                # Everything at once: a reconnect is exactly the moment we may
                # have missed a shot, a profile save and a firmware update.
                self.request_identity_sync("connected")
                self.request_shot_sync("connected")
                self.request_profile_sync("connected")
            case Disconnected(reason=reason):
                log.info("sync_device_disconnected", reason=reason)
            case ShotSaved(shot_id=shot_id):
                self._pushed_ids.add(shot_id)
                self.request_shot_sync("shot_saved")
            case StatusChanged(status=status):
                self._note_status(status)
            case IdentityChanged():
                self.request_identity_sync("broadcast")
            case _:
                return

    def _note_status(self, status: LiveStatus) -> None:
        """Record capability flags and the selected profile, when either moves.

        Called at 2 Hz, so everything here is a comparison against what we
        already hold: a write per telemetry frame would be a hundred thousand
        pointless UPDATEs a day.

        It records and pokes; it does not write. The row is written by the
        identity pass, under the lock, because this runs on the events loop and
        the database is one shared connection the sync worker is already using.
        """
        capabilities = (status.cp, status.cd, status.gp, status.led)
        if any(flag is not None for flag in capabilities) and capabilities != self._capabilities:
            self._capabilities = capabilities
            self.request_identity_sync("capabilities_changed")

        if status.puid is not None and status.puid != self._selected_profile_id:
            # The selected profile lives in NVS, not in the profile JSON, so
            # `puid` moving is the only push that says the mirror is stale.
            first_sighting = self._selected_profile_id is None
            self._selected_profile_id = status.puid
            if not first_sighting:
                self.request_profile_sync("puid_changed")

    async def _shots_loop(self) -> None:
        while True:
            trigger = await self._shot_poke.wait(self.index_interval, on_timeout="periodic")
            kind = "live" if trigger == "shot_saved" else "backfill"
            with contextlib.suppress(Exception):
                await self.sync_shots(kind=kind, trigger=trigger)

    async def _profiles_loop(self) -> None:
        while True:
            trigger = await self._profile_poke.wait(self.profile_interval, on_timeout="periodic")
            requested, self._identity_requested = self._identity_requested, None
            if requested is not None:
                with contextlib.suppress(Exception):
                    await self.sync_identity(trigger=requested)
            with contextlib.suppress(Exception):
                await self.sync_profiles(trigger=trigger)

    # ── identity ─────────────────────────────────────────────────────

    async def _ensure_machine(self) -> MachineRow:
        """The `machines` row for the configured host, created if this is the first pass.

        Created before anything else needs it because `shots.machine_id` is NOT
        NULL: a nullable owner would make the `(machine_id, device_id)` unique
        index useless (SQLite treats NULLs as distinct) and let the same shot in
        twice.
        """
        if self._machine is None:
            self._machine = await self.machines.upsert(
                identity_to_upsert(self.client.host, status=self.client.last_status)
            )
        return self._machine

    async def sync_identity(self, *, trigger: str = "manual") -> SyncRunRow | None:
        """Read what the machine is, and store it.

        `GET /api/settings` is read-only here and always will be: the POST
        counterpart clears every boolean key absent from its body, which is how
        a partial write silently turns off HomeKit and the boiler fill. The
        client does not expose one.
        """
        async with self._lock:
            machine = await self._ensure_machine()
            run_id = await self.runs.start_run("identity", trigger)
            update = SyncRunUpdate()
            settings: dict[str, Any] | None = None
            try:
                settings = await self.client.get_settings()
            except DeviceError as exc:
                # Not fatal: the versions and capabilities we already have are
                # worth storing without the PID string.
                update.errors += 1
                update.error = str(exc)
                log.info("sync_identity_settings_unavailable", error=str(exc))

            await self.machines.upsert(
                identity_to_upsert(
                    self.client.host,
                    identity=self.client.identity,
                    status=self.client.last_status,
                    settings=settings,
                )
            )
            self._machine = await self.machines.get(machine.id)
            await self.runs.finish_run(run_id, update)
            self._publish(SYNC_PROGRESS_EVENT, {"kind": "identity", "status": "finished"})
            return await self.runs.get_run(run_id)

    # ── shots ────────────────────────────────────────────────────────

    async def sync_shots(self, *, kind: str = "backfill", trigger: str = "manual") -> SyncRunRow:
        """One index diff: fetch what is missing, reconcile what changed, pull notes.

        The post-pass hook fires **after** the lock is released and only for a
        run that both succeeded and read a real index: a pass that could not
        reach the machine has learned nothing about what is on it, and acting on
        that would be a retention policy driven by a network fault.
        """
        async with self._lock:
            run = await self._sync_shots(kind=kind, trigger=trigger)
        if (
            self.on_index_synced is not None
            and run.status == "ok"
            and self._last_index_was_full
            and self._machine is not None
        ):
            self.on_index_synced(self._machine.id)
        return run

    async def _sync_shots(self, *, kind: str, trigger: str) -> SyncRunRow:
        machine = await self._ensure_machine()
        run_id = await self.runs.start_run(kind, trigger)
        update = SyncRunUpdate()
        self._publish(
            SYNC_PROGRESS_EVENT,
            {"kind": kind, "status": "started", "run_id": run_id, "trigger": trigger},
        )

        entries: dict[str, IndexEntry] | None = None
        self._last_index_was_full = False
        # try/finally around the whole pass, not just the fetches: a run left
        # at status "running" is a run nothing ever closes, and
        # `GET /api/sync/status` would report a sync in progress for ever.
        try:
            entries = await self._read_index()
            listed = entries or {}
            known = await self.shots.known_states(machine.id)
            update.shots_seen = len(listed)

            self._last_index_was_full = entries is not None
            missing = self._missing_entries(listed, known)
            await self._fetch_and_store(machine, missing, run_id=run_id, update=update)
            await self._reconcile(
                listed, known, run_id=run_id, update=update, full_index=entries is not None
            )
        except DeviceError as exc:
            update.errors += 1
            update.error = str(exc)
            log.info("sync_run_failed", kind=kind, error=str(exc), retryable=exc.retryable)
        except Exception as exc:
            update.errors += 1
            update.error = f"{type(exc).__name__}: {exc}"
            log.error("sync_run_crashed", kind=kind, exc_info=True)
        finally:
            await self.runs.finish_run(run_id, update)

        finished = await self.runs.get_run(run_id)
        assert finished is not None  # finish_run wrote it
        self._publish(
            SYNC_PROGRESS_EVENT,
            {
                "kind": kind,
                "status": finished.status,
                "run_id": run_id,
                "inserted": update.shots_inserted,
                "updated": update.shots_updated,
                "quarantined": update.shots_quarantined,
                "error": update.error,
            },
        )
        if finished.status == "ok" and entries is not None:
            await self._sync_notes(machine, entries, trigger=trigger)
        await self.runs.trim_events()
        return finished

    async def _read_index(self) -> dict[str, IndexEntry] | None:
        """`index.bin`, keyed by padded id, or ``None`` when there is no index file.

        The firmware never removes index rows and duplicates can exist (report
        §2.3), so the last entry for an id wins — that is the one whose flags
        the device's own UI honours.

        ``None`` and ``{}`` are different answers and the difference matters to
        :meth:`_reconcile`. ``None`` is a 404 — a machine that has never
        recorded a shot, or one mid-`req:history:rebuild` — and says nothing
        about what the machine still holds. ``{}`` is an index the machine
        served that lists nothing, which does.
        """
        index = await self.client.fetch_index()
        if index is None:
            return None
        return {pad6(entry.id): entry for entry in index.entries}

    def _missing_entries(
        self, entries: dict[str, IndexEntry], known: dict[str, ShotState]
    ) -> list[tuple[str, IndexEntry | None]]:
        """What to fetch, oldest first.

        Oldest first because the device deletes its **oldest** `.slog` when free
        space drops below 500 KB (`cleanupHistory`). The shots at the front of
        the index are the ones we may not get another chance at; the newest one
        is already safe for another few hundred shots.

        A pushed id the index has not listed yet is appended: `evt:history-
        shot-saved` and the index write are not atomic, and a live ingest that
        waited for the next poll would be a minute late for no reason.
        """
        missing: list[tuple[str, IndexEntry | None]] = [
            (device_id, entry)
            for device_id, entry in sorted(entries.items())
            # A deleted entry whose file we never fetched is gone from the
            # machine; there is nothing left to archive.
            if device_id not in known and not entry.deleted
        ]
        for shot_id in sorted(self._pushed_ids):
            device_id = pad6(shot_id)
            if device_id not in entries and device_id not in known:
                missing.append((device_id, None))
        self._pushed_ids.clear()
        return missing

    async def _fetch_and_store(
        self,
        machine: MachineRow,
        missing: Sequence[tuple[str, IndexEntry | None]],
        *,
        run_id: int,
        update: SyncRunUpdate,
    ) -> None:
        """Fetch with two workers, store with one.

        The split is not an optimisation, it is a correctness requirement: the
        whole app shares one SQLite connection, so two coroutines opening
        transactions would collide. Fetching is what benefits from concurrency
        anyway — the device is the slow part.
        """
        if not missing:
            return
        consecutive = 0
        async for fetched in self._fetch_pipeline(missing):
            # A machine that went away does not come back within this pass, and
            # every remaining shot would cost a full request timeout — two at a
            # time, under the lock, with the shots loop and the profiles loop
            # both waiting behind it. Three in a row is the machine; give up and
            # let the next `Connected` start a fresh pass.
            consecutive = consecutive + 1 if fetched.retryable else 0
            if consecutive >= MAX_CONSECUTIVE_DEVICE_ERRORS:
                update.errors += 1
                update.error = fetched.error or "the machine stopped answering"
                log.info(
                    "sync_backfill_abandoned",
                    after=update.shots_inserted,
                    remaining=len(missing) - update.shots_inserted,
                    error=update.error,
                )
                await self.runs.add_event(
                    "error",
                    run_id=run_id,
                    device_id=fetched.device_id,
                    message=(
                        f"gave up after {MAX_CONSECUTIVE_DEVICE_ERRORS} consecutive "
                        f"device errors: {update.error}"
                    ),
                )
                return
            try:
                await self._store(machine, fetched, run_id=run_id, update=update)
            except Exception as exc:  # one bad shot must not end the backfill
                update.errors += 1
                log.warning("sync_store_failed", device_id=fetched.device_id, exc_info=True)
                await self.runs.add_event(
                    "error",
                    run_id=run_id,
                    device_id=fetched.device_id,
                    message=f"storing the shot failed: {exc}",
                )

    async def _fetch_pipeline(
        self, missing: Sequence[tuple[str, IndexEntry | None]]
    ) -> AsyncIterator[_Fetched]:
        """Yield fetched shots as they arrive, at most ``concurrency`` in flight.

        A shared iterator rather than a queue of work: ``next()`` never awaits,
        so two workers pulling from it cannot get the same item, and the
        bounded results queue is what applies backpressure when the writer falls
        behind.
        """
        pending: Iterator[tuple[str, IndexEntry | None]] = iter(missing)
        results: asyncio.Queue[_Fetched | None] = asyncio.Queue(maxsize=self.concurrency)

        async def worker() -> None:
            try:
                for device_id, entry in pending:
                    await results.put(await self._fetch_one(device_id, entry))
            finally:
                await results.put(None)

        workers = [asyncio.create_task(worker()) for _ in range(self.concurrency)]
        finished = 0
        try:
            while finished < len(workers):
                item = await results.get()
                if item is None:
                    finished += 1
                    continue
                yield item
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def _fetch_one(self, device_id: str, entry: IndexEntry | None) -> _Fetched:
        """One `.slog`, with every failure turned into data.

        Never raises: this runs inside a worker whose death would stall the
        pipeline, and a machine that goes away mid-backfill is an ordinary
        Tuesday rather than an exception worth unwinding for.
        """
        try:
            fetch = await self.client.fetch_slog(device_id)
        except DeviceError as exc:
            return _Fetched(
                device_id=device_id, entry=entry, error=str(exc), retryable=exc.retryable
            )
        if fetch is None:
            # 404: the index lists it but the file is gone. The firmware clears
            # the index row when it deletes a file, so this is a race with
            # `cleanupHistory` rather than a corrupt index.
            return _Fetched(device_id=device_id, entry=entry, missing=True)
        return _Fetched(device_id=device_id, entry=entry, fetch=fetch)

    async def _store(
        self, machine: MachineRow, fetched: _Fetched, *, run_id: int, update: SyncRunUpdate
    ) -> None:
        """Turn one fetched shot into rows."""
        if fetched.error is not None:
            update.errors += 1
            await self.runs.add_event(
                "error", run_id=run_id, device_id=fetched.device_id, message=fetched.error
            )
            return
        if fetched.missing or fetched.fetch is None:
            await self.runs.add_event(
                "shot_gone",
                run_id=run_id,
                device_id=fetched.device_id,
                message="the machine no longer has this shot's file",
            )
            return

        fetch = fetched.fetch
        entry = fetched.entry
        if fetch.slog is None:
            await self._store_quarantined(
                machine,
                fetched,
                reason=fetch.parse_error or "the .slog did not parse",
                run_id=run_id,
                update=update,
            )
            return

        try:
            derived = derive_shot(
                fetch.slog,
                fetch.raw,
                machine_id=machine.id,
                device_id=fetched.device_id,
                source="device",
                has_pressure=self._has_pressure(),
                entry=entry,
                incomplete=fetch.incomplete,
            )
        except Exception as exc:
            # The bytes parsed but something downstream of the parse refused
            # them. Quarantine rather than drop: the header we could not model
            # is exactly the evidence the next fix needs.
            await self._store_quarantined(
                machine, fetched, reason=f"validation failed: {exc}", run_id=run_id, update=update
            )
            return
        shot, samples = derived.shot, derived.samples
        if derived.diagnostics_error is not None:
            update.errors += 1

        shot.profile_version_id = await self._version_for(machine.id, shot.profile_id_on_device)
        shot_id = await self.shots.insert(shot, samples)
        # The user's half of the shot: which Set was this, and what did they
        # think of it. Both are best-effort and neither can fail an ingest —
        # the archive's job is to hold the bytes, and a Set that does not match
        # leaves the shot in the `needs_set` inbox rather than losing it.
        set_version_id = await self.sets.auto_assign(
            shot_id,
            machine_id=machine.id,
            profile_version_id=shot.profile_version_id,
            device_profile_id=shot.profile_id_on_device,
        )
        update.shots_inserted += 1
        await self.runs.add_event(
            "shot_ingested",
            run_id=run_id,
            shot_id=shot_id,
            device_id=shot.device_id,
            message=f"{len(samples)} samples",
            data={
                "samples": len(samples),
                "score": shot.execution_score,
                "set_version_id": set_version_id,
            },
        )
        self._publish(
            SHOT_INGESTED_EVENT,
            {"shot_id": shot_id, "device_id": shot.device_id, "samples": len(samples)},
        )
        log.info(
            "shot_ingested",
            shot_id=shot_id,
            device_id=shot.device_id,
            samples=len(samples),
            quarantined=False,
            set_version_id=set_version_id,
        )

    async def _store_quarantined(
        self,
        machine: MachineRow,
        fetched: _Fetched,
        *,
        reason: str,
        run_id: int,
        update: SyncRunUpdate,
    ) -> None:
        """Store bytes we could not read, with the reason, and no samples.

        The archive's central rule in one method. Losing a shot to a parser bug is
        worse than storing one we cannot read yet, because by the time the bug
        is fixed the machine will have deleted its copy.
        """
        fetch = fetched.fetch
        assert fetch is not None  # only called with bytes in hand
        entry = fetched.entry
        shot = ShotInsert(
            device_id=fetched.device_id,
            machine_id=machine.id,
            raw_slog=fetch.raw,
            quarantined=True,
            quarantine_reason=reason,
            incomplete=fetch.incomplete,
            **index_fields(entry),
        )
        shot_id = await self.shots.insert(shot)
        # Counted as an insert *and* as a quarantine: the run's `shots_inserted`
        # is "rows this pass added to the archive", and a shot we could not read
        # is still archived — that is the whole point of quarantining it.
        update.shots_inserted += 1
        update.shots_quarantined += 1
        await self.runs.add_event(
            "shot_quarantined",
            run_id=run_id,
            shot_id=shot_id,
            device_id=shot.device_id,
            message=reason,
            data={"bytes": len(fetch.raw)},
        )
        self._publish(
            SHOT_QUARANTINED_EVENT,
            {"shot_id": shot_id, "device_id": shot.device_id, "reason": reason},
        )
        log.warning(
            "shot_quarantined",
            shot_id=shot_id,
            device_id=shot.device_id,
            reason=reason,
            bytes=len(fetch.raw),
        )

    def _has_pressure(self) -> bool | None:
        """Whether pressure telemetry means anything on this machine.

        Three answers, and the third is the important one. ``True`` is a Pro
        board; ``False`` is a Standard board, which reports a hard zero for
        pressure and on which every pressure-derived diagnostic is confident
        nonsense; ``None`` is "nobody has told us yet", which makes the
        diagnostics decide from the trace instead.

        Only the status **state** frame carries `cp`, and it arrives once per
        connection. Until one has, this must be ``None`` — not the `machines`
        row, whose `has_pressure` defaults to 0 and would make every shot
        ingested before the first state frame look like a Standard board's.
        """
        status = self.client.last_status
        if status is not None and status.cp is not None:
            return status.cp
        if self._capabilities is not None:
            return self._capabilities[0]
        return None

    async def _version_for(self, machine_id: int, profile_id: str) -> int | None:
        if not profile_id:
            return None
        version = await self.profiles.find_version_for_device_profile(machine_id, profile_id)
        return None if version is None else version.id

    async def _reconcile(
        self,
        entries: dict[str, IndexEntry],
        known: dict[str, ShotState],
        *,
        run_id: int,
        update: SyncRunUpdate,
        full_index: bool,
    ) -> None:
        """Apply the index entries that changed under shots we already hold.

        The device rewrites index rows **in place**: a rating typed on the
        machine, a dose entered in its notes card (which overrides `volume`),
        and the deleted flag `cleanupHistory()` sets when it frees space. None
        of that touches the `.slog`, so re-fetching the file would learn
        nothing — this is the only path by which those facts reach the archive.

        A shot **absent** from the index counts as deleted too, but only when
        ``full_index`` says the machine actually served one. `req:history:rebuild`
        regenerates `index.bin` from the `.slog` files that are still there, so
        after a rebuild a deleted shot has no row at all rather than a flagged
        one — and a run that could not read the index must not conclude from
        that that the machine has thrown everything away.
        """
        for device_id, state in known.items():
            entry = entries.get(device_id)
            if entry is None:
                if full_index and not state.deleted_on_device:
                    await self.shots.mark_deleted_on_device(state.id)
                    update.shots_updated += 1
                    await self.runs.add_event(
                        "shot_updated",
                        run_id=run_id,
                        shot_id=state.id,
                        device_id=device_id,
                        message="gone from the machine's index",
                        data={"deleted_on_device": True},
                    )
                    self._publish(
                        SHOT_UPDATED_EVENT,
                        {"shot_id": state.id, "device_id": device_id, "deleted_on_device": True},
                    )
                continue
            if (
                state.index_rating == entry.rating
                and state.index_volume_g == entry.volume_g
                and state.index_flags == entry.flags
                and state.deleted_on_device == entry.deleted
            ):
                continue
            await self.shots.update_index_fields(
                state.id,
                rating=entry.rating,
                volume_g=entry.volume_g,
                avg_temp_c=entry.avg_temp_c,
                max_pressure_bar=entry.max_pressure_bar,
                avg_flow_ml_s=entry.avg_flow_ml_s,
                flags=entry.flags,
                deleted_on_device=entry.deleted,
            )
            update.shots_updated += 1
            await self.runs.add_event(
                "shot_updated",
                run_id=run_id,
                shot_id=state.id,
                device_id=device_id,
                message="the device's index entry changed",
                data={
                    "rating": entry.rating,
                    "volume_g": entry.volume_g,
                    "deleted_on_device": entry.deleted,
                },
            )
            self._publish(
                SHOT_UPDATED_EVENT,
                {
                    "shot_id": state.id,
                    "device_id": device_id,
                    "deleted_on_device": entry.deleted,
                },
            )

    # ── notes ────────────────────────────────────────────────────────

    async def _sync_notes(
        self, machine: MachineRow, entries: dict[str, IndexEntry], *, trigger: str
    ) -> SyncRunRow | None:
        """Pull `/h/<id>.json` for every `HAS_NOTES` entry that we do not hold current.

        Over HTTP rather than `req:history:notes:get`, because HTTP is the route
        that still works when all three of the device's WebSocket slots are
        taken by browser tabs.

        Sequential, not pipelined: these documents are a few hundred bytes and
        there are only ever as many of them as the user has actually written.
        """
        wanted = {device_id: entry for device_id, entry in entries.items() if entry.has_notes}
        if not wanted:
            return None

        held = await self.notes.stale_shot_ids(machine.id)
        shots = await self.shots.known_states(machine.id)
        run_id = await self.runs.start_run("notes", trigger)
        update = SyncRunUpdate()
        try:
            for device_id, entry in sorted(wanted.items()):
                state = shots.get(device_id)
                if state is None:
                    continue
                existing = held.get(device_id)
                if existing is not None and not _notes_are_stale(existing, entry):
                    continue
                try:
                    notes = await self.client.fetch_notes_json(device_id)
                except DeviceError as exc:
                    update.errors += 1
                    update.error = str(exc)
                    continue
                if notes is None:
                    continue
                await self.notes.upsert(
                    state.id,
                    notes,
                    index_rating=entry.rating,
                    index_volume_g=entry.volume_g,
                )
                # The machine's notes card is the same five fields the judgement
                # form asks for, so a shot that arrives with notes and no
                # verdict gets one for free. Exactly once: the repository's
                # insert does nothing on conflict, which is what stops a re-pull
                # — and notes are re-pulled precisely *because* the user edited
                # them on the machine — from overwriting a verdict typed here.
                if await self.judgements.seed_from_device_notes(state.id, notes):
                    log.info("judgement_seeded", shot_id=state.id, device_id=device_id)
                update.notes_synced += 1
                await self.runs.add_event(
                    "notes_synced", run_id=run_id, shot_id=state.id, device_id=device_id
                )
                self._publish(
                    SHOT_UPDATED_EVENT, {"shot_id": state.id, "device_id": device_id, "notes": True}
                )
        finally:
            await self.runs.finish_run(run_id, update)
        return await self.runs.get_run(run_id)

    # ── profiles ─────────────────────────────────────────────────────

    async def sync_profiles(self, *, trigger: str = "manual") -> SyncRunRow:
        """Mirror `/p/` as content-hashed versions plus a device-id map."""
        async with self._lock:
            return await self._sync_profiles(trigger=trigger)

    async def _sync_profiles(self, *, trigger: str) -> SyncRunRow:
        machine = await self._ensure_machine()
        run_id = await self.runs.start_run("profiles", trigger)
        update = SyncRunUpdate()
        self._publish(
            SYNC_PROGRESS_EVENT, {"kind": "profiles", "status": "started", "run_id": run_id}
        )
        try:
            profiles = await self.client.list_profiles()
        except DeviceError as exc:
            return await self._fail_run(run_id, "profiles", update, exc)

        seen: list[str] = []
        selected_id = self._selected_profile_id
        for position, profile in enumerate(profiles):
            if profile.id is None:
                # `req:profiles:list` always stamps an id; one without is a
                # firmware we do not understand, not a profile we can mirror.
                update.errors += 1
                continue
            version, created = await self.profiles.ensure_version(
                profile, device_json=dumps(profile.to_device())
            )
            existing = await self.profiles.get_device_profile(machine.id, profile.id)
            await self.profiles.upsert_device_profile(
                machine_id=machine.id,
                device_id=profile.id,
                version_id=version.id,
                favorite=profile.favorite,
                # `selected` and `favorite` live in the display's NVS, not in
                # the file; the firmware stamps them onto the JSON as it
                # serialises. `puid` from the status frame is the fresher of the
                # two and wins when we have one.
                selected=(profile.id == selected_id) if selected_id else profile.selected,
                position=position,
            )
            await self.shots.link_unlinked_by_device_profile(machine.id, profile.id, version.id)
            seen.append(profile.id)
            if created or existing is None or existing.current_version_id != version.id:
                update.profiles_changed += 1
                await self.runs.add_event(
                    "profile_changed",
                    run_id=run_id,
                    device_id=profile.id,
                    message=profile.label,
                    data={"version_id": version.id, "new_version": created},
                )
                self._publish(
                    PROFILE_UPDATED_EVENT,
                    {"device_id": profile.id, "version_id": version.id, "label": profile.label},
                )

        removed = await self.profiles.mark_missing_deleted(machine.id, seen)
        if removed:
            update.profiles_changed += removed
            self._publish(PROFILE_UPDATED_EVENT, {"deleted": removed})

        await self.runs.finish_run(run_id, update)
        self._publish(
            SYNC_PROGRESS_EVENT,
            {
                "kind": "profiles",
                "status": "finished",
                "run_id": run_id,
                "changed": update.profiles_changed,
            },
        )
        finished = await self.runs.get_run(run_id)
        assert finished is not None  # written by finish_run above
        return finished

    # ── shared ───────────────────────────────────────────────────────

    async def _fail_run(
        self, run_id: int, kind: str, update: SyncRunUpdate, exc: DeviceError
    ) -> SyncRunRow:
        """Close a run the machine cut short.

        A device that is off, mid-OTA or busy is not a fault to shout about: the
        loop comes back on its own timer and on the next `Connected`. It is
        recorded, because "the last six runs all failed" is the thing an
        operator needs to be able to see.
        """
        update.errors += 1
        update.error = str(exc)
        await self.runs.finish_run(run_id, update)
        log.info("sync_run_failed", kind=kind, error=str(exc), retryable=exc.retryable)
        self._publish(
            SYNC_PROGRESS_EVENT,
            {"kind": kind, "status": "error", "run_id": run_id, "error": str(exc)},
        )
        run = await self.runs.get_run(run_id)
        assert run is not None  # written by finish_run above
        return run

    def _publish(self, event: str, data: dict[str, Any]) -> None:
        self.bus.publish(SseEvent(event=event, data=data))


# ── free functions ───────────────────────────────────────────────────


def _notes_are_stale(held: tuple[int, int | None, float | None], entry: IndexEntry) -> bool:
    """Whether the index says the notes we hold have been edited since.

    The comparison is against what the index said *when we pulled*, not against
    what the notes document says: the device writes the rating and `doseOut`
    back into the index entry when the notes card is saved, so a difference here
    is the cheapest possible "go and re-read" signal and costs no request.
    """
    _, synced_rating, synced_volume = held
    return synced_rating != entry.rating or synced_volume != entry.volume_g


def downsample[T](rows: Sequence[T], limit: int) -> list[T]:
    """At most ``limit`` rows, evenly spaced, first and last always kept.

    For sparklines: a list of two hundred shots must not pull two hundred
    hundred-point curves over the wire. Even spacing rather than averaging
    because the caller is drawing a shape, and an averaged curve hides exactly
    the spikes a shape is being scanned for.
    """
    if limit <= 0 or len(rows) <= limit:
        return list(rows)
    if limit == 1:
        return [rows[-1]]
    step = (len(rows) - 1) / (limit - 1)
    return [rows[round(index * step)] for index in range(limit)]
