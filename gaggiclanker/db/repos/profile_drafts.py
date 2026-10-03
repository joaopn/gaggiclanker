"""`profile_drafts` — a proposed profile and how far it has got.

The document is deliberately *not* here. A draft's profile is a
`profile_versions` row with `source = 'draft'`, exactly like a mirrored or an
imported one, and :attr:`ProfileDraftRow.draft_version_id` points at it. Profile
versions are content-hashed and immutable and shots resolve to them; a draft that
becomes the profile a Set is brewed with has to be the same kind of thing as
every other profile in the archive, not a blob in a table every later join would
have to special-case.

What *is* here is the state machine and the evidence:

    draft ──made active──> approved ──the sync puts it on the machine──> pushed
      │                            │
      │                            └──refine──> superseded
      └──discard──> discarded


and, beside it, the three things a person has to see before they press the
button: what the policy clamped, which stop conditions moved, and — for a draft whose profile
did not read back as sent — both documents, ours and the machine's.

`failed` is only ever read now: the staged push that used to leave a draft there is gone,
but a database may still hold one. Going back to a previous version or deleting a profile
discards the draft behind the file the sync then takes off the machine.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonList, JsonObject, dumps, utc_now
from gaggiclanker.db.repos.version_names import label_sql, next_names
from gaggiclanker.db.repository import Repository

__all__ = [
    "DRAFT_STATUSES",
    "OPEN_STATUSES",
    "DraftStatus",
    "ProfileDraftRow",
    "ProfileDraftWrite",
    "ProfileDraftsRepository",
]

type DraftStatus = Literal["draft", "approved", "pushed", "failed", "discarded", "superseded"]

#: Every value the `status` CHECK allows, in the order the state machine moves
#: through them. Exported so the API and the front end can be generated from one
#: list rather than three that drift.
DRAFT_STATUSES: tuple[str, ...] = (
    "draft",
    "approved",
    "pushed",
    "failed",
    "discarded",
    "superseded",
)

#: The ones still waiting for a person. `failed` is open on purpose: a push that
#: did not verify leaves a profile on the machine that somebody has to decide
#: about, and hiding it in a "done" bucket is how it stays there.
OPEN_STATUSES: tuple[str, ...] = ("draft", "approved", "failed")


class ProfileDraftWrite(BaseModel):
    """What :meth:`ProfileDraftsRepository.create` inserts."""

    model_config = ConfigDict(extra="forbid")

    base_version_id: int
    draft_version_id: int | None = None
    source_analysis_id: int | None = None
    source_suggestion_id: int | None = None
    parent_draft_id: int | None = None
    #: Which file on the display the base version was mirrored under when this
    #: draft was made. NULL when it was not on the machine at all.
    base_device_profile_id: str | None = None
    #: Who made the draft (migration 0037).
    made_by: Literal["agent", "edit"] | None = None
    #: The Set this was proposed for, when it was proposed inside one Set's
    #: conversation. It is what makes the prediction below mean something: a
    #: prediction is about one experiment, and pushing this draft for any other
    #: Set records none.
    set_id: int | None = None
    #: What this profile change is expected to do differently. Empty for a draft
    #: nobody predicted anything about — one typed by hand, or proposed in a
    #: conversation that is about no Set.
    prediction: str = ""
    #: Which version of that Set the prediction is measured against.
    compares_to_version_id: int | None = None
    #: The agent thinks recording this on its Set is a major version — a
    #: functional change to what the profile does rather than dialling in —
    #: and why. Only a suggestion: the push card preselects its box from it
    #: and the person decides.
    suggest_major: bool = False
    major_reason: str = Field(default="", max_length=500)
    #: A profile designed from scratch rather than an edit of its base. See the row.
    is_new: bool = False
    change_summary: str = ""
    stop_condition_changes: list[Any] = Field(default_factory=list)
    clamp_changes: list[Any] = Field(default_factory=list)
    notes: str = ""


class ProfileDraftRow(BaseModel):
    """One draft, with the joined labels a reader needs beside it."""

    model_config = ConfigDict(extra="forbid")

    id: int
    base_version_id: int
    draft_version_id: int | None = None
    source_analysis_id: int | None = None
    source_suggestion_id: int | None = None
    parent_draft_id: int | None = None
    base_device_profile_id: str | None = None
    made_by: Literal["agent", "edit"] | None = None
    #: The Set this was proposed for, the prediction it carries, and the version
    #: that prediction is against. All three are empty on a draft nobody
    #: predicted anything about, which is most of them.
    set_id: int | None = None
    #: Joined, so a card can say which experiment this belongs to without a
    #: second request. NULL when the Set has since been removed.
    set_name: str | None = None
    prediction: str = ""
    compares_to_version_id: int | None = None
    #: The compared-to version's name, joined in: a reader thinks in "v1.1".
    compares_to_version_label: str | None = None
    #: The agent's suggestion that recording this on its Set is a major
    #: version, and its reason. The push card preselects "Major change" from
    #: it; the person decides.
    suggest_major: bool = False
    major_reason: str = ""
    #: A profile designed from scratch, not an edit: its base version exists only because a
    #: draft must have one (the synthetic empty baseline, or the library's most-used profile),
    #: so a new draft serves no base profile and no base label, and the card shows the profile
    #: instead of a diff. Landing it on the board never continues the base's row.
    is_new: bool = False
    #: What that version would be called as a minor ("v1.3") and as a major
    #: ("v2"): the push button names one of them, whichever the person ticks.
    #: NULL without a Set. Filled by the repository from the same query the
    #: insert numbers with, so the button and the record cannot disagree.
    set_next_minor_label: str | None = None
    set_next_major_label: str | None = None
    #: The version of this draft's Set that its push recorded, prediction and
    #: all. NULL when it was pushed without recording it there (or for another
    #: Set), or not pushed yet. Read through `recorded_version_id`, which the push
    #: stores: two drafts of one profile that share a device id (the second found
    #: the first on the machine) each find their own version. A rollback clears the
    #: device id and the name goes with it.
    #: That version's name, "v1.3".
    recorded_version_label: str | None = None
    #: Whether the machine still holds the profile this was drafted from.
    #:
    #: Computed in SQL rather than stored, because it is a fact about *now*: a
    #: draft made this morning against a profile somebody edited at lunchtime is
    #: stale by the afternoon without anything about the draft changing. Current
    #: when the mirror has the base's content under any device id; stale only when
    #: its own id holds something else now. `True` for a base that was never on the
    #: machine, or that is gone from it (a reset display): there is nothing left it
    #: could have drifted from, and a push simply adds. A push re-reads the machine
    #: and decides by what it finds, not by this.
    base_is_current: bool = True
    change_summary: str = ""
    #: The per-phase target changes computed at draft time. Stored rather than
    #: recomputed, because the approval is *about this list* and the list that
    #: was acknowledged has to be the list that was shown.
    stop_condition_changes: JsonList = Field(
        default=None, validation_alias="stop_condition_changes_json"
    )
    #: Every number the safety policy moved on the way in, with before and after.
    clamp_changes: JsonList = Field(default=None, validation_alias="clamp_changes_json")
    notes: str = ""
    status: str = "draft"
    acknowledged_stop_changes: bool = False
    pushed_device_profile_id: str | None = None
    #: Both documents when the round trip disagreed: what we sent and what the
    #: machine served back, plus their canonical forms.
    verification: JsonObject = Field(default=None, validation_alias="verification_json")
    error: str | None = None
    #: Whether this draft's push saved a profile (``True``) or found an identical one
    #: already on the machine and used it. A rollback removes only one its own push saved.
    pushed_saved: bool = False
    #: The device id of the predecessor this draft's push removed from the machine,
    #: and the stored version holding the content the archive recorded for it, so a
    #: rollback knows to bring that content back first. NULL when the push left the
    #: predecessor alone or had none.
    replaced_device_profile_id: str | None = None
    replaced_version_id: int | None = None
    #: The Set versions that named the removed predecessor and were cleared by its
    #: removal: the only ones a rollback points at the restored copy.
    cleared_set_version_ids: JsonList = Field(
        default=None, validation_alias="cleared_set_version_ids_json"
    )
    #: The Set version this push recorded, when it recorded one.
    recorded_version_id: int | None = None
    #: The draft whose push removed this one's profile from the machine. Such a draft
    #: has nothing left to roll back.
    replaced_by_draft_id: int | None = None
    #: What the latest machine action on this draft did (a push or a rollback):
    #: ``action``, the ids involved, ``startup_profile_cleared`` and ``lines``, the
    #: sentences the card shows. Overwritten by the next action.
    outcome: JsonObject = Field(default=None, validation_alias="outcome_json")
    created_at: str
    updated_at: str
    #: Joined from `profile_versions`, so a list row can say what it is about
    #: without a second request per draft.
    base_label: str | None = None
    draft_label: str | None = None


_SELECT = f"""
    SELECT d.*,
           CASE WHEN d.is_new THEN NULL ELSE base.label END AS base_label,
           drafted.label AS draft_label,
           s.name AS set_name,
           {label_sql("cmp")} AS compares_to_version_label,
           (SELECT {label_sql("rv")} FROM set_versions rv
             WHERE rv.id = d.recorded_version_id
               AND rv.set_id = d.set_id
               AND d.pushed_device_profile_id IS NOT NULL) AS recorded_version_label,
           CASE
               WHEN d.is_new THEN 1
               WHEN d.base_device_profile_id IS NULL THEN 1
               WHEN EXISTS (
                   SELECT 1 FROM device_profiles dp
                    WHERE dp.deleted_at IS NULL
                      AND dp.current_version_id = d.base_version_id
               ) THEN 1
               ELSE NOT EXISTS (
                   SELECT 1 FROM device_profiles dp
                    WHERE dp.device_id = d.base_device_profile_id
                      AND dp.deleted_at IS NULL
               )
           END AS base_is_current
    FROM profile_drafts d
    LEFT JOIN profile_versions base ON base.id = d.base_version_id
    LEFT JOIN profile_versions drafted ON drafted.id = d.draft_version_id
    LEFT JOIN sets s ON s.id = d.set_id
    LEFT JOIN set_versions cmp ON cmp.id = d.compares_to_version_id
"""  # noqa: S608 - the only interpolation is the version label expression, a constant


class ProfileDraftsRepository(Repository):
    """Reads and writes the draft queue."""

    async def create(self, write: ProfileDraftWrite) -> ProfileDraftRow:
        now = utc_now()
        cursor = await self.db.execute(
            """
            INSERT INTO profile_drafts
                (base_version_id, draft_version_id, source_analysis_id, source_suggestion_id,
                 parent_draft_id, base_device_profile_id, set_id, prediction,
                 compares_to_version_id, suggest_major, major_reason, is_new, change_summary,
                 stop_condition_changes_json, clamp_changes_json, notes, status,
                 made_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
            """,
            (
                write.base_version_id,
                write.draft_version_id,
                write.source_analysis_id,
                write.source_suggestion_id,
                write.parent_draft_id,
                write.base_device_profile_id,
                write.set_id,
                write.prediction,
                write.compares_to_version_id,
                int(write.suggest_major),
                write.major_reason,
                int(write.is_new),
                write.change_summary,
                dumps(write.stop_condition_changes),
                dumps(write.clamp_changes),
                write.notes,
                write.made_by,
                now,
                now,
            ),
        )
        row = await self.get(int(cursor.lastrowid or 0))
        if row is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("profile draft vanished between write and read")
        return row

    async def get(self, draft_id: int) -> ProfileDraftRow | None:
        row = await self.db.fetch_one(f"{_SELECT} WHERE d.id = ?", (draft_id,))
        draft = self.to_model(ProfileDraftRow, row)
        return None if draft is None else (await self._with_next_names([draft]))[0]

    async def _with_next_names(self, drafts: list[ProfileDraftRow]) -> list[ProfileDraftRow]:
        """Fill in what the next version of each draft's Set would be called.

        Asked of :func:`next_names`, the query the insert itself numbers with,
        once per Set however many drafts share it. A draft with no Set has
        nothing to name.
        """
        names: dict[int, tuple[str, str]] = {}
        filled: list[ProfileDraftRow] = []
        for draft in drafts:
            if draft.set_id is None or draft.set_name is None:
                filled.append(draft)
                continue
            if draft.set_id not in names:
                found = await next_names(self.db, draft.set_id)
                names[draft.set_id] = (found.minor, found.major)
            minor, major = names[draft.set_id]
            filled.append(
                draft.model_copy(
                    update={"set_next_minor_label": minor, "set_next_major_label": major}
                )
            )
        return filled

    async def list_drafts(
        self, *, status: str | None = None, open_only: bool = False, limit: int = 100
    ) -> list[ProfileDraftRow]:
        """Newest first. ``open_only`` is the queue; everything else is history."""
        where = ["1 = 1"]
        params: list[object] = []
        if status is not None:
            where.append("d.status = ?")
            params.append(status)
        elif open_only:
            placeholders = ", ".join("?" for _ in OPEN_STATUSES)
            where.append(f"d.status IN ({placeholders})")
            params.extend(OPEN_STATUSES)
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE {' AND '.join(where)} ORDER BY d.id DESC LIMIT ?",
            [*params, limit],
        )
        return await self._with_next_names(self.to_models(ProfileDraftRow, rows))

    async def set_status(
        self,
        draft_id: int,
        status: DraftStatus,
        *,
        acknowledged: bool | None = None,
        pushed_device_profile_id: str | None = None,
        verification: dict[str, Any] | None = None,
        error: str | None = None,
        outcome: dict[str, Any] | None = None,
        pushed_saved: bool | None = None,
        replaced_device_profile_id: str | None = None,
        replaced_version_id: int | None = None,
        cleared_set_version_ids: list[int] | None = None,
    ) -> ProfileDraftRow | None:
        """Move a draft along, setting only the fields this transition owns.

        Every argument but ``status`` defaults to "leave it alone", so a push
        that fails does not blank the acknowledgement the approval recorded and
        a later rollback does not blank the verification evidence.
        """
        assignments = ["status = ?", "updated_at = ?"]
        params: list[object] = [status, utc_now()]
        if acknowledged is not None:
            assignments.append("acknowledged_stop_changes = ?")
            params.append(int(acknowledged))
        if pushed_device_profile_id is not None:
            assignments.append("pushed_device_profile_id = ?")
            params.append(pushed_device_profile_id)
        if verification is not None:
            assignments.append("verification_json = ?")
            params.append(dumps(verification))
        if error is not None:
            assignments.append("error = ?")
            params.append(error)
        if outcome is not None:
            assignments.append("outcome_json = ?")
            params.append(dumps(outcome))
        if pushed_saved is not None:
            assignments.append("pushed_saved = ?")
            params.append(int(pushed_saved))
        if replaced_device_profile_id is not None:
            assignments.append("replaced_device_profile_id = ?")
            params.append(replaced_device_profile_id)
        if replaced_version_id is not None:
            assignments.append("replaced_version_id = ?")
            params.append(replaced_version_id)
        if cleared_set_version_ids is not None:
            assignments.append("cleared_set_version_ids_json = ?")
            params.append(dumps(cleared_set_version_ids))
        await self.db.execute(
            f"UPDATE profile_drafts SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608 - assignments are literals, values are bound
            [*params, draft_id],
        )
        return await self.get(draft_id)

    async def clear_pushed_profile(
        self, draft_id: int, *, outcome: dict[str, Any] | None = None
    ) -> ProfileDraftRow | None:
        """Forget the device id after a rollback deleted the machine's copy.

        The draft stays `failed` — what happened, happened — but it must stop
        naming a profile that is no longer there, or the rollback button comes
        back and deletes whatever inherits that id next.
        """
        await self.db.execute(
            "UPDATE profile_drafts SET pushed_device_profile_id = NULL, pushed_saved = 0, "
            "replaced_device_profile_id = NULL, replaced_version_id = NULL, "
            "cleared_set_version_ids_json = NULL, recorded_version_id = NULL, "
            "outcome_json = COALESCE(?, outcome_json), updated_at = ? WHERE id = ?",
            (None if outcome is None else dumps(outcome), utc_now(), draft_id),
        )
        return await self.get(draft_id)

    async def set_recorded_version(self, draft_id: int, version_id: int | None) -> None:
        """Remember which Set version this draft's push recorded."""
        await self.db.execute(
            "UPDATE profile_drafts SET recorded_version_id = ? WHERE id = ?", (version_id, draft_id)
        )

    async def supersede_pushed(self, device_id: str, *, by_draft_id: int) -> int:
        """Mark every pushed draft of this device profile as replaced by a later push.

        Called once the profile is off the machine: those drafts hold a profile that
        is gone, so they have nothing left to roll back.
        """
        cursor = await self.db.execute(
            "UPDATE profile_drafts SET replaced_by_draft_id = ?, updated_at = ? "
            "WHERE status = 'pushed' AND pushed_device_profile_id = ? "
            "AND replaced_by_draft_id IS NULL AND id != ?",
            (by_draft_id, utc_now(), device_id, by_draft_id),
        )
        return cursor.rowcount

    async def pushed_to_device(self, device_id: str) -> list[ProfileDraftRow]:
        """The drafts that stand behind this file: pushed, not replaced by a later push."""
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE d.status = 'pushed' AND d.pushed_device_profile_id = ? "
            "AND d.replaced_by_draft_id IS NULL ORDER BY d.id",
            (device_id,),
        )
        return self.to_models(ProfileDraftRow, rows)

    async def pusher_of_version(self, version_id: int) -> int | None:
        """The newest draft of this document that is on the machine and still stands there."""
        row = await self.db.fetch_one(
            "SELECT id FROM profile_drafts WHERE status = 'pushed' AND draft_version_id = ? "
            "AND replaced_by_draft_id IS NULL ORDER BY id DESC LIMIT 1",
            (version_id,),
        )
        return None if row is None else int(row["id"])

    async def retire_pushed(
        self, device_id: str, *, outcome: dict[str, Any]
    ) -> list[ProfileDraftRow]:
        """Close the drafts behind a file that is off the machine for good.

        What a rollback did: a draft that pushed a profile the machine no longer has is
        ``discarded`` (it stayed ``pushed`` once, which described a file that was gone) and
        stops naming the file, so nothing can later act on whatever inherits the id. Returns
        the drafts as they were just before, for what still needs their device ids.
        """
        found = await self.pushed_to_device(device_id)
        for draft in found:
            await self.set_status(draft.id, "discarded")
            await self.clear_pushed_profile(draft.id, outcome=outcome)
        return found

    async def discard_unsent(self, draft_ids: Iterable[int], *, now: str | None = None) -> int:
        """Discard these drafts, unless they have already been sent to the machine.

        For the callers that retire a draft because what it was proposed for
        went away — its proposal was declined or overtaken, its Set discarded —
        inside a transaction they already hold. Only `draft` and `approved`
        move: a `pushed` or `failed` draft names a profile the display holds,
        and saying `discarded` about it would be the archive lying about the
        machine (the same rule the discard route enforces with a 409). One that
        is already superseded or discarded is left with the status it has.
        Returns how many moved.
        """
        wanted = sorted({int(draft_id) for draft_id in draft_ids})
        if not wanted:
            return 0
        placeholders = ", ".join("?" * len(wanted))
        cursor = await self.db.execute(
            "UPDATE profile_drafts SET status = 'discarded', updated_at = ? "  # noqa: S608 - placeholders are generated, the ids are bound
            f"WHERE status IN ('draft', 'approved') AND id IN ({placeholders})",
            [now or utc_now(), *wanted],
        )
        return cursor.rowcount

    async def supersede(self, draft_id: int) -> None:
        """Mark a draft overtaken by a refinement of it.

        Not `discarded`: nobody turned it down. A refinement is the next attempt
        at the same idea, and the chain of attempts is what makes the notes on
        each of them worth reading.
        """
        await self.db.execute(
            "UPDATE profile_drafts SET status = 'superseded', updated_at = ? WHERE id = ?",
            (utc_now(), draft_id),
        )
