"""Running an analysis: a row, a call, an outcome, and the advice that follows.

The shape of :meth:`AnalyzerService.run_analysis` is the contract, and it is
short on purpose:

    open a `running` row  ->  build the context  ->  call_json  ->  close the row

**It never raises for a provider failure.** That is the chunk's fourth
acceptance criterion and the reason the LLM layer returns failures as values
(`gaggiclanker/llm/types.py`): a 429, a bad key, a timeout or unparseable JSON
all come back as an ``Err``, and every one of them becomes a stored `failed`
analysis carrying the error code. A caller gets a row back, never an exception,
so a batch of sixty shots cannot be taken down by the eleventh.

**The row is opened before the call.** A process that dies mid-call therefore
leaves a `running` row, which the next boot marks `interrupted`
(:meth:`AnalysesRepository.reconcile_running`). The alternative — write the row
when the answer arrives — loses every crashed run silently, which is exactly the
class of failure a person needs told about.

**The work does not run inside the HTTP request.** :meth:`AnalyzerService.start`
opens the row and hands the call to the app's :class:`TaskRegistry`; the route
answers 202 with the running row. Three things follow from that, and all three
are the reason it is done this way:

* ``docker stop`` gives ten seconds. A request holding a two-minute provider
  call is killed mid-flight with the client still waiting; a registered task is
  cancelled by the lifespan, the ``CancelledError`` path leaves the row
  `running`, and the next boot marks it `interrupted`.
* **the task name is the idempotency rule.** ``analysis:<shot_id>`` can only be
  in the registry once, and the name is claimed *synchronously* inside
  :meth:`~gaggiclanker.infra.tasks.TaskRegistry.spawn` — before any await — so a
  second browser tab pressing the button cannot start a second analysis of the
  same shot however the two requests interleave. It gets the running row back
  instead.
* a batch is one task too, so cancelling it at shutdown cancels the whole
  thing rather than sixty individual futures nobody is holding.

**Post-processing is where policy lives.** `rules_used` is filtered against the
rules this shot was actually given, so an invented citation is dropped rather
than shown; `profile_patch` is stored and never applied, because gaggiclanker
writes nothing to the device.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any

import structlog

from gaggiclanker.analyzer.context import AnalysisContext, build_context
from gaggiclanker.analyzer.models import MAX_PROPOSED_INSIGHTS, AnalysisResult
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.analyses import (
    AnalysesRepository,
    AnalysisRow,
    AnalysisStart,
    SuggestionsRepository,
    SuggestionWrite,
)
from gaggiclanker.db.repos.knowledge_insights import (
    InsightsRepository,
    InsightWrite,
)
from gaggiclanker.infra.sse import SseEvent, SseEventBus
from gaggiclanker.infra.tasks import TaskRegistry, TaskSpawner
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import LlmMessage, LlmRequest, Ok

__all__ = [
    "ANALYSIS_EVENTS",
    "ANALYSIS_PROMPT",
    "ANALYSIS_USER_PROMPT",
    "BATCH_ACKNOWLEDGE_ABOVE",
    "BATCH_CONCURRENCY",
    "AnalyzerService",
    "BatchResult",
    "LargeBatch",
    "analysis_task_name",
    "set_task_name",
]

log = structlog.get_logger(__name__)

#: The two prompts one analysis renders. Split so the persona and the layout of
#: the facts can be edited independently; see `prompts/analysis.yaml`.
ANALYSIS_PROMPT = "analysis"
ANALYSIS_USER_PROMPT = "analysis-user"

#: The SSE events a run publishes, on the LLM bus. The header's activity
#: indicator is already watching that stream, so an analysis started from a
#: batch shows up in a tab that is looking at something else entirely.
ANALYSIS_EVENTS = ("analysis.started", "analysis.finished", "analysis.failed")

#: How many analyses run at once in a batch. Two, not ten: each call is a
#: minute of a provider's attention, the rate-limit budget is process-wide
#: (`gaggiclanker/llm/budget.py`), and a wide pool's only achievement would be
#: hitting the limit sooner and latching the whole app.
BATCH_CONCURRENCY = 2

#: A queued batch larger than this is refused until the caller acknowledges
#: its size. Every shot is a provider call, and a Set that has collected shots
#: for weeks turns one press of "analyse the un-analysed" into dozens of them:
#: real money and most of an hour of the rate-limit budget. Ten is roughly five
#: minutes at the pool's width; past that the person should see the number
#: before it is spent.
BATCH_ACKNOWLEDGE_ABOVE = 10

#: Linear backoff between this call's own retries. A real wait in production —
#: a provider that just 429'd wants a moment — and the one thing in an analysis
#: that costs wall-clock time with no provider involved, so the suite sets it to
#: zero rather than sleeping out several seconds per failure path.
RETRY_DELAY_S = 0.5


#: Separates "the caller said nothing about a re-run" from "the caller said
#: there is none". The first means "find the last successful analysis yourself",
#: which is what a button press wants; the second means "do not tell the model
#: anything", which is what a fresh run wants.
_UNSET_RERUN: Any = object()


@dataclass(frozen=True, slots=True)
class _Prepared:
    """Everything assembled before the provider is contacted."""

    row: AnalysisRow
    context: AnalysisContext
    system: str
    user: str
    version: str
    model: str


def analysis_task_name(shot_id: int) -> str:
    """The registry name that makes one analysis per shot an invariant."""
    return f"analysis:{shot_id}"


def set_task_name(set_id: int) -> str:
    """The registry name for a Set batch. One batch per Set at a time."""
    return f"analyse_set:{set_id}"


@dataclass(frozen=True, slots=True)
class BatchResult:
    """What a Set batch was asked to do, and — once it has run — what it did.

    Counts, not rows: a Set can hold hundreds. The route answers with this
    before the work happens, so `requested` and `skipped` are the useful fields
    there and the rest fill in as the task runs; `analyse_set` returns the same
    shape fully populated for a caller that ran it directly.
    """

    set_id: int
    requested: int
    #: Shots left alone because an analysis of them is already running. Not an
    #: error: two overlapping batches, or a batch and somebody pressing the
    #: button on one shot, are both ordinary.
    skipped: int = 0
    succeeded: int = 0
    failed: int = 0
    #: Set when the rate-limit latch stopped the batch part-way. The remaining
    #: shots were not attempted at all, which is the latch working as designed.
    stopped: bool = False
    #: The registry name, so a caller can tell that work was queued and, in a
    #: test, wait for it.
    task: str = ""
    analysis_ids: list[int] = field(default_factory=list)


class LargeBatch(Exception):
    """A Set batch bigger than :data:`BATCH_ACKNOWLEDGE_ABOVE`, not acknowledged.

    Its own type rather than a ``RuntimeError``: the route already turns that
    into "a batch is running", and this is a different answer to act on.
    """

    def __init__(self, set_id: int, count: int) -> None:
        super().__init__(f"Set {set_id} batch of {count} shots needs acknowledging")
        self.set_id = set_id
        self.count = count


class AnalyzerService:
    """The one entry point. Held on ``app.state.analyzer``."""

    def __init__(
        self,
        db: Database,
        llm: LlmService,
        prompts: PromptService,
        *,
        bus: SseEventBus | None = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.prompts = prompts
        self.bus = bus
        #: Overridable so tests do not sleep out the backoff; see
        #: :data:`RETRY_DELAY_S`.
        self.retry_delay_s = RETRY_DELAY_S
        self.analyses = AnalysesRepository(db)
        self.suggestions = SuggestionsRepository(db)
        self.insights = InsightsRepository(db)
        # Shots whose row is being opened right now, so a second request can
        # wait for the first one's row instead of reading a database the first
        # one has not written to yet. Held only across the opening — once the
        # row exists, the registry's name guard and the row itself are enough.
        self._opening: dict[int, asyncio.Future[AnalysisRow]] = {}

    # ── one shot ─────────────────────────────────────────────────────

    async def start(
        self,
        shot_id: int,
        *,
        tasks: TaskSpawner,
        model: str | None = None,
    ) -> tuple[AnalysisRow, bool]:
        """Queue an analysis. Returns the row and whether this call started it.

        The row comes back before the provider is contacted, because the row is
        the handle: the page renders it as `running`, the SSE stream says when
        it moves, and the caller never holds a two-minute request open.

        Idempotent on the shot. The registry name is claimed synchronously
        inside ``spawn``, so two requests that interleave cannot both start
        work; the loser gets the running row and ``False``.

        Raises ``LookupError`` for a shot that does not exist — the one failure
        that is the caller's mistake rather than the provider's, and the caller
        wants it as a 404 rather than as a stored `failed` row.
        """
        # No await before the spawn below, so this check and the registry's own
        # name guard together leave no window: either somebody is mid-open and
        # we wait for their row, or the name is free and we claim it.
        pending = self._opening.get(shot_id)
        if pending is not None:
            return await asyncio.shield(pending), False

        opened: asyncio.Future[AnalysisRow] = asyncio.get_running_loop().create_future()
        self._opening[shot_id] = opened
        try:
            tasks.spawn(analysis_task_name(shot_id), self._background(shot_id, model, opened))
        except RuntimeError:
            # The name is held by a task that has already opened its row — it
            # removed itself from `_opening` when it did.
            del self._opening[shot_id]
            opened.cancel()
            running = await self.analyses.latest_for_shot(shot_id)
            if running is not None and running.status == "running":
                return running, False
            raise
        row = await opened
        return row, row.status == "running"

    async def _background(
        self, shot_id: int, model: str | None, opened: asyncio.Future[AnalysisRow]
    ) -> None:
        """The registered task: open the row, hand it back, then do the work.

        The future is how ``start`` gets the row without awaiting the call. It
        is resolved before the provider is touched and never after, so a caller
        blocked on it waits for a database write rather than for a model.
        """
        try:
            # A batch may have opened one between the spawn and here. The
            # registry cannot see that — the batch runs its shots inside its
            # own task — so the row is the check.
            existing = await self.analyses.latest_for_shot(shot_id)
            if existing is not None and existing.status == "running":
                opened.set_result(existing)
                return
            prepared = await self._prepare(shot_id, model=model)
        except BaseException as exc:
            if not opened.done():
                opened.set_exception(exc)
            raise
        finally:
            # Whatever happened, the opening is over: a later request should
            # read the row (or fail cleanly) rather than wait on this future.
            self._opening.pop(shot_id, None)
        opened.set_result(prepared.row)
        await self._complete(prepared)

    async def run_analysis(
        self,
        shot_id: int,
        *,
        purpose: str = "analysis",
        model: str | None = None,
        rerun_of: int | None = None,
    ) -> AnalysisRow:
        """Analyse one shot and wait for it. Returns the row, whatever happened.

        The direct form, used by the batch and by anything that genuinely wants
        to block. A route should call :meth:`start` instead — see the module
        docstring for why a provider call has no business inside a request.

        ``rerun_of`` carries an earlier analysis of the same shot into the
        context; pass ``None`` and the model is not told it has seen this shot
        before, which reliably produces the same answer in different words.
        """
        prepared = await self._prepare(shot_id, model=model, purpose=purpose, rerun_of=rerun_of)
        return await self._complete(prepared)

    async def _prepare(
        self,
        shot_id: int,
        *,
        model: str | None = None,
        purpose: str = "analysis",
        rerun_of: int | None = _UNSET_RERUN,
    ) -> _Prepared:
        """Everything before the provider: the context, the prompts, the row.

        ``rerun_of`` defaults to "work it out": the newest *successful* analysis
        of this shot, so a re-run started from a button is told what it said
        last time without the caller having to look it up.
        """
        config = await self.llm.config()
        resolved = (model or "").strip() or config.resolve_model(purpose)  # type: ignore[arg-type]

        if rerun_of is _UNSET_RERUN:
            previous = await self.analyses.latest_for_shot(shot_id)
            rerun_of = previous.id if previous is not None and previous.status == "ok" else None

        # The excerpt budget is a setting, and it is read here rather than
        # inside `build_context` so that assembling a context stays a pure
        # function of the database it was handed — which is what lets the golden
        # test build one without a settings service.
        budget = int(await self.llm.settings.get("analysisChunkTokenBudget"))
        context = await build_context(
            self.db, shot_id, rerun_of=rerun_of, chunk_token_budget=budget
        )
        system, user, version = await self._render(context)

        analysis_id = await self.analyses.start(
            AnalysisStart(
                shot_id=shot_id,
                set_version_id=context.set.version_id if context.set else None,
                provider=config.provider,
                model=resolved,
                prompt_name=ANALYSIS_PROMPT,
                prompt_version=version,
                input=context.model_dump(mode="json"),
            )
        )
        self._publish("analysis.started", analysis_id, shot_id, status="running")
        row = _require(await self.analyses.get(analysis_id), analysis_id)
        return _Prepared(
            row=row,
            context=context,
            system=system,
            user=user,
            version=version,
            model=resolved,
        )

    async def _complete(self, prepared: _Prepared) -> AnalysisRow:
        """The provider call and the bookkeeping. Never raises for a failure.

        An *unexpected* exception — a bug in here, not a refusal out there — is
        also turned into a stored `failed` row before it is re-raised. A batch
        must be able to lose one shot to a programming error without losing its
        siblings, and a row that stays `running` for ever because something
        threw is exactly the spinner boot reconciliation exists to prevent.
        """
        analysis_id = prepared.row.id
        shot_id = prepared.row.shot_id
        context = prepared.context

        try:
            result = await self.llm.call_json(
                LlmRequest(
                    messages=[
                        LlmMessage(role="system", content=prepared.system),
                        LlmMessage(role="user", content=prepared.user),
                    ],
                    output_model=AnalysisResult,
                    model=prepared.model,
                    purpose="analysis",
                    label="analyse shot",
                    subject=f"#{context.shot.device_id} {context.shot.profile_name}".strip(),
                    prompt_name=ANALYSIS_PROMPT,
                    prompt_version=prepared.version,
                    retry_delay_s=self.retry_delay_s,
                )
            )
        except asyncio.CancelledError:
            # A cancelled call is not a provider failure, so it is not a
            # `failed` analysis either: the row stays `running` and boot
            # reconciliation will mark it `interrupted`, which is what actually
            # happened to it. Re-raised, because a caller that gave up must not
            # be made to wait for bookkeeping it did not ask for.
            self._publish("analysis.failed", analysis_id, shot_id, status="running")
            raise
        except Exception as exc:
            await self.analyses.finish(
                analysis_id, status="failed", error=f"unknown: {type(exc).__name__}: {exc}"
            )
            log.exception("analysis_crashed", analysis_id=analysis_id, shot_id=shot_id)
            self._publish("analysis.failed", analysis_id, shot_id, status="failed")
            raise

        if not isinstance(result, Ok):
            row = await self.analyses.finish(
                analysis_id,
                status="failed",
                error=f"{result.code}: {result.message}",
                provider=result.provider,
                model=result.model,
                llm_call_id=result.call_id or None,
                usage=_usage(result.usage.prompt_tokens, result.usage.completion_tokens),
            )
            log.info(
                "analysis_failed",
                analysis_id=analysis_id,
                shot_id=shot_id,
                code=result.code,
                provider=result.provider,
            )
            self._publish("analysis.failed", analysis_id, shot_id, status="failed")
            return _require(row, analysis_id)

        output = _post_process(result.data, context)
        await self.analyses.finish(
            analysis_id,
            status="ok",
            output=output.model_dump(mode="json", by_alias=True),
            usage=_usage(result.usage.prompt_tokens, result.usage.completion_tokens),
            provider=result.provider,
            model=result.model,
            llm_call_id=result.call_id or None,
        )
        await self._store_insights(analysis_id, output)
        await self.suggestions.insert_many(
            analysis_id,
            [
                SuggestionWrite(
                    variable=item.variable,
                    direction=item.direction,
                    magnitude=item.magnitude,
                    unit=item.unit,
                    reason=item.reason,
                    confidence=item.confidence,
                    priority=item.priority,
                )
                for item in output.suggestions
            ],
        )
        log.info(
            "analysis_finished",
            analysis_id=analysis_id,
            shot_id=shot_id,
            suggestions=len(output.suggestions),
            model=result.model,
        )
        self._publish("analysis.finished", analysis_id, shot_id, status="ok")
        # Re-read so the row carries its suggestions, which the caller renders.
        return _require(await self.analyses.get(analysis_id), analysis_id)

    async def _store_insights(self, analysis_id: int, output: AnalysisResult) -> None:
        """The proposals, as unconfirmed rows linked to this analysis.

        Unconfirmed is the whole point: they are shown on the panel with a
        confirm button and nothing reads them until somebody presses it
        (`gaggiclanker/db/repos/knowledge_insights.py`). Stored even so, rather
        than left in the output document, because confirming one has to be a
        `PATCH` on a row rather than an edit to a stored LLM reply — and because
        a proposal the user ignores is still evidence about whether the prompt
        is asking for the right thing.
        """
        for insight in output.proposed_insights:
            await self.insights.insert(
                InsightWrite(
                    scope=insight.scope,
                    text=insight.text,
                    evidence_shot_ids=insight.evidence_shot_ids,
                    source="analysis",
                    analysis_id=analysis_id,
                    confirmed=False,
                )
            )

    async def _render(self, context: AnalysisContext) -> tuple[str, str, str]:
        """The two prompts, rendered, plus the version string for the ledger.

        The version is both prompts' `updated_at` joined, because an analysis
        produced by an edited layout and an analysis produced by an edited
        persona are different analyses and the ledger has one column for it.
        """
        variables = context.render()
        system = await self.prompts.load(ANALYSIS_PROMPT)
        user = await self.prompts.load(ANALYSIS_USER_PROMPT, variables)
        return system.system, user.user, f"{system.version}+{user.version}"

    # ── a Set at a time ──────────────────────────────────────────────

    async def start_set(
        self,
        set_id: int,
        *,
        tasks: TaskRegistry,
        only_unanalysed: bool = True,
        model: str | None = None,
        acknowledge_large_batch: bool = False,
    ) -> BatchResult:
        """Queue a Set batch. Returns what it is about to do, not what it did.

        The shot list is resolved here rather than inside the task so the caller
        gets real numbers back with its 202 — "47 queued, 2 already running" is
        an answer; "accepted" is not.

        One batch per Set at a time, by the registry name. A second press while
        one is running is refused rather than doubled: the first batch is
        already working through exactly the shots the second one would pick.

        More than :data:`BATCH_ACKNOWLEDGE_ABOVE` shots raises
        :class:`LargeBatch` unless ``acknowledge_large_batch`` says the caller
        has seen the size. The count is what would actually be queued, after
        the running ones are left out, so the number a person is asked about
        is the number of calls they are agreeing to. The direct
        :meth:`analyse_set` is not gated: its callers wait for the result and
        chose the Set in code, not with a button.
        """
        shot_ids, skipped = await self._batch_shots(set_id, only_unanalysed=only_unanalysed)
        if not shot_ids:
            return BatchResult(set_id=set_id, requested=0, skipped=skipped)
        if len(shot_ids) > BATCH_ACKNOWLEDGE_ABOVE and not acknowledge_large_batch:
            log.info("analyse_set_refused_large", set_id=set_id, requested=len(shot_ids))
            raise LargeBatch(set_id, len(shot_ids))
        name = set_task_name(set_id)
        tasks.spawn(name, self._run_batch(set_id, shot_ids, model))
        return BatchResult(set_id=set_id, requested=len(shot_ids), skipped=skipped, task=name)

    async def analyse_set(
        self,
        set_id: int,
        *,
        only_unanalysed: bool = True,
        model: str | None = None,
    ) -> BatchResult:
        """Analyse every shot in a Set and wait for it. The direct form.

        ``only_unanalysed`` skips shots that already have a successful analysis.
        A *failed* one does not count as analysed: the whole point of the
        default is to pick up what the rate limit or a restart dropped. A shot
        whose analysis is already *running* is skipped either way — something
        else is doing it.
        """
        shot_ids, skipped = await self._batch_shots(set_id, only_unanalysed=only_unanalysed)
        if not shot_ids:
            return BatchResult(set_id=set_id, requested=0, skipped=skipped)
        result = await self._run_batch(set_id, shot_ids, model)
        return replace(result, skipped=skipped)

    async def _batch_shots(self, set_id: int, *, only_unanalysed: bool) -> tuple[list[int], int]:
        """The shots to analyse, and how many were left alone because they are running."""
        candidates = (
            await self.analyses.unanalysed_in_set(set_id)
            if only_unanalysed
            else await self.analyses.shots_in_set(set_id)
        )
        running = await self.analyses.running_shot_ids()
        wanted = [shot_id for shot_id in candidates if shot_id not in running]
        return wanted, len(candidates) - len(wanted)

    async def _run_batch(self, set_id: int, shot_ids: list[int], model: str | None) -> BatchResult:
        """The work itself, through a pool two wide.

        ``gather(return_exceptions=True)`` rather than a ``TaskGroup``: a task
        group cancels its siblings the moment one raises, and losing
        fifty-nine shots because the eleventh hit a bug is not a trade worth
        making. Each shot has already recorded its own outcome by the time an
        exception gets here (`_complete` stores a `failed` row before it
        re-raises), so this only has to count it and carry on.

        The rate-limit latch is respected between shots: once the process is
        stopped, the remaining shots are not attempted at all. Sixty calls each
        failing after three retries is eleven minutes of nothing, which is
        exactly what the latch exists to prevent (`gaggiclanker/llm/budget.py`).
        """
        semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)
        succeeded = 0
        failed = 0
        ids: list[int] = []

        async def one(shot_id: int) -> None:
            nonlocal succeeded, failed
            async with semaphore:
                if self.llm.budget.stopped:
                    return
                try:
                    row = await self.run_analysis(shot_id, model=model)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Already stored as a failed row by `_complete`; logged
                    # there too. Nothing to do here but let the siblings run.
                    failed += 1
                    return
                ids.append(row.id)
                if row.status == "ok":
                    succeeded += 1
                else:
                    failed += 1

        await asyncio.gather(*(one(shot_id) for shot_id in shot_ids), return_exceptions=False)

        stopped = self.llm.budget.stopped
        log.info(
            "analyse_set_finished",
            set_id=set_id,
            requested=len(shot_ids),
            succeeded=succeeded,
            failed=failed,
            stopped=stopped,
        )
        return BatchResult(
            set_id=set_id,
            requested=len(shot_ids),
            succeeded=succeeded,
            failed=failed,
            stopped=stopped,
            task=set_task_name(set_id),
            analysis_ids=sorted(ids),
        )

    # ── events ───────────────────────────────────────────────────────

    def _publish(self, event: str, analysis_id: int, shot_id: int, *, status: str) -> None:
        """Best-effort fan-out. Observability must never fail what it observes."""
        if self.bus is None:
            return
        self.bus.publish(
            SseEvent(
                event=event,
                data={"analysis_id": analysis_id, "shot_id": shot_id, "status": status},
            )
        )


def _require(row: AnalysisRow | None, analysis_id: int) -> AnalysisRow:
    if row is None:  # pragma: no cover - the insert above guarantees it
        raise RuntimeError(f"analysis {analysis_id} vanished between write and read")
    return row


def _usage(prompt_tokens: int | None, completion_tokens: int | None) -> dict[str, Any] | None:
    """The token counts, or ``None`` when the provider reported nothing.

    ``None`` rather than zeros, for the reason `Usage` itself keeps the
    distinction: a local model that reports nothing and a broken usage parser
    would otherwise both look free.
    """
    if prompt_tokens is None and completion_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": (prompt_tokens or 0) + (completion_tokens or 0),
    }


def _post_process(result: AnalysisResult, context: AnalysisContext) -> AnalysisResult:
    """Everything the schema cannot check, done in one place.

    Three checks, all of the same shape: a citation the shot was not given is
    dropped and logged. Dropping rather than failing the analysis is deliberate
    — a fabricated citation is a small flaw in an otherwise useful answer, and
    throwing the answer away over it would cost the user a call. It is logged
    because a model that invents citations often is a prompt problem worth
    seeing.

    * `rules_used` is filtered against the rules this shot was given;
    * `excerpts_used` against the excerpts it was given, by heading path. A
      heading path is a *citation*: a reader follows it to a passage, so one
      pointing at a passage the model never saw is worse than none at all;
    * a proposed insight's `evidence_shot_ids` against the shots that were
      actually in front of the model — this shot and its trajectory. An insight
      is only checkable if its evidence is real, and a model asked for shot ids
      will happily produce plausible ones.

    The proposal list is also *trimmed* to
    :data:`~gaggiclanker.analyzer.models.MAX_PROPOSED_INSIGHTS` here rather than
    bounded by the schema. A third proposal is not a broken answer: rejecting it
    would cost a corrective turn and a second paid call over a field the user
    was going to have to triage anyway.
    """
    allowed_shots = {context.shot.shot_id} | {entry.shot_id for entry in context.trajectory}
    if len(result.proposed_insights) > MAX_PROPOSED_INSIGHTS:
        log.info(
            "analysis_proposed_too_many_insights",
            shot_id=context.shot.shot_id,
            proposed=len(result.proposed_insights),
            kept=MAX_PROPOSED_INSIGHTS,
        )
    insights = [
        insight.model_copy(
            update={
                "evidence_shot_ids": sorted(
                    {shot_id for shot_id in insight.evidence_shot_ids if shot_id in allowed_shots}
                )
            }
        )
        for insight in result.proposed_insights[:MAX_PROPOSED_INSIGHTS]
    ]
    return result.model_copy(
        update={
            "rules_used": _cited(
                result.rules_used, context.rule_keys, context.shot.shot_id, "rules"
            ),
            "excerpts_used": _cited(
                result.excerpts_used, context.excerpt_paths, context.shot.shot_id, "excerpts"
            ),
            "proposed_insights": insights,
        }
    )


def _cited(claimed: list[str], allowed: frozenset[str], shot_id: int, kind: str) -> list[str]:
    """The citations that were really on offer, deduplicated, order preserved.

    A model that lists the same rule twice is not citing it twice, and the order
    it named them in is the order it used them in — which is worth keeping,
    because the panel renders that list as "what it leaned on".
    """
    dropped = [key for key in claimed if key not in allowed]
    if dropped:
        log.warning(
            "analysis_cited_unknown",
            kind=kind,
            shot_id=shot_id,
            dropped=sorted(dropped),
        )
    return list(dict.fromkeys(key for key in claimed if key in allowed))
