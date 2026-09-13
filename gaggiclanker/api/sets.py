"""`/api/sets` — what you were trying, version by version.

Five routes and one shape. A Set is created whole (identity plus its first
version, in one request, because a Set with no version is not a Set); everything
after that is **append-only**: a new version records what changed, why, and
which version it came from. Nothing here edits a version in place, and that is
the design rather than an omission — see `db/repos/sets.py`.

`GET /api/sets/{id}` is the page: the Set, every version newest first with the
diff against its parent already computed, and the shots grouped under the
version they were pulled with.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict, replace
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.analyzer.service import BatchResult
from gaggiclanker.api.deps import (
    AnalyzerServiceDep,
    BeansRepoDep,
    GrindersRepoDep,
    JudgementsRepoDep,
    ProfilesRepoDep,
    SetsRepoDep,
    ShotsRepoDep,
    SuggestionsRepoDep,
)
from gaggiclanker.db.repos.analyses import SuggestionRow
from gaggiclanker.db.repos.judgements import ShotJudgementRow
from gaggiclanker.db.repos.sets import (
    FieldChange,
    SetRow,
    SetTrends,
    SetVersionPatch,
    SetVersionRow,
    SetVersionWrite,
    SetWrite,
    version_changes,
)
from gaggiclanker.db.repos.shots import ShotListRow
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable
from gaggiclanker.infra.ratelimit import ANALYSIS_RATE_LIMIT, rate_limit

__all__ = ["router"]

router = APIRouter(prefix="/sets", tags=["sets"])

#: How many shots a Set page loads. A Set that has run past this is a Set worth
#: a filtered shots list, which the page links to.
SHOTS_PER_SET = 500


class SetAnalyseRequest(BaseModel):
    """`POST /api/sets/{id}/analyse`: the batch, and how much of it to do."""

    model_config = ConfigDict(extra="forbid")

    #: Skip shots that already have a *successful* analysis. A failed or
    #: interrupted one does not count as analysed: picking up what the rate
    #: limit or a restart dropped is what this default is for.
    only_unanalysed: bool = True
    model: str = ""


class SetCreate(BaseModel):
    """`POST /api/sets`: the identity and the first recipe, in one request."""

    model_config = ConfigDict(extra="forbid")

    name: str
    bean_id: int
    grinder_id: int | None = None
    #: Version 1. Everything on it is optional — a Set can start as "this bean,
    #: this grinder, we will see" — and the wizard fills it in.
    version: SetVersionWrite = SetVersionWrite()
    #: Whether this becomes the active Set. True by default: a Set is
    #: created by somebody who has just put that bag in the hopper, and one that
    #: did not start collecting shots would look broken.
    activate: bool = True


class SetListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SetRow]


class SetVersionDetail(BaseModel):
    """One version, its diff against its parent, and the shots pulled with it."""

    model_config = ConfigDict(extra="forbid")

    version: SetVersionRow
    #: What this version changed, computed against `parent_version_id`. Empty
    #: for version 1, which is a baseline rather than a change to anything.
    changes: list[FieldChange]
    shots: list[ShotListRow]


class SuggestionListData(BaseModel):
    """`GET /api/sets/{id}/suggestions`: every piece of advice about this Set.

    Flat and newest first rather than grouped by version: the reader's question
    is "what is outstanding", and grouping would bury one open suggestion from
    last week under four resolved ones from today. Each row carries its shot and
    its version, so the page groups them however it likes.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[SuggestionRow]


class SetDetailData(BaseModel):
    """`GET /api/sets/{id}`: everything the Set page draws."""

    model_config = ConfigDict(extra="forbid")

    set: SetRow
    versions: list[SetVersionDetail]
    #: Verdicts for the shots above, keyed by shot id as a string (JSON object
    #: keys are strings, and pretending otherwise costs the client a cast).
    judgements: dict[str, ShotJudgementRow]


def _missing(field: str, value: int, noun: str) -> NoReturn:
    """422 naming the field, for a reference that does not resolve."""
    raise Unprocessable(
        f"No {noun} {value}",
        details={"field": field, "message": f"that {noun} does not exist"},
    )


@router.get("", response_model=ApiResponse[SetListData], summary="The Sets, active one first")
async def list_sets(
    sets: SetsRepoDep,
    include_archived: Annotated[bool, Query()] = False,
) -> JSONResponse:
    rows = await sets.list_sets(include_archived=include_archived)
    return envelope_response(SetListData(items=rows).model_dump(mode="json"))


@router.post(
    "",
    response_model=ApiResponse[SetRow],
    status_code=201,
    summary="Start a Set, with its first version",
)
async def create_set(
    body: SetCreate,
    sets: SetsRepoDep,
    beans: BeansRepoDep,
    grinders: GrindersRepoDep,
    profiles: ProfilesRepoDep,
) -> JSONResponse:
    """Create the Set and version 1 atomically.

    Every reference is checked here rather than left to the foreign keys. A
    failed key raises `IntegrityError` from inside the transaction, which the
    envelope can only report as an internal error — a 500 on a request whose
    only problem is a stale id in a dropdown, and with nothing in the body
    saying which of the three ids was wrong. Each check answers that instead:
    422, with `details.field` naming the one at fault.
    """
    # A null grinder and a null profile are legitimate — a Set can name neither —
    # so only a stated id is looked up.
    if await beans.get(body.bean_id) is None:
        _missing("bean_id", body.bean_id, "bean")
    if body.grinder_id is not None and await grinders.get(body.grinder_id) is None:
        _missing("grinder_id", body.grinder_id, "grinder")
    profile_version_id = body.version.profile_version_id
    if profile_version_id is not None and await profiles.get_version(profile_version_id) is None:
        _missing("version.profile_version_id", profile_version_id, "profile version")

    row = await sets.create(
        SetWrite(
            name=body.name,
            bean_id=body.bean_id,
            grinder_id=body.grinder_id,
        ),
        body.version,
        activate=body.activate,
    )
    return envelope_response(row.model_dump(mode="json"), status_code=201)


@router.get(
    "/{set_id}",
    response_model=ApiResponse[SetDetailData],
    summary="One Set with its versions, their diffs and their shots",
)
async def get_set(
    set_id: int,
    sets: SetsRepoDep,
    shots: ShotsRepoDep,
    judgements: JudgementsRepoDep,
) -> JSONResponse:
    row = await sets.get(set_id)
    if row is None:
        raise NotFound(f"No Set {set_id}")
    versions = await sets.versions(set_id)
    by_id = {version.id: version for version in versions}
    labels = {
        version.profile_version_id: version.profile_label
        for version in versions
        if version.profile_version_id is not None and version.profile_label
    }

    # One shots query for the whole Set rather than one per version: a Set with
    # five versions is five round trips otherwise, and the rows are grouped in
    # memory from a column that is already in the projection.
    page = await shots.list_shots(set_id=set_id, limit=SHOTS_PER_SET)
    grouped: dict[int, list[ShotListRow]] = {version.id: [] for version in versions}
    for shot in page.items:
        if shot.set_version_id in grouped:
            grouped[shot.set_version_id].append(shot)

    details = [
        SetVersionDetail(
            version=version,
            changes=version_changes(version, by_id.get(version.parent_version_id or 0), labels),
            shots=grouped[version.id],
        )
        for version in versions
    ]
    verdicts = await judgements.for_shots([shot.id for shot in page.items])
    return envelope_response(
        SetDetailData(
            set=row,
            versions=details,
            judgements={str(shot_id): verdict for shot_id, verdict in verdicts.items()},
        ).model_dump(mode="json")
    )


@router.post(
    "/{set_id}/versions",
    response_model=ApiResponse[SetVersionRow],
    status_code=201,
    summary="Change something: a new version, with its parent and its intent",
)
async def add_version(set_id: int, body: SetVersionPatch, sets: SetsRepoDep) -> JSONResponse:
    """Append a version made of the current one plus the fields that were sent.

    Omitting a field inherits it; sending it as `null` clears it. That
    distinction is the reason the body is read with ``exclude_unset`` rather
    than compared against defaults — "no dose" and "same dose as before" are
    different statements about the coffee.
    """
    version = await sets.add_version(set_id, body)
    if version is None:
        raise NotFound(f"No Set {set_id}")
    return envelope_response(version.model_dump(mode="json"), status_code=201)


@router.post(
    "/{set_id}/activate",
    response_model=ApiResponse[SetRow],
    summary="Make this the Set the machine is set up for",
)
async def activate_set(set_id: int, sets: SetsRepoDep) -> JSONResponse:
    """Switches the flag off the previous active Set. Archives nothing.

    An archived Set is a 409 rather than a silent no-op: activating one would
    clear the flag from the live Set and leave the machine with no usable active
    Set at all, after which every shot lands in the inbox for no visible reason.
    """
    existing = await sets.get(set_id)
    if existing is None:
        raise NotFound(f"No Set {set_id}")
    if existing.status != "active":
        raise Conflict(
            f"Set {set_id} is archived",
            details={"field": "status", "message": "un-archive it before making it the active Set"},
        )
    row = await sets.activate(set_id)
    if row is None:  # pragma: no cover - checked above, inside the same request
        raise NotFound(f"No Set {set_id}")
    return envelope_response(row.model_dump(mode="json"))


@router.post("/{set_id}/archive", response_model=ApiResponse[SetRow], summary="Retire a Set")
async def archive_set(set_id: int, sets: SetsRepoDep) -> JSONResponse:
    row = await sets.archive(set_id)
    if row is None:
        raise NotFound(f"No Set {set_id}")
    return envelope_response(row.model_dump(mode="json"))


@router.get(
    "/{set_id}/trends",
    response_model=ApiResponse[SetTrends],
    summary="Score, duration, ratio and rating across the Set's versions",
)
async def get_trends(set_id: int, sets: SetsRepoDep) -> JSONResponse:
    """The chart's data: one point per shot, one summary per version.

    Both from one pass over the Set's shots, so a bar can never sit off its own
    points because two queries rounded differently.
    """
    row = await sets.get(set_id)
    if row is None:
        raise NotFound(f"No Set {set_id}")
    return envelope_response((await sets.trends(set_id)).model_dump(mode="json"))


@router.get(
    "/{set_id}/suggestions",
    response_model=ApiResponse[SuggestionListData],
    summary="Every suggestion made about a shot in this Set",
)
async def list_suggestions(
    set_id: int, sets: SetsRepoDep, suggestions: SuggestionsRepoDep
) -> JSONResponse:
    if await sets.get(set_id) is None:
        raise NotFound(f"No Set {set_id}")
    return envelope_response(
        SuggestionListData(items=await suggestions.for_set(set_id)).model_dump(mode="json")
    )


@router.post(
    "/{set_id}/analyse",
    response_model=ApiResponse[BatchResult],
    status_code=202,
    summary="Queue an analysis of every un-analysed shot in this Set",
    # The other money-spending route. Same bucket as the per-shot one on
    # purpose: a caller alternating between the two would otherwise get twice
    # the allowance for the same provider account.
    dependencies=[Depends(rate_limit("analysis", ANALYSIS_RATE_LIMIT))],
)
async def analyse_set(
    set_id: int,
    body: SetAnalyseRequest,
    request: Request,
    sets: SetsRepoDep,
    analyzer: AnalyzerServiceDep,
    wait: Annotated[bool, Query()] = False,
) -> JSONResponse:
    """Queue the batch and answer with what it is about to do.

    A Set of fifty un-analysed shots is twenty-five minutes of provider time, so
    it runs as one registered background task rather than inside this request —
    which also means shutdown cancels it in one place instead of leaving sixty
    futures nobody is holding. The response carries real numbers rather than a
    bare "accepted": `requested` is what was queued and `skipped` is how many
    shots something else is already analysing.

    One batch per Set at a time. A second press while one is running is a 409:
    the first batch is already working through exactly the shots the second
    would pick.

    ``?wait=1`` blocks until the batch is done. For tests and `curl`; a browser
    follows the LLM stream, which carries an event per shot.
    """
    if await sets.get(set_id) is None:
        raise NotFound(f"No Set {set_id}")
    try:
        result = await analyzer.start_set(
            set_id,
            tasks=request.app.state.tasks,
            only_unanalysed=body.only_unanalysed,
            model=body.model or None,
        )
    except RuntimeError as exc:
        raise Conflict(
            f"Set {set_id} is already being analysed",
            details={"field": "set_id", "message": "wait for the running batch to finish"},
        ) from exc

    if wait and result.task:
        task = request.app.state.tasks.get(result.task)
        if task is not None:
            with suppress(asyncio.CancelledError):
                finished = await asyncio.shield(task)
            if isinstance(finished, BatchResult):
                result = replace(finished, skipped=result.skipped)
    return envelope_response(asdict(result), status_code=202)
