"""The real write gate: one setting, one audit table, two rules.

:class:`~gaggiclanker.device.writes.DenyAllWrites` is what a client gets by
default. This is what it gets in an installation, and everything it knows that
the device layer does not is why it lives here rather than there: the
`deviceWritesEnabled` setting is resolved through
:class:`~gaggiclanker.settings_service.SettingsService`, and the provenance
check reads `device_writes`.

The setting is re-read on **every** write rather than cached at startup, for the
same reason the auth guard re-reads its own switch: the person turning it off is
usually the person who has just seen something they did not like, and "restart
the container" is not an acceptable answer to that.
"""

from __future__ import annotations

from typing import Literal

import structlog

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository, DeviceWriteWrite
from gaggiclanker.device.writes import DeviceWriteRefused, PendingWrite
from gaggiclanker.settings_service import SettingsService

__all__ = ["SettingsWriteGate"]

log = structlog.get_logger(__name__)

#: What a refused write says. Long, because it is what a person sees in a toast
#: and the useful part is where the switch is, not that there is one.
DISABLED_MESSAGE = (
    "Writing to the machine is switched off. Turn on 'Device writes enabled' under "
    "Settings → Machine to let gaggiclanker save profiles to the display. "
    "Nothing was sent."
)


class SettingsWriteGate:
    """Authorise device writes against the setting, and record every attempt.

    Held by the app's :class:`~gaggiclanker.device.client.GaggimateClient`, and
    built in the lifespan once the database and the settings service exist.
    """

    def __init__(self, settings: SettingsService, writes: DeviceWritesRepository) -> None:
        self.settings = settings
        self.writes = writes

    async def enabled(self) -> bool:
        return bool(await self.settings.get("deviceWritesEnabled"))

    async def authorize(self, write: PendingWrite) -> None:
        """Refuse unless writes are on — and, for a delete, unless it is ours."""
        if not await self.enabled():
            raise DeviceWriteRefused(DISABLED_MESSAGE)
        if write.kind == "profile_delete":
            device_id = write.device_id or ""
            if not await self.writes.created_by_us(device_id, host=write.host or None):
                raise DeviceWriteRefused(
                    f"Profile {device_id!r} was not created by this box — there is no successful "
                    "save for that id in the device-write audit. gaggiclanker deletes only the "
                    "profiles it wrote; delete this one from the machine's own display."
                )

    async def record(
        self, write: PendingWrite, *, result: Literal["ok", "refused", "failed"], error: str = ""
    ) -> None:
        await self.writes.record(
            DeviceWriteWrite(
                kind=write.kind,
                host=write.host,
                # For a save this is the id the firmware assigned, filled in by
                # the client before it records the result — the provenance check
                # a later delete runs is exactly this column.
                device_id=write.device_id,
                payload_hash=write.payload_hash,
                result=result,
                error=error,
            )
        )
        log.info(
            "device_write",
            kind=write.kind,
            host=write.host,
            device_id=write.device_id,
            result=result,
        )
