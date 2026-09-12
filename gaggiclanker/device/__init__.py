"""The device layer: one client for one machine, and a fake to develop against.

:class:`~gaggiclanker.device.client.GaggimateClient` owns the single WebSocket
and every HTTP fetch; :mod:`gaggiclanker.device.fake` is an in-process GaggiMate
that reproduces the firmware's quirks so the whole thing can be tested offline.
Nothing here writes to the machine — see the client's module docstring.
"""

from __future__ import annotations

from gaggiclanker.device.client import READ_ONLY_METHODS, GaggimateClient, SlogFetch
from gaggiclanker.device.errors import (
    DeviceBusy,
    DeviceError,
    DeviceProtocolError,
    DeviceTimeout,
    DeviceUnavailable,
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

__all__ = [
    "READ_ONLY_METHODS",
    "Connected",
    "DeviceBusy",
    "DeviceError",
    "DeviceEvent",
    "DeviceEventBus",
    "DeviceProtocolError",
    "DeviceTimeout",
    "DeviceUnavailable",
    "Disconnected",
    "GaggimateClient",
    "HistoryRebuildProgress",
    "IdentityChanged",
    "OtaProgress",
    "ShotFinishedStats",
    "ShotSaved",
    "SlogFetch",
    "StatusChanged",
    "merge_status",
]
