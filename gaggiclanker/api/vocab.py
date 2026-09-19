"""`/api/vocab` — the closed vocabularies, served as data.

One request on page load and the front end has every enum it needs: roast
levels, processes, burr types, grind step units, balance, the flavour wheel,
the decisions, and where a Set version came from.

It exists so that nothing in `web/src` types a list of coffee words. A UI that
hard-codes an enum drifts from the database the first time one changes, and the
symptom is a 422 on a value the user picked from a dropdown we shipped.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from gaggiclanker.domain.vocab import Vocabulary, vocabulary
from gaggiclanker.infra.envelope import ApiResponse, envelope_response

__all__ = ["router"]

router = APIRouter(prefix="/vocab", tags=["vocab"])


@router.get(
    "",
    response_model=ApiResponse[Vocabulary],
    summary="Every closed vocabulary the judgement and Set forms use",
)
async def get_vocabulary() -> JSONResponse:
    return envelope_response(vocabulary().model_dump(mode="json"))
