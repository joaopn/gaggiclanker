"""`profile_signatures`, `signature_expectations` and the Set-version overrides.

A signature is what one profile version is for, as a list of expectations a person
has answered. This module is the only SQL for it, and each method keeps one of
these properties true.

* **Only a confirmed expectation is ever read as a check.** A proposed one is a
  row a person answers, a rejected one is kept with its reason so the
  conversation that proposed it can be told. :meth:`confirmed_for_versions` is
  the one reader of "what counts"; nothing else filters on the status.

* **Every answer is check-then-write in one transaction**, the check being the
  `WHERE status = 'proposed'` of the one `UPDATE` that writes it: two browser
  tabs pressing Confirm on the same card get one answer and one refusal, never
  two writes. A shipped unique index on (signature, position) is the guard for
  a second process (the stdio child), which the retry in :meth:`add` rides out.

* **Positions are never reused**, so an expectation keeps its place in the list
  for good and the index is the writers' guard.

* **An override is one live row per Set version** (a partial unique index) and
  changes nothing but a confirmed measure's `compare`. It is found by Set
  version, never by expectation alone, so it cannot reach another version's shots.

Nothing here is reachable from a tool except the two writes that *propose*
(:meth:`add`, :meth:`add_override`); answering is a route a person presses.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository, row_to_dict
from gaggiclanker.domain.metric_language import Compare, Expression

log = structlog.get_logger(__name__)

__all__ = [
    "EXPECTATION_KINDS",
    "EXPECTATION_STATUSES",
    "TIERS",
    "AnswerResult",
    "ExpectationKind",
    "ExpectationRow",
    "ExpectationStatus",
    "ExpectationWrite",
    "OverrideAnswer",
    "OverrideRow",
    "OverrideStatus",
    "OverrideWrite",
    "SignatureRepository",
    "Tier",
]

type Tier = Literal["critical", "important", "context"]
type ExpectationKind = Literal["measure", "reached", "expects_warning", "free_text"]
type ExpectationStatus = Literal["proposed", "confirmed", "rejected"]
#: An override can also be withdrawn: a person took back one they had confirmed.
type OverrideStatus = Literal["proposed", "confirmed", "rejected", "withdrawn"]

TIERS: tuple[str, ...] = ("critical", "important", "context")
EXPECTATION_KINDS: tuple[str, ...] = ("measure", "reached", "expects_warning", "free_text")
EXPECTATION_STATUSES: tuple[str, ...] = ("proposed", "confirmed", "rejected")

#: How many times a write that lost a race on (signature, position) is tried again.
_POSITION_RETRIES = 4


class ExpectationWrite(BaseModel):
    """What :meth:`SignatureRepository.add` stores for one expectation.

    Already validated by the domain (`domain/signature.py`): the repository stores
    what it is given and refuses nothing about meaning, only about shape.
    """

    model_config = ConfigDict(extra="forbid")

    tier: Tier
    phase: str | None = None
    kind: ExpectationKind
    expression: Expression | None = None
    warning_fault: str | None = None
    text: str = ""
    fault: str | None = None
    sentence: str = Field(min_length=1)
    reason: str = ""
    needs_phase: bool = False
    carried_from_id: int | None = None
    proposed_by_thread_id: int | None = None
    proposed_by_draft_id: int | None = None

    @model_validator(mode="after")
    def _shape(self) -> ExpectationWrite:
        if (self.kind == "measure") != (self.expression is not None):
            raise ValueError("exactly a measure carries an expression")
        if (self.kind == "expects_warning") != (self.warning_fault is not None):
            raise ValueError("exactly an expects_warning carries a warning")
        return self


class ExpectationRow(BaseModel):
    """One expectation as stored, with the profile version its signature belongs to."""

    model_config = ConfigDict(extra="forbid")

    id: int
    signature_id: int
    profile_version_id: int
    position: int
    tier: Tier
    phase: str | None
    kind: ExpectationKind
    #: ``None`` for every kind but a measure, and for a measure whose stored JSON can no longer
    #: be read (``readable`` says which).
    expression: Expression | None = None
    readable: bool = True
    warning_fault: str | None = None
    text: str = ""
    fault: str | None = None
    sentence: str
    reason: str = ""
    status: ExpectationStatus
    reject_reason: str = ""
    needs_phase: bool = False
    proposed_by_thread_id: int | None = None
    proposed_by_draft_id: int | None = None
    carried_from_id: int | None = None
    #: The profile version that expectation was carried from, when it was.
    carried_from_version_id: int | None = None
    proposed_at: str
    answered_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _decode(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "expression_json" not in data:
            return data
        payload = dict(data)
        raw = payload.pop("expression_json")
        payload["needs_phase"] = bool(payload.get("needs_phase"))
        if raw is None:
            return payload
        try:
            payload["expression"] = Expression.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            # Damaged data is "unreadable", never a 500 on a page (the house rule for stored JSON).
            payload["expression"] = None
            payload["readable"] = False
            log.warning("signature_expression_unreadable", expectation_id=payload.get("id"))
        return payload


class OverrideWrite(BaseModel):
    """What :meth:`SignatureRepository.add_override` stores."""

    model_config = ConfigDict(extra="forbid")

    set_version_id: int
    expectation_id: int
    compare: Compare
    reason: str = ""
    proposed_by_thread_id: int | None = None


class OverrideRow(BaseModel):
    """A Set version's override of one expectation's compare values."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_version_id: int
    expectation_id: int
    compare: Compare | None = None
    readable: bool = True
    reason: str = ""
    status: OverrideStatus
    reject_reason: str = ""
    proposed_by_thread_id: int | None = None
    proposed_at: str
    answered_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _decode(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "compare_json" not in data:
            return data
        payload = dict(data)
        raw = payload.pop("compare_json")
        try:
            payload["compare"] = Compare.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            payload["compare"] = None
            payload["readable"] = False
            log.warning("signature_override_unreadable", override_id=payload.get("id"))
        return payload


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """A guarded answer: the row it produced, or why there is none."""

    row: ExpectationRow | None = None
    #: ``no_expectation`` (it is not there), ``not_waiting`` (already answered), or
    #: ``needs_phase`` (a carried one whose phase is gone cannot be confirmed).
    refused: Literal["no_expectation", "not_waiting", "needs_phase"] | None = None


@dataclass(frozen=True, slots=True)
class OverrideAnswer:
    row: OverrideRow | None = None
    refused: (
        Literal["no_override", "not_waiting", "expectation_not_confirmed", "not_confirmed"] | None
    ) = None


_EXPECTATION_SELECT = """
    SELECT e.id, e.signature_id, s.profile_version_id, e.position, e.tier, e.phase, e.kind,
           e.expression_json, e.warning_fault, e.text, e.fault, e.sentence, e.reason, e.status,
           e.reject_reason, e.needs_phase, e.proposed_by_thread_id, e.proposed_by_draft_id,
           e.carried_from_id, e.proposed_at, e.answered_at,
           (SELECT s2.profile_version_id FROM signature_expectations e2
              JOIN profile_signatures s2 ON s2.id = e2.signature_id
             WHERE e2.id = e.carried_from_id) AS carried_from_version_id
    FROM signature_expectations e
    JOIN profile_signatures s ON s.id = e.signature_id
"""

_TIER_ORDER = "CASE e.tier WHEN 'critical' THEN 0 WHEN 'important' THEN 1 ELSE 2 END"


def _expression_json(expression: Expression | None) -> str | None:
    if expression is None:
        return None
    return expression.model_dump_json(by_alias=True, exclude_none=True)


class SignatureRepository(Repository):
    """Reads and writes signatures."""

    def _transaction(self) -> AbstractAsyncContextManager[Any]:
        """The write's own transaction, or none when the caller already holds one."""
        return nullcontext() if self.db.in_transaction else self.db.transaction()

    # ── reading ──────────────────────────────────────────────────────

    async def get(self, expectation_id: int) -> ExpectationRow | None:
        row = await self.db.fetch_one(f"{_EXPECTATION_SELECT} WHERE e.id = ?", (expectation_id,))
        return self.to_model(ExpectationRow, row)

    async def for_version(
        self, profile_version_id: int, *, statuses: Iterable[str] | None = None
    ) -> list[ExpectationRow]:
        """Every expectation of one profile version, tier first, then in written order."""
        where: list[str] = ["s.profile_version_id = ?"]
        params: list[Any] = [profile_version_id]
        if statuses is not None:
            wanted = list(statuses)
            where.append(f"e.status IN ({', '.join('?' * len(wanted))})")
            params.extend(wanted)
        rows = await self.db.fetch_all(
            f"{_EXPECTATION_SELECT} WHERE {' AND '.join(where)} ORDER BY {_TIER_ORDER}, e.position",
            params,
        )
        return self.to_models(ExpectationRow, rows)

    async def confirmed_for_versions(
        self, profile_version_ids: Iterable[int]
    ) -> dict[int, list[ExpectationRow]]:
        """The confirmed expectations of each profile version: **the only ones that count**."""
        ids = sorted(set(profile_version_ids))
        if not ids:
            return {}
        rows = await self.db.fetch_all(
            f"{_EXPECTATION_SELECT} WHERE e.status = 'confirmed' "
            f"AND s.profile_version_id IN ({', '.join('?' * len(ids))}) "
            "ORDER BY s.profile_version_id, e.position",
            ids,
        )
        found: dict[int, list[ExpectationRow]] = {}
        for row in self.to_models(ExpectationRow, rows):
            found.setdefault(row.profile_version_id, []).append(row)
        return found

    async def counts(self, profile_version_ids: Iterable[int]) -> dict[int, dict[str, int]]:
        """How many expectations of each status each profile version has."""
        ids = sorted(set(profile_version_ids))
        if not ids:
            return {}
        rows = await self.db.fetch_all(
            "SELECT s.profile_version_id AS version_id, e.status AS status, COUNT(*) AS n "  # noqa: S608
            "FROM signature_expectations e JOIN profile_signatures s ON s.id = e.signature_id "
            f"WHERE s.profile_version_id IN ({', '.join('?' * len(ids))}) "
            "GROUP BY s.profile_version_id, e.status",
            ids,
        )
        found: dict[int, dict[str, int]] = {}
        for row in rows:
            found.setdefault(int(row["version_id"]), {})[str(row["status"])] = int(row["n"])
        return found

    async def proposed_in_thread(self, thread_id: int) -> list[ExpectationRow]:
        """What one conversation proposed (any status), oldest first: the only reader of a
        proposal that is not a person's screen, and it is keyed by the thread that wrote it."""
        rows = await self.db.fetch_all(
            f"{_EXPECTATION_SELECT} WHERE e.proposed_by_thread_id = ? ORDER BY e.id",
            (thread_id,),
        )
        return self.to_models(ExpectationRow, rows)

    # ── proposing ────────────────────────────────────────────────────

    async def add(
        self, profile_version_id: int, writes: Sequence[ExpectationWrite]
    ) -> list[ExpectationRow]:
        """Append expectations (as proposed) to a profile version's signature, in one transaction.

        Creates the signature on first use. The positions are the next free ones; a writer
        that lost a race on the unique index (a second process) reads again and retries.
        """
        if not writes:
            return []
        last: sqlite3.IntegrityError | None = None
        for _ in range(_POSITION_RETRIES):
            try:
                async with self._transaction():
                    return await self._append(profile_version_id, writes)
            except sqlite3.IntegrityError as exc:
                if "position" not in str(exc) and "profile_signatures" not in str(exc):
                    raise
                last = exc
        assert last is not None
        raise last

    async def _append(
        self, profile_version_id: int, writes: Sequence[ExpectationWrite]
    ) -> list[ExpectationRow]:
        signature_id = await self.db.fetch_value(
            "SELECT id FROM profile_signatures WHERE profile_version_id = ?",
            (profile_version_id,),
        )
        if signature_id is None:
            cursor = await self.db.execute(
                "INSERT INTO profile_signatures (profile_version_id) VALUES (?)",
                (profile_version_id,),
            )
            signature_id = cursor.lastrowid
        position = int(
            await self.db.fetch_value(
                "SELECT COALESCE(MAX(position), -1) FROM signature_expectations "
                "WHERE signature_id = ?",
                (signature_id,),
            )
        )
        now = utc_now()
        ids: list[int] = []
        for write in writes:
            position += 1
            cursor = await self.db.execute(
                """
                INSERT INTO signature_expectations
                    (signature_id, position, tier, phase, kind, expression_json, warning_fault,
                     text, fault, sentence, reason, needs_phase, proposed_by_thread_id,
                     proposed_by_draft_id, carried_from_id, proposed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signature_id,
                    position,
                    write.tier,
                    write.phase,
                    write.kind,
                    _expression_json(write.expression),
                    write.warning_fault,
                    write.text,
                    write.fault,
                    write.sentence,
                    write.reason,
                    int(write.needs_phase),
                    write.proposed_by_thread_id,
                    write.proposed_by_draft_id,
                    write.carried_from_id,
                    now,
                ),
            )
            ids.append(int(cursor.lastrowid or 0))
        rows = [await self.get(expectation_id) for expectation_id in ids]
        return [row for row in rows if row is not None]

    async def carried_ids(self, profile_version_id: int) -> set[int]:
        """The expectations this version's signature already carries from another's."""
        rows = await self.db.fetch_all(
            "SELECT e.carried_from_id AS source FROM signature_expectations e "
            "JOIN profile_signatures s ON s.id = e.signature_id "
            "WHERE s.profile_version_id = ? AND e.carried_from_id IS NOT NULL",
            (profile_version_id,),
        )
        return {int(row["source"]) for row in rows}

    # ── answering (a person's press) ─────────────────────────────────

    async def answer(
        self, expectation_id: int, *, confirm: bool, reject_reason: str = ""
    ) -> AnswerResult:
        """Confirm or reject one proposed expectation. Check and write in one statement.

        The `WHERE status = 'proposed'` is the guard: whoever answers first wins, and the
        second finds it answered.
        """
        async with self._transaction():
            row = await self.get(expectation_id)
            if row is None:
                return AnswerResult(refused="no_expectation")
            if row.status != "proposed":
                return AnswerResult(refused="not_waiting")
            if confirm and row.needs_phase:
                return AnswerResult(refused="needs_phase")
            cursor = await self.db.execute(
                "UPDATE signature_expectations SET status = ?, reject_reason = ?, answered_at = ? "
                "WHERE id = ? AND status = 'proposed'",
                (
                    "confirmed" if confirm else "rejected",
                    "" if confirm else reject_reason.strip(),
                    utc_now(),
                    expectation_id,
                ),
            )
            if cursor.rowcount != 1:
                return AnswerResult(refused="not_waiting")
            return AnswerResult(row=await self.get(expectation_id))

    async def confirm_all(self, profile_version_id: int) -> list[ExpectationRow]:
        """Confirm every proposed expectation of one profile version that can be confirmed.

        One statement, so it is all or nothing; the ones that *need a phase* stay proposed.
        """
        async with self._transaction():
            before = await self.for_version(profile_version_id, statuses=["proposed"])
            wanted = [row.id for row in before if not row.needs_phase]
            if not wanted:
                return []
            await self.db.execute(
                "UPDATE signature_expectations SET status = 'confirmed', answered_at = ? "  # noqa: S608
                f"WHERE status = 'proposed' AND needs_phase = 0 "
                f"AND id IN ({', '.join('?' * len(wanted))})",
                (utc_now(), *wanted),
            )
            rows = [await self.get(expectation_id) for expectation_id in wanted]
            return [row for row in rows if row is not None and row.status == "confirmed"]

    async def set_tier(self, expectation_id: int, tier: Tier) -> AnswerResult:
        """Move one proposed expectation to another tier. Only while it is waiting."""
        async with self._transaction():
            row = await self.get(expectation_id)
            if row is None:
                return AnswerResult(refused="no_expectation")
            cursor = await self.db.execute(
                "UPDATE signature_expectations SET tier = ? WHERE id = ? AND status = 'proposed'",
                (tier, expectation_id),
            )
            if cursor.rowcount != 1:
                return AnswerResult(refused="not_waiting")
            return AnswerResult(row=await self.get(expectation_id))

    # ── overrides ────────────────────────────────────────────────────

    async def get_override(self, override_id: int) -> OverrideRow | None:
        row = await self.db.fetch_one(
            "SELECT * FROM set_version_signature_overrides WHERE id = ?", (override_id,)
        )
        return self.to_model(OverrideRow, row)

    async def overrides_for_set_version(
        self, set_version_id: int, *, statuses: Iterable[str] | None = None
    ) -> list[OverrideRow]:
        where: list[str] = ["set_version_id = ?"]
        params: list[Any] = [set_version_id]
        if statuses is not None:
            wanted = list(statuses)
            where.append(f"status IN ({', '.join('?' * len(wanted))})")
            params.extend(wanted)
        rows = await self.db.fetch_all(
            f"SELECT * FROM set_version_signature_overrides WHERE {' AND '.join(where)} "  # noqa: S608
            "ORDER BY id",
            params,
        )
        return self.to_models(OverrideRow, rows)

    async def confirmed_overrides(self, set_version_ids: Iterable[int]) -> dict[int, OverrideRow]:
        """The confirmed override of each Set version, by Set version: the only way one is found."""
        ids = sorted(set(set_version_ids))
        if not ids:
            return {}
        rows = await self.db.fetch_all(
            "SELECT * FROM set_version_signature_overrides "  # noqa: S608
            f"WHERE status = 'confirmed' AND set_version_id IN ({', '.join('?' * len(ids))})",
            ids,
        )
        return {row.set_version_id: row for row in self.to_models(OverrideRow, rows)}

    async def overrides_proposed_in_thread(self, thread_id: int) -> list[OverrideRow]:
        rows = await self.db.fetch_all(
            "SELECT * FROM set_version_signature_overrides "
            "WHERE proposed_by_thread_id = ? ORDER BY id",
            (thread_id,),
        )
        return self.to_models(OverrideRow, rows)

    async def add_override(self, write: OverrideWrite) -> OverrideRow | None:
        """Propose an override, replacing the Set version's waiting one (a newer proposal wins).

        ``None`` when the version already has a **confirmed** one: that one is the person's
        answer and a proposal does not push it aside. The check and the write share one
        transaction behind the partial unique index.
        """
        async with self._transaction():
            live = await self.overrides_for_set_version(
                write.set_version_id, statuses=["proposed", "confirmed"]
            )
            if any(item.status == "confirmed" for item in live):
                return None
            for item in live:
                await self.db.execute(
                    "UPDATE set_version_signature_overrides SET status = 'rejected', "
                    "reject_reason = 'replaced by a newer proposal', answered_at = ? "
                    "WHERE id = ? AND status = 'proposed'",
                    (utc_now(), item.id),
                )
            cursor = await self.db.execute(
                """
                INSERT INTO set_version_signature_overrides
                    (set_version_id, expectation_id, compare_json, reason, proposed_by_thread_id,
                     proposed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    write.set_version_id,
                    write.expectation_id,
                    write.compare.model_dump_json(exclude_none=True),
                    write.reason,
                    write.proposed_by_thread_id,
                    utc_now(),
                ),
            )
            return await self.get_override(int(cursor.lastrowid or 0))

    async def answer_override(
        self, override_id: int, *, confirm: bool, reject_reason: str = ""
    ) -> OverrideAnswer:
        """Confirm or reject a proposed override: the first answer wins."""
        async with self._transaction():
            row = await self.get_override(override_id)
            if row is None:
                return OverrideAnswer(refused="no_override")
            if row.status != "proposed":
                return OverrideAnswer(refused="not_waiting")
            if confirm:
                target = await self.get(row.expectation_id)
                if target is None or target.status != "confirmed":
                    return OverrideAnswer(refused="expectation_not_confirmed")
            cursor = await self.db.execute(
                "UPDATE set_version_signature_overrides SET status = ?, reject_reason = ?, "
                "answered_at = ? WHERE id = ? AND status = 'proposed'",
                (
                    "confirmed" if confirm else "rejected",
                    "" if confirm else reject_reason.strip(),
                    utc_now(),
                    override_id,
                ),
            )
            if cursor.rowcount != 1:
                return OverrideAnswer(refused="not_waiting")
            return OverrideAnswer(row=await self.get_override(override_id))

    # ── raw material for evaluation ──────────────────────────────────

    async def shot_sources(self, shot_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        """The bytes and the profile document of each shot, for a signature check.

        Only the shots a confirmed signature applies to are asked for, so the cost of reading
        raw logs is paid by the shots that need them.
        """
        ids = list(shot_ids)
        if not ids:
            return {}
        rows = await self.db.fetch_all(
            "SELECT s.id, s.device_id, s.raw_slog, "  # noqa: S608 - placeholders only
            "CASE WHEN json_valid(v.json) THEN v.json END AS profile_json "
            "FROM shots s LEFT JOIN profile_versions v ON v.id = s.profile_version_id "
            f"WHERE s.id IN ({', '.join('?' * len(ids))})",
            ids,
        )
        return {int(row["id"]): row_to_dict(row) for row in rows}
