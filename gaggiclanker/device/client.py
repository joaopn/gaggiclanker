"""`GaggimateClient` — one persistent socket and a bounded HTTP fetcher.

The firmware allows **three** WebSocket clients in total and the machine's own
browser UI is one of them, so this class holds exactly one connection for the
whole process and multiplexes every request over it. A second connection is not
a fallback; index polling is what the sync engine falls back to.

The read-only rule
------------------

The prototype writes nothing to the device. That is
not a convention here, it is the type surface — the only public methods are the
ten reads listed in :data:`READ_ONLY_METHODS`, :meth:`_send` is private, and
``tests/device/test_public_surface.py`` fails the build if a tenth appears. A
write to `/api/settings` clears every boolean key it omits, and a bad
`req:profiles:save` can leave the machine with a profile that will not brew;
neither is something a sync loop should be able to reach by accident.

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
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self
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
from gaggiclanker.domain.ids import pad6, unpad
from gaggiclanker.domain.index import parse_index
from gaggiclanker.domain.models import LiveStatus, OtaSettings, Profile, ShotIndex, ShotNotes
from gaggiclanker.domain.slog import Slog, SlogError, header_size_for, is_html_response, parse_slog
from gaggiclanker.infra.sse import EventBus

__all__ = ["READ_ONLY_METHODS", "GaggimateClient", "SlogFetch"]

log = structlog.get_logger(__name__)

#: The client's entire public API. Pinned by a test rather than by review: the
#: point of a read-only prototype is that nobody can add `save_profile()`
#: without also deleting the line that says the device is read-only.
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
    ) -> None:
        if not host:
            raise ValueError("GaggimateClient needs a host; an empty host means 'no machine'")
        self.host = host
        self.protocol = protocol
        self.timeout = timeout
        self.events: DeviceEventBus = events if events is not None else EventBus[DeviceEvent]()

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
