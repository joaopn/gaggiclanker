"""The typed events the device client publishes, and the merge that feeds them.

Everything the machine tells us unprompted arrives here: connection state, the
merged live status, "a shot was saved", the identity frame. One bus, several
consumers — the sync engine, the SSE endpoints, the log.

**The bus is lossy on purpose** (see :class:`~gaggiclanker.infra.sse.EventBus`):
a slow subscriber drops its oldest event rather than stalling the socket
reader. At 2 Hz telemetry a dropped frame is invisible, but it means a consumer
must treat :class:`ShotSaved` as *"go and look"* and never as the only record
that a shot exists. That is why the sync engine keeps polling the index behind this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gaggiclanker.domain.models import LiveStatus, OtaSettings
from gaggiclanker.infra.sse import EventBus

__all__ = [
    "Connected",
    "DeviceEvent",
    "DeviceEventBus",
    "Disconnected",
    "HistoryRebuildProgress",
    "IdentityChanged",
    "OtaProgress",
    "ShotFinishedStats",
    "ShotSaved",
    "StatusChanged",
    "merge_status",
]


@dataclass(frozen=True, slots=True)
class Connected:
    """The WebSocket came up. Published once per successful connection."""

    host: str


@dataclass(frozen=True, slots=True)
class Disconnected:
    """The WebSocket went down. ``reason`` is for the log, not for logic."""

    reason: str


@dataclass(frozen=True, slots=True)
class StatusChanged:
    """A fresh `evt:status` frame, already merged onto the previous state.

    Subscribers get the *whole* picture, never the partial frame: merging in
    one place is what stops three consumers from each keeping their own
    half-correct copy.
    """

    status: LiveStatus


@dataclass(frozen=True, slots=True)
class ShotSaved:
    """`evt:history-shot-saved` — the canonical "new shot available" push.

    ``shot_id`` is the **unpadded** integer the firmware sends. The URL that
    fetches it needs the 6-digit padded form; :func:`gaggiclanker.domain.pad6`
    is the only place that conversion happens.
    """

    shot_id: int


@dataclass(frozen=True, slots=True)
class ShotFinishedStats:
    """`evt:shot-finished-stats`, broadcast the instant the brew ends.

    It arrives *before* the `.slog` is closed, so it is not a signal to fetch
    anything — it is what lets the UI show a result while the file is still
    being written. :class:`ShotSaved` is the fetch signal.
    """

    max_pressure: float | None
    avg_flow: float | None


@dataclass(frozen=True, slots=True)
class IdentityChanged:
    """`res:ota-settings` — firmware versions and the controller board name.

    Published on every connect (we ask for it) and whenever the machine
    broadcasts one of its own, which it does after each half-hourly update
    check.
    """

    identity: OtaSettings


@dataclass(frozen=True, slots=True)
class OtaProgress:
    """`evt:ota-progress`. Phase 0 idle, 1 display, 3 controller, 4 done, 5 failed."""

    phase: int
    progress: int


@dataclass(frozen=True, slots=True)
class HistoryRebuildProgress:
    """`evt:history-rebuild-progress` while the device re-scans `/h/`."""

    total: int
    current: int
    status: str


#: Every event the client can publish. A union rather than a base class so a
#: consumer's ``match`` statement is exhaustive and mypy says so.
type DeviceEvent = (
    Connected
    | Disconnected
    | StatusChanged
    | ShotSaved
    | ShotFinishedStats
    | IdentityChanged
    | OtaProgress
    | HistoryRebuildProgress
)

type DeviceEventBus = EventBus[DeviceEvent]


# ── evt:status merge ─────────────────────────────────────────────────


def merge_status(previous: LiveStatus | None, frame: dict[str, Any]) -> LiveStatus:
    """Merge one `evt:status` frame onto the last known status.

    Two different frames share the `evt:status` type — fast telemetry every
    500 ms and slow state on change — and each carries only its own half. The
    firmware documents the rule and its own UI implements it:

    * a key **absent** from the frame keeps its previous value,
    * a key sent as **null** clears it.

    The second half is the one that matters and the one that is easy to get
    wrong: `"process": null` is how the machine says "the finished shot has
    been cleared", and a merge that skipped nulls would leave a shot on screen
    for ever.

    ``tp`` is dropped — it is the envelope, not a status field — and anything
    else the firmware has grown since is carried through unvalidated, because
    :class:`LiveStatus` is open and losing the whole frame over one new
    telemetry key is not a trade worth making at 2 Hz.
    """
    merged: dict[str, Any] = {} if previous is None else previous.model_dump(by_alias=True)
    for key, value in frame.items():
        if key == "tp":
            continue
        merged[key] = value
    # Re-validating the whole merged object each time (rather than mutating the
    # model in place) is what keeps `process` a ProcessStatus after a frame has
    # replaced it with a raw dict.
    return LiveStatus.model_validate(merged)
