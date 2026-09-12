"""`POST /api/import` — the multipart door into the archive.

The service tests cover what an import *does*; these cover the wire: the
envelope, per-file results, the size bound, and that an imported shot is
immediately visible through `GET /api/shots?source=import` like any other.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.api.imports import MAX_REQUEST_BYTES
from gaggiclanker.infra.sse import SseEvent
from gaggiclanker.sync.engine import PROFILE_UPDATED_EVENT, SHOT_INGESTED_EVENT
from tests.imports.helpers import PROFILE_ARRAY_EXPORT, V7_EXPORT, fixture_bytes
from tests.test_api_contract import assert_envelope

SHOT_FIXTURE = "shot-129.json"
PROFILE_FIXTURE = "profile-dCs4AOOcBn.json"
SHOT_129_SAMPLES = 213

type Upload = tuple[str, tuple[str, bytes, str]]


def upload(name: str, data: bytes | None = None) -> Upload:
    return ("files", (name, data if data is not None else fixture_bytes(name), "application/json"))


async def post_import(
    client: httpx.AsyncClient, uploads: list[Upload], **form: Any
) -> httpx.Response:
    data = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in form.items()
    }
    return await client.post("/api/import", files=uploads, data=data)


async def test_importing_the_two_real_files_over_multipart(client: httpx.AsyncClient) -> None:
    response = await post_import(client, [upload(SHOT_FIXTURE), upload(PROFILE_FIXTURE)])

    assert response.status_code == 200
    body = response.json()
    assert_envelope(body, request_id=response.headers["x-request-id"])
    data = body["data"]
    assert data["created"] == 2
    assert data["failed"] == 0
    assert data["machine_id"]

    shot = next(item for item in data["items"] if item["kind"] == "shot")
    profile = next(item for item in data["items"] if item["kind"] == "profile")
    assert shot["status"] == "created"
    assert shot["device_id"] == "000129"
    assert shot["message"] == f"{SHOT_129_SAMPLES} samples"
    assert profile["label"] == "Cremina v2"


async def test_an_imported_shot_is_a_shot_like_any_other(client: httpx.AsyncClient) -> None:
    """The point of the whole chunk: the archive does not care where it came from."""
    created = await post_import(client, [upload(SHOT_FIXTURE)])
    shot_id = created.json()["data"]["items"][0]["shot_id"]

    listing = await client.get("/api/shots", params={"source": "import"})
    detail = await client.get(f"/api/shots/{shot_id}")
    samples = await client.get(f"/api/shots/{shot_id}/samples")
    raw = await client.get(f"/api/shots/{shot_id}/raw")

    assert [row["id"] for row in listing.json()["data"]["items"]] == [shot_id]
    assert detail.json()["data"]["shot"]["execution_score"] == pytest.approx(7.7)
    assert len(detail.json()["data"]["shot"]["phases"]) == 4
    assert detail.json()["data"]["notes"]["dose_out_g"] == pytest.approx(32.1)
    assert samples.json()["data"]["count"] == SHOT_129_SAMPLES
    assert raw.content[:4] == b"SHOT"


async def test_re_importing_reports_skipped_and_replace_updates(
    client: httpx.AsyncClient,
) -> None:
    await post_import(client, [upload(SHOT_FIXTURE)])
    again = await post_import(client, [upload(SHOT_FIXTURE)])
    replaced = await post_import(client, [upload(SHOT_FIXTURE)], replace=True)

    assert again.json()["data"]["skipped"] == 1
    assert replaced.json()["data"]["updated"] == 1
    listing = await client.get("/api/shots")
    assert listing.json()["data"]["total"] == 1


async def test_one_bad_file_does_not_cost_the_batch(client: httpx.AsyncClient) -> None:
    response = await post_import(
        client,
        [
            upload("broken.json", b"{not json"),
            upload(SHOT_FIXTURE),
            upload(PROFILE_FIXTURE),
        ],
    )

    data = response.json()["data"]
    assert response.status_code == 200
    assert data["created"] == 2
    assert data["failed"] == 1
    assert data["items"][0]["filename"] == "broken.json"
    assert "not JSON" in data["items"][0]["message"]


async def test_a_zip_of_exports_imports(client: httpx.AsyncClient) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("history/shot-129.json", fixture_bytes(SHOT_FIXTURE))
        archive.writestr("history/profiles.json", fixture_bytes(PROFILE_ARRAY_EXPORT))

    response = await post_import(
        client, [("files", ("exports.zip", buffer.getvalue(), "application/zip"))]
    )

    data = response.json()["data"]
    assert data["created"] == 3  # one shot, two profiles
    assert {item["filename"] for item in data["items"]} == {
        "exports.zip:history/shot-129.json",
        "exports.zip:history/profiles.json",
    }


async def test_a_v7_export_imports_over_the_wire(client: httpx.AsyncClient) -> None:
    response = await post_import(client, [upload(V7_EXPORT)])

    shot_id = response.json()["data"]["items"][0]["shot_id"]
    detail = await client.get(f"/api/shots/{shot_id}")
    assert detail.json()["data"]["shot"]["slog_version"] == 7
    assert detail.json()["data"]["shot"]["brew_delay_ms"] == 900


async def test_an_unknown_machine_is_a_404_in_the_envelope(client: httpx.AsyncClient) -> None:
    response = await post_import(client, [upload(SHOT_FIXTURE)], machine_id=999)

    assert response.status_code == 404
    body = response.json()
    assert_envelope(body)
    assert body["error"]["code"] == "NOT_FOUND"


async def test_a_request_with_no_files_is_a_400(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/import")

    assert response.status_code == 400
    assert_envelope(response.json())


async def test_an_oversized_upload_is_refused_with_the_limit_named(
    client: httpx.AsyncClient,
) -> None:
    """The files are parsed in memory on a box with a small heap, so there is a bound."""
    oversized = json.dumps({"samples": [], "pad": "x" * (MAX_REQUEST_BYTES + 1)}).encode()

    response = await post_import(client, [upload("huge.json", oversized)])

    assert response.status_code == 400
    body = response.json()
    assert_envelope(body)
    assert "50 MB" in body["error"]["message"]


async def test_the_import_tells_open_tabs_to_re_read(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """One event per kind, never one per file: the bus drops events under load."""
    seen: list[SseEvent] = []
    app.state.events.publish = seen.append

    await post_import(client, [upload(SHOT_FIXTURE), upload(PROFILE_FIXTURE)])

    assert [event.event for event in seen] == [SHOT_INGESTED_EVENT, PROFILE_UPDATED_EVENT]
    assert seen[0].data == {"imported": 1}
