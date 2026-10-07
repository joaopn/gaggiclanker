"""`/api/reviews`: one review read back with what it was told, and a person's answers to its claims.

The routes that start and list reviews live on the shot they are about
(`POST /api/shots/{id}/reviews`, `GET /api/shots/{id}/reviews`); this router
holds what is not about a shot: a single review, with the input the model was given, so any
review can be explained, and the one way a person answers its claims: rejecting one, or
restoring it. Only a person answers: no tool, chat run or sync step reaches these.

Only the newest finished review of a shot can be answered. A review set aside by a newer
one answers 409, and so does one that never finished.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.deps import ReviewServiceDep, ReviewsRepoDep
from gaggiclanker.db.repos.reviews import AnswerResult, ShotReviewDetail, ShotReviewRow
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import Conflict, NotFound

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
    """`PATCH /api/reviews/{id}/claims/{claim_id}`: reject one claim, or restore it."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "rejected"]


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
    summary="Reject one claim of a review, or restore it",
)
async def answer_claim(
    review_id: int, claim_id: int, body: ClaimAnswer, reviewer: ReviewServiceDep
) -> JSONResponse:
    """A person's answer, which they may change: the last one wins.

    Checked and written in one transaction, so a review a newer one has set aside is refused
    rather than answered, and two answers arriving together leave one consistent state.
    """
    result = await reviewer.answer(review_id, claim_id, keep=body.status == "confirmed")
    return _answered(result, review_id)
