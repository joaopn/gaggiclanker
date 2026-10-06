"""`shot_reviews` and `review_claims`: one reading of one shot per row, and what it said.

A review row is opened before the provider is contacted and closed after it
answers, whichever way it answered. That ordering is what makes a crashed
process visible: :meth:`ShotReviewsRepository.reconcile_running` turns every
row still marked `running` at boot into `interrupted`, so "the container
restarted mid-reading" is a state on the page rather than a spinner that never
stops. A partial unique index allows one `running` row per shot, and the row is
opened in a transaction of its own, so two processes (the app and a stdio
server share the file) cannot both open one.

A shot may carry several reviews: a person can read it again. The newest finished
one (`status = 'ok'`) is the one that answers for the shot, everywhere: the shot
page shows it first, only its claims can still be answered, and the chat is served
only what a person confirmed in it. The earlier ones stay, each with the input it
was given, so any reading can still be explained.

**What a reading says is a list of claims, written with the finished review in one
transaction** (:meth:`ShotReviewsRepository.finish`), so a reading is never half there.
Every claim starts `proposed`; only a person's answer (:meth:`answer`,
:meth:`confirm_all`) moves it, inside one transaction that first checks the review
is still the one that answers for its shot.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gaggiclanker.db.repos.base import JsonList, JsonObject, dumps, utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.vocab import (
    PredictionStance,
    ReviewClaimKind,
    ReviewClaimStatus,
    ReviewStatus,
)

__all__ = [
    "AnswerResult",
    "ClaimWrite",
    "EvidenceOut",
    "EvidenceStored",
    "ReadingRecord",
    "ReviewAlreadyRunning",
    "ReviewBrief",
    "ReviewClaimRow",
    "ReviewOutcome",
    "ReviewStart",
    "ShotReviewDetail",
    "ShotReviewRow",
    "ShotReviewsRepository",
]

log = structlog.get_logger(__name__)

#: How much of an error message a row keeps. A provider's message is a
#: sentence; anything longer is a stack of HTML from a proxy nobody wants on a
#: shot page.
ERROR_MAX = 1000

#: How long the line a person may give with an answer is.
REASON_MAX = 300

#: Shot ids per query when a reading is looked up for many shots: far below SQLite's limit on
#: bound variables, so the Review sort over a whole archive never meets it.
_CHUNK = 500


class ReviewAlreadyRunning(Exception):
    """A second `running` row was refused by the unique index: one is already open."""


# ── evidence ────────────────────────────────────────────────────────


class EvidenceStored(BaseModel):
    """One expression of a claim's evidence, with what the server evaluated it to.

    Every number here came from the evaluator, never from the model's reply: the model
    chose the expression (and its comparison), the server worked out the rest.
    """

    model_config = ConfigDict(extra="forbid")

    #: The expression as the model wrote it, canonical, so the page can show what was asked.
    expression: dict[str, Any]
    #: The language's one-line rendering of it.
    sentence: str
    value: float | None = None
    unit: str = ""
    #: ``measured``, ``estimated`` or ``commanded``.
    kind: str
    #: The reason code when the value is absent (``phase_not_reached``…), and the words for it.
    absent: str | None = None
    why: str | None = None
    #: Whether the expression's own comparison held; ``None`` with no comparison or no value.
    held: bool | None = None
    #: The limit as a person reads it ("at most 15 % of target", "over 3 g/s"), worked out the way
    #: a check's is; empty when the expression has no comparison.
    limit_text: str = ""


class EvidenceOut(BaseModel):
    """What the page reads of one piece of evidence."""

    model_config = ConfigDict(extra="forbid")

    sentence: str
    value: float | None
    unit: str
    kind: str
    #: Why there is no value, in words (the same as a check's ``absent``); ``None`` when there is.
    absent: str | None
    held: bool | None
    #: The limit the expression was held against, as a check's ``limit_text`` words it ("at most
    #: 15 % of target"); empty with no comparison. A share is a percentage here, as in ``value``.
    limit_text: str = ""


# ── writing ─────────────────────────────────────────────────────────


class ClaimWrite(BaseModel):
    """One statement of a finished reading, ready to store (always as ``proposed``)."""

    model_config = ConfigDict(extra="forbid")

    kind: ReviewClaimKind
    window: dict[str, Any] = Field(default_factory=dict)
    window_text: str = ""
    phase: str | None = None
    start_s: float | None = None
    end_s: float | None = None
    fault: str | None = None
    text: str = Field(min_length=1)
    evidence: list[EvidenceStored] = Field(default_factory=list)
    supported: bool = True
    expectation_id: int | None = None
    held: bool | None = None
    stance: PredictionStance | None = None

    @model_validator(mode="after")
    def _shape(self) -> ClaimWrite:
        if (self.kind == "free_text") != (self.expectation_id is not None):
            raise ValueError("exactly a free-text result names an expectation")
        if (self.kind == "free_text") != (self.held is not None):
            raise ValueError("exactly a free-text result says whether it held")
        if (self.kind == "prediction") != (self.stance is not None):
            raise ValueError("exactly a prediction has a stance")
        return self


class ReviewStart(BaseModel):
    """What is known before the provider is asked anything."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    #: The Set version's prediction as the model is shown it; empty when there is none.
    prediction_given: str = ""
    #: What the model is told, snapshotted. Not a reference to the shot and the
    #: rules: both can change, and an old review explained by today's inputs
    #: is worse than one not explained at all.
    input: dict[str, Any] = Field(default_factory=dict)


class ReviewOutcome(BaseModel):
    """How a run ended: the reading, or the reason there is none."""

    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus
    summary: str | None = None
    claims: list[ClaimWrite] = Field(default_factory=list)
    rules_used: list[str] = Field(default_factory=list)
    excerpts_used: list[str] = Field(default_factory=list)
    usage: dict[str, Any] | None = None
    error: str | None = None
    llm_call_id: str | None = None
    #: Written again because the request may have left them to the configured
    #: defaults, and the row should record what actually answered. Empty keeps
    #: what the opening wrote.
    provider: str = ""
    model: str = ""

    @model_validator(mode="after")
    def _only_a_finished_reading_has_claims(self) -> ReviewOutcome:
        if self.claims and self.status != "ok":
            raise ValueError("only a finished reading has claims")
        return self


# ── reading ─────────────────────────────────────────────────────────


class ReviewClaimRow(BaseModel):
    """One claim as the page reads it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: int
    review_id: int
    position: int
    kind: ReviewClaimKind
    #: The phase when the window is one, else ``None``.
    phase: str | None = None
    #: The window as a person reads it ("the ramp", "the span from … to …").
    window_text: str = ""
    #: The seconds the server resolved the window to; ``None`` when the shot never reached it.
    start_s: float | None = None
    end_s: float | None = None
    fault: str | None = None
    text: str
    evidence: list[EvidenceOut] = Field(default_factory=list)
    #: ``False`` marks "the numbers don't bear this out"; the claim is kept all the same.
    supported: bool = True
    expectation_id: int | None = None
    held: bool | None = None
    stance: PredictionStance | None = None
    status: ReviewClaimStatus = "proposed"
    reason: str = ""
    answered_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_row(cls, data: Any) -> Any:
        """Decode a stored row: the evidence is a JSON column, the flags are integers."""
        if not isinstance(data, dict) or "evidence_json" not in data:
            return data
        payload = dict(data)
        raw = payload.pop("evidence_json")
        stored = [EvidenceStored.model_validate(item) for item in json.loads(raw or "[]")]
        payload["evidence"] = [
            {
                "sentence": item.sentence,
                # A share is a percentage on the page, as a check's value is.
                "value": round(item.value * 100, 1)
                if item.unit == "share" and item.value is not None
                else item.value,
                "unit": "%" if item.unit == "share" else item.unit,
                "kind": item.kind,
                "absent": item.why or item.absent,
                "held": item.held,
                "limit_text": item.limit_text,
            }
            for item in stored
        ]
        payload["supported"] = bool(payload.get("supported", 1))
        if payload.get("held") is not None:
            payload["held"] = bool(payload["held"])
        payload.pop("window_json", None)
        return payload


class ShotReviewRow(BaseModel):
    """One review as a page reads it: everything but the input, with its claims."""

    model_config = ConfigDict(extra="forbid")

    id: int
    shot_id: int
    status: ReviewStatus = "running"
    error: str | None = None
    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    #: One sentence for the person. Never served to the chat.
    summary: str | None = None
    #: The Set version's prediction as the model was shown it; empty when there was none.
    prediction_given: str = ""
    rules_used: JsonList = Field(default=None, validation_alias="rules_used_json")
    excerpts_used: JsonList = Field(default=None, validation_alias="excerpts_used_json")
    usage: JsonObject = Field(default=None, validation_alias="usage_json")
    llm_call_id: str | None = None
    created_at: str
    finished_at: str | None = None
    claims: list[ReviewClaimRow] = Field(default_factory=list)


class ShotReviewDetail(ShotReviewRow):
    """One review with what it was told: the row plus its stored input."""

    input: JsonObject = Field(default=None, validation_alias="input_json")


class ReviewBrief(BaseModel):
    """What the verdict and the list need of a review: no claims, no input, no citations."""

    model_config = ConfigDict(extra="forbid")

    id: int
    shot_id: int
    status: ReviewStatus
    error: str | None = None
    summary: str | None = None
    model: str = ""
    created_at: str
    finished_at: str | None = None


@dataclass(frozen=True, slots=True)
class ReadingRecord:
    """What a shot's reading is, as far as the verdict is concerned.

    ``latest`` is the newest review of any status, ``finished`` the newest `ok` one (the same
    row when the newest is finished), and ``claims`` are the finished one's claims, in order.
    """

    latest: ReviewBrief | None = None
    finished: ReviewBrief | None = None
    claims: tuple[ReviewClaimRow, ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """The outcome of answering a claim, or of confirming all of a reading's."""

    review: ShotReviewRow | None = None
    #: Why nothing was written: ``no_review``, ``no_claim`` (the claim is not this review's) or
    #: ``superseded`` (a newer finished reading answers for the shot, or this one is not finished).
    refused: Literal["no_review", "no_claim", "superseded"] | None = None
    #: How many claims were moved (``confirm_all``); 1 for one answered claim.
    changed: int = 0


#: The columns a :class:`ShotReviewRow` is read from; the input is left out of
#: every list, because it is the whole rendered shot and a page of reviews
#: would otherwise carry a curve per row.
_ROW_COLUMNS = ", ".join(
    [
        "id",
        "shot_id",
        "status",
        "error",
        "provider",
        "model",
        "prompt_name",
        "prompt_version",
        "summary",
        "prediction_given",
        "rules_used_json",
        "excerpts_used_json",
        "usage_json",
        "llm_call_id",
        "created_at",
        "finished_at",
    ]
)

_BRIEF_COLUMNS = (
    "r.id, r.shot_id, r.status, r.error, r.summary, r.model, r.created_at, r.finished_at"
)

_CLAIM_COLUMNS = (
    "id, review_id, position, kind, window_text, phase, start_s, end_s, fault, text, "
    "evidence_json, supported, expectation_id, held, stance, status, reason, answered_at"
)


class ShotReviewsRepository(Repository):
    """Opens, closes, answers and reads review runs. The only writer of `shot_reviews`."""

    def _transaction(self) -> AbstractAsyncContextManager[Any]:
        """The write's own transaction, or none when the caller already holds one."""
        return nullcontext() if self.db.in_transaction else self.db.transaction()

    # ── opening and closing ─────────────────────────────────────────

    async def start(self, spec: ReviewStart) -> int:
        """Open a `running` row. Returns its id.

        Raises :class:`ReviewAlreadyRunning` when the shot already has one: the unique index
        is the real guard, since the registry's task name only covers this process.
        """
        try:
            async with self._transaction():
                cursor = await self.db.execute(
                    """
                    INSERT INTO shot_reviews
                        (shot_id, status, provider, model, prompt_name, prompt_version,
                         prediction_given, input_json, created_at)
                    VALUES (:shot_id, 'running', :provider, :model, :prompt_name, :prompt_version,
                            :prediction_given, :input_json, :created_at)
                    """,
                    {
                        "shot_id": spec.shot_id,
                        "provider": spec.provider,
                        "model": spec.model,
                        "prompt_name": spec.prompt_name,
                        "prompt_version": spec.prompt_version,
                        "prediction_given": spec.prediction_given,
                        "input_json": dumps(spec.input),
                        "created_at": utc_now(),
                    },
                )
        except sqlite3.IntegrityError as exc:
            if "shot_reviews.shot_id" in str(exc) or "UNIQUE" in str(exc):
                raise ReviewAlreadyRunning(spec.shot_id) from exc
            raise
        return int(cursor.lastrowid or 0)

    async def finish(self, review_id: int, outcome: ReviewOutcome) -> ShotReviewRow | None:
        """Close a run, however it went, and write its claims in the same transaction."""
        async with self._transaction():
            await self.db.execute(
                """
                UPDATE shot_reviews
                   SET status = :status,
                       summary = :summary,
                       rules_used_json = :rules_used_json,
                       excerpts_used_json = :excerpts_used_json,
                       usage_json = :usage_json,
                       error = :error,
                       llm_call_id = COALESCE(:llm_call_id, llm_call_id),
                       provider = CASE WHEN :provider = '' THEN provider ELSE :provider END,
                       model = CASE WHEN :model = '' THEN model ELSE :model END,
                       finished_at = :finished_at
                 WHERE id = :id
                """,
                {
                    "id": review_id,
                    "status": outcome.status,
                    "summary": outcome.summary,
                    "rules_used_json": dumps(outcome.rules_used),
                    "excerpts_used_json": dumps(outcome.excerpts_used),
                    "usage_json": None if outcome.usage is None else dumps(outcome.usage),
                    "error": None if outcome.error is None else outcome.error[:ERROR_MAX],
                    "llm_call_id": outcome.llm_call_id,
                    "provider": outcome.provider,
                    "model": outcome.model,
                    "finished_at": utc_now(),
                },
            )
            for position, claim in enumerate(outcome.claims):
                await self.db.execute(
                    """
                    INSERT INTO review_claims
                        (review_id, position, kind, window_json, window_text, phase, start_s,
                         end_s, fault, text, evidence_json, supported, expectation_id, held,
                         stance)
                    VALUES (:review_id, :position, :kind, :window_json, :window_text, :phase,
                            :start_s, :end_s, :fault, :text, :evidence_json, :supported,
                            :expectation_id, :held, :stance)
                    """,
                    {
                        "review_id": review_id,
                        "position": position,
                        "kind": claim.kind,
                        "window_json": dumps(claim.window),
                        "window_text": claim.window_text,
                        "phase": claim.phase,
                        "start_s": claim.start_s,
                        "end_s": claim.end_s,
                        "fault": claim.fault,
                        "text": claim.text,
                        "evidence_json": dumps([e.model_dump(mode="json") for e in claim.evidence]),
                        "supported": int(claim.supported),
                        "expectation_id": claim.expectation_id,
                        "held": None if claim.held is None else int(claim.held),
                        "stance": claim.stance,
                    },
                )
        return await self.get(review_id)

    # ── reading one ─────────────────────────────────────────────────

    async def _claims(self, review_ids: Sequence[int]) -> dict[int, list[ReviewClaimRow]]:
        ids = sorted(set(review_ids))
        found: dict[int, list[ReviewClaimRow]] = {}
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            rows = await self.db.fetch_all(
                f"SELECT {_CLAIM_COLUMNS} FROM review_claims "  # noqa: S608 - a literal column list
                f"WHERE review_id IN ({', '.join('?' * len(chunk))}) "
                "ORDER BY review_id, position",
                chunk,
            )
            for claim in self.to_models(ReviewClaimRow, rows):
                found.setdefault(claim.review_id, []).append(claim)
        return found

    async def _with_claims(self, rows: Sequence[ShotReviewRow]) -> list[ShotReviewRow]:
        claims = await self._claims([row.id for row in rows])
        return [row.model_copy(update={"claims": claims.get(row.id, [])}) for row in rows]

    async def get(self, review_id: int) -> ShotReviewRow | None:
        row = await self.db.fetch_one(
            f"SELECT {_ROW_COLUMNS} FROM shot_reviews WHERE id = ?",  # noqa: S608 - a literal column list
            (review_id,),
        )
        model = self.to_model(ShotReviewRow, row)
        return None if model is None else (await self._with_claims([model]))[0]

    async def detail(self, review_id: int) -> ShotReviewDetail | None:
        """One review with its stored input: what the model was actually told."""
        row = await self.db.fetch_one("SELECT * FROM shot_reviews WHERE id = ?", (review_id,))
        model = self.to_model(ShotReviewDetail, row)
        if model is None:
            return None
        return model.model_copy(
            update={"claims": (await self._claims([review_id])).get(review_id, [])}
        )

    async def for_shot(self, shot_id: int) -> list[ShotReviewRow]:
        """Every review of one shot, newest first: the order the card reads."""
        rows = await self.db.fetch_all(
            f"SELECT {_ROW_COLUMNS} FROM shot_reviews WHERE shot_id = ? ORDER BY id DESC",  # noqa: S608 - a literal column list
            (shot_id,),
        )
        return await self._with_claims(self.to_models(ShotReviewRow, rows))

    async def latest_for_shot(self, shot_id: int) -> ShotReviewRow | None:
        """The newest review of a shot, whatever state it is in."""
        row = await self.db.fetch_one(
            f"SELECT {_ROW_COLUMNS} FROM shot_reviews WHERE shot_id = ? ORDER BY id DESC LIMIT 1",  # noqa: S608 - a literal column list
            (shot_id,),
        )
        model = self.to_model(ShotReviewRow, row)
        return None if model is None else (await self._with_claims([model]))[0]

    async def finished_for_shot(self, shot_id: int) -> ShotReviewRow | None:
        """The newest finished review of a shot: the one that answers for it."""
        row = await self.db.fetch_one(
            f"SELECT {_ROW_COLUMNS} FROM shot_reviews "  # noqa: S608 - a literal column list
            "WHERE shot_id = ? AND status = 'ok' ORDER BY id DESC LIMIT 1",
            (shot_id,),
        )
        model = self.to_model(ShotReviewRow, row)
        return None if model is None else (await self._with_claims([model]))[0]

    async def readings_for_shots(self, shot_ids: Sequence[int]) -> dict[int, ReadingRecord]:
        """Each shot's newest review, its newest finished one and that one's claims.

        Two reads per chunk of shots whatever the number of shots (the shots list and the
        Review sort call this for a whole page, or the whole archive). A shot that was never
        read has no entry.
        """
        ids = sorted(set(shot_ids))
        latest: dict[int, ReviewBrief] = {}
        finished: dict[int, ReviewBrief] = {}
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            marks = ", ".join("?" * len(chunk))
            for status_filter, into in (("", latest), (" AND n.status = 'ok'", finished)):
                rows = await self.db.fetch_all(
                    f"SELECT {_BRIEF_COLUMNS} FROM shot_reviews r "  # noqa: S608 - literals and generated placeholders
                    f"WHERE r.shot_id IN ({marks}) "
                    f"AND r.id = (SELECT MAX(n.id) FROM shot_reviews n "
                    f"WHERE n.shot_id = r.shot_id{status_filter})",
                    chunk,
                )
                for brief in self.to_models(ReviewBrief, rows):
                    into[brief.shot_id] = brief
        claims = await self._claims([brief.id for brief in finished.values()])
        return {
            shot_id: ReadingRecord(
                latest=latest.get(shot_id),
                finished=finished.get(shot_id),
                claims=tuple(claims.get(finished[shot_id].id, ())) if shot_id in finished else (),
            )
            for shot_id in latest
        }

    # ── answering ───────────────────────────────────────────────────

    async def _answering(self, review_id: int) -> tuple[str | None, int | None]:
        """(``refused`` or ``None``, the shot) for a review a person is about to answer."""
        row = await self.db.fetch_one(
            "SELECT shot_id, status FROM shot_reviews WHERE id = ?", (review_id,)
        )
        if row is None:
            return "no_review", None
        shot_id = int(row["shot_id"])
        newest = await self.db.fetch_value(
            "SELECT MAX(id) FROM shot_reviews WHERE shot_id = ? AND status = 'ok'", (shot_id,)
        )
        if row["status"] != "ok" or newest != review_id:
            return "superseded", shot_id
        return None, shot_id

    async def answer(
        self, review_id: int, claim_id: int, *, confirm: bool, reason: str = ""
    ) -> AnswerResult:
        """Confirm or reject one claim of the review that answers for its shot.

        The check and the write share one transaction: a reading set aside by a newer one
        between the two is refused, never answered. A person may change an answer; the last
        one wins and each is logged. A claim answered again with the same status only moves
        its reason and time.
        """
        async with self._transaction():
            refused, _shot = await self._answering(review_id)
            if refused is not None:
                return AnswerResult(refused=refused)  # type: ignore[arg-type]
            cursor = await self.db.execute(
                "UPDATE review_claims SET status = ?, reason = ?, answered_at = ? "
                "WHERE id = ? AND review_id = ?",
                (
                    "confirmed" if confirm else "rejected",
                    reason.strip()[:REASON_MAX],
                    utc_now(),
                    claim_id,
                    review_id,
                ),
            )
            if cursor.rowcount == 0:
                return AnswerResult(refused="no_claim")
            log.info(
                "review_claim_answered",
                review_id=review_id,
                claim_id=claim_id,
                status="confirmed" if confirm else "rejected",
            )
        return AnswerResult(review=await self.get(review_id), changed=1)

    async def confirm_all(
        self, review_id: int, *, except_kinds: Sequence[str] = ()
    ) -> AnswerResult:
        """Confirm every claim of the reading still waiting, in one transaction.

        Claims a person already answered (a rejection included) are left as they are, and so are
        claims of ``except_kinds``: they stay `proposed`. The page holds the prediction's stance
        back until the shot has a decision, so a person pressing Confirm all has not seen it and
        must not be taken to have confirmed it.
        """
        kinds = sorted(set(except_kinds))
        async with self._transaction():
            refused, _shot = await self._answering(review_id)
            if refused is not None:
                return AnswerResult(refused=refused)  # type: ignore[arg-type]
            cursor = await self.db.execute(
                "UPDATE review_claims SET status = 'confirmed', answered_at = ? "  # noqa: S608
                "WHERE review_id = ? AND status = 'proposed' "
                f"AND kind NOT IN ({', '.join('?' * len(kinds))})",
                (utc_now(), review_id, *kinds),
            )
            changed = cursor.rowcount
            log.info(
                "review_claims_confirmed_all",
                review_id=review_id,
                changed=changed,
                except_kinds=kinds,
            )
        return AnswerResult(review=await self.get(review_id), changed=changed)

    # ── boot ────────────────────────────────────────────────────────

    async def reconcile_running(self) -> int:
        """Mark every `running` row `interrupted`. Runs once, at boot.

        A row is only `running` while a process is holding it, and no process
        survives a boot. Anything still in that state was cut off mid-call,
        and saying so is the point: the alternative is a shot whose card spins
        for ever with no row anybody can act on.
        """
        cursor = await self.db.execute(
            """
            UPDATE shot_reviews
               SET status = 'interrupted',
                   error = COALESCE(error, 'the process stopped before this review finished'),
                   finished_at = ?
             WHERE status = 'running'
            """,
            (utc_now(),),
        )
        return cursor.rowcount
