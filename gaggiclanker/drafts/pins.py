"""Pinning a Set version to the machine, and unpinning it. A person's actions only.

A pin keeps one Set version on the machine as a profile of its own while the Set goes on
changing. It is made from the pinned version's profile by the one copy function
(:class:`~gaggiclanker.drafts.copies.ProfileCopier`), so the safety layers and the name rule apply
unchanged, and it is the row of an ordinary profile-list entry plus a record
(:mod:`gaggiclanker.db.repos.pins`) saying which Set version it belongs to. Nothing here writes to
the machine: the next sync with Writes on pushes the row, and removes it again after an unpin,
through the machinery every row goes through.

**One function asks every question, the preview and the pin alike** (:meth:`PinService.inspect`),
so what the dialog says the pin will do is what the pin does. No chat tool, MCP client or
background task is handed this class: it is built over the board service, which only the routes
receive.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.lineage import person_taken_sentence, pinned_copy_sentence
from gaggiclanker.db.repos.pins import PinDetail, PinRow, PinsRepository, PinWrite
from gaggiclanker.db.repos.profile_board import BoardRow, BoardRowPatch
from gaggiclanker.db.repos.sets import SetRow, SetsRepository, SetVersionRow
from gaggiclanker.domain.profile_policy import PolicyChange
from gaggiclanker.drafts.board import BoardService
from gaggiclanker.drafts.copies import CopyBeyondPolicy, PreparedCopy, ProfileCopier
from gaggiclanker.drafts.proposals import TakenName
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable

__all__ = [
    "PinAnswer",
    "PinPreview",
    "PinRefused",
    "PinService",
]


class PinRefused(Conflict):
    """A pin the request cannot make, with the one code and sentence the dialog shows."""

    def __init__(self, code: str, message: str, *, details: object = None) -> None:
        super().__init__(message, code=code, details=details)


def _already_pinned(version_label: str) -> PinRefused:
    """The one refusal for a version that has a live pin, said by the preview and the pin alike."""
    return PinRefused("ALREADY_PINNED", f"{version_label} is already pinned to the machine.")


class PinPreview(BaseModel):
    """`GET .../pin-preview`: what a pin of this version would do, without doing it."""

    model_config = ConfigDict(extra="forbid")

    #: The name the copy would be stored under: the label given, stripped, else
    #: "<the version's profile> <the version's name>".
    name: str
    #: Whether the pin is refused because that name is taken (a live profile has it, or it is the
    #: copy of another Set version's pin).
    taken: bool = False
    #: Why the pin would be refused, in the sentence the pin itself answers with; ``None`` when it
    #: would go through. Set for every refusal the pin has, not only a taken name.
    refused: str | None = None
    #: The numbers the safety policy would move in the copy (the reason of a policy refusal).
    clamp_changes: list[PolicyChange] = []


class PinAnswer(BaseModel):
    """`POST .../pin` and `POST /api/pins/{id}/unpin`: the pin as it now stands, and the id of the
    profile-list row that carries its copy (tombstoned after an unpin)."""

    model_config = ConfigDict(extra="forbid")

    pin: PinDetail
    board_id: int


@dataclass(slots=True)
class _Inspection:
    """What one look at a version found: the name, and the refusal if there is one."""

    set_row: SetRow
    version: SetVersionRow
    name: str
    copy: PreparedCopy | None = None
    refusal: PinRefused | None = None
    taken: bool = False
    clamp_changes: list[PolicyChange] | None = None


class PinService:
    """Pin, preview and unpin. Built over the board service the routes hold."""

    def __init__(self, db: Database, board: BoardService) -> None:
        self.db = db
        self.board = board
        self.pins = PinsRepository(db)
        self.sets = SetsRepository(db)
        self.copier = ProfileCopier(board)

    # ── the one look ─────────────────────────────────────────────────

    async def inspect(self, set_id: int, version_id: int, label: str | None) -> _Inspection:
        """Every question a pin asks, in the order it asks them. Reads only.

        Raises for what is not a pin's state at all (404: no such Set or version, or the version
        is not the Set's; 422: a blank label); returns a refusal as a value, so the preview can
        say it and the pin can raise it.
        """
        set_row = await self.sets.get(set_id)
        if set_row is None:
            raise NotFound(f"No Set {set_id}")
        version = await self.sets.version_of_set(set_id, version_id)
        if version is None:
            raise NotFound(f"Set {set_id} has no version {version_id}")
        if label is not None and not label.strip():
            raise Unprocessable("A profile needs a name")
        name = (
            label.strip()
            if label is not None
            else (
                f"{version.profile_label} {version.version_label}"
                if version.profile_label
                else version.version_label
            )
        )
        found = _Inspection(set_row=set_row, version=version, name=name)
        if set_row.archived:
            found.refusal = PinRefused(
                "SET_ARCHIVED", "This Set is archived: an archived Set receives no shots."
            )
        elif set_row.designing:
            found.refusal = PinRefused(
                "DESIGNING", "This Set is still being designed: there is no version to pin yet."
            )
        elif version.profile_version_id is None:
            found.refusal = PinRefused(
                "VERSION_HAS_NO_PROFILE",
                f"{version.version_label} names no profile, so there is nothing to put on the "
                "machine.",
            )
        elif await self.pins.live_for_version(version.id) is not None:
            found.refusal = _already_pinned(version.version_label)
        if found.refusal is not None:
            return found
        assert version.profile_version_id is not None
        try:
            found.copy = await self.copier.prepare(version.profile_version_id, name, for_pin=True)
        except TakenName as taken:
            found.taken = True
            found.refusal = PinRefused(
                "PIN_NAME_TAKEN",
                person_taken_sentence(taken.existing),
                details={"name": taken.label},
            )
        except CopyBeyondPolicy as refused:
            found.clamp_changes = list(refused.changes)
            found.refusal = PinRefused(
                "PIN_POLICY_REFUSED",
                refused.sentence,
                details={"clamp_changes": [c.model_dump(mode="json") for c in refused.changes]},
            )
        else:
            found.name = found.copy.name
            owner = await self._owner_of(found.copy)
            stored = await self.board.profiles.get_version_by_hash(found.copy.content_hash)
            brewed_by = None if stored is None else await self.pins.set_version_naming(stored.id)
            held = (
                None
                if owner is None or owner.set_version_id == version.id
                else await self.pins.detail(owner.id)
            )
            if held is not None:
                # A copy names one Set version for ever: a name freed by an unpin cannot be
                # reused, with the same content, by another version's pin.
                sentence = pinned_copy_sentence(found.name, held.set_name, held.version_label)
            elif brewed_by is not None:
                # A profile some Set version already brews is not a copy to pin.
                sentence = (
                    f"{brewed_by[0]} {brewed_by[1]} brews a profile called {found.name}. "
                    "Choose another name."
                )
            else:
                sentence = ""
            if sentence:
                found.copy = None
                found.taken = True
                found.refusal = PinRefused("PIN_NAME_TAKEN", sentence, details={"name": found.name})
        return found

    async def _owner_of(self, copy: PreparedCopy) -> PinRow | None:
        """The pin record, live or not, whose copy has exactly this content."""
        stored = await self.board.profiles.get_version_by_hash(copy.content_hash)
        return None if stored is None else await self.pins.by_copy(stored.id)

    async def preview(self, set_id: int, version_id: int, label: str | None) -> PinPreview:
        found = await self.inspect(set_id, version_id, label)
        return PinPreview(
            name=found.name,
            taken=found.taken,
            refused=None if found.refusal is None else found.refusal.message,
            clamp_changes=found.clamp_changes or [],
        )

    # ── pin ──────────────────────────────────────────────────────────

    async def pin(self, set_id: int, version_id: int, label: str | None) -> PinAnswer:
        """Pin a Set version: the copy and the record in one transaction.

        The same version pinned again under the same name brings its own unpinned record and its
        row back (the same name and content are the same profile version), instead of a second
        of either. Two pins at once make one: the second finds the first (the lock the
        transaction takes, and the unique index behind it).
        """
        version_label = "That version"
        try:
            async with self.db.transaction():
                found = await self.inspect(set_id, version_id, label)
                version_label = found.version.version_label
                if found.refusal is not None:
                    raise found.refusal
                assert found.copy is not None
                record = await self._owner_of(found.copy)
                if record is not None:
                    pin, row = await self._bring_back(record)
                else:
                    row = await self.copier.put(found.copy, carry_signature=False)
                    pin = await self.pins.insert(
                        PinWrite(
                            set_version_id=found.version.id,
                            board_id=row.id,
                            profile_version_id=row.current_version_id,
                        )
                    )
                detail = await self.pins.detail(pin.id)
        except sqlite3.IntegrityError:
            raise _already_pinned(version_label) from None
        assert detail is not None
        return PinAnswer(pin=detail, board_id=row.id)

    async def _bring_back(self, record: PinRow) -> tuple[PinRow, BoardRow]:
        """Pin a version again: its own record is live again and its row is back on the list."""
        row = await self.board.board.get(record.board_id)
        if row is None:  # pragma: no cover - a foreign key guarantees it
            raise NotFound(f"No profile row {record.board_id}")
        patch = BoardRowPatch(deleted_at=None, on_machine=True, failed_version_id=None)
        if (
            row.device_profile_id is not None
            and await self.board.board.other_live_on_device(row.device_profile_id, excluding=row.id)
            is not None
        ):
            # The file it stood on is another profile's now (the sync has not removed it yet).
            patch = BoardRowPatch(
                deleted_at=None,
                on_machine=True,
                failed_version_id=None,
                device_profile_id=None,
                device_version_id=None,
            )
        revived_row = await self.board.board.update(row.id, patch)
        assert revived_row is not None
        return await self.pins.revive(record.id), revived_row

    # ── unpin ────────────────────────────────────────────────────────

    async def unpin(self, pin_id: int) -> PinAnswer:
        """Take a pin off the machine at the next sync: stamp the record, tombstone the row."""
        async with self.db.transaction():
            pin = await self.pins.get(pin_id)
            if pin is None:
                raise NotFound(f"No pin {pin_id}")
            if pin.unpinned_at is not None:
                raise PinRefused("NOT_PINNED", "That pin is already unpinned.")
            await self.pins.unpin(pin_id)
            detail = await self.pins.detail(pin_id)
        assert detail is not None
        return PinAnswer(pin=detail, board_id=pin.board_id)
