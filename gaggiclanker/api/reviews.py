"""`/api/reviews`: one review read back with what it was told.

The routes that start and list reviews live on the shot they are about
(`POST /api/shots/{id}/reviews`, `GET /api/shots/{id}/reviews`); this router
holds the one thing that is not about a shot: a single review, with the input
the model was given, so any reading can be explained.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from gaggiclanker.api.deps import ReviewsRepoDep
from gaggiclanker.db.repos.reviews import ShotReviewDetail
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import NotFound

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
