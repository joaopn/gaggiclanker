"""`SyncEngine` — the passes that make SQLite equal to the machine.

Four jobs, one lock:

* **identity** — at startup and on every connect, fold `res:ota-settings`, the
  status state frame's capability flags and `GET /api/settings` into one
  `machines` row.
* **shots** — diff `index.bin` against what we hold, fetch what is missing,
  parse it, derive diagnostics and a score, store the lot in one transaction;
  reconcile the entries whose rating, volume or deleted flag changed.
* **profiles** — mirror `/p/` as content-hashed versions plus a device-id map.
* **notes** — pull `/h/<id>.json` for entries flagged `HAS_NOTES`, and re-pull
  when the index says the rating or the dose behind them moved.

Three rules that are easy to get wrong and expensive to get wrong:

**Getting data off the machine is something a person asks for.** Shots,
profiles and notes move only when `POST /api/sync/run` asks for them: there is
no timer and no pass triggered by a device event. A mirror that ran on its own
duplicated what the machine's own web UI already shows and spent the device's
two HTTP slots on it; an archive is something you pull into, so the pull is a
button. Identity is the exception and stays automatic — it is one frame plus
one request, it is what tells the header whether the machine is there at all,
and the `machines` row has to exist before any pull can store a shot against it.

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
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import dumps
from gaggiclanker.db.repos.judgements import JudgementsRepository
from gaggiclanker.db.repos.machines import MachineRepository, MachineRow, identity_to_upsert
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
    "LOOP_TASK_NAMES",
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

#: How many consecutive transport failures mean "the machine has gone", rather
#: than "that shot was unreadable". Three: enough that a single dropped request
#: does not abandon a backfill, few enough that a machine switched off mid-pass
#: costs three timeouts instead of one per remaining shot.
MAX_CONSECUTIVE_DEVICE_ERRORS = 3

#: The registry names of the engine's loops. Fixed, because there is one engine
#: at a time: a rebuilt connection stops the old engine's loops, which releases
#: these names, before the new engine claims them.
LOOP_TASK_NAMES: tuple[str, ...] = ("sync-events", "sync-identity", "sync-shots", "sync-profiles")

#: What a pass cut short by a cancellation records. A cancellation counts no
#: device error, and a run closed in a ``finally`` with nothing counted would be
#: filed ``ok`` — a healthy pull of an archive that did not fill.
STOPPED_MESSAGE = (
    "stopped before it finished: the machine connection was rebuilt or the app shut down"
)

#: Two in-flight HTTP fetches, never more. The client
#: enforces the same bound with its own semaphore; this one keeps the *pipeline*
#: that deep so a slow parse does not leave both device slots idle.
FETCH_CONCURRENCY = 2


@dataclass(slots=True)
class _Poke:
    """A "go and look" signal carrying the reason it was raised.

    An :class:`asyncio.Event` alone would tell the loop to run but not why, and
    the why is what the ledger records as a run's `trigger` — the difference
    between a pass somebody asked for and one the socket coming back asked for.
    """

    event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = "startup"

    def raise_(self, reason: str) -> None:
        # Last reason wins: two clicks arriving while a run is in flight
        # coalesce into one pass, which is the point of poking rather than
        # spawning.
        self.reason = reason
        self.event.set()

    async def wait(self) -> str:
        """Block until somebody asks for a pass, and say who asked.

        No timeout, deliberately: the loop wakes when a request arrives and
        never otherwise. A timeout here would be a periodic pass wearing a
        different name.
        """
        await self.event.wait()
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
        concurrency: int = FETCH_CONCURRENCY,
    ) -> None:
        self.client = client
        self.db = db
        self.bus = bus
        self.concurrency = max(1, concurrency)

        self.machines = MachineRepository(db)
        self.profiles = ProfilesRepository(db)
        self.shots = ShotsRepository(db)
        self.notes = NotesRepository(db)
        self.runs = SyncRepository(db)
        self.sets = SetsRepository(db)
        self.judgements = JudgementsRepository(db)
        self._machine: MachineRow | None = None
        self._shot_poke = _Poke()
        self._profile_poke = _Poke()
        #: Identity has a loop of its own because it is the one pass that still
        #: runs unasked — at startup and on every `Connected`. Riding the
        #: profiles poke, as it used to, would mean every automatic identity
        #: read dragged a profile mirror onto the machine behind it.
        self._identity_poke = _Poke()
        #: Ids announced by `evt:history-shot-saved` while the socket was up,
        #: for the next pull to look at even if the index has not caught up.
        #: The firmware writes the index entry and the event in quick succession
        #: but not atomically, so an event can beat its own row — and a pull
        #: asked for in that window would otherwise miss the shot the person
        #: pressed the button for.
        self._pushed_ids: set[int] = set()
        #: The last capability flags seen on a status frame, and the selected
        #: profile id. `None` means "we have not seen a state frame", which is a
        #: different fact from "this board has no pressure sensor".
        self._capabilities: tuple[bool | None, ...] | None = None
        self._selected_profile_id: str | None = None
        #: One device pass at a time. The machine has two HTTP slots and one
        #: socket; two passes would fight over both, and SQLite's single
        #: connection cannot hold two transactions anyway.
        self._lock = asyncio.Lock()
        #: The registry the loops were spawned into, once started.
        self._tasks: TaskRegistry | None = None
        #: Shot and profile passes asked for and not yet finished, counted from
        #: the moment one is requested of the engine rather than from when it
        #: gets the lock — a pull waiting behind an identity read is still a
        #: pull somebody is waiting for. What :meth:`busy` reports.
        self._pulls_in_flight = 0

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
        self._tasks = tasks
        events, identity, shots, profiles = LOOP_TASK_NAMES
        tasks.spawn(events, self._events_loop())
        tasks.spawn(identity, self._identity_loop())
        tasks.spawn(shots, self._shots_loop())
        tasks.spawn(profiles, self._profiles_loop())
        # Identity only, and only because the `machines` row has to exist before
        # a pull can store anything against it and the header pill has nothing
        # to say without it. The client is started before the engine, so if the
        # socket came up in between (which on a loopback fake it always has) the
        # `Connected` event is already gone; the poke coalesces with one
        # arriving a moment later, so asking here costs nothing.
        self.request_identity_sync("startup")
        log.info("sync_engine_started", host=self.client.host)

    async def stop(self) -> None:
        """Stop exactly this engine's loops and wait for them. Idempotent.

        For a connection that is being rebuilt while the app keeps running; at
        shutdown `TaskRegistry.cancel_all` does the same for every task at once.
        The caller has already refused to rebuild while a pull is under way (see
        :meth:`busy`), so the only pass this can cut short is an identity read.
        That leaves its ledger row `running`, and nothing else would ever close
        it, so it is closed here the way a boot closes one — the engine is the
        only writer of those rows, and it has just stopped.
        """
        tasks, self._tasks = self._tasks, None
        if tasks is None:
            return
        await tasks.cancel(LOOP_TASK_NAMES)
        closed = await self.runs.reconcile_running()
        log.info("sync_engine_stopped", host=self.client.host, runs_closed=closed)

    def busy(self) -> str | None:
        """What this engine is doing that a connection change would cut, or ``None``.

        A pull — a shot or profile pass running, waiting for the lock, or asked
        for and not yet picked up. Not an identity read: it writes nothing a
        person is waiting for, and the next connection reads identity again the
        moment it connects.
        """
        if (
            self._pulls_in_flight
            or self._shot_poke.event.is_set()
            or self._profile_poke.event.is_set()
        ):
            return "a pull"
        return None

    def request_shot_sync(self, reason: str = "manual") -> None:
        """Ask for an index diff on the next turn of the loop."""
        self._shot_poke.raise_(reason)

    def request_profile_sync(self, reason: str = "manual") -> None:
        """Ask for a profile mirror on the next turn of the loop."""
        self._profile_poke.raise_(reason)

    def request_identity_sync(self, reason: str = "manual") -> None:
        """Ask for a fresh read of what the machine is."""
        self._identity_poke.raise_(reason)

    # ── the loops ────────────────────────────────────────────────────

    async def _events_loop(self) -> None:
        """Turn device events into in-memory state and one poke. Nothing else, ever.

        This coroutine is the only consumer of the client's event stream, and
        that stream is **lossy under backpressure**: a subscriber that falls
        behind drops its oldest events (`infra/sse.py`). At 2 Hz telemetry a
        256-deep queue is about two minutes.

        It used to await `sync_identity()`, which takes the engine lock. During
        a long pull the lock is held for as long as the fetches take, the loop
        parked behind it, and the queue filled with status frames until the
        capability flags this engine gates pressure diagnostics on were thrown
        away unread. So: every handler here is synchronous, sets in-memory
        state, and at most raises a poke. The identity loop does the waiting.
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
                # Identity only. A reconnect is the moment the firmware version
                # or the board may have changed under us, and it is what the
                # header pill reads; shots and profiles wait to be asked for.
                self.request_identity_sync("connected")
            case Disconnected(reason=reason):
                log.info("sync_device_disconnected", reason=reason)
            case ShotSaved(shot_id=shot_id):
                # Remembered, not acted on. Nothing is fetched until somebody
                # asks for a pull; this only means the pull they ask for knows
                # about a shot the index may not list yet.
                self._pushed_ids.add(shot_id)
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

        It records; it does not write and it does not ask for a pass. The
        capability flags are what gate pressure diagnostics on ingest, so they
        have to be current in memory whether or not anybody ever presses pull;
        the identity pass folds them into the `machines` row the next time it
        runs. The selected profile id is kept for the same reason — the profile
        mirror marks which version the machine has selected — and a `puid` that
        moved is a mirror that is stale until the next pull, which is a thing
        the person pulling will get for free.
        """
        capabilities = (status.cp, status.cd, status.gp, status.led)
        if any(flag is not None for flag in capabilities) and capabilities != self._capabilities:
            self._capabilities = capabilities

        if status.puid is not None and status.puid != self._selected_profile_id:
            self._selected_profile_id = status.puid

    async def _identity_loop(self) -> None:
        while True:
            trigger = await self._identity_poke.wait()
            with contextlib.suppress(Exception):
                await self.sync_identity(trigger=trigger)

    async def _shots_loop(self) -> None:
        while True:
            trigger = await self._shot_poke.wait()
            # Every shot pass is a `backfill` now: the ledger's `live` kind
            # meant "this run was started by a push from the machine", and
            # nothing starts a run but a request. Archives filled before that
            # still hold `live` rows, which is why nothing reads the kind as an
            # enumeration of what can happen next.
            with contextlib.suppress(Exception):
                await self.sync_shots(kind="backfill", trigger=trigger)

    async def _profiles_loop(self) -> None:
        while True:
            trigger = await self._profile_poke.wait()
            with contextlib.suppress(Exception):
                await self.sync_profiles(trigger=trigger)

    # ── identity ─────────────────────────────────────────────────────

    async def _ensure_machine(self) -> MachineRow:
        """The one `machines` row, with its host set to the one we are talking to.

        There is always a row — the schema holds exactly one — so this is a
        write of the configured host onto it rather than a lookup that might
        miss. Pointing the container at a new address therefore moves the
        machine rather than forking the archive: every shot, profile and Set
        stays attached, which is the whole reason identity stopped being the
        host.
        """
        if self._machine is None:
            self._machine = await self.machines.update_identity(
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
            await self._ensure_machine()
            run_id = await self.runs.start_run("identity", trigger)
            update = SyncRunUpdate()
            settings: dict[str, Any] | None = None
            try:
                settings = await self.client.get_settings()
            except asyncio.CancelledError:
                update.errors += 1
                update.error = STOPPED_MESSAGE
                await self.runs.finish_run(run_id, update)
                raise
            except DeviceError as exc:
                # Not fatal: the versions and capabilities we already have are
                # worth storing without the PID string.
                update.errors += 1
                update.error = str(exc)
                log.info("sync_identity_settings_unavailable", error=str(exc))

            self._machine = await self.machines.update_identity(
                identity_to_upsert(
                    self.client.host,
                    identity=self.client.identity,
                    status=self.client.last_status,
                    settings=settings,
                )
            )
            await self.runs.finish_run(run_id, update)
            self._publish(SYNC_PROGRESS_EVENT, {"kind": "identity", "status": "finished"})
            return await self.runs.get_run(run_id)

    # ── shots ────────────────────────────────────────────────────────

    async def sync_shots(self, *, kind: str = "backfill", trigger: str = "manual") -> SyncRunRow:
        """One index diff: fetch what is missing, reconcile what changed, pull notes.

        Nothing hangs off the end of a pass: shots leave the machine only when a
        person confirms a cleanup on the Sync page, so a pull only ever reads.
        """
        # Counted before the lock is taken, and synchronously with the loop
        # waking: see `busy`.
        self._pulls_in_flight += 1
        try:
            async with self._lock:
                return await self._sync_shots(kind=kind, trigger=trigger)
        finally:
            self._pulls_in_flight -= 1

    async def _sync_shots(self, *, kind: str, trigger: str) -> SyncRunRow:
        await self._ensure_machine()
        run_id = await self.runs.start_run(kind, trigger)
        update = SyncRunUpdate()
        self._publish(
            SYNC_PROGRESS_EVENT,
            {"kind": kind, "status": "started", "run_id": run_id, "trigger": trigger},
        )

        entries: dict[str, IndexEntry] | None = None
        # try/finally around the whole pass, not just the fetches: a run left
        # at status "running" is a run nothing ever closes, and
        # `GET /api/sync/status` would report a sync in progress for ever.
        try:
            entries = await self._read_index()
            listed = entries or {}
            known = await self.shots.known_states()
            update.shots_seen = len(listed)

            missing = self._missing_entries(listed, known)
            await self._fetch_and_store(missing, run_id=run_id, update=update)
            await self._reconcile(
                listed, known, run_id=run_id, update=update, full_index=entries is not None
            )
        except asyncio.CancelledError:
            update.errors += 1
            update.error = STOPPED_MESSAGE
            log.info("sync_run_stopped", kind=kind, run_id=run_id)
            raise
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
            await self._sync_notes(entries, trigger=trigger)
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

        An announced id the index has not listed yet is appended: `evt:history-
        shot-saved` and the index write are not atomic, so somebody pressing
        pull the moment the machine beeps would otherwise be told there is
        nothing new and be right only about the index.
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
            # both waiting behind it. Three in a row is the machine; give up,
            # record the failure, and leave the rest for the next pull. Nothing
            # retries on its own, which is why the run's error has to be
            # legible: it is what the button's toast repeats.
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
                await self._store(fetched, run_id=run_id, update=update)
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

    async def _store(self, fetched: _Fetched, *, run_id: int, update: SyncRunUpdate) -> None:
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
                fetched, reason=f"validation failed: {exc}", run_id=run_id, update=update
            )
            return
        shot, samples = derived.shot, derived.samples
        if derived.diagnostics_error is not None:
            update.errors += 1

        shot.profile_version_id = await self._version_for(shot.profile_id_on_device)
        shot_id = await self.shots.insert(shot, samples)
        # The user's half of the shot: which Set was this, and what did they
        # think of it. Both are best-effort and neither can fail an ingest —
        # the archive's job is to hold the bytes, and a Set that does not match
        # leaves the shot in the `needs_set` inbox rather than losing it.
        match = await self.sets.profile_match(
            shot_id,
            profile_version_id=shot.profile_version_id,
            device_profile_id=shot.profile_id_on_device,
        )
        set_version_id = match.set_version_id
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

    async def _version_for(self, profile_id: str) -> int | None:
        if not profile_id:
            return None
        version = await self.profiles.find_version_for_device_profile(profile_id)
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
        self, entries: dict[str, IndexEntry], *, trigger: str
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

        held = await self.notes.stale_shot_ids()
        shots = await self.shots.known_states()
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
        except asyncio.CancelledError:
            update.errors += 1
            update.error = STOPPED_MESSAGE
            raise
        finally:
            await self.runs.finish_run(run_id, update)
        return await self.runs.get_run(run_id)

    # ── profiles ─────────────────────────────────────────────────────

    async def sync_profiles(self, *, trigger: str = "manual") -> SyncRunRow:
        """Mirror `/p/` as content-hashed versions plus a device-id map."""
        self._pulls_in_flight += 1
        try:
            async with self._lock:
                return await self._sync_profiles(trigger=trigger)
        finally:
            self._pulls_in_flight -= 1

    async def _sync_profiles(self, *, trigger: str) -> SyncRunRow:
        await self._ensure_machine()
        run_id = await self.runs.start_run("profiles", trigger)
        update = SyncRunUpdate()
        try:
            return await self._mirror_profiles(run_id, update)
        except asyncio.CancelledError:
            update.errors += 1
            update.error = STOPPED_MESSAGE
            await self.runs.finish_run(run_id, update)
            raise

    async def _mirror_profiles(self, run_id: int, update: SyncRunUpdate) -> SyncRunRow:
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
            existing = await self.profiles.get_device_profile(profile.id)
            await self.profiles.upsert_device_profile(
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
            await self.shots.link_unlinked_by_device_profile(profile.id, version.id)
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

        removed = await self.profiles.mark_missing_deleted(seen)
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

        A device that is off, mid-OTA or busy is not a fault to shout about,
        and it is not this engine's to retry: the next pull is somebody
        pressing the button again. It is recorded, because the failure is what
        that person is shown, and because "the last six runs all failed" is the
        thing an operator needs to be able to see.
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
