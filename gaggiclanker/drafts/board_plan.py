"""What the next sync would do to the machine, worked out from the list and one read of it.

Reads only. The write phase (:mod:`gaggiclanker.drafts.board`) executes exactly what this
module computes, and the board route serves the same list as the preview, so what a person
is shown and what a sync does cannot be two implementations.

The plan is a pure function of its inputs: the profile rows (in id order), the stored versions
they name, the machine as :func:`~gaggiclanker.drafts.machine.read_machine` returned it, and
the policy's verdict on a version. The same inputs give the same list in the same order: rows
by id, on-the-machine rows before the ones switched off, then deleted ones, discoveries by
device id.

**The machine ends up holding exactly the profiles that are switched on.** Every profile the
app has synced is the app's to manage (firmware defaults and ones made on the display
included): a profile that is **on** is pushed with its active version, whoever made it; one
that is **off** is taken off the machine. What stands between a profile and its removal is not
who made it but whether the app has *seen* what the file holds:

* the file holds what was recorded for it, or **a brew the profile has had** (somebody put an
  older version back on the display): that is no conflict. It is recorded as what the file holds
  (a summary line), and the profile is handled as usual: a profile that is on has its active
  version saved over the file, under a fresh load that must match what was just recorded; one
  that is off has the file removed the same way;
* the file holds **a brew the profile never had** (edited on the display): that is a
  **conflict**, which covers the name too. The content is kept as a version
  (``edited_on_machine``, not active), nothing at all is done for that profile until a person
  keeps one side, and the other profiles sync;
* **the name is decided apart from the brew** (:func:`judge_file`), with the name the app last
  recorded for the file as the one both sides started from: only the app changed it, and the sync
  saves the profile's name over the file; only the machine changed it, and the profile takes the
  machine's name when it is free (a read: nothing is written, with the switch off as well); both
  changed it, or the machine's name is another live profile's, and it is a conflict;
* a file the app **has never seen** (no profile holds its id) is never removed or replaced by the
  sync that finds it: it joins the list as a new profile that is on and starred as the machine
  has it, under its own name (made unique with " (2)" and so on when a live profile has it; the
  next sync renames the file). The files are matched by id and by nothing else; the one exception
  is a machine that looks reset, whose profiles lost their files: once a person resumes, a file
  whose brew is a lost profile's active version attaches to it.

**What counts as the machine "holding" a profile**: its own file (the id the row stands on) with
exactly the row's active brew and the profile's name (a file is compared as
:class:`~gaggiclanker.drafts.machine.FileContent`, brew and name, never by either alone). Another
file with the same content is a profile of its own.

**The selected profile.** Switching off the profile the machine has selected selects the first
profile that is on (list order, never a utility profile) first, then removes it; with no other
profile on the machine it stays, and the plan says why.

**Starred** is applied only while a profile is on the machine, and remembered while it is off.

**A machine that looks reset pauses the phase**: when profiles were on the machine at the last
sync and none of their files is there now, nothing is pushed or removed until a person resumes
it. The plan still says what resuming would do (``would_do``), so the person is asked once with
the real numbers. An empty list (nothing synced yet) is not a reset.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.repos.lineage import free_name, name_key
from gaggiclanker.db.repos.profile_board import BoardRow, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository, ProfileVersionRow
from gaggiclanker.domain.models import Profile, document_hash
from gaggiclanker.drafts.machine import NO_SUCCESSOR, FileContent, MachineState

__all__ = [
    "ANOTHER_ROW",
    "CONFLICT",
    "EDITED",
    "FINAL_REFUSALS",
    "NO_SUCCESSOR",
    "RENAMED_AT_NEXT_SYNC",
    "RESET_REASON",
    "BoardAction",
    "BoardPlan",
    "DeletedPlan",
    "Judgement",
    "PlanBuilder",
    "PolicyCheck",
    "RowPlan",
    "judge_file",
]

type ActionKind = Literal["adopt", "push", "remove", "leave", "home_screen", "report"]

#: Says why a version may not be pushed under the safety bounds as they are now, or ``None``.
type PolicyCheck = Callable[[ProfileVersionRow], Awaitable[str | None]]

#: Why a file stays while another live profile stands on it.
ANOTHER_ROW = "another profile stands on it"

#: Why a file that was edited on the display stays this sync: its content is recorded as a
#: version of its profile now, and the next sync decides.
EDITED = "edited on the machine since the last sync; recorded as a version, the next sync decides"

#: What a push that only saves the profile's name over its file says it will do.
RENAMED_AT_NEXT_SYNC = "Will be renamed on the machine at the next sync"

#: Why nothing is done for a profile in conflict.
CONFLICT = (
    "the machine's file was changed outside the app and differs from the active version; "
    "nothing is done for this profile until you choose which one to keep"
)

#: What the run records when a sync finds the machine looks reset.
RESET_REASON = "the machine looks reset (none of the profiles it held is on it); nothing written"

#: Refusals that will not change by waiting, so a row lets go of the file. Nothing is final any
#: more: a file that changed since it was recorded is recorded by the next sync and then
#: decided, and one another profile stands on or that could not be asked stays on its row.
FINAL_REFUSALS: tuple[str, ...] = ()


class BoardAction(BaseModel):
    """One thing the next sync would do (or, for ``leave`` and ``report``, would not)."""

    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    #: The profile it belongs to. ``None`` for a file that joins the list as a new profile.
    row_id: int | None = None
    label: str
    #: The file on the machine concerned: the one to remove or leave, the one whose star
    #: changes, the one that joins the list, the one a push saves over. ``None`` for a push that
    #: makes a new file (the machine assigns the id).
    device_id: str | None = None
    #: ``adopt``: ``first_pull``, ``unseen`` (a new profile) or ``attached`` (to ``row_id``, a
    #: profile that lost its file in a reset, by its active brew).
    #: ``push``: ``missing`` (a new file), ``superseded`` (a new version over the profile's own
    #: file) or ``renamed`` (only the name is saved over it; ``detail`` says so). ``remove`` and
    #: ``leave``: ``deleted``, ``off`` (switched off) or ``edited_on_machine``.
    #: ``home_screen``: ``on`` or ``off``. ``report``: ``missing``, ``unreadable`` (the machine
    #: listed it and could not load it), ``did_not_verify`` (this version did not read back last
    #: time), ``policy`` (the active version is outside the safety bounds), ``conflict`` (the
    #: machine's file differs from everything the app knows).
    reason: str
    #: For ``leave`` and ``report``, why, in words a person reads.
    detail: str = ""
    #: ``home_screen``: the star's new state. ``adopt``: the star as the machine has it.
    on: bool | None = None


class BoardPlan(BaseModel):
    """The whole preview: whether the machine's profiles have been taken yet, and the actions."""

    model_config = ConfigDict(extra="forbid")

    adopted: bool
    #: Why a sync would write nothing at all (paused after a suspected reset), else ``None``.
    paused: str | None = None
    #: What is wrong with a profile the sync will not touch. Not writes, so a machine in sync has
    #: no actions even while a report stands.
    reports: list[BoardAction]
    actions: list[BoardAction]
    #: While paused: what resuming would do, in the order a sync does it. Empty otherwise.
    would_do: list[BoardAction] = []


@dataclass
class RowPlan:
    """What one live row needs, with the machine facts the executor needs with it."""

    row: BoardRow
    version: ProfileVersionRow
    #: The file already holding the row's active content, when there is one.
    held: str | None = None
    push: BoardAction | None = None
    #: The file this row stood on that is to go (or stay): ``remove`` or ``leave``.
    pred: BoardAction | None = None
    #: What the removal is told to expect: the content the file holds as this plan read it.
    pred_hash: str | None = None
    #: The profile that takes over the selection when the file that goes is the selected one: the
    #: first profile that is on (a row id, resolved to its file when the removal runs).
    successor_row_id: int | None = None
    #: Something to say that is not a write (see ``report`` above).
    report: BoardAction | None = None
    home: BoardAction | None = None
    #: The standing file holds something other than what was recorded for it: the executor
    #: records that content as a version of this profile before it does anything else.
    edited_file: str | None = None
    #: The name the machine gave the profile's file, which the profile takes (see
    #: :func:`judge_file`): nothing is written to the machine for it.
    take_name: str | None = None
    #: A file this sync attaches to the profile. The profile is otherwise left alone this sync.
    attached: str | None = None
    #: The profile is in conflict: a machine file carries content the app has neither recorded
    #: for it nor holds as a version. The write phase does nothing for it until a person chooses
    #: a side. ``conflict_file`` is that file, ``conflict_hash`` the content it holds.
    conflict_file: str | None = None
    #: The document hash of the file in conflict (its brew and its name: what the conflict route
    #: is told) and its brew hash (to find the version the sync keeps for it).
    conflict_hash: str | None = None
    conflict_brew: str | None = None


@dataclass
class DeletedPlan:
    """What one deleted board row still has on the machine."""

    row: BoardRow
    #: ``remove``, ``leave`` or ``report``; ``None`` when the machine no longer holds the file.
    action: BoardAction | None = None
    expected_hash: str | None = None
    successor_row_id: int | None = None
    edited_file: str | None = None


@dataclass
class Computed:
    """The plan in the shape the executor walks."""

    adopted: bool
    paused: str | None = None
    #: Files that join the list: new profiles (``unseen``) and attachments (``attached``).
    adopt: list[BoardAction] = field(default_factory=list)
    rows: list[RowPlan] = field(default_factory=list)
    deleted: list[DeletedPlan] = field(default_factory=list)

    def would_do(self) -> list[BoardAction]:
        """Every action, as a sync that is not paused would take it."""
        flat: list[BoardAction] = list(self.adopt)
        for plan in self.rows:
            flat.extend(a for a in (plan.push, plan.pred, plan.home) if a is not None)
        flat.extend(
            d.action for d in self.deleted if d.action is not None and d.action.kind != "report"
        )
        return flat

    def actions(self) -> list[BoardAction]:
        return [] if self.paused else self.would_do()

    def reports(self) -> list[BoardAction]:
        found = [p.report for p in self.rows if p.report is not None]
        found.extend(
            BoardAction(
                kind="report",
                row_id=p.row.id,
                label=p.row.label,
                device_id=p.conflict_file,
                reason="conflict",
                detail=CONFLICT,
            )
            for p in self.rows
            if p.conflict_file is not None
        )
        found.extend(
            d.action for d in self.deleted if d.action is not None and d.action.kind == "report"
        )
        return found


@dataclass(frozen=True)
class Judgement:
    """What the app makes of a profile's own file that differs from what it recorded for it."""

    #: ``record``: the file holds content the profile has had (or a person chose the app's side of
    #: exactly this file): it is recorded as what the file holds, and the profile goes on from
    #: there. ``take_name``: only the machine renamed it, and the name is free: the profile takes
    #: it. ``conflict``: the brew is content the profile never had, or the name was changed on
    #: both sides, or the machine's name is another live profile's: nothing is done for the
    #: profile until a person chooses.
    outcome: Literal["record", "take_name", "conflict"]
    #: The live profile that has the machine's name, when that is why it is a conflict.
    holder: BoardRow | None = None
    #: A conflict about the name alone: the file's brew is one the profile has had (the active one
    #: or an older one), so only the name is in question.
    name_only: bool = False


async def judge_file(
    board: ProfileBoardRepository,
    row: BoardRow,
    seen: FileContent,
    recorded: FileContent | None,
    document: str,
    expected: FileContent,
) -> Judgement:
    """The one rule for a profile's own file that is not what was recorded for it: the brew and
    the name are decided separately.

    **The brew**: a brew the profile has had (its active one, the one recorded for the file, or any
    version it lists) is recorded; one it never had is a conflict, which covers the name too,
    unless a person chose the app's side of exactly this document (``document`` is the file's
    document hash).

    **The name**, with ``recorded.name`` (``device_label``) as the last name the app recorded for
    that file: only the app changed it (the file's name is the recorded one, the profile's is not)
    is left to the save that follows; only the machine changed it takes the machine's name when it
    is free, else asks; both changed it asks. A file already called what the profile is called has
    no name question.
    """
    known = {expected.brew} | await board.listed_brews(row.id)
    if recorded is not None:
        known.add(recorded.brew)
    if seen.brew not in known:
        if document == row.conflict_overruled_hash:
            return Judgement("record")
        return Judgement("conflict")
    if seen.name == row.label or (recorded is not None and seen.name == recorded.name):
        return Judgement("record")
    if recorded is None or row.label != recorded.name:
        # The name was changed on both sides, or nothing says who changed it.
        return (
            Judgement("record")
            if document == row.conflict_overruled_hash
            else Judgement("conflict", name_only=True)
        )
    holder = await board.find_live_by_name_key(seen.name, excluding=row.id)
    if holder is not None:
        if document == row.conflict_overruled_hash:
            return Judgement("record")
        return Judgement("conflict", holder, name_only=True)
    return Judgement("take_name")


class PlanBuilder:
    """Computes the plan. Holds repositories, never a client."""

    def __init__(
        self,
        board: ProfileBoardRepository,
        profiles: ProfilesRepository,
        *,
        policy: PolicyCheck | None = None,
    ) -> None:
        self.board = board
        self.profiles = profiles
        #: The safety policy as it is now. ``None`` only in a test that is not about it.
        self.policy = policy

    async def plan(self, machine: MachineState, *, host: str) -> BoardPlan:
        computed = await self.compute(machine, host=host)
        return BoardPlan(
            adopted=computed.adopted,
            paused=computed.paused,
            reports=computed.reports(),
            actions=computed.actions(),
            would_do=computed.would_do() if computed.paused else [],
        )

    async def compute(
        self, machine: MachineState, *, host: str, assume_adopted: bool = False
    ) -> Computed:
        """The plan. ``assume_adopted`` is for the first sync, which takes the machine's files
        into the list by the same rules as every later one and then writes nothing."""
        adoption = await self.board.adoption()
        rows = await self.board.list_rows(include_deleted=True)
        live = [row for row in rows if row.deleted_at is None]
        if adoption is None and not assume_adopted:
            return Computed(
                adopted=False,
                adopt=[
                    BoardAction(
                        kind="adopt",
                        label=profile.label,
                        device_id=device_id,
                        reason="first_pull",
                        on=profile.favorite,
                    )
                    for device_id, profile in sorted(machine.profiles.items())
                ],
            )

        versions: dict[int, ProfileVersionRow] = {}

        async def version(version_id: int) -> ProfileVersionRow:
            if version_id not in versions:
                found = await self.profiles.get_version(version_id)
                if found is None:  # pragma: no cover - a foreign key guarantees it
                    raise RuntimeError(f"profile version {version_id} is missing")
                versions[version_id] = found
            return versions[version_id]

        # A file is its brew and its name; the document hash is what a removal's guard and a
        # conflict's choice are told (the exact bytes the machine holds).
        files = {device_id: FileContent.of(p) for device_id, p in machine.profiles.items()}
        docs = {device_id: document_hash(p) for device_id, p in machine.profiles.items()}
        # Every file a row stands on is spoken for, deleted rows' included: the sync has not
        # removed those yet, and a live row taking one would have it removed under it.
        claimed = {row.device_profile_id: row.id for row in rows if row.device_profile_id}
        live_claims: dict[str, set[int]] = {}
        for row in live:
            if row.device_profile_id:
                live_claims.setdefault(row.device_profile_id, set()).add(row.id)

        # A machine that looks reset has lost its files, and what a person resumes is what is shown
        # while it is paused and done once resumed: only then may a file attach to a profile that
        # lost its own, by its brew.
        paused = adoption.paused_reason if adoption else None
        if (
            paused is None
            and adoption is not None
            and not adoption.resume_pending
            and looks_reset(live, machine)
        ):
            paused = RESET_REASON
        resuming = paused is not None or (adoption is not None and adoption.resume_pending)

        computed = Computed(adopted=adoption is not None, paused=paused)
        by_row: dict[int, RowPlan] = {}
        for row in live:
            current = await version(row.current_version_id)
            plan = RowPlan(row=row, version=current)
            by_row[row.id] = plan
            computed.rows.append(plan)
            old_id = row.device_profile_id
            if old_id is not None and old_id in machine.unreadable:
                plan.report = _report(row, row.label, "unreadable", old_id, UNREADABLE)
                continue
            expected = FileContent(current.content_hash, row.label)
            recorded = await self._recorded(row)
            if old_id is not None and old_id in machine.profiles and files[old_id] != recorded:
                judged = await judge_file(
                    self.board, row, files[old_id], recorded, docs[old_id], expected
                )
                if judged.outcome == "conflict":
                    plan.conflict_file, plan.conflict_hash = old_id, docs[old_id]
                    plan.conflict_brew = files[old_id].brew
                else:
                    # Content the profile has had, or a person's choice of the app's side for
                    # this very file: recorded as what the file holds.
                    plan.edited_file = old_id
                    if judged.outcome == "take_name":
                        plan.take_name = files[old_id].name
                        expected = FileContent(current.content_hash, files[old_id].name)
            if row.on_machine:
                plan.held = _held(row, expected, files)

        # Files no profile holds the id of: the app has never seen them. A file is a profile of its
        # own, whatever it is called and whatever it holds; matching goes by id and by nothing else.
        # The one exception is a resumed reset, whose profiles lost their files.
        unseen = [
            i for i in sorted(machine.profiles) if i not in claimed and i not in machine.unreadable
        ]
        taken_names = {name_key(row.label) for row in live}
        lost = [
            plan
            for plan in computed.rows
            if plan.report is None
            and plan.row.device_profile_id is not None
            and plan.row.device_profile_id not in machine.profiles
        ]
        # After a resumed reset a profile that lost its file takes a file with its active brew
        # first (every file, whatever the order of their ids), and only then a file with any brew
        # it has had: the next sync saves the active version over that one.
        attaching: dict[str, RowPlan] = {}
        if resuming:
            for any_brew in (False, True):
                for device_id in unseen:
                    if device_id in attaching:
                        continue
                    brew = files[device_id].brew
                    for plan in lost:
                        if any(plan is p for p in attaching.values()):
                            continue
                        if plan.version.content_hash == brew or (
                            any_brew and brew in await self.board.listed_brews(plan.row.id)
                        ):
                            attaching[device_id] = plan
                            break
        for device_id in unseen:
            profile = machine.profiles[device_id]
            if resuming:
                target = attaching.get(device_id)
                if target is not None:
                    target.attached = device_id
                    computed.adopt.append(
                        BoardAction(
                            kind="adopt",
                            row_id=target.row.id,
                            label=target.row.label,
                            device_id=device_id,
                            reason="attached",
                            detail=(
                                f"{device_id} brews what {target.row.label!r} has brewed, and that "
                                "profile lost its file"
                                + (
                                    ""
                                    if target.row.on_machine
                                    else " and it is switched off, so the next sync removes it"
                                )
                            ),
                            on=profile.favorite,
                        )
                    )
                    continue
            computed.adopt.append(
                BoardAction(
                    kind="adopt",
                    label=free_name(profile.label, taken_names),
                    device_id=device_id,
                    reason="unseen",
                    on=profile.favorite,
                )
            )

        # Switched on first (pushes before any removal), then switched off.
        for plan in computed.rows:
            if (
                plan.report is not None
                or plan.attached is not None
                or plan.conflict_file is not None
                or not plan.row.on_machine
            ):
                continue
            await self._plan_on(plan, machine, docs, host, live_claims)
        for plan in computed.rows:
            if (
                plan.report is not None
                or plan.attached is not None
                or plan.conflict_file is not None
                or plan.row.on_machine
            ):
                continue
            await self._plan_off(plan, computed.rows, machine, docs, live_claims)

        for row in rows:
            if row.deleted_at is None or not row.device_profile_id:
                continue
            old_id = row.device_profile_id
            plan_d = DeletedPlan(row=row)
            computed.deleted.append(plan_d)
            if old_id in machine.unreadable:
                plan_d.action = _report(row, row.label, "unreadable", old_id, UNREADABLE)
                continue
            old = machine.profiles.get(old_id)
            if old is None:
                continue
            recorded = await self._recorded(row)
            plan_d.expected_hash = docs[old_id]
            if files[old_id] != recorded:
                plan_d.edited_file = old_id
                plan_d.action = BoardAction(
                    kind="leave",
                    row_id=row.id,
                    label=old.label,
                    device_id=old_id,
                    reason="edited_on_machine",
                    detail=EDITED,
                )
                continue
            plan_d.successor_row_id = _successor_row(computed.rows, exclude=row.id)
            plan_d.action = _removal(
                row,
                old_id,
                old,
                reason="deleted",
                standing_rows=live_claims.get(old_id, set()) - {row.id},
                selected_without_successor=old.selected and plan_d.successor_row_id is None,
            )

        return computed

    async def _plan_on(
        self,
        plan: RowPlan,
        machine: MachineState,
        docs: dict[str, str],
        host: str,
        live_claims: dict[str, set[int]],
    ) -> None:
        """A profile that is on: push its active version when the machine lacks it."""
        row, current = plan.row, plan.version
        old_id = row.device_profile_id
        old = machine.profiles.get(old_id) if old_id else None
        held = plan.held
        if held is None and row.failed_version_id == row.current_version_id:
            plan.report = _report(
                row,
                row.label,
                "did_not_verify",
                None,
                "this version did not read back as sent, so it is not tried again until another "
                "version is made active (making this one active again asks for one more try)",
            )
            return
        if held is None and self.policy is not None:
            refusal = await self.policy(current)
            if refusal is not None:
                plan.report = _report(row, row.label, "policy", None, f"not pushed: {refusal}")
                return
        if held is None:
            # Its own file is saved over in place; with none, a new file is saved. When the file
            # already holds the active brew, only the name is saved.
            renamed = old is not None and FileContent.of(old).brew == current.content_hash
            plan.push = BoardAction(
                kind="push",
                row_id=row.id,
                label=row.label,
                reason="missing" if old is None else "renamed" if renamed else "superseded",
                device_id=old_id if old is not None else None,
                detail=RENAMED_AT_NEXT_SYNC if renamed else "",
            )
        if old_id is not None and old is not None:
            # A save over its own file keeps the star where it was (the firmware stars only a
            # file it creates), so the star is judged against the file as it is.
            if old.favorite != row.on_home_screen:
                plan.home = _home(row, row.label, old_id, row.on_home_screen)
        elif not row.on_home_screen:
            # The firmware stars every profile it saves; the list says this one is not starred.
            plan.home = _home(row, row.label, None, False)

    async def _plan_off(
        self,
        plan: RowPlan,
        rows: list[RowPlan],
        machine: MachineState,
        docs: dict[str, str],
        live_claims: dict[str, set[int]],
    ) -> None:
        """A profile that is off: its file goes. Its star is remembered, never applied."""
        row = plan.row
        old_id = row.device_profile_id
        old = machine.profiles.get(old_id) if old_id else None
        if old_id is None or old is None:
            return
        plan.pred_hash = docs[old_id]
        plan.successor_row_id = _successor_row(rows, exclude=row.id)
        plan.pred = _removal(
            row,
            old_id,
            old,
            reason="off",
            standing_rows=live_claims.get(old_id, set()) - {row.id},
            selected_without_successor=old.selected and plan.successor_row_id is None,
        )

    async def _recorded(self, row: BoardRow) -> FileContent | None:
        """What the app last recorded its file as holding: the recorded version's brew under the
        name of the document recorded (``None`` when nothing is recorded)."""
        if row.device_version_id is None:
            return None
        recorded = await self.profiles.get_version(row.device_version_id)
        if recorded is None:  # pragma: no cover - a foreign key guarantees it
            return None
        return FileContent(recorded.content_hash, row.device_label or "")


#: What a file the machine listed and then could not load is reported as.
UNREADABLE = "the machine listed it but could not load it; nothing was changed for it"


def _removal(
    row: BoardRow,
    device_id: str,
    copy: Profile,
    *,
    reason: str,
    standing_rows: set[int],
    selected_without_successor: bool,
) -> BoardAction:
    """``remove`` when every fact the plan can read says the file may go, else ``leave``.

    A prediction made from the same facts the executor reads again, on fresh loads, before any
    delete; the guards there have the last word.
    """
    refusal: str | None = None
    if standing_rows:
        refusal = ANOTHER_ROW
    elif selected_without_successor:
        refusal = NO_SUCCESSOR
    return BoardAction(
        kind="leave" if refusal else "remove",
        row_id=row.id,
        label=copy.label,
        device_id=device_id,
        reason=reason,
        detail=refusal or "",
    )


def looks_reset(live: list[BoardRow], machine: MachineState) -> bool:
    """Whether none of the files the last sync left (any profile's) is on the machine any more."""
    standing = [r.device_profile_id for r in live if r.device_profile_id]
    if not standing:
        return False
    return all(i not in machine.profiles and i not in machine.unreadable for i in standing)


def _report(
    row: BoardRow, label: str, reason: str, device_id: str | None, detail: str
) -> BoardAction:
    return BoardAction(
        kind="report",
        row_id=row.id,
        label=label,
        device_id=device_id,
        reason=reason,
        detail=detail,
    )


def _held(row: BoardRow, expected: FileContent, files: dict[str, FileContent]) -> str | None:
    """The row's own file when it holds the row's active content (its brew under its name).

    A profile is held only by its own file: another file with the same content is a profile of
    its own.
    """
    own = row.device_profile_id
    return own if own is not None and files.get(own) == expected else None


def _home(row: BoardRow, label: str, device_id: str | None, on: bool) -> BoardAction:
    return BoardAction(
        kind="home_screen",
        row_id=row.id,
        label=label,
        device_id=device_id,
        reason="on" if on else "off",
        on=on,
    )


def _successor_row(rows: list[RowPlan], *, exclude: int) -> int | None:
    """The profile that takes the selection when the selected one goes: the first that is on.

    List order (row id), never a utility profile (a backflush is not what to brew with), and
    only one the machine will hold: it has its file, or this sync pushes it. A choice the same
    inputs always make the same way.
    """
    for plan in rows:
        if (
            plan.row.id != exclude
            and plan.row.on_machine
            and not plan.version.utility
            and plan.report is None
            and plan.attached is None
            and plan.conflict_file is None
            and (plan.held is not None or plan.push is not None)
        ):
            return plan.row.id
    return None
