"""Running a reading: a row, a call, the numbers, an outcome, and nothing else.

The shape of :meth:`ReviewService.run_review` is the contract, and it is short
on purpose:

    build the input  ->  open a `running` row  ->  call_json  ->  evaluate the evidence
    ->  close the row with its claims

**A reading writes one review and its claims, about one shot.** It opens the row and closes
it, and touches no other table: no Set version, no insight, no draft, no conversation, no
signature. Every claim it writes is kept (`confirmed`); what it says reaches the chat unless a
person rejected the claim (:meth:`ReviewService.answer`, which also restores one).

**The numbers are the server's.** The model attaches metric-language expressions to its claims;
the evaluator works out every value (:mod:`gaggiclanker.review.evidence`), so no figure a person
reads on a claim was typed by the model.

**Only a person starts one.** The one caller is the route behind the shot
page's Review button. No chat tool, MCP tool, batch, timer, sync hook or boot
step holds this service or calls it, and the tests walk the registry and the
tool, chat, sync and device packages to keep it that way.

**It never raises for a provider failure.** A 429, a bad key, a timeout or an
answer that does not fit :class:`~gaggiclanker.review.models.ReviewResult`
all come back from the LLM layer as an ``Err``, and every one of them becomes a
stored `failed` review carrying the error. A caller gets a row back, never an
exception.

**The row is opened before the call.** A process that dies mid-call therefore
leaves a `running` row, which the next boot marks `interrupted`
(:meth:`ShotReviewsRepository.reconcile_running`). A partial unique index allows one
`running` row per shot, so a second process that opens one at the same moment is refused
by the database and gets the first one's row back.

**The work does not run inside the HTTP request.** :meth:`ReviewService.start`
opens the row and hands the call to the app's :class:`TaskRegistry`; the route
answers 202 with the running row. ``docker stop`` gives ten seconds, and the
task name ``review:<shot_id>`` is the idempotency rule: it is claimed
synchronously inside ``spawn``, so a second press while one runs gets the
running row back instead of a second call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.reviews import (
    AnswerResult,
    ReviewAlreadyRunning,
    ReviewOutcome,
    ReviewStart,
    ShotReviewRow,
    ShotReviewsRepository,
)
from gaggiclanker.infra.sse import SseEvent, SseEventBus
from gaggiclanker.infra.tasks import TaskSpawner
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import LlmMessage, LlmRequest, Ok
from gaggiclanker.review.context import ReviewInput, build_review_input
from gaggiclanker.review.evidence import claims_from_answer
from gaggiclanker.review.models import OutputModel, build_output_model
from gaggiclanker.shotinfo.evaluation import UnreadableShot, stored_data

__all__ = [
    "REVIEW_EVENTS",
    "REVIEW_PROMPT",
    "REVIEW_USER_PROMPT",
    "ReviewService",
    "review_task_name",
]

log = structlog.get_logger(__name__)

#: The two prompts one review renders. Split so the instructions and the layout
#: of the facts can be edited independently; see `prompts/review.yaml`.
REVIEW_PROMPT = "review"
REVIEW_USER_PROMPT = "review-user"

#: The SSE events a run publishes, on the LLM bus, each with `shot_id` and `review_id`. The shot
#: page and the shots table refresh from them, and the header's activity indicator is already
#: watching that stream. `review.answered` is a person's answer to a claim, so another tab
#: follows it.
REVIEW_EVENTS = ("review.started", "review.finished", "review.failed", "review.answered")

#: Linear backoff between this call's own retries. A real wait in production —
#: a provider that just 429'd wants a moment — so the suite sets it to zero
#: rather than sleeping out several seconds per failure path.
RETRY_DELAY_S = 0.5


@dataclass(frozen=True, slots=True)
class _Prepared:
    """Everything assembled before the provider is contacted."""

    row: ShotReviewRow
    review: ReviewInput
    output: OutputModel
    system: str
    user: str
    version: str
    model: str


def review_task_name(shot_id: int) -> str:
    """The registry name that makes one running review per shot an invariant."""
    return f"review:{shot_id}"


class ReviewService:
    """The one entry point. Held on ``app.state.reviews``; the route is its only caller."""

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
        self.reviews = ShotReviewsRepository(db)
        # Shots whose row is being opened right now, so a second request can
        # wait for the first one's row instead of reading a database the first
        # one has not written to yet. Held only across the opening — once the
        # row exists, the registry's name guard and the row itself are enough.
        self._opening: dict[int, asyncio.Future[ShotReviewRow]] = {}

    async def start(
        self,
        shot_id: int,
        *,
        tasks: TaskSpawner,
        model: str | None = None,
    ) -> tuple[ShotReviewRow, bool]:
        """Queue a review. Returns the row and whether this call started it.

        The row comes back before the provider is contacted, because the row is
        the handle: the page renders it as `running` and the SSE stream says
        when it moves.

        Raises ``LookupError`` for a shot that does not exist: the one failure
        that is the caller's mistake rather than the provider's, which the
        route answers with a 404 rather than a stored `failed` row.
        """
        # No await before the spawn below, so this check and the registry's own
        # name guard together leave no window: either somebody is mid-open and
        # we wait for their row, or the name is free and we claim it.
        pending = self._opening.get(shot_id)
        if pending is not None:
            return await asyncio.shield(pending), False

        opened: asyncio.Future[ShotReviewRow] = asyncio.get_running_loop().create_future()
        self._opening[shot_id] = opened
        try:
            tasks.spawn(review_task_name(shot_id), self._background(shot_id, model, opened))
        except RuntimeError:
            # The name is held by a task that has already opened its row — it
            # removed itself from `_opening` when it did.
            del self._opening[shot_id]
            opened.cancel()
            running = await self.reviews.latest_for_shot(shot_id)
            if running is not None and running.status == "running":
                return running, False
            raise
        row = await opened
        return row, row.status == "running"

    async def _background(
        self, shot_id: int, model: str | None, opened: asyncio.Future[ShotReviewRow]
    ) -> None:
        """The registered task: open the row, hand it back, then do the work.

        The future is resolved before the provider is touched and never after,
        so a caller blocked on it waits for a database write rather than for a
        model.
        """
        try:
            prepared = await self._prepare(shot_id, model=model)
        except ReviewAlreadyRunning:
            # Another process opened one in the instant between our check and our insert: the
            # unique index refused ours, and the page gets theirs. Nothing is started here.
            running = await self.reviews.latest_for_shot(shot_id)
            if running is None:  # pragma: no cover - the index refused because a row exists
                raise
            opened.set_result(running)
            return
        except BaseException as exc:
            if not opened.done():
                opened.set_exception(exc)
            raise
        finally:
            self._opening.pop(shot_id, None)
        opened.set_result(prepared.row)
        await self._complete(prepared)

    async def run_review(self, shot_id: int, *, model: str | None = None) -> ShotReviewRow:
        """Review one shot and wait for it. Returns the row, whatever happened.

        The direct form, for tests and for anything that genuinely wants to
        block. The route calls :meth:`start` instead.
        """
        return await self._complete(await self._prepare(shot_id, model=model))

    async def _prepare(self, shot_id: int, *, model: str | None = None) -> _Prepared:
        """Everything before the provider: the input, the output model, the prompts, the row."""
        config = await self.llm.config()
        resolved = (model or "").strip() or config.resolve_model("review")

        # The excerpt budget is a setting, read here rather than inside
        # `build_review_input` so that assembling the input stays a pure
        # function of the database it was handed.
        budget = int(await self.llm.settings.get("knowledgeChunkTokenBudget"))
        review = await build_review_input(self.db, shot_id, chunk_token_budget=budget)
        # What this shot's answer may say: its own phases, this reading's expectation ids and
        # whether there is a prediction to answer.
        output = build_output_model(
            phases=review.phases,
            free_text_ids=[item.id for item in review.expectations],
            has_prediction=bool(review.prediction),
        )
        system, user, version = await self._render(review)

        review_id = await self.reviews.start(
            ReviewStart(
                shot_id=shot_id,
                provider=config.provider,
                model=resolved,
                prompt_name=REVIEW_PROMPT,
                prompt_version=version,
                prediction_given=review.prediction,
                input=review.model_dump(mode="json"),
            )
        )
        self._publish("review.started", review_id, shot_id, status="running")
        row = _require(await self.reviews.get(review_id), review_id)
        return _Prepared(
            row=row,
            review=review,
            output=output,
            system=system,
            user=user,
            version=version,
            model=resolved,
        )

    async def _complete(self, prepared: _Prepared) -> ShotReviewRow:
        """The provider call and the bookkeeping. Never raises for a failure.

        An *unexpected* exception — a bug in here, not a refusal out there — is
        also turned into a stored `failed` row before it is re-raised, so a row
        never stays `running` for ever because something threw.
        """
        review_id = prepared.row.id
        shot_id = prepared.row.shot_id

        try:
            result = await self.llm.call_json(
                LlmRequest(
                    messages=[
                        LlmMessage(role="system", content=prepared.system),
                        LlmMessage(role="user", content=prepared.user),
                    ],
                    output_model=prepared.output,
                    model=prepared.model,
                    purpose="review",
                    label="review shot",
                    subject=f"shot {shot_id}",
                    prompt_name=REVIEW_PROMPT,
                    prompt_version=prepared.version,
                    retry_delay_s=self.retry_delay_s,
                )
            )
        except asyncio.CancelledError:
            # A cancelled call is not a provider failure, so it is not a
            # `failed` review either: the row stays `running` and boot
            # reconciliation marks it `interrupted`, which is what happened.
            self._publish("review.failed", review_id, shot_id, status="running")
            raise
        except Exception as exc:
            await self.reviews.finish(
                review_id,
                ReviewOutcome(status="failed", error=f"unknown: {type(exc).__name__}: {exc}"),
            )
            log.exception("review_crashed", review_id=review_id, shot_id=shot_id)
            self._publish("review.failed", review_id, shot_id, status="failed")
            raise

        if not isinstance(result, Ok):
            row = await self.reviews.finish(
                review_id,
                ReviewOutcome(
                    status="failed",
                    error=f"{result.code}: {result.message}",
                    provider=result.provider,
                    model=result.model,
                    llm_call_id=result.call_id or None,
                    usage=_usage(result.usage.prompt_tokens, result.usage.completion_tokens),
                ),
            )
            log.info(
                "review_failed",
                review_id=review_id,
                shot_id=shot_id,
                code=result.code,
                provider=result.provider,
            )
            self._publish("review.failed", review_id, shot_id, status="failed")
            return _require(row, review_id)

        answer: Any = result.data
        try:
            data = await stored_data(self.db, shot_id)
        except UnreadableShot as exc:
            data = None
            problem = f"the shot cannot be read: {exc}"
        else:
            problem = "the shot has gone"
        if data is None:
            row = await self.reviews.finish(
                review_id,
                ReviewOutcome(
                    status="failed",
                    error=f"unknown: {problem}",
                    provider=result.provider,
                    model=result.model,
                    llm_call_id=result.call_id or None,
                    usage=_usage(result.usage.prompt_tokens, result.usage.completion_tokens),
                ),
            )
            log.warning("review_unreadable_shot", review_id=review_id, shot_id=shot_id)
            self._publish("review.failed", review_id, shot_id, status="failed")
            return _require(row, review_id)

        try:
            claims = claims_from_answer(answer, data, prepared.review.expectations)
            row = await self.reviews.finish(
                review_id,
                ReviewOutcome(
                    status="ok",
                    summary=answer.summary.strip(),
                    claims=claims,
                    rules_used=_cited(
                        answer.rules_used, prepared.review.rule_keys, shot_id, "rules"
                    ),
                    excerpts_used=_cited(
                        answer.excerpts_used, prepared.review.excerpt_paths, shot_id, "excerpts"
                    ),
                    usage=_usage(result.usage.prompt_tokens, result.usage.completion_tokens),
                    provider=result.provider,
                    model=result.model,
                    llm_call_id=result.call_id or None,
                ),
            )
        except Exception as exc:
            await self.reviews.finish(
                review_id,
                ReviewOutcome(status="failed", error=f"unknown: {type(exc).__name__}: {exc}"),
            )
            log.exception("review_crashed", review_id=review_id, shot_id=shot_id)
            self._publish("review.failed", review_id, shot_id, status="failed")
            raise
        log.info(
            "review_finished",
            review_id=review_id,
            shot_id=shot_id,
            model=result.model,
            claims=len(claims),
            unsupported=sum(1 for claim in claims if not claim.supported),
        )
        self._publish("review.finished", review_id, shot_id, status="ok")
        return _require(row, review_id)

    # ── a person's answers ──────────────────────────────────────────

    async def answer(self, review_id: int, claim_id: int, *, keep: bool) -> AnswerResult:
        """A person rejects one claim of the review that answers for its shot, or restores it."""
        result = await self.reviews.answer(review_id, claim_id, keep=keep)
        self._answered(result)
        return result

    def _answered(self, result: AnswerResult) -> None:
        if result.review is not None:
            self._publish("review.answered", result.review.id, result.review.shot_id, status="ok")

    async def _render(self, review: ReviewInput) -> tuple[str, str, str]:
        """The two prompts, rendered, plus the version string for the ledger.

        The version is both prompts' `updated_at` joined: a review produced by
        an edited layout and one produced by edited instructions are different
        reviews, and the row has one column for it.
        """
        variables = review.render()
        system = await self.prompts.load(REVIEW_PROMPT)
        user = await self.prompts.load(REVIEW_USER_PROMPT, variables)
        return system.system, user.user, f"{system.version}+{user.version}"

    def _publish(self, event: str, review_id: int, shot_id: int, *, status: str) -> None:
        """Best-effort fan-out. Observability must never fail what it observes."""
        if self.bus is None:
            return
        self.bus.publish(
            SseEvent(
                event=event,
                data={"review_id": review_id, "shot_id": shot_id, "status": status},
            )
        )


def _require(row: ShotReviewRow | None, review_id: int) -> ShotReviewRow:
    if row is None:  # pragma: no cover - the insert above guarantees it
        raise RuntimeError(f"review {review_id} vanished between write and read")
    return row


def _usage(prompt_tokens: int | None, completion_tokens: int | None) -> dict[str, Any] | None:
    """The token counts, or ``None`` when the provider reported nothing."""
    if prompt_tokens is None and completion_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": (prompt_tokens or 0) + (completion_tokens or 0),
    }


def _cited(claimed: list[str], allowed: frozenset[str], shot_id: int, kind: str) -> list[str]:
    """The citations that were really on offer, deduplicated, order preserved.

    A citation the shot was not given is dropped and logged rather than
    failing the review: it is a small flaw in an otherwise useful answer, and a
    model that invents citations often is a prompt problem worth seeing.
    """
    dropped = [key for key in claimed if key not in allowed]
    if dropped:
        log.warning("review_cited_unknown", kind=kind, shot_id=shot_id, dropped=sorted(dropped))
    return list(dict.fromkeys(key for key in claimed if key in allowed))
