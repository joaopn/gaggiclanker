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
from fastapi import APIRouter, BackgroundTasks, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from gaggiclanker.api.deps import DatabaseDep, EnvSettingsDep
from gaggiclanker.db.backup import BackupManifest, create_export
from gaggiclanker.db.repos.backup_state import BackupStateRepository
from gaggiclanker.db.restore import (
    PendingRestore,
    RestoreCounts,
    RestoreRefused,
    discard_staged,
    prepare_staged,
    stage_upload,
    staged_path,
    validate_staged,
)
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.device.connection import DeviceConnection
from gaggiclanker.infra.envelope import ApiResponse, envelope_response, file_download
from gaggiclanker.infra.errors import Conflict, NotFound
from gaggiclanker.infra.request_context import get_request_id
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.sync.engine import SyncEngine

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


def refuse_while_pending(app: FastAPI) -> None:
    """Once an apply (or a reset) is accepted the work it prepared is the one the restart does.

    A cancel or a new upload then would delete the prepared file, the swap would fail, and the
    app would come back on the old data while the page said "Restoring…". The restore and the
    reset exclude each other for the same reason: each ends in the one restart.
    """
    if app.state.restore_pending is not None:
        raise Conflict("A restore is already under way.", code="RESTORE_PENDING")
    if app.state.reset_pending:
        raise Conflict("A reset is already under way.", code="RESET_PENDING")


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
    refuse_while_pending(request.app)
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
async def cancel_restore(token: str, request: Request, env: EnvSettingsDep) -> JSONResponse:
    refuse_while_pending(request.app)
    path = staged_path(env.data_dir, token)
    existed = path.exists()
    discard_staged(path)
    return envelope_response(RestoreDiscardData(deleted=existed).model_dump())


class RestoreApplyData(BaseModel):
    restarting: bool


def refuse_while_busy(app: FastAPI, *, code: str) -> None:
    """The 409 both restart routes give while work that a restart would cut off is running."""
    if work_in_flight(app):
        raise Conflict(
            "A sync, a chat answer or a review is running. Try again when it finishes.",
            code=code,
        )


def work_in_flight(app: FastAPI) -> bool:
    """Whether anything is running that a restart would cut off.

    The app's shared registry (reviews, chat runs, starting points, pattern runs, a Claude
    Code install), a sync pass or profile-list write on the machine connection, and the
    Claude Code installer's own job. The connection's long-lived loops are not work in
    flight: the shutdown stops them in its usual order.
    """
    tasks: TaskRegistry = app.state.tasks
    connection: DeviceConnection[SyncEngine] = app.state.connection
    return len(tasks) > 0 or connection.busy() is not None or app.state.claude_cli.running


@router.post(
    "/restore/{token}/apply",
    response_model=ApiResponse[RestoreApplyData],
    status_code=202,
    summary="Replace everything with the staged file and restart the app",
)
async def apply_restore(
    token: str,
    request: Request,
    background: BackgroundTasks,
    db: DatabaseDep,
    env: EnvSettingsDep,
) -> JSONResponse:
    app: FastAPI = request.app
    path = staged_path(env.data_dir, token)
    if not path.exists():
        raise NotFound("There is no staged file to restore. Choose the file again.")
    # No await between this check and the mark: a second apply, or a second tab, finds the
    # restore already pending and is refused instead of racing the first.
    refuse_while_pending(app)
    try:
        refuse_while_busy(app, code="RESTORE_BUSY")
    except Conflict:
        # The upload is stale by the time the work ends; asking for the file again is cheap
        # next to a staged copy of the archive sitting in the data directory indefinitely.
        discard_staged(path)
        raise
    app.state.restore_pending = PendingRestore(path=path, schema_version="")
    try:
        live_settings = await SettingsRepository(db).get_all()
        version = await asyncio.to_thread(prepare_staged, path, live_settings)
    except BaseException:
        # Nothing was changed but the staged copy: the app goes on as it was.
        app.state.restore_pending = None
        log.error("restore_prepare_failed", exc_info=True)
        raise
    app.state.restore_pending = PendingRestore(path=path, schema_version=version)
    log.info("restore_applying", schema_version=version)
    # After the answer is sent: the page has to read "restarting" before the process goes.
    # Shutdown cancels whatever else starts in the meantime (no new refusal is added).
    background.add_task(app.state.terminate_process)
    return envelope_response(RestoreApplyData(restarting=True).model_dump(), status_code=202)
