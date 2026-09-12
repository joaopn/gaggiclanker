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

from typing import Annotated, NoReturn

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.deps import (
    BeansRepoDep,
    GrindersRepoDep,
    JudgementsRepoDep,
    MachinesRepoDep,
    ProfilesRepoDep,
    SetsRepoDep,
    ShotsRepoDep,
)
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

__all__ = ["router"]

router = APIRouter(prefix="/sets", tags=["sets"])

#: How many shots a Set page loads. A Set that has run past this is a Set worth
#: a filtered shots list, which the page links to.
SHOTS_PER_SET = 500


class SetCreate(BaseModel):
    """`POST /api/sets`: the identity and the first recipe, in one request."""

    model_config = ConfigDict(extra="forbid")

    name: str
    bean_id: int
    machine_id: int
    grinder_id: int | None = None
    #: Version 1. Everything on it is optional — a Set can start as "this bean,
    #: this grinder, we will see" — and the wizard fills it in.
    version: SetVersionWrite = SetVersionWrite()
    #: Whether this becomes the machine's active Set. True by default: a Set is
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
    machine_id: Annotated[int | None, Query()] = None,
) -> JSONResponse:
    rows = await sets.list_sets(include_archived=include_archived, machine_id=machine_id)
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
    machines: MachinesRepoDep,
    profiles: ProfilesRepoDep,
) -> JSONResponse:
    """Create the Set and version 1 atomically.

    Every reference is checked here rather than left to the foreign keys. A
    failed key raises `IntegrityError` from inside the transaction, which the
    envelope can only report as an internal error — a 500 on a request whose
    only problem is a stale id in a dropdown, and with nothing in the body
    saying which of the four ids was wrong. Each check answers that instead:
    422, with `details.field` naming the one at fault.
    """
    # A null grinder and a null profile are legitimate — a Set can name neither —
    # so only a stated id is looked up.
    if await beans.get(body.bean_id) is None:
        _missing("bean_id", body.bean_id, "bean")
    if await machines.get(body.machine_id) is None:
        _missing("machine_id", body.machine_id, "machine")
    if body.grinder_id is not None and await grinders.get(body.grinder_id) is None:
        _missing("grinder_id", body.grinder_id, "grinder")
    profile_version_id = body.version.profile_version_id
    if profile_version_id is not None and await profiles.get_version(profile_version_id) is None:
        _missing("version.profile_version_id", profile_version_id, "profile version")

    row = await sets.create(
        SetWrite(
            name=body.name,
            bean_id=body.bean_id,
            machine_id=body.machine_id,
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
    """Switches the flag off the machine's previous Set. Archives nothing.

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
