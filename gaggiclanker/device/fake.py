"""An in-process GaggiMate, faithful to the parts that bite.

Why this exists rather than a pile of mocks: every interesting thing about the
real device is a *behaviour over time* — a `.slog` that is header-only for two
requests and complete on the third, a socket that drops mid-request, a 4th
client being refused, HTML arriving where binary was asked for. A mock that
returns a canned body cannot express any of that, and those are exactly the
cases that break a sync loop at 3 a.m.

So this is a real aiohttp server on a real loopback port, speaking the real
wire protocol, with switches for each quirk (:class:`FakeDevice`). It is used
three ways:

* the offline test suite, through the ``fake_device`` fixture;
* ``python -m gaggiclanker.device.fake --port 8090``, so the front end can be
  developed against a machine that is online, holds shots and answers a pull
  without anybody owning a GaggiMate;
* as the shape the simulator test asserts against, so the two agree.

Fixture data comes from ``tests/fixtures`` when it is there (real `.slog` bytes
and real profiles, which is what makes the parsers worth testing) and from a
small synthesised shot when it is not, so the CLI works from an installed
package too.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import random
import string
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.index import encode_index
from gaggiclanker.domain.models import (
    SHOT_FLAG_COMPLETED,
    SHOT_FLAG_DELETED,
    SHOT_FLAG_HAS_NOTES,
    IndexEntry,
    IndexHeader,
    PhaseTransition,
    Sample,
    ShotIndex,
    SlogHeader,
)
from gaggiclanker.domain.slog import FIELDS_MASK_ALL, Slog, encode_slog

__all__ = ["FakeDevice", "FakeShot", "run_fake_device", "write_profile"]

#: What the real firmware answers with for any path it does not recognise. The
#: byte-for-byte shape does not matter; that it starts with a doctype does,
#: because that is what `is_html_response` keys on.
SPA_HTML = b"<!DOCTYPE html><html><head><title>GaggiMate</title></head><body></body></html>"

#: `-DDEFAULT_MAX_WS_CLIENTS=3` in the firmware's `platformio.ini`, and the
#: machine's own browser UI is usually one of them.
#:
#: The limit works the opposite way round from the obvious guess, and getting
#: it wrong hides a real bug: a fourth client is **accepted** and gets its state
#: frame, and then `WebSocketHandler::loop` calls `ws.cleanupClients()` — which
#: ESPAsyncWebServer runs once a second and which closes the **oldest** client
#: when the count exceeds the limit. So the newcomer wins and an incumbent is
#: evicted, which is why a client that resets its backoff on a successful
#: handshake reconnects for ever.
MAX_WS_CLIENTS = 3

#: The identity the fake reports. A Pro board, because that is the maintainer's
#: machine and the one where pressure telemetry is real.
DEFAULT_IDENTITY: dict[str, Any] = {
    "latestVersion": "1.9.0",
    "displayVersion": "v1.9.0-3-gabc123",
    "controllerVersion": "v1.9.0",
    "hardware": "GaggiMate Pro Rev 1.1",
    "displayUpdateAvailable": False,
    "controllerUpdateAvailable": False,
    "channel": "latest",
    "updating": False,
}

#: `GET /api/status` on the real device is this small: mode, target temp,
#: current temp, and nothing else (`WebUIPlugin.cpp:172`).
DEFAULT_HTTP_STATUS: dict[str, Any] = {"mode": 1, "tt": 93.0, "ct": 92.4}

DEFAULT_DEVICE_SETTINGS: dict[str, Any] = {
    "startupMode": "standby",
    "targetSteamTemp": 145,
    "targetWaterTemp": 80,
    "mdnsName": "gaggimate",
    "pid": "58.397,1.027,249.055,0.0",
    # The predictive brew delay a shot runs with, mirrored into every `.slog`
    # header, and the boiler probe offset. Both are read by the sync engine's
    # identity pass; neither is ever written back — POST /api/settings clears
    # every boolean key it omits.
    "brewDelay": 800,
    "temperatureOffset": 2.5,
    "flushDuration": 5,
    "homekit": False,
    "boilerFillActive": False,
}


@dataclass
class FakeShot:
    """One shot the fake serves: its index row, its bytes, maybe its notes."""

    entry: IndexEntry
    slog_bytes: bytes
    notes: dict[str, Any] | None = None
    #: Serve a header-only file for this many more requests before the real
    #: bytes — the "device is still writing" case, counted down per request.
    header_only_requests: int = 0


#: Characters `generateShortID()` draws from (`utils.h`), and the length the
#: schema's own example implies. Random ids rather than a counter, because a
#: caller that guessed "the next one will be p2" would pass every test here and
#: fail against a machine.
SHORT_ID_ALPHABET = string.ascii_letters + string.digits
SHORT_ID_LENGTH = 10


def generate_short_id() -> str:
    """The firmware's own id generator, in Python."""
    return "".join(random.choice(SHORT_ID_ALPHABET) for _ in range(SHORT_ID_LENGTH))  # noqa: S311


def write_profile(profile: dict[str, Any], *, favorite: bool, selected: bool) -> dict[str, Any]:
    """Serialise a profile the way `writeProfile` does (`profile.h:335-410`).

    This is the single most load-bearing quirk in the whole feature, because it
    is what a save-then-load round trip compares against. The firmware never
    echoes what you sent: it parses into a struct and serialises the struct, so
    the document that comes back has fields the document that went out did not.

    Specifically it **always** emits, whatever the input said:

    * `id`, `favorite` and `selected` — all three owned by NVS, not by the file;
    * a `transition` on every phase, defaulting to instant/0/false;
    * `transition.target`, which is absent from `schema/profile.json` entirely
      and defaults to `"time"`;
    * a phase `temperature`, spelled out as `0` where the author left it out.

    And it omits `targets` when the list is empty, rather than sending `[]`.

    `canonical_profile_json` drops exactly this set, which is why a faithful
    round trip compares equal.
    """
    phases = [_write_phase(phase) for phase in profile.get("phases", [])]
    written: dict[str, Any] = {
        "id": profile.get("id", ""),
        "label": profile.get("label", ""),
        "type": profile.get("type", "standard"),
        "description": profile.get("description", ""),
        "temperature": float(profile.get("temperature", 0) or 0),
        "favorite": favorite,
        "selected": selected,
        "utility": bool(profile.get("utility", False)),
        "phases": phases,
    }
    # `^_` annotation keys are round-tripped by the real firmware's parser only
    # in the sense that it ignores them and they stay in the file it rewrites.
    # It rewrites the file from the struct, so they are lost. Reproduced.
    return written


def _write_phase(phase: dict[str, Any]) -> dict[str, Any]:
    duration = float(phase.get("duration", 0) or 0)
    transition = dict(phase.get("transition") or {})
    # "Duration longer than phase duration is clamped" (profile.h:255-272). A
    # silent clamp on the device is a round-trip mismatch here unless the
    # policy has already done it, which is exactly why the policy does it.
    ramp = float(transition.get("duration", 0) or 0)
    written: dict[str, Any] = {
        "name": str(phase.get("name", "")),
        # An unrecognised phase kind parses as `brew` (profile.h).
        "phase": phase.get("phase") if phase.get("phase") in ("preinfusion", "brew") else "brew",
        "valve": int(phase.get("valve", 0) or 0),
        "duration": duration,
        "temperature": float(phase.get("temperature", 0) or 0),
        "transition": {
            "type": transition.get("type", "instant"),
            "target": transition.get("target", "time"),
            "duration": min(ramp, duration),
            "adaptive": bool(transition.get("adaptive", False)),
        },
        "pump": phase.get("pump", 0),
    }
    targets = [
        {
            "type": target.get("type"),
            # Any spelling that is not `gte` parses as LTE (profile.h:291).
            "operator": "gte" if target.get("operator", "gte") == "gte" else "lte",
            "value": float(target.get("value", 0) or 0),
        }
        for target in phase.get("targets") or []
        # An unknown target type is silently dropped.
        if target.get("type") in ("volumetric", "pressure", "flow", "pumped")
    ]
    if targets:
        written["targets"] = targets
    return written


@dataclass
class FakeDevice:
    """A GaggiMate on localhost, with every quirk behind a switch.

    Behaviour switches are plain attributes so a test reads like the scenario it
    is describing::

        device.ota_in_progress = True     # every /api/history/* is now a 503
        device.html_paths.add("/api/history/index.bin")
        await device.emit_shot_saved(130)
    """

    shots: dict[int, FakeShot] = field(default_factory=dict)
    profiles: list[dict[str, Any]] = field(default_factory=list)
    identity: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_IDENTITY))
    http_status: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_HTTP_STATUS))
    device_settings: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_DEVICE_SETTINGS))

    #: Every `/api/history/*` request answers `503 Update in progress`, and
    #: every `req:history*`/`req:profiles:*` frame comes back with an error.
    ota_in_progress: bool = False
    #: Paths that answer with the SPA instead of what was asked for.
    html_paths: set[str] = field(default_factory=set)
    #: Request types the fake receives and deliberately never answers.
    hang_requests: set[str] = field(default_factory=set)
    #: How many clients may be attached at once. A connection over the limit
    #: evicts the oldest, as the firmware does.
    max_clients: int = MAX_WS_CLIENTS
    #: Turn the limit into "refuse the newcomer" instead. Not what this
    #: firmware does — kept because it is what a *different* server would do
    #: and a client has to survive both.
    refuse_newcomer: bool = False
    #: Serve `index.bin` as a 404, the way a device with no history does.
    index_missing: bool = False
    #: Shot ids whose index entry exists but whose file does not. That is what
    #: `cleanupHistory()` leaves behind when it frees space: the entry is
    #: flagged deleted and stays for ever, the `.slog` is gone.
    missing_files: set[int] = field(default_factory=set)
    #: Shot ids whose file is served but which are missing from `index.bin` —
    #: the other half of the same race. The firmware closes the `.slog`, appends
    #: the index entry and broadcasts `evt:history-shot-saved` in quick
    #: succession but not atomically, so a client can be told about a shot the
    #: index has not listed yet.
    hidden_from_index: set[int] = field(default_factory=set)
    #: Profile ids the fake has been asked to star. The real firmware keeps
    #: favourites and the selected id in NVS rather than in the profile files,
    #: and stamps them onto the JSON as it serialises — so a test that asserts
    #: on `favorite` is asserting on this, not on what it saved.
    favorite_profile_ids: set[str] = field(default_factory=set)
    selected_profile_id: str | None = None
    #: A hook that corrupts what a save stores, for the one test that has to
    #: prove the round-trip check catches a machine that did not store what it
    #: was sent. Takes the parsed profile and returns what to keep.
    mutate_on_save: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    #: Seconds to stall every `/api/history/*` response. The real machine takes
    #: tens of milliseconds to read a `.slog` off LittleFS and rather longer off
    #: an SD card, and "does the API stay responsive during a backfill" is only
    #: a real question when the fetches are slow enough to overlap with it.
    history_delay_s: float = 0.0

    # Populated by start().
    host: str = ""
    port: int = 0
    #: Every request path the fake has served, in order. The cheapest way for a
    #: test to assert that a padded id went out on the wire.
    requests: list[str] = field(default_factory=list)
    #: Every WebSocket request type the fake has received, in order, answered
    #: or not. What a test reads to prove that no `req:history:notes:save` or
    #: `req:history:delete` frame was ever sent, rather than inferring it from
    #: state the frame would have changed.
    ws_requests: list[str] = field(default_factory=list)

    _runner: web.AppRunner | None = None
    _clients: list[web.WebSocketResponse] = field(default_factory=list)
    _refused: int = 0
    _evicted: int = 0

    # ── lifecycle ────────────────────────────────────────────────────

    async def start(self, port: int = 0, host: str = "127.0.0.1") -> str:
        """Bind a loopback socket and return ``"<host>:<port>"``.

        Port 0 by default so tests never collide, and the caller reads the real
        port back off the return value.
        """
        app = web.Application()
        app.router.add_get("/ws", self._ws_handler)
        app.router.add_get("/api/status", self._status_handler)
        app.router.add_route("*", "/api/settings", self._settings_handler)
        app.router.add_get("/api/history/index.bin", self._index_handler)
        app.router.add_get("/api/history/recent.bin", self._recent_handler)
        app.router.add_get("/api/history/{name}", self._history_file_handler)
        # The catch-all is last and answers the SPA, exactly as the firmware
        # does — which is the trap `is_html_response` exists to catch.
        app.router.add_route("*", "/{tail:.*}", self._spa_handler)

        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        self._runner = runner
        # aiohttp exposes no public accessor for the bound port after port=0.
        sockets = getattr(site._server, "sockets", None)
        self.port = sockets[0].getsockname()[1] if sockets else port
        self.host = host
        return self.address

    @property
    def address(self) -> str:
        """``"host:port"`` — what :class:`GaggimateClient` takes as its host."""
        return f"{self.host}:{self.port}"

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def refused_connections(self) -> int:
        """How many newcomers were turned away (``refuse_newcomer`` mode only)."""
        return self._refused

    @property
    def evicted_connections(self) -> int:
        """How many incumbents were evicted to make room for a newcomer."""
        return self._evicted

    async def stop(self) -> None:
        await self.drop_connections()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def __aenter__(self) -> FakeDevice:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ── scripted behaviour ───────────────────────────────────────────

    def add_shot(
        self,
        shot_id: int,
        slog_bytes: bytes,
        *,
        notes: dict[str, Any] | None = None,
        timestamp: int | None = None,
        profile_name: str = "Test Profile",
        header_only_requests: int = 0,
    ) -> FakeShot:
        """Register a shot: an index row, the file behind it, optional notes."""
        flags = SHOT_FLAG_COMPLETED | (SHOT_FLAG_HAS_NOTES if notes else 0)
        entry = IndexEntry(
            id=shot_id,
            timestamp=timestamp if timestamp is not None else 1_700_000_000 + shot_id,
            duration_ms=28_000,
            volume_g=36.0,
            rating=0,
            flags=flags,
            profile_id="test",
            profile_name=profile_name,
            avg_temp_c=93.0,
            max_pressure_bar=9.1,
            avg_flow_ml_s=1.8,
        )
        shot = FakeShot(
            entry=entry,
            slog_bytes=slog_bytes,
            notes=notes,
            header_only_requests=header_only_requests,
        )
        self.shots[shot_id] = shot
        return shot

    def index(self) -> ShotIndex:
        """The index as the fake would encode it, newest id last."""
        entries = [
            self.shots[key].entry for key in sorted(self.shots) if key not in self.hidden_from_index
        ]
        next_id = (max(self.shots) + 1) if self.shots else 1
        return ShotIndex(
            header=IndexHeader(
                version=1, entry_size=128, entry_count=len(entries), next_id=next_id
            ),
            entries=entries,
        )

    async def broadcast(self, frame: dict[str, Any]) -> None:
        """Send one frame to every attached client."""
        payload = json.dumps(frame)
        for socket in list(self._clients):
            with contextlib.suppress(Exception):
                await socket.send_str(payload)

    async def emit_status(self, **frame: Any) -> None:
        """Push one `evt:status` frame. Keys are the firmware's (`ct`, `sys`, …)."""
        await self.broadcast({"tp": "evt:status", **frame})

    async def emit_shot_saved(self, shot_id: int) -> None:
        """`evt:history-shot-saved` — an **unpadded** int, as the firmware sends."""
        await self.broadcast({"tp": "evt:history-shot-saved", "id": shot_id})

    async def emit_shot_finished_stats(self, max_pressure: float, avg_flow: float) -> None:
        await self.broadcast(
            {"tp": "evt:shot-finished-stats", "maxPressure": max_pressure, "avgFlow": avg_flow}
        )

    async def emit_ota_settings(self, **overrides: Any) -> None:
        """The unsolicited identity broadcast the device sends every 30 minutes."""
        await self.broadcast({"tp": "res:ota-settings", **self.identity, **overrides})

    async def emit_ota_progress(self, phase: int, progress: int) -> None:
        await self.broadcast({"tp": "evt:ota-progress", "phase": phase, "progress": progress})

    async def emit_rebuild_progress(self, total: int, current: int, status: str) -> None:
        await self.broadcast(
            {
                "tp": "evt:history-rebuild-progress",
                "total": total,
                "current": current,
                "status": status,
            }
        )

    async def drop_connections(self) -> None:
        """Close every socket without warning, the way a Wi-Fi blip does."""
        for socket in list(self._clients):
            with contextlib.suppress(Exception):
                await socket.close(code=1006, message=b"dropped")
        self._clients.clear()

    async def run_brew(self, shot_id: int, *, slog_bytes: bytes | None = None) -> None:
        """Play the whole shot-completion sequence from firmware report §2.6.

        Start → finished stats → the file appears → `evt:history-shot-saved`.
        The order matters: the stats frame arrives *before* the file is closed,
        so a client that fetched on the stats frame would get a header.
        """
        await self.emit_status(process={"a": 1, "s": "brew", "l": "Infusion", "e": 0})
        await self.emit_status(process={"a": 0, "s": "brew", "l": "Finished", "e": 28_000})
        await self.emit_shot_finished_stats(9.1, 1.8)
        if shot_id not in self.shots:
            self.add_shot(shot_id, slog_bytes if slog_bytes is not None else synthetic_slog_bytes())
        await self.emit_shot_saved(shot_id)

    # ── HTTP handlers ────────────────────────────────────────────────

    def _record(self, request: web.Request) -> None:
        self.requests.append(request.path_qs)

    async def _stall(self) -> None:
        """Spend :attr:`history_delay_s` before answering, if one is set."""
        if self.history_delay_s > 0:
            await asyncio.sleep(self.history_delay_s)

    def _quirk(self, request: web.Request, *, history: bool) -> web.Response | None:
        """The two failures that can hit any route, before the route runs."""
        if history and self.ota_in_progress:
            return web.Response(status=503, text="Update in progress")
        if request.path in self.html_paths:
            return web.Response(body=SPA_HTML, content_type="text/html")
        return None

    async def _status_handler(self, request: web.Request) -> web.Response:
        self._record(request)
        quirk = self._quirk(request, history=False)
        return quirk if quirk is not None else web.json_response(self.http_status)

    async def _settings_handler(self, request: web.Request) -> web.Response:
        self._record(request)
        if request.method != "GET":
            # The real device accepts a POST here and clears every boolean key
            # the body omits. gaggiclanker must never send one, so the fake
            # refuses rather than pretending — a test that writes settings
            # fails loudly instead of passing quietly.
            return web.Response(status=405, text="the fake device is read-only")
        quirk = self._quirk(request, history=False)
        return quirk if quirk is not None else web.json_response(self.device_settings)

    async def _index_handler(self, request: web.Request) -> web.Response:
        self._record(request)
        quirk = self._quirk(request, history=True)
        if quirk is not None:
            return quirk
        await self._stall()
        if self.index_missing:
            return web.Response(status=404, text="Index not found")
        return web.Response(
            body=encode_index(self.index()), content_type="application/octet-stream"
        )

    async def _recent_handler(self, request: web.Request) -> web.Response:
        self._record(request)
        quirk = self._quirk(request, history=True)
        if quirk is not None:
            return quirk
        try:
            limit = int(request.query.get("limit", "8"))
        except ValueError:
            limit = 8
        limit = max(1, min(50, limit))
        entries = [self.shots[key].entry for key in sorted(self.shots, reverse=True)][:limit]
        # The firmware answers recent.bin with nextId zeroed: it is a window,
        # not the index (`WebUIPlugin.cpp:202`).
        recent = ShotIndex(
            header=IndexHeader(version=1, entry_size=128, entry_count=len(entries), next_id=0),
            entries=entries,
        )
        return web.Response(body=encode_index(recent), content_type="application/octet-stream")

    async def _history_file_handler(self, request: web.Request) -> web.Response:
        self._record(request)
        quirk = self._quirk(request, history=True)
        if quirk is not None:
            return quirk

        await self._stall()
        name = request.match_info["name"]
        stem, _, suffix = name.partition(".")
        # The device serves `/h/` with serveStatic, so the *padded* name is the
        # filename. An unpadded id is a 404 here exactly as it is there.
        shot = next((s for s in self.shots.values() if pad6(s.entry.id) == stem), None)
        if shot is None or shot.entry.id in self.missing_files:
            return web.Response(status=404, text="Not found")

        if suffix == "slog":
            if shot.header_only_requests > 0:
                shot.header_only_requests -= 1
                return web.Response(
                    body=header_only_bytes(shot.slog_bytes),
                    content_type="application/octet-stream",
                )
            return web.Response(body=shot.slog_bytes, content_type="application/octet-stream")
        if suffix == "json":
            if shot.notes is None:
                return web.Response(status=404, text="Not found")
            return web.json_response(shot.notes)
        return web.Response(status=404, text="Not found")

    async def _spa_handler(self, request: web.Request) -> web.Response:
        self._record(request)
        return web.Response(body=SPA_HTML, content_type="text/html")

    # ── WebSocket ────────────────────────────────────────────────────

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()

        if self.refuse_newcomer and len(self._clients) >= self.max_clients:
            self._refused += 1
            await socket.prepare(request)
            await socket.close(code=1013, message=b"too many clients")
            return socket

        await socket.prepare(request)
        self._clients.append(socket)
        # Over the limit: the newcomer stays and the oldest goes, which is what
        # `cleanupClients()` does on the device (see MAX_WS_CLIENTS). Closing
        # them here rather than on a timer is the same sequence compressed —
        # the firmware's tick is one second, and a test should not wait for it.
        await self._evict_oldest()

        try:
            # A new client gets the full state frame immediately, as the real
            # one does (`publishState`, WebSocketHandler.cpp:350) — unless the
            # eviction above closed *this* socket, which is what happens at
            # max_clients = 0. Writing to it then raises
            # ClientConnectionResetError and aiohttp prints the traceback, which
            # in a test run reads like a failure rather than the scenario the
            # test asked for.
            if not socket.closed:
                await socket.send_str(json.dumps({"tp": "evt:status", **self._state_frame()}))
            async for message in socket:
                if message.type is not WSMsgType.TEXT:
                    continue
                await self._handle_request(socket, message.data)
        finally:
            if socket in self._clients:
                self._clients.remove(socket)
        return socket

    async def _evict_oldest(self) -> None:
        """Close incumbents until the client count is back within the limit."""
        while len(self._clients) > self.max_clients:
            oldest = self._clients.pop(0)
            self._evicted += 1
            with contextlib.suppress(Exception):
                await oldest.close(code=1001, message=b"evicted by a newer client")

    def _state_frame(self) -> dict[str, Any]:
        return {
            "m": 1,
            "p": "Test Profile",
            "puid": "test",
            "cp": True,
            "cd": True,
            "sys": {"s": "ready", "m": "", "c": 0},
            "bc": False,
            "sbat": None,
        }

    async def _handle_request(self, socket: web.WebSocketResponse, raw: str) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        tp = message.get("tp", "")
        rid = message.get("rid")
        self.ws_requests.append(str(tp))
        if tp in self.hang_requests:
            return

        if tp == "req:ota-settings":
            # Broadcast, with no rid echoed — the real quirk this fake exists
            # to reproduce (`WebUIPlugin.cpp:613`).
            await self.emit_ota_settings()
            return

        if self.ota_in_progress and (tp.startswith(("req:profiles:", "req:history"))):
            await self._reply(socket, tp, rid, error="Update in progress")
            return

        if tp == "req:profiles:list":
            minimal = bool(message.get("minimal"))
            profiles = (
                [{"id": p.get("id"), "label": p.get("label")} for p in self.profiles]
                if minimal
                else [self._serialise(p) for p in self.profiles]
            )
            await self._reply(socket, tp, rid, profiles=profiles)
        elif tp == "req:profiles:load":
            wanted = message.get("id")
            found = next((p for p in self.profiles if p.get("id") == wanted), None)
            if found is None:
                await self._reply(socket, tp, rid, error="Profile not found")
            else:
                await self._reply(socket, tp, rid, profile=self._serialise(found))
        elif tp == "req:profiles:save":
            await self._save_profile(socket, tp, rid, message)
        elif tp == "req:profiles:delete":
            await self._delete_profile(socket, tp, rid, str(message.get("id", "")))
        elif tp == "req:profiles:select":
            self.selected_profile_id = str(message.get("id", ""))
            await self._reply(socket, tp, rid)
        elif tp == "req:profiles:favorite":
            self.favorite_profile_ids.add(str(message.get("id", "")))
            await self._reply(socket, tp, rid)
        elif tp == "req:profiles:unfavorite":
            self.favorite_profile_ids.discard(str(message.get("id", "")))
            await self._reply(socket, tp, rid)
        elif tp == "req:history:notes:get":
            wanted = str(message.get("id", ""))
            shot = next((s for s in self.shots.values() if pad6(s.entry.id) == wanted), None)
            await self._reply(socket, tp, rid, notes=(shot.notes if shot else {}) or {})
        elif tp == "req:history:notes:save":
            await self._save_notes(socket, tp, rid, message)
        elif tp == "req:history:delete":
            await self._delete_shot(socket, tp, rid, str(message.get("id", "")))
        else:
            # Fire-and-forget commands get no answer at all on the real device,
            # and so do the writes this client is not allowed to make.
            return

    # ── profile writes ───────────────────────────────────────────────

    def _serialise(self, profile: dict[str, Any]) -> dict[str, Any]:
        """One stored profile, as the firmware would put it on the wire."""
        profile_id = str(profile.get("id", ""))
        return write_profile(
            profile,
            favorite=profile_id in self.favorite_profile_ids,
            selected=profile_id == self.selected_profile_id,
        )

    async def _save_profile(
        self, socket: web.WebSocketResponse, tp: str, rid: Any, message: dict[str, Any]
    ) -> None:
        """`ProfileManager::saveProfile`, quirks included.

        Three of them, and all three are load-bearing for the push flow:

        * an **absent or empty id gets a generated one**, which is the only way
          a caller learns the id of what it just wrote;
        * a save carrying an **existing id overwrites** that file — the upsert
          this client refuses to perform, reproduced here so a test can prove
          it never reaches the wire;
        * a **new** profile is auto-favourited (`ProfileManager.cpp:186-188`),
          so a push puts an unreviewed draft on the machine's home screen unless
          somebody unstars it.
        """
        incoming = message.get("profile")
        if not isinstance(incoming, dict):
            await self._reply(socket, tp, rid, error="Save failed")
            return
        stored = dict(incoming)
        profile_id = str(stored.get("id") or "")
        is_new = not profile_id
        if is_new:
            profile_id = generate_short_id()
        stored["id"] = profile_id
        if self.mutate_on_save is not None:
            stored = self.mutate_on_save(stored)
            stored["id"] = profile_id
        existing = next((p for p in self.profiles if p.get("id") == profile_id), None)
        if existing is None:
            self.profiles.append(stored)
        else:
            self.profiles[self.profiles.index(existing)] = stored
        if is_new:
            self.favorite_profile_ids.add(profile_id)
        await self._reply(socket, tp, rid, profile=self._serialise(stored))

    async def _delete_profile(
        self, socket: web.WebSocketResponse, tp: str, rid: Any, profile_id: str
    ) -> None:
        found = next((p for p in self.profiles if p.get("id") == profile_id), None)
        if found is None:
            await self._reply(socket, tp, rid, error="Delete failed")
            return
        self.profiles.remove(found)
        # Deleting removes it from favourites and clears the startup profile if
        # it matched (`ProfileManager::deleteProfile`).
        self.favorite_profile_ids.discard(profile_id)
        if self.selected_profile_id == profile_id:
            self.selected_profile_id = None
        await self._reply(socket, tp, rid)

    # ── history writes ───────────────────────────────────────────────

    async def _delete_shot(
        self, socket: web.WebSocketResponse, tp: str, rid: Any, shot_id: str
    ) -> None:
        """`ShotHistoryPlugin::handleRequest` for `req:history:delete` (`:528-540`).

        The firmware removes `/h/<id>.slog` and `/h/<id>.json` and **flags the
        index entry deleted** — it never removes the row, which is why the
        entry stays visible to a client for ever and why the sync engine treats
        the flag rather than the absence as the signal. That asymmetry is
        reproduced exactly, because a fake that dropped the row would let a
        reconcile bug pass.

        An id it does not know gets `error: "Delete failed"`, the same shape a
        failed `req:profiles:delete` gets.
        """
        shot = next((s for s in self.shots.values() if pad6(s.entry.id) == shot_id), None)
        if shot is None:
            await self._reply(socket, tp, rid, error="Delete failed")
            return
        shot.entry.flags |= SHOT_FLAG_DELETED
        shot.entry.flags &= ~SHOT_FLAG_HAS_NOTES
        shot.notes = None
        # The files are gone; the index row is not. `missing_files` is exactly
        # how this fake spells "the entry is there and the file is not".
        self.missing_files.add(shot.entry.id)
        await self._reply(socket, tp, rid, msg="Ok")

    async def _save_notes(
        self, socket: web.WebSocketResponse, tp: str, rid: Any, message: dict[str, Any]
    ) -> None:
        """`req:history:notes:save` (`ShotHistoryPlugin.cpp:546-567`), quirks included.

        Four behaviours, and every one of them has cost somebody an evening
        somewhere:

        * the id is used **verbatim as the filename**, so an unpadded id writes
          a file nothing will ever read back — here it simply matches nothing;
        * the document is stored **verbatim**, so an extra key another client
          wrote survives and a key we omit is *gone*, not defaulted;
        * the index entry's `rating` is overwritten from `notes.rating`, and
          `HAS_NOTES` is set when it is above zero;
        * `volume` is overwritten from `doseOut` **only when it is a non-empty
          string** that parses above zero (`notes["doseOut"].is<String>()`). A
          JSON number is stored in the file and ignored by the index, which is
          the quirk the notes write-back exists to get right.
        """
        wanted = str(message.get("id", ""))
        notes = message.get("notes")
        shot = next((s for s in self.shots.values() if pad6(s.entry.id) == wanted), None)
        if shot is None or not isinstance(notes, dict):
            await self._reply(socket, tp, rid, error="Save failed")
            return
        shot.notes = dict(notes)

        rating = notes.get("rating")
        if isinstance(rating, int) and not isinstance(rating, bool):
            shot.entry.rating = rating
            if rating > 0:
                shot.entry.flags |= SHOT_FLAG_HAS_NOTES
        elif notes:
            shot.entry.flags |= SHOT_FLAG_HAS_NOTES

        dose_out = notes.get("doseOut")
        if isinstance(dose_out, str) and dose_out:
            with contextlib.suppress(ValueError):
                volume = float(dose_out)
                if volume > 0:
                    shot.entry.volume_g = volume
        await self._reply(socket, tp, rid, msg="Ok")

    async def _reply(
        self, socket: web.WebSocketResponse, tp: str, rid: Any, **payload: Any
    ) -> None:
        frame: dict[str, Any] = {"tp": tp.replace("req:", "res:", 1), **payload}
        if rid is not None:
            frame["rid"] = rid
        with contextlib.suppress(Exception):
            await socket.send_str(json.dumps(frame))


# ── fixtures ─────────────────────────────────────────────────────────


def header_only_bytes(slog_bytes: bytes) -> bytes:
    """The first ``headerSize`` bytes of a file, with `sampleCount` zeroed.

    What the device serves while a shot is being written: the header exists
    because it is written at open, the samples have not been flushed, and the
    count is patched in at close.
    """
    version = slog_bytes[4] if len(slog_bytes) > 4 else 7
    header_size = 128 if version <= 4 else 512
    head = bytearray(slog_bytes[:header_size])
    if len(head) >= 20:
        head[16:20] = (0).to_bytes(4, "little")
    return bytes(head)


def synthetic_slog_bytes(
    samples: int = 40,
    *,
    shot_id: int = 1,
    start_epoch: int = 1_700_000_000,
    profile_name: str = "Test Profile",
) -> bytes:
    """A small but real v7 `.slog`, for when no fixture directory is present.

    Deliberately built through :func:`encode_slog` rather than checked in as
    bytes, so it stays valid if the codec's understanding of the format ever
    changes.
    """
    rows: list[Sample] = []
    for i in range(samples):
        t = i * 250
        rows.append(
            Sample(
                t=t,
                tt=93.0,
                ct=92.5 + (i % 5) * 0.1,
                tp=9.0,
                cp=min(9.0, 0.5 + i * 0.3),
                fl=2.2,
                tf=2.0,
                pf=1.9,
                vf=1.8,
                v=i * 0.9,
                ev=i * 0.9,
                pr=4.5,
                si=0x000B,
                wp=i * 0.8,
            )
        )
    transitions = [
        PhaseTransition(sample_index=0, phase_number=0, phase_name="Preinfusion"),
        PhaseTransition(sample_index=8, phase_number=1, transition_reason=5, phase_name="Brew"),
    ]
    for i, row in enumerate(rows):
        row.phase = 1 if i >= 8 else 0
        row.phase_name = "Brew" if i >= 8 else "Preinfusion"
    header = SlogHeader(
        version=7,
        sample_size=0,
        header_size=512,
        sample_interval=250,
        fields_mask=FIELDS_MASK_ALL,
        sample_count=len(rows),
        duration_ms=len(rows) * 250,
        start_epoch=start_epoch,
        profile_id="test",
        profile_name=profile_name,
        final_weight_g=36.0,
        transitions=transitions,
        final_exit_reason=1,
        brew_delay_ms=800,
    )
    return encode_slog(Slog(header=header, samples=rows, shot_id=pad6(shot_id)))


def default_notes(shot_id: int) -> dict[str, Any]:
    """A notes document in the device's own shape: numbers as strings."""
    return {
        "id": pad6(shot_id),
        "rating": 4,
        "beanType": "Test Roaster Ethiopia",
        "doseIn": "18",
        "doseOut": "36.5",
        "ratio": "2.03",
        "grindSetting": "3.2",
        "balanceTaste": "balanced",
        "notes": "fixture shot",
        "timestamp": 1_700_000_000,
    }


def find_fixtures() -> Path | None:
    """``tests/fixtures`` if this is a source checkout, else ``None``."""
    candidate = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
    return candidate if candidate.is_dir() else None


def _load_profiles(fixtures: Path | None) -> list[dict[str, Any]]:
    if fixtures is None:
        return []
    out: list[dict[str, Any]] = []
    for path in sorted((fixtures / "profiles").glob("*.json")):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(body, dict):
            body.setdefault("id", path.stem[:31])
            out.append(body)
    return out


def _load_slogs(fixtures: Path | None) -> Iterable[tuple[int, bytes, str]]:
    """``(id, bytes, label)`` for every `.slog` fixture, ids taken from the name."""
    if fixtures is None:
        return []
    out: list[tuple[int, bytes, str]] = []
    for path in sorted((fixtures / "slog").glob("*.slog")):
        parts = path.stem.split("_")
        shot_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else len(out) + 1
        out.append((shot_id, path.read_bytes(), path.stem))
    return out


def build_fake_device(fixtures: Path | None = None) -> FakeDevice:
    """A :class:`FakeDevice` populated from the repository's fixtures.

    Falls back to one synthesised shot when the fixtures are not on disk, so
    ``python -m gaggiclanker.device.fake`` works from an installed wheel.
    """
    fixtures = fixtures if fixtures is not None else find_fixtures()
    device = FakeDevice()
    device.profiles = _load_profiles(fixtures)

    for shot_id, raw, label in _load_slogs(fixtures):
        device.add_shot(shot_id, raw, profile_name=label)

    # The maintainer's own export, re-encoded: a shot with every v7 field set,
    # which is the one that catches a codec regression.
    if fixtures is not None:
        export = fixtures / "exports" / "shot-129.json"
        if export.is_file():
            with contextlib.suppress(Exception):
                device.add_shot(
                    129,
                    _slog_from_export(json.loads(export.read_text(encoding="utf-8"))),
                    notes=default_notes(129),
                    profile_name="shot-129",
                )

    if not device.shots:
        device.add_shot(1, synthetic_slog_bytes(), notes=default_notes(1))
    return device


def _slog_from_export(export: dict[str, Any]) -> bytes:
    """Re-encode the web UI's JSON shot export back into `.slog` bytes.

    The export is the output of the firmware's *own* JS parser, so bytes built
    from it are a round trip through their reading of the format rather than
    ours — which is what makes the fake's biggest shot worth having.
    """
    transitions = [
        PhaseTransition(
            sample_index=int(t["sampleIndex"]),
            phase_number=int(t["phaseNumber"]),
            transition_reason=int(t.get("transitionReason", 0)),
            phase_name=t.get("phaseName", ""),
        )
        for t in export.get("phaseTransitions", [])
    ]
    keys = ("t", "tt", "ct", "tp", "cp", "fl", "tf", "pf", "vf", "v", "ev", "pr", "wp")
    samples: list[Sample] = []
    for row in export["samples"]:
        values: dict[str, Any] = {k: row[k] for k in keys if k in row}
        system_info = row.get("systemInfo")
        if isinstance(system_info, dict):
            values["si"] = system_info["raw"]
        elif system_info is not None:
            values["si"] = system_info
        samples.append(Sample.model_validate(values))

    volume = export.get("volume")
    header = SlogHeader(
        version=int(export["version"]),
        sample_size=0,
        header_size=512,
        sample_interval=int(export["sampleInterval"]),
        fields_mask=int(export.get("fieldsMask", FIELDS_MASK_ALL)),
        sample_count=len(samples),
        duration_ms=int(export["duration"]),
        start_epoch=int(export["timestamp"]),
        profile_id=export.get("profileId", ""),
        profile_name=export.get("profile", ""),
        final_weight_g=float(volume) if volume else None,
        transitions=transitions,
        final_exit_reason=int(export.get("finalExitReason", 0)),
        brew_delay_ms=int(export.get("brewDelay", 0)),
    )
    return encode_slog(Slog(header=header, samples=samples))


# ── the CLI ──────────────────────────────────────────────────────────


async def _development_loop(device: FakeDevice) -> None:
    """Idle telemetry, and nothing else.

    The fake used to be able to pull a shot on a timer, because a live view had
    to be developed against something. There is no live view any more — the
    machine's own web UI draws the shot that is happening now — so what a
    developer needs from this loop is a machine that looks plugged in: a socket
    that stays up and a boiler temperature that moves. Shots are added by a test
    calling :meth:`FakeDevice.run_brew`, and archived when a pull asks for them.
    """
    start = time.monotonic()
    while True:
        await asyncio.sleep(0.5)
        elapsed = time.monotonic() - start
        await device.emit_status(
            ct=round(92.0 + 1.5 * ((elapsed % 6) / 6), 3),
            tt=93.0,
            pr=0.0,
            fl=0.0,
            pw=0.0,
            hp=18.0,
            m=0,
            # Capabilities, so a UI developed against the fake gates the
            # pressure diagnostics the same way it would against a Pro board.
            cp=True,
            cd=True,
            bc=True,
            sbat=88,
            process={"a": 0},
        )


async def run_fake_device(port: int, host: str = "127.0.0.1") -> None:
    """Serve the fake until cancelled. The body of ``python -m …device.fake``."""
    device = build_fake_device()
    address = await device.start(port=port, host=host)
    logging.getLogger(__name__).warning(
        "fake device on http://%s (ws://%s/ws), %d shots, %d profiles",
        address,
        address,
        len(device.shots),
        len(device.profiles),
    )
    telemetry = asyncio.create_task(_development_loop(device))
    try:
        await asyncio.Event().wait()
    finally:
        telemetry.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await telemetry
        await device.stop()


@contextlib.asynccontextmanager
async def running_fake_device(
    fixtures: Path | None = None, port: int = 0
) -> AsyncIterator[FakeDevice]:
    """A started :class:`FakeDevice`, stopped on the way out. Used by the fixture."""
    device = build_fake_device(fixtures)
    await device.start(port=port)
    try:
        yield device
    finally:
        await device.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gaggiclanker.device.fake",
        description="Serve an in-process fake GaggiMate for development.",
    )
    parser.add_argument("--port", type=int, default=8090, help="port to bind (default 8090)")
    parser.add_argument("--host", default="127.0.0.1", help="address to bind (default loopback)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_fake_device(args.port, args.host))
    return 0


if __name__ == "__main__":  # pragma: no cover - the CLI entry point
    raise SystemExit(main())
