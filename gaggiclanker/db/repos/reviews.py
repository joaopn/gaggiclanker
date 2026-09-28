"""`shot_reviews`: one passive model reading of one shot per row.

A review row is opened before the provider is contacted and closed after it
answers, whichever way it answered. That ordering is what makes a crashed
process visible: :meth:`ShotReviewsRepository.reconcile_running` turns every
row still marked `running` at boot into `interrupted`, so "the container
restarted mid-review" is a state on the page rather than a spinner that never
stops.

A shot may carry several reviews: a person can press Review again. The newest
finished one (`status = 'ok'`) is the one the shot page shows first and the one
the shot information serves to the chat; the earlier ones stay, each with the
input it was given, so any reading can still be explained.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonList, JsonObject, dumps, utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.vocab import Balance, ReviewConfidence, ReviewStatus, TasteBody

__all__ = [
    "ReviewOutcome",
    "ReviewStart",
    "ShotReviewDetail",
    "ShotReviewRow",
    "ShotReviewsRepository",
]

#: How much of an error message a row keeps. A provider's message is a
#: sentence; anything longer is a stack of HTML from a proxy nobody wants on a
#: shot page.
ERROR_MAX = 1000


class ReviewStart(BaseModel):
    """What is known before the provider is asked anything."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    #: What the model is told, snapshotted. Not a reference to the shot and the
    #: rules: both can change, and an old review explained by today's inputs
    #: is worse than one not explained at all.
    input: dict[str, Any] = Field(default_factory=dict)


class ReviewOutcome(BaseModel):
    """How a run ended: the reading, or the reason there is none."""

    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus
    taste_balance: Balance | None = None
    taste_body: TasteBody | None = None
    taste_confidence: ReviewConfidence | None = None
    description: str | None = None
    summary: str | None = None
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


class ShotReviewRow(BaseModel):
    """One row of `shot_reviews`, as a page reads it: everything but the input."""

    model_config = ConfigDict(extra="forbid")

    id: int
    shot_id: int
    status: ReviewStatus = "running"
    error: str | None = None
    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    taste_balance: Balance | None = None
    taste_body: TasteBody | None = None
    taste_confidence: ReviewConfidence | None = None
    description: str | None = None
    summary: str | None = None
    rules_used: JsonList = Field(default=None, validation_alias="rules_used_json")
    excerpts_used: JsonList = Field(default=None, validation_alias="excerpts_used_json")
    usage: JsonObject = Field(default=None, validation_alias="usage_json")
    llm_call_id: str | None = None
    created_at: str
    finished_at: str | None = None


class ShotReviewDetail(ShotReviewRow):
    """One review with what it was told: the row plus its stored input."""

    input: JsonObject = Field(default=None, validation_alias="input_json")


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
        "taste_balance",
        "taste_body",
        "taste_confidence",
        "description",
        "summary",
        "rules_used_json",
        "excerpts_used_json",
        "usage_json",
        "llm_call_id",
        "created_at",
        "finished_at",
    ]
)


class ShotReviewsRepository(Repository):
    """Opens, closes and reads review runs. The only writer of `shot_reviews`."""

    async def start(self, spec: ReviewStart) -> int:
        """Open a `running` row. Returns its id."""
        cursor = await self.db.execute(
            """
            INSERT INTO shot_reviews
                (shot_id, status, provider, model, prompt_name, prompt_version,
                 input_json, created_at)
            VALUES (:shot_id, 'running', :provider, :model, :prompt_name, :prompt_version,
                    :input_json, :created_at)
            """,
            {
                "shot_id": spec.shot_id,
                "provider": spec.provider,
                "model": spec.model,
                "prompt_name": spec.prompt_name,
                "prompt_version": spec.prompt_version,
                "input_json": dumps(spec.input),
                "created_at": utc_now(),
            },
        )
        return int(cursor.lastrowid or 0)

    async def finish(self, review_id: int, outcome: ReviewOutcome) -> ShotReviewRow | None:
        """Close a run, however it went."""
        await self.db.execute(
            """
            UPDATE shot_reviews
               SET status = :status,
                   taste_balance = :taste_balance,
                   taste_body = :taste_body,
                   taste_confidence = :taste_confidence,
                   description = :description,
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
                "taste_balance": outcome.taste_balance,
                "taste_body": outcome.taste_body,
                "taste_confidence": outcome.taste_confidence,
                "description": outcome.description,
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
        return await self.get(review_id)

    async def get(self, review_id: int) -> ShotReviewRow | None:
        row = await self.db.fetch_one(
            f"SELECT {_ROW_COLUMNS} FROM shot_reviews WHERE id = ?",  # noqa: S608 - a literal column list
            (review_id,),
        )
        return self.to_model(ShotReviewRow, row)

    async def detail(self, review_id: int) -> ShotReviewDetail | None:
        """One review with its stored input: what the model was actually told."""
        row = await self.db.fetch_one("SELECT * FROM shot_reviews WHERE id = ?", (review_id,))
        return self.to_model(ShotReviewDetail, row)

    async def for_shot(self, shot_id: int) -> list[ShotReviewRow]:
        """Every review of one shot, newest first: the order the card reads."""
        rows = await self.db.fetch_all(
            f"SELECT {_ROW_COLUMNS} FROM shot_reviews WHERE shot_id = ? ORDER BY id DESC",  # noqa: S608 - a literal column list
            (shot_id,),
        )
        return self.to_models(ShotReviewRow, rows)

    async def latest_for_shot(self, shot_id: int) -> ShotReviewRow | None:
        """The newest review of a shot, whatever state it is in."""
        row = await self.db.fetch_one(
            f"SELECT {_ROW_COLUMNS} FROM shot_reviews WHERE shot_id = ? ORDER BY id DESC LIMIT 1",  # noqa: S608 - a literal column list
            (shot_id,),
        )
        return self.to_model(ShotReviewRow, row)

    async def latest_finished_for_shots(self, shot_ids: Sequence[int]) -> dict[int, ShotReviewRow]:
        """The newest `ok` review of each shot that has one, in one query.

        What the shot information reads: a failed or running review of a shot
        says nothing about the shot, so the reading served is the newest one
        that finished, however many attempts came after it.
        """
        ids = sorted(set(shot_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        rows = await self.db.fetch_all(
            f"""
            SELECT {_ROW_COLUMNS}
              FROM shot_reviews r
             WHERE r.status = 'ok'
               AND r.shot_id IN ({placeholders})
               AND r.id = (SELECT MAX(n.id) FROM shot_reviews n
                            WHERE n.shot_id = r.shot_id AND n.status = 'ok')
            """,  # noqa: S608 - placeholders are generated, ids are bound
            ids,
        )
        return {model.shot_id: model for model in self.to_models(ShotReviewRow, rows)}

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
