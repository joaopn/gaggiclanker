"""`signature_change_requests` — a change to a profile's signature the agent asks for, waiting
for the person.

An expectation in force is what every shot on a profile is checked against, so taking one out,
changing it, moving its tier or putting back one the person rejected is the person's. The agent
may ask, with a reason for each change; a row here is words and changes nothing until somebody
presses Approve on its card. This module is the only SQL for the table, and each method keeps
one property true.

* **Only the service's person-facing methods decide.** :meth:`SignatureRequestsRepository.decide`
  is the one write that moves a request out of ``waiting``, guarded by the status in the
  ``WHERE`` of its ``UPDATE``: two tabs pressing the same card get one answer and one refusal.
  What an approval writes to the signature goes through ``SignatureRepository``'s existing
  verbs, never from here.

* **Stale is a status, not a side effect left to chance.** The methods named ``stale_…`` mark a
  waiting request ``stale`` with the reason a card shows; the callers run them in the
  transaction of the cause (a rejection, a restore, a tier move, a draft declined).

* **A draft's request is its draft's.** It is found by ``draft_id`` and answered with the draft,
  so the stale hooks that watch rows a person answers on the Profiles page skip it: the
  approval of the draft checks it again.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from contextlib import nullcontext
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.version_names import label_sql
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.signature_changes import PutBackOverrideChange, StoredChanges

log = structlog.get_logger(__name__)

__all__ = [
    "RequestStatus",
    "SignatureRequestRow",
    "SignatureRequestWrite",
    "SignatureRequestsRepository",
]

type RequestStatus = Literal["waiting", "approved", "declined", "stale"]


class SignatureRequestWrite(BaseModel):
    """What :meth:`SignatureRequestsRepository.create` stores."""

    model_config = ConfigDict(extra="forbid")

    profile_version_id: int
    thread_id: int | None = None
    draft_id: int | None = None
    reason: str = Field(min_length=1, max_length=500)
    changes: StoredChanges


class SignatureRequestRow(BaseModel):
    """One request as stored."""

    model_config = ConfigDict(extra="forbid")

    id: int
    profile_version_id: int
    thread_id: int | None = None
    draft_id: int | None = None
    reason: str
    #: ``None`` when the stored JSON can no longer be read (``readable`` says so).
    changes: StoredChanges | None = None
    readable: bool = True
    status: RequestStatus
    stale_reason: str = ""
    answer_reason: str = ""
    #: The expectation ids an approval wrote, one per replacement, in order.
    written: list[int] = Field(default_factory=list)
    created_at: str
    answered_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _decode(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "changes_json" not in data:
            return data
        payload = dict(data)
        raw = payload.pop("changes_json")
        try:
            payload["changes"] = StoredChanges.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            # Damaged data is "unreadable", never a 500 on a page (the house rule for stored JSON).
            payload["changes"] = None
            payload["readable"] = False
            log.warning("signature_request_unreadable", request_id=payload.get("id"))
        try:
            written = json.loads(payload.pop("written_json", "[]"))
            payload["written"] = [int(i) for i in written]
        except (ValueError, TypeError):
            payload["written"] = []
        return payload


_SELECT = "SELECT r.* FROM signature_change_requests r"


class SignatureRequestsRepository(Repository):
    """Reads and writes the changes an agent has asked for."""

    # ── reading ──────────────────────────────────────────────────────

    async def get(self, request_id: int) -> SignatureRequestRow | None:
        row = await self.db.fetch_one(f"{_SELECT} WHERE r.id = ?", (request_id,))
        return self.to_model(SignatureRequestRow, row)

    async def for_thread(self, thread_id: int) -> list[SignatureRequestRow]:
        """Every request this conversation made, oldest first, however old: the chat's cards read
        their request from here, so one is found whatever has been asked since."""
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE r.thread_id = ? ORDER BY r.id", (thread_id,)
        )
        return self.to_models(SignatureRequestRow, rows)

    async def for_version(self, profile_version_id: int) -> list[SignatureRequestRow]:
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE r.profile_version_id = ? ORDER BY r.id DESC", (profile_version_id,)
        )
        return self.to_models(SignatureRequestRow, rows)

    async def for_draft(self, draft_id: int) -> SignatureRequestRow | None:
        row = await self.db.fetch_one(
            f"{_SELECT} WHERE r.draft_id = ? ORDER BY r.id DESC LIMIT 1", (draft_id,)
        )
        return self.to_model(SignatureRequestRow, row)

    async def for_drafts(self, draft_ids: Iterable[int]) -> dict[int, SignatureRequestRow]:
        wanted = sorted({int(i) for i in draft_ids})
        if not wanted:
            return {}
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE r.draft_id IN ({', '.join('?' * len(wanted))}) ORDER BY r.id",
            wanted,
        )
        return {
            int(r.draft_id): r
            for r in self.to_models(SignatureRequestRow, rows)
            if r.draft_id is not None
        }

    async def proposed_in(self, thread_id: int, *, limit: int = 20) -> list[SignatureRequestRow]:
        """What **this conversation** asked for, newest last: what the agent is told of each."""
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE r.thread_id = ? ORDER BY r.id DESC LIMIT ?", (thread_id, limit)
        )
        return list(reversed(self.to_models(SignatureRequestRow, rows)))

    async def waiting_without_drafts(
        self, *, profile_version_id: int | None = None
    ) -> list[SignatureRequestRow]:
        where = "r.status = 'waiting' AND r.draft_id IS NULL"
        params: list[Any] = []
        if profile_version_id is not None:
            where += " AND r.profile_version_id = ?"
            params.append(profile_version_id)
        rows = await self.db.fetch_all(f"{_SELECT} WHERE {where} ORDER BY r.id", params)
        return self.to_models(SignatureRequestRow, rows)

    # ── writing ──────────────────────────────────────────────────────

    async def create(self, write: SignatureRequestWrite) -> SignatureRequestRow:
        """Store a request, waiting. A newer request that names a row an older waiting one names
        makes the older one stale, in the same transaction."""
        async with nullcontext() if self.db.in_transaction else self.db.transaction():
            cursor = await self.db.execute(
                "INSERT INTO signature_change_requests "
                "(profile_version_id, thread_id, draft_id, reason, changes_json, status, "
                " created_at) VALUES (?, ?, ?, ?, ?, 'waiting', ?)",
                (
                    write.profile_version_id,
                    write.thread_id,
                    write.draft_id,
                    write.reason,
                    write.changes.model_dump_json(),
                    utc_now(),
                ),
            )
            request_id = int(cursor.lastrowid or 0)
            if write.draft_id is None:
                await self.stale_naming(
                    write.changes.expectation_ids(),
                    write.changes.override_ids(),
                    "A newer request replaced it.",
                    except_id=request_id,
                )
        row = await self.get(request_id)
        assert row is not None
        return row

    async def decide(
        self,
        request_id: int,
        status: Literal["approved", "declined"],
        *,
        answer_reason: str = "",
        written: Sequence[int] = (),
    ) -> bool:
        """Move a waiting request to the person's answer. ``False`` when it was not waiting."""
        cursor = await self.db.execute(
            "UPDATE signature_change_requests SET status = ?, answer_reason = ?, written_json = ?, "
            "answered_at = ? WHERE id = ? AND status = 'waiting'",
            (status, answer_reason.strip(), json.dumps(list(written)), utc_now(), request_id),
        )
        return cursor.rowcount == 1

    async def set_written(self, request_id: int, written: Sequence[int]) -> None:
        await self.db.execute(
            "UPDATE signature_change_requests SET written_json = ? WHERE id = ?",
            (json.dumps(list(written)), request_id),
        )

    async def mark_stale(self, request_id: int, reason: str) -> bool:
        """Take a waiting request out of play, saying why. ``False`` when it was not waiting."""
        cursor = await self.db.execute(
            "UPDATE signature_change_requests SET status = 'stale', stale_reason = ?, "
            "answered_at = ? WHERE id = ? AND status = 'waiting'",
            (reason, utc_now(), request_id),
        )
        return cursor.rowcount == 1

    async def stale_naming(
        self,
        expectation_ids: Iterable[int],
        override_ids: Iterable[int],
        reason: str,
        *,
        except_id: int | None = None,
    ) -> int:
        """Stale every waiting request (not a draft's) that names one of these rows."""
        expectations, overrides = set(expectation_ids), set(override_ids)
        if not expectations and not overrides:
            return 0
        staled = 0
        for request in await self.waiting_without_drafts():
            if request.id == except_id or request.changes is None:
                continue
            if expectations & set(request.changes.expectation_ids()) or overrides & set(
                request.changes.override_ids()
            ):
                staled += int(await self.mark_stale(request.id, reason))
        return staled

    async def stale_putting_back_on(
        self, recipe_version_id: int, reason: str, *, except_override_id: int | None = None
    ) -> int:
        """Stale every waiting request (not a draft's) that asks for a rejected override to come
        back on this Recipe version: a version has one override in force at a time, and one just
        came into force there, by any path."""
        staled = 0
        for request in await self.waiting_without_drafts():
            if request.changes is None:
                continue
            for change in request.changes.changes:
                if (
                    isinstance(change, PutBackOverrideChange)
                    and change.recipe_version_id == recipe_version_id
                    and change.override_id != except_override_id
                ):
                    staled += int(await self.mark_stale(request.id, reason))
                    break
        return staled

    async def stale_where(
        self,
        profile_version_id: int,
        names_a_row: Callable[[SignatureRequestRow], str | None],
    ) -> int:
        """Stale the waiting requests (not a draft's) of one profile version for which
        ``names_a_row`` answers with a reason."""
        staled = 0
        for request in await self.waiting_without_drafts(profile_version_id=profile_version_id):
            reason = names_a_row(request)
            if reason is not None:
                staled += int(await self.mark_stale(request.id, reason))
        return staled

    async def stale_for_drafts(self, draft_ids: Iterable[int], reason: str) -> int:
        """A draft was declined or replaced: the requests that came with it go with it."""
        wanted = sorted({int(i) for i in draft_ids})
        if not wanted:
            return 0
        cursor = await self.db.execute(
            "UPDATE signature_change_requests SET status = 'stale', stale_reason = ?, "  # noqa: S608
            "answered_at = ? WHERE status = 'waiting' "
            f"AND draft_id IN ({', '.join('?' * len(wanted))})",
            [reason, utc_now(), *wanted],
        )
        return cursor.rowcount

    # ── what a request is about ──────────────────────────────────────

    async def shots_judged_by(self, profile_version_id: int) -> list[int]:
        """The shots whose checks are read from this profile version's signature.

        Shots brewed on the version itself and on a pin's copy of it (a copy is judged by the
        original), the ones that parsed and that the person has not discarded.
        """
        rows = await self.db.fetch_all(
            """
            SELECT s.id FROM shots s
            LEFT JOIN shot_judgements j ON j.shot_id = s.id
            WHERE s.quarantined = 0 AND COALESCE(j.discarded, 0) = 0
              AND (s.profile_version_id = :v OR s.profile_version_id IN (
                    SELECT p.profile_version_id FROM recipe_version_pins p
                      JOIN recipe_versions rv ON rv.id = p.recipe_version_id
                     WHERE rv.profile_version_id = :v))
            ORDER BY s.id
            """,
            {"v": profile_version_id},
        )
        return [int(r["id"]) for r in rows]

    async def open_prediction_label(self, profile_version_id: int) -> str | None:
        """The name of a Recipe version that brews this profile version (or its pin's copy), has
        a prediction and has no recorded outcome, with its Recipe's name ("Alturas v2"): the
        newest one, or ``None``."""
        row = await self.db.fetch_one(
            f"""
            SELECT r.name || ' ' || {label_sql("sv")} AS label FROM recipe_versions sv
            JOIN recipes r ON r.id = sv.recipe_id
            WHERE sv.prediction != '' AND sv.outcome IS NULL
              AND (sv.profile_version_id = :v OR sv.profile_version_id IN (
                    SELECT p.profile_version_id FROM recipe_version_pins p
                      JOIN recipe_versions rv ON rv.id = p.recipe_version_id
                     WHERE rv.profile_version_id = :v))
            ORDER BY sv.id DESC LIMIT 1
            """,  # noqa: S608 - the one interpolation is the version label expression, a constant
            {"v": profile_version_id},
        )
        return None if row is None else str(row["label"])

    async def version_label(self, profile_version_id: int) -> str | None:
        """How the Profiles page names this version (``"v3"``), counted from the profile's first
        version as its dropdown lists them (newest first, so the last is v1). ``None`` for a
        version no profile lists."""
        row = await self.db.fetch_one(
            """
            SELECT (SELECT COUNT(*) FROM profile_board_versions o
                     WHERE o.board_id = me.board_id
                       AND (o.added_at < me.added_at
                            OR (o.added_at = me.added_at AND o.version_id <= me.version_id)))
                   AS position
            FROM profile_board_versions me
            JOIN profile_versions pv ON pv.id = me.version_id AND pv.board_id = me.board_id
            WHERE me.version_id = ?
            """,
            (profile_version_id,),
        )
        return None if row is None else f"v{int(row['position'])}"
