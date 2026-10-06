"""`/api/reviews`: one review read back with what it was told, and a person's answers to its claims.

The routes that start and list reviews live on the shot they are about
(`POST /api/shots/{id}/reviews`, `GET /api/shots/{id}/reviews`); this router
holds what is not about a shot: a single review, with the input the model was given, so any
reading can be explained, and the two ways a person answers its claims, one at a time or all
at once. Only a person answers: no tool, chat run or sync step reaches these.

Only the newest finished reading of a shot can be answered. A reading set aside by a newer
one answers 409, and so does one that never finished.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.api.deps import ReviewServiceDep, ReviewsRepoDep
from gaggiclanker.db.repos.reviews import AnswerResult, ShotReviewDetail, ShotReviewRow
from gaggiclanker.domain.vocab import REVIEW_CLAIM_KINDS
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable

__all__ = ["router"]

router = APIRouter(prefix="/reviews", tags=["review"])


@router.get(
    "/{review_id}",
    response_model=ApiResponse[ShotReviewDetail],
    summary="One review, with the input it was given",
)
async def get_review(review_id: int, reviews: ReviewsRepoDep) -> JSONResponse:
    row = await reviews.detail(review_id)
    if row is None:
        raise NotFound(f"No review {review_id}")
    return envelope_response(row.model_dump(mode="json"))


class ClaimAnswer(BaseModel):
    """`PATCH /api/reviews/{id}/claims/{claim_id}`: confirm or reject one claim."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "rejected"]
    #: One line, whichever the answer; empty when there is none to give.
    reason: str = Field(default="", max_length=300)


class ConfirmAll(BaseModel):
    """`POST /api/reviews/{id}/claims/confirm-all`: optionally keep some kinds of claim waiting."""

    model_config = ConfigDict(extra="forbid")

    #: Claims of these kinds (``claim``, ``free_text``, ``prediction``) stay `proposed`.
    except_kinds: list[str] = Field(default_factory=list, max_length=10)


def _answered(result: AnswerResult, review_id: int) -> JSONResponse:
    """The updated review, or the refusal as the error it is."""
    if result.review is not None:
        return envelope_response(result.review.model_dump(mode="json"))
    if result.refused == "superseded":
        raise Conflict(
            f"Review {review_id} no longer answers for its shot",
            code="REVIEW_SUPERSEDED",
            details={
                "field": "review_id",
                "message": "Only the newest finished reading of a shot can be answered.",
            },
        )
    if result.refused == "no_claim":
        raise NotFound(f"No such claim in review {review_id}")
    raise NotFound(f"No review {review_id}")


@router.patch(
    "/{review_id}/claims/{claim_id}",
    response_model=ApiResponse[ShotReviewRow],
    summary="Confirm or reject one claim of a reading",
)
async def answer_claim(
    review_id: int, claim_id: int, body: ClaimAnswer, reviewer: ReviewServiceDep
) -> JSONResponse:
    """A person's answer, which they may change: the last one wins.

    Checked and written in one transaction, so a reading a newer one has set aside is refused
    rather than answered, and two answers arriving together leave one consistent state.
    """
    result = await reviewer.answer(
        review_id, claim_id, confirm=body.status == "confirmed", reason=body.reason
    )
    return _answered(result, review_id)


@router.post(
    "/{review_id}/claims/confirm-all",
    response_model=ApiResponse[ShotReviewRow],
    summary="Confirm every claim of a reading that is still waiting",
)
async def confirm_all_claims(
    review_id: int, reviewer: ReviewServiceDep, body: ConfirmAll | None = None
) -> JSONResponse:
    """Every `proposed` claim becomes `confirmed` in one transaction; answered ones stay.

    ``except_kinds`` leaves the claims of those kinds `proposed`: the page holds a prediction's
    stance back until the shot has a decision, so Confirm all must not confirm what was not shown.
    An unknown kind is a 422 that names the field and never echoes what was sent.
    """
    kinds = [] if body is None else body.except_kinds
    unknown = [kind for kind in kinds if kind not in REVIEW_CLAIM_KINDS]
    if unknown:
        raise Unprocessable(
            "Some kinds of claim are not known",
            details={
                "field": "except_kinds",
                "message": f"each kind is one of {', '.join(REVIEW_CLAIM_KINDS)}",
            },
        )
    return _answered(await reviewer.confirm_all(review_id, except_kinds=kinds), review_id)
