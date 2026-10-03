"""`set_insight_deletions` — an added insight the agent proposes to delete, waiting for the person.

An insight the person added reaches every later conversation of its Set, so one
that the shots no longer support is a standing mistake in the agent's prompt. The
agent may say so, with its reason, but **removing is the person's**: a row here is
words and changes nothing until somebody presses Delete. Each method keeps one
property true.

* **Only a person deletes.** :meth:`InsightDeletionsRepository.accept` is the
  Delete button's write and :meth:`~InsightDeletionsRepository.keep` the Keep
  button's; nothing a tool, a timer or a boot step reaches calls either.
  ``tests/tools/test_no_proposal_is_accepted_by_a_tool.py`` keeps it that way.

* **Removed means removed.** Accepting deletes the insight through the same
  :meth:`~gaggiclanker.db.repos.knowledge_insights.InsightsRepository.delete_in_transaction`
  the person's other Delete buttons use; the proposal keeps only the text the
  card showed, so the conversation that proposed it can say what went.

* **One waiting proposal per insight.** The partial unique index is the real
  guard (the stdio child is a second process); a newer proposal marks the waiting
  one `superseded` in the same transaction.

* **What may be named** is an *added* insight of this Set: never a waiting or
  dismissed one, a general one, or another Set's.

* **Every answer is one transaction.** The proposal is still waiting, the
  insight is still there and still added, and the write happens, in one go; a
  second tab pressing the same card finds it decided.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

import structlog
from pydantic import BaseModel, ConfigDict, StringConstraints

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.db.repository import Repository

log = structlog.get_logger(__name__)

__all__ = [
    "REASON_MAX",
    "REASON_MIN",
    "DeletionResult",
    "InsightDeletionRefusal",
    "InsightDeletionRow",
    "InsightDeletionStatus",
    "InsightDeletionWrite",
    "InsightDeletionsRepository",
]

#: `superseded` is the one nobody chooses: a newer proposal for the same insight
#: replaced it. `stale` is the insight going by another path first.
type InsightDeletionStatus = Literal["proposed", "deleted", "kept", "stale", "superseded"]

type InsightDeletionRefusal = Literal[
    "bad_insight",
    "general",
    "not_added",
    "bad_thread",
    "no_proposal",
    "not_waiting",
    "insight_changed",
]

REASON_MIN = 20
REASON_MAX = 500

_Reason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=REASON_MIN, max_length=REASON_MAX)
]


class InsightDeletionWrite(BaseModel):
    """What :meth:`InsightDeletionsRepository.propose` stores."""

    model_config = ConfigDict(extra="forbid")

    thread_id: int
    insight_id: int
    reason: _Reason


class InsightDeletionRow(BaseModel):
    """One proposed deletion, as a card reads it."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_id: int
    thread_id: int
    #: ``None`` once the insight is gone, whichever way it went.
    insight_id: int | None = None
    #: The insight's text as it was when proposed: all that is kept of it.
    insight_text: str
    reason: str
    status: InsightDeletionStatus = "proposed"
    created_at: str
    decided_at: str | None = None


@dataclass(frozen=True, slots=True)
class DeletionResult:
    """A guarded write: the proposal it produced, or why there is none."""

    proposal: InsightDeletionRow | None = None
    refused: InsightDeletionRefusal | None = None
    #: The insight id a refusal is about.
    subject: int | None = None
    #: The waiting proposal a new one replaced, when :meth:`propose` replaced one.
    replaced: InsightDeletionRow | None = None


_SELECT = "SELECT d.* FROM set_insight_deletions d"


class InsightDeletionsRepository(Repository):
    """Reads and writes the deletions an agent has proposed for a Set's insights."""

    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.insights = InsightsRepository(db)

    # ── reading ──────────────────────────────────────────────────────

    async def get(self, set_id: int, proposal_id: int) -> InsightDeletionRow | None:
        row = await self.db.fetch_one(
            f"{_SELECT} WHERE d.id = ? AND d.set_id = ?", (proposal_id, set_id)
        )
        return self.to_model(InsightDeletionRow, row)

    async def for_set(self, set_id: int, *, limit: int = 100) -> list[InsightDeletionRow]:
        """Every deletion proposed on this Set, newest first."""
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE d.set_id = ? ORDER BY d.id DESC LIMIT ?", (set_id, limit)
        )
        return self.to_models(InsightDeletionRow, rows)

    async def for_thread(self, thread_id: int) -> list[InsightDeletionRow]:
        """Every deletion this conversation proposed, however old, newest first.

        Uncapped on purpose: the chat's cards read their proposal from here, and a
        card must find its row however many proposals the Set has had since.
        """
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE d.thread_id = ? ORDER BY d.id DESC", (thread_id,)
        )
        return self.to_models(InsightDeletionRow, rows)

    async def waiting_for_set(self, set_id: int) -> list[InsightDeletionRow]:
        """The ones still waiting, for the Set page's line on an insight."""
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE d.set_id = ? AND d.status = 'proposed' ORDER BY d.id", (set_id,)
        )
        return self.to_models(InsightDeletionRow, rows)

    async def proposed_in(self, thread_id: int, *, limit: int = 10) -> list[InsightDeletionRow]:
        """What **this conversation** proposed deleting, newest last.

        `superseded` is left out: a newer card for the same insight stands in its
        place, and the conversation is told about that one.
        """
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE d.thread_id = ? AND d.status != 'superseded' "
            "ORDER BY d.id DESC LIMIT ?",
            (thread_id, limit),
        )
        return list(reversed(self.to_models(InsightDeletionRow, rows)))

    # ── writing ──────────────────────────────────────────────────────

    async def propose(self, set_id: int, spec: InsightDeletionWrite) -> DeletionResult:
        """Write down a deletion the person has not agreed to yet.

        The insight is read inside the transaction that writes, so one deleted a
        moment ago is refused, never stored as a card with nothing behind it. A
        proposal already waiting for the same insight is replaced: the agent
        revises its reason and the last card is the one that counts.
        """
        now = utc_now()
        async with self.db.transaction():
            thread = await self.db.fetch_value(
                "SELECT 1 FROM chat_threads WHERE id = ? AND set_id = ?",
                (spec.thread_id, set_id),
            )
            if thread is None:
                return DeletionResult(refused="bad_thread")
            insight = await self.db.fetch_one(
                "SELECT set_id, text, confirmed, dismissed FROM knowledge_insights WHERE id = ?",
                (spec.insight_id,),
            )
            if insight is None or (insight["set_id"] is not None and insight["set_id"] != set_id):
                return DeletionResult(refused="bad_insight", subject=spec.insight_id)
            if insight["set_id"] is None:
                return DeletionResult(refused="general", subject=spec.insight_id)
            if not insight["confirmed"] or insight["dismissed"]:
                return DeletionResult(refused="not_added", subject=spec.insight_id)
            waiting = await self._waiting_for_insight(spec.insight_id)
            if waiting is not None:
                await self._decide(waiting.id, "superseded", now)
            cursor = await self.db.execute(
                """
                INSERT INTO set_insight_deletions
                    (set_id, thread_id, insight_id, insight_text, reason, status, created_at)
                VALUES (:set_id, :thread_id, :insight_id, :text, :reason, 'proposed', :now)
                """,
                {
                    "set_id": set_id,
                    "thread_id": spec.thread_id,
                    "insight_id": spec.insight_id,
                    "text": insight["text"],
                    "reason": spec.reason,
                    "now": now,
                },
            )
            proposal_id = int(cursor.lastrowid or 0)
        return DeletionResult(proposal=await self.get(set_id, proposal_id), replaced=waiting)

    async def accept(self, set_id: int, proposal_id: int) -> DeletionResult:
        """Delete the insight. A person's press, and the only way a proposal removes anything."""
        now = utc_now()
        async with self.db.transaction():
            proposal = await self.get(set_id, proposal_id)
            if proposal is None:
                return DeletionResult(refused="no_proposal")
            if proposal.status != "proposed":
                return DeletionResult(refused="not_waiting", proposal=proposal)
            insight = (
                None
                if proposal.insight_id is None
                else await self.db.fetch_one(
                    "SELECT set_id, confirmed, dismissed FROM knowledge_insights WHERE id = ?",
                    (proposal.insight_id,),
                )
            )
            if insight is None:
                # Cannot happen while the delete marks its waiting proposals stale in
                # the same transaction; said as a refusal rather than assumed.
                await self._decide(proposal_id, "stale", now)
                return DeletionResult(
                    refused="not_waiting", proposal=await self.get(set_id, proposal_id)
                )
            if not insight["confirmed"] or insight["dismissed"]:
                # Taken back since: it is no longer an added insight, so there is
                # nothing the agent's reason applies to. Nothing is deleted.
                await self._decide(proposal_id, "stale", now)
                return DeletionResult(
                    refused="insight_changed", proposal=await self.get(set_id, proposal_id)
                )
            # Decide first: the delete marks every *waiting* proposal for the insight
            # stale, and this one must end `deleted`.
            await self._decide(proposal_id, "deleted", now)
            assert proposal.insight_id is not None  # checked with the row above
            await self.insights.delete_in_transaction(
                proposal.insight_id, deciding_proposal_id=proposal_id
            )
        log.info("insight_deletion_accepted", proposal_id=proposal_id, set_id=set_id)
        return DeletionResult(proposal=await self.get(set_id, proposal_id))

    async def keep(self, set_id: int, proposal_id: int) -> DeletionResult:
        """Leave the insight where it is. A person's press; nothing else is written."""
        now = utc_now()
        async with self.db.transaction():
            proposal = await self.get(set_id, proposal_id)
            if proposal is None:
                return DeletionResult(refused="no_proposal")
            if proposal.status != "proposed":
                return DeletionResult(refused="not_waiting", proposal=proposal)
            await self._decide(proposal_id, "kept", now)
        return DeletionResult(proposal=await self.get(set_id, proposal_id))

    async def _waiting_for_insight(self, insight_id: int) -> InsightDeletionRow | None:
        row = await self.db.fetch_one(
            f"{_SELECT} WHERE d.insight_id = ? AND d.status = 'proposed'", (insight_id,)
        )
        return self.to_model(InsightDeletionRow, row)

    async def _decide(self, proposal_id: int, status: InsightDeletionStatus, now: str) -> None:
        await self.db.execute(
            "UPDATE set_insight_deletions SET status = :status, decided_at = :now WHERE id = :id",
            {"status": status, "now": now, "id": proposal_id},
        )
