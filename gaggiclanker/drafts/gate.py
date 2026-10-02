"""The real write gate: one setting, one audit table, one rule.

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
#: and the useful part is where the switch is (the top bar), not that there is one.
DISABLED_MESSAGE = (
    "Writing to the machine is switched off. Turn on the Writes switch in the top bar "
    "to let gaggiclanker write to the display. Nothing was sent."
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
        """Refuse unless writes are on.

        There is no per-kind rule left: every profile the app has synced is the app's to
        manage, so who made a profile no longer decides whether it may be deleted. What
        stands between a profile and its removal is the content check the client makes
        against a fresh load (``GaggimateClient.delete_profile``).
        """
        if not await self.enabled():
            raise DeviceWriteRefused(DISABLED_MESSAGE)

    async def record(
        self, write: PendingWrite, *, result: Literal["ok", "refused", "failed"], error: str = ""
    ) -> None:
        await self.writes.record(
            DeviceWriteWrite(
                kind=write.kind,
                host=write.host,
                # For a save this is the id the firmware assigned, filled in by
                # the client before it records the result.
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
