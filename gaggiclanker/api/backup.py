"""``/api/backup`` — write a consistent copy of the database, and list the copies.

Under the bind mount, so the file is on the host the moment the call returns
and a container recreate cannot take it away.

Restore stays a file copy rather than an endpoint: a running server cannot swap
the database out from under its own open connection. `GET` exists so an operator
can see what there is to copy without a shell in the container.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gaggiclanker.api.deps import DatabaseDep, EnvSettingsDep
from gaggiclanker.db.backup import create_backup, list_backups
from gaggiclanker.infra.envelope import ApiResponse, envelope_response

__all__ = ["router"]

router = APIRouter(prefix="/backup", tags=["backup"])


class BackupData(BaseModel):
    """Where the backup was written."""

    filename: str
    path: str
    size_bytes: int
    created_at: datetime


class BackupListData(BaseModel):
    """The backups on disk, newest first, and where they live."""

    directory: str
    items: list[BackupData]


@router.get("", response_model=ApiResponse[BackupListData], summary="List the backups on disk")
async def get_backups(env: EnvSettingsDep) -> JSONResponse:
    results = await list_backups(env.backups_dir)
    return envelope_response(
        BackupListData(
            directory=str(env.backups_dir),
            items=[
                BackupData(
                    filename=result.filename,
                    path=str(result.path),
                    size_bytes=result.size_bytes,
                    created_at=result.created_at,
                )
                for result in results
            ],
        ).model_dump(mode="json")
    )


@router.post("", response_model=ApiResponse[BackupData], summary="Back up the database")
async def post_backup(db: DatabaseDep, env: EnvSettingsDep) -> JSONResponse:
    result = await create_backup(db, env.backups_dir)
    return envelope_response(
        BackupData(
            filename=result.filename,
            path=str(result.path),
            size_bytes=result.size_bytes,
            created_at=result.created_at,
        ).model_dump(mode="json"),
        status_code=201,
    )
