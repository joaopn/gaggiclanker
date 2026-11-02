"""`/api/signature-requests`: the changes to a signature an agent asked for, and a person's answer.

An agent cannot change an expectation in force: it asks, with `request_signature_change`, and the
request waits on a card in its conversation. Reading is here for the chat (a conversation's own
requests, with no cap) and for a profile version; **Approve and Decline are a person's presses**
and there is no tool for either. Approve checks every change again and writes in one transaction,
with the verbs the Profiles page already has: a request that no longer applies becomes stale and
answers 409 `SIGNATURE_REQUEST_STALE`; one already answered answers 409
`SIGNATURE_REQUEST_DECIDED`. A request that came with a profile draft is answered by its draft
(409 `SIGNATURE_REQUEST_IS_A_DRAFTS`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.api.deps import DatabaseDep
from gaggiclanker.db.repos.signature_requests import SignatureRequestsRepository
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable
from gaggiclanker.signatures.change_requests import (
    ApproveResult,
    RequestView,
    SignatureRequestService,
)

__all__ = ["router"]

router = APIRouter(tags=["signatures"])

REASON_MAX = 300


class SignatureRequestList(BaseModel):
    """`GET /api/signature-requests`: oldest first for a conversation, newest first for a
    profile version."""

    model_config = ConfigDict(extra="forbid")

    items: list[RequestView]


class SignatureRequestAnswer(BaseModel):
    """What answering produced: the request as it now stands."""

    model_config = ConfigDict(extra="forbid")

    request: RequestView


class DeclineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=REASON_MAX)


@router.get(
    "/signature-requests",
    response_model=ApiResponse[SignatureRequestList],
    summary="The signature changes an agent asked for, in a conversation or on a profile version",
)
async def list_signature_requests(
    db: DatabaseDep,
    thread_id: Annotated[
        int | None,
        Query(
            description=(
                "Only what this conversation asked for, with no cap: the chat's cards read "
                "their request this way, so one is found however old it is."
            )
        ),
    ] = None,
    profile_version_id: Annotated[
        int | None, Query(description="Every request made for this profile version.")
    ] = None,
) -> JSONResponse:
    """Waiting and answered alike, so a card read days later tells the truth."""
    if (thread_id is None) == (profile_version_id is None):
        raise Unprocessable(
            "Ask for one conversation or one profile version",
            details={"field": "thread_id", "message": "give thread_id or profile_version_id"},
        )
    repo = SignatureRequestsRepository(db)
    rows = (
        await repo.for_thread(thread_id)
        if thread_id is not None
        else await repo.for_version(profile_version_id or 0)
    )
    items = await SignatureRequestService(db).views(rows)
    return envelope_response(SignatureRequestList(items=items).model_dump(mode="json"))


async def _answered(result: ApproveResult, request_id: int, db: DatabaseDep) -> JSONResponse:
    if result.refused == "no_request" or result.request is None:
        raise NotFound(f"No signature request {request_id}")
    if result.refused == "draft":
        raise Conflict(
            f"Signature request {request_id} came with a profile draft",
            code="SIGNATURE_REQUEST_IS_A_DRAFTS",
            details={
                "field": "draft_id",
                "message": "it is answered with its draft: approve or decline the draft",
            },
        )
    if result.refused == "stale" or (
        result.refused == "not_waiting" and result.request.status == "stale"
    ):
        # A request the cause staled (a rejection on the Profiles page, a newer request) answers
        # with its own code and the reason it was staled for, not "already answered".
        raise Conflict(
            f"This request is no longer open. {result.request.stale_reason}",
            code="SIGNATURE_REQUEST_STALE",
            details={"field": "status", "message": "it is no longer open; nothing was changed"},
        )
    if result.refused == "not_waiting":
        raise Conflict(
            f"Signature request {request_id} has already been answered",
            code="SIGNATURE_REQUEST_DECIDED",
            details={"field": "status", "message": "it is no longer waiting for an answer"},
        )
    view = await SignatureRequestService(db).view(result.request)
    return envelope_response(SignatureRequestAnswer(request=view).model_dump(mode="json"))


@router.post(
    "/signature-requests/{request_id}/approve",
    response_model=ApiResponse[SignatureRequestAnswer],
    summary="Approve a signature change an agent asked for: the changes are made",
)
async def approve_signature_request(request_id: int, db: DatabaseDep) -> JSONResponse:
    """A person's press, and the only way a request changes anything. Every change is checked
    again, by the guard the tool used, and written in one transaction with the reject, add,
    tier and restore a person already has; a change that no longer applies makes the whole
    request stale (409) and writes nothing."""
    return await _answered(await SignatureRequestService(db).approve(request_id), request_id, db)


@router.post(
    "/signature-requests/{request_id}/decline",
    response_model=ApiResponse[SignatureRequestAnswer],
    summary="Decline a signature change an agent asked for: nothing changes",
)
async def decline_signature_request(
    request_id: int, db: DatabaseDep, body: DeclineBody | None = None
) -> JSONResponse:
    """The agent is told at its next message, with the reason when one is given, and may ask
    again with a new request."""
    reason = " ".join((body.reason if body is not None else "").split())
    return await _answered(
        await SignatureRequestService(db).decline(request_id, reason), request_id, db
    )
