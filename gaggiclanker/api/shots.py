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

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.deps import NotesRepoDep, ShotsRepoDep
from gaggiclanker.db.repos.notes import DeviceShotNotesRow
from gaggiclanker.db.repos.shots import ShotDetailRow, ShotListRow, ShotSampleRow
from gaggiclanker.infra.envelope import ApiResponse, binary_response, envelope_response
from gaggiclanker.infra.errors import BadRequest, NotFound
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
    """One shot in full: the row, its phases, its diagnostics and the device's notes."""

    model_config = ConfigDict(extra="forbid")

    shot: ShotDetailRow
    notes: DeviceShotNotesRow | None = None


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
async def get_shot(shot_id: int, shots: ShotsRepoDep, notes: NotesRepoDep) -> JSONResponse:
    shot = await shots.get(shot_id)
    if shot is None:
        raise NotFound(f"No shot {shot_id}")
    return envelope_response(
        ShotDetailData(shot=shot, notes=await notes.get(shot_id)).model_dump(mode="json")
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
