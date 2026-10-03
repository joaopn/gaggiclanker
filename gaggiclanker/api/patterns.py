"""`/api/knowledge/patterns`: Find patterns across Sets, and a person's answers to what it proposed.

Every route here is a person's press: the button that starts a run, and the two buttons on a
proposal. There is no tool, MCP tool, timer or boot step behind any of them (the tests walk the
registry and the bytecode of the whole package to keep it that way).

A run is queued, not awaited: ``POST .../runs`` answers 202 with the `running` row and the LLM
stream carries `patterns.started` / `patterns.finished` / `patterns.failed`. A provider failure
still answers 2xx: the row says `failed` and carries the error.

Approving is one transaction (see :meth:`PatternProposalsRepository.approve`); a source that was
gone or no longer confirmed is skipped and named in the answer, and the approval is refused with
its own code when fewer than two Sets' sources remain. `details` carries a field and a sentence
and never echoes what was sent.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.api.deps import PatternProposalsRepoDep, PatternRunsRepoDep, PatternsServiceDep
from gaggiclanker.db.repos.patterns import (
    PatternProposalRow,
    PatternRunRow,
    PatternSkipped,
)
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import Conflict, NotFound
from gaggiclanker.infra.ratelimit import REVIEW_RATE_LIMIT, rate_limit
from gaggiclanker.patterns.service import MIN_SETS, PATTERNS_TASK, NotEnoughSets

__all__ = ["router"]

router = APIRouter(prefix="/knowledge/patterns", tags=["knowledge"])


class PatternsData(BaseModel):
    """What the Knowledge page's section reads: the newest run, its proposals, two numbers."""

    model_config = ConfigDict(extra="forbid")

    #: The newest run, whatever state it is in; ``None`` before the first press.
    run: PatternRunRow | None = None
    #: The proposals of the newest **finished** run, answered ones included so a card can say
    #: what happened to it. A failed run after it leaves these standing.
    proposals: list[PatternProposalRow] = Field(default_factory=list)
    #: Confirmed Set insights confirmed after the last finished run began.
    new_since_last_run: int = 0
    #: When the last **finished** run began: what the count above is counted from. ``None``
    #: before any run has finished, which is also when the count is of everything.
    counted_from: str | None = None
    #: How many Sets have a confirmed insight, and how many a run needs.
    sets_with_insights: int = 0
    min_sets: int = MIN_SETS


class PatternRunRequest(BaseModel):
    """`POST /api/knowledge/patterns/runs`: start one, optionally on a named model."""

    model_config = ConfigDict(extra="forbid")

    #: Overrides `modelPatterns` for this run only. Empty takes the setting.
    model: str = ""


class PatternRunDetail(BaseModel):
    """A run with what the model was told, so any proposal can be explained."""

    model_config = ConfigDict(extra="forbid")

    run: PatternRunRow
    input: dict[str, Any] = Field(default_factory=dict)


class PatternProposalDecision(BaseModel):
    """The answer to a press on a proposal."""

    model_config = ConfigDict(extra="forbid")

    proposal: PatternProposalRow
    #: What Approve left alone (a source already gone, one taken back, a Set the scope no
    #: longer reaches, a replaced insight already gone). Empty for a dismissal.
    skipped: list[PatternSkipped] = Field(default_factory=list)
    #: The Set insights it deleted, so the page can refresh exactly their Sets.
    deleted_insight_ids: list[int] = Field(default_factory=list)


@router.get(
    "",
    response_model=ApiResponse[PatternsData],
    summary="The newest pattern run, its proposals and how many insights are new",
)
async def get_patterns(
    runs: PatternRunsRepoDep, proposals: PatternProposalsRepoDep
) -> JSONResponse:
    latest_done = await runs.latest_done()
    data = PatternsData(
        run=await runs.latest(),
        proposals=[] if latest_done is None else await proposals.for_run(latest_done.id),
        new_since_last_run=await runs.new_since_last_run(),
        counted_from=None if latest_done is None else latest_done.created_at,
        sets_with_insights=await runs.sets_with_insights(),
    )
    return envelope_response(data.model_dump(mode="json"))


@router.post(
    "/runs",
    response_model=ApiResponse[PatternRunRow],
    status_code=202,
    summary="Find patterns across Sets",
    # A run spends a provider call; the registry already makes a second press while one
    # runs idempotent, and this bounds a loop of presses over time.
    dependencies=[Depends(rate_limit("patterns", REVIEW_RATE_LIMIT))],
)
async def start_run(
    body: PatternRunRequest,
    request: Request,
    runs: PatternRunsRepoDep,
    patterns: PatternsServiceDep,
    wait: Annotated[bool, Query()] = False,
) -> JSONResponse:
    """Queue the work and answer with the `running` row. 202, not 201.

    This is the only way a run starts: a person pressing the button. 409
    `PATTERNS_NOT_ENOUGH_SETS` when fewer than two Sets have a confirmed insight. A press
    while one runs gets that running row back. ``?wait=1`` blocks until the run is finished
    (tests and `curl`); a browser follows the stream.
    """
    try:
        row, _started = await patterns.start(
            tasks=request.app.state.tasks, model=body.model or None
        )
    except NotEnoughSets as exc:
        raise Conflict(
            "Fewer than two Sets have a confirmed insight",
            code="PATTERNS_NOT_ENOUGH_SETS",
            details={
                "field": "sets",
                "message": (
                    f"A pattern needs {MIN_SETS} Sets that have each confirmed an insight; "
                    f"{exc.found} do."
                ),
            },
        ) from exc
    if wait:
        task = request.app.state.tasks.get(PATTERNS_TASK)
        if task is not None:
            with suppress(asyncio.CancelledError):
                await asyncio.shield(task)
        row = await runs.get(row.id) or row
    return envelope_response(row.model_dump(mode="json"), status_code=202)


@router.get(
    "/runs/{run_id}",
    response_model=ApiResponse[PatternRunDetail],
    summary="One run, with what the model was told",
)
async def get_run(run_id: int, runs: PatternRunsRepoDep) -> JSONResponse:
    row = await runs.get(run_id)
    if row is None:
        raise NotFound(f"No pattern run {run_id}")
    detail = PatternRunDetail(run=row, input=await runs.detail_input(run_id) or {})
    return envelope_response(detail.model_dump(mode="json"))


def _refused(refused: str | None, proposal_id: int) -> Conflict | NotFound:
    if refused == "no_proposal" or refused is None:
        return NotFound(f"No pattern proposal {proposal_id}")
    if refused == "run_going":
        return Conflict(
            "A run is going",
            code="PATTERNS_RUNNING",
            details={
                "field": "status",
                "message": (
                    "A run is reading the insights now, and it replaces these proposals when it "
                    "finishes. Answer them once it has."
                ),
            },
        )
    if refused == "too_few_sets":
        return Conflict(
            "Fewer than two Sets' insights are left to replace",
            code="PATTERN_TOO_FEW_SETS",
            details={
                "field": "sources",
                "message": (
                    "The Set insights this was derived from have changed since it was proposed, "
                    "and fewer than two Sets' are left. Dismiss it."
                ),
            },
        )
    return Conflict(
        f"Pattern proposal {proposal_id} is not waiting for an answer",
        code="PATTERN_PROPOSAL_DECIDED",
        details={
            "field": "status",
            "message": "it was already answered, or a newer run replaced it",
        },
    )


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=ApiResponse[PatternProposalDecision],
    summary="Approve a proposed general insight",
)
async def approve_proposal(proposal_id: int, proposals: PatternProposalsRepoDep) -> JSONResponse:
    """A person's press: writes the general insight and deletes what it was derived from.

    One transaction. A source that is gone or no longer confirmed is skipped and named in
    `skipped`; the approval still succeeds when at least two Sets' sources remain, and is
    refused (409 `PATTERN_TOO_FEW_SETS`, nothing written) otherwise. 409 `PATTERNS_RUNNING` while
    a run is going. There is no tool for this.
    """
    result = await proposals.approve(proposal_id)
    if result.proposal is None or result.refused is not None:
        raise _refused(result.refused, proposal_id)
    return envelope_response(
        PatternProposalDecision(
            proposal=result.proposal, skipped=result.skipped, deleted_insight_ids=result.deleted
        ).model_dump(mode="json")
    )


@router.post(
    "/proposals/{proposal_id}/dismiss",
    response_model=ApiResponse[PatternProposalDecision],
    summary="Turn a proposed general insight down",
)
async def dismiss_proposal(proposal_id: int, proposals: PatternProposalsRepoDep) -> JSONResponse:
    """A person's press. The proposal is kept until the next finished run, which is told it
    was declined, and then deleted. There is no tool for this."""
    result = await proposals.dismiss(proposal_id)
    if result.proposal is None or result.refused is not None:
        raise _refused(result.refused, proposal_id)
    return envelope_response(
        PatternProposalDecision(proposal=result.proposal).model_dump(mode="json")
    )
