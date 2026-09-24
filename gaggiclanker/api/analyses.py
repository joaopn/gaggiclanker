"""`/api/analyses` and `/api/suggestions` — reading runs, and acting on advice.

The routes that *start* an analysis live on the resources they are about
(`POST /api/shots/{id}/analyses`, `POST /api/sets/{id}/analyse`); this router
holds the two things that are not about a shot or a Set: reading one analysis
back, and resolving one suggestion.

Every route here runs the call in the request. An analysis takes thirty seconds
to two minutes and the browser is not left guessing about it — the LLM stream
already carries `analysis.started` and `analysis.finished`, and the header's
activity indicator is watching it — so the request that asked for the work is
also the one that reports the result. Backgrounding it would buy nothing except
a second place for the outcome to get lost.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.analyzer.suggestions import accept_suggestion, reject_suggestion
from gaggiclanker.api.deps import AnalysesRepoDep, DatabaseDep
from gaggiclanker.api.sets import version_refused
from gaggiclanker.db.repos.analyses import AnalysisRow, SuggestionRow
from gaggiclanker.db.repos.sets import SetVersionRow, VersionRefused
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import NotFound

__all__ = ["router", "suggestions_router"]

router = APIRouter(prefix="/analyses", tags=["analysis"])
suggestions_router = APIRouter(prefix="/suggestions", tags=["analysis"])


class AcceptedData(BaseModel):
    """What an accept produced: the resolved suggestion and the new version."""

    model_config = ConfigDict(extra="forbid")

    suggestion: SuggestionRow
    #: The version the change created. Its parent is the version the shot was
    #: pulled with, and the only field that differs is the suggested one.
    version: SetVersionRow


@router.get(
    "/{analysis_id}",
    response_model=ApiResponse[AnalysisRow],
    summary="One analysis, with its suggestions",
)
async def get_analysis(analysis_id: int, analyses: AnalysesRepoDep) -> JSONResponse:
    row = await analyses.get(analysis_id)
    if row is None:
        raise NotFound(f"No analysis {analysis_id}")
    return envelope_response(row.model_dump(mode="json"))


@suggestions_router.post(
    "/{suggestion_id}/accept",
    response_model=ApiResponse[AcceptedData],
    status_code=201,
    summary="Apply a suggestion: a new Set version with that one field changed",
)
async def accept(suggestion_id: int, db: DatabaseDep) -> JSONResponse:
    """Create the version this suggestion asks for, and supersede its siblings.

    Refused with a 409 for a suggestion that cannot be applied — a pressure or
    profile change (the prototype writes nothing to the machine), a Set that has
    moved on since the shot, or a version with no number to change. Each refusal
    names what would have to be different; see
    :mod:`gaggiclanker.analyzer.suggestions`.
    """
    try:
        suggestion, version = await accept_suggestion(db, suggestion_id)
    except VersionRefused as exc:
        # The shot is filed on a Set still being designed, so the change would
        # have to fill its version 1 — which the shot itself now forbids.
        raise version_refused(exc) from None
    return envelope_response(
        AcceptedData(suggestion=suggestion, version=version).model_dump(mode="json"),
        status_code=201,
    )


@suggestions_router.post(
    "/{suggestion_id}/reject",
    response_model=ApiResponse[SuggestionRow],
    summary="Turn a suggestion down",
)
async def reject(suggestion_id: int, db: DatabaseDep) -> JSONResponse:
    """Rejecting one suggestion leaves its siblings open: the backups may still be right."""
    return envelope_response((await reject_suggestion(db, suggestion_id)).model_dump(mode="json"))
