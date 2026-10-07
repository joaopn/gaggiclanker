"""``/api/backup`` — download the whole app as one file, and check a file for restoring.

The file is a complete gaggiclanker database with a small manifest table inside.
There is no copy kept on the server: the file goes to the person, and a restore
takes only an uploaded file, as a raw body (not multipart: Starlette spools
multipart outside ``DATA_DIR``, which rules out an atomic rename).

``POST /backup/restore`` only stages and checks; nothing about the app changes
until the apply step.
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated
from urllib.parse import unquote

import structlog
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from gaggiclanker.api.deps import DatabaseDep, EnvSettingsDep
from gaggiclanker.db.backup import BackupManifest, create_export
from gaggiclanker.db.repos.backup_state import BackupStateRepository
from gaggiclanker.db.restore import (
    RestoreCounts,
    RestoreRefused,
    discard_staged,
    stage_upload,
    staged_path,
    validate_staged,
)
from gaggiclanker.infra.envelope import ApiResponse, envelope_response, file_download
from gaggiclanker.infra.request_context import get_request_id

log = structlog.get_logger(__name__)

__all__ = ["router"]

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get(
    "",
    summary="Download everything in the app as one file",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_backup(
    db: DatabaseDep,
    env: EnvSettingsDep,
    include_keys: bool = Query(
        False,
        description=(
            "Put the API keys and tokens in the file, in plain text. The sign-in user and "
            "password hash are always in it."
        ),
    ),
) -> Response:
    export = await create_export(db, env.data_dir, include_keys=include_keys)
    return file_download(
        export.path,
        filename=export.filename,
        cleanup=export.discard,
        request_id=get_request_id(),
    )


class RestoreCheckData(BaseModel):
    """What the preview shows about a staged file. Nothing has changed yet."""

    token: str
    filename: str
    size_bytes: int
    manifest: BackupManifest | None
    in_file: RestoreCounts
    now: RestoreCounts
    keys_in_file: bool
    keys_here: bool


class RestoreDiscardData(BaseModel):
    deleted: bool


def _display_name(raw: str | None) -> str:
    """The name the browser sent, for display only: a base name without control characters."""
    name = re.sub(r"[\x00-\x1f\x7f]", "", unquote(raw or "").replace("\\", "/").rsplit("/", 1)[-1])
    return name.strip()[:200] or "backup.db"


@router.post(
    "/restore",
    response_model=ApiResponse[RestoreCheckData],
    summary="Upload a file to restore and check it (nothing changes yet)",
)
async def check_restore(
    request: Request,
    db: DatabaseDep,
    env: EnvSettingsDep,
    x_filename: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    state = BackupStateRepository(db)
    declared = request.headers.get("content-length")
    token, path = await stage_upload(
        env.data_dir,
        request.stream(),
        declared_size=int(declared) if declared and declared.isdigit() else None,
    )
    try:
        staged = await asyncio.to_thread(validate_staged, path, token)
    except RestoreRefused as refused:
        discard_staged(path)
        log.warning("restore_refused", reason=refused.code)
        raise
    except BaseException:
        discard_staged(path)
        raise
    log.info(
        "restore_validated",
        size_bytes=staged.size_bytes,
        has_manifest=staged.manifest is not None,
        keys_in_file=staged.keys_in_file,
    )
    return envelope_response(
        RestoreCheckData(
            token=token,
            filename=_display_name(x_filename),
            size_bytes=staged.size_bytes,
            manifest=staged.manifest,
            in_file=staged.counts,
            now=await state.counts(),
            keys_in_file=staged.keys_in_file,
            keys_here=await state.keys_held(),
        ).model_dump(mode="json")
    )


@router.delete(
    "/restore/{token}",
    response_model=ApiResponse[RestoreDiscardData],
    summary="Cancel a staged restore: delete the uploaded file",
)
async def cancel_restore(token: str, env: EnvSettingsDep) -> JSONResponse:
    path = staged_path(env.data_dir, token)
    existed = path.exists()
    discard_staged(path)
    return envelope_response(RestoreDiscardData(deleted=existed).model_dump())
