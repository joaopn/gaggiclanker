"""`DeviceConnection` — the one owner of the machine connection, rebuilt live.

The client and the sync engine used to be built once, in the lifespan, and
captured by every service that needed them. Changing the machine's address in
Settings therefore did nothing until a restart, and nothing said so. This object
owns both instead, and everything else asks it for the current ones at the
moment it acts — the routes through their dependencies, the cleanup, notes and
profile-draft services through :attr:`DeviceConnection.client`.

Three rules shape it:

**One lock, and a rebuild holds it.** :meth:`DeviceConnection.update` takes the
lock, decides whether the change may be made, stores the new settings, and
rebuilds the client and the engine from the effective values if they moved —
all in one hold. :meth:`DeviceConnection.operation` takes the same lock to
register a machine-bound operation, and :meth:`DeviceConnection.current_engine`
to hand out the engine a pull is asked of. So neither an operation nor a pull
starts between the busy check and the rebuild, and a rebuild never starts while
one is registered.

**Never cut a write in half.** A change that would move the connection is
refused while anything is using the machine — a profile push or rollback, a
cleanup run, a notes send, a pull — with the reason, and before anything is
stored. A change that leaves the effective values where they were does nothing
to the connection at all, busy or not.

**It knows nothing above the device layer.** Reading the settings, building a
client with the write gate attached and building the sync engine are handed in
as callables by the lifespan, so this module imports neither the database nor
the sync package — the direction `docs/architecture.md` draws.

**Every background task that talks to the machine lives here.** This object
keeps a :class:`~gaggiclanker.infra.tasks.TaskRegistry` of its own — the sync
engine's loops, a cleanup run, a notes send — rather than sharing the app's.
The app's registry is handed to things a language model drives (a chat run, an
analysis, a starting point queue work on it), and a registry is not an opaque
handle: every task in it answers ``get_coro()``, and a coroutine's frame holds
the ``self`` it was called on. One shared registry therefore means a chat tool
holding the sync engine, its client and its ``save_profile`` two public calls
away, and ``cancel()`` on the four loop names one call away with no
introspection at all. Two registries is what makes "a tool cannot reach the
machine" a property of the object graph rather than of nobody having tried.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.infra.errors import Conflict
from gaggiclanker.infra.tasks import TaskRegistry

__all__ = [
    "DEVICE_SETTING_KEYS",
    "ConnectionBusy",
    "DeviceConfig",
    "DeviceConnection",
    "DeviceEngine",
    "device_config",
    "machine_operation",
]

log = structlog.get_logger(__name__)

#: The settings a connection is built from. A PATCH touching none of them never
#: reaches :meth:`DeviceConnection.update`.
DEVICE_SETTING_KEYS: tuple[str, ...] = (
    "gaggimateHost",
    "gaggimateProtocol",
    "gaggimateTimeoutSeconds",
    "deviceSyncEnabled",
)


@dataclass(frozen=True, slots=True)
class DeviceConfig:
    """The effective values of :data:`DEVICE_SETTING_KEYS`, compared as a whole."""

    host: str
    protocol: str
    timeout: float
    sync_enabled: bool

    @property
    def wanted(self) -> bool:
        """Whether this configuration holds a connection at all.

        An empty host is an archive with no machine; sync switched off is a
        machine somebody does not want one of its three WebSocket slots taken
        from. Either way there is no client and no engine.
        """
        return bool(self.host) and self.sync_enabled


def device_config(values: Mapping[str, Any]) -> DeviceConfig:
    """Build a :class:`DeviceConfig` from resolved setting values, keyed by registry key."""
    return DeviceConfig(
        host=str(values.get("gaggimateHost") or "").strip(),
        protocol=str(values.get("gaggimateProtocol") or "ws"),
        timeout=float(values.get("gaggimateTimeoutSeconds") or 0.0),
        sync_enabled=bool(values.get("deviceSyncEnabled")),
    )


class DeviceEngine(Protocol):
    """What the connection needs of the sync engine: start, stop, and what it is doing."""

    async def start(self, tasks: TaskRegistry) -> None: ...

    async def stop(self) -> None: ...

    def busy(self) -> str | None: ...


class ConnectionBusy(Conflict):
    """A connection change refused because something is using the machine."""

    def __init__(self, running: str) -> None:
        super().__init__(
            f"The machine settings were not changed: {running} is using the machine. "
            f"Wait for {running} to finish, then save again.",
            details={"running": running},
        )


class DeviceConnection[EngineT: DeviceEngine]:
    """Owns the device client and the sync engine, and rebuilds both when the settings move."""

    def __init__(
        self,
        *,
        read_config: Callable[[], Awaitable[DeviceConfig]],
        build_client: Callable[[DeviceConfig], GaggimateClient],
        build_engine: Callable[[GaggimateClient], EngineT],
        busy_tasks: Mapping[str, str] | None = None,
    ) -> None:
        self._read_config = read_config
        self._build_client = build_client
        self._build_engine = build_engine
        #: Built here, not handed in: see the module docstring. Everything in it
        #: holds the client, so nothing outside the device layer may hold it.
        self._tasks = TaskRegistry()
        #: Registry task names that use the machine, with the phrase a refusal
        #: says ("a cleanup run"). The cleanup run and the notes send run as
        #: background tasks and outlive the request that started them.
        self._busy_tasks: dict[str, str] = dict(busy_tasks or {})
        self._lock = asyncio.Lock()
        #: Operations registered through :meth:`operation`, by phrase.
        self._operations: Counter[str] = Counter()
        self._config: DeviceConfig | None = None
        self._client: GaggimateClient | None = None
        self._engine: EngineT | None = None

    # ── what there is now ────────────────────────────────────────────

    @property
    def client(self) -> GaggimateClient | None:
        """The current client, or ``None`` when no machine is configured."""
        return self._client

    @property
    def engine(self) -> EngineT | None:
        """The current sync engine, or ``None`` when there is no client."""
        return self._engine

    @property
    def config(self) -> DeviceConfig | None:
        """The configuration the current connection was built from."""
        return self._config

    @property
    def tasks(self) -> TaskRegistry:
        """Where a background task that talks to the machine belongs.

        The sync engine's loops are spawned here by :meth:`_build`; a cleanup
        run and a notes send are spawned here by the two routes that start them
        — both hold this connection through their service, so both belong to
        the machine's owner rather than to the app's shared registry. The busy
        check reads their names from here, so a rebuild is still refused while
        either is in flight, and :meth:`stop` cancels them before anything else
        goes.
        """
        return self._tasks

    def busy(self) -> str | None:
        """What is using the machine right now, as a phrase, or ``None``."""
        for phrase, count in self._operations.items():
            if count > 0:
                return phrase
        for name, phrase in self._busy_tasks.items():
            if self._tasks.get(name) is not None:
                return phrase
        if self._engine is not None:
            return self._engine.busy()
        return None

    # ── lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Build the connection from the settings as they are now. Never blocks on the machine."""
        async with self._lock:
            await self._build(await self._read_config())

    async def stop(self) -> None:
        """Stop this connection's background work, then the engine and the client.

        Idempotent; the shutdown path. The registry goes first because a
        cleanup run and a notes send are machine writes that also write to the
        archive, and the lifespan closes the database straight after this: they
        have to be over before the client they write through disappears, and
        long before the file is released. The engine's loops are in the same
        registry and are cancelled here too, which leaves
        :meth:`DeviceEngine.stop` nothing to cancel and its ledger backstop
        still to run.
        """
        async with self._lock:
            await self._tasks.cancel_all()
            await self._teardown()
            self._config = None

    async def current_engine(self) -> EngineT | None:
        """The engine as it is once no change is in progress, for a caller about to use it.

        Waits for a rebuild in progress, so a pull asked for while the settings
        are being changed goes to the connection the change produces — or finds
        none — instead of starting on the old one and being cut off by the
        rebuild. The caller must use the engine without awaiting anything first
        (a poke is synchronous): the lock is released on return, and only the
        absence of a suspension point keeps the next change out.
        """
        async with self._lock:
            return self._engine

    async def update[T](self, write: Callable[[], Awaitable[T]], *, proposed: DeviceConfig) -> T:
        """Store a settings change and apply it to the connection, as one step under the lock.

        ``proposed`` is what the effective values will be once ``write`` has
        run. If it differs from the live configuration and something is using
        the machine, :class:`ConnectionBusy` is raised and ``write`` never runs.
        Otherwise ``write`` runs, the settings are read back, and the
        connection is rebuilt if they moved.
        """
        async with self._lock:
            if proposed != self._config and (running := self.busy()) is not None:
                log.info("device_reconfigure_refused", running=running)
                raise ConnectionBusy(running)
            result = await write()
            await self._rebuild_if_changed()
            return result

    @asynccontextmanager
    async def operation(self, phrase: str) -> AsyncIterator[GaggimateClient | None]:
        """Use the machine for the length of the block, holding off any rebuild.

        Registration waits for a rebuild in progress to finish, so the client
        handed out is the one the rebuild produced; for as long as the block
        runs, a change that would move the connection is refused naming
        ``phrase``.
        """
        async with self._lock:
            self._operations[phrase] += 1
            client = self._client
        try:
            yield client
        finally:
            self._operations[phrase] -= 1
            if self._operations[phrase] <= 0:
                del self._operations[phrase]

    # ── internals, all under the lock ────────────────────────────────

    async def _rebuild_if_changed(self) -> bool:
        config = await self._read_config()
        if config == self._config:
            return False
        previous = self._config
        await self._teardown()
        await self._build(config)
        log.info(
            "device_reconfigured",
            host=config.host or None,
            previous_host=(previous.host or None) if previous is not None else None,
            connected=self._client is not None,
        )
        return True

    async def _build(self, config: DeviceConfig) -> None:
        """Build and start a client and an engine for ``config``, or leave nothing at all.

        All or nothing. A build that fails part-way stops what it started and
        records no configuration, so the connection is plainly absent rather
        than half there — a client with no engine, marked as built — and the
        next change, even to the same values, tries again instead of being
        taken for a no-op.
        """
        if not config.host:
            log.info("device_not_configured")
            self._config = config
            return
        if not config.sync_enabled:
            log.info("device_sync_disabled", host=config.host)
            self._config = config
            return
        client: GaggimateClient | None = None
        engine: EngineT | None = None
        try:
            client = self._build_client(config)
            await client.start()
            log.info("device_client_started", host=config.host)
            engine = self._build_engine(client)
            await engine.start(self._tasks)
        except BaseException:
            log.error("device_connection_build_failed", host=config.host, exc_info=True)
            self._config = None
            self._engine, self._client = engine, client
            await self._teardown()
            raise
        self._client = client
        self._engine = engine
        self._config = config

    async def _teardown(self) -> None:
        engine, self._engine = self._engine, None
        client, self._client = self._client, None
        # The engine first: its event loop is subscribed to the client's bus,
        # and its passes talk through the client. Each step is attempted even
        # if the one before it failed, so a broken engine cannot leave a live
        # socket and an open HTTP session behind.
        if engine is not None:
            try:
                await engine.stop()
            except Exception:
                log.warning("sync_engine_stop_failed", exc_info=True)
        if client is not None:
            try:
                await client.stop()
            except Exception:
                log.warning("device_client_stop_failed", exc_info=True)
            else:
                log.info("device_client_stopped", host=client.host)


@asynccontextmanager
async def machine_operation(
    connection: DeviceConnection[Any] | None, phrase: str
) -> AsyncIterator[GaggimateClient | None]:
    """:meth:`DeviceConnection.operation`, or ``None`` when there is no connection at all."""
    if connection is None:
        yield None
        return
    async with connection.operation(phrase) as client:
        yield client
