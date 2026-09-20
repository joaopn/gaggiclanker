"""`/api/sets` — what you were trying, version by version.

Five routes and one shape. A Set is created whole (identity plus its first
version, in one request, because a Set with no version is not a Set); everything
after that is **append-only**: a new version records what changed, why, and
which version it came from. Nothing here edits a version in place, and that is
the design rather than an omission — see `db/repos/sets.py`.

`GET /api/sets/{id}` is the page: the Set, every version newest first with the
diff against its parent already computed, and the shots grouped under the
version they were pulled with.

Three routes do edit a version, and they are the exceptions that prove the
rule: they write the **prediction** (only while the version has no shots) and
the **outcome** (at any time, and clearable). Neither is part of the recipe. The
fourth, `/rollback`, appends a version like everything else — it just copies its
recipe from an earlier one instead of the current one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
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
    RollbackWrite,
    SetRow,
    SetTrackRecord,
    SetTrends,
    SetVersionPatch,
    SetVersionRow,
    SetVersionWrite,
    SetWrite,
    VersionLabelCounts,
    VersionOutcomeWrite,
    VersionPredictionWrite,
    VersionRefusal,
    VersionWriteResult,
    dead_end_ids,
    track_record,
    version_changes,
)
from gaggiclanker.db.repos.shots import ShotListRow
from gaggiclanker.domain.spread import (
    MeasureSpread,
    VersionEvidence,
    pooled_spreads,
    spread_report,
    version_evidence,
)
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import AppError, Conflict, NotFound, Unprocessable
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
    #: A later roll back stepped over this version. Computed from the list, not
    #: from the row: it is a fact about what came after, not about this version.
    dead_end: bool = False
    #: How this version's shots were labelled. Counted over the Set's shots
    #: rather than over `shots` above, which is capped at `SHOTS_PER_SET`.
    labels: VersionLabelCounts = VersionLabelCounts()
    #: This version's shots against the compared version's, measure by measure,
    #: with each difference marked as beyond the Set's spread or inside it.
    #: NULL on a version with no prediction: there is nothing it is evidence
    #: for, and a table answering no question is noise in a log people read.
    evidence: VersionEvidence | None = None


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
    #: How often this Set's predictions held. The page's one headline number.
    track_record: SetTrackRecord = SetTrackRecord()
    #: How much this Set's shots vary when nothing in the recipe changed, per
    #: measure, with the basis under each one. Worked out by the app and not by
    #: a model, so the same shots always give the same figure.
    spread: list[MeasureSpread] = []
    #: The version the page offers to go back to: the newest one other than the
    #: current that has a Keep shot. NULL when there is nowhere to go back to.
    rollback_target_version_id: int | None = None


def _missing(field: str, value: int, noun: str) -> NoReturn:
    """422 naming the field, for a reference that does not resolve."""
    raise Unprocessable(
        f"No {noun} {value}",
        details={"field": field, "message": f"that {noun} does not exist"},
    )


def _unwrap(result: VersionWriteResult, set_id: int, version_id: int | None = None) -> JSONResponse:
    """The written version, or the one error its refusal means.

    The repository answers with a slug and no opinion about HTTP; this is the
    one place a slug becomes a status, a code and a sentence. `details` names
    the field at fault and never repeats what was sent — a prediction is the
    person's own words and has no business in an error body.
    """
    if result.version is not None:
        return envelope_response(result.version.model_dump(mode="json"))
    raise _REFUSALS[result.refused or "no_version"](set_id, version_id)


def _refusal_no_version(set_id: int, version_id: int | None) -> AppError:
    return NotFound(f"No version {version_id} in Set {set_id}")


def _refusal_has_shots(set_id: int, version_id: int | None) -> AppError:
    return Conflict(
        f"Version {version_id} already has shots",
        code="VERSION_HAS_SHOTS",
        details={
            "field": "prediction",
            "message": "a prediction is written before the shots, not after them",
        },
    )


def _refusal_has_outcome(set_id: int, version_id: int | None) -> AppError:
    return Conflict(
        f"Version {version_id} has already been graded",
        code="VERSION_HAS_OUTCOME",
        details={
            "field": "prediction",
            "message": "clear the outcome first — it grades the prediction as it was written",
        },
    )


def _refusal_bad_compare(set_id: int, version_id: int | None) -> AppError:
    return Unprocessable(
        "That is not a version this prediction can be compared against",
        details={
            "field": "compares_to_version_id",
            "message": "it must be an earlier version of this Set",
        },
    )


def _refusal_no_prediction(set_id: int, version_id: int | None) -> AppError:
    return Unprocessable(
        f"Version {version_id} states no prediction",
        details={"field": "outcome", "message": "there is nothing to grade"},
    )


def _refusal_nothing_to_grade(set_id: int, version_id: int | None) -> AppError:
    return Conflict(
        f"Version {version_id} has no shot you have formed a view about",
        code="NOTHING_TO_GRADE",
        details={
            "field": "outcome",
            "message": "label a shot Keep or Improve before grading the prediction",
        },
    )


def _refusal_no_target(set_id: int, version_id: int | None) -> AppError:
    return Unprocessable(
        f"No such version in Set {set_id}",
        details={"field": "to_version_id", "message": "it must be a version of this Set"},
    )


def _refusal_current_version(set_id: int, version_id: int | None) -> AppError:
    return Unprocessable(
        "That is already the current version",
        details={"field": "to_version_id", "message": "pick an earlier version to go back to"},
    )


#: Every refusal the repository can answer with, and the error it becomes.
_REFUSALS: dict[VersionRefusal, Callable[[int, int | None], AppError]] = {
    "no_version": _refusal_no_version,
    "has_shots": _refusal_has_shots,
    "has_outcome": _refusal_has_outcome,
    "bad_compare": _refusal_bad_compare,
    "no_prediction": _refusal_no_prediction,
    "nothing_to_grade": _refusal_nothing_to_grade,
    "no_target": _refusal_no_target,
    "current_version": _refusal_current_version,
}


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

    dead_ends = dead_end_ids(versions)
    counts = await sets.label_counts(set_id)
    # One pass over the Set's counted shots feeds both the spread and every
    # version's evidence: the evidence is held against the spread, and two
    # queries could answer with two different sets of shots if one landed
    # between them.
    counted = await sets.counted_shots(set_id)
    spreads = pooled_spreads(counted)
    details = [
        SetVersionDetail(
            version=version,
            changes=version_changes(version, by_id.get(version.parent_version_id or 0), labels),
            shots=grouped[version.id],
            dead_end=version.id in dead_ends,
            labels=counts.get(version.id, VersionLabelCounts()),
            evidence=(
                version_evidence(
                    counted,
                    spreads,
                    version_id=version.id,
                    version_no=version.version_no,
                    compares_to_version_id=version.compares_to_version_id,
                    compares_to_version_no=version.compares_to_version_no,
                )
                if version.prediction
                else None
            ),
        )
        for version in versions
    ]
    verdicts = await judgements.for_shots([shot.id for shot in page.items])
    return envelope_response(
        SetDetailData(
            set=row,
            versions=details,
            judgements={str(shot_id): verdict for shot_id, verdict in verdicts.items()},
            track_record=track_record(versions),
            spread=spread_report(spreads),
            rollback_target_version_id=await sets.rollback_target(set_id),
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
    # The Set first: a comparison check against a Set that is not there would
    # answer "that is not a version of this Set", which is true and useless
    # when the Set itself is the thing that does not exist.
    if await sets.get(set_id) is None:
        raise NotFound(f"No Set {set_id}")
    # The one reference on this body that is not a recipe field. Checked here
    # because it is the only place it can arrive: `add_version` inherits and
    # appends, and a comparison against a version of somebody else's Set is a
    # bad request rather than something the append should quietly drop. Every
    # candidate is older by construction — the new version is the highest there
    # is — so only the "same Set" half of the rule applies here.
    compare = body.compares_to_version_id
    if body.prediction and compare is not None:
        if await sets.version_of_set(set_id, compare) is None:
            raise _refusal_bad_compare(set_id, None)
    version = await sets.add_version(set_id, body)
    if version is None:  # pragma: no cover - the Set was checked above
        raise NotFound(f"No Set {set_id}")
    return envelope_response(version.model_dump(mode="json"), status_code=201)


@router.patch(
    "/{set_id}/versions/{version_id}/prediction",
    response_model=ApiResponse[SetVersionRow],
    summary="Say what this version is expected to do differently",
)
async def set_prediction(
    set_id: int, version_id: int, body: VersionPredictionWrite, sets: SetsRepoDep
) -> JSONResponse:
    """Writable only while the version has no shots.

    An empty `prediction` takes the prediction back, comparison and all — which
    is the only way to remove one, and still only before the first shot.
    """
    return _unwrap(await sets.set_prediction(set_id, version_id, body), set_id, version_id)


@router.put(
    "/{set_id}/versions/{version_id}/outcome",
    response_model=ApiResponse[SetVersionRow],
    summary="Grade this version's prediction",
)
async def set_outcome(
    set_id: int, version_id: int, body: VersionOutcomeWrite, sets: SetsRepoDep
) -> JSONResponse:
    """Held, partly held, failed or inconclusive, with the why beside it.

    Recordable only once the version has a prediction and a shot somebody
    labelled Keep or Improve; changeable afterwards as often as you like.
    """
    return _unwrap(await sets.set_outcome(set_id, version_id, body), set_id, version_id)


@router.delete(
    "/{set_id}/versions/{version_id}/outcome",
    response_model=ApiResponse[SetVersionRow],
    summary="Take back the grade on this version's prediction",
)
async def clear_outcome(set_id: int, version_id: int, sets: SetsRepoDep) -> JSONResponse:
    return _unwrap(await sets.clear_outcome(set_id, version_id), set_id, version_id)


@router.post(
    "/{set_id}/rollback",
    response_model=ApiResponse[SetVersionRow],
    status_code=201,
    summary="Go back to an earlier recipe, as a new version",
)
async def rollback(set_id: int, body: RollbackWrite, sets: SetsRepoDep) -> JSONResponse:
    """Append a version whose recipe is `to_version_id`'s.

    Nothing is written to the machine. If the restored version names a
    different profile, the log says so exactly as it does for any other version
    that changes one, and putting that profile on the machine stays a separate,
    deliberate act.
    """
    if await sets.get(set_id) is None:
        raise NotFound(f"No Set {set_id}")
    result = await sets.rollback(set_id, body)
    if result.version is None:
        return _unwrap(result, set_id, body.to_version_id)
    return envelope_response(result.version.model_dump(mode="json"), status_code=201)


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
