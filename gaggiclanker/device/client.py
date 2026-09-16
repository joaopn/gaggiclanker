"""`GaggimateClient` — one persistent socket and a bounded HTTP fetcher.

The firmware allows **three** WebSocket clients in total and the machine's own
browser UI is one of them, so this class holds exactly one connection for the
whole process and multiplexes every request over it. A second connection is not
a fallback; index polling is what the sync engine falls back to.

The two-list rule
-----------------

The public surface is two closed lists and nothing else: the ten reads in
:data:`READ_ONLY_METHODS`, and the seven writes in
:data:`GATED_WRITE_METHODS` — five profile operations, plus the shot delete and
the notes save added later. :meth:`_send`
stays private and ``tests/device/test_public_surface.py`` fails the build if an
eleventh read or an eighth write appears, or if a request type outside those
seven turns up anywhere in this module — including in a docstring.

The lists are separate because the two halves have different rules. A read
needs a socket. **A write additionally needs a gate** (:mod:`.writes`): it is
authorised against the `deviceWritesEnabled` setting and the `device_writes`
audit before a byte goes out, and the attempt is recorded either way. A client
built without a gate gets :class:`~gaggiclanker.device.writes.DenyAllWrites` and
can write nothing at all, so read-only is what you get by forgetting.

What is *not* here, and will not be: `POST /api/settings` (it clears every
boolean key the body omits, and it can change WiFi and PID),
`req:history:rebuild` (it regenerates the index for every shot at once), and
`req:profiles:reorder` (it rewrites the display's whole ordering for a cosmetic
gain). A bad `req:profiles:save` can still leave the machine with a profile that
will not brew, which is why four validation layers sit in front of the five
profile methods and none of them is in
this file. The shot delete is unrecoverable — the machine is the only copy until
we have synced it — so the gate refuses it for anything the archive does not
already hold intact; that rule is in `gaggiclanker/cleanup/eligibility.py` and
is likewise not in this file.

Failure modes, and which one you get
------------------------------------

===========================  ==============================================
:class:`DeviceUnavailable`   the socket is down; the request was not sent
:class:`DeviceTimeout`       sent, nothing came back within ``timeout``
:class:`DeviceBusy`          503 / "Update in progress" — come back later
:class:`DeviceProtocolError` the device served its SPA instead of the file
:class:`DeviceError`         the device understood and refused
===========================  ==============================================

Everything retryable carries ``retryable = True`` so the sync loop branches on
one attribute rather than on a message.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, replace
from types import TracebackType
from typing import Any, Literal, Self
from uuid import uuid4

import aiohttp
import structlog
from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from gaggiclanker.device.errors import (
    DeviceBusy,
    DeviceError,
    DeviceProtocolError,
    DeviceTimeout,
    DeviceUnavailable,
    classify_device_error,
)
from gaggiclanker.device.events import (
    Connected,
    DeviceEvent,
    DeviceEventBus,
    Disconnected,
    HistoryRebuildProgress,
    IdentityChanged,
    OtaProgress,
    ShotFinishedStats,
    ShotSaved,
    StatusChanged,
    merge_status,
)
from gaggiclanker.device.writes import (
    DenyAllWrites,
    DeviceWriteGate,
    DeviceWriteRefused,
    PendingWrite,
    payload_hash,
)
from gaggiclanker.domain.address import machine_host
from gaggiclanker.domain.ids import pad6, unpad
from gaggiclanker.domain.index import parse_index
from gaggiclanker.domain.models import (
    APP_PROFILE_SUFFIX,
    LiveStatus,
    OtaSettings,
    Profile,
    ShotIndex,
    ShotNotes,
    canonical_profile_json,
)
from gaggiclanker.domain.slog import Slog, SlogError, header_size_for, is_html_response, parse_slog
from gaggiclanker.infra.sse import EventBus

__all__ = ["GATED_WRITE_METHODS", "READ_ONLY_METHODS", "GaggimateClient", "SlogFetch"]

log = structlog.get_logger(__name__)

#: Half of the client's public API: everything that only *asks*. Pinned by a
#: test rather than by review, so an eleventh read cannot appear without
#: somebody also editing the list that says what this client is allowed to do.
READ_ONLY_METHODS: frozenset[str] = frozenset(
    {
        "list_profiles",
        "load_profile",
        "get_shot_notes",
        "get_ota_settings",
        "fetch_index",
        "fetch_recent",
        "fetch_slog",
        "fetch_notes_json",
        "get_status",
        "get_settings",
    }
)

#: The other half: the seven writes this client can make, every one of them
#: behind :class:`DeviceWriteGate`. Five are profile operations; two
#: are history operations — the storage cleanup's (`delete_shot`) and the notes
#: write-back's (`save_shot_notes`) — each of which widened this list
#: deliberately and with
#: its own eligibility rule in the gate. The list is closed and the test pins
#: it; an eighth entry is a design decision, not a refactor.
GATED_WRITE_METHODS: frozenset[str] = frozenset(
    {
        "save_profile",
        "delete_profile",
        "select_profile",
        "favorite_profile",
        "unfavorite_profile",
        "delete_shot",
        "save_shot_notes",
    }
)

# Reconnect curve. 1 s is fast enough that a router blip is invisible; 60 s is
# slow enough that a machine that is off for the night costs a request a minute
# rather than one a second.
BACKOFF_INITIAL_S = 1.0
BACKOFF_MAX_S = 60.0

#: How long a connection has to survive before the backoff resets to 1 s.
#:
#: A successful handshake is *not* proof the machine wants us. The firmware
#: allows three WebSocket clients and `WebSocketHandler::loop` calls
#: `ws.cleanupClients()` once a second; ESPAsyncWebServer closes the **oldest**
#: client when the count exceeds the limit. So a fourth client is accepted,
#: gets its state frame, and then somebody — possibly us, a second later — is
#: evicted. Resetting the curve on connect turns that into a reconnect every
#: ~0.7 s for as long as the machine's own browser UI stays open, with each
#: cycle evicting somebody else. Thirty seconds is well past the one-second
#: cleanup tick, so a connection that lasts that long really did win a slot.
STABLE_CONNECTION_S = 30.0

#: How long to wait for the machine to answer our close frame before dropping
#: the TCP connection. `websockets` defaults to 10 s and the firmware does not
#: answer promptly, so every shutdown took ten seconds — the container's stop
#: included, where it is the difference between a restart and a `docker kill`.
#:
#: Five rather than something smaller: the display's web server is pumped
#: cooperatively from its main loop, and at two seconds the simulator stopped
#: accepting connections altogether after a few aborted closes. Five is the
#: smallest value it survives, and still half the default.
CLOSE_TIMEOUT_S = 5.0

#: Reconnect-loop detection: this many connections inside this window means we
#: are being evicted rather than dropped, and that is worth saying out loud —
#: it is a fight over the third slot, not a network fault, and the fix is to
#: close a browser tab rather than to look at the Wi-Fi.
RECONNECT_LOOP_THRESHOLD = 4
RECONNECT_LOOP_WINDOW_S = 60.0

#: Two concurrent HTTP requests, never more. The display serves everything —
#: the browser UI included — from roughly 300 KB of heap, and its own UI aborts
#: in-flight fetches for exactly this reason (firmware report §10.6). Three
#: parallel `.slog` reads is how you get HTML back instead of binary.
HTTP_CONCURRENCY = 2

#: How long to keep retrying a `.slog` that is still being written before
#: returning what we have. The file is closed within ~3 s of the brew ending
#: (report §2.6); 10 s is that with room to spare.
SLOG_RETRY_BUDGET_S = 10.0
SLOG_RETRY_INITIAL_S = 0.25
SLOG_RETRY_MAX_S = 2.0

#: `res:ota-settings` is **broadcast**, not addressed: `WebUIPlugin.cpp:613`
#: builds one frame and sends it to every client, with no `rid` echoed. So it
#: cannot be correlated the way every other response can, and
#: :meth:`get_ota_settings` waits on "the next one of these" instead.
OTA_SETTINGS_TYPE = "res:ota-settings"


@dataclass(slots=True)
class SlogFetch:
    """The result of fetching one shot file: always the bytes, maybe a parse.

    The raw bytes come first and are never discarded, because the machine
    deletes its copy under storage pressure and a parser bug we fix next month
    is worth nothing if we threw the evidence away. ``slog`` is ``None`` when
    the bytes did not parse; ``parse_error`` then says why, and the caller
    stores the shot quarantined rather than dropping it.
    """

    shot_id: int
    raw: bytes
    slog: Slog | None = None
    parse_error: str | None = None
    #: The device was still writing this file when we gave up waiting. The
    #: samples present are real; there are simply fewer than there will be.
    incomplete: bool = False


class GaggimateClient:
    """One machine, one socket, one bounded HTTP session.

    ``start()`` spawns the connection supervisor and returns immediately: the
    app must boot whether or not the machine is powered on. Everything that
    needs the socket raises :class:`DeviceUnavailable` until it is up.
    """

    def __init__(
        self,
        host: str,
        *,
        protocol: str = "ws",
        timeout: float = 15.0,
        events: DeviceEventBus | None = None,
        session: aiohttp.ClientSession | None = None,
        backoff_initial: float = BACKOFF_INITIAL_S,
        backoff_max: float = BACKOFF_MAX_S,
        stable_after: float = STABLE_CONNECTION_S,
        slog_retry_budget: float = SLOG_RETRY_BUDGET_S,
        write_gate: DeviceWriteGate | None = None,
    ) -> None:
        # Reduced here as well as in the settings path: every URL is built from
        # `self.host`, and a scheme left on it resolves a host named `http`.
        host = machine_host(host)
        if not host:
            raise ValueError("GaggimateClient needs a host; an empty host means 'no machine'")
        self.host = host
        self.protocol = protocol
        self.timeout = timeout
        self.events: DeviceEventBus = events if events is not None else EventBus[DeviceEvent]()
        # Deny-all unless somebody deliberately handed us a gate. A client that
        # nobody configured is a client that cannot change a machine.
        self._gate: DeviceWriteGate = write_gate if write_gate is not None else DenyAllWrites()

        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._stable_after = stable_after
        self._slog_retry_budget = slog_retry_budget
        #: When each recent connection came up, for the eviction-loop warning.
        self._recent_connects: deque[float] = deque(maxlen=RECONNECT_LOOP_THRESHOLD)

        self._owns_session = session is None
        self._session = session
        self._http_slots = asyncio.Semaphore(HTTP_CONCURRENCY)

        self._ws: ClientConnection | None = None
        self._task: asyncio.Task[None] | None = None
        # An Event rather than a bool so the backoff sleep can be interrupted:
        # a shutdown that arrives one second into a 60 s wait should not hold
        # the lifespan open for the other 59.
        self._stop = asyncio.Event()
        self._connected_at: float | None = None

        # rid -> the future waiting for that response. One dict for the whole
        # process; every request is multiplexed over the single socket.
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # Waiters for the next (unaddressed) `res:ota-settings` broadcast.
        self._ota_waiters: list[asyncio.Future[OtaSettings]] = []
        self._connected_event = asyncio.Event()

        self.last_status: LiveStatus | None = None
        self.identity: OtaSettings | None = None

    # ── lifecycle ────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        """Whether a socket is up *right now*. Racy by nature; use it for display."""
        return self._ws is not None

    @property
    def ws_url(self) -> str:
        return f"{self.protocol}://{self.host}/ws"

    @property
    def http_base(self) -> str:
        # `wss` implies a proxy in front of the machine, which terminates TLS
        # for the HTTP side too. The firmware itself is plain HTTP either way.
        scheme = "https" if self.protocol == "wss" else "http"
        return f"{scheme}://{self.host}"

    async def start(self) -> None:
        """Open the HTTP session and start the connection supervisor.

        Returns as soon as the task exists — not when the machine answers. A
        box that boots before the espresso machine must still serve its archive.
        """
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        self._task = asyncio.create_task(self._supervise(), name="device-ws")

    async def stop(self) -> None:
        """Tear the connection down and fail everything still waiting on it."""
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._close_socket("client stopped")
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    async def wait_connected(self, timeout: float = 10.0) -> bool:  # noqa: ASYNC109
        """Block until the socket is up, or ``timeout`` passes.

        The timeout is a parameter rather than the caller's own
        ``asyncio.timeout`` block (ASYNC109) because "did it connect within N
        seconds" is the question, and a caller that has to write the try/except
        itself gets it wrong.
        """
        try:
            async with asyncio.timeout(timeout):
                await self._connected_event.wait()
        except TimeoutError:
            return False
        return True

    def subscribe(self) -> AsyncGenerator[DeviceEvent]:
        """An endless async iterator of device events for one consumer.

        Lossy under backpressure — see :mod:`gaggiclanker.device.events`. Ends
        when the consuming task is cancelled.
        """
        return self.events.stream()

    # ── the connection supervisor ────────────────────────────────────

    async def _supervise(self) -> None:
        """Keep the socket up for as long as the client is running.

        Exponential backoff with jitter, and — the part that matters against
        this firmware — **the curve only resets once a connection has proved
        itself**. See :data:`STABLE_CONNECTION_S`: a handshake succeeding means
        nothing on a machine that accepts a fourth client and then evicts
        somebody a second later, so "connected" is not the same fact as
        "connected and still here".

        The jitter is not decoration either: several gaggiclanker instances (or
        a container restart loop) would otherwise retry in lockstep for the one
        free slot, and whichever one lost the race would lose it every time.
        """
        delay = self._backoff_initial
        while not self._stop.is_set():
            reason = "closed"
            started: float | None = None
            try:
                async with connect(
                    self.ws_url,
                    open_timeout=self.timeout,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=CLOSE_TIMEOUT_S,
                    # The firmware reassembles text frames up to 64 KB; a full
                    # profile list is the biggest thing that arrives.
                    max_size=2**20,
                ) as socket:
                    self._ws = socket
                    self._connected_event.set()
                    started = time.monotonic()
                    self._connected_at = started
                    self._note_connection(started)
                    log.info("device_connected", host=self.host)
                    self.events.publish(Connected(host=self.host))
                    # Identity first: it is how we learn the board type, and
                    # therefore whether pressure telemetry means anything.
                    await self._request_identity()
                    async for raw in socket:
                        self._handle_frame(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # any failure at all means "reconnect"
                reason = f"{type(exc).__name__}: {exc}"
                log.warning("device_connection_failed", host=self.host, error=reason)
            finally:
                uptime = 0.0 if started is None else time.monotonic() - started
                await self._close_socket(reason)

            if self._stop.is_set():
                break
            if uptime >= self._stable_after:
                # It held a slot long enough to be real. Anything after this is
                # a new problem and deserves a fresh curve.
                delay = self._backoff_initial
            # Full jitter over [delay/2, delay]: enough spread to break a
            # lockstep, not so much that a blip costs a whole cycle.
            wait = random.uniform(delay / 2, delay)  # noqa: S311 - backoff spread, not a secret
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), wait)
            delay = min(delay * 2, self._backoff_max)

    def _note_connection(self, now: float) -> None:
        """Record a connect, and say so when they are coming too fast.

        Connecting four times a minute is not a flaky network — it is the
        three-client limit: we take the slot, the device evicts the oldest
        client a second later, and around it goes. The warning names the cause
        because the fix is to close a browser tab pointed at the machine, and
        nothing in a connection log would ever suggest that.
        """
        self._recent_connects.append(now)
        if len(self._recent_connects) < RECONNECT_LOOP_THRESHOLD:
            return
        span = now - self._recent_connects[0]
        if span <= RECONNECT_LOOP_WINDOW_S:
            log.warning(
                "device_reconnect_loop",
                host=self.host,
                connects=len(self._recent_connects),
                seconds=round(span, 1),
                hint=(
                    "the machine allows 3 WebSocket clients and evicts the oldest when a "
                    "fourth connects; close a browser tab pointed at the machine"
                ),
            )

    async def _close_socket(self, reason: str) -> None:
        was_connected = self._ws is not None
        socket, self._ws = self._ws, None
        self._connected_event.clear()
        if socket is not None:
            with contextlib.suppress(Exception):
                await socket.close()
        # Nothing will ever answer these now. Failing them is the whole point
        # of not queueing: a caller learns immediately rather than after its
        # own timeout, and learns *why*.
        self._fail_pending(DeviceUnavailable(f"The machine disconnected ({reason})"))
        if was_connected:
            log.info("device_disconnected", host=self.host, reason=reason)
            self.events.publish(Disconnected(reason=reason))

    def _fail_pending(self, error: BaseException) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        for waiter in list(self._ota_waiters):
            if not waiter.done():
                waiter.set_exception(error)
        self._ota_waiters.clear()

    async def _request_identity(self) -> None:
        """Ask for `res:ota-settings` without waiting for it.

        Fire and forget on purpose: the reply is a broadcast that the frame
        handler picks up and republishes as :class:`IdentityChanged`, so
        blocking the reader on it would deadlock the very loop that receives it.
        """
        socket = self._ws
        if socket is None:
            return
        with contextlib.suppress(Exception):
            await socket.send(json.dumps({"tp": "req:ota-settings"}))

    # ── inbound frames ───────────────────────────────────────────────

    def _handle_frame(self, raw: str | bytes) -> None:
        """Dispatch one inbound frame. Never raises: a bad frame is not fatal."""
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            log.warning("device_frame_unparseable", host=self.host)
            return
        if not isinstance(message, dict):
            log.warning("device_frame_not_an_object", host=self.host)
            return

        tp = message.get("tp")
        if not isinstance(tp, str):
            log.warning("device_frame_without_type", host=self.host)
            return

        if tp == "evt:status":
            self._on_status(message)
        elif tp == "evt:history-shot-saved":
            self._on_shot_saved(message)
        elif tp == "evt:shot-finished-stats":
            self.events.publish(
                ShotFinishedStats(
                    max_pressure=_as_float(message.get("maxPressure")),
                    avg_flow=_as_float(message.get("avgFlow")),
                )
            )
        elif tp == "evt:ota-progress":
            self.events.publish(
                OtaProgress(
                    phase=_as_int(message.get("phase")) or 0,
                    progress=_as_int(message.get("progress")) or 0,
                )
            )
        elif tp == "evt:history-rebuild-progress":
            self.events.publish(
                HistoryRebuildProgress(
                    total=_as_int(message.get("total")) or 0,
                    current=_as_int(message.get("current")) or 0,
                    status=str(message.get("status", "")),
                )
            )
        elif tp == OTA_SETTINGS_TYPE:
            self._on_ota_settings(message)
        elif tp.startswith("res:"):
            self._resolve(message)
        else:
            log.debug("device_frame_ignored", host=self.host, tp=tp)

    def _on_status(self, message: dict[str, Any]) -> None:
        try:
            merged = merge_status(self.last_status, message)
        except ValidationError as exc:
            # LiveStatus is open, so this only fires on a *type* change (a key
            # that used to be a float arriving as an object). Keep the last good
            # status rather than going dark.
            log.warning("device_status_invalid", host=self.host, error=str(exc))
            return
        self.last_status = merged
        self.events.publish(StatusChanged(status=merged))

    def _on_shot_saved(self, message: dict[str, Any]) -> None:
        raw_id = message.get("id")
        try:
            shot_id = unpad(raw_id if isinstance(raw_id, int | str) else "")
        except ValueError:
            log.warning("device_shot_saved_bad_id", host=self.host, id=raw_id)
            return
        log.info("device_shot_saved", host=self.host, shot_id=shot_id)
        self.events.publish(ShotSaved(shot_id=shot_id))

    def _on_ota_settings(self, message: dict[str, Any]) -> None:
        """Handle the identity broadcast, solicited or not.

        The firmware sends this to every client after each half-hourly update
        check, so a frame with no waiter is normal rather than an error — and
        it is still the freshest identity we have.
        """
        payload = {k: v for k, v in message.items() if k != "tp"}
        try:
            identity = OtaSettings.model_validate(payload)
        except ValidationError as exc:
            log.warning("device_identity_invalid", host=self.host, error=str(exc))
            return
        self.identity = identity
        for waiter in list(self._ota_waiters):
            if not waiter.done():
                waiter.set_result(identity)
        self._ota_waiters.clear()
        self.events.publish(IdentityChanged(identity=identity))

    def _resolve(self, message: dict[str, Any]) -> None:
        rid = message.get("rid")
        if not isinstance(rid, str):
            log.debug("device_response_without_rid", host=self.host, tp=message.get("tp"))
            return
        future = self._pending.pop(rid, None)
        if future is None:
            # Late: the request already timed out, or another client's rid
            # collided with ours. Either way there is nobody to hand it to.
            log.debug("device_response_unmatched", host=self.host, rid=rid)
            return
        if not future.done():
            future.set_result(message)

    # ── the one private sender ───────────────────────────────────────

    async def _send(self, tp: str, **payload: Any) -> dict[str, Any]:
        """Send one `req:*` frame and wait for the `res:*` with the same `rid`.

        Private, and it stays private: see the module docstring. Correlation is
        by `rid` alone — the firmware echoes whatever string we send and the
        responses can arrive in any order, interleaved with 2 Hz telemetry.
        """
        socket = self._ws
        if socket is None:
            raise DeviceUnavailable(
                f"Not connected to the machine at {self.host}; request {tp} was not sent"
            )

        rid = uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        try:
            await socket.send(json.dumps({"tp": tp, "rid": rid, **payload}))
            async with asyncio.timeout(self.timeout):
                message = await future
        except TimeoutError:
            raise DeviceTimeout(
                f"The machine did not answer {tp} within {self.timeout:g}s"
            ) from None
        except WebSocketException as exc:
            raise DeviceUnavailable(f"The machine disconnected while sending {tp}: {exc}") from exc
        finally:
            self._pending.pop(rid, None)

        error = message.get("error")
        if error:
            raise classify_device_error(str(error), details={"request": tp})
        return message

    # ── read-only WebSocket surface ──────────────────────────────────

    async def list_profiles(self, minimal: bool = False) -> list[Profile]:
        """Every profile on the machine. ``minimal`` asks for just id + label.

        A profile that fails validation is logged and skipped rather than
        failing the list: the firmware tolerates keys we do not know about, and
        losing the other twenty profiles over one odd file helps nobody. The
        warning names the id, which is what makes the next model fix possible.
        """
        message = await self._send("req:profiles:list", minimal=minimal)
        raw = message.get("profiles")
        if not isinstance(raw, list):
            raise DeviceProtocolError("res:profiles:list carried no profiles array")
        profiles: list[Profile] = []
        for item in raw:
            try:
                profiles.append(Profile.model_validate(item))
            except ValidationError as exc:
                log.warning(
                    "device_profile_invalid",
                    host=self.host,
                    profile_id=item.get("id") if isinstance(item, dict) else None,
                    error=str(exc),
                )
        return profiles

    async def load_profile(self, profile_id: str) -> Profile:
        """One profile in full. Raises :class:`DeviceError` if it is not there."""
        message = await self._send("req:profiles:load", id=profile_id)
        body = message.get("profile")
        if not isinstance(body, dict):
            raise DeviceProtocolError(f"res:profiles:load carried no profile for {profile_id!r}")
        try:
            return Profile.model_validate(body)
        except ValidationError as exc:
            raise DeviceProtocolError(
                f"The machine's profile {profile_id!r} does not validate",
                details=str(exc),
            ) from exc

    # ── gated write surface ──────────────────────────────────────────
    #
    # Five methods, each one frame, each behind the gate. The validation that
    # decides whether a *document* is fit to send lives above this layer
    # (`gaggiclanker/domain/profile_policy.py` and the draft service); what is
    # enforced here is narrower and absolute: writes are off unless somebody
    # turned them on, a save never overwrites, and a delete only ever removes a
    # profile this box created.

    async def save_profile(self, profile: Profile) -> Profile:
        """Store ``profile`` on the machine as a **new** profile. Returns it.

        The returned profile is the firmware's own serialisation of what it
        stored, carrying the id `generateShortID()` assigned — which is the only
        way to learn that id, and the document the round-trip check compares
        against.

        **It cannot overwrite.** `ProfileManager::saveProfile` upserts on the
        file `/p/<id>.json`, so a save carrying an existing id replaces somebody
        else's profile; a profile that arrives here with an id is therefore
        refused outright rather than silently stripped, because a caller that
        passed one meant something by it and quietly doing something else is how
        you lose a profile somebody spent an evening on.

        Two things the firmware does on its own, worth knowing before reading
        the result back: a new profile is **auto-favourited**
        (`ProfileManager.cpp:186-188`), and `selected`/`favorite` are re-stamped
        from NVS on every load. Neither is part of what a profile brews, and
        :func:`canonical_profile_json` drops all three.
        """
        write = PendingWrite(
            kind="profile_save",
            host=self.host,
            device_id=profile.id,
            payload_hash=payload_hash(canonical_profile_json(profile)),
        )
        if profile.id is not None:
            refusal = DeviceWriteRefused(
                f"save_profile creates new profiles only, and this one carries the id "
                f"{profile.id!r}. Saving with an existing id overwrites that profile on the "
                "machine; build a fresh document with Profile.for_new_device_profile()."
            )
            # Audited like every other refusal. "Something tried to overwrite a
            # profile" is exactly the row a person wants to find later, and a
            # refusal that leaves no trace is the one kind nobody can debug.
            await self._record(write, "refused", str(refusal))
            raise refusal
        body = profile.to_device()
        message = await self._write(
            write,
            "req:profiles:save",
            id_from_response=_saved_profile_id,
            profile=body,
        )
        served = message.get("profile")
        if not isinstance(served, dict):
            raise DeviceProtocolError("res:profiles:save carried no profile back")
        try:
            stored = Profile.model_validate(served)
        except ValidationError as exc:
            raise DeviceProtocolError(
                "The machine accepted the profile and served back something that does not "
                "validate; treat the save as unverified and check the display",
                details=str(exc),
            ) from exc
        if stored.id is None:
            raise DeviceProtocolError("The machine saved a profile and gave it no id")
        log.info("device_profile_saved", host=self.host, profile_id=stored.id, label=stored.label)
        return stored

    async def delete_profile(self, profile_id: str) -> None:
        """Delete a profile **this box created**, and nothing else.

        Two independent proofs are required, and both can be checked:

        * the label on the machine right now ends in :data:`APP_PROFILE_SUFFIX`,
          which is read here, from the device, rather than from our mirror — the
          mirror can be stale and the display is the authority on its own files;
        * the gate finds an `ok` `profile_save` for this id in `device_writes`,
          which is the part a label cannot fake.

        Either alone is insufficient. A person can rename a profile to end in
        "[AI]"; an id can be reused by the firmware after a delete. Together
        they mean this is the profile we pushed and it is still ours.
        """
        write = PendingWrite(
            kind="profile_delete",
            host=self.host,
            device_id=profile_id,
            payload_hash=payload_hash(profile_id),
        )
        # The gate first, before the read. A delete that the switch forbids, or
        # that names a profile this box did not create, must not put a frame on
        # the wire at all — and `load_profile` is a frame. The machine has three
        # WebSocket slots and a refused write should cost it none of them.
        await self._authorize(write)
        profile = await self.load_profile(profile_id)
        if not profile.label.rstrip().endswith(APP_PROFILE_SUFFIX.strip()):
            refusal = DeviceWriteRefused(
                f"Profile {profile_id!r} is labelled {profile.label!r}, which does not carry the "
                f"{APP_PROFILE_SUFFIX.strip()} suffix. This box only deletes profiles it wrote."
            )
            await self._gate.record(write, result="refused", error=str(refusal))
            raise refusal
        await self._write(write, "req:profiles:delete", id=profile_id, authorized=True)
        log.info("device_profile_deleted", host=self.host, profile_id=profile_id)

    async def select_profile(self, profile_id: str) -> None:
        """Make ``profile_id`` the machine's selected profile.

        Never called by a push: a draft is pushed *beside* what the person is
        brewing with, never in place of it. This exists so the UI can offer it
        as a separate, deliberate action.
        """
        write = PendingWrite(
            kind="profile_select",
            host=self.host,
            device_id=profile_id,
            payload_hash=payload_hash(profile_id),
        )
        await self._write(write, "req:profiles:select", id=profile_id)

    async def favorite_profile(self, profile_id: str) -> None:
        """Star a profile, so it appears on the machine's home screen."""
        write = PendingWrite(
            kind="profile_favorite",
            host=self.host,
            device_id=profile_id,
            payload_hash=payload_hash(profile_id),
        )
        await self._write(write, "req:profiles:favorite", id=profile_id)

    async def unfavorite_profile(self, profile_id: str) -> None:
        """Unstar a profile.

        The one this pair exists for: the firmware auto-favourites every new
        profile, so a push puts an unreviewed draft on the home screen whether
        anybody wanted it there or not. This is how that is undone.
        """
        write = PendingWrite(
            kind="profile_unfavorite",
            host=self.host,
            device_id=profile_id,
            payload_hash=payload_hash(profile_id),
        )
        await self._write(write, "req:profiles:unfavorite", id=profile_id)

    async def delete_shot(self, shot_id: int | str) -> None:
        """Delete one shot from the machine: its `.slog`, its notes, its index row.

        **Unrecoverable.** There is no undo on the display and no copy left
        behind, which is why nothing about *whether* this shot may go is decided
        here: the gate's ``shot_delete`` branch refuses unless the archive holds
        the bytes, they are the length the header says they should be, the shot
        is not quarantined and it belongs to this machine
        (`gaggiclanker/cleanup/eligibility.py`). That check runs before a frame
        goes out, and its refusal is audited with the reason.

        The id goes on the wire padded. The firmware accepts either form for
        this one request, but every other history call uses the filename and a
        single spelling is one fewer thing to get wrong.
        """
        padded = pad6(shot_id)
        write = PendingWrite(
            kind="shot_delete",
            host=self.host,
            device_id=padded,
            payload_hash=payload_hash(padded),
        )
        await self._write(write, "req:history:delete", id=padded)
        log.info("device_shot_deleted", host=self.host, shot_id=padded)

    async def save_shot_notes(self, shot_id: int | str, notes: ShotNotes) -> ShotNotes:
        """Write the machine's own notes card for a shot.

        Three firmware details are handled by the caller building the document
        and re-asserted here, because getting any of them wrong is silent:

        * the id is **padded** — it is used verbatim as the filename
          `/h/<id>.json`, so an unpadded 129 writes a second, invisible file;
        * ``doseOut`` is only honoured as an override for the index's `volume`
          when it arrives as a **non-empty string** (`ShotHistoryPlugin.cpp:557`);
          :class:`~gaggiclanker.domain.models.ShotNotes` stores it as one, and
          :meth:`~gaggiclanker.domain.models.ShotNotes.to_device` keeps it that way;
        * ``timestamp`` is **ours to set**. The firmware never writes it, and it
          is the only thing that makes "is the device's copy newer than ours"
          answerable, so it is filled in here rather than trusted from the
          caller.

        The document is stored verbatim, extra keys included — which is what
        lets another client's field survive a write from this one.

        Returns the document as it was sent, which is exactly what the machine
        now holds: the mirror is updated from this rather than from a re-read,
        because a re-read costs a second frame and could only disagree with it
        by the machine having lied.
        """
        padded = pad6(shot_id)
        sent = notes.model_copy(update={"id": padded, "timestamp": int(time.time())})
        document = sent.to_device()
        write = PendingWrite(
            kind="notes_save",
            host=self.host,
            device_id=padded,
            payload_hash=payload_hash(json.dumps(document, sort_keys=True)),
        )
        await self._write(write, "req:history:notes:save", id=padded, notes=document)
        log.info("device_shot_notes_saved", host=self.host, shot_id=padded)
        return sent

    async def _authorize(self, write: PendingWrite) -> None:
        """Ask the gate, and record the refusal if it says no.

        Separate from :meth:`_write` because one write — the delete — has to
        read from the machine before it knows whether it is allowed to proceed,
        and that read must not happen until the gate has already said yes.
        """
        try:
            await self._gate.authorize(write)
        except DeviceError as exc:
            await self._record(write, "refused", str(exc))
            raise

    async def _write(
        self,
        write: PendingWrite,
        tp: str,
        *,
        id_from_response: Callable[[dict[str, Any]], str | None] | None = None,
        authorized: bool = False,
        **payload: Any,
    ) -> dict[str, Any]:
        """Authorise, send, record. The only path from a write method to `_send`.

        Every outcome leaves an audit row, and the three results mean different
        things to whoever reads them later: `refused` never reached the wire,
        `failed` did and the machine said no or said nothing, `ok` is a write
        the machine acknowledged — which is not yet the same as a write the
        machine *stored*, and that is what the round-trip check is for.

        ``id_from_response`` exists for the save, which is the one write whose
        subject does not exist until the machine answers: the audit row has to
        carry the id `generateShortID()` produced, or no save would ever satisfy
        the provenance check and nothing this box pushed could be deleted by it.

        ``authorized`` says the caller has already been through
        :meth:`_authorize` — the delete has to, because it reads the profile's
        label off the machine first and that read must not happen for a write
        the gate would refuse. Asking twice would be harmless but would also let
        a second refusal write a second audit row for one attempt.

        A failure inside :meth:`DeviceWriteGate.record` is swallowed: losing the
        audit row for a write that worked is bad, and turning it into an
        exception that makes the caller think the write failed is worse.
        """
        if not authorized:
            await self._authorize(write)
        try:
            message = await self._send(tp, **payload)
        except Exception as exc:
            await self._record(write, "failed", f"{type(exc).__name__}: {exc}")
            raise
        recorded = write
        if id_from_response is not None:
            assigned = id_from_response(message)
            if assigned:
                recorded = replace(write, device_id=assigned)
        await self._record(recorded, "ok", "")
        return message

    async def _record(
        self, write: PendingWrite, result: Literal["ok", "refused", "failed"], error: str
    ) -> None:
        try:
            await self._gate.record(write, result=result, error=error[:500])
        except Exception:  # pragma: no cover - bookkeeping must not break a write
            log.warning("device_write_audit_failed", host=self.host, kind=write.kind, exc_info=True)

    # ── read-only WebSocket surface, continued ───────────────────────

    async def get_shot_notes(self, shot_id: int | str) -> ShotNotes | None:
        """The device's notes for a shot, or ``None`` when it has none.

        The id goes on the wire **padded**: `req:history:notes:get` uses it
        verbatim as the filename `/h/<id>.json`, so an unpadded 129 reads a
        file that does not exist and looks exactly like "no notes".
        """
        padded = pad6(shot_id)
        message = await self._send("req:history:notes:get", id=padded)
        notes = message.get("notes")
        if not isinstance(notes, dict) or not notes:
            return None
        return self._parse_notes(notes, padded)

    async def get_ota_settings(self) -> OtaSettings:
        """The machine's identity: firmware versions and the controller board.

        Not a normal request/response: the firmware *broadcasts* the answer to
        every client with no `rid` (`WebUIPlugin.cpp:613`), so this registers a
        waiter for the next one instead of correlating. A periodic broadcast
        that lands first is a perfectly good answer.
        """
        socket = self._ws
        if socket is None:
            raise DeviceUnavailable(
                f"Not connected to the machine at {self.host}; ota-settings was not requested"
            )
        waiter: asyncio.Future[OtaSettings] = asyncio.get_running_loop().create_future()
        self._ota_waiters.append(waiter)
        try:
            await socket.send(json.dumps({"tp": "req:ota-settings"}))
            async with asyncio.timeout(self.timeout):
                return await waiter
        except TimeoutError:
            raise DeviceTimeout(
                f"The machine did not broadcast ota-settings within {self.timeout:g}s"
            ) from None
        except WebSocketException as exc:
            raise DeviceUnavailable(f"The machine disconnected: {exc}") from exc
        finally:
            with contextlib.suppress(ValueError):
                self._ota_waiters.remove(waiter)

    # ── read-only HTTP surface ───────────────────────────────────────

    async def fetch_index(self) -> ShotIndex | None:
        """`/api/history/index.bin` — every shot the machine has ever recorded."""
        raw = await self._get_bytes("/api/history/index.bin")
        if raw is None:
            return None
        return self._parse_index(raw, "index.bin")

    async def fetch_recent(self, limit: int = 8) -> ShotIndex | None:
        """`/api/history/recent.bin` — the newest ``limit`` shots, newest first.

        The firmware clamps ``limit`` to 1..50 and answers with an index whose
        `nextId` is 0, so it is a view rather than a substitute for the index.
        """
        clamped = max(1, min(50, limit))
        raw = await self._get_bytes(f"/api/history/recent.bin?limit={clamped}")
        if raw is None:
            return None
        return self._parse_index(raw, "recent.bin")

    async def fetch_slog(self, shot_id: int | str) -> SlogFetch | None:
        """One shot file, retried while the machine is still writing it.

        `evt:history-shot-saved` fires after the file is closed, but the index
        poller and the calibration flow both reach a shot earlier than that,
        and the firmware happily serves a header with no samples. A file that
        is only a header (or claims `sampleCount == 0` while carrying bytes) is
        therefore re-fetched with backoff for up to
        :data:`SLOG_RETRY_BUDGET_S`; after that we return what we have with
        ``incomplete=True`` rather than nothing, because the samples present are
        real and the machine may delete the file before we ask again.
        """
        numeric = unpad(shot_id)
        padded = pad6(numeric)
        deadline = time.monotonic() + self._slog_retry_budget
        wait = SLOG_RETRY_INITIAL_S

        while True:
            raw = await self._get_bytes(f"/api/history/{padded}.slog")
            if raw is None:
                return None
            still_writing = _looks_header_only(raw)
            if not still_writing or time.monotonic() >= deadline:
                return self._parse_slog(numeric, raw, incomplete_hint=still_writing)
            log.debug("device_slog_header_only", host=self.host, shot_id=numeric, bytes=len(raw))
            # Clamped to what is left of the budget, so the worst case really is
            # SLOG_RETRY_BUDGET_S and not that plus one whole final sleep.
            await asyncio.sleep(min(wait, max(0.0, deadline - time.monotonic())))
            wait = min(wait * 2, SLOG_RETRY_MAX_S)

    async def fetch_notes_json(self, shot_id: int | str) -> ShotNotes | None:
        """`/api/history/<id6>.json` — the notes file, or ``None`` if there is none.

        The same document :meth:`get_shot_notes` fetches over the socket. Both
        exist because the socket is the machine's own UI's route and HTTP is the
        one that still works when all three WebSocket slots are taken.
        """
        padded = pad6(shot_id)
        raw = await self._get_bytes(f"/api/history/{padded}.json", accept="application/json")
        if raw is None:
            return None
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise DeviceProtocolError(
                f"The machine's notes for {padded} are not JSON", details=str(exc)
            ) from exc
        if not isinstance(body, dict) or not body:
            return None
        return self._parse_notes(body, padded)

    async def get_status(self) -> dict[str, Any]:
        """`GET /api/status` — `{mode, tt, ct}`.

        The cheapest possible "is the machine there", which is why discovery
        uses it before opening a socket (firmware report §10.1).
        """
        return await self._get_json("/api/status")

    async def get_settings(self) -> dict[str, Any]:
        """`GET /api/settings` — the machine's own settings, read-only.

        There is deliberately no writing counterpart. `POST /api/settings`
        clears every checkbox-style boolean key absent from the body, so a
        partial write silently turns off HomeKit, boiler fill and the momentary
        buttons; it is also the one endpoint that can change Wi-Fi and the PID.
        """
        return await self._get_json("/api/settings")

    # ── HTTP plumbing ────────────────────────────────────────────────

    async def _get_bytes(
        self, path: str, *, accept: str = "application/octet-stream"
    ) -> bytes | None:
        """One bounded GET. ``None`` on 404; everything else raises.

        The device ignores `Accept` — `serveStatic` picks the content type from
        the file name and the SPA fallback answers HTML whatever you ask for.
        It is sent anyway because it makes the *log* unambiguous: "we asked for
        octet-stream and got a web page" is a complete bug report, where a
        request with no Accept leaves the reader wondering whether we asked for
        the right thing. (`Accept-Encoding` is the header the firmware does act
        on, for its gzipped assets; aiohttp sets that itself.)
        """
        session = self._session
        if session is None:
            raise DeviceUnavailable("The device client is not started")

        url = f"{self.http_base}{path}"
        async with self._http_slots:
            try:
                async with session.get(
                    url,
                    headers={"Accept": accept},
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:
                    if response.status == 404:
                        return None
                    if response.status == 503:
                        # Every /api/history/* route answers this for the whole
                        # duration of an OTA update (WebUIPlugin.cpp:189).
                        raise DeviceBusy(
                            f"The machine refused {path} with 503 (update in progress)"
                        )
                    if response.status >= 400:
                        raise DeviceError(
                            f"The machine answered {response.status} for {path}",
                            details={"status": response.status},
                        )
                    body = await response.read()
            except TimeoutError:
                raise DeviceTimeout(
                    f"The machine did not answer {path} within {self.timeout:g}s"
                ) from None
            except aiohttp.ClientError as exc:
                raise DeviceUnavailable(f"Could not reach the machine for {path}: {exc}") from exc

        if is_html_response(body):
            raise DeviceProtocolError(
                f"The machine served its web UI instead of {path}. "
                "The firmware answers unknown paths with the SPA, and /api/history "
                "returns HTML under memory pressure — worth retrying.",
                details={"path": path, "bytes": len(body)},
            )
        return body

    async def _get_json(self, path: str) -> dict[str, Any]:
        raw = await self._get_bytes(path, accept="application/json")
        if raw is None:
            raise DeviceError(f"The machine has no {path}")
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise DeviceProtocolError(
                f"The machine's {path} is not JSON", details=str(exc)
            ) from exc
        if not isinstance(body, dict):
            raise DeviceProtocolError(f"The machine's {path} is not a JSON object")
        return body

    # ── parsing helpers ──────────────────────────────────────────────

    def _parse_index(self, raw: bytes, what: str) -> ShotIndex:
        try:
            return parse_index(raw)
        except ValueError as exc:
            raise DeviceProtocolError(
                f"The machine's {what} did not parse", details=str(exc)
            ) from exc

    def _parse_slog(self, shot_id: int, raw: bytes, *, incomplete_hint: bool) -> SlogFetch:
        """Wrap the bytes with a parse attempt. A parse failure is not a fetch failure.

        This is the archive's central rule in one method: the bytes are the
        product, the parse is a convenience. A shot that does not parse is
        returned with ``parse_error`` set so the sync engine can store it quarantined; the
        machine will have deleted its copy long before the parser is fixed.
        """
        try:
            slog = parse_slog(raw, shot_id=pad6(shot_id))
        except SlogError as exc:
            log.warning("device_slog_unparseable", host=self.host, shot_id=shot_id, error=str(exc))
            return SlogFetch(
                shot_id=shot_id, raw=raw, parse_error=str(exc), incomplete=incomplete_hint
            )
        return SlogFetch(
            shot_id=shot_id,
            raw=raw,
            slog=slog,
            incomplete=incomplete_hint or slog.incomplete,
        )

    def _parse_notes(self, body: dict[str, Any], padded: str) -> ShotNotes:
        # The firmware stores whatever object it was handed, so `id` may be
        # missing entirely; the one we asked for is the right answer.
        payload = {"id": padded, **body}
        try:
            return ShotNotes.model_validate(payload)
        except ValidationError as exc:
            raise DeviceProtocolError(
                f"The machine's notes for {padded} do not validate", details=str(exc)
            ) from exc


def _saved_profile_id(message: dict[str, Any]) -> str | None:
    """The id `req:profiles:save` came back with, for the audit row.

    Deliberately forgiving: this runs before the response is validated, and an
    audit row naming the id is worth having even when the document beside it
    turns out to be something we cannot parse.
    """
    profile = message.get("profile")
    if not isinstance(profile, dict):
        return None
    served = profile.get("id")
    return served if isinstance(served, str) and served else None


def _looks_header_only(raw: bytes) -> bool:
    """Whether the device is still writing this `.slog`.

    Two shapes, both seen in the firmware's own calibration client
    (`web/src/pages/PumpFlowCalibration/api.js:31-56`):

    * the file is no longer than its header — nothing has been flushed yet;
    * the header still says `sampleCount == 0` while bytes follow it, which is
      what a file looks like between the first sample being written and the
      count being patched in at close.

    Anything too short to read a version out of is treated as still-writing
    too; :func:`parse_slog` would only raise on it.
    """
    if len(raw) < 6:
        return True
    version = raw[4]
    header_size = header_size_for(version)
    if len(raw) <= header_size:
        return True
    if len(raw) < 20:
        return True
    sample_count = int.from_bytes(raw[16:20], "little")
    return sample_count == 0


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)
