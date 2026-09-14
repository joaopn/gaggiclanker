"""Compose a judgement into the machine's notes document, and send it.

What makes this more than a field copy is that the machine's card is **not
ours**. It is a JSON file the display's own UI writes, another client may have
written, and the firmware stores verbatim — so the document that goes out is the
document that is already there with our fields laid over it, never a fresh one
built from the judgement alone. An unknown key another client wrote survives a
write from here; a field the user left out of `notesWritebackFields` keeps
whatever the machine has in it.

Three firmware facts shape the rest (research §4):

* ``doseOut`` only overrides the index's `volume` when it arrives as a
  **non-empty string**. Every numeric field on the card is a string for that
  reason and :class:`~gaggiclanker.domain.models.ShotNotes` models them as one.
* ``notes`` is capped at 200 characters by the schema. The judgement column has
  the same cap, so truncation here is a belt to that brace — and it is a
  truncation rather than a refusal, because losing the first 200 characters of a
  tasting note to a validation error helps nobody.
* ``timestamp`` is never set by the firmware. It is set by the client on the way
  out (in :meth:`~gaggiclanker.device.client.GaggimateClient.save_shot_notes`)
  and it is the only thing that makes "is the device's copy newer than ours"
  answerable at all.

**The conflict rule.** A judgement is written back only when it is genuinely
newer than what the machine holds: `updated_at` after the device note's
`timestamp`, or the machine has no note for that shot. And a judgement that was
*seeded* from the device and never edited is never written at all — it is the
machine's own words, and echoing them back with a fresh timestamp would make the
echo win every future comparison. The read path's own half of this (the rule that
seeding is an insert that does nothing on conflict) is untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.judgements import (
    NOTES_MAX,
    JudgementsRepository,
    PendingWritebackRow,
    ShotJudgementRow,
)
from gaggiclanker.db.repos.notes import DeviceShotNotesRow, NotesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.errors import DeviceError, DeviceUnavailable
from gaggiclanker.domain.models import ShotNotes
from gaggiclanker.drafts.gate import refuse_unless_writes_enabled
from gaggiclanker.infra.errors import BadRequest, Conflict, ServiceUnavailable
from gaggiclanker.infra.sse import SseEvent, SseEventBus
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.settings import NOTES_WRITEBACK_FIELDS
from gaggiclanker.settings_service import SettingsService

__all__ = [
    "NOTES_WRITEBACK_EVENT",
    "NotesWritebackPolicy",
    "NotesWritebackService",
    "WritebackResult",
    "compose_device_notes",
    "writeback_task_name",
]

log = structlog.get_logger(__name__)

#: Published on the sync bus after a write-back, so the shot page and the Sync
#: page re-read rather than poll.
NOTES_WRITEBACK_EVENT = "notes.writeback"

#: How the archive's balance vocabulary maps onto the firmware's three-way
#: `balanceTaste`. They agree today; the map is here so that a vocabulary that
#: grows a fourth value fails loudly at one place rather than writing a string
#: the device's own UI cannot render.
BALANCE_TO_DEVICE: dict[str, str] = {
    "sour": "sour",
    "balanced": "balanced",
    "bitter": "bitter",
}


def writeback_task_name() -> str:
    """The registry name a bulk push holds. One machine, so one name."""
    return "notes-writeback"


class NotesWritebackPolicy(BaseModel):
    """The master write switch and the field list, resolved fresh on every use.

    There is no switch of this feature's own. A send is something a person
    starts on the Sync page with the judgements in front of them, and that is
    the consent; `deviceWritesEnabled` is the gate every write is behind.
    """

    model_config = ConfigDict(extra="forbid")

    writes_enabled: bool = False
    fields: list[str] = Field(default_factory=list)


class WritebackResult(BaseModel):
    """What one write-back did, or why it did nothing."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    device_id: str = ""
    written: bool = False
    #: A sentence when ``written`` is false. Rendered in a toast, so it says what
    #: would have to be different rather than naming a rule.
    reason: str | None = None
    #: True when the machine refused or failed, rather than a rule here saying
    #: no. The bulk push stops on the first of these and skips past the others:
    #: "there is no judgement on this shot" is one shot's business, "the machine
    #: disconnected" is every remaining shot's.
    device_error: bool = False


def _number(value: float | None) -> str:
    """A dose as the card stores it: a string, trimmed of a pointless `.0`.

    Empty for ``None``, which is what the device's own UI writes for "not filled
    in" — and what stops an absent dose from being sent as the string ``"None"``.
    """
    if value is None:
        return ""
    return f"{value:g}"


def compose_device_notes(
    judgement: ShotJudgementRow,
    existing: ShotNotes | None,
    *,
    device_id: str,
    fields: list[str],
) -> ShotNotes:
    """The document to send: what the machine has, with the chosen fields laid over it.

    Pure, so the composition can be tested without a machine — which matters,
    because every quirk it encodes is silent when it is wrong. Keys the machine
    carries that nothing here models are preserved untouched;
    :class:`ShotNotes` is ``extra="allow"`` for exactly this.
    """
    document: dict[str, Any] = dict(existing.to_device()) if existing is not None else {}
    document["id"] = device_id

    if "rating" in fields:
        # 0 is the card's "unrated", and it is also what clears the index's
        # rating — so a withdrawn judgement genuinely unrates the shot on the
        # machine rather than leaving a stale star count behind.
        document["rating"] = judgement.rating or 0
    if "balance" in fields and judgement.balance is not None:
        document["balanceTaste"] = BALANCE_TO_DEVICE[judgement.balance]
    if "doseIn" in fields:
        document["doseIn"] = _number(judgement.dose_in_g)
    if "doseOut" in fields:
        # A string, always. The firmware only honours `doseOut` as an override
        # for the index volume when `notes["doseOut"].is<String>()`; a float
        # here is stored and silently ignored by the index.
        document["doseOut"] = _number(judgement.dose_out_g)
    if "doseIn" in fields or "doseOut" in fields:
        ratio = judgement.ratio
        document["ratio"] = _number(ratio)
    if "grindSetting" in fields:
        document["grindSetting"] = judgement.grind_setting or ""
    if "notes" in fields:
        document["notes"] = judgement.notes[:NOTES_MAX]

    return ShotNotes.model_validate(document)


class NotesWritebackService:
    """Decides whether a judgement may go to the machine, composes it, sends it."""

    def __init__(
        self,
        db: Database,
        settings: SettingsService,
        *,
        client: GaggimateClient | None,
        bus: SseEventBus | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.client = client
        self.bus = bus
        self.judgements = JudgementsRepository(db)
        self.notes = NotesRepository(db)
        self.shots = ShotsRepository(db)
        self.writes = DeviceWritesRepository(db)

    async def policy(self) -> NotesWritebackPolicy:
        raw = str(await self.settings.get("notesWritebackFields") or "")
        chosen = [part.strip() for part in raw.split(",") if part.strip()]
        return NotesWritebackPolicy(
            writes_enabled=bool(await self.settings.get("deviceWritesEnabled")),
            fields=[name for name in chosen if name in NOTES_WRITEBACK_FIELDS],
        )

    async def pending(self, *, limit: int = 200) -> list[int]:
        """Shot ids whose verdict the machine does not have yet."""
        return await self.judgements.pending_writeback(limit=limit)

    async def pending_rows(self, *, limit: int = 200) -> list[PendingWritebackRow]:
        """The same backlog, with what a person picks a shot by."""
        return await self.judgements.pending_writeback_rows(limit=limit)

    async def approve_push(self, shot_ids: list[int] | None) -> list[int]:
        """The shots a send from the Sync page may write, in the order it writes them.

        ``None`` is "every pending judgement", which is what the page's "select
        all" amounts to; a list is exactly those shots. Every selected id has to
        be pending *now*: one that is not (sent from another tab, edited on the
        machine and re-pulled, deleted from the machine) makes the whole request
        a 409 and nothing is queued, so what is sent is what the person saw
        selected. The per-shot rules still run again inside :meth:`writeback`.

        Checks in order: a machine, the master switch (a refusal is audited), a
        connection, the selection.
        """
        if self.client is None:
            raise ServiceUnavailable(
                "No machine is configured, so there is nowhere to write notes to."
            )
        await refuse_unless_writes_enabled(
            self.settings, self.writes, kind="notes_save", host=self.client.host
        )
        if not self.client.connected:
            raise DeviceUnavailable(
                "The machine is not connected, so nothing can be sent to it now."
            )
        pending = await self.pending()
        if shot_ids is None:
            return pending
        if not shot_ids:
            raise BadRequest(
                "Select at least one judgement to send.", details={"field": "shot_ids"}
            )
        wanted = set(shot_ids)
        stale = wanted - set(pending)
        if stale:
            raise Conflict(
                "Some of the selected judgements are no longer waiting to be sent — sent "
                "already, changed on the machine, or the shot is gone from it. Nothing was "
                "sent. Review the list and send again.",
                details={"field": "shot_ids", "not_pending": len(stale)},
            )
        # The backlog's own order, oldest verdict first, whatever order the
        # page happened to send the ids in.
        return [shot_id for shot_id in pending if shot_id in wanted]

    async def writeback(self, shot_id: int, *, trigger: str = "manual") -> WritebackResult:
        """Send one shot's judgement to the machine, if every rule allows it.

        Never raises for a rule that says no: a refusal is a result with a
        reason on it, because this runs inside the background task a send from
        the Sync page queues, and an exception there is a log line nobody reads.

        A device error *does* propagate the same way — as a result, not an
        exception — because the caller that matters (the bulk push) needs to
        know whether to carry on.
        """
        policy = await self.policy()
        if not policy.writes_enabled:
            return WritebackResult(
                shot_id=shot_id,
                reason=(
                    "Writing to the machine is switched off. Turn on 'Device writes enabled' "
                    "under Settings → Machine."
                ),
            )
        if self.client is None:
            return WritebackResult(
                shot_id=shot_id, reason="No machine is configured.", device_error=True
            )

        shot = await self.shots.get(shot_id)
        if shot is None:
            return WritebackResult(shot_id=shot_id, reason=f"No shot {shot_id}.")
        if shot.deleted_on_device:
            return WritebackResult(
                shot_id=shot_id,
                device_id=shot.device_id,
                reason=(
                    f"The machine no longer holds shot {shot.device_id}, so there is no "
                    "notes card to write."
                ),
            )
        judgement = await self.judgements.get(shot_id)
        if judgement is None:
            return WritebackResult(
                shot_id=shot_id,
                device_id=shot.device_id,
                reason="There is no judgement on this shot to send.",
            )
        if judgement.seeded_from_device_note:
            return WritebackResult(
                shot_id=shot_id,
                device_id=shot.device_id,
                reason=(
                    "This verdict came from the machine's own notes card and has not been "
                    "edited since, so there is nothing to send back."
                ),
            )

        mirror = await self.notes.get(shot_id)
        stale = _device_copy_is_newer(judgement, mirror)
        if stale is not None:
            return WritebackResult(shot_id=shot_id, device_id=shot.device_id, reason=stale)

        existing = _existing_notes(mirror, shot.device_id)
        document = compose_device_notes(
            judgement, existing, device_id=shot.device_id, fields=policy.fields
        )
        try:
            sent = await self.client.save_shot_notes(shot.device_id, document)
        except DeviceError as exc:
            log.info("notes_writeback_failed", shot_id=shot_id, error=str(exc))
            return WritebackResult(
                shot_id=shot_id, device_id=shot.device_id, reason=str(exc), device_error=True
            )

        # The mirror is updated from what was sent, not from a re-read: the
        # machine stores the document verbatim, so a second frame could only
        # disagree by the machine having lied. `synced_rating`/`synced_volume_g`
        # are set to what the firmware will now put in its own index entry, so
        # the next index diff does not read our own write as somebody editing
        # the card on the machine and re-pull it — which means they have to
        # follow the firmware's own two rules exactly (`:546-567`): the rating
        # is copied across whatever it is, and the volume is overridden **only**
        # by a non-empty string `doseOut` that parses above zero. Sending an
        # empty dose leaves the index volume alone, so the mirror must keep
        # whatever figure it already had rather than claim the entry now reads
        # nothing.
        await self.notes.upsert(
            shot_id,
            sent,
            index_rating=sent.rating,
            index_volume_g=_index_volume_after(sent, mirror),
        )
        await self.judgements.mark_device_synced(shot_id)
        self._publish({"shot_id": shot_id, "device_id": shot.device_id, "trigger": trigger})
        log.info("notes_written_back", shot_id=shot_id, device_id=shot.device_id, trigger=trigger)
        return WritebackResult(shot_id=shot_id, device_id=shot.device_id, written=True)

    async def push(self, shot_ids: list[int], *, trigger: str = "manual") -> list[WritebackResult]:
        """The approved judgements, in order, stopping on the first device error.

        A rule-based refusal (nothing to send, the shot is gone) is a skip and
        the walk carries on; a device error is a stop, for the same reason the
        cleanup run stops — the machine is unhappy now, and two hundred more
        frames will not improve it.
        """
        results: list[WritebackResult] = []
        for shot_id in shot_ids:
            result = await self.writeback(shot_id, trigger=trigger)
            results.append(result)
            if result.device_error:
                break
        return results

    def spawn_push(self, tasks: TaskRegistry, shot_ids: list[int]) -> bool:
        """Queue a send of approved judgements under one name. False if one is running."""
        name = writeback_task_name()
        if tasks.get(name) is not None:
            return False
        tasks.spawn(name, self.push(shot_ids, trigger="sync-page"))
        return True

    def _publish(self, data: dict[str, Any]) -> None:
        if self.bus is not None:
            self.bus.publish(SseEvent(event=NOTES_WRITEBACK_EVENT, data=data))


def _index_volume_after(sent: ShotNotes, mirror: DeviceShotNotesRow | None) -> float | None:
    """What the machine's index entry will hold for `volume` after this write.

    The firmware overrides it only when `notes["doseOut"].is<String>()` and the
    string parses above zero (`ShotHistoryPlugin.cpp:557`). Anything else — an
    empty string, a zero, a JSON number — leaves the entry as it was, so the
    mirror keeps the figure it already had. Claiming otherwise would make the
    next index diff see a difference that is not there and re-pull the card.
    """
    dose_out = sent.parsed.dose_out
    if sent.dose_out and dose_out is not None and dose_out > 0:
        return dose_out
    return mirror.synced_volume_g if mirror is not None else None


def _existing_notes(mirror: DeviceShotNotesRow | None, device_id: str) -> ShotNotes | None:
    """The machine's current card as a model, or ``None`` when it has none.

    From the mirror's raw document rather than its typed columns, because the
    typed columns are a convenience and the raw document is the record — the
    extra keys another client wrote only exist in the latter.
    """
    if mirror is None or not isinstance(mirror.document, dict):
        return None
    document = dict(mirror.document)
    document.setdefault("id", device_id)
    return ShotNotes.model_validate(document)


def _device_copy_is_newer(
    judgement: ShotJudgementRow, mirror: DeviceShotNotesRow | None
) -> str | None:
    """The refusal when the machine's card is newer than this verdict, else ``None``.

    The comparison is `judgement.updated_at` (an ISO-8601 UTC string) against the
    device note's `timestamp` (unix seconds, and set by whichever client last
    wrote it). A card with **no** timestamp is treated as older: the firmware
    itself never writes one, so the alternative would be refusing every
    write-back onto a card the display's own UI created.
    """
    if mirror is None or mirror.device_timestamp is None:
        return None
    try:
        ours = datetime.fromisoformat(judgement.updated_at.replace("Z", "+00:00"))
    except ValueError:  # pragma: no cover - the column is written by utc_now()
        return None
    if ours.tzinfo is None:
        ours = ours.replace(tzinfo=UTC)
    if ours.timestamp() > mirror.device_timestamp:
        return None
    return (
        "The machine's own notes card for this shot is newer than this verdict, so it was "
        "left alone. Edit the judgement here to make it the newer one."
    )
