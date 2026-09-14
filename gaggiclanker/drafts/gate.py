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

from gaggiclanker.cleanup.eligibility import ineligible_reason
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.cleanup import CleanupRepository
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository, DeviceWriteWrite
from gaggiclanker.device.writes import DeviceWriteRefused, PendingWrite
from gaggiclanker.settings_service import SettingsService

__all__ = ["SettingsWriteGate", "refuse_unless_writes_enabled"]

log = structlog.get_logger(__name__)

#: What a refused write says. Long, because it is what a person sees in a toast
#: and the useful part is where the switch is, not that there is one.
DISABLED_MESSAGE = (
    "Writing to the machine is switched off. Turn on 'Device writes enabled' under "
    "Settings → Machine to let gaggiclanker write to the display. "
    "Nothing was sent."
)

#: What a notes write-back says when its own switch is off. `deviceWritesEnabled`
#: is the master switch and this is the per-feature one: somebody may well want
#: to push profiles without publishing their tasting notes to the kitchen.
NOTES_DISABLED_MESSAGE = (
    "Writing judgements back to the machine is switched off. Turn on 'Notes writeback "
    "enabled' under Settings → Machine. Nothing was sent."
)


async def refuse_unless_writes_enabled(
    settings: SettingsService,
    writes: DeviceWritesRepository,
    *,
    kind: Literal["shot_delete", "notes_save"],
    host: str,
) -> None:
    """Refuse a person's request to start a batch of writes while the switch is off.

    The Sync page's two batch actions check here before anything is queued, so
    pressing "Send" or "Delete" with writes off is a 403 naming the switch rather
    than a background task whose every frame the gate then refuses one by one.
    The refusal is still an attempt, and every attempt leaves a row in
    `device_writes` — one row, with no shot id, because nothing was tried against
    any shot in particular. The gate itself still checks the switch on every
    frame that follows when it is on.
    """
    if bool(await settings.get("deviceWritesEnabled")):
        return
    await writes.record(
        DeviceWriteWrite(kind=kind, host=host, result="refused", error=DISABLED_MESSAGE)
    )
    log.info("device_write", kind=kind, host=host, device_id=None, result="refused")
    raise DeviceWriteRefused(DISABLED_MESSAGE)


class SettingsWriteGate:
    """Authorise device writes against the setting, and record every attempt.

    Held by the app's :class:`~gaggiclanker.device.client.GaggimateClient`, and
    built in the lifespan once the database and the settings service exist.
    """

    def __init__(
        self,
        settings: SettingsService,
        writes: DeviceWritesRepository,
        *,
        db: Database | None = None,
    ) -> None:
        self.settings = settings
        self.writes = writes
        # The shot-delete branch needs to look a shot up in the archive, which
        # the profile branches never did. Optional so a gate can still be built
        # from a settings service and an audit alone (several tests do): without
        # a database there is nothing to prove a shot is safely archived with,
        # so `shot_delete` is refused outright rather than allowed by default.
        self.cleanup = CleanupRepository(db) if db is not None else None

    async def enabled(self) -> bool:
        return bool(await self.settings.get("deviceWritesEnabled"))

    async def authorize(self, write: PendingWrite) -> None:
        """Refuse unless writes are on, then apply the rule for this kind.

        The per-kind branches are here rather than in the routes and services
        that call the client, and that is the point of the whole arrangement: a
        rule enforced at the edge is a rule the next caller gets to skip, and
        the next caller is a background task nobody is watching.
        """
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
        elif write.kind == "shot_delete":
            await self._authorize_shot_delete(write)
        elif write.kind == "notes_save":
            if not await self.settings.get("notesWritebackEnabled"):
                raise DeviceWriteRefused(NOTES_DISABLED_MESSAGE)

    async def _authorize_shot_delete(self, write: PendingWrite) -> None:
        """Refuse unless the archive already holds this shot, intact and readable.

        `req:history:delete` removes the `.slog`, the notes file and the index
        entry, and there is no undo. Every condition is in
        :func:`~gaggiclanker.cleanup.eligibility.ineligible_reason`, which the
        plan step runs too so that the preview a person approves is the same set
        this will allow. The refusal carries the reason into the audit row,
        because "why is this shot still on my machine" is the question the
        Device page is asked.
        """
        device_id = write.device_id or ""
        if self.cleanup is None:
            raise DeviceWriteRefused(
                f"Shot {device_id} was not deleted: this client has no archive to check it "
                "against, and a shot is only ever deleted from a machine once this box "
                "holds its bytes."
            )
        candidate = await self.cleanup.candidate(device_id)
        reason = ineligible_reason(candidate, device_id=device_id)
        if reason is not None:
            raise DeviceWriteRefused(reason)

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
