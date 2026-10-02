"""The app's profile list, and the write phase of a sync that makes the machine match it.

Two halves with one rule between them.

**Editing the list never touches the machine.** Switching a profile on or off the machine,
starring it, making one of its versions active and putting an approved draft on the list change
rows in the archive and nothing else; they work with the writes switch off, and with no machine
at all.

**The write phase is the one automatic write this app makes**, and it runs only when the
`deviceWritesEnabled` switch is on, at the end of the profile pass of a sync, inside the sync
engine's lock. It does not decide anything the plan has not (:mod:`.board_plan`) and it has
no machine path of its own: every save is :func:`~gaggiclanker.drafts.machine.place`, every
removal :func:`~gaggiclanker.drafts.machine.remove_profile`, so the audit, the write gate and
the one removal guard (a fresh load must hold exactly the content the archive recorded) apply
as they do to a person's push.

What a sync does, in order:

1. **The first sync with the switch on** takes the machine's files into the list by the
   rules below and writes nothing; the sync ends there.
2. **Files it has never seen** join the list (see :mod:`.board_plan`): attached to the profile
   they belong to, or as a new profile that is on. Nothing is removed in the sync that finds a
   file.
3. **Per profile that is on**: push its active version when the machine holds none (the safety
   policy runs again here, the save-then-load round trip too); then remove the file it stood
   on before. A round trip that does not match removes the copy just written (the same guarded
   path), keeps the previous version on the machine and records the failure; that version is
   not tried again until another is made active. A profile in conflict (a file holding content
   it never had) is skipped entirely; a file holding an older version of it is recorded and
   handled as usual.
4. **Per profile that is off**: remove its file (the selection moves first to the first profile
   that is on), after the same fresh-load check.
5. **Per deleted row**: remove its file the same way.
6. **Stars**, only for profiles that are on, from a fresh read when anything was written above.

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
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos import lineage
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.lineage import lineage_owner
from gaggiclanker.db.repos.profile_board import (
    BoardRow,
    BoardRowPatch,
    BoardRowWrite,
    ProfileBoardRepository,
)
from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow, ProfileDraftsRepository
from gaggiclanker.db.repos.profile_list import version_source_for_draft
from gaggiclanker.db.repos.profiles import (
    SYNTHETIC_BASE_LABEL,
    ProfilesRepository,
    ProfileVersionRow,
)
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionRow, VersionRefused
from gaggiclanker.db.repos.sync import SyncRepository, SyncRunUpdate
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.domain.models import APP_PROFILE_SUFFIX, Profile, profile_content_hash
from gaggiclanker.domain.profile_policy import ProfileRejected, enforce
from gaggiclanker.drafts.board_plan import (
    ANOTHER_ROW,
    CONFLICT,
    BoardAction,
    BoardPlan,
    Computed,
    DeletedPlan,
    PlanBuilder,
    RowPlan,
)
from gaggiclanker.drafts.board_versions import (
    ActiveVersion,
    ConflictSummary,
    ConflictView,
    ListedVersion,
    ProfileVersionsView,
    ProposedVersion,
    SetBrewing,
    short_hash,
)
from gaggiclanker.drafts.machine import (
    CHANGED_SINCE,
    NO_SUCCESSOR,
    MachineState,
    Placed,
    Removal,
    place,
    read_machine,
    remove_profile,
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
EVENT_JOINED = "board_joined"
EVENT_RECORDED = "board_recorded"
EVENT_CONFLICT = "board_conflict"
EVENT_PUSHED = "board_pushed"
EVENT_REMOVED = "board_removed"
EVENT_LEFT = "board_left"
EVENT_HOME_SCREEN = "board_home_screen"
EVENT_FAILED = "board_failed"
EVENT_PAUSED = "board_paused"

#: Three failed profiles in a row mean the machine has gone, not that three profiles are bad.
MAX_CONSECUTIVE_FAILURES = 3

type AttachToSet = Callable[[ProfileDraftRow, int, bool | None], Awaitable[SetVersionRow | None]]


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

    #: Files that joined the list this run: all of the machine's on the first sync with the
    #: switch on, and afterwards the ones the app had never seen (``unseen`` new profiles,
    #: ``attached`` to the profile they belong to).
    adopted: list[BoardSummaryItem] = Field(default_factory=list)
    #: Profiles in conflict this run: the machine's file differs from everything the app knows,
    #: so nothing was done for them.
    conflicts: list[BoardSummaryItem] = Field(default_factory=list)
    #: Content found on a file edited on the display, recorded as a version of its profile.
    recorded: list[BoardSummaryItem] = Field(default_factory=list)
    pushed: list[BoardSummaryItem] = Field(default_factory=list)
    removed: list[BoardSummaryItem] = Field(default_factory=list)
    #: Files that stay on the machine (still in use, edited since the last sync, the selected
    #: profile with nothing to take over) and why.
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
    #: What the next sync would do about this profile (while paused: what resuming would do).
    planned: list[BoardAction] = Field(default_factory=list)
    #: The two independent flags, repeated from ``row`` under the names the page uses.
    on_machine: bool = True
    starred: bool = True
    #: The active version, summarised.
    active_version: ActiveVersion
    #: Sets whose current version brews this profile (any of its versions, or the file it stands
    #: on): changing the active version or switching it off changes what they brew.
    sets_brewing: list[SetBrewing] = Field(default_factory=list)
    #: The profile an "Edit a copy" of this one becomes a version of (its own label, or the app's
    #: copy of it), or ``None`` when the copy would be a profile of its own: the lineage rule's
    #: answer, so a page can say which before saving.
    edit_lands_on_label: str | None = None
    #: In conflict: the machine's file differs from everything the app knows, so a sync does
    #: nothing for this profile until a person chooses a side (``POST .../conflict``).
    in_conflict: bool = False
    conflict: ConflictSummary | None = None
    #: How many open drafts would become a version of this profile once made active.
    proposed_versions: int = 0


class BoardLanding(BaseModel):
    """Where a put of one draft would land: the row it continues, or a new profile."""

    model_config = ConfigDict(extra="forbid")

    #: The board row the draft would become the next version of; ``None`` for a new profile.
    row_id: int | None = None
    row_label: str | None = None
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


class BoardProposal(BaseModel):
    """An open draft as the list shows it: a version waiting for a person."""

    model_config = ConfigDict(extra="forbid")

    draft: ProfileDraftRow
    #: The profile it would become a version of (by the landing a put runs); ``None`` when it
    #: would be a new profile, or cannot land (``landing.plain.taken_label``).
    row_id: int | None = None
    landing: DraftLanding


class ResumePreview(BaseModel):
    """What resuming a paused sync would do, so one button can say it."""

    model_config = ConfigDict(extra="forbid")

    #: Profiles put back on the machine.
    push: int = 0
    #: Files taken off it, counting a file that joins a profile that is off (the sync after
    #: the one that attaches it removes it: a file is never removed by the sync that finds it).
    remove: int = 0
    #: Stars changed.
    star: int = 0
    #: Files that would join the list (attached to a profile, or a new one).
    join: int = 0
    #: The lines, one per action, in the order a sync takes them.
    lines: list[BoardAction] = Field(default_factory=list)


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
    #: While paused: what resuming would do ("put back N profiles and remove M"), else ``None``.
    resume_preview: ResumePreview | None = None
    #: For every approved draft not yet on the board, where a put would land.
    landings: list[DraftLanding] = Field(default_factory=list)
    #: The open drafts that are not a version of any profile yet, each with the profile it would
    #: land on: the proposed versions inside a profile, and the proposed new profiles.
    proposals: list[BoardProposal] = Field(default_factory=list)


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
    #: The profiles that gained a file in this sync (a new profile, or a file attached): the
    #: sync that finds a file does nothing else to it, its star included.
    fresh: set[int] = field(default_factory=set)
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
        self.proposals = DraftProposals(db, settings)
        self.plans = PlanBuilder(self.board, self.profiles, policy=self.policy_refusal)

    # ── reading ──────────────────────────────────────────────────────

    async def policy_refusal(self, version: ProfileVersionRow) -> str | None:
        """Why this version may not be pushed under the safety bounds as they are now, if so.

        A profile that was valid when it was drafted may not be under the bounds a person has
        since tightened, and the schema may reject a document an older build stored. The plan,
        the push and "make active" all ask this one question.
        """
        try:
            profile = profile_from_version(version)
            _, changes = enforce(profile, await self.proposals.bounds())
        except (ProfileRejected, Unprocessable, ValidationError) as exc:
            return str(exc)
        if changes:
            return "outside the current safety bounds: " + "; ".join(
                f"{c.path} {c.before:g} -> {c.after:g}" for c in changes
            )
        return None

    async def plan(self, machine: MachineState, *, host: str) -> BoardPlan:
        """What the next sync would do. Reads the archive only; ``machine`` is already read."""
        return await self.plans.plan(machine, host=host)

    async def view(self, machine: MachineState, *, source: str, host: str) -> BoardView:
        """The list, each profile's machine state and the plan. Reads the archive only."""
        computed = await self.plans.compute(machine, host=host)
        adoption = await self.board.adoption()
        actions = computed.actions()
        would_do = computed.would_do()
        landings = await self._landings() if computed.adopted else []
        proposals = await self._proposals(landings)
        proposed_by_row: dict[int, int] = {}
        for proposal in proposals:
            if proposal.row_id is not None:
                proposed_by_row[proposal.row_id] = proposed_by_row.get(proposal.row_id, 0) + 1
        brews = await self.sets.current_brews()
        counts = await self.board.shot_counts([p.version.id for p in computed.rows])
        rows: list[BoardRowView] = []
        for plan in computed.rows:
            device_id = plan.held or plan.row.device_profile_id
            copy = machine.profiles.get(device_id) if device_id else None
            entry = await self.board.get_version_entry(plan.row.id, plan.version.id)
            listed = {v.version_id for v in await self.board.list_versions(plan.row.id)}
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
                    planned=[a for a in would_do if a.row_id == plan.row.id],
                    on_machine=plan.row.on_machine,
                    starred=plan.row.on_home_screen,
                    active_version=ActiveVersion(
                        version_id=plan.version.id,
                        short_hash=short_hash(plan.version.content_hash),
                        label=plan.version.label,
                        type=plan.version.type,
                        utility=plan.version.utility,
                        source=entry.source if entry is not None else "machine",
                        created_at=plan.version.created_at,
                        shots_brewed=counts.get(plan.version.id, 0),
                        profile=plan.version.profile,
                    ),
                    sets_brewing=_sets_brewing(brews, listed, plan.row.device_profile_id),
                    edit_lands_on_label=await self._edit_lands_on(plan.row.id),
                    in_conflict=plan.conflict_file is not None,
                    conflict=await self._conflict_summary(plan),
                    proposed_versions=proposed_by_row.get(plan.row.id, 0),
                )
            )
        preview = None
        if computed.paused:
            off_rows = {p.row.id for p in computed.rows if not p.row.on_machine}
            preview = ResumePreview(
                push=sum(1 for a in would_do if a.kind == "push"),
                # Files the plan removes now, and files that join a profile that is off: the
                # sync after the one that attaches them takes those off the machine.
                remove=sum(1 for a in would_do if a.kind == "remove")
                + sum(
                    1
                    for a in would_do
                    if a.kind == "adopt" and a.reason == "attached" and a.row_id in off_rows
                ),
                star=sum(1 for a in would_do if a.kind == "home_screen"),
                join=sum(1 for a in would_do if a.kind == "adopt"),
                lines=would_do,
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
            resume_preview=preview,
            landings=landings,
            proposals=proposals,
        )

    async def _edit_lands_on(self, row_id: int) -> str | None:
        landed = await lineage.edit_continues(_LiveLineage(self), row_id)
        return None if landed is None else landed.label

    async def _conflict_summary(self, plan: RowPlan) -> ConflictSummary | None:
        if plan.conflict_file is None or plan.conflict_hash is None:
            return None
        stored = await self.profiles.get_version_by_hash(plan.conflict_hash)
        return ConflictSummary(
            device_id=plan.conflict_file,
            version_id=None if stored is None else stored.id,
            content_hash=plan.conflict_hash,
            short_hash=short_hash(plan.conflict_hash),
        )

    async def _mirror_conflict(self, row: BoardRow) -> ProfileVersionRow | None:
        """The machine's version of a profile's file when it is in conflict, from the mirror.

        The same rule the plan applies to a read of the machine: content that is neither what
        the profile last recorded for the file, nor its active version, nor a version it has had
        (the ones only found on the file excepted), nor what a person already overruled.
        """
        if row.device_profile_id is None:
            return None
        mirrored = await self.profiles.get_device_profile(row.device_profile_id)
        if mirrored is None or mirrored.deleted_at is not None:
            return None
        version = await self.profiles.get_version(mirrored.current_version_id)
        active = await self.profiles.get_version(row.current_version_id)
        if version is None or active is None:  # pragma: no cover - foreign keys
            return None
        recorded = (
            None
            if row.device_version_id is None
            else await self.profiles.get_version(row.device_version_id)
        )
        known = {active.content_hash, row.conflict_overruled_hash}
        if recorded is not None:
            known.add(recorded.content_hash)
        known |= await self.board.listed_hashes(row.id)
        return None if version.content_hash in known else version

    async def conflict(self, row_id: int) -> ConflictView | None:
        """Both sides of a profile's conflict, or ``None`` when it has none."""
        row = await self._live_row(row_id)
        machine = await self._mirror_conflict(row)
        active = await self.profiles.get_version(row.current_version_id)
        if machine is None or active is None or row.device_profile_id is None:
            return None
        return ConflictView(
            row_id=row.id,
            label=row.label,
            machine=ConflictSummary(
                device_id=row.device_profile_id,
                version_id=machine.id,
                content_hash=machine.content_hash,
                short_hash=short_hash(machine.content_hash),
            ),
            machine_profile=machine.profile,
            app_version_id=active.id,
            app_short_hash=short_hash(active.content_hash),
            app_profile=active.profile,
        )

    async def resolve_conflict(
        self, row_id: int, *, keep: Literal["app", "machine"], content_hash: str
    ) -> BoardRow:
        """A person's choice in a conflict. Sends nothing to the machine.

        ``machine``: the machine's version becomes the active one and the profile stands on that
        file; nothing will be pushed. ``app``: that exact machine content is overruled (so the
        same file is not flagged again) and the next sync replaces the file with the active
        version, its removal guarded by a fresh load against that content. Refused when there is
        no conflict, or when the machine's content is no longer the one the person saw.
        """
        async with self.db.transaction():
            row = await self._live_row(row_id)
            machine = await self._mirror_conflict(row)
            if machine is None:
                raise Conflict(
                    f"{row.label} has no conflict to resolve.", details={"reason": "no_conflict"}
                )
            if machine.content_hash != content_hash:
                raise Conflict(
                    "The machine's file changed since you looked at it; look at it again.",
                    details={"reason": "stale_conflict"},
                )
            await self.board.add_version(row.id, machine.id, "edited_on_machine")
            if keep == "app":
                updated = await self.board.update(
                    row.id, BoardRowPatch(conflict_overruled_hash=machine.content_hash)
                )
            else:
                if machine.label != row.label:
                    holder = await self.board.find_live_by_label(machine.label, excluding=row.id)
                    if holder is not None:
                        raise Conflict(
                            f"The list already has {holder.label}; rename or switch off that one "
                            "first.",
                            details={"reason": "duplicate_label"},
                        )
                updated = await self.board.update(
                    row.id,
                    BoardRowPatch(
                        label=machine.label,
                        current_version_id=machine.id,
                        previous_version_id=row.current_version_id,
                        device_version_id=machine.id,
                        conflict_overruled_hash=None,
                        failed_version_id=None,
                        back_from_version_id=None,
                        back_from_set_version_id=None,
                        pending_draft_id=None,
                        pending_set_id=None,
                        pending_major=None,
                    ),
                )
            assert updated is not None
            return updated

    async def _proposals(self, landings: list[DraftLanding]) -> list[BoardProposal]:
        """Open drafts that are not yet a version of any profile, each placed by its landing.

        A draft that lands on a profile is a **proposed version** of it; one that lands on none
        is a proposed new profile. A draft whose document is already one of a profile's
        versions is not a proposal (the version is listed), and neither is one with no document.
        The landing is the one a put runs (``_lineage_row``), a Set draft's by its Set.
        """
        found: list[BoardProposal] = []
        by_draft = {landing.draft_id: landing for landing in landings}
        for draft in await self.drafts.list_drafts(open_only=True, limit=200):
            if draft.status not in ("draft", "approved") or draft.draft_version_id is None:
                continue
            landing = by_draft.get(draft.id)
            if landing is None or landing.already_on_board_label is not None:
                continue
            if await self.board.find_live_by_listed_version(draft.draft_version_id) is not None:
                continue
            target = landing.for_set if landing.for_set is not None else landing.plain
            found.append(BoardProposal(draft=draft, row_id=target.row_id, landing=landing))
        return found

    # ── a profile's versions ─────────────────────────────────────────

    async def versions(self, row_id: int) -> ProfileVersionsView:
        """A profile's versions, newest first, and the proposals that would join them.

        Reads the archive's mirror for which version a file on the machine holds. Each version
        names the one before it in the list so a page can diff it; the first has none.
        """
        row = await self._live_row(row_id)
        entries = await self.board.list_versions(row.id)
        by_id = {e.version_id: e for e in entries}
        stored = {}
        for entry in entries:
            found = await self.profiles.get_version(entry.version_id)
            if found is not None:
                stored[entry.version_id] = found
        on_machine = {d.current_version_id for d in await self.profiles.list_device_profiles()}
        brews = await self.sets.current_brews()
        counts = await self.board.shot_counts(list(stored))
        ordered = [e for e in entries if e.version_id in stored]
        listed: list[ListedVersion] = []
        for position, entry in enumerate(ordered):
            version = stored[entry.version_id]
            older = ordered[position + 1] if position + 1 < len(ordered) else None
            listed.append(
                ListedVersion(
                    version_id=version.id,
                    short_hash=short_hash(version.content_hash),
                    label=version.label,
                    type=version.type,
                    created_at=version.created_at,
                    added_at=by_id[version.id].added_at,
                    source=by_id[version.id].source,
                    is_active=version.id == row.current_version_id,
                    is_on_machine=version.id in on_machine,
                    did_not_verify=row.failed_version_id == version.id,
                    shots_brewed=counts.get(version.id, 0),
                    sets_brewing=_sets_brewing(brews, {version.id}, None),
                    profile=version.profile,
                    previous_version_id=None if older is None else older.version_id,
                )
            )
        landings = await self._landings()
        proposed: list[ProposedVersion] = []
        for proposal in await self._proposals(landings):
            if proposal.row_id != row.id or proposal.draft.draft_version_id is None:
                continue
            document = await self.profiles.get_version(proposal.draft.draft_version_id)
            if document is None:  # pragma: no cover - a foreign key guarantees it
                continue
            proposed.append(
                ProposedVersion(
                    draft=proposal.draft,
                    profile=document.profile,
                    compared_to_version_id=row.current_version_id,
                )
            )
        return ProfileVersionsView(
            row_id=row.id,
            label=row.label,
            on_machine=row.on_machine,
            active_version_id=row.current_version_id,
            versions=listed,
            proposed=proposed,
        )

    async def _landings(self) -> list[DraftLanding]:
        """Where each waiting draft (drafted or approved) would land, by the code a put runs.

        The same ``_destination`` ``put_draft`` calls, never a second implementation, so the
        page cannot offer a put the server would place somewhere else or refuse.
        """
        found: list[DraftLanding] = []
        # 200 is the most the drafts route can return and twice what the page asks for (100), so a
        # draft the page shows always has a landing here and never waits for one for ever.
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
        # Every proposal is an independent candidate: making another one active never blocks or
        # undoes this one, so there is nothing to say about newer drafts.
        return BoardLanding(row_id=row.id, row_label=row.label)

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
                raise Conflict(f"A {draft.status} proposal cannot be made active.")
            changes = draft.stop_condition_changes or []
            if changes and not (draft.acknowledged_stop_changes or acknowledge_stop_changes):
                raise Conflict(
                    "This draft changes when the machine stops pumping, which changes how much "
                    "coffee ends up in the cup. Make it active again with "
                    "acknowledge_stop_changes to confirm you meant that.",
                    details={
                        "field": "acknowledge_stop_changes",
                        "stop_condition_changes": changes,
                    },
                )
            if draft.draft_version_id is None:
                raise Conflict("That proposal has no document to make active")
            version = await self.profiles.get_version(draft.draft_version_id)
            if version is None:  # pragma: no cover - a foreign key guarantees it
                raise NotFound(f"No profile version {draft.draft_version_id}")
            if await self.board.find_live_by_version(version.id) is not None:
                raise Conflict("That profile version is already in the list.")

            dest = await self._destination(draft, version, set_id)
            if dest.taken is not None:
                raise Conflict(
                    f"The list already has a profile called {dest.taken}. Open it and use Edit a "
                    "copy on one of its versions to make the change there, or decline this "
                    "proposal.",
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
            source = version_source_for_draft(draft.model_dump())
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
                    await self.board.add_version(back.id, version.id, source)
                    return back
                return await self.board.insert(
                    BoardRowWrite(
                        label=version.label,
                        current_version_id=version.id,
                        origin="draft",
                        pending_draft_id=draft.id,
                        pending_set_id=set_id,
                        pending_major=major,
                        version_source=source,
                    )
                )
            updated = await self.board.update(
                row.id,
                BoardRowPatch(
                    label=version.label,
                    current_version_id=version.id,
                    # What the row was, so a person can go back to it.
                    previous_version_id=row.current_version_id,
                    back_from_version_id=None,
                    back_from_set_version_id=None,
                    failed_version_id=None,
                    **pending.model_dump(exclude_unset=True),
                ),
            )
            assert updated is not None  # the row was read in this transaction
            await self.board.add_version(updated.id, version.id, source)
            return updated

    async def _lineage_row(
        self, draft: ProfileDraftRow, version: ProfileVersionRow, set_id: int | None
    ) -> BoardRow | None:
        """The live profile a put of this draft continues: ``lineage_owner``, over the live list."""
        base = await self.profiles.get_version(draft.base_version_id)
        return await lineage_owner(
            _LiveLineage(self),
            set_id=set_id,
            base_label=None if base is None else base.label,
            version_label=version.label,
            is_new=draft.is_new,
            base_version_id=draft.base_version_id,
            base_device_profile_id=draft.base_device_profile_id,
            target_id=draft.target_board_id,
        )

    async def resume(self) -> None:
        """Let the next sync write again after it paused for a suspected machine reset.

        A person's decision, reached only from its route. It writes to the archive and sends
        nothing to the machine; the next sync then does what the page showed.
        """
        await self.board.resume()

    async def _saved_by_the_app(
        self, device_id: str, label: str, content_hash: str, *, host: str
    ) -> bool:
        """Whether a machine file is this app's own save, still exactly as it was saved.

        The first adoption's rule: the app label, an ``ok`` save of that id on this host, and
        content equal to what that save sent. It only sets a profile's ``origin``, which is
        information: a sync no longer treats the app's profiles and the person's differently.
        """
        return label.rstrip().endswith(
            APP_PROFILE_SUFFIX.strip()
        ) and await self.writes.saved_with_content(device_id, host=host, content_hash=content_hash)

    async def set_on_machine(self, row_id: int, on: bool) -> BoardRow:
        """Switch a profile on or off the machine. The next sync follows; nothing is sent now."""
        row = await self._live_row(row_id)
        updated = await self.board.update(row.id, BoardRowPatch(on_machine=on))
        assert updated is not None
        return updated

    async def set_active_version(self, row_id: int, version_id: int) -> BoardRow:
        """Make one of a profile's versions its active one. The next sync puts it on the machine.

        Any version the profile has had will do, an older one included (the sync pushes it and
        removes the file the profile stood on). It records nothing on any Set: only a proposal
        made active through ``put_draft`` does that. Refused, each with its own sentence, for a
        version that is not this profile's, the synthetic baseline, a version outside the safety
        bounds as they are now, and a name another profile already has. Making the version that
        is already active active again clears a failed verification, so the sync tries it again.
        """
        async with self.db.transaction():
            row = await self._live_row(row_id)
            version = await self.profiles.get_version(version_id)
            if version is None:
                raise NotFound(f"No profile version {version_id}")
            if version.label == SYNTHETIC_BASE_LABEL:
                raise Conflict(
                    "That is the empty baseline new drafts are compared with, not a version of a "
                    "profile.",
                    details={"reason": "synthetic_base"},
                )
            if await self.board.get_version_entry(row.id, version.id) is None:
                owner = await self.board.find_live_by_listed_version(version.id)
                raise Conflict(
                    f"That version belongs to {owner.label}, not to {row.label}."
                    if owner is not None
                    else f"That is not a version of {row.label}.",
                    details={"reason": "other_profile" if owner is not None else "not_listed"},
                )
            if version.id == row.current_version_id:
                if row.failed_version_id is None:
                    return row
                cleared = await self.board.update(row.id, BoardRowPatch(failed_version_id=None))
                assert cleared is not None
                return cleared
            refusal = await self.policy_refusal(version)
            if refusal is not None:
                raise Conflict(
                    f"That version cannot go on the machine: {refusal}.",
                    details={"reason": "policy"},
                )
            if version.label != row.label:
                holder = await self.board.find_live_by_label(version.label, excluding=row.id)
                if holder is not None:
                    raise Conflict(
                        f"The list already has {holder.label}; rename or switch off that one "
                        f"before making this version of {row.label} active.",
                        details={"reason": "duplicate_label"},
                    )
            pending = (
                None
                if row.pending_draft_id is None
                else await self.drafts.get(row.pending_draft_id)
            )
            # What was waiting for the sync to record is kept only when it is this very version.
            keep_pending = pending is not None and pending.draft_version_id == version.id
            extra = (
                {}
                if keep_pending
                else {"pending_draft_id": None, "pending_set_id": None, "pending_major": None}
            )
            patch = BoardRowPatch(
                label=version.label,
                current_version_id=version.id,
                previous_version_id=row.current_version_id,
                back_from_version_id=None,
                back_from_set_version_id=None,
                failed_version_id=None,
                **extra,
            )
            updated = await self.board.update(row.id, patch)
            assert updated is not None
            return updated

    async def set_home_screen(self, row_id: int, on: bool) -> BoardRow:
        row = await self._live_row(row_id)
        updated = await self.board.update(row.id, BoardRowPatch(on_home_screen=on))
        assert updated is not None
        return updated

    async def _live_row(self, row_id: int) -> BoardRow:
        row = await self.board.get(row_id)
        if row is None or row.deleted_at is not None:
            raise NotFound(f"No profile {row_id} in the list")
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
            self._fail(update, summary, "the machine could not be read for the profile list", exc)
        except Exception as exc:
            log.error("board_phase_crashed", exc_info=True)
            update.errors += 1
            update.error = update.error or f"{type(exc).__name__}: {exc}"
            summary.failures.append(
                BoardSummaryItem(label="profile list", reason="crashed", detail=str(exc))
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
            BoardSummaryItem(label="profile list", reason=step, detail=str(exc))
        )
        log.info("board_phase_failed", step=step, error=str(exc))

    async def _run(
        self, client: GaggimateClient, run_id: int, update: SyncRunUpdate, summary: BoardRunSummary
    ) -> None:
        machine = await read_machine(client)
        phase = _Phase(client, run_id, update, summary, machine, client.host)
        if await self.board.adoption() is None:
            await self._adopt(phase)
            return
        computed = await self.plans.compute(machine, host=phase.host)
        if computed.paused:
            # Nothing is pushed or removed, and the pause outlives this sync: a person
            # resumes it, since a reset machine and a person who deleted every file by hand
            # look the same and only the person knows which it is.
            await self.board.pause(computed.paused)
            summary.paused = computed.paused
            await self._event(phase, EVENT_PAUSED, computed.paused, None, None)
            return
        await self._join(phase, computed)
        phase.held = {p.row.id: p.held for p in computed.rows if p.held is not None}

        # Profiles that are on first, so a replacement is saved before anything is removed,
        # then the ones switched off, then the deleted ones.
        for plan in computed.rows:
            if phase.failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                break
            if plan.row.on_machine:
                await self._apply_row(phase, plan)
        for plan in computed.rows:
            if phase.failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                break
            if not plan.row.on_machine:
                await self._apply_off(phase, plan)
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
            if (
                plan.home is not None
                and plan.home.device_id is not None
                and plan.row.id not in phase.fresh
            ):
                await self._apply_home(phase, plan.home)
        await self.board.settle_resume()

    # ── files the app has never seen ─────────────────────────────────

    async def _adopt(self, phase: _Phase) -> None:
        """The first sync with the switch on: take the machine's files into the list. No writes."""
        if not phase.machine.profiles:
            # The firmware creates a Default profile on an empty filesystem, so an empty
            # list is a failed read; adopting nothing would leave the list empty for good.
            phase.summary.failures.append(
                BoardSummaryItem(
                    label="profile list",
                    reason="adoption",
                    detail="the machine listed no profiles, so nothing was adopted",
                )
            )
            phase.update.errors += 1
            phase.update.error = phase.update.error or "the machine listed no profiles"
            return
        async with self.db.transaction():
            computed = await self.plans.compute(phase.machine, host=phase.host, assume_adopted=True)
            await self._join(phase, computed, first_pull=True)
            await self.board.mark_adopted(phase.host)
        await self._event(
            phase,
            EVENT_ADOPTED,
            f"Took {len(phase.summary.adopted)} profile(s) from the machine into the list.",
            None,
            {"count": len(phase.summary.adopted)},
        )

    async def _join(self, phase: _Phase, computed: Computed, *, first_pull: bool = False) -> None:
        """Take each file the app has never seen into the list. Writes nothing to the machine.

        A file belongs to the profile its content or its name says (it is attached to it: the
        profile now stands on it) and otherwise becomes a new profile that is on, starred as the
        machine has it. Either way the profile is left alone for the rest of this sync, so the
        sync that finds a file never removes or replaces it.
        """
        for action in computed.adopt:
            device_id = action.device_id or ""
            profile = phase.machine.profiles.get(device_id)
            if profile is None:  # pragma: no cover - the plan read it from this machine
                continue
            try:
                row = await self._join_one(phase, action, profile)
            except sqlite3.IntegrityError:
                # A row stood on the file in the meantime (the unique index): nothing to join.
                continue
            phase.fresh.add(row.id)
            phase.summary.adopted.append(
                BoardSummaryItem(
                    row_id=row.id,
                    label=row.label,
                    device_id=device_id,
                    reason="first_pull" if first_pull else action.reason,
                    detail=action.detail,
                    on=profile.favorite,
                )
            )
            if not first_pull:
                await self._event(
                    phase,
                    EVENT_JOINED,
                    f"{device_id} ({profile.label}) "
                    + (
                        f"was attached to {row.label}."
                        if action.reason in ("attached", "conflict")
                        else "joined the list as a new profile."
                    ),
                    device_id,
                    {"row_id": row.id, "reason": action.reason},
                )

    async def _join_one(self, phase: _Phase, action: BoardAction, profile: Profile) -> BoardRow:
        device_id = action.device_id or ""
        version, _ = await self.profiles.ensure_version(profile)
        if action.reason in ("attached", "conflict") and action.row_id is not None:
            row = await self.board.get(action.row_id)
            assert row is not None  # the plan read it a moment ago
            await self.board.add_version(row.id, version.id, "edited_on_machine")
            # A conflict's file is stood on without being recorded as what the profile last held
            # there, so the difference stays a difference until a person chooses.
            updated = await self.board.update(
                row.id,
                BoardRowPatch(
                    device_profile_id=device_id,
                    device_version_id=None if action.reason == "conflict" else version.id,
                ),
            )
            assert updated is not None
            return updated
        # A file this app saved itself is the app's own profile (information only).
        ours = await self._saved_by_the_app(
            device_id, profile.label, version.content_hash, host=phase.host
        )
        return await self.board.insert(
            BoardRowWrite(
                label=version.label,
                current_version_id=version.id,
                device_profile_id=device_id,
                device_version_id=version.id,
                on_machine=True,
                on_home_screen=profile.favorite,
                origin="draft" if ours else "adopted",
                version_source="machine",
            )
        )

    async def _record_edit(self, phase: _Phase, row: BoardRow, device_id: str) -> None:
        """A file somebody edited on the display: keep what it holds as a version of its profile.

        Done before anything is replaced or removed, so nothing the person made on the display
        is lost, and so the removal that may follow is guarded by exactly what is recorded.
        """
        served = phase.machine.profiles.get(device_id)
        if served is None:  # pragma: no cover - the plan read it from this machine
            return
        version, _ = await self.profiles.ensure_version(
            served, device_json=json.dumps(served.to_device())
        )
        added = await self.board.add_version(row.id, version.id, "edited_on_machine")
        await self.board.update(row.id, BoardRowPatch(device_version_id=version.id))
        phase.summary.recorded.append(
            BoardSummaryItem(
                row_id=row.id,
                label=row.label,
                device_id=device_id,
                reason="edited_on_machine",
                detail=(
                    "what the file holds is now a version of this profile"
                    if added
                    else "what the file holds is a version this profile has had"
                ),
            )
        )
        if added:
            await self._event(
                phase,
                EVENT_RECORDED,
                f"{device_id} ({row.label}) was edited on the machine; what it holds is kept as "
                "a version of the profile.",
                device_id,
                {"row_id": row.id, "version_id": version.id},
            )

    async def _conflict(self, phase: _Phase, plan: RowPlan) -> None:
        """A profile whose file differs from everything the app knows: keep the machine's content as
        a version (so nothing is lost), do nothing else for the profile, and say so."""
        row, device_id = plan.row, plan.conflict_file or ""
        served = phase.machine.profiles.get(device_id)
        if served is not None:
            version, _ = await self.profiles.ensure_version(
                served, device_json=json.dumps(served.to_device())
            )
            await self.board.add_version(row.id, version.id, "edited_on_machine")
        phase.summary.conflicts.append(
            BoardSummaryItem(
                row_id=row.id,
                label=row.label,
                device_id=device_id,
                reason="conflict",
                detail=CONFLICT,
            )
        )
        await self._event(
            phase,
            EVENT_CONFLICT,
            f"{row.label} ({device_id}) was changed outside the app; nothing is done for it until "
            "you choose which version to keep.",
            device_id,
            {"row_id": row.id},
        )

    # ── one live row ─────────────────────────────────────────────────

    async def _apply_row(self, phase: _Phase, plan: RowPlan) -> None:
        """A profile that is on: put its active version on the machine, retire what it replaces."""
        row = plan.row
        if plan.attached is not None:
            return  # joined this sync: recorded, and otherwise left alone until the next one
        if plan.conflict_file is not None:
            await self._conflict(phase, plan)
            return
        if plan.report is not None:
            await self._report(phase, plan.report)
            return
        if plan.edited_file is not None:
            await self._record_edit(phase, row, plan.edited_file)
        new_id = plan.held
        placed: Placed | None = None
        if plan.push is not None:
            placed = await self._push(phase, plan)
            if placed is None:
                return  # old stays on the machine, the row stands where it was
            new_id = placed.device_id
            phase.held[row.id] = new_id
        assert new_id is not None  # a row either holds its content or has just pushed it

        settled = True
        removal: Removal | None = None
        if plan.pred is not None and plan.pred.device_id is not None:
            if plan.pred.kind == "leave":
                await self._left(phase, plan.pred)
                settled = False
            else:
                removal = await self._remove(
                    phase,
                    plan.pred,
                    expected_hash=plan.pred_hash,
                    successor=new_id,
                    row=plan.row,
                )
                settled = _is_settled(removal)
        if settled and (
            row.device_profile_id != new_id or row.device_version_id != plan.version.id
        ):
            await self.board.update(
                row.id,
                BoardRowPatch(device_profile_id=new_id, device_version_id=plan.version.id),
            )
        if row.back_from_version_id is not None and settled:
            # The file was dealt with: the going back is over.
            await self.board.update(
                row.id,
                BoardRowPatch(back_from_version_id=None, back_from_set_version_id=None),
            )
        if placed is not None and placed.served is not None:
            favorite = placed.served.favorite or bool(removal and removal.favorite_carried)
            selected = placed.served.selected or bool(removal and removal.selected_carried)
            await self._mirror(new_id, placed.served, favorite=favorite, selected=selected)
        await self._record(
            phase, plan, new_id, saved=placed is not None and not placed.reused, removal=removal
        )

    async def _apply_off(self, phase: _Phase, plan: RowPlan) -> None:
        """A profile that is off: take its file off the machine, once the selection has moved."""
        row = plan.row
        if plan.attached is not None:
            return
        if plan.conflict_file is not None:
            await self._conflict(phase, plan)
            return
        if plan.report is not None:
            await self._report(phase, plan.report)
            return
        if plan.edited_file is not None:
            await self._record_edit(phase, row, plan.edited_file)
        action = plan.pred
        if action is None:
            if (
                row.device_profile_id is not None
                and row.device_profile_id not in phase.machine.profiles
            ):
                # The file is not on the machine any more: the row lets go of it.
                await self.board.update(
                    row.id, BoardRowPatch(device_profile_id=None, device_version_id=None)
                )
            return
        if action.kind == "leave":
            await self._left(phase, action)
            return
        removal = await self._remove(
            phase,
            action,
            expected_hash=plan.pred_hash,
            successor=phase.held.get(plan.successor_row_id) if plan.successor_row_id else None,
            row=row,
            carry_favorite=False,
        )
        if _is_settled(removal):
            await self.board.update(
                row.id, BoardRowPatch(device_profile_id=None, device_version_id=None)
            )

    async def _fresh(self, row: BoardRow) -> BoardRow:
        return await self.board.get(row.id) or row

    async def _report(self, phase: _Phase, action: BoardAction) -> None:
        """Say what is wrong with a row the sync will not touch."""
        item = BoardSummaryItem(
            row_id=action.row_id,
            label=action.label,
            device_id=action.device_id,
            reason=action.reason,
            detail=action.detail,
        )
        if action.reason in ("unreadable", "policy"):
            # Said as failures (the run is an error): the person must look. Neither is the
            # machine's fault, so they do not count toward the three-in-a-row stop.
            await self._row_failed(
                phase, action, action.reason, action.detail, counts_as_device_failure=False
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
        refusal = await self.policy_refusal(plan.version)
        if refusal is not None:
            await self._row_failed(phase, action, "policy", refusal, counts_as_device_failure=False)
            return None
        clamped = profile_from_version(plan.version)
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
            # Removed against what the machine stored (the copy that did not verify is the
            # only thing the check can match); a copy that cannot even be read back is not
            # one a guarded delete can be made of.
            if placed.served is not None:
                cleanup = await remove_profile(
                    phase.client,
                    device_id=placed.device_id,
                    expected_hash=profile_content_hash(placed.served),
                    successor=None,
                )
            else:
                cleanup = Removal(reason="could not be read back")
            phase.summary.writes += 1 if cleanup.removed else 0
            if cleanup.removed:
                phase.machine.profiles.pop(placed.device_id, None)
                await self.profiles.mark_one_deleted(placed.device_id)
            # Not tried again until another version is made active (or this one is made active
            # again, which asks for a retry): a firmware that cannot round-trip this version
            # would otherwise be written to and cleaned up again on every sync, whether or not
            # the bad copy could be taken off.
            await self.board.update(plan.row.id, BoardRowPatch(failed_version_id=plan.version.id))
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
        return placed

    async def _remove(
        self,
        phase: _Phase,
        action: BoardAction,
        *,
        expected_hash: str | None,
        successor: str | None,
        row: BoardRow,
        carry_favorite: bool = True,
    ) -> Removal:
        """Remove a file through the one guard and say what happened.

        What the file must still hold is what the archive recorded for it, never what the plan
        read at some other moment. And who stands on it is asked again now: another live profile
        keeps it, whatever the plan saw a moment ago, and so does a profile switched back on.
        """
        device_id = action.device_id or ""
        assert expected_hash is not None  # the plan sets it for every removal it makes
        blocked = None
        if action.reason == "deleted" and not await self._still_deleted(row.id):
            blocked = ANOTHER_ROW
        elif action.reason == "off" and not await self._still_off(row.id):
            blocked = "switched back on since the plan was made"
        elif await self.board.other_live_on_device(device_id, excluding=row.id) is not None:
            blocked = ANOTHER_ROW
        removal = await remove_profile(
            phase.client,
            device_id=device_id,
            expected_hash=expected_hash,
            successor=successor,
            blocked=blocked,
            carry_favorite=carry_favorite,
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
            if action.reason == "off":
                await self._retire(row, device_id, successor, "switched_off")
            elif row.pending_draft_id is not None:
                await self.drafts.supersede_pushed(device_id, by_draft_id=row.pending_draft_id)
            elif row.deleted_at is not None or (
                row.back_from_version_id is not None
                and row.device_version_id == row.back_from_version_id
            ):
                await self._retire(
                    row,
                    device_id,
                    successor,
                    "deleted" if row.deleted_at is not None else "went_back",
                )
            else:
                # A replacement that had to wait (the earlier copy stayed for a moment): the
                # draft that put the current version on the machine replaces the draft behind
                # this file.
                newer = await self.drafts.pusher_of_version(row.current_version_id)
                if newer is not None:
                    await self.drafts.supersede_pushed(device_id, by_draft_id=newer)
                else:
                    # Nothing newer stands behind the profile: the copy is off the machine, so
                    # its draft is closed as a delete closes it.
                    await self._retire(row, device_id, successor, "removed")
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
        elif _is_asked_again(removal):
            await self._left(
                phase, action.model_copy(update={"kind": "leave", "detail": removal.reason})
            )
        else:
            # The machine could not be asked, or refused for a reason that may pass: the row
            # keeps standing on the file and the next sync tries again.
            await self._row_failed(phase, action, "remove", removal.reason, device_id=device_id)
        return removal

    async def _retire(
        self,
        row: BoardRow,
        device_id: str,
        successor: str | None,
        how: Literal["went_back", "deleted", "removed", "switched_off"],
    ) -> None:
        """Close the record of a file removed without a newer draft taking its place.

        Going back to a previous version, or deleting a profile, takes a pushed draft's file
        off the machine and no later push replaces it. What a rollback did: the draft that
        pushed it is ``discarded`` and stops naming the file. And for going back, the Set
        versions that named the copy this one replaced (cleared when it was replaced) name the
        copy that has just been put back, so the history says where each of them is again.
        ``removed`` is the same closing for a copy whose removal had to wait (a Set was brewing
        it) and found nothing newer behind the profile.
        """
        words = {
            "went_back": f"Went back to the previous version: {device_id} is off the machine.",
            "deleted": f"The profile was deleted: {device_id} is off the machine.",
            "removed": f"{device_id} is off the machine and nothing newer stands behind it.",
            "switched_off": f"The profile was switched off: {device_id} is off the machine.",
        }
        found = await self.drafts.retire_pushed(
            device_id,
            outcome={
                "action": how,
                "removed_device_profile_id": device_id,
                "lines": [words[how]],
            },
        )
        if how != "went_back" or successor is None:
            return
        for draft in found:
            if (
                draft.replaced_version_id == row.current_version_id
                and draft.cleared_set_version_ids
            ):
                await self.sets.restore_pushed_device_profile(
                    [int(i) for i in draft.cleared_set_version_ids], successor
                )

    async def _still_off(self, row_id: int) -> bool:
        """Asked again at the moment of the delete: a profile switched back on keeps its file."""
        fresh = await self.board.get(row_id)
        return fresh is not None and fresh.deleted_at is None and not fresh.on_machine

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
        if deleted.edited_file is not None:
            await self._record_edit(phase, row, deleted.edited_file)
        if action.kind == "leave":
            await self._left(phase, action)
            return
        removal = await self._remove(
            phase,
            action,
            expected_hash=deleted.expected_hash,
            successor=phase.held.get(deleted.successor_row_id)
            if deleted.successor_row_id
            else None,
            row=row,
            carry_favorite=False,
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
        lines = [f"Put on the machine by a sync as {device_id}."]
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
    "deleted": "the profile was deleted",
    "off": "the profile is switched off",
}


def _sets_brewing(
    brews: list[tuple[int, str, int | None, str | None]],
    version_ids: set[int],
    device_id: str | None,
) -> list[SetBrewing]:
    """The Sets whose current version brews one of these versions, or the file given."""
    return [
        SetBrewing(set_id=set_id, name=name)
        for set_id, name, version_id, file in brews
        if (version_id is not None and version_id in version_ids)
        or (device_id is not None and file == device_id)
    ]


class _LiveLineage:
    """The lineage rule's questions, answered from the live list and the Sets."""

    def __init__(self, service: BoardService) -> None:
        self.service = service

    async def by_id(self, profile_id: int) -> BoardRow | None:
        row = await self.service.board.get(profile_id)
        return None if row is None or row.deleted_at is not None else row

    async def by_set(self, set_id: int) -> BoardRow | None:
        service = self.service
        current = await service.sets.current_version(set_id)
        if current is None or current.profile_version_id is None:
            return None
        found = await service.sets.current_device_profile(set_id)
        row = None
        if found is not None:
            row = await service.board.find_live_by_device(found[0])
        if row is None:
            row = await service.board.find_live_by_version(current.profile_version_id)
        return row

    async def by_version(self, version_id: int) -> BoardRow | None:
        # By the profile's version list, not only the version it is on now: a proposal based on
        # an older version (or one that was never pushed) still belongs to its profile.
        return await self.service.board.find_live_by_listed_version(version_id)

    async def by_label(self, label: str) -> BoardRow | None:
        return await self.service.board.find_live_by_label(label)

    def label_of(self, profile: BoardRow) -> str:
        return profile.label

    async def by_device(self, device_id: str) -> BoardRow | None:
        return await self.service.board.find_live_by_device(device_id)

    def is_app_made(self, profile: BoardRow) -> bool:
        """A new version only continues a profile the app itself pushed; a profile the person
        made stays as it was, and a draft of it is a profile of its own beside it."""
        return profile.origin == "draft"


def _is_settled(removal: Removal) -> bool:
    """The row may let go of the file: it is gone.

    Another profile standing on it, a profile that is selected with nothing to take over, a file
    that changed since it was recorded (the next sync records it and decides) and a machine that
    did not answer are not final: the row keeps the file and the next sync asks again.
    """
    return removal.removed or removal.gone


def _is_asked_again(removal: Removal) -> bool:
    """A refusal that is reported, not a failure, and repeats until what causes it passes."""
    return removal.reason in (
        ANOTHER_ROW,
        NO_SUCCESSOR,
        CHANGED_SINCE,
    ) or removal.reason.startswith("switched back on")


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
