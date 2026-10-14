"""The app's profile list, and the write phase of a sync that makes the machine match it.

Two halves with one rule between them.

**Editing the list never touches the machine.** Switching a profile on or off the machine,
starring it, making one of its versions active and putting an approved draft on the list change
rows in the archive and nothing else; they work with the writes switch off, and with no machine
at all.

**The write phase is the one automatic write this app makes**, and it runs only when the
`deviceWritesEnabled` switch is on, at the end of the profile pass of a sync, inside the sync
engine's lock. It does not decide anything the plan has not (:mod:`.board_plan`) and it has
no machine path of its own: every save is :func:`~gaggiclanker.drafts.machine.place` (a profile
with no file) or :func:`~gaggiclanker.drafts.machine.overwrite` (a profile's own file), every
removal :func:`~gaggiclanker.drafts.machine.remove_profile`, so the audit, the write gate and the
guard (a fresh load must hold exactly the content the archive recorded) apply as they do to a
person's push.

What a sync does, in order:

1. **The first sync with the switch on** takes the machine's files into the list by the
   rules below and writes nothing; the sync ends there.
2. **Files it has never seen** (no profile holds their id) join the list as new profiles that
   are on (see :mod:`.board_plan`). Nothing is removed or replaced in the sync that finds a file.
3. **Per profile that is on**: save its active version when its file does not hold it (the safety
   policy runs again here, the read-back too). A profile that has a file is saved over that
   file, in place, after a fresh load shows exactly what the archive recorded for it, so its id,
   its star, its selection and the startup setting stay; the machine's shots are pulled first,
   so a shot brewed on the old content is never linked to the new. A profile with no file gets
   a new one. A read-back that does not match puts the recorded document back, records the
   failure, and the version is not tried again until another is made active. A profile in
   conflict (a file holding content it never had) is skipped entirely; a file holding an older
   version of it is recorded and handled as usual.
4. **Per profile that is off**: remove its file (the selection moves first to the first profile
   that is on), after the same fresh-load check.
5. **Per deleted row**: remove its file the same way.
6. **Stars**, only for profiles that are on, from a fresh read when anything was written above.

A save over a file is one frame, so a sync that stops halfway leaves every profile old or new.
Failures are values (events, the run's summary, an error count), never an exception out of the
sync.
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
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.lineage import (
    destination_now,
    person_taken_sentence,
    place_draft,
    taken_name_sentence,
)
from gaggiclanker.db.repos.pins import PinDetail, PinsRepository
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
    document_for,
    stored_document,
)
from gaggiclanker.db.repos.sets import LiveSet, RollbackWrite, SetsRepository, VersionWriteResult
from gaggiclanker.db.repos.sync import SyncRepository, SyncRunUpdate
from gaggiclanker.db.repos.version_names import next_names
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.domain.models import Profile, document_hash
from gaggiclanker.domain.profile_policy import (
    ProfileRejected,
    clamp,
    enforce,
    outside_bounds_sentence,
)
from gaggiclanker.drafts.board_plan import (
    ANOTHER_ROW,
    CONFLICT,
    BoardAction,
    BoardPlan,
    Computed,
    DeletedPlan,
    PlanBuilder,
    RowPlan,
    judge_file,
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
from gaggiclanker.drafts.live_lineage import LiveLineage
from gaggiclanker.drafts.machine import (
    CHANGED_SINCE,
    NO_SUCCESSOR,
    FileContent,
    MachineState,
    Placed,
    Removal,
    overwrite,
    place,
    read_machine,
    remove_profile,
)
from gaggiclanker.drafts.proposals import (
    DraftProposals,
    PinFrozen,
    PinnedCopyName,
    TakenName,
    schema_errors,
)
from gaggiclanker.drafts.standing import RowFacts, StandingState, classify
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.signatures.service import SignatureService

__all__ = [
    "BoardMachineState",
    "BoardRowPin",
    "BoardRowView",
    "BoardRunSummary",
    "BoardService",
    "BoardSummaryItem",
    "BoardView",
    "DraftStanding",
    "NameCheck",
    "RenamedOnMachine",
    "machine_from_mirror",
]

log = structlog.get_logger(__name__)

#: The sync event kinds this phase writes, one per action, so the Device page can name each.
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
#: A profile took the name somebody gave its file at the machine (old and new name, row id).
EVENT_RENAMED_ON_MACHINE = "profile_renamed_on_machine"

#: Three failed profiles in a row mean the machine has gone, not that three profiles are bad.
MAX_CONSECUTIVE_FAILURES = 3


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
    """What one write phase did. Stored on the run row and shown on the Device page."""

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
    #: Profiles that took the name their file was given at the machine (nothing was written).
    renamed: list[BoardSummaryItem] = Field(default_factory=list)
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
        return (
            len(self.adopted)
            + len(self.renamed)
            + len(self.pushed)
            + len(self.removed)
            + len(self.home_screen)
        )


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


class BoardRowPin(BaseModel):
    """The live pin a profile-list row carries the copy of: which Set version it belongs to."""

    model_config = ConfigDict(extra="forbid")

    pin_id: int
    set_id: int
    set_name: str
    set_version_id: int
    #: The pinned version's name, "v1.2".
    version_label: str
    #: The profile the pinned version brews, the one whose signature judges the copy's shots.
    original_label: str | None = None
    original_version_id: int | None = None
    #: Shots filed under the pinned version whose profile is this copy.
    shot_count: int = 0


class RenamedOnMachine(BaseModel):
    """The note a profile carries after a sync took the name its file was given at the machine."""

    model_config = ConfigDict(extra="forbid")

    #: What the profile was called before, and when the sync that took the new name ran.
    from_name: str
    at: str


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
    #: The profile's Set (a profile has at most one, and archived Sets do not count): changing the
    #: active version gives it a version of its own, and switching the profile off changes what
    #: the machine holds for it.
    set: SetBrewing | None = None
    #: In conflict: the machine's file differs from everything the app knows, so a sync does
    #: nothing for this profile until a person chooses a side (``POST .../conflict``).
    in_conflict: bool = False
    conflict: ConflictSummary | None = None
    #: How many open drafts would become a version of this profile once made active.
    proposed_versions: int = 0
    #: Set when this row carries the frozen copy of a pinned Set version: it has one version for
    #: ever, no switch, and changes only by unpinning. ``None`` for every other profile.
    pin: BoardRowPin | None = None
    #: Set while the latest profile sync is the one that took the machine's name for this profile
    #: (and the profile still has that name): "Renamed on the machine from <from_name> at the sync
    #: of <at>". The next sync clears it.
    renamed_on_machine: RenamedOnMachine | None = None


class LandingSet(BaseModel):
    """The Set a put would give a new version: the Set of the profile it lands on."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    #: What the new version would be called as a minor and as a major (the put names it one
    #: or the other, as the person ticks).
    next_minor_label: str
    next_major_label: str


class BoardLanding(BaseModel):
    """Where a put of one draft would land: the row it continues, or a new profile."""

    model_config = ConfigDict(extra="forbid")

    #: The board row the draft would become the next version of; ``None`` for a new profile.
    row_id: int | None = None
    row_label: str | None = None
    #: A put that would bring a deleted profile back (its file still waits to be dealt with).
    revives_label: str | None = None
    #: Why a put would be refused: the draft is new or renamed and its name is already a
    #: profile (the sentence the put answers with), else ``None``.
    refused: str | None = None
    #: The Set of the profile the put lands on, which follows it with a version of its own;
    #: ``None`` when the profile has no Set (and for a new profile).
    set: LandingSet | None = None


class DraftLanding(BaseModel):
    """What putting an approved draft on the board would do."""

    model_config = ConfigDict(extra="forbid")

    draft_id: int
    #: The label of the live board profile that already holds this draft's exact document, if
    #: one does: there is nothing to put (the document is on the board), only to discard.
    already_on_board_label: str | None = None
    #: Where the put lands: found through the draft's stored destination, as the route does.
    plain: BoardLanding


class BoardProposal(BaseModel):
    """An open draft as the list shows it: a version waiting for a person."""

    model_config = ConfigDict(extra="forbid")

    draft: ProfileDraftRow
    #: The profile it would become a version of (by the landing a put runs); ``None`` when it
    #: would be a new profile.
    row_id: int | None = None
    landing: DraftLanding


class DraftStanding(BaseModel):
    """`GET /api/profile-drafts/{id}/standing`: a proposal, its documents and where it stands."""

    model_config = ConfigDict(extra="forbid")

    draft: ProfileDraftRow
    #: The proposed document, as stored.
    profile: dict[str, Any] | None = None
    #: The document of the active version of the profile it continues, while it is waiting;
    #: ``None`` once answered and for a new profile.
    active_profile: dict[str, Any] | None = None
    #: What a put would do, and whether it would be refused: while waiting, else ``None``.
    landing: DraftLanding | None = None
    state: StandingState
    #: The server's sentence for ``approved``, ``not_on_machine`` and ``replaced``.
    reason: str | None = None
    #: The profile it went to (or, while waiting, would go to).
    row_id: int | None = None
    row_label: str | None = None
    #: Whether it is the selected profile on the machine, once it is on the machine.
    selected: bool | None = None
    #: The Set version making this proposal active recorded; ``None`` while it waits, and for a
    #: proposal whose profile has no Set.
    set_version_label: str | None = None
    writes_enabled: bool


class NameCheck(BaseModel):
    """May this proposal be approved under this name? Answered for a field as it is typed."""

    model_config = ConfigDict(extra="forbid")

    #: The name as it would be stored: the typed one without surrounding whitespace.
    label: str
    #: The sentence a put under this name would be refused with, else ``None``.
    refused: str | None = None


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
class _MirrorConflict:
    """The machine's side of a conflict: the version its brew is, the name the file has, and the
    hash of the document (brew and name) the person is shown and answers about."""

    version: ProfileVersionRow
    name: str
    document_hash: str
    #: The name the app last recorded for the file, if it recorded one.
    recorded_name: str | None = None
    #: The live profile that has the machine's name (a conflict about the name alone).
    holder: BoardRow | None = None
    #: The conflict is about the name alone: the file's brew is one the profile has had.
    name_only: bool = False


@dataclass
class _Destination:
    """What a put of one draft would do: continue ``row``, bring ``revived`` back, or add."""

    row: BoardRow | None = None
    revived: BoardRow | None = None
    #: The sentence a put is refused with (a new or renamed draft whose name is taken).
    refused: str | None = None
    #: The stored name of the live profile that has the draft's name: the landing and the name
    #: check say it to the person.
    taken_label: str | None = None
    #: The put is refused because the profile it would continue is a pin's frozen copy
    #: (``refused`` is then the person's sentence about the pin).
    frozen: bool = False
    #: The put is refused because the draft's document is exactly a pin's copy, which belongs to
    #: that pin's Set version for ever (``refused`` is then the person's sentence).
    pinned_copy: PinnedCopyName | None = None


@dataclass
class _Phase:
    """The state one write phase carries from step to step."""

    client: GaggimateClient
    run_id: int
    update: SyncRunUpdate
    summary: BoardRunSummary
    machine: MachineState
    host: str
    #: The engine's shot pass, awaited before the first save over a file (see ``_pull_shots``).
    pull_shots: Callable[[], Awaitable[bool]]
    #: Whether the machine announced a shot since the last pull.
    shot_announced: Callable[[], bool]
    failures_in_a_row: int = 0
    #: ``None`` until the shots were asked for in this sync, then whether that pass completed.
    shots_pulled: bool | None = None
    #: The file each live row holds in this plan, so one row's push never reuses another's.
    held: dict[int, str] = field(default_factory=dict)
    #: The profiles that gained a file in this sync (a new profile, or a file attached): the
    #: sync that finds a file does nothing else to it, its star included.
    fresh: set[int] = field(default_factory=set)


class BoardService:
    """Edits the board (no machine) and runs the write phase (the switch on)."""

    def __init__(
        self,
        db: Database,
        settings: SettingsService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.board = ProfileBoardRepository(db)
        self.profiles = ProfilesRepository(db)
        self.signatures = SignatureService(db)
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
            profile = document_for(version, await self.profiles.name_of(version))
            _, changes = enforce(profile, await self.proposals.bounds())
        except (ProfileRejected, Unprocessable, ValidationError) as exc:
            return str(exc)
        if changes:
            return outside_bounds_sentence(changes)
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
        brews = await self.sets.live_sets_by_profile()
        counts = await self.board.shot_counts([p.version.id for p in computed.rows])
        pinned = await PinsRepository(self.db).live_for_boards(p.row.id for p in computed.rows)
        renamed = await self._renamed_on_machine()
        rows: list[BoardRowView] = []
        for plan in computed.rows:
            entry = await self.board.get_version_entry(plan.row.id, plan.version.id)
            rows.append(
                BoardRowView(
                    row=plan.row,
                    type=plan.version.type,
                    utility=plan.version.utility,
                    machine=_machine_state(plan, machine),
                    planned=[a for a in would_do if a.row_id == plan.row.id],
                    on_machine=plan.row.on_machine,
                    starred=plan.row.on_home_screen,
                    active_version=ActiveVersion(
                        version_id=plan.version.id,
                        short_hash=short_hash(plan.version.content_hash),
                        label=plan.row.label,
                        type=plan.version.type,
                        utility=plan.version.utility,
                        source=entry.source if entry is not None else "machine",
                        created_at=plan.version.created_at,
                        shots_brewed=counts.get(plan.version.id, 0),
                        profile=stored_document(plan.version, plan.row.label),
                    ),
                    set=_set_brewing(brews.get(plan.row.id)),
                    in_conflict=plan.conflict_file is not None,
                    conflict=await self._conflict_summary(plan),
                    proposed_versions=proposed_by_row.get(plan.row.id, 0),
                    pin=_row_pin(pinned.get(plan.row.id)),
                    renamed_on_machine=_renamed_note(renamed.get(plan.row.id), plan.row.label),
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

    async def _renamed_on_machine(self) -> dict[int, tuple[RenamedOnMachine, str]]:
        """What the latest profile sync took from the machine's names: per profile, the note and
        the name it took. Read from that run's ``profile_renamed_on_machine`` events, so a note
        stays until the next sync and no longer."""
        run = (await self.runs.last_runs()).get("profiles")
        if run is None:
            return {}
        found: dict[int, tuple[RenamedOnMachine, str]] = {}
        for event in await self.runs.run_events(run.id, EVENT_RENAMED_ON_MACHINE):
            try:
                data = json.loads(event.data_json or "{}")
                found[int(data["row_id"])] = (
                    RenamedOnMachine(from_name=str(data["from"]), at=event.at),
                    str(data["to"]),
                )
            except (ValueError, KeyError, TypeError):  # pragma: no cover - written by _take_name
                continue
        return found

    async def _conflict_summary(self, plan: RowPlan) -> ConflictSummary | None:
        if plan.conflict_file is None or plan.conflict_hash is None:
            return None
        stored = (
            None
            if plan.conflict_brew is None
            else await self.profiles.find_version(plan.conflict_brew, owner=plan.row.id)
        )
        return ConflictSummary(
            device_id=plan.conflict_file,
            version_id=None if stored is None else stored.id,
            content_hash=plan.conflict_hash,
            short_hash=short_hash(plan.conflict_hash),
            machine_name=plan.conflict_name or "",
            machine_name_held_by=plan.conflict_holder,
            name_only=plan.conflict_name_only,
        )

    async def _mirror_conflict(self, row: BoardRow) -> _MirrorConflict | None:
        """The machine's file of a profile when it is in conflict, from the mirror.

        The same rule the plan applies to a read of the machine (:func:`judge_file`): a file
        that is not what the profile last recorded for it, whose brew the profile never had, or
        whose name was changed on both sides, or at the machine alone to another live profile's
        name, and that a person has not already overruled.
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
        file = FileContent(version.content_hash, mirrored.label)
        document = document_hash(document_for(version, mirrored.label))
        recorded: FileContent | None = None
        if row.device_version_id is not None:
            held = await self.profiles.get_version(row.device_version_id)
            if held is not None:
                recorded = FileContent(held.content_hash, row.device_label or "")
        if file == recorded:
            return None
        judged = await judge_file(
            self.board, row, file, recorded, document, FileContent(active.content_hash, row.label)
        )
        if judged.outcome != "conflict":
            return None
        return _MirrorConflict(
            version=version,
            name=mirrored.label,
            document_hash=document,
            recorded_name=None if recorded is None else recorded.name,
            holder=judged.holder,
            name_only=judged.name_only,
        )

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
                version_id=machine.version.id,
                content_hash=machine.document_hash,
                short_hash=short_hash(machine.document_hash),
                machine_name=machine.name,
                machine_name_held_by=None if machine.holder is None else machine.holder.label,
                name_only=machine.name_only,
            ),
            machine_profile=stored_document(machine.version, machine.name),
            app_version_id=active.id,
            app_short_hash=short_hash(active.content_hash),
            app_profile=stored_document(active, row.label),
            app_name=row.label,
            machine_name=machine.name,
            recorded_name=machine.recorded_name,
            machine_name_held_by=None if machine.holder is None else machine.holder.label,
        )

    async def resolve_conflict(
        self, row_id: int, *, keep: Literal["app", "machine"], content_hash: str
    ) -> BoardRow:
        """A person's choice in a conflict. Sends nothing to the machine.

        ``machine``: the file becomes what the profile is (its brew is the active version, and
        the profile takes the file's name); nothing will be pushed, and the profile's Set follows
        (a new minor version naming it). A pin's copy is no exception: its Set's profile gets its
        own version of the machine's brew, made active the same way, and the pin then names the
        Set's new version while the copy keeps the file and the machine's version. ``app``: that
        exact machine document (brew and name) is overruled, so the same file is not flagged
        again, and the next sync replaces the file with the active version, its removal guarded
        by a fresh load against that document. Refused when there is no conflict, or when the
        machine's file is no longer the one the person saw.
        """
        async with self.db.transaction():
            row = await self._live_row(row_id)
            machine = await self._mirror_conflict(row)
            machine_active = await self.profiles.get_version(row.current_version_id)
            if machine is None or machine_active is None:
                raise Conflict(
                    f"{row.label} has no conflict to resolve.", details={"reason": "no_conflict"}
                )
            if machine.document_hash != content_hash:
                raise Conflict(
                    "The machine's file changed since you looked at it; look at it again.",
                    details={"reason": "stale_conflict"},
                )
            # A conflict about the name alone: the machine's brew is one the profile has had (the
            # one rule, :func:`judge_file`, which also says so on the board and for a rename).
            if keep == "machine" and row.device_version_id is not None and machine.name_only:
                return await self._take_the_machines_name(row, machine)
            # A brew the profile never had is recorded as a version (the file's, so nothing is
            # lost).
            kept = await self.board.version_for(row.id, machine.version.id)
            await self.board.add_version(row.id, kept, "edited_on_machine")
            if keep == "app":
                updated = await self.board.update(
                    row.id, BoardRowPatch(conflict_overruled_hash=machine.document_hash)
                )
            else:
                if machine.name != row.label:
                    holder = await self.board.find_live_by_name_key(machine.name, excluding=row.id)
                    if holder is not None:
                        raise Conflict(
                            f"The list already has {holder.label}; rename or switch off that one "
                            "first.",
                            details={"reason": "duplicate_label"},
                        )
                updated = await self.board.update(
                    row.id,
                    BoardRowPatch(
                        label=machine.name,
                        current_version_id=kept,
                        previous_version_id=row.current_version_id,
                        device_version_id=kept,
                        device_label=machine.name,
                        conflict_overruled_hash=None,
                        failed_version_id=None,
                        back_from_version_id=None,
                        pending_draft_id=None,
                    ),
                )
                assert updated is not None
                await self._set_follows(row.id, kept)
                pin = await PinsRepository(self.db).live_for_board(row.id)
                if pin is not None:
                    await self._pinned_machine_side(pin, machine, kept)
            assert updated is not None
            return updated

    async def _take_the_machines_name(self, row: BoardRow, machine: _MirrorConflict) -> BoardRow:
        """Keeping the machine's side of a conflict about the name alone writes only the name.

        The file's brew is one the profile has had, so there is no brew to adopt: the active
        version, the Set that follows it and a pin stay as they are, and the next sync decides the
        brew by its own rule (an older brew under the current name is matched and replaced).
        """
        holder = await self.board.find_live_by_name_key(machine.name, excluding=row.id)
        if holder is not None and machine.name != row.label:
            raise Conflict(
                f"The list already has {holder.label}; rename or switch off that one first.",
                details={"reason": "duplicate_label"},
            )
        updated = await self.board.update(
            row.id,
            BoardRowPatch(
                label=machine.name,
                device_version_id=row.device_version_id,
                device_label=machine.name,
                conflict_overruled_hash=None,
            ),
        )
        assert updated is not None
        return updated

    async def _pinned_machine_side(
        self, pin: PinDetail, machine: _MirrorConflict, machine_version_id: int
    ) -> None:
        """A pin's file was edited on the machine and the person kept that: the Set's profile gets
        its own version of the machine's brew (under its own name), made active like any other,
        which gives the Set a new minor version, and the pin names that Set version."""
        owner = await self.sets.get(pin.set_id)
        target = (
            None
            if owner is None or owner.board_id is None
            else await self.board.get(owner.board_id)
        )
        if target is None:  # pragma: no cover - a pin needs a Set that brews a profile
            return
        document = document_for(machine.version, target.label)
        mine, created = await self.profiles.ensure_version(document, owner=target.id)
        if created:
            await self.signatures.carry_quietly(target.current_version_id, mine.id)
        await self.board.add_version(target.id, mine.id, "edited_on_machine")
        recorded = None
        if mine.id != target.current_version_id:
            await self._activatable(target, mine.id)
            await self._make_active(target, mine)
            recorded = await self.sets.follow_profile(target.id, mine.id)
        # The pin names the machine's version (the copy row's active one now) and the Set's new
        # version, so it still names exactly one version and one Set version.
        await PinsRepository(self.db).move_to_set_version(
            pin.id, None if recorded is None else recorded.id, machine_version_id
        )

    async def name_check(self, draft_id: int, label: str) -> NameCheck:
        """Whether a put of this proposal under ``label`` would be refused, and with which sentence.

        For the Name field as it is typed. The same questions the put asks, through the same
        ``place_draft`` and with the same sentences, in the order it asks them; it writes nothing.
        Refused as a request (422) for a proposal that is not waiting, or that is a change to a
        profile (a name never changes through a version).
        """
        draft = await self.drafts.get(draft_id)
        if draft is None:
            raise NotFound(f"No profile draft {draft_id}")
        version = (
            None
            if draft.draft_version_id is None
            else await self.profiles.get_version(draft.draft_version_id)
        )
        if draft.status != "draft" or version is None:
            raise Unprocessable("That proposal is not waiting for an answer")
        landing = await self._destination(draft, version)
        if (landing.frozen or landing.pinned_copy is not None) and landing.refused is not None:
            return NameCheck(label=label.strip(), refused=landing.refused)
        if landing.row is not None:
            raise Unprocessable("A change to an existing profile keeps its name")
        name = label.strip()
        if not name:
            return NameCheck(label=name, refused="A profile needs a name")
        # The constructor's question, on the name the person typed: does a live profile have it?
        placed = await place_draft(
            LiveLineage(self.db),
            label=name,
            destination=None,
            base_version_id=None if draft.is_new else draft.base_version_id,
        )
        if placed.frozen is not None:
            return NameCheck(label=name, refused=placed.frozen)
        if placed.taken is not None:
            return NameCheck(label=name, refused=person_taken_sentence(placed.taken.label))
        # The put rebuilds the document under this name, and a document that is exactly a pin's
        # copy is refused there: say so here too, in the person's words.
        pinned = await self._pinned_copy_named(version, placed.name)
        if pinned is not None:
            return NameCheck(label=name, refused=pinned.person)
        return NameCheck(label=name)

    async def _pinned_copy_named(
        self, version: ProfileVersionRow, name: str
    ) -> PinnedCopyName | None:
        """Whether this version's document under ``name`` (as the constructor would store it,
        clamped) is the copy of a pin."""
        clamped, _ = clamp(document_for(version, name), await self.proposals.bounds())
        return await self.proposals.pinned_copy_of(clamped.for_new_device_profile(label=name))

    async def standing(self, draft_id: int, machine: MachineState, *, host: str) -> DraftStanding:
        """Where one proposal stands, whether it is waiting or answered. Reads the archive only.

        ``machine`` is the mirror (or a read the caller made): nothing here asks the machine
        anything or writes anything. The state is :func:`~.standing.classify`'s.
        """
        draft = await self.drafts.get(draft_id)
        if draft is None:
            raise NotFound(f"No profile draft {draft_id}")
        version = (
            None
            if draft.draft_version_id is None
            else await self.profiles.get_version(draft.draft_version_id)
        )
        computed = await self.plans.compute(machine, host=host)
        writes_enabled = bool(await self.settings.get("deviceWritesEnabled"))
        name = None if version is None else await self._draft_name(draft, version)
        listed = (
            None
            if version is None
            else await self.board.find_live_by_listed_version(version.id, named=name)
        )
        plan = (
            None
            if listed is None
            else next((p for p in computed.rows if p.row.id == listed.id), None)
        )
        facts = None
        if version is not None and listed is not None and plan is not None:
            machine_state = _machine_state(plan, machine)
            facts = RowFacts(
                row_id=listed.id,
                label=listed.label,
                active_is_this=listed.current_version_id == version.id,
                on_machine=listed.on_machine,
                failed_is_this=listed.failed_version_id == version.id,
                in_conflict=plan.conflict_file is not None,
                holds_current=machine_state.holds_current,
                selected=machine_state.selected,
            )
        existing = (
            None
            if version is None or draft.status != "draft"
            else await self.board.find_live_by_version(version.id, named=name)
        )
        last = (await self.runs.last_runs()).get("profiles")
        sync_failed_since = (
            last is not None and last.status == "error" and last.started_at > draft.updated_at
        )
        found = classify(
            draft,
            facts,
            writes_enabled=writes_enabled,
            paused=computed.paused is not None,
            sync_failed_since=sync_failed_since,
            already_listed_as=None if existing is None else existing.label,
        )
        landing = None
        active_profile = None
        row_id = None if facts is None else facts.row_id
        row_label = None if facts is None else facts.label
        if existing is not None:
            row_id, row_label = existing.id, existing.label
        if found.state == "waiting":
            landing = await self._draft_landing(draft)
            if landing is not None:
                row_id, row_label = landing.plain.row_id, landing.plain.row_label
                target = landing.plain
                if target.row_id is not None:
                    row = await self.board.get(target.row_id)
                    current = (
                        None
                        if row is None
                        else await self.profiles.get_version(row.current_version_id)
                    )
                    active_profile = (
                        None
                        if current is None or row is None
                        else stored_document(current, row.label)
                    )
        return DraftStanding(
            draft=draft,
            profile=None if name is None or version is None else stored_document(version, name),
            active_profile=active_profile,
            landing=landing,
            state=found.state,
            reason=found.reason,
            row_id=row_id,
            row_label=row_label,
            selected=facts.selected if facts is not None and found.state == "on_machine" else None,
            set_version_label=draft.recorded_version_label,
            writes_enabled=writes_enabled,
        )

    async def _proposals(self, landings: list[DraftLanding]) -> list[BoardProposal]:
        """Open drafts that are not yet a version of any profile, each placed by its landing.

        A draft that lands on a profile is a **proposed version** of it; one that lands on none
        is a proposed new profile. A draft whose document is already one of a profile's
        versions is not a proposal (the version is listed), and neither is one with no document.
        The landing is the one a put runs (``place_draft``, through ``_destination``).
        """
        found: list[BoardProposal] = []
        by_draft = {landing.draft_id: landing for landing in landings}
        for draft in await self.drafts.list_drafts(open_only=True, limit=200):
            if draft.status not in ("draft", "approved") or draft.draft_version_id is None:
                continue
            landing = by_draft.get(draft.id)
            if landing is None or landing.already_on_board_label is not None:
                continue
            own = await self.profiles.get_version(draft.draft_version_id)
            if own is not None and (
                await self.board.find_live_by_listed_version(
                    own.id, named=await self._draft_name(draft, own)
                )
                is not None
            ):
                continue
            found.append(BoardProposal(draft=draft, row_id=landing.plain.row_id, landing=landing))
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
        brewing = (await self.sets.live_sets_by_profile()).get(row.id)
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
                    label=row.label,
                    type=version.type,
                    created_at=version.created_at,
                    added_at=by_id[version.id].added_at,
                    source=by_id[version.id].source,
                    is_active=version.id == row.current_version_id,
                    is_on_machine=version.id in on_machine,
                    did_not_verify=row.failed_version_id == version.id,
                    shots_brewed=counts.get(version.id, 0),
                    set=_set_brewing(
                        brewing if brewing and brewing.brewing_version_id == version.id else None
                    ),
                    profile=stored_document(version, row.label),
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
                    profile=stored_document(
                        document, await self._draft_name(proposal.draft, document)
                    ),
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
            landing = await self._draft_landing(draft)
            if landing is not None:
                found.append(landing)
        return found

    async def _draft_landing(self, draft: ProfileDraftRow) -> DraftLanding | None:
        """What a put of this draft would do, or ``None`` for a draft with no document."""
        if draft.draft_version_id is None:
            return None
        version = await self.profiles.get_version(draft.draft_version_id)
        if version is None:
            return None
        existing = await self.board.find_live_by_version(
            version.id, named=await self._draft_name(draft, version)
        )
        if existing is not None:
            # Its document is on the board already (this draft's own row, or another
            # draft that made the same document): nothing to put.
            return DraftLanding(
                draft_id=draft.id,
                already_on_board_label=existing.label,
                plain=BoardLanding(),
            )
        return DraftLanding(draft_id=draft.id, plain=await self._landing(draft, version))

    async def _landing(self, draft: ProfileDraftRow, version: ProfileVersionRow) -> BoardLanding:
        dest = await self._destination(draft, version)
        row = dest.row
        if (dest.frozen or dest.pinned_copy is not None) and dest.refused is not None:
            return BoardLanding(refused=dest.refused)
        if dest.taken_label is not None:
            # For the person: the sentence under the Name field. The put's refusal below keeps
            # the agent's sentence.
            return BoardLanding(refused=person_taken_sentence(dest.taken_label))
        if row is None:
            return BoardLanding(revives_label=None if dest.revived is None else dest.revived.label)
        # Every proposal is an independent candidate: making another one active never blocks or
        # undoes this one, so there is nothing to say about newer drafts.
        return BoardLanding(row_id=row.id, row_label=row.label, set=await self._landing_set(row.id))

    async def _landing_set(self, board_id: int) -> LandingSet | None:
        found = await self.sets.set_of_profile(board_id)
        if found is None:
            return None
        names = await next_names(self.db, found[0])
        return LandingSet(
            id=found[0], name=found[1], next_minor_label=names.minor, next_major_label=names.major
        )

    async def _destination(
        self, draft: ProfileDraftRow, version: ProfileVersionRow
    ) -> _Destination:
        """Where a put of this draft lands.

        The one place that decides, for the put, the landing and the standing, and it reads what
        the draft stored when it was made (``new_profile``, ``destination_board_id``, ``label``),
        never the document's label: the live profile the draft is a change to (with none stored,
        or none left, the one that owns its base now), else refused (a new profile whose name is
        taken, or a change to a pin's frozen copy), else a deleted row it brings back, else a new
        row. Two live profiles never share a name because a put never adds a second.
        """
        lookup = LiveLineage(self.db)
        base = await self.profiles.get_version(draft.base_version_id)
        base_id = None if draft.is_new else draft.base_version_id
        destination = await destination_now(
            lookup,
            new_profile=draft.new_profile,
            stored=(
                None
                if draft.destination_board_id is None
                else await lookup.by_id(draft.destination_board_id)
            ),
            base_version_id=base_id,
            base_label=None if base is None else await self.profiles.name_of(base),
        )
        name = await self._draft_name(draft, version)
        placed = await place_draft(
            lookup,
            label=name,
            destination=destination,
            base_version_id=base_id,
        )
        if placed.frozen is not None:
            return _Destination(refused=placed.frozen, frozen=True)
        if placed.taken is not None:
            return _Destination(
                refused=taken_name_sentence(placed.name), taken_label=placed.taken.label
            )
        # A draft made before the pin can end up exactly equal to the pin's copy; a put would
        # then list a live row on a profile version that belongs to the pin. The constructor
        # refuses this for a new draft (`prepare`); a stored one meets the same function here.
        pinned = await self.proposals.pinned_copy_of(document_for(version, placed.name))
        if pinned is not None:
            return _Destination(refused=pinned.person, pinned_copy=pinned)
        if placed.owner is not None:
            return _Destination(row=placed.owner)
        return _Destination(revived=await self._revivable(version, placed.name))

    async def _draft_name(self, draft: ProfileDraftRow, version: ProfileVersionRow) -> str:
        """What the draft is called: its own stored name, else the name the version carries."""
        return draft.draft_label or await self.profiles.name_of(version)

    async def _revivable(self, version: ProfileVersionRow, name: str) -> BoardRow | None:
        """The deleted row called ``name`` a put of this version would bring back, if there is one.

        One function for the put and for the read of where a put lands. A deleted row whose
        file a live row now stands on is not revivable: a new row, never two on one file.
        """
        revived = await self.board.find_deleted_with_file(version.content_hash, name)
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
        major: bool | None = None,
        label: str | None = None,
    ) -> BoardRow:
        """Make a draft the profile's next current version, or a new profile. One action.

        Approving a proposal and putting it on the board are the same click: a drafted draft
        is approved here, in the same transaction as the row it makes, and the profile is
        switched on (approving means "I want this on the machine"; a sync still decides when).
        A draft that moves a stop condition is put like any other: the card shows the change,
        and the agent is told never to make one unprompted, but nothing here refuses it.

        Where it lands is decided by ``place_draft``, the same function the draft constructor and
        the page's landing ask: a draft continues its base's live profile when its label is that
        profile's name; a new or renamed
        draft whose name is already a live profile (it became one after the draft was made) is
        refused with the same sentence, and the draft stays as it was.

        ``label`` is the name the person typed for a new profile. ``major`` names the new version
        of the profile's Set a major one (a minor otherwise). See :meth:`put_in_transaction`.
        """
        # Every check and the write in one transaction: two puts of the same draft (a
        # double click) are serialised, and the second finds the first's row.
        async with self.db.transaction():
            return await self.put_in_transaction(draft_id, major=major, label=label)

    async def settle_for_accept(self, draft_id: int, *, label: str | None) -> BoardRow | None:
        """What a first recipe's accept does with its draft, inside the accept's transaction.

        A draft still waiting is put (:meth:`put_in_transaction`: switched on, no Set recording,
        renamed when ``label`` is given). A draft already answered, whether approved or pushed
        on the Profiles page or overtaken by a later version of its profile, is left exactly as
        it is: the accept must not make an older version active again or undo anyone's answer.
        Its name cannot change here (422). A closed draft (discarded, superseded, failed) never
        gets this far: the accept refuses it first, as ``draft_closed``. Returns the live profile
        that lists the draft's version, if there is one.
        """
        draft = await self.drafts.get(draft_id)
        if draft is None:
            raise NotFound(f"No profile draft {draft_id}")
        if draft.status == "draft":
            return await self.put_in_transaction(draft_id, label=label)
        if label is not None:
            raise Unprocessable("That profile was already approved; its name cannot change here.")
        if draft.draft_version_id is None:
            return None
        listed = await self.profiles.get_version(draft.draft_version_id)
        if listed is None:  # pragma: no cover - a foreign key guarantees it
            return None
        return await self.board.find_live_by_listed_version(
            listed.id, named=await self._draft_name(draft, listed)
        )

    async def put_in_transaction(
        self,
        draft_id: int,
        *,
        major: bool | None = None,
        label: str | None = None,
    ) -> BoardRow:
        """:meth:`put_draft` inside a transaction the caller already holds.

        The database allows one transaction at a time and refuses to nest, so a write that must
        be one with a put (a first recipe's accept) opens the transaction itself and calls this.

        A ``label`` is allowed only for a draft that is a new profile (it has no destination): the
        draft takes that name, the document is rebuilt under it through the constructor every
        draft document goes through, and the draft follows it to the new version. The refusals
        are the constructor's: an empty name, a taken name.

        **Where the put goes is the destination the draft stored when it was made**, and the new
        profile is called what the draft is called; the document's own label decides nothing.

        **The profile's Set follows in the same transaction** (:meth:`_set_follows`): a profile
        with a Set gets a new active version, so the Set gets a new version naming it, carrying
        the draft's experiment when the draft was argued in that Set's chat.
        """
        draft = await self.drafts.get(draft_id)
        if draft is None:
            raise NotFound(f"No profile draft {draft_id}")
        if draft.status not in ("draft", "approved"):
            raise Conflict(f"A {draft.status} proposal cannot be made active.")
        if draft.draft_version_id is None:
            raise Conflict("That proposal has no document to make active")
        version = await self.profiles.get_version(draft.draft_version_id)
        if version is None:  # pragma: no cover - a foreign key guarantees it
            raise NotFound(f"No profile version {draft.draft_version_id}")
        # "Already in the list" is about this draft's own landing: a version is what a profile
        # brews, so another profile that brews the same thing is not this draft being put twice.
        if (
            await self.board.find_live_by_version(
                version.id, named=await self._draft_name(draft, version)
            )
            is not None
        ):
            raise Conflict("That profile version is already in the list.")

        if label is not None:
            draft, version = await self._rename(draft, version, label)
            if (
                await self.board.find_live_by_version(
                    version.id, named=await self._draft_name(draft, version)
                )
                is not None
            ):
                raise Conflict("That profile version is already in the list.")

        dest = await self._destination(draft, version)
        if dest.frozen and dest.refused is not None:
            raise PinFrozen(dest.refused)
        if dest.pinned_copy is not None:
            raise dest.pinned_copy
        name = (await self._draft_name(draft, version)).strip()
        if dest.refused is not None:
            raise TakenName(name)
        # A change is a version of its profile under the profile's own name: the document the
        # machine gets is built from the profile's name and the version's brew, so nothing is
        # stored for a name.
        row = dest.row
        if draft.status == "draft":
            await self.drafts.set_status(draft.id, "approved")
        pending = BoardRowPatch(pending_draft_id=draft.id)
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
                        on_machine=True,
                        **pending.model_dump(exclude_unset=True),
                    ),
                )
                assert back is not None
                kept = await self.board.version_for(back.id, version.id)
                await self.board.add_version(back.id, kept, source)
                await self._follow(draft, kept)
                await self._set_follows(back.id, kept, draft=draft, major=major)
                return back
            created = await self.board.insert(
                BoardRowWrite(
                    label=name,
                    current_version_id=version.id,
                    origin="draft",
                    pending_draft_id=draft.id,
                    version_source=source,
                )
            )
            await self._follow(draft, created.current_version_id)
            return created
        kept = await self.board.version_for(row.id, version.id)
        await self.board.add_version(row.id, kept, source)
        updated = await self.board.update(
            row.id,
            BoardRowPatch(
                # (Never a label: a put does not rename the profile it changes.)
                current_version_id=kept,
                # Approving is "I want this on the machine": a profile switched off is
                # switched on. Its star is left as it was.
                on_machine=True,
                # What the row was, so a person can go back to it.
                previous_version_id=row.current_version_id,
                back_from_version_id=None,
                failed_version_id=None,
                **pending.model_dump(exclude_unset=True),
            ),
        )
        assert updated is not None  # the row was read in this transaction
        await self._follow(draft, kept)
        await self._set_follows(updated.id, kept, draft=draft, major=major)
        return updated

    async def _follow(self, draft: ProfileDraftRow, version_id: int) -> None:
        """Make the draft name the version its profile ended up with.

        A draft's version is the profile's own when the put lists it. When the profile already
        had that brew under a version of its own (a profile put back, an old document put again)
        that version is the one the profile has, and the draft, which is recorded on the Set and
        read by the sync as that profile's, names it too.
        """
        if draft.draft_version_id != version_id:
            await self.drafts.set_draft_version(draft.id, version_id, label=draft.draft_label or "")

    async def _set_follows(
        self,
        board_id: int,
        version_id: int,
        *,
        draft: ProfileDraftRow | None = None,
        major: bool | None = None,
    ) -> None:
        """The profile has a new active version: its Set, if it has one, follows.

        The draft that made it so passes its experiment on: the prediction and what it is
        compared to when the draft was argued in this very Set's chat (the comparison falls back
        to the Set's current version when the one the prediction named is no longer one of its
        versions), the draft's summary as the intent, and the person's choice of a major.
        """
        found = await self.sets.set_of_profile(board_id)
        if found is None:
            return
        origin: Literal["manual", "analysis", "chat"] = "manual"
        extra: dict[str, Any] = {}
        if draft is not None:
            extra["intent"] = draft.change_summary[:500]
            extra["origin_analysis_id"] = draft.source_analysis_id
            if draft.set_id == found[0] and draft.prediction:
                compares_to = draft.compares_to_version_id
                if compares_to is not None and (
                    await self.sets.version_of_set(found[0], compares_to) is None
                ):
                    compares_to = None
                if compares_to is None:
                    current = await self.sets.current_version(found[0])
                    compares_to = None if current is None else current.id
                extra["prediction"] = draft.prediction
                extra["compares_to_version_id"] = compares_to
            origin = (
                "analysis"
                if draft.source_analysis_id is not None
                else ("chat" if extra.get("prediction") else "manual")
            )
        recorded = await self.sets.follow_profile(
            board_id, version_id, major=major, origin=origin, **extra
        )
        if draft is not None and recorded is not None:
            await self.drafts.set_recorded_version(draft.id, recorded.id)

    async def _rename(
        self, draft: ProfileDraftRow, version: ProfileVersionRow, label: str
    ) -> tuple[ProfileDraftRow, ProfileVersionRow]:
        """The draft under the name a person typed for a new profile.

        Allowed only for a draft that is a new profile (``_destination`` finds no profile it is a
        change to), and the new name must itself be free (a live profile with that name is
        refused as taken, by the same function). What is renamed is the draft: its stored name
        is the only thing that changes, and the document is built from it for the machine through
        :meth:`DraftProposals.prepare`, the constructor of every draft document, so the schema,
        the clamp and the check apply as they did at drafting.

        A version on no profile is the document it came as, name included, so the rebuilt document
        is the version of that brew under the new name (stored if it is new) and the draft names
        it; the expectations in force follow. Once the profile lands, its version is its brew and
        the name is the profile's. All inside the caller's transaction.
        """
        if (await self._destination(draft, version)).row is not None:
            raise Unprocessable("A change to an existing profile keeps its name")
        name = label.strip()
        if not name:
            raise Unprocessable("A profile needs a name")
        try:
            candidate = document_for(version, name)
        except ValidationError as exc:
            raise Unprocessable(
                "That is not a valid profile name", details={"schema_errors": schema_errors(exc)}
            ) from None
        base = await self.proposals.base_profile(draft.base_version_id)
        prepared = await self.proposals.prepare(
            draft.base_version_id,
            base,
            candidate,
            new_profile_name=name,
            is_new=draft.is_new,
        )
        renamed, _ = await self.profiles.ensure_version(
            prepared.profile, owner=None, source="draft"
        )
        if renamed.id != version.id:
            # The expectations in force on the proposed version follow the rename, whether the
            # renamed version is new or another draft made it first (carrying twice adds nothing).
            await self.signatures.carry_quietly(version.id, renamed.id)
        await self.drafts.set_draft_version(draft.id, renamed.id, label=prepared.target.label)
        current = await self.drafts.get(draft.id)
        assert current is not None
        return current, renamed

    async def resume(self) -> None:
        """Let the next sync write again after it paused for a suspected machine reset.

        A person's decision, reached only from its route. It writes to the archive and sends
        nothing to the machine; the next sync then does what the page showed.
        """
        await self.board.resume()

    async def _saved_by_the_app(self, device_id: str, content_hash: str, *, host: str) -> bool:
        """Whether a machine file is this app's own save, still exactly as it was saved.

        The save audit alone decides: an ``ok`` save of that id on this host, with content equal
        to what that save sent. The name says nothing (a person may call their own profile
        anything, "[AI]" included). It only sets a profile's ``origin``, which is information: a
        sync does not treat the app's profiles and the person's differently.
        """
        return await self.writes.saved_with_content(device_id, host=host, content_hash=content_hash)

    async def set_on_machine(self, row_id: int, on: bool) -> BoardRow:
        """Switch a profile on or off the machine. The next sync follows; nothing is sent now."""
        row = await self._live_row(row_id)
        await self._refuse_if_pinned(row)
        updated = await self.board.update(row.id, BoardRowPatch(on_machine=on))
        assert updated is not None
        return updated

    async def rename(self, row_id: int, label: str) -> BoardRow:
        """Give a profile another name. The next sync puts it on the machine; nothing is sent now.

        A person's act (no tool, MCP client or agent renames). It writes the profile's name and
        nothing else: no version, draft, Set or signature row, so it works with the switch off,
        and on a pinned row (a pin freezes what the copy brews, not what it is called). The name
        is checked as the list checks every name: not empty, and not another live profile's
        (case and surrounding space ignored, so a change of case alone is a rename). The same
        name is a no-op. When the profile is in a conflict about its name alone (the machine's
        brew is one the profile has had), renaming is the person's answer for the app's side, so
        the app's side of that conflict is kept in the same transaction.
        """
        name = label.strip()
        if not name:
            raise Unprocessable("A profile needs a name")
        async with self.db.transaction():
            row = await self._live_row(row_id)
            if name == row.label:
                return row
            holder = await self.board.find_live_by_name_key(name, excluding=row.id)
            if holder is not None:
                raise Conflict(
                    person_taken_sentence(holder.label), details={"reason": "duplicate_label"}
                )
            patch = BoardRowPatch(label=name)
            machine = await self._mirror_conflict(row)
            if machine is not None and machine.name_only:
                patch = BoardRowPatch(label=name, conflict_overruled_hash=machine.document_hash)
            updated = await self.board.update(row.id, patch)
            assert updated is not None
            return updated

    async def set_active_version(self, row_id: int, version_id: int) -> BoardRow:
        """Make one of a profile's versions its active one. The next sync puts it on the machine.

        Any version the profile has had will do, an older one included (the sync pushes it and
        removes the file the profile stood on). The profile's Set follows in the same
        transaction: it gets a new minor version naming the version made active. The profile
        keeps its name: only its active version changes. Refused, each with its own sentence,
        for a version that is not this profile's, the synthetic baseline, and a version outside
        the safety bounds as they are now. Making the version that is already active active
        again clears a failed verification, so the sync tries it again.
        """
        async with self.db.transaction():
            row = await self._live_row(row_id)
            await self._refuse_if_pinned(row)
            version = await self._activatable(row, version_id)
            if version.id == row.current_version_id:
                if row.failed_version_id is None:
                    return row
                cleared = await self.board.update(row.id, BoardRowPatch(failed_version_id=None))
                assert cleared is not None
                return cleared
            updated = await self._make_active(row, version)
            await self._set_follows(row.id, version.id)
            return updated

    async def revert_set(self, set_id: int, spec: RollbackWrite) -> VersionWriteResult:
        """Put a Set back on another of its versions, and its profile on the version that
        version brews: one transaction, and no Set version is written (the Set is going back to
        one it already has). Everything that can refuse making a version active refuses the
        revert, and then nothing has moved."""
        async with self.db.transaction():
            result = await self.sets.rollback(set_id, spec)
            version = result.version
            if version is None or version.profile_version_id is None:
                return result
            found = await self.sets.get(set_id)
            row = None
            if found is not None and found.board_id is not None:
                row = await self.board.get(found.board_id)
            if row is None or row.deleted_at is not None:
                return result
            if version.profile_version_id != row.current_version_id:
                target = await self._activatable(row, version.profile_version_id)
                await self._make_active(row, target)
            return result

    async def _activatable(self, row: BoardRow, version_id: int) -> ProfileVersionRow:
        """The version, when it may become this profile's active one; else the refusal."""
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
            return version
        refusal = await self.policy_refusal(version)
        if refusal is not None:
            raise Conflict(
                f"That version cannot go on the machine: {refusal}.",
                details={"reason": "policy"},
            )
        return version

    async def _make_active(self, row: BoardRow, version: ProfileVersionRow) -> BoardRow:
        """The row's active version is this one. What was waiting for the sync to mark pushed is
        kept only when it is this very version."""
        pending = (
            None if row.pending_draft_id is None else await self.drafts.get(row.pending_draft_id)
        )
        keep_pending = pending is not None and pending.draft_version_id == version.id
        extra = {} if keep_pending else {"pending_draft_id": None}
        updated = await self.board.update(
            row.id,
            BoardRowPatch(
                current_version_id=version.id,
                previous_version_id=row.current_version_id,
                back_from_version_id=None,
                failed_version_id=None,
                **extra,
            ),
        )
        assert updated is not None
        return updated

    async def set_home_screen(self, row_id: int, on: bool) -> BoardRow:
        row = await self._live_row(row_id)
        updated = await self.board.update(row.id, BoardRowPatch(on_home_screen=on))
        assert updated is not None
        return updated

    async def _refuse_if_pinned(self, row: BoardRow) -> None:
        """A pin's frozen copy changes only by unpinning it (the star is the one thing a person may
        still set): a 409 naming the pin."""
        sentence = await LiveLineage(self.db).frozen_sentence(row)
        if sentence is not None:
            raise PinFrozen(sentence)

    async def _live_row(self, row_id: int) -> BoardRow:
        row = await self.board.get(row_id)
        if row is None or row.deleted_at is not None:
            raise NotFound(f"No profile {row_id} in the list")
        return row

    # ── the write phase ──────────────────────────────────────────────

    async def run(
        self,
        client: GaggimateClient,
        *,
        run_id: int,
        update: SyncRunUpdate,
        pull_shots: Callable[[], Awaitable[bool]],
        shot_announced: Callable[[], bool],
    ) -> bool | None:
        """The sync's write phase. ``None`` (and nothing read or sent) with the switch off, unless
        a profile took the name its file was given at the machine (``True``: a read).

        Otherwise whether it changed anything, with the summary left on ``update``.

        Checked before the machine is read for any writing purpose: with the switch off the
        sync is a sync, and an audited "refused" row for something nobody attempted would only
        be noise in the audit a person reads to see what was sent.
        """
        if not await self.settings.get("deviceWritesEnabled"):
            # A name somebody gave a file at the machine is read, not written: the profile takes
            # it whatever the switch says. Nothing else happens, and the run has no board summary.
            try:
                renamed = await self._take_names_from_the_mirror(
                    client, run_id=run_id, update=update
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.error("board_names_from_the_mirror_failed", exc_info=True)
                return None
            return True if renamed else None
        summary = BoardRunSummary()
        try:
            await self._run(client, run_id, update, summary, pull_shots, shot_announced)
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
        self,
        client: GaggimateClient,
        run_id: int,
        update: SyncRunUpdate,
        summary: BoardRunSummary,
        pull_shots: Callable[[], Awaitable[bool]],
        shot_announced: Callable[[], bool],
    ) -> None:
        machine = await read_machine(client)
        phase = _Phase(
            client, run_id, update, summary, machine, client.host, pull_shots, shot_announced
        )
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
        await self._take_names(phase, computed)
        phase.held = {p.row.id: p.held for p in computed.rows if p.held is not None}

        # Profiles that are on first (their saves), then the ones switched off, then the deleted
        # ones: nothing is removed before every save has been tried.
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

        # The stars last, and from a fresh read when anything was written: the firmware stars
        # every file it creates, and a star is judged against the file as it is now.
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

    # ── a name given at the machine ──────────────────────────────────

    async def _take_names(self, phase: _Phase, computed: Computed) -> None:
        """Every profile whose file was renamed at the machine alone takes the machine's name."""
        for plan in computed.rows:
            if plan.take_name is not None:
                item = await self._take_name(plan, run_id=phase.run_id)
                if item is not None:
                    phase.summary.renamed.append(item)
                else:
                    # Not taken (another profile has the name now): the file is not recorded as
                    # holding that name either, so the next sync sees the difference and asks.
                    plan.edited_file = None

    async def _take_name(self, plan: RowPlan, *, run_id: int) -> BoardSummaryItem | None:
        """The profile takes the name its file has at the machine; nothing is written there.

        A read, so it happens with the switch off as well. The name is free when the plan says so
        and is checked again here, since the list may have changed in between; the recorded name
        follows the profile's, so the file and the profile agree. A rename never writes a version.
        """
        name = plan.take_name
        row = plan.row
        if name is None or row.device_version_id is None:
            return None
        async with self.db.transaction():
            fresh = await self.board.get(row.id)
            if (
                fresh is None
                or fresh.deleted_at is not None
                or fresh.label != row.label
                or fresh.device_version_id is None
                or await self.board.find_live_by_name_key(name, excluding=row.id) is not None
            ):
                return None
            updated = await self.board.update(
                row.id,
                BoardRowPatch(
                    label=name,
                    device_version_id=fresh.device_version_id,
                    device_label=name,
                ),
            )
        assert updated is not None
        plan.row = updated
        await self.runs.add_event(
            EVENT_RENAMED_ON_MACHINE,
            run_id=run_id,
            device_id=row.device_profile_id,
            message=f"{row.label} was renamed on the machine to {name}.",
            data={"row_id": row.id, "from": row.label, "to": name},
        )
        return BoardSummaryItem(
            row_id=row.id,
            label=name,
            device_id=row.device_profile_id,
            reason="renamed_on_machine",
            detail=f"it took the name the machine gave its file; it was called {row.label}",
        )

    async def _take_names_from_the_mirror(
        self, client: GaggimateClient, *, run_id: int, update: SyncRunUpdate
    ) -> bool:
        """With the switch off: the same names are taken, from what the sync just mirrored.

        Nothing is read for writing and nothing is sent; the plan is built from the mirror exactly
        as the page builds it when it cannot reach the machine.
        """
        computed = await self.plans.compute(
            await machine_from_mirror(self.profiles), host=client.host
        )
        if computed.paused:
            return False
        taken = False
        for plan in computed.rows:
            if plan.take_name is not None and await self._take_name(plan, run_id=run_id):
                update.profiles_changed += 1
                taken = True
        return taken

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

        A file no profile holds the id of becomes a new profile that is on, starred as the machine
        has it (the one exception: after a resumed reset, a file with the brew of a profile that
        lost its file is attached to that profile, which now stands on it). Either way the
        profile is left alone for the rest of this sync, so the sync that finds a file never
        removes or replaces it.
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
                        if action.reason == "attached"
                        else "joined the list as a new profile."
                    ),
                    device_id,
                    {"row_id": row.id, "reason": action.reason},
                )

    async def _join_one(self, phase: _Phase, action: BoardAction, profile: Profile) -> BoardRow:
        device_id = action.device_id or ""
        if action.reason == "attached" and action.row_id is not None:
            row = await self.board.get(action.row_id)
            assert row is not None  # the plan read it a moment ago
            # The file's content, as a version of the profile it is now attached to: its active
            # brew, since that is how the plan matched it.
            version, _ = await self.profiles.ensure_version(profile, owner=row.id)
            await self.board.add_version(row.id, version.id, "edited_on_machine")
            # The mirror read the file before any profile stood on it: it holds this profile's
            # version of that content from now on.
            await self.profiles.point_file_at(device_id, version.id)
            updated = await self.board.update(
                row.id,
                BoardRowPatch(
                    device_profile_id=device_id,
                    device_version_id=version.id,
                    device_label=profile.label,
                ),
            )
            assert updated is not None
            return updated
        # A new profile: the version the mirror stored for the file, which belongs to no profile
        # yet and is the new profile's from now on.
        version, _ = await self.profiles.ensure_version(profile, owner=None)
        # A file this app saved itself is the app's own profile (information only).
        ours = await self._saved_by_the_app(device_id, document_hash(profile), host=phase.host)
        row = await self.board.insert(
            BoardRowWrite(
                # Called what the file is called unless a live profile has that name (the plan
                # made it "<name> (2)"), whatever the stored version was first called; the file's
                # own name is what the archive last recorded for it, so the next sync renames it.
                label=action.label,
                current_version_id=version.id,
                device_profile_id=device_id,
                device_version_id=version.id,
                device_label=profile.label,
                on_machine=True,
                on_home_screen=profile.favorite,
                origin="draft" if ours else "adopted",
                version_source="machine",
            )
        )
        # The mirror may have mapped the file to a version another profile owns (it did when
        # exactly one profile owned that content); the file holds the new profile's own now.
        await self.profiles.point_file_at(device_id, row.current_version_id)
        return row

    async def _record_edit(self, phase: _Phase, row: BoardRow, device_id: str) -> None:
        """A file somebody edited on the display: keep what it holds as a version of its profile.

        Done before anything is replaced or removed, so nothing the person made on the display
        is lost, and so the removal that may follow is guarded by exactly what is recorded.
        """
        served = phase.machine.profiles.get(device_id)
        if served is None:  # pragma: no cover - the plan read it from this machine
            return
        version, _ = await self.profiles.ensure_version(
            served, owner=row.id, device_json=json.dumps(served.to_device())
        )
        # The row's own version of what the file holds (an ownerless or another profile's
        # version is attached, or copied, to it first).
        kept = await self.board.version_for(row.id, version.id)
        added = await self.board.add_version(row.id, kept, "edited_on_machine")
        await self.board.update(
            row.id, BoardRowPatch(device_version_id=kept, device_label=served.label)
        )
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
                {"row_id": row.id, "version_id": kept},
            )

    async def _conflict(self, phase: _Phase, plan: RowPlan) -> None:
        """A profile whose file differs from everything the app knows: keep the machine's content as
        a version (so nothing is lost), do nothing else for the profile, and say so."""
        row, device_id = plan.row, plan.conflict_file or ""
        served = phase.machine.profiles.get(device_id)
        if served is not None:
            version, _ = await self.profiles.ensure_version(
                served, owner=row.id, device_json=json.dumps(served.to_device())
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
        """A profile that is on: put its active version on the machine, over its own file."""
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
                return  # the file stays as it was, and the row stands where it was
            new_id = placed.device_id
            phase.held[row.id] = new_id
        assert new_id is not None  # a row either holds its content or has just pushed it

        if (
            row.device_profile_id != new_id
            or row.device_version_id != plan.version.id
            or row.device_label != row.label
        ):
            await self.board.update(
                row.id,
                BoardRowPatch(
                    device_profile_id=new_id,
                    device_version_id=plan.version.id,
                    device_label=row.label,
                ),
            )
        if row.device_profile_id != new_id:
            # A file the row has just come to stand on holds the row's own version of that
            # content, not the loose one the mirror stored before any profile stood on it. A
            # file saved over in place is not moved: the shots it has were brewed on what it held,
            # and the mirror takes the new version below.
            await self.profiles.point_file_at(new_id, plan.version.id)
        if row.back_from_version_id is not None:
            # The file was dealt with: the going back is over.
            await self.board.update(
                row.id,
                BoardRowPatch(back_from_version_id=None),
            )
        if placed is not None and placed.served is not None:
            await self._mirror(
                new_id,
                placed.served,
                owner=row.id,
                favorite=placed.served.favorite,
                selected=placed.served.selected,
            )
        await self._record(phase, plan, new_id, saved=placed is not None)

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
        """Save the row's current version and verify it.

        Every version that is pushed here is first run through the schema and the safety
        policy with the bounds as they are now: a profile that was valid when it was drafted
        may not be under the bounds a person has since tightened. A version that fails stays
        off the machine and is reported. ``None`` when it did not land, with the failure
        recorded; the file is then as it was. A profile that has a file is saved over it
        (:meth:`_push_over`); one with none gets a new file (:meth:`_push_new`).
        """
        assert plan.push is not None
        refusal = await self.policy_refusal(plan.version)
        if refusal is not None:
            await self._row_failed(
                phase, plan.push, "policy", refusal, counts_as_device_failure=False
            )
            return None
        clamped = document_for(plan.version, plan.row.label)
        row = await self._fresh(plan.row)
        if row.device_profile_id is not None and row.device_profile_id in phase.machine.profiles:
            return await self._push_over(phase, plan, clamped, row.device_profile_id)
        return await self._push_new(phase, plan, clamped)

    async def _pulled_the_shots(self, phase: _Phase) -> bool:
        """Whether the machine's shots are pulled as of now, asking when they are not.

        A profile keeps its machine id for good, and a shot is linked to the version its file
        holds when the shot is read, so the shots are read before anything is saved over a file:
        once in a sync, and again when the machine announced a shot since. A pull that did not
        complete stays failed for the rest of the sync.
        """
        for _ in range(3):
            if phase.shots_pulled is None or (phase.shots_pulled and phase.shot_announced()):
                try:
                    phase.shots_pulled = await phase.pull_shots()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.warning("board_shots_pull_failed", exc_info=True)
                    phase.shots_pulled = False
            if not phase.shots_pulled:
                return False
            if not phase.shot_announced():
                return True
        return False  # shots keep arriving: not now

    async def _a_shot_is_running(self, phase: _Phase, action: BoardAction, device_id: str) -> bool:
        """Whether the machine reports a process running; the save is then left for the next sync.

        A shot that is running is not in the index yet, and a save now would link it to the new
        version when it ends. It is said on the run and is not a device failure.
        """
        status = phase.client.last_status
        if status is None or status.process is None or status.process.a != 1:
            return False
        await self._left(
            phase,
            action.model_copy(
                update={
                    "kind": "leave",
                    "device_id": device_id,
                    "reason": "shot_running",
                    "detail": "the machine is running a shot; saved at the next sync",
                }
            ),
        )
        return True

    async def _push_over(
        self, phase: _Phase, plan: RowPlan, clamped: Profile, device_id: str
    ) -> Placed | None:
        """Save the document over the profile's own file, under the guard.

        What the file must hold is what the archive recorded for it (the recorded version's brew
        under the name recorded with it), never what the plan read at some other moment. With no
        record, or with the machine's shots not pulled, nothing is saved.
        """
        action = plan.push
        assert action is not None
        row = await self._fresh(plan.row)
        recorded = (
            None
            if row.device_version_id is None
            else await self.profiles.get_version(row.device_version_id)
        )
        if recorded is None or row.device_label is None:
            await self._row_failed(
                phase,
                action,
                "push",
                "the archive has no record of what the file holds, so it is not saved over",
                device_id=device_id,
                counts_as_device_failure=False,
            )
            return None
        if await self._a_shot_is_running(phase, action, device_id):
            return None
        if not await self._pulled_the_shots(phase):
            await self._row_failed(
                phase,
                action,
                "push",
                "the machine's shots could not be pulled first, so nothing is saved over its file "
                "in this sync",
                device_id=device_id,
                counts_as_device_failure=False,
            )
            return None
        if await self._a_shot_is_running(phase, action, device_id):
            return None
        recorded_document = document_for(recorded, row.device_label)
        try:
            placed = await overwrite(
                phase.client,
                clamped,
                phase.machine,
                device_id=device_id,
                expected_hash=document_hash(recorded_document),
                recorded=recorded_document,
            )
        except DeviceError as exc:
            await self._row_failed(phase, action, "push", str(exc), device_id=device_id)
            return None
        phase.summary.writes += placed.saves
        if placed.problem == "changed":
            await self._left(
                phase,
                action.model_copy(
                    update={
                        "kind": "leave",
                        "device_id": device_id,
                        "reason": "edited_on_machine",
                        "detail": placed.error,
                    }
                ),
            )
            return None
        if placed.problem is not None:
            # Not tried again until another version is made active (or this one is made active
            # again, which asks for a retry): a firmware that cannot round-trip this version
            # would otherwise be written to and repaired again on every sync.
            await self.board.update(plan.row.id, BoardRowPatch(failed_version_id=plan.version.id))
            await self._row_failed(
                phase,
                action,
                "round_trip",
                f"{placed.error}; "
                + (
                    "the recorded document was put back on the file and the previous version stays."
                    if placed.repaired
                    else "the file may not hold what was recorded; the next sync reads it again."
                ),
                device_id=device_id,
            )
            return None
        await self._supersede_what_stood_behind(row, plan, device_id)
        await self._pushed(phase, plan, placed)
        return placed

    async def _push_new(self, phase: _Phase, plan: RowPlan, clamped: Profile) -> Placed | None:
        """Save a new file for a profile that has none, and verify it."""
        action = plan.push
        assert action is not None
        try:
            placed = await place(phase.client, clamped, phase.machine)
        except DeviceError as exc:
            await self._row_failed(phase, action, "push", str(exc))
            return None
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
                    expected_hash=document_hash(placed.served),
                    successor=None,
                    carry_favorite=False,
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
            if not cleanup.removed:
                # The leftover file is the profile's own now, recorded as what was sent: the next
                # sync sees content the profile never had on it and asks, instead of adopting a
                # mangled copy as a profile of its own.
                await self.board.update(
                    plan.row.id,
                    BoardRowPatch(
                        device_profile_id=placed.device_id,
                        device_version_id=plan.version.id,
                        device_label=clamped.label,
                    ),
                )
            await self._row_failed(
                phase,
                action,
                "round_trip",
                f"{placed.error}; "
                + (
                    "that copy was removed again."
                    if cleanup.removed
                    else f"that copy could not be removed ({cleanup.reason}); the profile "
                    "stands on it and the next sync asks."
                ),
                device_id=placed.device_id,
            )
            return None
        await self._pushed(phase, plan, placed)
        return placed

    async def _pushed(self, phase: _Phase, plan: RowPlan, placed: Placed) -> None:
        """A save that verified: the run says so."""
        assert plan.push is not None
        action = plan.push
        phase.failures_in_a_row = 0
        item = BoardSummaryItem(
            row_id=plan.row.id,
            label=plan.row.label,
            device_id=placed.device_id,
            reason=action.reason,
        )
        phase.summary.pushed.append(item)
        message = f"Put {plan.row.label} on the machine as {placed.device_id}."
        await self._event(
            phase,
            EVENT_PUSHED,
            message,
            placed.device_id,
            {"row_id": plan.row.id, "reason": action.reason},
        )

    async def _supersede_what_stood_behind(
        self, row: BoardRow, plan: RowPlan, device_id: str
    ) -> None:
        """The drafts that put the file's earlier content there stand behind it no more.

        What a removal did for a replaced file, said of a file saved over: the draft waiting on
        this profile replaces them, else the newest draft of the version now on the file does,
        else they are closed.
        """
        newer = row.pending_draft_id or await self.drafts.pusher_of_version(plan.version.id)
        if newer is not None:
            await self.drafts.supersede_pushed(device_id, by_draft_id=newer)
        else:
            await self._retire(
                device_id,
                "went_back_over"
                if row.back_from_version_id is not None
                and row.device_version_id == row.back_from_version_id
                else "saved_over",
            )

    async def _remove(
        self,
        phase: _Phase,
        action: BoardAction,
        *,
        expected_hash: str | None,
        successor: str | None,
        row: BoardRow,
        carry_favorite: bool,
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
            if action.reason == "off":
                await self._retire(device_id, "switched_off")
            elif row.pending_draft_id is not None:
                await self.drafts.supersede_pushed(device_id, by_draft_id=row.pending_draft_id)
            elif row.deleted_at is not None or (
                row.back_from_version_id is not None
                and row.device_version_id == row.back_from_version_id
            ):
                await self._retire(
                    device_id, "deleted" if row.deleted_at is not None else "went_back"
                )
            else:
                # The copy is off the machine and nothing stands behind it: its draft is closed
                # as a delete closes it.
                await self._retire(device_id, "removed")
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
                {"row_id": row.id, "reason": action.reason},
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
        device_id: str,
        how: Literal[
            "went_back", "deleted", "removed", "switched_off", "went_back_over", "saved_over"
        ],
    ) -> None:
        """Close the record of a file removed without a newer draft taking its place.

        Going back to a previous version, or deleting a profile, takes a pushed draft's file
        off the machine and no later push replaces it. What a rollback did: the draft that
        pushed it is ``discarded`` and stops naming the file. ``removed`` is the same closing for
        a copy whose removal had to wait and found nothing newer behind the profile.
        """
        words = {
            "went_back": f"Went back to the previous version: {device_id} is off the machine.",
            "deleted": f"The profile was deleted: {device_id} is off the machine.",
            "removed": f"{device_id} is off the machine and nothing newer stands behind it.",
            "switched_off": f"The profile was switched off: {device_id} is off the machine.",
            "went_back_over": f"Went back to the previous version: {device_id} was saved over.",
            "saved_over": f"{device_id} was saved over with another version of the profile.",
        }
        await self.drafts.retire_pushed(
            device_id,
            outcome={
                "action": how,
                "removed_device_profile_id": device_id,
                "lines": [words[how]],
            },
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
                # The name the file has: what the sync read, else what the mirror already says.
                label=served.label if served is not None else existing.label,
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
        self, device_id: str, served: Profile, *, owner: int, favorite: bool, selected: bool
    ) -> None:
        """Put what was just saved into the archive's mirror straight away, as a version of the
        profile it was saved for."""
        version, _ = await self.profiles.ensure_version(
            served, owner=owner, source="draft", device_json=json.dumps(served.to_device())
        )
        existing = await self.profiles.get_device_profile(device_id)
        await self.profiles.upsert_device_profile(
            device_id=device_id,
            version_id=version.id,
            favorite=favorite,
            selected=selected,
            position=None if existing is None else existing.position,
            label=served.label,
        )

    async def _record(self, phase: _Phase, plan: RowPlan, device_id: str, *, saved: bool) -> None:
        """Say on the draft that the profile is on the machine.

        The draft becomes ``pushed`` exactly as a person's push leaves it. Done at the moment the
        profile is found on the machine, whether this pass put it there or an earlier one did and
        was cut off before this step. (Its Set recorded the version when the draft was put: the
        profile's active version is what the Set follows, not what is on the machine.)
        """
        row = plan.row
        if row.pending_draft_id is None:
            return
        draft = await self.drafts.get(row.pending_draft_id)

        async def clear() -> None:
            # Only if the row is still what this pass acted on: a person may have put a newer
            # draft on the profile while it worked, and that one must stay pending.
            await self.board.clear_pending_if_unchanged(
                row.id, version_id=plan.version.id, draft_id=row.pending_draft_id
            )

        if draft is None or draft.status != "approved":
            # Only a draft still waiting is marked: one a person pushed or turned down in the
            # meantime has been dealt with elsewhere.
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
        if plan.push is not None and plan.push.reason != "missing":
            lines.append(
                f"{device_id} is the profile's own file: its earlier version was saved over."
            )
        served = phase.machine.profiles.get(device_id)
        await self.drafts.set_status(
            draft.id,
            "pushed",
            pushed_device_profile_id=device_id,
            pushed_saved=saved,
            verification=None
            if served is None
            else {
                "sent": document_for(plan.version, row.label).to_device(),
                "loaded": served.to_device(),
            },
            outcome=outcome,
        )
        await clear()

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
    "deleted": "the profile was deleted",
    "off": "the profile is switched off",
}


def _row_pin(pin: PinDetail | None) -> BoardRowPin | None:
    if pin is None:
        return None
    return BoardRowPin(
        pin_id=pin.id,
        set_id=pin.set_id,
        set_name=pin.set_name,
        set_version_id=pin.set_version_id,
        version_label=pin.version_label,
        original_label=pin.original_label,
        original_version_id=pin.original_version_id,
        shot_count=pin.shot_count,
    )


def _renamed_note(
    found: tuple[RenamedOnMachine, str] | None, label: str
) -> RenamedOnMachine | None:
    """The note, unless the profile has been renamed again since the sync that took the name."""
    return found[0] if found is not None and found[1] == label else None


def _machine_state(plan: RowPlan, machine: MachineState) -> BoardMachineState:
    """Where one board row stands on the machine, as of the read ``machine`` came from."""
    device_id = plan.held or plan.row.device_profile_id
    copy = machine.profiles.get(device_id) if device_id else None
    return BoardMachineState(
        device_id=device_id,
        present=copy is not None,
        holds_current=plan.held is not None,
        favorite=None if copy is None else copy.favorite,
        selected=None if copy is None else copy.selected,
    )


def _set_brewing(found: LiveSet | None) -> SetBrewing | None:
    """The profile's Set as the pages show it, or ``None``."""
    if found is None:
        return None
    return SetBrewing(set_id=found.set_id, name=found.name, next_minor_label=found.next_minor_label)


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
        # Each file's document is built with the name the file has, which the mirror stored with it.
        profile = document_for(version, summary.label or version.label).model_copy(
            update={"favorite": summary.favorite, "selected": summary.selected}
        )
        state.profiles[summary.device_id] = profile
    return state
