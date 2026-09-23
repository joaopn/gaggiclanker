"""`POST /api/import` — load exported shots and profiles from files.

The one write endpoint in the prototype that takes a file rather than JSON, and
the only way a shot the machine has already deleted gets back into the archive.
Multipart because that is what a browser's file picker and `curl -F` both speak,
and because the files are a few tens of kilobytes each and arrive in batches of
hundreds when somebody imports a folder.

Per-file results rather than one status: the interesting answer to "I dropped
two hundred files on it" is which ones did not land and why, and a 400 that
abandoned the batch would throw away the hundred and ninety that were fine.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from gaggiclanker.api.deps import DatabaseDep, EventBusDep
from gaggiclanker.imports.service import (
    MAX_EXPANDED_BYTES,
    ImportFile,
    ImportService,
    ImportSummary,
)
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import BadRequest
from gaggiclanker.infra.sse import SseEvent
from gaggiclanker.sync.engine import PROFILE_UPDATED_EVENT, SHOT_INGESTED_EVENT

__all__ = ["MAX_REQUEST_BYTES", "router"]

router = APIRouter(prefix="/import", tags=["import"])

#: How much one request may carry, in total. A shot export is 30-100 KB, so this
#: is a folder of several hundred of them — and a bound is needed because the
#: files are read into memory to be parsed, on a box with a small heap.
#:
#: The same figure as the service's decompression budget, and taken from it so
#: the two cannot drift: "how much a request may weigh" and "how much a request
#: may expand to" being different numbers is how a 1 MB upload turns into a
#: 64 MB one.
MAX_REQUEST_BYTES = MAX_EXPANDED_BYTES


@router.post(
    "",
    response_model=ApiResponse[ImportSummary],
    summary="Import shot and profile exports",
)
async def import_files(
    db: DatabaseDep,
    events: EventBusDep,
    files: Annotated[
        list[UploadFile],
        File(description="Shot exports, profile exports, or zips containing them."),
    ],
    replace: Annotated[
        bool,
        Form(description="Overwrite shots already in the archive rather than skipping them."),
    ] = False,
) -> JSONResponse:
    """Import every uploaded file and report on each one separately.

    A file is recognised by its content, not its name: a shot export, a profile
    export, a JSON array of profiles, or a zip of any of those. An unreadable
    file is one `failed` row in the results — except an unreadable *shot* that
    still names its id, which is stored quarantined with its bytes, because the
    machine's copy is gone and a parser fix is a re-derive away.
    """
    if not files:
        raise BadRequest(
            "No files uploaded",
            details={"field": "files", "message": "attach at least one file"},
        )

    payloads: list[ImportFile] = []
    total = 0
    for upload in files:
        data = await upload.read()
        total += len(data)
        if total > MAX_REQUEST_BYTES:
            raise BadRequest(
                f"Upload exceeds the {MAX_REQUEST_BYTES // (1024 * 1024)} MB limit for one "
                "request; import the files in smaller batches",
                details={"field": "files", "message": "request too large"},
            )
        payloads.append(ImportFile(filename=upload.filename or "upload", data=data))

    service = ImportService(db)
    summary = await service.import_files(payloads, replace=replace)

    # One event per kind, not one per file: the bus is lossy and an event only
    # ever means "this family is stale, go and re-read" (infra/sse.py), so a
    # two-hundred-file import must not push two hundred of them at a live tab.
    shots = sum(1 for item in summary.items if item.kind == "shot" and item.shot_id is not None)
    profiles = sum(
        1 for item in summary.items if item.kind == "profile" and item.status != "failed"
    )
    if shots:
        events.publish(SseEvent(event=SHOT_INGESTED_EVENT, data={"imported": shots}))
    if profiles:
        events.publish(SseEvent(event=PROFILE_UPDATED_EVENT, data={"imported": profiles}))

    return envelope_response(summary.model_dump(mode="json"))
