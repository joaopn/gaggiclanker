"""The app's profile board, and the write phase of a sync that makes the machine match it.

Two halves with one rule between them.

**Editing the board never touches the machine.** Putting an approved draft on the board,
turning a profile's home-screen star on or off and deleting a profile change rows in the
archive and nothing else; they work with the writes switch off, and with no machine at all.

**The write phase is the one automatic write this app makes**, and it runs only when the
`deviceWritesEnabled` switch is on, at the end of the profile pass of a sync, inside the sync
engine's lock. It does not decide anything the plan has not (:mod:`.board_plan`) and it has
no machine path of its own: every save is :func:`~gaggiclanker.drafts.machine.place`, every
removal :func:`~gaggiclanker.drafts.machine.remove_if_ours`, so the audit, the write gate and
the delete guards (an ``ok`` save of that id by this app, the app label, exactly the content
recorded, no Set still brewing it) apply as they do to a person's push.

What a sync does, in order:

1. **Adoption**, once, on the first sync with the switch on: every profile the machine holds
   becomes a board row exactly as it is (home screen = its star). Nothing is written, and the
   sync ends there.
2. **Per live row**: push the row's current version when the machine holds none (schema and
   policy ran when the draft was made; the save-then-load round trip runs here); then remove
   the file it stood on before, when that file is the app's. A round trip that does not match
   removes the copy just written (the same guarded path), keeps the previous version on the
   machine and records the failure. A profile edited on the machine is overwritten by the
   board, with an event naming it.
3. **Per deleted row**: remove its file when it is the app's, else leave it and say why.
4. **Home screen**: set each file's star to the row's flag, from a fresh read when anything was
   written above.

A sync that stops halfway leaves every profile old or new: a push is a save then a removal, and
a failure between them leaves both files on the machine, which the next sync finishes (the row
still stands on the old file, and the new one is found by its content). Failures are values
(events, the run's summary, an error count), never an exception out of the sync.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.profile_board import (
    BoardRow,
    BoardRowPatch,
    BoardRowWrite,
    ProfileBoardRepository,
)
from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow, ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository, ProfileVersionRow
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionRow, VersionRefused
from gaggiclanker.db.repos.sync import SyncRepository, SyncRunUpdate
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.domain.models import APP_PROFILE_SUFFIX, Profile
from gaggiclanker.domain.profile_policy import ProfileRejected, enforce
from gaggiclanker.drafts.board_plan import (
    ANOTHER_ROW,
    FINAL_REFUSALS,
    BoardAction,
    BoardPlan,
    Computed,
    DeletedPlan,
    PlanBuilder,
    RowPlan,
)
from gaggiclanker.drafts.machine import (
    NOT_OURS,
    MachineState,
    Placed,
    Removal,
    place,
    read_machine,
    remove_if_ours,
)
from gaggiclanker.drafts.proposals import DraftProposals, profile_from_version
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable
from gaggiclanker.settings_service import SettingsService

__all__ = [
    "AttachToSet",
    "BoardMachineState",
    "BoardRowView",
    "BoardRunSummary",
    "BoardService",
    "BoardSummaryItem",
    "BoardView",
    "machine_from_mirror",
]

log = structlog.get_logger(__name__)

#: The sync event kinds this phase writes, one per action, so the Sync page can name each.
EVENT_ADOPTED = "board_adopted"
EVENT_PUSHED = "board_pushed"
EVENT_OVERWROTE = "board_overwrote"
EVENT_REMOVED = "board_removed"
EVENT_LEFT = "board_left"
EVENT_HOME_SCREEN = "board_home_screen"
EVENT_FAILED = "board_failed"
EVENT_PAUSED = "board_paused"

#: Three failed profiles in a row mean the machine has gone, not that three profiles are bad.
MAX_CONSECUTIVE_FAILURES = 3

type AttachToSet = Callable[[ProfileDraftRow, int, bool | None], Awaitable[SetVersionRow | None]]


class _OutsideBounds(Exception):
    """A stored version the safety bounds, as they are now, would have moved."""


class BoardSummaryItem(BaseModel):
    """One line of a run's summary."""

    model_config = ConfigDict(extra="forbid")

    row_id: int | None = None
    label: str
    device_id: str | None = None
    reason: str = ""
    detail: str = ""
    on: bool | None = None
    #: A push that found an identical file already on the machine and sent no save: listed
    #: under ``pushed``, but not a write, so the notification counts it apart.
    reused: bool = False


class BoardRunSummary(BaseModel):
    """What one write phase did. Stored on the run row and shown on the Sync page."""

    model_config = ConfigDict(extra="forbid")

    #: Profiles taken onto the board by the first sync with the switch on.
    adopted: list[BoardSummaryItem] = Field(default_factory=list)
    pushed: list[BoardSummaryItem] = Field(default_factory=list)
    #: Pushed over a copy somebody had edited on the machine.
    overwritten: list[BoardSummaryItem] = Field(default_factory=list)
    removed: list[BoardSummaryItem] = Field(default_factory=list)
    #: Files the app did not write, or that are still in use, and so stay on the machine.
    left: list[BoardSummaryItem] = Field(default_factory=list)
    home_screen: list[BoardSummaryItem] = Field(default_factory=list)
    failures: list[BoardSummaryItem] = Field(default_factory=list)
    #: Saves, deletes and star changes sent to the machine in this phase.
    writes: int = 0
    #: Set when the phase wrote nothing because the machine looks reset.
    paused: str | None = None

    @property
    def changed(self) -> int:
        return len(self.adopted) + len(self.pushed) + len(self.removed) + len(self.home_screen)


class BoardMachineState(BaseModel):
    """Where one board row stands on the machine, as of the read the preview was built from."""

    model_config = ConfigDict(extra="forbid")

    #: The file the row stands on (or, failing that, one holding its current content).
    device_id: str | None = None
    #: Whether that file is on the machine.
    present: bool = False
    #: Whether it holds exactly the row's current version.
    holds_current: bool = False
    favorite: bool | None = None
    selected: bool | None = None


class BoardRowView(BaseModel):
    """A board row, its machine state and what the next sync would do about it."""

    model_config = ConfigDict(extra="forbid")

    row: BoardRow
    #: ``pro``/``standard``, from the current version, for a list that shows both kinds.
    type: str
    utility: bool = False
    machine: BoardMachineState
    planned: list[BoardAction] = Field(default_factory=list)


class BoardLanding(BaseModel):
    """Where a put of one draft would land: the row it continues, or a new profile."""

    model_config = ConfigDict(extra="forbid")

    #: The board row the draft would become the next version of; ``None`` for a new profile.
    row_id: int | None = None
    row_label: str | None = None
    #: Whether that row already holds a newer draft (current, or waiting for a sync), so a
    #: put of this one would undo it.
    holds_newer_draft: bool = False
    #: A put the board refuses because a live board profile already carries the label this
    #: draft would give a profile of its own (a new profile, or a version that renames the
    #: one it continues): that label. Two live profiles never share a label, so there is no
    #: put to offer, only a refinement of the profile that has the label, or a discard.
    taken_label: str | None = None
    #: A put that would bring a deleted profile back (its file still waits to be dealt with).
    revives_label: str | None = None


class DraftLanding(BaseModel):
    """What putting an approved draft on the board would do, with and without its Set."""

    model_config = ConfigDict(extra="forbid")

    draft_id: int
    #: The label of the live board profile that already holds this draft's exact document, if
    #: one does: there is nothing to put (the document is on the board), only to discard.
    already_on_board_label: str | None = None
    #: A put that records nothing on a Set: found through the draft's base, as the route does.
    plain: BoardLanding
    #: A put recorded on the draft's own Set: found through the Set's current version.
    #: ``None`` for a draft that belongs to no Set.
    for_set: BoardLanding | None = None


class BoardView(BaseModel):
    """The board and the next sync's plan, which is what the Profiles page and the switch show."""

    model_config = ConfigDict(extra="forbid")

    #: Whether the machine's own profiles have been taken onto the board yet.
    adopted: bool
    writes_enabled: bool
    #: ``machine`` (read just now), ``mirror`` (the archive's last copy, the machine could not
    #: be read), ``none`` (no copy either).
    machine_source: str
    rows: list[BoardRowView] = Field(default_factory=list)
    #: Deleted profiles whose file the app still has to deal with.
    pending_removals: list[BoardRow] = Field(default_factory=list)
    actions: list[BoardAction] = Field(default_factory=list)
    #: What is wrong with profiles the sync will not touch; see ``BoardPlan.reports``.
    reports: list[BoardAction] = Field(default_factory=list)
    #: Why a sync would write nothing (the machine looks reset), else ``None``. A person
    #: resumes it with ``POST /api/profile-board/resume``.
    paused: str | None = None
    #: Whether the pause has been recorded (the board stays paused until resumed).
    pause_recorded: bool = False
    #: For every approved draft not yet on the board, where a put would land.
    landings: list[DraftLanding] = Field(default_factory=list)


@dataclass
class _Destination:
    """What a put of one draft would do: continue ``row``, bring ``revived`` back, or add."""

    row: BoardRow | None = None
    revived: BoardRow | None = None
    #: The label of a live profile that makes the put a duplicate; the put is refused.
    taken: str | None = None


@dataclass
class _Phase:
    """The state one write phase carries from step to step."""

    client: GaggimateClient
    run_id: int
    update: SyncRunUpdate
    summary: BoardRunSummary
    machine: MachineState
    host: str
    failures_in_a_row: int = 0
    #: The file each live row holds in this plan, so one row's push never reuses another's.
    held: dict[int, str] = field(default_factory=dict)
    #: The Set versions each row's removal of its old file stopped naming, kept for the draft
    #: that replaced it: going back needs them to point the restored copy at the same versions.
    cleared: dict[int, list[int]] = field(default_factory=dict)


class BoardService:
    """Edits the board (no machine) and runs the write phase (the switch on)."""

    def __init__(
        self,
        db: Database,
        settings: SettingsService,
        *,
        attach: AttachToSet | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        #: How a Set version is recorded once its profile has reached the machine:
        #: ``ProfileDraftService.attach_to_set``, the same call a person's push makes.
        self.attach = attach
        self.board = ProfileBoardRepository(db)
        self.profiles = ProfilesRepository(db)
        self.sets = SetsRepository(db)
        self.drafts = ProfileDraftsRepository(db)
        self.writes = DeviceWritesRepository(db)
        self.runs = SyncRepository(db)
        self.plans = PlanBuilder(self.board, self.profiles, self.sets, self.writes)
        self.proposals = DraftProposals(db, settings)

    # ── reading ──────────────────────────────────────────────────────

    async def plan(self, machine: MachineState, *, host: str) -> BoardPlan:
        """What the next sync would do. Reads the archive only; ``machine`` is already read."""
        return await self.plans.plan(machine, host=host)

    async def view(self, machine: MachineState, *, source: str, host: str) -> BoardView:
        """The board, each row's machine state and the plan. Reads the archive only."""
        computed = await self.plans.compute(machine, host=host)
        adoption = await self.board.adoption()
        actions = computed.actions()
        rows: list[BoardRowView] = []
        for plan in computed.rows:
            device_id = plan.held or plan.row.device_profile_id
            copy = machine.profiles.get(device_id) if device_id else None
            rows.append(
                BoardRowView(
                    row=plan.row,
                    type=plan.version.type,
                    utility=plan.version.utility,
                    machine=BoardMachineState(
                        device_id=device_id,
                        present=copy is not None,
                        holds_current=plan.held is not None,
                        favorite=None if copy is None else copy.favorite,
                        selected=None if copy is None else copy.selected,
                    ),
                    planned=[a for a in actions if a.row_id == plan.row.id],
                )
            )
        return BoardView(
            adopted=computed.adopted,
            writes_enabled=bool(await self.settings.get("deviceWritesEnabled")),
            machine_source=source,
            rows=rows,
            pending_removals=[d.row for d in computed.deleted],
            actions=actions,
            reports=computed.reports(),
            paused=computed.paused,
            pause_recorded=adoption is not None and adoption.paused_at is not None,
            landings=await self._landings() if computed.adopted else [],
        )

    async def _landings(self) -> list[DraftLanding]:
        """Where each waiting draft (drafted or approved) would land, by the code a put runs.

        The same ``_destination`` ``put_draft`` calls, never a second implementation, so the
        page cannot offer a put the server would place somewhere else or refuse.
        """
        found: list[DraftLanding] = []
        waiting = [
            draft
            for draft in await self.drafts.list_drafts(open_only=True, limit=200)
            if draft.status in ("draft", "approved")
        ]
        for draft in waiting:
            if draft.draft_version_id is None:
                continue
            version = await self.profiles.get_version(draft.draft_version_id)
            if version is None:
                continue
            existing = await self.board.find_live_by_version(version.id)
            if existing is not None:
                # Its document is on the board already (this draft's own row, or another
                # draft that made the same document): nothing to put.
                found.append(
                    DraftLanding(
                        draft_id=draft.id,
                        already_on_board_label=existing.label,
                        plain=BoardLanding(),
                    )
                )
                continue
            found.append(
                DraftLanding(
                    draft_id=draft.id,
                    plain=await self._landing(draft, version, None),
                    for_set=None
                    if draft.set_id is None
                    else await self._landing(draft, version, draft.set_id),
                )
            )
        return found

    async def _landing(
        self, draft: ProfileDraftRow, version: ProfileVersionRow, set_id: int | None
    ) -> BoardLanding:
        dest = await self._destination(draft, version, set_id)
        if dest.taken is not None:
            return BoardLanding(taken_label=dest.taken)
        row = dest.row
        if row is None:
            return BoardLanding(revives_label=None if dest.revived is None else dest.revived.label)
        newer = row.pending_draft_id is not None and row.pending_draft_id > draft.id
        if not newer:
            holder = await self.drafts.first_draft_on_version(row.current_version_id)
            newer = holder is not None and holder > draft.id
        return BoardLanding(row_id=row.id, row_label=row.label, holds_newer_draft=newer)

    async def _destination(
        self, draft: ProfileDraftRow, version: ProfileVersionRow, set_id: int | None
    ) -> _Destination:
        """Where a put of this draft lands, and whether the board refuses it.

        The one place that decides, for the put and for the read of where a put lands:
        the row it continues (by lineage), else a deleted row it brings back, else a new
        row; and in every case the refusal that two live profiles never share a label. A
        put that keeps a row's label is never refused for it (a pair that already shares a
        label is left to be fixed, not made worse); one that would give a row a label
        another live row holds, or add a row beside one, is.
        """
        row = await self._lineage_row(draft, version, set_id)
        if row is not None:
            if row.label == version.label:
                return _Destination(row=row)
            holder = await self.board.find_live_by_label(version.label, excluding=row.id)
            return _Destination(row=row, taken=None if holder is None else holder.label)
        revived = await self._revivable(version)
        holder = await self.board.find_live_by_label(version.label)
        return _Destination(revived=revived, taken=None if holder is None else holder.label)

    async def _revivable(self, version: ProfileVersionRow) -> BoardRow | None:
        """The deleted row a put of this version would bring back, if there is one.

        One function for the put and for the read of where a put lands. A deleted row whose
        file a live row now stands on is not revivable: a new row, never two on one file.
        """
        revived = await self.board.find_deleted_with_file(version.id)
        if (
            revived is not None
            and revived.device_profile_id is not None
            and await self.board.other_live_on_device(
                revived.device_profile_id, excluding=revived.id
            )
            is not None
        ):
            return None
        return revived

    # ── editing the board (never the machine) ────────────────────────

    async def put_draft(
        self,
        draft_id: int,
        *,
        set_id: int | None = None,
        major: bool | None = None,
        acknowledge_stop_changes: bool = False,
    ) -> BoardRow:
        """Make a draft the profile's next current version, or a new profile. One action.

        Approving a proposal and putting it on the board are the same click: a drafted draft
        is approved here, in the same transaction as the row it makes, and a draft that moves
        a stop condition (the pump stops at another volume or weight, which changes how much
        coffee ends up in the cup) is refused until the person says they know
        (``acknowledge_stop_changes``). The list acknowledged is the one stored on the draft,
        which is the list the card rendered. A draft approved before this existed is put as it
        is, its acknowledgement already on record.

        The profile it is a new version of is found by lineage: for a draft recorded on a Set,
        the board profile standing for the Set's current version; otherwise the profile the
        draft was made from, and only when the label is unchanged (the draft keeps its base's
        label, so a renamed or forked draft is a new profile). A draft made from a profile the
        person wrote themselves carries the app label and so lands beside it, as a new row,
        unless the board already has a profile with that label, which refuses it.
        """
        # Every check and the write in one transaction: two puts of the same draft (a
        # double click) are serialised, and the second finds the first's row.
        async with self.db.transaction():
            draft = await self.drafts.get(draft_id)
            if draft is None:
                raise NotFound(f"No profile draft {draft_id}")
            if draft.status not in ("draft", "approved"):
                raise Conflict(f"A {draft.status} draft cannot go on the board.")
            changes = draft.stop_condition_changes or []
            if changes and not (draft.acknowledged_stop_changes or acknowledge_stop_changes):
                raise Conflict(
                    "This draft changes when the machine stops pumping, which changes how much "
                    "coffee ends up in the cup. Put it on the board again with "
                    "acknowledge_stop_changes to confirm you meant that.",
                    details={
                        "field": "acknowledge_stop_changes",
                        "stop_condition_changes": changes,
                    },
                )
            if draft.draft_version_id is None:
                raise Conflict("That draft has no document to put on the board")
            version = await self.profiles.get_version(draft.draft_version_id)
            if version is None:  # pragma: no cover - a foreign key guarantees it
                raise NotFound(f"No profile version {draft.draft_version_id}")
            if await self.board.find_live_by_version(version.id) is not None:
                raise Conflict("That profile version is already on the board.")

            dest = await self._destination(draft, version, set_id)
            if dest.taken is not None:
                raise Conflict(
                    f"The board already has {dest.taken}; refine this draft from it, or discard "
                    "it.",
                    details={"reason": "duplicate_label"},
                )
            row = dest.row
            if draft.status == "draft":
                await self.drafts.set_status(
                    draft.id, "approved", acknowledged=bool(changes and acknowledge_stop_changes)
                )
            pending = BoardRowPatch(
                pending_draft_id=draft.id, pending_set_id=set_id, pending_major=major
            )
            if row is None:
                revived = dest.revived
                if revived is not None:
                    # The same profile put back while its old file still waits to be dealt
                    # with (a Set may be brewing it): the row is the same profile again, not
                    # a second row that would push a second identical copy.
                    back = await self.board.update(
                        revived.id,
                        BoardRowPatch(
                            deleted_at=None,
                            failed_version_id=None,
                            **pending.model_dump(exclude_unset=True),
                        ),
                    )
                    assert back is not None
                    return back
                return await self.board.insert(
                    BoardRowWrite(
                        label=version.label,
                        current_version_id=version.id,
                        origin="draft",
                        pending_draft_id=draft.id,
                        pending_set_id=set_id,
                        pending_major=major,
                    )
                )
            updated = await self.board.update(
                row.id,
                BoardRowPatch(
                    label=version.label,
                    current_version_id=version.id,
                    # What the row was, so a person can go back to it.
                    previous_version_id=row.current_version_id,
                    back_from_set_id=None,
                    failed_version_id=None,
                    **pending.model_dump(exclude_unset=True),
                ),
            )
            assert updated is not None  # the row was read in this transaction
            return updated

    async def _lineage_row(
        self, draft: ProfileDraftRow, version: ProfileVersionRow, set_id: int | None
    ) -> BoardRow | None:
        if set_id is not None:
            current = await self.sets.current_version(set_id)
            if current is None or current.profile_version_id is None:
                return None
            found = await self.sets.current_device_profile(set_id)
            row = None
            if found is not None:
                row = await self.board.find_live_by_device(found[0])
            if row is None:
                row = await self.board.find_live_by_version(current.profile_version_id)
            return _only_app_row(row)
        base = await self.profiles.get_version(draft.base_version_id)
        if base is None or base.label != version.label:
            return None
        by_version = await self.board.find_live_by_version(base.id)
        if by_version is None and draft.base_device_profile_id is not None:
            by_version = await self.board.find_live_by_device(draft.base_device_profile_id)
        return _only_app_row(by_version)

    async def resume(self) -> None:
        """Let the next sync write again after it paused for a suspected machine reset.

        A person's decision, reached only from its route. It writes to the archive and sends
        nothing to the machine; the next sync then pushes the board's app profiles.
        """
        await self.board.resume()

    async def _saved_by_the_app(
        self, device_id: str, label: str, content_hash: str, *, host: str
    ) -> bool:
        """Whether a machine file is this app's own save, still exactly as it was saved.

        One rule for the first adoption and for taking a single profile later: the app
        label, an ``ok`` save of that id on this host, and content equal to what that save
        sent. Anything else is the person's profile, which a sync never pushes or removes.
        """
        return label.rstrip().endswith(
            APP_PROFILE_SUFFIX.strip()
        ) and await self.writes.saved_with_content(device_id, host=host, content_hash=content_hash)

    async def take(self, device_profile_id: str) -> BoardRow:
        """Put one profile the machine holds, and the board does not, onto the board as it is.

        What the first adoption does for every profile, for one that appeared afterwards
        (made on the display, or a copy the board let go of). It reads the archive's mirror
        and writes nothing to the machine. Refused for a profile no live mirror row has, and
        for a file any board row (a deleted one still waiting to be dealt with included)
        already stands on.
        """
        # Every check and the insert in one transaction, so two takes of one file are
        # serialised and the second finds the first's row. The unique index on a live row's
        # file is the backstop: a violation is the same refusal, never a 500.
        async with self.db.transaction():
            adoption = await self.board.adoption()
            if adoption is None:
                raise Conflict("The board has not taken the machine's profiles yet; sync first.")
            mirrored = await self.profiles.get_device_profile(device_profile_id)
            if mirrored is None or mirrored.deleted_at is not None:
                raise NotFound(
                    f"The machine has no profile {device_profile_id} as of the last sync"
                )
            for row in await self.board.list_rows(include_deleted=True):
                if row.device_profile_id == device_profile_id:
                    raise Conflict(
                        "That profile is already on the board."
                        if row.deleted_at is None
                        else "That profile was deleted from the board and its file is still "
                        "waiting to be dealt with by the next sync."
                    )
            version = await self.profiles.get_version(mirrored.current_version_id)
            if version is None:  # pragma: no cover - a foreign key guarantees it
                raise NotFound(f"No profile version {mirrored.current_version_id}")
            if await self.board.find_live_app_by_version(version.id) is not None:
                # A profile the app pushed already stands for exactly this content (a copy a
                # sync has just put on the machine while the old one is still kept): a second
                # row on it would be pushed a third copy by the next sync. The label is part of
                # the content, so the label rule below would refuse it too; this says it more
                # precisely.
                raise Conflict("A profile on the board already stands for this exact profile.")
            same_label = await self.board.find_live_by_label(version.label)
            if same_label is not None:
                # Two live profiles never share a label, the one the person made included:
                # the machine's own display tells them apart by it. Adoption takes the
                # machine as it is and reports the pairs instead; taking one more is a choice.
                raise Conflict(
                    f"The board already has {same_label.label}. Delete that one from the board "
                    "first if this is the one you want on it.",
                    details={"reason": "duplicate_label"},
                )
            ours = await self._saved_by_the_app(
                device_profile_id, version.label, version.content_hash, host=adoption.host
            )
            try:
                return await self.board.insert(
                    BoardRowWrite(
                        label=version.label,
                        current_version_id=version.id,
                        device_profile_id=device_profile_id,
                        device_version_id=version.id,
                        on_home_screen=mirrored.favorite,
                        origin="draft" if ours else "adopted",
                    )
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict("That profile is already on the board.") from exc

    async def go_back(self, row_id: int) -> BoardRow:
        """Make a profile its previous version again. The next sync does it on the machine.

        What the old rollback of a pushed draft did, as an edit of the board: the row's
        current version becomes the one it was before its newest put, and the sync then
        pushes that version and removes the newer copy through the same guards as any
        replacement. Nothing is sent to the machine here.

        Refused for a profile of the person's (the app only changes what it wrote), for one
        with no earlier version, and when the earlier version would give two live profiles one
        label (the newer version may have renamed the profile) or put a document on the board
        that another profile already holds.

        The record stays true in the same transaction: a version still waiting for the sync
        is withdrawn (its draft is discarded, nothing of it reached the machine), and a Set
        that recorded the version being left is remembered for the sync's removal guard. What
        the sync then does to drafts and Set versions is described at ``_retire``.
        """
        async with self.db.transaction():
            row = await self._live_row(row_id)
            if row.origin != "draft":
                raise Conflict(
                    f"{row.label} is a profile of yours: the app only goes back on profiles it "
                    "wrote itself."
                )
            if row.previous_version_id is None:
                raise Conflict(f"{row.label} has no earlier version to go back to.")
            previous = await self.profiles.get_version(row.previous_version_id)
            if previous is None:  # pragma: no cover - a foreign key guarantees it
                raise NotFound(f"No profile version {row.previous_version_id}")
            if previous.label != row.label:
                holder = await self.board.find_live_by_label(previous.label, excluding=row.id)
                if holder is not None:
                    raise Conflict(
                        f"The board already has {holder.label}, which is what {row.label} was "
                        "before; rename or delete that one first.",
                        details={"reason": "duplicate_label"},
                    )
            on_board = await self.board.find_live_by_version(previous.id)
            if on_board is not None and on_board.id != row.id:
                raise Conflict(f"That earlier version is already on the board as {on_board.label}.")

            back_from: int | None = None
            if row.pending_draft_id is not None:
                # Never reached the machine: the person withdraws it.
                await self.drafts.discard_unsent([row.pending_draft_id])
            if (
                row.device_profile_id is not None
                and row.device_version_id == row.current_version_id
            ):
                # The version being left is on the machine. A Set that recorded it still names
                # it; the sync is told not to keep the file for that Set's sake.
                for draft in await self.drafts.pushed_to_device(row.device_profile_id):
                    recorded = await self._recorded_set(draft)
                    if recorded is not None:
                        back_from = recorded
            updated = await self.board.update(
                row.id,
                BoardRowPatch(
                    label=previous.label,
                    current_version_id=previous.id,
                    previous_version_id=None,
                    back_from_set_id=back_from,
                    failed_version_id=None,
                    pending_draft_id=None,
                    pending_set_id=None,
                    pending_major=None,
                ),
            )
            assert updated is not None  # the row was read in this transaction
            return updated

    async def _recorded_set(self, draft: ProfileDraftRow) -> int | None:
        """The Set a draft's push recorded a version on, if it did."""
        if draft.recorded_version_id is None:
            return None
        version = await self.sets.get_version(draft.recorded_version_id)
        return None if version is None else version.set_id

    async def set_home_screen(self, row_id: int, on: bool) -> BoardRow:
        row = await self._live_row(row_id)
        updated = await self.board.update(row.id, BoardRowPatch(on_home_screen=on))
        assert updated is not None
        return updated

    async def delete_row(self, row_id: int) -> BoardRow:
        """Tombstone a profile. The next sync removes its file if the app wrote it."""
        row = await self._live_row(row_id)
        updated = await self.board.update(
            row.id,
            BoardRowPatch(
                deleted_at=utc_now(),
                pending_draft_id=None,
                pending_set_id=None,
                pending_major=None,
            ),
        )
        assert updated is not None
        return updated

    async def _live_row(self, row_id: int) -> BoardRow:
        row = await self.board.get(row_id)
        if row is None or row.deleted_at is not None:
            raise NotFound(f"No profile {row_id} on the board")
        return row

    # ── the write phase ──────────────────────────────────────────────

    async def run(
        self, client: GaggimateClient, *, run_id: int, update: SyncRunUpdate
    ) -> bool | None:
        """The sync's write phase. ``None`` (and nothing read or sent) with the switch off.

        Otherwise whether it changed anything, with the summary left on ``update``.

        Checked before the machine is read for any writing purpose: with the switch off the
        sync is a sync, and an audited "refused" row for something nobody attempted would only
        be noise in the audit a person reads to see what was sent.
        """
        if not await self.settings.get("deviceWritesEnabled"):
            return None
        summary = BoardRunSummary()
        try:
            await self._run(client, run_id, update, summary)
        except asyncio.CancelledError:
            raise
        except DeviceError as exc:
            self._fail(update, summary, "the machine could not be read for the board", exc)
        except Exception as exc:
            log.error("board_phase_crashed", exc_info=True)
            update.errors += 1
            update.error = update.error or f"{type(exc).__name__}: {exc}"
            summary.failures.append(
                BoardSummaryItem(label="profile board", reason="crashed", detail=str(exc))
            )
        update.summary = summary.model_dump(mode="json")
        update.profiles_changed += summary.changed
        return bool(summary.changed)

    def _fail(
        self, update: SyncRunUpdate, summary: BoardRunSummary, step: str, exc: DeviceError
    ) -> None:
        update.errors += 1
        update.error = update.error or f"{step}: {exc}"
        summary.failures.append(
            BoardSummaryItem(label="profile board", reason=step, detail=str(exc))
        )
        log.info("board_phase_failed", step=step, error=str(exc))

    async def _run(
        self, client: GaggimateClient, run_id: int, update: SyncRunUpdate, summary: BoardRunSummary
    ) -> None:
        machine = await read_machine(client)
        phase = _Phase(client, run_id, update, summary, machine, client.host)
        computed = await self.plans.compute(machine, host=phase.host)
        if not computed.adopted:
            await self._adopt(phase, computed)
            return
        if computed.paused:
            # Nothing is pushed or removed, and the pause outlives this sync: a person
            # resumes it, since a reset machine and a person who deleted every file by hand
            # look the same and only the person knows which it is.
            await self.board.pause(computed.paused)
            summary.paused = computed.paused
            await self._event(phase, EVENT_PAUSED, computed.paused, None, None)
            return
        phase.held = {p.row.id: p.held for p in computed.rows if p.held is not None}

        for plan in computed.rows:
            if phase.failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                break
            await self._apply_row(phase, plan)
        for deleted in computed.deleted:
            if phase.failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                break
            await self._apply_deleted(phase, deleted)

        # The stars last, and from a fresh read when anything was written: a replaced
        # profile's star was carried to its successor, and the firmware stars every save.
        if summary.writes or summary.pushed or summary.removed:
            machine = await read_machine(client)
            phase.machine = machine
            computed = await self.plans.compute(machine, host=phase.host)
        for plan in computed.rows:
            if plan.home is not None and plan.home.device_id is not None:
                await self._apply_home(phase, plan.home)
        await self.board.settle_resume()

    # ── adoption ─────────────────────────────────────────────────────

    async def _adopt(self, phase: _Phase, computed: Computed) -> None:
        """Take what the machine holds onto the board, as it is. Writes nothing to it."""
        if not phase.machine.profiles:
            # The firmware creates a Default profile on an empty filesystem, so an empty
            # list is a failed read; adopting nothing would leave the board empty for good.
            phase.summary.failures.append(
                BoardSummaryItem(
                    label="profile board",
                    reason="adoption",
                    detail="the machine listed no profiles, so nothing was adopted",
                )
            )
            phase.update.errors += 1
            phase.update.error = phase.update.error or "the machine listed no profiles"
            return
        async with self.db.transaction():
            for action in computed.adopt:
                device_id = action.device_id or ""
                profile = phase.machine.profiles[device_id]
                version, _ = await self.profiles.ensure_version(profile)
                # A file this app saved itself is the app's profile, not the person's: an
                # `ok` save of that id on this host, the app label, and content equal to what
                # that save sent. A later draft of it continues the row and replaces the copy.
                # A file edited on the display since the save (or anything else) stays the
                # person's, so the sync neither pushes nor removes it.
                ours = await self._saved_by_the_app(
                    device_id, profile.label, version.content_hash, host=phase.host
                )
                row = await self.board.insert(
                    BoardRowWrite(
                        label=version.label,
                        current_version_id=version.id,
                        device_profile_id=device_id,
                        device_version_id=version.id,
                        on_home_screen=profile.favorite,
                        origin="draft" if ours else "adopted",
                    )
                )
                phase.summary.adopted.append(
                    BoardSummaryItem(
                        row_id=row.id,
                        label=version.label,
                        device_id=device_id,
                        reason="first_pull",
                        on=profile.favorite,
                    )
                )
            await self.board.mark_adopted(phase.host)
        await self._event(
            phase,
            EVENT_ADOPTED,
            f"Took {len(computed.adopt)} profile(s) from the machine onto the board as they are.",
            None,
            {"count": len(computed.adopt)},
        )

    # ── one live row ─────────────────────────────────────────────────

    async def _apply_row(self, phase: _Phase, plan: RowPlan) -> None:
        row = plan.row
        if plan.report is not None:
            await self._report(phase, plan.report)
            return
        new_id = plan.held
        placed: Placed | None = None
        if plan.push is not None:
            placed = await self._push(phase, plan)
            if placed is None:
                return  # old stays on the machine, the row stands where it was
            new_id = placed.device_id
        assert new_id is not None  # a row either holds its content or has just pushed it

        settled = True
        removal: Removal | None = None
        if plan.pred is not None and plan.pred.device_id is not None:
            if plan.pred.kind == "leave":
                await self._left(phase, plan.pred)
                settled = plan.pred.detail in FINAL_REFUSALS
            else:
                removal = await self._remove(
                    phase,
                    plan.pred,
                    expected_hash=plan.pred_hash,
                    successor=new_id,
                    row=plan.row,
                    version_id=row.device_version_id,
                    excluding_set=row.leaving_set_id,
                )
                settled = _is_settled(removal)
        if settled and (
            row.device_profile_id != new_id or row.device_version_id != plan.version.id
        ):
            await self.board.update(
                row.id,
                BoardRowPatch(device_profile_id=new_id, device_version_id=plan.version.id),
            )
        if settled and row.back_from_set_id is not None:
            # The file the Set was named for has been dealt with (or will never be this app's
            # to remove): the exemption was for this one pass.
            await self.board.update(row.id, BoardRowPatch(back_from_set_id=None))
        if placed is not None and placed.served is not None:
            favorite = placed.served.favorite or bool(removal and removal.favorite_carried)
            selected = placed.served.selected or bool(removal and removal.selected_carried)
            await self._mirror(new_id, placed.served, favorite=favorite, selected=selected)
        await self._record(
            phase, plan, new_id, saved=placed is not None and not placed.reused, removal=removal
        )

    async def _report(self, phase: _Phase, action: BoardAction) -> None:
        """Say what is wrong with a row the sync will not touch."""
        item = BoardSummaryItem(
            row_id=action.row_id,
            label=action.label,
            device_id=action.device_id,
            reason=action.reason,
            detail=action.detail,
        )
        if action.reason == "unreadable":
            await self._row_failed(
                phase, action, "unreadable", action.detail, counts_as_device_failure=False
            )
            return
        phase.summary.left.append(item)
        await self._event(
            phase,
            EVENT_LEFT,
            f"{action.label}: {action.detail}.",
            action.device_id,
            {"row_id": action.row_id, "reason": action.reason},
        )

    async def _push(self, phase: _Phase, plan: RowPlan) -> Placed | None:
        """Save the row's current version (unless the machine holds it) and verify it.

        Every version that is pushed here is first run through the schema and the safety
        policy with the bounds as they are now: a profile that was valid when it was drafted
        may not be under the bounds a person has since tightened. A version that fails stays
        off the machine and is reported. ``None`` when it did not land, with the failure
        recorded; the previous file is then untouched. A copy that was saved but did not
        verify is removed again through the guarded path, so each retry does not add one.
        """
        assert plan.push is not None
        action = plan.push
        try:
            profile = profile_from_version(plan.version)
            bounds = await self.proposals.bounds()
            clamped, changes = enforce(profile, bounds)
            if changes:
                raise _OutsideBounds(
                    "outside the current safety bounds: "
                    + "; ".join(f"{c.path} {c.before:g} -> {c.after:g}" for c in changes)
                )
        except (ProfileRejected, Unprocessable, ValidationError, _OutsideBounds) as exc:
            await self._row_failed(
                phase, action, "policy", str(exc), counts_as_device_failure=False
            )
            return None
        # `place` reuses any file holding the content, and a file another board profile
        # stands on is not this row's to share: two rows on one file would have the next
        # replace of one remove the other's profile. It is shown the machine without them,
        # deleted rows' files and files other rows hold in this plan included.
        taken = {
            other.device_profile_id
            for other in await self.board.list_rows(include_deleted=True)
            if other.id != plan.row.id and other.device_profile_id is not None
        } | {file for row_id, file in phase.held.items() if row_id != plan.row.id}
        visible = MachineState(
            profiles={i: p for i, p in phase.machine.profiles.items() if i not in taken}
        )
        try:
            placed = await place(phase.client, clamped, visible)
        except DeviceError as exc:
            await self._row_failed(phase, action, "push", str(exc))
            return None
        if not placed.reused:
            phase.summary.writes += 1
        if placed.served is not None and placed.problem is None:
            phase.machine.profiles[placed.device_id] = placed.served
        if placed.problem is not None:
            cleanup = await remove_if_ours(
                phase.client,
                self.writes,
                device_id=placed.device_id,
                expected_hash=None,
                successor=None,
            )
            phase.summary.writes += 1 if cleanup.removed else 0
            if cleanup.removed:
                phase.machine.profiles.pop(placed.device_id, None)
                await self.profiles.mark_one_deleted(placed.device_id)
            else:
                # A copy that did not verify and cannot be taken off again: pushing the
                # same version every sync would add one each time.
                await self.board.update(
                    plan.row.id, BoardRowPatch(failed_version_id=plan.version.id)
                )
            await self._row_failed(
                phase,
                action,
                "round_trip",
                f"{placed.error}; "
                + (
                    "that copy was removed again and the previous version stays."
                    if cleanup.removed
                    else f"that copy could not be removed ({cleanup.reason}); "
                    "the previous version stays."
                ),
                device_id=placed.device_id,
            )
            return None
        phase.failures_in_a_row = 0
        item = BoardSummaryItem(
            row_id=plan.row.id,
            label=plan.version.label,
            device_id=placed.device_id,
            reason=action.reason,
            detail="an identical profile was already on the machine" if placed.reused else "",
            reused=placed.reused,
        )
        phase.summary.pushed.append(item)
        message = f"Put {plan.version.label} on the machine as {placed.device_id}."
        await self._event(
            phase,
            EVENT_PUSHED,
            message,
            placed.device_id,
            {"row_id": plan.row.id, "reason": action.reason, "reused": placed.reused},
        )
        if action.reason == "edited_on_machine":
            phase.summary.overwritten.append(item)
            await self._event(
                phase,
                EVENT_OVERWROTE,
                f"{plan.version.label} had been edited on the machine; the board's version was "
                f"put back as {placed.device_id}.",
                placed.device_id,
                {"row_id": plan.row.id, "replaced_device_id": plan.row.device_profile_id},
            )
        return placed

    async def _remove(
        self,
        phase: _Phase,
        action: BoardAction,
        *,
        expected_hash: str | None,
        successor: str | None,
        row: BoardRow,
        version_id: int | None,
        excluding_set: int | None,
    ) -> Removal:
        """Remove a file through the guards and say what happened.

        What the file must still hold is what the archive recorded for it, never what the plan
        read. And who stands on it is asked again now: another live board profile, or a Set,
        keeps it, whatever the plan saw a moment ago.
        """
        device_id = action.device_id or ""
        blocked = None
        if action.reason == "deleted" and not await self._still_deleted(row.id):
            blocked = ANOTHER_ROW
        elif row.origin != "draft":
            # A person's profile, whatever the audit says about the id: never removed.
            blocked = NOT_OURS
        elif await self.board.other_live_on_device(device_id, excluding=row.id) is not None:
            blocked = ANOTHER_ROW
        else:
            using = await self.sets.sets_currently_using(
                device_id, version_id, excluding=excluding_set
            )
            if using:
                blocked = f"still the current version of the Set {using[0]!r}"
        removal = await remove_if_ours(
            phase.client,
            self.writes,
            device_id=device_id,
            expected_hash=expected_hash,
            successor=successor,
            blocked=blocked,
        )
        phase.summary.writes += (
            int(removal.favorite_carried) + int(removal.selected_carried) + int(removal.removed)
        )
        if removal.removed:
            phase.failures_in_a_row = 0
            phase.machine.profiles.pop(device_id, None)
            await self.profiles.mark_one_deleted(device_id)
            cleared = await self.sets.clear_pushed_device_profile(device_id)
            phase.cleared[row.id] = cleared
            if row.pending_draft_id is not None:
                await self.drafts.supersede_pushed(device_id, by_draft_id=row.pending_draft_id)
            else:
                await self._retire(row, device_id, successor)
            phase.summary.removed.append(
                BoardSummaryItem(
                    row_id=row.id,
                    label=action.label,
                    device_id=device_id,
                    reason=action.reason,
                    detail="the favourite star moved to the new copy"
                    if removal.favorite_carried
                    else "",
                )
            )
            await self._event(
                phase,
                EVENT_REMOVED,
                f"Removed {action.label} ({device_id}) from the machine: {_REASON[action.reason]}.",
                device_id,
                {"row_id": row.id, "reason": action.reason, "cleared_set_versions": cleared},
            )
        elif removal.gone or removal.unrelated:
            return removal
        elif _is_settled(removal) or _is_asked_again(removal):
            await self._left(
                phase, action.model_copy(update={"kind": "leave", "detail": removal.reason})
            )
        else:
            # The machine could not be asked, or refused for a reason that may pass: the row
            # keeps standing on the file and the next sync tries again.
            await self._row_failed(phase, action, "remove", removal.reason, device_id=device_id)
        return removal

    async def _retire(self, row: BoardRow, device_id: str, successor: str | None) -> None:
        """Close the record of a file removed without a newer draft taking its place.

        Going back to a previous version, or deleting a profile, takes a pushed draft's file
        off the machine and no later push replaces it. What a rollback did: the draft that
        pushed it is ``discarded`` and stops naming the file. And for going back, the Set
        versions that named the copy this one replaced (cleared when it was replaced) name the
        copy that has just been put back, so the history says where each of them is again.
        """
        going_back = row.deleted_at is None
        found = await self.drafts.retire_pushed(
            device_id,
            outcome={
                "action": "went_back" if going_back else "removed",
                "removed_device_profile_id": device_id,
                "lines": [
                    f"Went back to the previous version: {device_id} is off the machine."
                    if going_back
                    else f"The profile was deleted from the board: {device_id} is off the machine."
                ],
            },
        )
        if not going_back or successor is None:
            return
        for draft in found:
            if (
                draft.replaced_version_id == row.current_version_id
                and draft.cleared_set_version_ids
            ):
                await self.sets.restore_pushed_device_profile(
                    [int(i) for i in draft.cleared_set_version_ids], successor
                )

    async def _still_deleted(self, row_id: int) -> bool:
        """Asked again at the moment of the delete: a revived row's file is not to be removed."""
        fresh = await self.board.get(row_id)
        return fresh is not None and fresh.deleted_at is not None

    async def _left(self, phase: _Phase, action: BoardAction) -> None:
        phase.summary.left.append(
            BoardSummaryItem(
                row_id=action.row_id,
                label=action.label,
                device_id=action.device_id,
                reason=action.reason,
                detail=action.detail,
            )
        )
        await self._event(
            phase,
            EVENT_LEFT,
            f"Left {action.label} ({action.device_id}) on the machine: {action.detail}.",
            action.device_id,
            {"row_id": action.row_id, "reason": action.reason, "why": action.detail},
        )

    # ── one deleted row ──────────────────────────────────────────────

    async def _apply_deleted(self, phase: _Phase, deleted: DeletedPlan) -> None:
        row = deleted.row
        release = BoardRowPatch(device_profile_id=None, device_version_id=None)
        fresh = await self.board.get(row.id)
        if fresh is None or fresh.deleted_at is None:
            return  # put back on the board since the plan: it is a live profile again
        if deleted.action is None:
            await self.board.update(row.id, release)  # the file is already gone
            return
        action = deleted.action
        if action.kind == "report":
            await self._report(phase, action)  # not readable: neither removed nor forgotten
            return
        if action.kind == "leave":
            await self._left(phase, action)
            if action.detail in FINAL_REFUSALS:
                await self.board.update(row.id, release)
            return
        removal = await self._remove(
            phase,
            action,
            expected_hash=deleted.expected_hash,
            successor=deleted.successor_id,
            row=row,
            version_id=row.device_version_id,
            excluding_set=None,
        )
        if _is_settled(removal):
            await self.board.update(row.id, release)

    # ── stars ────────────────────────────────────────────────────────

    async def _apply_home(self, phase: _Phase, action: BoardAction) -> None:
        device_id = action.device_id or ""
        try:
            if action.on:
                await phase.client.favorite_profile(device_id)
            else:
                await phase.client.unfavorite_profile(device_id)
        except DeviceError as exc:
            await self._row_failed(phase, action, "home_screen", str(exc), device_id=device_id)
            return
        phase.summary.writes += 1
        phase.summary.home_screen.append(
            BoardSummaryItem(
                row_id=action.row_id,
                label=action.label,
                device_id=device_id,
                reason=action.reason,
                on=action.on,
            )
        )
        existing = await self.profiles.get_device_profile(device_id)
        served = phase.machine.profiles.get(device_id)
        if existing is not None:
            await self.profiles.upsert_device_profile(
                device_id=device_id,
                version_id=existing.current_version_id,
                favorite=bool(action.on),
                selected=existing.selected,
                position=existing.position,
            )
        if served is not None:
            served.favorite = bool(action.on)
        await self._event(
            phase,
            EVENT_HOME_SCREEN,
            f"{action.label} is {'on' if action.on else 'off'} the machine's home screen.",
            device_id,
            {"row_id": action.row_id, "on": action.on},
        )

    # ── bookkeeping ──────────────────────────────────────────────────

    async def _mirror(
        self, device_id: str, served: Profile, *, favorite: bool, selected: bool
    ) -> None:
        """Put what was just saved into the archive's mirror straight away."""
        version, _ = await self.profiles.ensure_version(
            served, source="draft", device_json=json.dumps(served.to_device())
        )
        existing = await self.profiles.get_device_profile(device_id)
        await self.profiles.upsert_device_profile(
            device_id=device_id,
            version_id=version.id,
            favorite=favorite,
            selected=selected,
            position=None if existing is None else existing.position,
        )

    async def _record(
        self, phase: _Phase, plan: RowPlan, device_id: str, *, saved: bool, removal: Removal | None
    ) -> None:
        """Say on the draft and on the Set that the profile is on the machine.

        The draft becomes ``pushed`` exactly as a person's push leaves it, and a Set version is
        recorded through the same ``attach_to_set`` a push for a Set calls. Done at the moment
        the profile is found on the machine, whether this pass put it there or an earlier one
        did and was cut off before this step.
        """
        row = plan.row
        if row.pending_draft_id is None:
            return
        draft = await self.drafts.get(row.pending_draft_id)

        async def clear() -> None:
            # Only if the row is still what this pass acted on: a person may have put a newer
            # draft on the profile while it worked, and that one's pending Set must survive.
            await self.board.clear_pending_if_unchanged(
                row.id, version_id=plan.version.id, draft_id=row.pending_draft_id
            )

        if draft is None or draft.status != "approved":
            # Only a draft still waiting is recorded: one a person pushed or turned down in
            # the meantime has been dealt with elsewhere, and recording it again would add a
            # second Set version.
            await clear()
            return
        # Truthful about who saved it: a pass cut off between the save and this step finds the
        # file already there, and the audit still holds the save this app made.
        saved = saved or await self.writes.created_by_us(device_id, host=phase.host)
        lines = [f"Put on the machine by the profile board as {device_id}."]
        outcome: dict[str, Any] = {
            "action": "push",
            "reused_device_profile_id": None if saved else device_id,
            "replaced_device_profile_id": None,
            "kept_device_profile_id": None,
            "kept_reason": None,
            "startup_profile_cleared": False,
            "lines": lines,
        }
        replaced = (
            plan.pred.device_id if plan.pred is not None and removal and removal.removed else None
        )
        if replaced:
            outcome["replaced_device_profile_id"] = replaced
            lines.append(f"Replaced {replaced}: the previous copy is off the machine.")
        elif plan.pred is not None and plan.pred.device_id:
            outcome["kept_device_profile_id"] = plan.pred.device_id
            outcome["kept_reason"] = plan.pred.detail or (removal.reason if removal else "")
            lines.append(f"Left {plan.pred.device_id} on the machine: {outcome['kept_reason']}.")
        served = phase.machine.profiles.get(device_id)
        pushed = await self.drafts.set_status(
            draft.id,
            "pushed",
            pushed_device_profile_id=device_id,
            pushed_saved=saved,
            verification=None
            if served is None
            else {
                "sent": profile_from_version(plan.version).to_device(),
                "loaded": served.to_device(),
            },
            outcome=outcome,
            replaced_device_profile_id=replaced,
            replaced_version_id=row.device_version_id if replaced else None,
            cleared_set_version_ids=phase.cleared.get(row.id) if replaced else None,
        )
        if pushed is not None and row.pending_set_id is not None:
            await self._record_on_set(phase, pushed, row)
        await clear()

    async def _record_on_set(self, phase: _Phase, pushed: ProfileDraftRow, row: BoardRow) -> None:
        set_id = row.pending_set_id
        assert set_id is not None
        refusal = await self.sets.design_refusal(set_id)
        reason = ""
        if self.attach is None:  # pragma: no cover - the lifespan always wires it
            reason = "no way to record a Set version is configured"
        elif refusal is not None:
            reason = f"the Set refused a version ({refusal})"
        else:
            try:
                version = await self.attach(pushed, set_id, row.pending_major)
            except VersionRefused as exc:
                reason = exc.message
            else:
                await self.drafts.set_recorded_version(pushed.id, version.id if version else None)
        if reason:
            phase.summary.failures.append(
                BoardSummaryItem(
                    row_id=row.id,
                    label=row.label,
                    reason="set_version",
                    detail="the profile is on the machine, but no Set version was recorded: "
                    + reason,
                )
            )
            await self._event(
                phase,
                EVENT_FAILED,
                f"{row.label} is on the machine, but no Set version was recorded: {reason}.",
                pushed.pushed_device_profile_id,
                {"row_id": row.id, "set_id": set_id},
            )

    async def _row_failed(
        self,
        phase: _Phase,
        action: BoardAction,
        step: str,
        detail: str,
        *,
        device_id: str | None = None,
        counts_as_device_failure: bool = True,
    ) -> None:
        phase.update.errors += 1
        phase.update.error = phase.update.error or f"{action.label}: {detail}"
        if counts_as_device_failure:
            phase.failures_in_a_row += 1
        phase.summary.failures.append(
            BoardSummaryItem(
                row_id=action.row_id,
                label=action.label,
                device_id=device_id or action.device_id,
                reason=step,
                detail=detail,
            )
        )
        log.info("board_action_failed", step=step, label=action.label, error=detail)
        await self._event(
            phase,
            EVENT_FAILED,
            f"{action.label}: {step} failed: {detail}",
            device_id or action.device_id,
            {"row_id": action.row_id, "step": step},
        )

    async def _event(
        self,
        phase: _Phase,
        kind: str,
        message: str,
        device_id: str | None,
        data: dict[str, Any] | None,
    ) -> None:
        await self.runs.add_event(
            kind, run_id=phase.run_id, device_id=device_id, message=message, data=data
        )


#: What each removal reason says in an event.
_REASON = {
    "superseded": "a newer version of the profile took its place",
    "deleted": "the profile was deleted from the board",
}


def _only_app_row(row: BoardRow | None) -> BoardRow | None:
    """A new version only ever continues a profile the app itself pushed.

    A profile the person made (an adopted row) stays on the board as it was; a draft made
    from it, or for a Set that brews it, is a profile of its own beside it.
    """
    return row if row is not None and row.origin == "draft" else None


def _is_settled(removal: Removal) -> bool:
    """The row may let go of the file: it is gone, or will never be this app's to remove.

    A Set still brewing it, another board profile standing on it, or a machine that did not
    answer are not final: the row keeps the file and the next sync asks again.
    """
    return removal.removed or removal.gone or removal.reason in FINAL_REFUSALS


def _is_asked_again(removal: Removal) -> bool:
    """A refusal that is reported, not a failure, and repeats until what causes it passes."""
    return removal.reason == ANOTHER_ROW or removal.reason.startswith("still the current version")


async def machine_from_mirror(profiles: ProfilesRepository) -> MachineState:
    """The machine as the archive last mirrored it, for a preview when it cannot be read now."""
    state = MachineState()
    for summary in await profiles.list_device_profiles():
        version = await profiles.get_version(summary.current_version_id)
        if version is None:  # pragma: no cover - a foreign key guarantees it
            continue
        profile = profile_from_version(version)
        profile = profile.model_copy(
            update={"favorite": summary.favorite, "selected": summary.selected}
        )
        state.profiles[summary.device_id] = profile
    return state
