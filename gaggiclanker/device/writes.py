"""The gate every device write passes through, and the refusal it raises.

`GaggimateClient` gained five write methods with profile push. It did not gain the
right to decide whether a write is allowed: that depends on a setting and on an
audit table, both of which live in the database, and the device layer sits
*below* the database layer in this codebase's import graph (`sync/engine.py`
imports both; neither imports the other).

So the client holds a :class:`DeviceWriteGate` — a two-method protocol — and the
concrete implementation is wired in at startup
(:class:`gaggiclanker.drafts.gate.SettingsWriteGate`). The default is
:class:`DenyAllWrites`, which means a `GaggimateClient` constructed with no gate
at all — in a test, in a script, in a future caller nobody has written yet —
cannot write to a machine. The safe configuration is the one you get by
forgetting.

Every attempt is recorded, refusals included. An audit that only holds the
writes that worked cannot answer "did anything try to write while this was
switched off", which is the question a person asks it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from gaggiclanker.device.errors import DeviceError

__all__ = [
    "DenyAllWrites",
    "DeviceWriteGate",
    "DeviceWriteRefused",
    "PendingWrite",
    "WriteKind",
    "payload_hash",
]

#: The seven things this box may ever ask a machine to change, and no more.
#:
#: Five are `req:profiles:*` frames. Two are `req:history:*` frames
#: and each was a deliberate widening of this list rather than a refactor:
#:
#: * ``shot_delete`` is `req:history:delete`, and it is
#:   **unrecoverable** — which is why the gate refuses it for any shot whose
#:   bytes are not already in the archive, intact and unquarantined. The
#:   firmware performs exactly the same deletion itself when free space drops
#:   below 500 KB, so the machine is losing these shots either way; the only
#:   question is whether the archive has them first.
#: * ``notes_save`` is `req:history:notes:save`, which overwrites the
#:   machine's own notes card for one shot and, as a side effect, the index's
#:   rating and volume.
#:
#: Still absent, and still a design decision to add: `req:profiles:reorder` (it
#: rewrites the display's whole ordering for a cosmetic gain),
#: `req:history:rebuild` (it regenerates `index.bin` for every shot at once),
#: and `POST /api/settings` (it clears every boolean key the body omits, and it
#: can change WiFi and PID).
type WriteKind = Literal[
    "profile_save",
    "profile_delete",
    "profile_select",
    "profile_favorite",
    "profile_unfavorite",
    "shot_delete",
    "notes_save",
]


class DeviceWriteRefused(DeviceError):
    """A write the gate would not authorise. Never reached the wire.

    Deliberately **not** retryable: the machine has nothing to do with it. A
    sync loop that branched on ``retryable`` and got ``True`` here would hammer
    a switch that is off.
    """

    code = "DEVICE_WRITE_REFUSED"
    retryable = False

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, status=403, **kwargs)


@dataclass(frozen=True, slots=True)
class PendingWrite:
    """One attempted write, as the audit records it.

    ``device_id`` is the profile id on the machine and is ``None`` for a save:
    the firmware assigns it, so at the moment of asking there is nothing to
    name. ``payload_hash`` is the canonical JSON's sha256 for a save and the
    target id's for everything else, which is what lets "we sent this" be
    compared with "it served this back".
    """

    kind: WriteKind
    host: str
    device_id: str | None = None
    payload_hash: str = ""


@runtime_checkable
class DeviceWriteGate(Protocol):
    """Authorise a write, then record what happened to it.

    Two methods rather than one wrapper, because the client has to record a
    *failed* write as well as a refused one, and the thing that fails is the
    frame the client owns.
    """

    async def authorize(self, write: PendingWrite) -> None:
        """Raise :class:`DeviceWriteRefused` unless this write may proceed."""
        ...

    async def record(
        self, write: PendingWrite, *, result: Literal["ok", "refused", "failed"], error: str = ""
    ) -> None:
        """Append the audit row. Must never raise: it is bookkeeping."""
        ...


class DenyAllWrites:
    """The default gate: nothing is authorised and nothing is recorded.

    This is what a `GaggimateClient` built without a gate gets, which makes
    "read-only" the behaviour you get from *omission* rather than from
    configuration. The refusal names the alternative so the message is useful
    where it lands — in a log line in a script, or in an API error.
    """

    async def authorize(self, write: PendingWrite) -> None:
        raise DeviceWriteRefused(
            f"This client cannot write to the machine ({write.kind} was not sent). "
            "Device writes need the app's write gate and the deviceWritesEnabled setting."
        )

    async def record(
        self, write: PendingWrite, *, result: Literal["ok", "refused", "failed"], error: str = ""
    ) -> None:
        # No database here, and no place to put a row. The refusal above is the
        # record; a client with no gate is not part of an installation.
        return None


def payload_hash(text: str) -> str:
    """sha256 of whatever identifies this write, hex-encoded."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
