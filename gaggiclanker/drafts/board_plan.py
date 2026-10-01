"""What the next sync would do to the machine, worked out from the board and one read of it.

Reads only. The write phase (:mod:`gaggiclanker.drafts.board`) executes exactly what this
module computes, and the board route serves the same list as the preview, so what a person
is shown and what a sync does cannot be two implementations.

The plan is a pure function of its inputs: the board rows (in id order), the stored versions
they name, the machine as :func:`~gaggiclanker.drafts.machine.read_machine` returned it, and
facts the archive answers (did this app save that id, which Set still brews it). The same
inputs give the same list in the same order: rows by id, live rows before deleted ones, the
adoption list by device id.

**A sync pushes only the app's own versions.** A row whose current version came from an
approved draft (``origin = 'draft'``) is pushed, replaced and removed. A row adopted from the
machine is a profile the person made: it is shown, its home-screen flag is applied while its
file exists, and when its file is missing or was changed the plan reports that and does
nothing else. Nothing that was never through the safety policy is written.

**What counts as the machine "holding" a profile**: a file with exactly the row's current
canonical content, under any id, that no other board row stands on (a deleted row's file is
still standing until the sync has dealt with it) and no earlier row of this same plan took.

**Why an app row and the machine can differ, and what each means**:

* the machine holds nothing for the row, or holds a file that is not what the row last found
  there: ``missing``, a push;
* the file the row stands on still holds the content the row recorded, but the board has moved
  to another version: ``superseded``, a push, then the old file goes if it is the app's;
* the file the row stands on holds something the row never recorded: ``edited_on_machine``.
  The board is the master, so the board's content is pushed; the edited file is **left** (it
  no longer holds what the app saved, and removal expects exactly that) and reported.

**Removal is only ever of a file the app wrote**, still holding exactly the content the archive
recorded for it, that no live board row and no Set is standing on. Anything else is a
``leave`` with the reason. Whether a removal is planned is a prediction made from the same
facts the guards in :func:`~gaggiclanker.drafts.machine.remove_if_ours` read again, on fresh
loads, before any delete; those guards have the last word.

**A machine that looks reset pauses the phase**: when the app's profiles were on the machine
at the last sync and none of their files is there now, nothing is pushed or removed until a
person resumes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.profile_board import BoardRow, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository, ProfileVersionRow
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.domain.models import APP_PROFILE_SUFFIX, Profile, profile_content_hash
from gaggiclanker.drafts.machine import CHANGED_SINCE, NOT_OURS, MachineState

__all__ = [
    "ANOTHER_ROW",
    "FINAL_REFUSALS",
    "NO_SUCCESSOR",
    "RESET_REASON",
    "SHARED_LABEL",
    "BoardAction",
    "BoardPlan",
    "DeletedPlan",
    "PlanBuilder",
    "RowPlan",
]

type ActionKind = Literal["adopt", "push", "remove", "leave", "home_screen", "report"]

#: Why a deleted profile that is selected on the machine stays: removing it would leave the
#: display naming a file that is gone, and nothing else on the board is there to take over.
NO_SUCCESSOR = "selected on the machine and no other board profile is there to select instead"

#: Why a file stays while another live board profile stands on it.
ANOTHER_ROW = "another board profile stands on it"

#: The reason of the report for two live profiles with one label.
SHARED_LABEL = "duplicate_label"

#: What the run records when a sync finds the machine looks reset.
RESET_REASON = "the machine looks reset (none of the app's profiles is on it); nothing written"

#: Refusals that will not change by waiting, so a row lets go of the file. Anything else
#: (a Set still brewing it, nothing to select instead, a machine that did not answer) is
#: asked again on the next sync.
FINAL_REFUSALS = (NOT_OURS, CHANGED_SINCE)


class BoardAction(BaseModel):
    """One thing the next sync would do (or, for ``leave`` and ``report``, would not)."""

    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    #: The board row it belongs to. ``None`` only for ``adopt``.
    row_id: int | None = None
    label: str
    #: The file on the machine concerned: the one to remove or leave, the one whose star
    #: changes, the one adopted. ``None`` for a push (the machine assigns the id).
    device_id: str | None = None
    #: ``adopt``: ``first_pull``. ``push``: ``missing``, ``superseded``, ``edited_on_machine``.
    #: ``remove`` and ``leave``: ``superseded`` or ``deleted``. ``home_screen``: ``on`` or
    #: ``off``. ``report``: ``missing``, ``edited_on_machine`` (a profile the person made, not
    #: pushed), ``unreadable`` (the machine listed it and could not load it),
    #: ``did_not_verify`` (this version did not read back last time), ``duplicate_label``
    #: (another live profile has the same label).
    reason: str
    #: For ``leave`` and ``report``, why, in words a person reads.
    detail: str = ""
    #: ``home_screen``: the star's new state. ``adopt``: the star as the machine has it.
    on: bool | None = None


class BoardPlan(BaseModel):
    """The whole preview: whether the machine's board has been adopted, and the actions."""

    model_config = ConfigDict(extra="forbid")

    adopted: bool
    #: Why a sync would write nothing at all (paused after a suspected reset), else ``None``.
    paused: str | None = None
    #: What is wrong with a profile the sync will not touch (a profile you made that is
    #: missing or changed, a file that could not be read, a version that did not verify).
    #: Not writes, so a machine in sync has no actions even while a report stands.
    reports: list[BoardAction]
    actions: list[BoardAction]


@dataclass
class RowPlan:
    """What one live board row needs, with the machine facts the executor needs with it."""

    row: BoardRow
    version: ProfileVersionRow
    #: The file already holding the row's current content, when there is one.
    held: str | None = None
    push: BoardAction | None = None
    #: The previous file this row stood on that is to go (or stay): ``remove`` or ``leave``.
    pred: BoardAction | None = None
    #: The content the archive recorded for that previous file: what the removal is told to
    #: expect, so a file changed on the display is left.
    pred_hash: str | None = None
    #: Something to say that is not a write (see ``report`` above).
    report: BoardAction | None = None
    home: BoardAction | None = None
    #: The Set whose current version is expected to name the old file, and is not a reason to keep
    #: it: the Set a put records on, or the Set a going back was made from while the Set version
    #: that recorded the version being left is still its current one.
    excluding_set: int | None = None


@dataclass
class DeletedPlan:
    """What one deleted board row still has on the machine."""

    row: BoardRow
    #: ``remove``, ``leave`` or ``report``; ``None`` when the machine no longer holds the file.
    action: BoardAction | None = None
    expected_hash: str | None = None
    successor_id: str | None = None


@dataclass
class Computed:
    """The plan in the shape the executor walks."""

    adopted: bool
    paused: str | None = None
    adopt: list[BoardAction] = field(default_factory=list)
    rows: list[RowPlan] = field(default_factory=list)
    deleted: list[DeletedPlan] = field(default_factory=list)
    #: Live profiles sharing a label, said once per profile. Not a row's own report: a row
    #: that shares a label is pushed, replaced and removed like any other.
    labels: list[BoardAction] = field(default_factory=list)

    def actions(self) -> list[BoardAction]:
        if self.paused:
            return []
        flat: list[BoardAction] = list(self.adopt)
        for plan in self.rows:
            flat.extend(a for a in (plan.push, plan.pred, plan.home) if a is not None)
        flat.extend(
            d.action for d in self.deleted if d.action is not None and d.action.kind != "report"
        )
        return flat

    def reports(self) -> list[BoardAction]:
        found = [p.report for p in self.rows if p.report is not None]
        found.extend(
            d.action for d in self.deleted if d.action is not None and d.action.kind == "report"
        )
        found.extend(self.labels)
        return found


class PlanBuilder:
    """Computes the plan. Holds repositories, never a client."""

    def __init__(
        self,
        board: ProfileBoardRepository,
        profiles: ProfilesRepository,
        sets: SetsRepository,
        writes: DeviceWritesRepository,
    ) -> None:
        self.board = board
        self.profiles = profiles
        self.sets = sets
        self.writes = writes

    async def plan(self, machine: MachineState, *, host: str) -> BoardPlan:
        computed = await self.compute(machine, host=host)
        return BoardPlan(
            adopted=computed.adopted,
            paused=computed.paused,
            reports=computed.reports(),
            actions=computed.actions(),
        )

    async def compute(self, machine: MachineState, *, host: str) -> Computed:
        adoption = await self.board.adoption()
        if adoption is None:
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

        rows = await self.board.list_rows(include_deleted=True)
        live = [row for row in rows if row.deleted_at is None]
        versions: dict[int, ProfileVersionRow] = {}

        async def version(version_id: int) -> ProfileVersionRow:
            if version_id not in versions:
                found = await self.profiles.get_version(version_id)
                if found is None:  # pragma: no cover - a foreign key guarantees it
                    raise RuntimeError(f"profile version {version_id} is missing")
                versions[version_id] = found
            return versions[version_id]

        hashes = {device_id: profile_content_hash(p) for device_id, p in machine.profiles.items()}
        # Every file a row stands on is spoken for, deleted rows' included: the sync has not
        # removed those yet, and a live row taking one would have it removed under it.
        claimed = {row.device_profile_id: row.id for row in rows if row.device_profile_id}
        live_claims: dict[str, set[int]] = {}
        for row in live:
            if row.device_profile_id:
                live_claims.setdefault(row.device_profile_id, set()).add(row.id)
        assigned: set[str] = set()

        computed = Computed(adopted=True, paused=adoption.paused_reason)
        held_by_live: list[tuple[BoardRow, str, bool]] = []
        for row in live:
            current = await version(row.current_version_id)
            old_id = row.device_profile_id
            old = machine.profiles.get(old_id) if old_id else None
            plan = RowPlan(row=row, version=current)
            plan.excluding_set = await self.exempt_set(row)
            computed.rows.append(plan)
            if old_id is not None and old_id in machine.unreadable:
                plan.report = _report(row, current.label, "unreadable", old_id, UNREADABLE)
                continue
            held = _held(row, current.content_hash, machine, hashes, claimed, assigned)
            plan.held = held
            if held is not None:
                assigned.add(held)
                held_by_live.append((row, held, current.utility))
            recorded_hash = await self._recorded_hash(row)
            edited = old is not None and hashes[old_id or ""] != recorded_hash

            if held is None and row.origin != "draft":
                # A profile the person made: never pushed by a sync. Said, and left.
                plan.report = _report(
                    row,
                    current.label,
                    "edited_on_machine" if old is not None else "missing",
                    old_id,
                    (
                        f"{old_id} was changed on the machine since it was recorded"
                        if old is not None
                        else "a profile you made is no longer on the machine as recorded"
                    ),
                )
                if old is not None and old.favorite != row.on_home_screen:
                    plan.home = _home(row, current.label, old_id, row.on_home_screen)
                continue
            if held is None and row.failed_version_id == row.current_version_id:
                plan.report = _report(
                    row,
                    current.label,
                    "did_not_verify",
                    None,
                    "this version did not read back as sent last time and the copy could "
                    "not be removed; change the profile to try again",
                )
                continue
            if held is None:
                plan.push = BoardAction(
                    kind="push",
                    row_id=row.id,
                    label=current.label,
                    reason=(
                        "missing"
                        if old is None
                        else "edited_on_machine"
                        if edited
                        else "superseded"
                    ),
                    detail=(
                        f"{old_id} was changed on the machine; the board's version is put beside it"
                        if edited
                        else ""
                    ),
                )
            if old_id is not None and old is not None and old_id != held:
                plan.pred_hash = recorded_hash
                plan.pred = await self._removal(
                    row,
                    old_id,
                    old,
                    reason="superseded",
                    host=host,
                    recorded_hash=recorded_hash,
                    seen_hash=hashes[old_id],
                    excluding_set=plan.excluding_set,
                    live_claims=live_claims,
                )
            if held is not None:
                if machine.profiles[held].favorite != row.on_home_screen:
                    plan.home = _home(row, current.label, held, row.on_home_screen)
            elif not row.on_home_screen:
                # The firmware stars every profile it saves; the board says this one is off.
                plan.home = _home(row, current.label, None, False)

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
            recorded_hash = await self._recorded_hash(row)
            plan_d.expected_hash = recorded_hash
            successor = _successor(held_by_live, exclude=old_id)
            plan_d.successor_id = successor
            action = await self._removal(
                row,
                old_id,
                old,
                reason="deleted",
                host=host,
                recorded_hash=recorded_hash,
                seen_hash=hashes[old_id],
                excluding_set=None,
                live_claims=live_claims,
            )
            if action.kind == "remove" and old.selected and successor is None:
                action = action.model_copy(update={"kind": "leave", "detail": NO_SUCCESSOR})
            plan_d.action = action

        computed.labels = _shared_labels(live)
        if computed.paused is None and not adoption.resume_pending and looks_reset(live, machine):
            computed.paused = RESET_REASON
        return computed

    async def exempt_set(self, row: BoardRow) -> int | None:
        if row.pending_set_id is not None:
            return row.pending_set_id
        if row.back_from_set_version_id is None:
            return None
        recorded = await self.sets.get_version(row.back_from_set_version_id)
        if recorded is None:
            return None
        current = await self.sets.current_version(recorded.set_id)
        return recorded.set_id if current is not None and current.id == recorded.id else None

    async def _recorded_hash(self, row: BoardRow) -> str | None:
        if row.device_version_id is None:
            return None
        recorded = await self.profiles.get_version(row.device_version_id)
        return None if recorded is None else recorded.content_hash

    async def _removal(
        self,
        row: BoardRow,
        device_id: str,
        copy: Profile,
        *,
        reason: str,
        host: str,
        recorded_hash: str | None,
        seen_hash: str,
        excluding_set: int | None,
        live_claims: dict[str, set[int]],
    ) -> BoardAction:
        """``remove`` when every fact the guards read says the file may go, else ``leave``."""
        refusal = await self.refusal(
            device_id,
            copy,
            host=host,
            version_id=row.device_version_id,
            recorded_hash=recorded_hash,
            seen_hash=seen_hash,
            excluding_set=excluding_set,
            standing_rows=live_claims.get(device_id, set()) - {row.id},
            person_made=row.origin != "draft",
        )
        return BoardAction(
            kind="leave" if refusal else "remove",
            row_id=row.id,
            label=copy.label,
            device_id=device_id,
            reason=reason,
            detail=refusal or "",
        )

    async def refusal(
        self,
        device_id: str,
        copy: Profile,
        *,
        host: str,
        version_id: int | None,
        recorded_hash: str | None,
        seen_hash: str,
        excluding_set: int | None,
        standing_rows: set[int],
        person_made: bool = False,
    ) -> str | None:
        """Why this file is not the app's to remove, or ``None``. Reads the archive only.

        In the order the guards read: the app wrote it, it holds what the archive recorded, and
        nobody else is standing on it.
        """
        labelled = copy.label.rstrip().endswith(APP_PROFILE_SUFFIX.strip())
        if person_made or not labelled or not await self.writes.created_by_us(device_id, host=host):
            return NOT_OURS
        if recorded_hash is None or seen_hash != recorded_hash:
            return CHANGED_SINCE
        if standing_rows:
            return ANOTHER_ROW
        using = await self.sets.sets_currently_using(device_id, version_id, excluding=excluding_set)
        if using:
            return f"still the current version of the Set {using[0]!r}"
        return None


#: What a file the machine listed and then could not load is reported as.
UNREADABLE = "the machine listed it but could not load it; nothing was changed for it"


def _shared_labels(live: list[BoardRow]) -> list[BoardAction]:
    """A report for every live profile that shares its label with another live one.

    The board never makes such a pair (a put or a take that would is refused), but adoption
    takes the machine as it is, and a machine can hold two profiles with one name. They are
    said, not refused, and nothing is written for the sake of the pair.
    """
    by_label: dict[str, list[BoardRow]] = {}
    for row in live:
        by_label.setdefault(row.label, []).append(row)
    return [
        BoardAction(
            kind="report",
            row_id=row.id,
            label=row.label,
            device_id=row.device_profile_id,
            reason=SHARED_LABEL,
            detail=f"another profile on the board is also called {row.label}; "
            "delete one of them so each name is used once",
        )
        for rows in by_label.values()
        if len(rows) > 1
        for row in rows
    ]


def looks_reset(live: list[BoardRow], machine: MachineState) -> bool:
    """Whether none of the app's profiles (with a file on record) is on the machine any more."""
    ours = [r.device_profile_id for r in live if r.origin == "draft" and r.device_profile_id]
    if not ours:
        return False
    return all(i not in machine.profiles and i not in machine.unreadable for i in ours)


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


def _held(
    row: BoardRow,
    content_hash: str,
    machine: MachineState,
    hashes: dict[str, str],
    claimed: dict[str, int],
    assigned: set[str],
) -> str | None:
    """The file holding the row's current content: its own first, else any unspoken-for one."""
    own = row.device_profile_id
    if own is not None and own not in assigned and hashes.get(own) == content_hash:
        return own
    for device_id in sorted(machine.profiles):
        if (
            hashes[device_id] == content_hash
            and device_id not in assigned
            and claimed.get(device_id, row.id) == row.id
        ):
            return device_id
    return None


def _home(row: BoardRow, label: str, device_id: str | None, on: bool) -> BoardAction:
    return BoardAction(
        kind="home_screen",
        row_id=row.id,
        label=label,
        device_id=device_id,
        reason="on" if on else "off",
        on=on,
    )


def _successor(held_by_live: list[tuple[BoardRow, str, bool]], *, exclude: str) -> str | None:
    """The file to select when a deleted profile that is selected goes: a live row's own.

    Never a utility profile (a backflush is not what to brew with), the first one on the home
    screen, else the first at all, by board order: a choice the same inputs always make the
    same way.
    """
    candidates = [
        (row, device_id)
        for row, device_id, utility in held_by_live
        if device_id != exclude and not utility
    ]
    for row, device_id in candidates:
        if row.on_home_screen:
            return device_id
    return candidates[0][1] if candidates else None
