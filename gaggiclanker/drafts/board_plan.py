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

* the file holds what was recorded for it, or **content the profile has had** (somebody put an
  older version back on the display): that is no conflict. It is recorded as what the file
  holds (a summary line), and the profile is handled as usual: a profile that is on has its
  active version put beside it and the file removed, after a fresh load that must match what
  was just recorded; one that is off has the file removed the same way;
* the file holds **content the profile never had** (edited on the display, or a never-seen file
  carrying the profile's name): that is a **conflict**. The content is kept as a version
  (``edited_on_machine``, not active), nothing at all is done for that profile until a person
  keeps one side, and the other profiles sync;
* a file the app **has never seen** (no row stands on it) is never removed or replaced by the
  sync that finds it: content the profile has had attaches it to that profile, a profile's name
  with other content is the machine's side of a conflict, and anything else joins the list as a
  new profile that is on and starred as the machine has it. A second file for a profile that
  already has one is reported, not touched.

**What counts as the machine "holding" a profile**: a file with exactly the row's active
canonical content, under any id, that no other row stands on (a deleted row's file is still
standing until the sync has dealt with it) and no earlier row of this same plan took.

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

from gaggiclanker.db.repos.profile_board import BoardRow, ProfileBoardRepository
from gaggiclanker.db.repos.profile_list import stripped_label
from gaggiclanker.db.repos.profiles import ProfilesRepository, ProfileVersionRow
from gaggiclanker.domain.models import Profile, profile_content_hash
from gaggiclanker.drafts.machine import NO_SUCCESSOR, MachineState

__all__ = [
    "ANOTHER_ROW",
    "CONFLICT",
    "EDITED",
    "FINAL_REFUSALS",
    "NO_SUCCESSOR",
    "RESET_REASON",
    "SHARED_LABEL",
    "BoardAction",
    "BoardPlan",
    "DeletedPlan",
    "PlanBuilder",
    "PolicyCheck",
    "RowPlan",
]

type ActionKind = Literal["adopt", "push", "remove", "leave", "home_screen", "report"]

#: Says why a version may not be pushed under the safety bounds as they are now, or ``None``.
type PolicyCheck = Callable[[ProfileVersionRow], Awaitable[str | None]]

#: Why a file stays while another live profile stands on it.
ANOTHER_ROW = "another profile stands on it"

#: Why a file that was edited on the display stays this sync: its content is recorded as a
#: version of its profile now, and the next sync decides.
EDITED = "edited on the machine since the last sync; recorded as a version, the next sync decides"

#: Why nothing is done for a profile in conflict.
CONFLICT = (
    "the machine's file was changed outside the app and differs from the active version; "
    "nothing is done for this profile until you choose which one to keep"
)

#: The reason of the report for two live profiles with one label.
SHARED_LABEL = "duplicate_label"

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
    #: changes, the one that joins the list. ``None`` for a push (the machine assigns the id).
    device_id: str | None = None
    #: ``adopt``: ``first_pull``, ``unseen`` (a new profile), ``attached`` (to ``row_id``) or
    #: ``conflict`` (a file with the name of ``row_id`` but content that profile never had: it is
    #: attached as the machine's side of a conflict).
    #: ``push``: ``missing``, ``superseded``, ``edited_on_machine``. ``remove`` and ``leave``:
    #: ``superseded``, ``deleted``, ``off`` (switched off) or ``edited_on_machine``.
    #: ``home_screen``: ``on`` or ``off``. ``report``: ``missing``, ``unreadable`` (the machine
    #: listed it and could not load it), ``did_not_verify`` (this version did not read back last
    #: time), ``policy`` (the active version is outside the safety bounds), ``duplicate_label``
    #: (another profile has the same label), ``extra_copy`` (a second file for a profile that
    #: has one), ``conflict`` (the machine's file differs from everything the app knows).
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
    #: A file this sync attaches to the profile. The profile is otherwise left alone this sync.
    attached: str | None = None
    #: The profile is in conflict: a machine file carries content the app has neither recorded
    #: for it nor holds as a version. The write phase does nothing for it until a person chooses
    #: a side. ``conflict_file`` is that file, ``conflict_hash`` the content it holds.
    conflict_file: str | None = None
    conflict_hash: str | None = None


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
    #: Live profiles sharing a label, said once per profile.
    labels: list[BoardAction] = field(default_factory=list)
    #: Second files for a profile that already has one.
    extras: list[BoardAction] = field(default_factory=list)

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
        found.extend(self.labels)
        found.extend(self.extras)
        return found


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

        hashes = {device_id: profile_content_hash(p) for device_id, p in machine.profiles.items()}
        # Every file a row stands on is spoken for, deleted rows' included: the sync has not
        # removed those yet, and a live row taking one would have it removed under it.
        claimed = {row.device_profile_id: row.id for row in rows if row.device_profile_id}
        live_claims: dict[str, set[int]] = {}
        for row in live:
            if row.device_profile_id:
                live_claims.setdefault(row.device_profile_id, set()).add(row.id)
        assigned: set[str] = set()

        computed = Computed(
            adopted=adoption is not None, paused=adoption.paused_reason if adoption else None
        )
        by_row: dict[int, RowPlan] = {}
        for row in live:
            current = await version(row.current_version_id)
            plan = RowPlan(row=row, version=current)
            by_row[row.id] = plan
            computed.rows.append(plan)
            old_id = row.device_profile_id
            if old_id is not None and old_id in machine.unreadable:
                plan.report = _report(row, current.label, "unreadable", old_id, UNREADABLE)
                continue
            if row.on_machine:
                held = _held(row, current.content_hash, machine, hashes, claimed, assigned)
                plan.held = held
                if held is not None:
                    assigned.add(held)
            recorded_hash = await self._recorded_hash(row)
            if (
                old_id is not None
                and old_id in machine.profiles
                and hashes[old_id] != recorded_hash
            ):
                seen = hashes[old_id]
                if seen == current.content_hash or seen in await self.board.listed_hashes(row.id):
                    # Content the profile has had: recorded as what the file holds.
                    plan.edited_file = old_id
                elif seen != row.conflict_overruled_hash:
                    plan.conflict_file, plan.conflict_hash = old_id, seen
                # else: a person chose the app's side for this very content; it is replaced

        # Files nothing stands on and no profile holds: the app has never seen them.
        listed = await self.board.live_version_hashes()
        listed_here: dict[int, set[str]] = {}
        for content_hash, row_ids in listed.items():
            for row_id in row_ids:
                listed_here.setdefault(row_id, set()).add(content_hash)
        unseen = [
            i
            for i in sorted(machine.profiles)
            if i not in claimed and i not in assigned and i not in machine.unreadable
        ]
        joining: dict[str, str] = {}
        for device_id in unseen:
            profile = machine.profiles[device_id]
            match = _match_row(live, listed, hashes[device_id], profile.label)
            if match is not None:
                target = by_row[match.id]
                if (
                    target.attached is not None
                    or _has_file(target, machine)
                    or match.failed_version_id is not None
                ):
                    # (A profile whose last push did not verify and whose bad copy could not be
                    # removed owns that copy: it is no conflict, only a leftover.)
                    computed.extras.append(_extra(device_id, profile.label, match.label))
                elif hashes[device_id] not in listed_here.get(match.id, set()):
                    # Its name, but content this profile never had: the machine's side of a
                    # conflict, not something to attach silently.
                    target.attached = device_id
                    target.conflict_file, target.conflict_hash = device_id, hashes[device_id]
                    computed.adopt.append(
                        BoardAction(
                            kind="adopt",
                            row_id=match.id,
                            label=match.label,
                            device_id=device_id,
                            reason="conflict",
                            detail=CONFLICT,
                            on=profile.favorite,
                        )
                    )
                else:
                    target.attached = device_id
                    computed.adopt.append(
                        BoardAction(
                            kind="adopt",
                            row_id=match.id,
                            label=match.label,
                            device_id=device_id,
                            reason="attached",
                            detail=(
                                f"{device_id} holds {profile.label!r}, which is this profile"
                                + (
                                    ""
                                    if match.on_machine
                                    else " and it is switched off, so the next sync removes it"
                                )
                            ),
                            on=profile.favorite,
                        )
                    )
                continue
            key = stripped_label(profile.label)
            if key in joining:
                computed.extras.append(_extra(device_id, profile.label, joining[key]))
                continue
            joining[key] = profile.label
            computed.adopt.append(
                BoardAction(
                    kind="adopt",
                    label=profile.label,
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
            await self._plan_on(plan, machine, hashes, host, live_claims)
        for plan in computed.rows:
            if (
                plan.report is not None
                or plan.attached is not None
                or plan.conflict_file is not None
                or plan.row.on_machine
            ):
                continue
            await self._plan_off(plan, computed.rows, machine, hashes, live_claims)

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
            plan_d.expected_hash = hashes[old_id]
            if hashes[old_id] != recorded_hash:
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

        computed.labels = _shared_labels(live)
        if (
            computed.paused is None
            and adoption is not None
            and not adoption.resume_pending
            and looks_reset(live, machine)
        ):
            computed.paused = RESET_REASON
        return computed

    async def _plan_on(
        self,
        plan: RowPlan,
        machine: MachineState,
        hashes: dict[str, str],
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
                current.label,
                "did_not_verify",
                None,
                "this version did not read back as sent, so it is not tried again until another "
                "version is made active (making this one active again asks for one more try)",
            )
            return
        if held is None and self.policy is not None:
            refusal = await self.policy(current)
            if refusal is not None:
                plan.report = _report(row, current.label, "policy", None, f"not pushed: {refusal}")
                return
        if held is None:
            plan.push = BoardAction(
                kind="push",
                row_id=row.id,
                label=current.label,
                reason="missing" if old is None else "superseded",
            )
        if old_id is not None and old is not None and old_id != held:
            plan.pred_hash = hashes[old_id]
            plan.pred = _removal(
                row,
                old_id,
                old,
                reason="superseded",
                standing_rows=live_claims.get(old_id, set()) - {row.id},
                selected_without_successor=False,
            )
        if held is not None:
            if machine.profiles[held].favorite != row.on_home_screen:
                plan.home = _home(row, current.label, held, row.on_home_screen)
        elif not row.on_home_screen:
            # The firmware stars every profile it saves; the list says this one is not starred.
            plan.home = _home(row, current.label, None, False)

    async def _plan_off(
        self,
        plan: RowPlan,
        rows: list[RowPlan],
        machine: MachineState,
        hashes: dict[str, str],
        live_claims: dict[str, set[int]],
    ) -> None:
        """A profile that is off: its file goes. Its star is remembered, never applied."""
        row = plan.row
        old_id = row.device_profile_id
        old = machine.profiles.get(old_id) if old_id else None
        if old_id is None or old is None:
            return
        plan.pred_hash = hashes[old_id]
        plan.successor_row_id = _successor_row(rows, exclude=row.id)
        plan.pred = _removal(
            row,
            old_id,
            old,
            reason="off",
            standing_rows=live_claims.get(old_id, set()) - {row.id},
            selected_without_successor=old.selected and plan.successor_row_id is None,
        )

    async def _recorded_hash(self, row: BoardRow) -> str | None:
        if row.device_version_id is None:
            return None
        recorded = await self.profiles.get_version(row.device_version_id)
        return None if recorded is None else recorded.content_hash


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


def _shared_labels(live: list[BoardRow]) -> list[BoardAction]:
    """A report for every live profile that shares its label with another live one.

    The list never makes such a pair (a put or a take that would is refused, and a file the
    machine holds under a label a profile already has is attached to it), but a database that
    carries one from before is said, not refused, and nothing is written for the sake of the pair.
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
            detail=f"another profile is also called {row.label}; "
            "rename or remove one of them so each name is used once",
        )
        for rows in by_label.values()
        if len(rows) > 1
        for row in rows
    ]


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


def _extra(device_id: str, label: str, profile_label: str) -> BoardAction:
    return BoardAction(
        kind="report",
        label=label,
        device_id=device_id,
        reason="extra_copy",
        detail=f"{device_id} is another file for {profile_label!r}, which already has one on the "
        "machine; it was left as it is",
    )


def _has_file(plan: RowPlan, machine: MachineState) -> bool:
    """Whether the profile already stands on a file the machine holds."""
    return plan.held is not None or (
        plan.row.device_profile_id is not None and plan.row.device_profile_id in machine.profiles
    )


def _match_row(
    live: list[BoardRow], listed: dict[str, list[int]], content_hash: str, label: str
) -> BoardRow | None:
    """The live profile a file the app has never seen belongs to: its content, else its name.

    By content first (the file is a version the profile has had), then by exact label, then by
    label without the app suffix; the lowest row id when several fit, so the same inputs always
    choose the same profile.
    """
    ids = set(listed.get(content_hash, []))
    for row in live:
        if row.id in ids:
            return row
    for row in live:
        if row.label == label:
            return row
    base = stripped_label(label)
    for row in live:
        if stripped_label(row.label) == base:
            return row
    return None


def _held(
    row: BoardRow,
    content_hash: str,
    machine: MachineState,
    hashes: dict[str, str],
    claimed: dict[str, int],
    assigned: set[str],
) -> str | None:
    """The file holding the row's active content: its own first, else any unspoken-for one."""
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
