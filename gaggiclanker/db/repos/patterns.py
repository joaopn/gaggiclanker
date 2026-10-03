"""`pattern_runs` and `pattern_proposals`: Find patterns across Sets, and what a person does.

A run reads the confirmed insights of every Set and proposes general insights that several
Sets say in different words. Each method here keeps one property true.

* **A run writes only its own rows.** :meth:`PatternRunsRepository.finish_done` writes the run
  and its proposals and nothing in ``knowledge_insights``. The one other thing it touches is
  the previous runs' proposals and inputs, which it deletes and blanks (see
  :meth:`~PatternRunsRepository.finish_done`): copies of insight text live one run.

* **Only a person approves or dismisses.** :meth:`PatternProposalsRepository.approve` and
  :meth:`~PatternProposalsRepository.dismiss` are the two buttons' writes; nothing a tool, a
  timer or a boot step reaches calls either (``tests/patterns`` walks the registry and the
  bytecode to keep it that way).

* **Approval is one transaction.** The general insight is written confirmed, every source
  that is still a confirmed insight of its Set and still matched by the scope is deleted
  through :meth:`~gaggiclanker.db.repos.knowledge_insights.InsightsRepository.delete_in_transaction`
  (so deletion cards and replacements pointing at it go stale as they do for any delete), and
  an existing general insight the proposal replaces is deleted the same way. A source that
  vanished, was taken back, or whose Set stopped matching the scope in the meantime is
  **skipped and named**: its insight stays where it is, since the general one would not reach
  that Set. The approval is refused when fewer than two Sets' sources remain.

* **The scope is checked with the live matching rule.** A proposal's scope has to hold for
  every source Set, which is :func:`scope_matches` over :func:`set_attributes` (through
  :meth:`InsightsRepository.attributes_of`): the same function that decides which Sets a
  general insight reaches. There is no copy of that rule here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import dumps, utc_now
from gaggiclanker.db.repos.knowledge_insights import (
    InsightScope,
    InsightsRepository,
    InsightWrite,
    scope_matches,
)
from gaggiclanker.db.repository import Repository

log = structlog.get_logger(__name__)

__all__ = [
    "ERROR_MAX",
    "ApproveResult",
    "DismissResult",
    "PatternProposalRow",
    "PatternProposalWrite",
    "PatternProposalsRepository",
    "PatternRunOutcome",
    "PatternRunRow",
    "PatternRunStart",
    "PatternRunsRepository",
    "PatternSetFacts",
    "PatternSkipped",
    "PatternSource",
]

#: How much of an error message a row keeps (a provider's message is a sentence, not a page).
ERROR_MAX = 1000

type PatternRunStatus = Literal["running", "done", "failed", "interrupted"]
type PatternProposalStatus = Literal["proposed", "approved", "dismissed", "superseded"]

#: Why Approve left a source where it was (or could not delete what a proposal replaces).
type SkipReason = Literal["gone", "not_confirmed", "scope_changed", "replaced_gone"]


class PatternSource(BaseModel):
    """One insight a proposal was derived from, as the model was given it."""

    model_config = ConfigDict(extra="forbid")

    insight_id: int
    set_id: int
    #: The Set's name when the run read it, so a card can say where a lesson came from.
    set_name: str
    text: str


class PatternSkipped(BaseModel):
    """A source Approve did not delete, or the replaced general insight that was already gone."""

    model_config = ConfigDict(extra="forbid")

    #: ``None`` for a replaced general insight that was deleted before Approve, which leaves
    #: no id to name (the link to it clears when it goes).
    insight_id: int | None = None
    #: ``None`` for the general insight a proposal replaces.
    set_id: int | None = None
    text: str
    reason: SkipReason


class PatternSetFacts(BaseModel):
    """What a run is told about a Set besides its attributes: the names a person knows it by."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    archived: bool = False
    bean_name: str | None = None
    roaster: str | None = None
    grinder_name: str | None = None


class PatternRunStart(BaseModel):
    """What is known before the provider is asked anything."""

    model_config = ConfigDict(extra="forbid")

    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    #: What the model is told, snapshotted: the Set insights, the general ones and the
    #: declined proposals as they were at this moment.
    input: dict[str, Any] = Field(default_factory=dict)
    insights_read: int = 0
    sets_read: int = 0


class PatternRunOutcome(BaseModel):
    """How a run ended."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["done", "failed"]
    error: str | None = None
    usage: dict[str, Any] | None = None
    llm_call_id: str | None = None
    #: Written again because the request may have left them to the configured defaults.
    provider: str = ""
    model: str = ""
    #: What the post-filter dropped, by reason.
    dropped: dict[str, int] = Field(default_factory=dict)


class PatternRunRow(BaseModel):
    """One run, as a page reads it: everything but the input."""

    model_config = ConfigDict(extra="forbid")

    id: int
    status: PatternRunStatus = "running"
    error: str | None = None
    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    insights_read: int = 0
    sets_read: int = 0
    proposals_kept: int = 0
    proposals_dropped: int = 0
    dropped: dict[str, int] = Field(default_factory=dict)
    usage: dict[str, Any] | None = None
    llm_call_id: str | None = None
    created_at: str
    finished_at: str | None = None


class PatternProposalWrite(BaseModel):
    """One proposal on its way into the table."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    scope: InsightScope = Field(default_factory=InsightScope)
    sources: list[PatternSource] = Field(min_length=2)
    replaces_id: int | None = None
    replaces_text: str = ""

    @field_validator("text")
    @classmethod
    def _one_line(cls, value: str) -> str:
        collapsed = " ".join(value.split())
        if not collapsed:
            raise ValueError("a proposal needs some text")
        return collapsed


class PatternProposalRow(BaseModel):
    """One proposal, as a card reads it."""

    model_config = ConfigDict(extra="forbid")

    id: int
    run_id: int
    text: str
    scope: InsightScope = Field(default_factory=InsightScope)
    sources: list[PatternSource] = Field(default_factory=list)
    #: The general insight it would replace while it is waiting, and its text as given.
    replaces_id: int | None = None
    replaces_text: str = ""
    status: PatternProposalStatus = "proposed"
    #: The general insight Approve wrote.
    insight_id: int | None = None
    skipped: list[PatternSkipped] = Field(default_factory=list)
    created_at: str
    decided_at: str | None = None


type ApproveRefusal = Literal["no_proposal", "not_waiting", "too_few_sets", "run_going"]


@dataclass(frozen=True, slots=True)
class ApproveResult:
    """An approval: the proposal as it ended, or why nothing happened."""

    proposal: PatternProposalRow | None = None
    refused: ApproveRefusal | None = None
    #: What Approve left alone, also stored on the proposal. For a refusal, what it found.
    skipped: list[PatternSkipped] = field(default_factory=list)
    #: The source insights deleted, by id, for the caller's invalidation and the log.
    deleted: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DismissResult:
    proposal: PatternProposalRow | None = None
    refused: Literal["no_proposal", "not_waiting", "run_going"] | None = None


_RUN_COLUMNS = (
    "id, status, error, provider, model, prompt_name, prompt_version, insights_read, sets_read, "
    "proposals_kept, proposals_dropped, dropped_json, usage_json, llm_call_id, created_at, "
    "finished_at"
)


def _decode_run(row: Any) -> PatternRunRow:
    payload = dict(zip(row.keys(), tuple(row), strict=True))
    payload["dropped"] = _loads(payload.pop("dropped_json", "{}"), {})
    usage = payload.pop("usage_json", None)
    payload["usage"] = None if usage is None else _loads(usage, None)
    return PatternRunRow.model_validate(payload)


def _decode_proposal(row: Any) -> PatternProposalRow:
    payload = dict(zip(row.keys(), tuple(row), strict=True))
    scope = _loads(payload.pop("scope_json", "{}"), {})
    payload["scope"] = InsightScope.model_validate(scope)
    payload["sources"] = _loads(payload.pop("sources_json", "[]"), [])
    payload["skipped"] = _loads(payload.pop("skipped_json", "[]"), [])
    return PatternProposalRow.model_validate(payload)


def _loads(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw) if isinstance(raw, str) else default
    except ValueError:
        return default


class PatternRunsRepository(Repository):
    """Opens, closes and reads runs. The only writer of `pattern_runs`."""

    async def start(self, spec: PatternRunStart) -> int:
        """Open a `running` row. Returns its id."""
        cursor = await self.db.execute(
            """
            INSERT INTO pattern_runs
                (status, provider, model, prompt_name, prompt_version, input_json,
                 insights_read, sets_read, created_at)
            VALUES ('running', :provider, :model, :prompt_name, :prompt_version, :input_json,
                    :insights_read, :sets_read, :created_at)
            """,
            {
                "provider": spec.provider,
                "model": spec.model,
                "prompt_name": spec.prompt_name,
                "prompt_version": spec.prompt_version,
                "input_json": dumps(spec.input),
                "insights_read": spec.insights_read,
                "sets_read": spec.sets_read,
                "created_at": utc_now(),
            },
        )
        return int(cursor.lastrowid or 0)

    async def finish_failed(self, run_id: int, outcome: PatternRunOutcome) -> PatternRunRow | None:
        """Close a run that produced nothing. Touches no proposal."""
        await self._close(run_id, outcome, kept=0)
        return await self.get(run_id)

    async def finish_done(
        self,
        run_id: int,
        outcome: PatternRunOutcome,
        proposals: list[PatternProposalWrite],
    ) -> PatternRunRow | None:
        """Close a run with its proposals, and clear what the earlier runs kept, in one step.

        **Copies of insight text live one run.** A proposal quotes its sources as the model was
        given them and a run's input holds every insight it read, so once the insights they came
        from are deleted those copies are the only place the words survive. When a run finishes,
        every proposal of every **earlier** run is deleted (waiting, approved, dismissed alike:
        the newest finished run's proposals are the only ones anything reads, and the dismissed
        ones were told to this run through its input) and every earlier run's input is blanked.
        The rows themselves stay (status, counts, model, timestamps), so the since-count and an
        approved insight's ``pattern_run_id`` still resolve. A failed run's input is blanked by
        the next run that finishes. After an insight is deleted, its words can therefore survive
        in the newest run's input and proposals until the next run finishes, and never longer.
        """
        now = utc_now()
        async with self.db.transaction():
            await self._close(run_id, outcome, kept=len(proposals))
            for proposal in proposals:
                await self.db.execute(
                    """
                    INSERT INTO pattern_proposals
                        (run_id, text, scope_json, sources_json, replaces_id, replaces_text,
                         status, created_at)
                    VALUES (:run_id, :text, :scope, :sources, :replaces_id, :replaces_text,
                            'proposed', :now)
                    """,
                    {
                        "run_id": run_id,
                        "text": proposal.text,
                        "scope": dumps(proposal.scope.stated()),
                        "sources": dumps([item.model_dump() for item in proposal.sources]),
                        "replaces_id": proposal.replaces_id,
                        "replaces_text": proposal.replaces_text,
                        "now": now,
                    },
                )
            await self.db.execute("DELETE FROM pattern_proposals WHERE run_id != ?", (run_id,))
            await self.db.execute(
                "UPDATE pattern_runs SET input_json = '{}' WHERE id != ?", (run_id,)
            )
        return await self.get(run_id)

    async def _close(self, run_id: int, outcome: PatternRunOutcome, *, kept: int) -> None:
        dropped = sum(outcome.dropped.values())
        await self.db.execute(
            """
            UPDATE pattern_runs
               SET status = :status,
                   error = :error,
                   proposals_kept = :kept,
                   proposals_dropped = :dropped,
                   dropped_json = :dropped_json,
                   usage_json = :usage_json,
                   llm_call_id = COALESCE(:llm_call_id, llm_call_id),
                   provider = CASE WHEN :provider = '' THEN provider ELSE :provider END,
                   model = CASE WHEN :model = '' THEN model ELSE :model END,
                   finished_at = :finished_at
             WHERE id = :id
            """,
            {
                "id": run_id,
                "status": outcome.status,
                "error": None if outcome.error is None else outcome.error[:ERROR_MAX],
                "kept": kept,
                "dropped": dropped,
                "dropped_json": dumps(dict(sorted(outcome.dropped.items()))),
                "usage_json": None if outcome.usage is None else dumps(outcome.usage),
                "llm_call_id": outcome.llm_call_id,
                "provider": outcome.provider,
                "model": outcome.model,
                "finished_at": utc_now(),
            },
        )

    async def get(self, run_id: int) -> PatternRunRow | None:
        row = await self.db.fetch_one(
            f"SELECT {_RUN_COLUMNS} FROM pattern_runs WHERE id = ?",  # noqa: S608 - a literal column list
            (run_id,),
        )
        return None if row is None else _decode_run(row)

    async def detail_input(self, run_id: int) -> dict[str, Any] | None:
        """What the model was told in this run, verbatim."""
        raw = await self.db.fetch_value(
            "SELECT input_json FROM pattern_runs WHERE id = ?", (run_id,)
        )
        if raw is None:
            return None
        loaded = _loads(raw, {})
        return loaded if isinstance(loaded, dict) else {}

    async def latest(self) -> PatternRunRow | None:
        """The newest run, whatever state it is in."""
        row = await self.db.fetch_one(
            f"SELECT {_RUN_COLUMNS} FROM pattern_runs ORDER BY id DESC LIMIT 1"  # noqa: S608 - a literal column list
        )
        return None if row is None else _decode_run(row)

    async def latest_running(self) -> PatternRunRow | None:
        row = await self.db.fetch_one(
            f"SELECT {_RUN_COLUMNS} FROM pattern_runs WHERE status = 'running' "  # noqa: S608 - a literal column list
            "ORDER BY id DESC LIMIT 1"
        )
        return None if row is None else _decode_run(row)

    async def latest_done(self) -> PatternRunRow | None:
        row = await self.db.fetch_one(
            f"SELECT {_RUN_COLUMNS} FROM pattern_runs WHERE status = 'done' "  # noqa: S608 - a literal column list
            "ORDER BY id DESC LIMIT 1"
        )
        return None if row is None else _decode_run(row)

    async def reconcile_running(self) -> int:
        """Mark every `running` row `interrupted`. Runs once, at boot.

        A row is only `running` while a process holds it, and no process survives a boot.
        """
        cursor = await self.db.execute(
            """
            UPDATE pattern_runs
               SET status = 'interrupted',
                   error = COALESCE(error, 'the process stopped before this run finished'),
                   finished_at = ?
             WHERE status = 'running'
            """,
            (utc_now(),),
        )
        return cursor.rowcount

    async def sets_with_confirmed_insights(self) -> list[PatternSetFacts]:
        """The Sets a run reads, by id: not being designed, with a confirmed insight each.

        Archived ones are in: what they taught is still true, or was replaced.
        """
        rows = await self.db.fetch_all(
            """
            SELECT s.id, s.name, s.archived, b.name AS bean_name, b.roaster,
                   g.name AS grinder_name
              FROM sets s
              LEFT JOIN beans b ON b.id = s.bean_id
              LEFT JOIN grinders g ON g.id = s.grinder_id
             WHERE s.designing = 0
               AND EXISTS (SELECT 1 FROM knowledge_insights i
                            WHERE i.set_id = s.id AND i.confirmed = 1 AND i.dismissed = 0)
             ORDER BY s.id
            """
        )
        return self.to_models(PatternSetFacts, rows)

    # ── the page's two numbers ───────────────────────────────────────

    async def sets_with_insights(self) -> int:
        """How many Sets have at least one confirmed insight: the run needs two."""
        return int(
            await self.db.fetch_value(
                "SELECT COUNT(DISTINCT i.set_id) FROM knowledge_insights i "
                "JOIN sets s ON s.id = i.set_id "
                "WHERE i.confirmed = 1 AND i.dismissed = 0 AND s.designing = 0"
            )
            or 0
        )

    async def new_since_last_run(self) -> int:
        """Confirmed Set insights confirmed after the last **finished** run began.

        A failed or interrupted run read nothing the person can rely on, so it does not move
        the line; with no finished run, every confirmed Set insight is new.
        """
        since = await self.db.fetch_value(
            "SELECT MAX(created_at) FROM pattern_runs WHERE status = 'done'"
        )
        return int(
            await self.db.fetch_value(
                "SELECT COUNT(*) FROM knowledge_insights i JOIN sets s ON s.id = i.set_id "
                "WHERE i.confirmed = 1 AND i.dismissed = 0 AND s.designing = 0 "
                "AND (? IS NULL OR i.confirmed_at > ?)",
                (since, since),
            )
            or 0
        )


class PatternProposalsRepository(Repository):
    """Reads proposals and carries out a person's answer to one."""

    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.insights = InsightsRepository(db)

    async def get(self, proposal_id: int) -> PatternProposalRow | None:
        row = await self.db.fetch_one(
            "SELECT * FROM pattern_proposals WHERE id = ?", (proposal_id,)
        )
        return None if row is None else _decode_proposal(row)

    async def for_run(self, run_id: int) -> list[PatternProposalRow]:
        rows = await self.db.fetch_all(
            "SELECT * FROM pattern_proposals WHERE run_id = ? ORDER BY id", (run_id,)
        )
        return [_decode_proposal(row) for row in rows]

    async def declined(self) -> list[PatternProposalRow]:
        """The dismissed proposals, oldest first: what the next run is told was declined."""
        rows = await self.db.fetch_all(
            "SELECT * FROM pattern_proposals WHERE status = 'dismissed' ORDER BY id"
        )
        return [_decode_proposal(row) for row in rows]

    async def dismiss(self, proposal_id: int) -> DismissResult:
        """Turn a proposal down. A person's press; nothing but the proposal changes."""
        async with self.db.transaction():
            proposal = await self.get(proposal_id)
            if proposal is None:
                return DismissResult(refused="no_proposal")
            if proposal.status != "proposed":
                return DismissResult(proposal=proposal, refused="not_waiting")
            if await self._a_run_is_going():
                return DismissResult(proposal=proposal, refused="run_going")
            await self.db.execute(
                "UPDATE pattern_proposals SET status = 'dismissed', decided_at = ? WHERE id = ?",
                (utc_now(), proposal_id),
            )
        return DismissResult(proposal=await self.get(proposal_id))

    async def approve(self, proposal_id: int) -> ApproveResult:
        """Write the general insight and delete what it was derived from, in one transaction.

        Everything is read inside the transaction that writes, so a source deleted or taken
        back a moment ago is skipped rather than deleted twice or counted; nothing is written
        at all when fewer than two Sets' sources remain.
        """
        now = utc_now()
        async with self.db.transaction():
            proposal = await self.get(proposal_id)
            if proposal is None:
                return ApproveResult(refused="no_proposal")
            if proposal.status != "proposed":
                return ApproveResult(proposal=proposal, refused="not_waiting")
            if await self._a_run_is_going():
                return ApproveResult(proposal=proposal, refused="run_going")

            scope = proposal.scope.stated()
            kept: list[PatternSource] = []
            skipped: list[PatternSkipped] = []
            for source in proposal.sources:
                reason = await self._why_not_deletable(source, scope)
                if reason is None:
                    kept.append(source)
                else:
                    skipped.append(
                        PatternSkipped(
                            insight_id=source.insight_id,
                            set_id=source.set_id,
                            text=source.text,
                            reason=reason,
                        )
                    )
            if len({source.set_id for source in kept}) < 2:
                return ApproveResult(proposal=proposal, refused="too_few_sets", skipped=skipped)

            if proposal.replaces_id is not None or proposal.replaces_text:
                # The link clears itself (SET NULL) when the old insight is deleted by any
                # other path, but the text given with the proposal stays: that is how a
                # replacement that lost its target is still told to the person.
                old = (
                    None
                    if proposal.replaces_id is None
                    else await self.db.fetch_one(
                        "SELECT set_id FROM knowledge_insights WHERE id = ?",
                        (proposal.replaces_id,),
                    )
                )
                if old is not None and old["set_id"] is None:
                    assert proposal.replaces_id is not None
                    await self.insights.delete_in_transaction(proposal.replaces_id)
                else:
                    skipped.append(
                        PatternSkipped(
                            insight_id=proposal.replaces_id,
                            text=proposal.replaces_text,
                            reason="replaced_gone",
                        )
                    )

            # A person approved it, so it is confirmed and `user` is who learned it; the run is
            # recorded beside it. Written before the deletes so a failing delete rolls both back.
            general_id = await self.insights.insert(
                InsightWrite(
                    text=proposal.text,
                    scope=proposal.scope,
                    source="user",
                    confirmed=True,
                    pattern_run_id=proposal.run_id,
                )
            )
            for source in kept:
                await self.insights.delete_in_transaction(source.insight_id)
            await self.db.execute(
                """
                UPDATE pattern_proposals
                   SET status = 'approved', decided_at = :now, insight_id = :insight_id,
                       skipped_json = :skipped, replaces_id = NULL
                 WHERE id = :id
                """,
                {
                    "now": now,
                    "insight_id": general_id,
                    "skipped": dumps([item.model_dump() for item in skipped]),
                    "id": proposal_id,
                },
            )
        log.info(
            "pattern_proposal_approved",
            proposal_id=proposal_id,
            insight_id=general_id,
            deleted=len(kept),
            skipped=len(skipped),
        )
        return ApproveResult(
            proposal=await self.get(proposal_id),
            skipped=skipped,
            deleted=[source.insight_id for source in kept],
        )

    async def _a_run_is_going(self) -> bool:
        """Whether a run is `running`: no answer is taken while one is.

        Read inside the answer's transaction. A run finishing deletes every earlier proposal, so
        a dismissal made during it would be deleted without any run having been told, and an
        approval would delete sources the run has already read. Answers wait for the run.
        """
        return bool(
            await self.db.fetch_value("SELECT 1 FROM pattern_runs WHERE status = 'running' LIMIT 1")
        )

    async def _why_not_deletable(
        self, source: PatternSource, scope: dict[str, Any]
    ) -> SkipReason | None:
        """Why this source must stay, or ``None`` when Approve may delete it.

        It has to still be a confirmed, not dismissed insight of the Set the run read it in,
        and that Set has to still be matched by the scope: the general insight must reach
        the Set whose lesson it replaces, or deleting the source would lose the lesson there.
        """
        row = await self.db.fetch_one(
            "SELECT set_id, confirmed, dismissed FROM knowledge_insights WHERE id = ?",
            (source.insight_id,),
        )
        if row is None or row["set_id"] != source.set_id:
            return "gone"
        if not row["confirmed"] or row["dismissed"]:
            return "not_confirmed"
        attributes = await self.insights.attributes_of(source.set_id)
        if attributes is None:
            return "gone"
        if not scope_matches(scope, attributes):
            return "scope_changed"
        return None
