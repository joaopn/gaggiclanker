"""``POST /api/backup`` — write a consistent copy of the database.

Under the bind mount, so the file is on the host the moment the call returns
and a container recreate cannot take it away.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gaggiclanker.api.deps import DatabaseDep, EnvSettingsDep
from gaggiclanker.db.backup import create_backup
from gaggiclanker.infra.envelope import ApiResponse, envelope_response

__all__ = ["router"]

router = APIRouter(prefix="/backup", tags=["backup"])


class BackupData(BaseModel):
    """Where the backup was written."""

    filename: str
    path: str
    size_bytes: int
    created_at: datetime


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
