"""`/api/shots` — the archive, listed, detailed, sampled and downloadable.

Four shapes for four jobs, because one "shot" payload cannot serve all of them:

* the **list** row is small enough to send fifty of, and carries what a table
  column needs — time, profile, duration, volume, score, the quarantine flag;
* the **detail** row adds the derived phases and diagnostics blobs;
* **samples** are the curve, optionally thinned for a sparkline;
* **raw** is the `.slog` bytes themselves, which is the only endpoint here that
  is not in the response envelope (see
  :func:`~gaggiclanker.infra.envelope.binary_response`).

The web UI builds the real shot page on these. Everything is read-only.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.analyzer.service import analysis_task_name
from gaggiclanker.api.deps import (
    AnalysesRepoDep,
    AnalyzerServiceDep,
    JudgementsRepoDep,
    NotesRepoDep,
    SetsRepoDep,
    ShotsRepoDep,
)
from gaggiclanker.db.repos.analyses import AnalysisRow
from gaggiclanker.db.repos.judgements import JudgementWrite, ShotJudgementRow
from gaggiclanker.db.repos.notes import DeviceShotNotesRow
from gaggiclanker.db.repos.sets import SetVersionRow
from gaggiclanker.db.repos.shots import ShotDetailRow, ShotListRow, ShotSampleRow
from gaggiclanker.infra.envelope import ApiResponse, binary_response, envelope_response
from gaggiclanker.infra.errors import BadRequest, NotFound, Unprocessable
from gaggiclanker.infra.request_context import get_request_id
from gaggiclanker.sync.engine import downsample

__all__ = ["router"]

router = APIRouter(prefix="/shots", tags=["shots"])

#: A page bigger than this is somebody scraping rather than browsing, and each
#: row carries a diagnostics-derived score the list query has to read.
MAX_LIMIT = 500

#: The sorts the list accepts, spelled once and shared with the repository so
#: the OpenAPI enum and the SQL cannot drift apart.
type SortKey = Literal["started_at", "execution_score", "duration", "rating"]


class ShotListData(BaseModel):
    """One page of shots.

    ``total`` counts the *filtered* set, not the archive, so a UI can render "47
    quarantined shots" from the same response that draws the first page of them.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ShotListRow]
    total: int
    limit: int
    offset: int | None = None
    next_cursor: str | None = None


class ShotDetailData(BaseModel):
    """One shot in full: the row, its blobs, the device's notes and your verdict.

    ``notes`` and ``judgement`` are both here and are different things. The
    notes are a read-only mirror of what the *machine's* UI recorded; the
    judgement is the archive's own, editable, and seeded from the notes the
    first time a shot arrives with them. Showing both side by side is what makes
    a disagreement between them visible.
    """

    model_config = ConfigDict(extra="forbid")

    shot: ShotDetailRow
    notes: DeviceShotNotesRow | None = None
    judgement: ShotJudgementRow | None = None
    #: The Set version this shot is attached to, resolved. NULL is `needs_set`.
    set_version: SetVersionRow | None = None
    #: Every analysis of this shot, newest first. Sent with the shot
    #: rather than fetched separately because the panel is on this page and a
    #: second request for a list that is almost always empty or one row long is
    #: a round trip for nothing.
    analyses: list[AnalysisRow] = Field(default_factory=list)


class ShotSamplesData(BaseModel):
    """The curve. ``downsampled`` says whether what you got is the whole thing."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    count: int
    total: int
    #: The header's own `sampleInterval`, not the nominal 250 ms. A chart that
    #: assumed the nominal figure would draw a shot with gaps in it as a shot
    #: that ran short (firmware report §2.2: the samples carry real `millis()`).
    sample_interval_ms: int | None = None
    downsampled: bool = False
    samples: list[ShotSampleRow]


@router.get(
    "",
    response_model=ApiResponse[ShotListData],
    summary="List shots, newest first",
)
async def list_shots(
    shots: ShotsRepoDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int | None, Query(ge=0)] = None,
    cursor: Annotated[str | None, Query()] = None,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: Annotated[str | None, Query()] = None,
    profile_version_id: Annotated[int | None, Query()] = None,
    machine_id: Annotated[int | None, Query()] = None,
    set_id: Annotated[int | None, Query()] = None,
    set_version_id: Annotated[int | None, Query()] = None,
    needs_set: Annotated[bool | None, Query()] = None,
    quarantined: Annotated[bool | None, Query()] = None,
    include_deleted: Annotated[bool, Query()] = True,
    source: Annotated[Literal["device", "import"] | None, Query()] = None,
    min_score: Annotated[float | None, Query(ge=0, le=10)] = None,
    max_score: Annotated[float | None, Query(ge=0, le=10)] = None,
    min_rating: Annotated[int | None, Query(ge=0, le=5)] = None,
    sort: Annotated[SortKey, Query()] = "started_at",
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
) -> JSONResponse:
    """Paged, filtered, newest first.

    Two paginations, and they do not mix: ``offset`` is for a table with page
    numbers, ``cursor`` is keyset paging over `(started_at, id)` and is the one
    that stays correct while the sync engine inserts rows underneath the reader
    — which on this appliance it is doing all the time. Passing both is a 400
    rather than a quiet precedence rule.

    ``from``/``to`` are ISO timestamps compared against ``started_at``; a shot
    from a machine whose clock never synced has none and is excluded by any date
    filter, which is the honest answer.

    ``set_id`` and ``set_version_id`` narrow to one Set or one of its versions;
    ``needs_set=1`` is the inbox — shots the archive could not attach to a Set
    on its own and is waiting for an answer on. Quarantined shots are excluded
    from it: their bytes never parsed, so there is nothing to judge and the
    count would never reach zero.

    ``sort`` takes one of a fixed set of names — a sort column pasted out of a
    query string is an injection — and only the default one supports ``cursor``,
    because the cursor encodes that key. "Worst shots first" is an offset page,
    which is what such a view wants anyway.
    """
    if cursor is not None and offset is not None:
        raise BadRequest(
            "Use either `cursor` or `offset`, not both",
            details={"field": "cursor", "message": "cursor and offset are alternatives"},
        )
    try:
        page = await shots.list_shots(
            limit=limit,
            offset=offset,
            cursor=cursor,
            start_from=from_,
            start_to=to,
            profile_version_id=profile_version_id,
            machine_id=machine_id,
            set_id=set_id,
            set_version_id=set_version_id,
            needs_set=needs_set,
            quarantined=quarantined,
            include_deleted_on_device=include_deleted,
            source=source,
            min_score=min_score,
            max_score=max_score,
            min_rating=min_rating,
            sort=sort,
            descending=order == "desc",
        )
    except ValueError as exc:
        field = "cursor" if "cursor" in str(exc) else "sort"
        raise BadRequest(str(exc), details={"field": field, "message": str(exc)}) from exc

    return envelope_response(
        ShotListData(
            items=page.items,
            total=page.total,
            limit=limit,
            offset=offset,
            next_cursor=page.next_cursor,
        ).model_dump(mode="json")
    )


@router.get(
    "/{shot_id}",
    response_model=ApiResponse[ShotDetailData],
    summary="One shot with its phases, diagnostics and device notes",
)
async def get_shot(
    shot_id: int,
    shots: ShotsRepoDep,
    notes: NotesRepoDep,
    judgements: JudgementsRepoDep,
    sets: SetsRepoDep,
    analyses: AnalysesRepoDep,
) -> JSONResponse:
    shot = await shots.get(shot_id)
    if shot is None:
        raise NotFound(f"No shot {shot_id}")
    version = None if shot.set_version_id is None else await sets.get_version(shot.set_version_id)
    return envelope_response(
        ShotDetailData(
            shot=shot,
            notes=await notes.get(shot_id),
            judgement=await judgements.get(shot_id),
            set_version=version,
            analyses=await analyses.for_shot(shot_id),
        ).model_dump(mode="json")
    )


@router.get(
    "/{shot_id}/samples",
    response_model=ApiResponse[ShotSamplesData],
    summary="The shot's samples in t_ms order",
)
async def get_shot_samples(
    shot_id: int,
    shots: ShotsRepoDep,
    downsample_to: Annotated[int | None, Query(alias="downsample", ge=1, le=10_000)] = None,
) -> JSONResponse:
    """Every sample, in `t_ms` order, with all 14 fields plus `phase_number`.

    ``?downsample=N`` returns at most N evenly spaced points, first and last
    always kept. That is for sparklines: a list of two hundred shots must not
    pull two hundred hundred-point curves over the wire, and an *averaged* curve
    would hide exactly the spikes a shape is being scanned for.
    """
    shot = await shots.get(shot_id)
    if shot is None:
        raise NotFound(f"No shot {shot_id}")
    rows = await shots.samples(shot_id)
    selected = rows if downsample_to is None else downsample(rows, downsample_to)
    return envelope_response(
        ShotSamplesData(
            shot_id=shot_id,
            count=len(selected),
            total=len(rows),
            sample_interval_ms=shot.sample_interval_ms,
            downsampled=len(selected) != len(rows),
            samples=selected,
        ).model_dump(mode="json")
    )


@router.get(
    "/{shot_id}/notes",
    response_model=ApiResponse[DeviceShotNotesRow],
    summary="The device's own notes for this shot",
)
async def get_shot_notes(shot_id: int, notes: NotesRepoDep) -> JSONResponse:
    row = await notes.get(shot_id)
    if row is None:
        raise NotFound(f"No device notes for shot {shot_id}")
    return envelope_response(row.model_dump(mode="json"))


@router.get(
    "/{shot_id}/raw",
    summary="Download the stored .slog bytes",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def get_shot_raw(shot_id: int, shots: ShotsRepoDep) -> Response:
    """The bytes the machine wrote, byte for byte.

    Deliberately outside the response envelope: these are the archive's product
    — the thing every derived column can be rebuilt from — and base64 in a JSON
    body would mean a client has to decode before it can compare them with the
    device's own copy.
    """
    row = await shots.get(shot_id)
    if row is None:
        raise NotFound(f"No shot {shot_id}")
    raw = await shots.raw_slog(shot_id)
    if raw is None:  # pragma: no cover - raw_slog is NOT NULL
        raise NotFound(f"No stored bytes for shot {shot_id}")
    return binary_response(
        raw,
        media_type="application/octet-stream",
        filename=f"{row.device_id}.slog",
        request_id=get_request_id(),
    )


# ---------------------------------------------------------------------------
# The two things a person writes about a shot: what they thought of it, and
# which Set it belongs to. Everything above this line is read-only.
# ---------------------------------------------------------------------------


class SetVersionAssignment(BaseModel):
    """`PUT /api/shots/{id}/set-version`: which Set version this shot belongs to.

    A body with an explicit `null` rather than a DELETE, because "this shot was
    not part of any Set" is a statement the user makes, and it lands in the same
    place a correction does.
    """

    model_config = ConfigDict(extra="forbid")

    set_version_id: int | None = None


@router.put(
    "/{shot_id}/judgement",
    response_model=ApiResponse[ShotJudgementRow],
    summary="Record or replace what you thought of this cup",
)
async def put_judgement(
    shot_id: int, body: JudgementWrite, shots: ShotsRepoDep, judgements: JudgementsRepoDep
) -> JSONResponse:
    """An upsert. The verdict is one row per shot and the form sends all of it.

    Saving here marks the row as the user's: a judgement seeded from the
    machine's notes card loses its `seeded_from_device_note` flag the moment
    somebody edits it, and the sync engine only ever *creates* judgements that
    do not exist. That is what makes "user edits are never overwritten by sync"
    a property of the data rather than of a code path somebody has to remember.
    """
    if await shots.get(shot_id) is None:
        raise NotFound(f"No shot {shot_id}")
    row = await judgements.upsert(shot_id, body)
    return envelope_response(row.model_dump(mode="json"))


@router.delete(
    "/{shot_id}/judgement",
    response_model=ApiResponse[dict[str, bool]],
    summary="Withdraw a verdict",
)
async def delete_judgement(shot_id: int, judgements: JudgementsRepoDep) -> JSONResponse:
    """Deleting is not "rating zero": it puts the shot back to unjudged.

    Worth having as its own verb, because a judgement seeded from a device note
    the user disagrees with should be removable without inventing a rating.
    """
    if not await judgements.delete(shot_id):
        raise NotFound(f"No judgement for shot {shot_id}")
    return envelope_response({"deleted": True})


@router.put(
    "/{shot_id}/set-version",
    response_model=ApiResponse[ShotDetailRow],
    summary="Assign this shot to a Set version, or detach it",
)
async def put_set_version(
    shot_id: int, body: SetVersionAssignment, shots: ShotsRepoDep, sets: SetsRepoDep
) -> JSONResponse:
    """The correction path, so unlike auto-assignment it overwrites.

    Auto-assignment only ever fills a NULL (`db/repos/sets.py`), which is what
    keeps a hand correction from being undone by the next sync pass. This is the
    hand.
    """
    if await shots.get(shot_id) is None:
        raise NotFound(f"No shot {shot_id}")
    if not await sets.assign_shot(shot_id, body.set_version_id):
        raise Unprocessable(
            f"No Set version {body.set_version_id}",
            details={"field": "set_version_id", "message": "that Set version does not exist"},
        )
    row = await shots.get(shot_id)
    if row is None:  # pragma: no cover - checked above, inside the same request
        raise NotFound(f"No shot {shot_id}")
    return envelope_response(row.model_dump(mode="json"))


class AnalysisRequest(BaseModel):
    """`POST /api/shots/{id}/analyses`: run one, optionally on a named model.

    `force` is what makes a second press of the button mean something. Without
    it a shot that already has a successful analysis answers with that one,
    because the common accidental double-click should not spend a second call
    on a question that is already answered.
    """

    model_config = ConfigDict(extra="forbid")

    #: Overrides `modelAnalysis` for this run only. Empty takes the setting.
    model: str = ""
    force: bool = False


class AnalysisListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AnalysisRow]


@router.get(
    "/{shot_id}/analyses",
    response_model=ApiResponse[AnalysisListData],
    summary="Every analysis of this shot, newest first",
)
async def list_analyses(shot_id: int, analyses: AnalysesRepoDep) -> JSONResponse:
    return envelope_response(
        AnalysisListData(items=await analyses.for_shot(shot_id)).model_dump(mode="json")
    )


@router.post(
    "/{shot_id}/analyses",
    response_model=ApiResponse[AnalysisRow],
    status_code=202,
    summary="Queue an analysis of this shot",
)
async def run_analysis(
    shot_id: int,
    body: AnalysisRequest,
    request: Request,
    shots: ShotsRepoDep,
    analyses: AnalysesRepoDep,
    analyzer: AnalyzerServiceDep,
    wait: Annotated[bool, Query()] = False,
) -> JSONResponse:
    """Queue the work and answer with the `running` row. 202, not 201.

    The provider call takes thirty seconds to two minutes and does **not** run
    inside this request: `docker stop` allows ten seconds, and a request holding
    a call that long is killed mid-flight with the browser still waiting. It
    goes to the app's task registry instead, the row is the handle, and the LLM
    stream carries `analysis.started` / `analysis.finished` for the page to
    follow.

    Idempotent per shot. A second tab pressing the button — or this tab pressing
    it twice — gets the running row back rather than a second call, because the
    registry name `analysis:<id>` can only be held once.

    ``?wait=1`` blocks until the work is finished and answers with the final
    row. It exists for tests and for `curl`; a browser should follow the stream.

    A **provider failure still answers 2xx.** The row exists, it says `failed`
    and it carries the error code; turning that into a 502 would leave the
    client an error and no id, and the row it could not see is the one thing
    that explains what happened.
    """
    shot = await shots.get(shot_id)
    if shot is None:
        raise NotFound(f"No shot {shot_id}")
    if shot.quarantined:
        raise Unprocessable(
            f"Shot {shot.device_id} is quarantined",
            details={
                "field": "shot_id",
                "message": (
                    "Its bytes never parsed, so there are no diagnostics to analyse. "
                    "The raw file is still stored and a parser fix can re-derive it."
                ),
            },
        )

    # The accidental double-click should not spend a call on a question that is
    # already answered; `force` is what makes a deliberate second press mean
    # something. 200, because nothing was accepted.
    previous = await analyses.latest_for_shot(shot_id)
    if previous is not None and previous.status == "ok" and not body.force:
        return envelope_response(previous.model_dump(mode="json"))

    row, _started = await analyzer.start(
        shot_id, tasks=request.app.state.tasks, model=body.model or None
    )
    if wait:
        row = await _awaited(request, analysis_task_name(shot_id), analyses, row)
    return envelope_response(row.model_dump(mode="json"), status_code=202)


async def _awaited(
    request: Request, task_name: str, analyses: AnalysesRepoDep, row: AnalysisRow
) -> AnalysisRow:
    """Wait for a queued task and re-read the row. ``?wait=1`` only.

    A task that has already finished is not in the registry any more, which is
    not an error — it is the whole point of the name being released on
    completion — so a missing task means "read the row again".
    """
    task = request.app.state.tasks.get(task_name)
    if task is not None:
        with suppress(asyncio.CancelledError):
            await asyncio.shield(task)
    return await analyses.get(row.id) or row
