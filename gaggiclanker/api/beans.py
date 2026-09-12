"""`/api/beans` — the bags.

CRUD plus archive. No delete: a finished bag is still the bag a hundred shots
were pulled with, and a Set pointing at a deleted bean would be a Set whose page
cannot render. `POST /api/beans/{id}/archive` hides it from the pickers and
leaves every reference intact.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.deps import BeansRepoDep
from gaggiclanker.db.repos.beans import BeanRow, BeanWrite
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import NotFound

__all__ = ["router"]

router = APIRouter(prefix="/beans", tags=["beans"])


class BeanListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BeanRow]


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
