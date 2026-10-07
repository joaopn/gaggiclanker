"""``/api/backup`` — download the whole app as one file.

The file is a complete gaggiclanker database with a small manifest table inside;
restoring one is :mod:`gaggiclanker.api.restore`. There is no copy kept on the
server: the file goes to the person, and a restore takes only an uploaded file.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response

from gaggiclanker.api.deps import DatabaseDep, EnvSettingsDep
from gaggiclanker.db.backup import create_export
from gaggiclanker.infra.envelope import file_download
from gaggiclanker.infra.request_context import get_request_id

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
