"""`/api/beans` — the bags.

CRUD plus archive and delete. Archive is how a coffee is retired: a finished
bag is still the coffee a hundred shots were pulled with, and
`POST /api/beans/{id}/archive` hides it from the pickers and leaves every
reference intact. `DELETE /api/beans/{id}` is for a bean nobody used — a typo, a
duplicate — and answers 409 while any Set points at it, because Sets cannot be
deleted and a Set whose bean is gone is a Set whose page cannot render.

`GET /api/beans/{id}/similar-sets` is the wizard's evidence query on its own. It
lives here rather than under `/api/starting-points` because it is a fact about a
bean and it costs nothing — the wizard shows those cards before anybody presses
a button that spends money, and "here is what you already know about beans like
this" is worth reading even if nobody asks the model anything.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.deps import BeansRepoDep, DatabaseDep
from gaggiclanker.db.repos.beans import BeanRow, BeanWrite
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import Conflict, NotFound
from gaggiclanker.starting.similar import DEFAULT_LIMIT, SimilarSet, similar_sets

__all__ = ["router"]

router = APIRouter(prefix="/beans", tags=["beans"])


class BeanListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BeanRow]


class SimilarSetsData(BaseModel):
    """What the archive already knows about beans like this one."""

    model_config = ConfigDict(extra="forbid")

    bean_id: int
    #: Echoed back so the card can say "on the Niche" rather than leaving the
    #: reader to remember what they filtered by.
    grinder_id: int | None = None
    items: list[SimilarSet]


@router.get("", response_model=ApiResponse[BeanListData], summary="The beans, freshest first")
async def list_beans(
    beans: BeansRepoDep,
    include_archived: Annotated[bool, Query()] = False,
) -> JSONResponse:
    rows = await beans.list_all(include_archived=include_archived)
    return envelope_response(BeanListData(items=rows).model_dump(mode="json"))


@router.post("", response_model=ApiResponse[BeanRow], status_code=201, summary="Record a bean")
async def create_bean(body: BeanWrite, beans: BeansRepoDep) -> JSONResponse:
    row = await beans.create(body)
    return envelope_response(row.model_dump(mode="json"), status_code=201)


@router.get("/{bean_id}", response_model=ApiResponse[BeanRow], summary="One bean")
async def get_bean(bean_id: int, beans: BeansRepoDep) -> JSONResponse:
    row = await beans.get(bean_id)
    if row is None:
        raise NotFound(f"No bean {bean_id}")
    return envelope_response(row.model_dump(mode="json"))


@router.put("/{bean_id}", response_model=ApiResponse[BeanRow], summary="Edit a bean")
async def update_bean(bean_id: int, body: BeanWrite, beans: BeansRepoDep) -> JSONResponse:
    """A whole-object PUT, not a PATCH.

    The form sends every field it renders, and a bean is a dozen short strings:
    partial-update semantics would buy nothing and would make "clear the
    roaster" indistinguishable from "leave the roaster alone".
    """
    row = await beans.update(bean_id, body)
    if row is None:
        raise NotFound(f"No bean {bean_id}")
    return envelope_response(row.model_dump(mode="json"))


@router.delete(
    "/{bean_id}",
    response_model=ApiResponse[dict[str, bool]],
    summary="Delete a bean no Set uses",
)
async def delete_bean(bean_id: int, beans: BeansRepoDep) -> JSONResponse:
    """A real delete, refused for a bean that is in use.

    Starting-point runs about the bean go with it (their foreign key cascades).
    Nothing else holds a bean id that has to keep resolving: an analysis keeps
    its own snapshot of the facts it was given, and an insight scoped to the id
    simply never matches again, because ids are never reused.
    """
    outcome = await beans.delete(bean_id)
    if not outcome.found:
        raise NotFound(f"No bean {bean_id}")
    if not outcome.deleted:
        noun = "Set uses" if outcome.set_count == 1 else "Sets use"
        raise Conflict(
            f"{outcome.set_count} {noun} this bean. Archive it instead.",
            details={"field": "bean_id", "message": "archive a bean that Sets use"},
        )
    return envelope_response({"deleted": True})


@router.post(
    "/{bean_id}/archive",
    response_model=ApiResponse[BeanRow],
    summary="Hide a finished bag without breaking its Sets",
)
async def archive_bean(bean_id: int, beans: BeansRepoDep) -> JSONResponse:
    row = await beans.set_archived(bean_id, archived=True)
    if row is None:
        raise NotFound(f"No bean {bean_id}")
    return envelope_response(row.model_dump(mode="json"))


@router.post(
    "/{bean_id}/unarchive",
    response_model=ApiResponse[BeanRow],
    summary="Put an archived bean back in the pickers",
)
async def unarchive_bean(bean_id: int, beans: BeansRepoDep) -> JSONResponse:
    row = await beans.set_archived(bean_id, archived=False)
    if row is None:
        raise NotFound(f"No bean {bean_id}")
    return envelope_response(row.model_dump(mode="json"))


@router.get(
    "/{bean_id}/similar-sets",
    response_model=ApiResponse[SimilarSetsData],
    summary="Past Set versions that resemble this bag, best first",
)
async def bean_similar_sets(
    bean_id: int,
    beans: BeansRepoDep,
    db: DatabaseDep,
    grinder_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=10)] = DEFAULT_LIMIT,
) -> JSONResponse:
    """The similar-Set query on its own, for the wizard's first step.

    ``grinder_id`` is a filter rather than a hint: a grind number from a
    different grinder is not weaker evidence, it is meaningless, and putting one
    on a card would invite somebody to dial it. Omitting it lifts the filter,
    which is what a kitchen with no recorded grinder needs.
    """
    bean = await beans.get(bean_id)
    if bean is None:
        raise NotFound(f"No bean {bean_id}")
    items = await similar_sets(
        db,
        roast_level=bean.roast_level,
        process=bean.process,
        origin=bean.origin,
        decaf=bean.decaf,
        grinder_id=grinder_id,
        limit=limit,
    )
    return envelope_response(
        SimilarSetsData(bean_id=bean_id, grinder_id=grinder_id, items=items).model_dump(mode="json")
    )
