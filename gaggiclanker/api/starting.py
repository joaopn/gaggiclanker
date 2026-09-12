"""`/api/starting-points` — asking for a first recipe, and taking one.

Three routes, and the split between them is the feature's whole shape: one
queues a provider call and answers 202 with the row, one reads that row back,
and one turns a chosen option into a Set. The similar-Set query has a route of
its own on the bean (`api/beans.py`), because the wizard shows those cards
*before* anybody spends a token — "here is what you already know about beans
like this" is useful on its own and free.

The POST does not run the call inside the request, for the reason
`POST /api/shots/{id}/analyses` does not: `docker stop` allows ten seconds and a
request holding a two-minute provider call is killed mid-flight with the browser
still waiting. `?wait=1` blocks until the work is done, for tests and `curl`.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.api.deps import StartingPointServiceDep
from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow
from gaggiclanker.db.repos.sets import SetRow, SetVersionRow
from gaggiclanker.db.repos.starting import StartingPointRunRow
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import NotFound
from gaggiclanker.infra.ratelimit import rate_limit
from gaggiclanker.starting.models import OptionKey
from gaggiclanker.starting.service import starting_point_task_name

__all__ = ["router"]

router = APIRouter(prefix="/starting-points", tags=["starting-point"])

#: The same bucket size the analysis route uses, and for the same reason: this
#: is the other route in the API that spends money, and the registry's name
#: guard only makes a repeat request for the *same* bag idempotent. A loop
#: walking a catalogue of beans is what this bounds.
STARTING_POINT_RATE_LIMIT = 30


class StartingPointRequest(BaseModel):
    """`POST /api/starting-points`: which bag, on which kit."""

    model_config = ConfigDict(extra="forbid")

    bean_id: int = Field(gt=0)
    machine_id: int = Field(gt=0)
    #: Optional for the reason `sets.grinder_id` is: pre-ground coffee, and a
    #: grinder nobody has recorded, are both real.
    grinder_id: int | None = None
    #: What they normally grind espresso at, in the grinder's own units. The
    #: single most useful thing on this body — it is what lets the answer be a
    #: number rather than a direction.
    usual_grind: str = Field(default="", max_length=100)
    #: A dose to hold, usually their basket. Empty lets the model choose.
    dose_hint_g: float | None = Field(default=None, gt=0, le=100)
    #: Overrides `modelStartingPoint` for this run only.
    model: str = ""


class StartingPointAcceptRequest(BaseModel):
    """`POST /api/starting-points/{id}/accept`: which of the three."""

    model_config = ConfigDict(extra="forbid")

    option: OptionKey


class StartingPointAccepted(BaseModel):
    """What an accept produced.

    Named for the feature rather than `AcceptedData`, because the suggestion
    routes already have a model by that name and two of them collide into
    module-qualified names in the OpenAPI document — which would rename the
    front end's existing alias for somebody else's model.

    The draft is here rather than only its id because the wizard's next step is
    to open it, and a second round-trip to discover whether there even is one
    would make the button feel like it did nothing.
    """

    model_config = ConfigDict(extra="forbid")

    run: StartingPointRunRow
    set: SetRow
    version: SetVersionRow
    draft: ProfileDraftRow | None = None


@router.post(
    "",
    response_model=ApiResponse[StartingPointRunRow],
    status_code=202,
    summary="Ask for a starting point for a new bag",
    dependencies=[Depends(rate_limit("starting_point", STARTING_POINT_RATE_LIMIT))],
)
async def create_starting_point(
    body: StartingPointRequest,
    request: Request,
    starting: StartingPointServiceDep,
    wait: Annotated[bool, Query()] = False,
) -> JSONResponse:
    """Queue the work and answer with the `running` row. 202, not 201.

    Idempotent per (bean, machine, grinder). A second tab pressing the button
    gets the running row rather than a second call, because the registry name
    can only be held once.

    A **provider failure still answers 2xx.** The row exists, it says `failed`
    and it carries the error code; a 502 would leave the client an error and no
    id, and the row it could not see is the one thing that explains what
    happened.
    """
    try:
        row, _started = await starting.start(
            bean_id=body.bean_id,
            machine_id=body.machine_id,
            grinder_id=body.grinder_id,
            usual_grind=body.usual_grind,
            dose_hint_g=body.dose_hint_g,
            tasks=request.app.state.tasks,
            model=body.model or None,
        )
    except LookupError as exc:
        raise NotFound(str(exc)) from None
    if wait:
        row = await _awaited(request, body, starting, row)
    return envelope_response(row.model_dump(mode="json"), status_code=202)


@router.get(
    "/{run_id}",
    response_model=ApiResponse[StartingPointRunRow],
    summary="One starting-point run, with its three options",
)
async def get_starting_point(run_id: int, starting: StartingPointServiceDep) -> JSONResponse:
    row = await starting.get(run_id)
    return envelope_response(row.model_dump(mode="json"))


@router.post(
    "/{run_id}/accept",
    response_model=ApiResponse[StartingPointAccepted],
    status_code=201,
    summary="Take one option: create the Set, its first version and any draft",
)
async def accept_starting_point(
    run_id: int, body: StartingPointAcceptRequest, starting: StartingPointServiceDep
) -> JSONResponse:
    """Create the Set this option describes.

    Refused with a 409 for a run that is still running, that failed, or that
    somebody has already accepted — the last one carries the ids of what the
    first accept made, so the UI can take the person there rather than showing
    them an error about a Set that exists.

    Refused with a 422 when the option's profile document is one the safety
    policy will not allow, carrying every violation at once. Nothing is created
    in that case: a Set pointing at a profile that was rejected is worse than no
    Set.
    """
    accepted = await starting.accept(run_id, body.option)
    return envelope_response(
        StartingPointAccepted(
            run=accepted.run,
            set=accepted.set_row,
            version=accepted.version,
            draft=accepted.draft,
        ).model_dump(mode="json"),
        status_code=201,
    )


async def _awaited(
    request: Request,
    body: StartingPointRequest,
    starting: StartingPointServiceDep,
    row: StartingPointRunRow,
) -> StartingPointRunRow:
    """Wait for the queued task and re-read the row. ``?wait=1`` only.

    A task that has already finished is not in the registry any more, which is
    not an error — it is the point of the name being released on completion — so
    a missing task means "read the row again".
    """
    task = request.app.state.tasks.get(
        starting_point_task_name(body.bean_id, body.machine_id, body.grinder_id)
    )
    if task is not None:
        with suppress(asyncio.CancelledError):
            await asyncio.shield(task)
    return await starting.get(row.id)
