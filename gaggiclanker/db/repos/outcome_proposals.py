"""`set_outcome_proposals` — a version's grade, written by an agent, waiting for the person.

The loop this archive is built around ends every experiment with a grade, and
the grade is what the track record, every later conversation and the "no new
change while the last prediction is ungraded" rule read. The agent has always
been the one who works the grade out; until this table the person then had to
leave the conversation and type it into the Set page. A row here is the grade
as the agent wrote it, **changing nothing**, until somebody answers it. Every
method keeps one of these properties true.

* **An outcome is per version.** A proposal names the one version it grades and
  is graded against all of that version's counted shots, never one shot.

* **Nothing an agent graded is read as an outcome until a person accepted it.**
  The version's own outcome columns are written by :meth:`accept` and
  :meth:`change` (a person's press) and by the Set page, and by nothing else.
  A proposal that is waiting, dismissed or superseded is a row in this table and
  nowhere else: no ledger, track record or other version's context reads it.
  The one reader that says "waiting" is that version's own conversation.

* **One waiting grade per version.** A partial unique index says so (the stdio
  child is a second process); :meth:`create` supersedes the waiting row in the
  same transaction, so the latest grade is the one on the card.

* **Every answer is one transaction.** The proposal is still waiting, the grade
  is recordable under the same guards the Set page's route applies, and the
  proposal is marked, in one go; a second browser tab answering the same card
  finds it decided and gets a refusal, not a second write.

**Nothing here is reachable from a tool.** Accept, change and dismiss are routes
a person presses. ``tests/tools/test_no_proposal_is_accepted_by_a_tool.py``
walks the registry and the tool package's bytecode to keep it that way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

import structlog
from pydantic import BaseModel, ConfigDict, StringConstraints

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.sets import (
    TEXT_MAX,
    SetsRepository,
    SetVersionRow,
    VersionOutcomeWrite,
)
from gaggiclanker.db.repos.version_names import label_sql
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.vocab import VersionOutcome

log = structlog.get_logger(__name__)

__all__ = [
    "OUTCOME_PROPOSAL_STATUSES",
    "OutcomeProposalRefusal",
    "OutcomeProposalRow",
    "OutcomeProposalStatus",
    "OutcomeProposalWrite",
    "OutcomeProposalsRepository",
    "OutcomeResult",
]

#: `changed` is an answer too: the person recorded a different outcome from the
#: card's. `superseded` is the one nobody chooses: a newer grade of the same
#: version replaced it.
type OutcomeProposalStatus = Literal["proposed", "accepted", "changed", "dismissed", "superseded"]

OUTCOME_PROPOSAL_STATUSES: tuple[str, ...] = (
    "proposed",
    "accepted",
    "changed",
    "dismissed",
    "superseded",
)

#: Why a guarded write was refused. A slug, like the version writes next door:
#: the repository has no opinion about HTTP statuses, and the tool and the route
#: that do each say it in their own words.
type OutcomeProposalRefusal = Literal[
    "no_version",
    "no_prediction",
    "nothing_to_grade",
    "bad_thread",
    "no_proposal",
    "not_waiting",
]

_Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TEXT_MAX)]


class OutcomeProposalWrite(BaseModel):
    """What :meth:`OutcomeProposalsRepository.create` stores."""

    model_config = ConfigDict(extra="forbid")

    #: The conversation it was argued in. Nullable because a tool call arriving
    #: over the stdio server may have no thread to name; when one is named it
    #: has to be a conversation of this Set.
    thread_id: int | None = None
    outcome: VersionOutcome
    #: The per-claim lines, required: a grade nobody can read the reasons for is
    #: a grade nobody can accept with their eyes open. How long it has to be is
    #: the tool's rule, because the tool is where a model reads the refusal.
    note: _Note


class OutcomeProposalRow(BaseModel):
    """One proposed grade, with what a card needs joined in."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_id: int
    set_version_id: int
    #: The graded version's name, "v3", joined in.
    version_label: str | None = None
    thread_id: int | None = None
    outcome: VersionOutcome
    note: str
    #: How many graded (Keep or Improve) shots the version had when this was written.
    counted_shots: int = 0
    #: How many it has now, so the card can say "2 more since".
    counted_shots_now: int = 0
    status: OutcomeProposalStatus = "proposed"
    #: What the person recorded when they chose another outcome (`changed`).
    recorded_outcome: VersionOutcome | None = None
    decision_note: str = ""
    #: What the version's own outcome is **now**, whoever recorded it. Shown
    #: beside a waiting grade so a card that disagrees with the Set page says so.
    version_outcome: VersionOutcome | None = None
    version_outcome_note: str = ""
    created_at: str
    decided_at: str | None = None


@dataclass(frozen=True, slots=True)
class OutcomeResult:
    """A guarded write: the proposal it produced, or why there is none."""

    proposal: OutcomeProposalRow | None = None
    refused: OutcomeProposalRefusal | None = None
    #: The version carrying the recorded grade, filled by an answer that records one.
    version: SetVersionRow | None = None
    #: The waiting grade a new one replaced, when :meth:`create` replaced one.
    replaced: OutcomeProposalRow | None = None


_SELECT = f"""
    SELECT p.*,
           {label_sql("v")} AS version_label,
           v.outcome AS version_outcome,
           v.outcome_note AS version_outcome_note,
           (SELECT COUNT(*) FROM shots s
              JOIN shot_judgements j ON j.shot_id = s.id
             WHERE s.set_version_id = p.set_version_id
               AND j.decision IN ('keep', 'improve')) AS counted_shots_now
    FROM set_outcome_proposals p
    JOIN set_versions v ON v.id = p.set_version_id
"""  # noqa: S608 - the only interpolation is the version label expression, a constant


def _refusal_of(refused: str) -> OutcomeProposalRefusal:
    """A grade the version refuses, as this table's slug for it.

    Only three can come out of the version's guarded write when the proposal row
    exists; anything else is the missing-evidence case, the one a shot filed
    elsewhere or re-judged since the card was written produces.
    """
    if refused == "no_version":
        return "no_version"
    if refused == "no_prediction":
        return "no_prediction"
    return "nothing_to_grade"


class OutcomeProposalsRepository(Repository):
    """Reads and writes the grades an agent has proposed for Set versions."""

    def __init__(self, db: Database) -> None:
        super().__init__(db)
        #: The versions half. Held so that an answer can record the grade
        #: through the one guarded write the Set page uses.
        self.sets = SetsRepository(db)

    # ── reading ──────────────────────────────────────────────────────

    async def get(self, set_id: int, proposal_id: int) -> OutcomeProposalRow | None:
        row = await self.db.fetch_one(
            f"{_SELECT} WHERE p.id = ? AND p.set_id = ?", (proposal_id, set_id)
        )
        return self.to_model(OutcomeProposalRow, row)

    async def waiting_for_version(self, version_id: int) -> OutcomeProposalRow | None:
        """The one grade waiting for this version, if there is one."""
        row = await self.db.fetch_one(
            f"{_SELECT} WHERE p.set_version_id = ? AND p.status = 'proposed'", (version_id,)
        )
        return self.to_model(OutcomeProposalRow, row)

    async def waiting_by_version(self, set_id: int) -> dict[int, OutcomeProposalRow]:
        """Every grade waiting on this Set, keyed by the version it grades.

        One query for the Set page, which shows a version's waiting grade beside
        its own outcome without a request per row.
        """
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE p.set_id = ? AND p.status = 'proposed'", (set_id,)
        )
        return {row.set_version_id: row for row in self.to_models(OutcomeProposalRow, rows)}

    async def last_answered(self, version_id: int) -> OutcomeProposalRow | None:
        """What the person last did with a grade of this version.

        `superseded` is not an answer: nobody saw it as a card they could press
        once a newer one stood in its place.
        """
        row = await self.db.fetch_one(
            f"{_SELECT} WHERE p.set_version_id = ? "
            "AND p.status IN ('accepted', 'changed', 'dismissed') "
            "ORDER BY p.decided_at DESC, p.id DESC LIMIT 1",
            (version_id,),
        )
        return self.to_model(OutcomeProposalRow, row)

    async def for_set(self, set_id: int, *, limit: int = 100) -> list[OutcomeProposalRow]:
        """Every grade proposed on this Set, newest first."""
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE p.set_id = ? ORDER BY p.id DESC LIMIT ?", (set_id, limit)
        )
        return self.to_models(OutcomeProposalRow, rows)

    # ── writing ──────────────────────────────────────────────────────

    async def create(
        self, set_id: int, version_id: int, spec: OutcomeProposalWrite
    ) -> OutcomeResult:
        """Write down a grade the person has not agreed to yet.

        The guards are the Set page's own for a grade — the version has a
        prediction, and a shot somebody judged Keep or Improve — because a
        proposal the person could not accept is a card that can only answer 409.
        They read inside the transaction that writes. A grade already waiting
        for this version is **replaced**: the agent revises its answer and the
        last card is the one that counts; the older one goes `superseded`
        before the new row is inserted so the one-waiting index holds.
        """
        now = utc_now()
        async with self.db.transaction():
            version = await self.sets.version_of_set(set_id, version_id)
            if version is None:
                return OutcomeResult(refused="no_version")
            if not version.prediction:
                return OutcomeResult(refused="no_prediction")
            counted = await self.sets.gradable_shot_count(version_id)
            if counted == 0:
                return OutcomeResult(refused="nothing_to_grade")
            if spec.thread_id is not None:
                mine = await self.db.fetch_value(
                    "SELECT 1 FROM chat_threads WHERE id = ? AND set_id = ?",
                    (spec.thread_id, set_id),
                )
                if mine is None:
                    return OutcomeResult(refused="bad_thread")
            replaced = await self.waiting_for_version(version_id)
            if replaced is not None:
                await self._decide(replaced.id, "superseded", now)
            cursor = await self.db.execute(
                """
                INSERT INTO set_outcome_proposals
                    (set_id, set_version_id, thread_id, outcome, note, counted_shots,
                     status, created_at)
                VALUES (:set_id, :version_id, :thread_id, :outcome, :note, :counted,
                        'proposed', :now)
                """,
                {
                    "set_id": set_id,
                    "version_id": version_id,
                    "thread_id": spec.thread_id,
                    "outcome": spec.outcome,
                    "note": spec.note,
                    "counted": counted,
                    "now": now,
                },
            )
            proposal_id = int(cursor.lastrowid or 0)
        return OutcomeResult(
            proposal=await self.get(set_id, proposal_id),
            replaced=replaced,
        )

    async def accept(self, set_id: int, proposal_id: int) -> OutcomeResult:
        """Record the proposed grade on the version, as written. A person's press."""
        return await self._answer(set_id, proposal_id, change=None, note=None)

    async def change(
        self, set_id: int, proposal_id: int, outcome: VersionOutcome, note: str | None = None
    ) -> OutcomeResult:
        """Record a different grade from the card's, and say so (`changed`).

        The note is the card's own unless the person wrote another: the
        per-claim lines are what the grade rested on, and the person is
        choosing a different grade of the same evidence.
        """
        return await self._answer(set_id, proposal_id, change=outcome, note=note)

    async def _answer(
        self, set_id: int, proposal_id: int, *, change: VersionOutcome | None, note: str | None
    ) -> OutcomeResult:
        now = utc_now()
        async with self.db.transaction():
            proposal = await self.get(set_id, proposal_id)
            if proposal is None:
                return OutcomeResult(refused="no_proposal")
            if proposal.status != "proposed":
                return OutcomeResult(refused="not_waiting", proposal=proposal)
            outcome = proposal.outcome if change is None else change
            written = await self.sets.grade_in_transaction(
                set_id,
                proposal.set_version_id,
                VersionOutcomeWrite(outcome=outcome, note=proposal.note if note is None else note),
            )
            if written.refused is not None:
                return OutcomeResult(refused=_refusal_of(written.refused), proposal=proposal)
            await self._decide(
                proposal_id,
                "accepted" if change is None else "changed",
                now,
                recorded_outcome=change,
            )
        log.info(
            "set_outcome_proposal_answered",
            proposal_id=proposal_id,
            set_id=set_id,
            status="accepted" if change is None else "changed",
        )
        return OutcomeResult(proposal=await self.get(set_id, proposal_id), version=written.version)

    async def record_in_transaction(self, proposal: OutcomeProposalRow, now: str) -> OutcomeResult:
        """Accept a waiting grade inside a transaction the caller already holds.

        What accepting the version proposal that follows it does first: one
        press records the grade and appends the next version, so a second tab
        cannot record one without the other. The same guards as :meth:`accept`.
        """
        written = await self.sets.grade_in_transaction(
            proposal.set_id,
            proposal.set_version_id,
            VersionOutcomeWrite(outcome=proposal.outcome, note=proposal.note),
        )
        if written.refused is not None:
            return OutcomeResult(refused=_refusal_of(written.refused), proposal=proposal)
        await self._decide(proposal.id, "accepted", now)
        return OutcomeResult(proposal=proposal, version=written.version)

    async def dismiss(self, set_id: int, proposal_id: int, note: str = "") -> OutcomeResult:
        """Turn a grade down, optionally saying why. Records nothing."""
        now = utc_now()
        async with self.db.transaction():
            proposal = await self.get(set_id, proposal_id)
            if proposal is None:
                return OutcomeResult(refused="no_proposal")
            if proposal.status != "proposed":
                return OutcomeResult(refused="not_waiting", proposal=proposal)
            await self._decide(proposal_id, "dismissed", now, note=note.strip())
        return OutcomeResult(proposal=await self.get(set_id, proposal_id))

    async def _decide(
        self,
        proposal_id: int,
        status: OutcomeProposalStatus,
        now: str,
        *,
        note: str = "",
        recorded_outcome: VersionOutcome | None = None,
    ) -> None:
        await self.db.execute(
            """
            UPDATE set_outcome_proposals
               SET status = :status,
                   decision_note = :note,
                   recorded_outcome = :recorded,
                   decided_at = :now
             WHERE id = :id
            """,
            {
                "status": status,
                "note": note,
                "recorded": recorded_outcome,
                "now": now,
                "id": proposal_id,
            },
        )
