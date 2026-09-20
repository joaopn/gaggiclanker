"""`profile_drafts` — a proposed profile and how far it has got.

The document is deliberately *not* here. A draft's profile is a
`profile_versions` row with `source = 'draft'`, exactly like a mirrored or an
imported one, and :attr:`ProfileDraftRow.draft_version_id` points at it. Profile
versions are content-hashed and immutable and shots resolve to them; a draft that
becomes the profile a Set is brewed with has to be the same kind of thing as
every other profile in the archive, not a blob in a table every later join would
have to special-case.

What *is* here is the state machine and the evidence:

    draft ──approve──> approved ──push──> pushed
      │                   │                  └──(round trip disagreed)──> failed
      │                   └──refine──> superseded
      └──discard──> discarded

and, beside it, the three things a person has to see before they press the
button: what the policy clamped, which stop conditions moved, and — if the push
failed — both documents, ours and the machine's.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonList, JsonObject, dumps, utc_now
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
    #: The Set this was proposed for, the prediction it carries, and the version
    #: that prediction is against. All three are empty on a draft nobody
    #: predicted anything about, which is most of them.
    set_id: int | None = None
    #: Joined, so a card can say which experiment this belongs to without a
    #: second request. NULL when the Set has since been removed.
    set_name: str | None = None
    prediction: str = ""
    compares_to_version_id: int | None = None
    #: The compared-to version's number, joined in: a reader thinks in "v3".
    compares_to_version_no: int | None = None
    #: Whether the machine still holds the profile this was drafted from.
    #:
    #: Computed in SQL rather than stored, because it is a fact about *now*: a
    #: draft made this morning against a profile somebody edited at lunchtime is
    #: stale by the afternoon without anything about the draft changing. `True`
    #: for a base that was never on the machine — there is nothing it could have
    #: drifted from.
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
    created_at: str
    updated_at: str
    #: Joined from `profile_versions`, so a list row can say what it is about
    #: without a second request per draft.
    base_label: str | None = None
    draft_label: str | None = None


_SELECT = """
    SELECT d.*,
           base.label AS base_label,
           drafted.label AS draft_label,
           s.name AS set_name,
           cmp.version_no AS compares_to_version_no,
           CASE
               WHEN d.base_device_profile_id IS NULL THEN 1
               ELSE EXISTS (
                   SELECT 1 FROM device_profiles dp
                    WHERE dp.device_id = d.base_device_profile_id
                      AND dp.deleted_at IS NULL
                      AND dp.current_version_id = d.base_version_id
               )
           END AS base_is_current
    FROM profile_drafts d
    LEFT JOIN profile_versions base ON base.id = d.base_version_id
    LEFT JOIN profile_versions drafted ON drafted.id = d.draft_version_id
    LEFT JOIN sets s ON s.id = d.set_id
    LEFT JOIN set_versions cmp ON cmp.id = d.compares_to_version_id
"""


class ProfileDraftsRepository(Repository):
    """Reads and writes the draft queue."""

    async def create(self, write: ProfileDraftWrite) -> ProfileDraftRow:
        now = utc_now()
        cursor = await self.db.execute(
            """
            INSERT INTO profile_drafts
                (base_version_id, draft_version_id, source_analysis_id, source_suggestion_id,
                 parent_draft_id, base_device_profile_id, set_id, prediction,
                 compares_to_version_id, change_summary,
                 stop_condition_changes_json, clamp_changes_json, notes, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
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
                write.change_summary,
                dumps(write.stop_condition_changes),
                dumps(write.clamp_changes),
                write.notes,
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
        return self.to_model(ProfileDraftRow, row)

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
        return self.to_models(ProfileDraftRow, rows)

    async def set_status(
        self,
        draft_id: int,
        status: DraftStatus,
        *,
        acknowledged: bool | None = None,
        pushed_device_profile_id: str | None = None,
        verification: dict[str, Any] | None = None,
        error: str | None = None,
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
        await self.db.execute(
            f"UPDATE profile_drafts SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608 - assignments are literals, values are bound
            [*params, draft_id],
        )
        return await self.get(draft_id)

    async def clear_pushed_profile(self, draft_id: int) -> ProfileDraftRow | None:
        """Forget the device id after a rollback deleted the machine's copy.

        The draft stays `failed` — what happened, happened — but it must stop
        naming a profile that is no longer there, or the rollback button comes
        back and deletes whatever inherits that id next.
        """
        await self.db.execute(
            "UPDATE profile_drafts SET pushed_device_profile_id = NULL, updated_at = ? "
            "WHERE id = ?",
            (utc_now(), draft_id),
        )
        return await self.get(draft_id)

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
