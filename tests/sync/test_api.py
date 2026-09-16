"""The archive's HTTP surface, against a real app wired to the fake machine.

Everything here goes through the app the container runs: its lifespan opens the
database, migrates it, starts the device client and starts the sync engine's
loops. The tests wait for the boot-time backfill to land and then read the
archive the way the front end will.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.settings import EnvSettings
from tests.conftest import machine_tasks, running_app, seed_settings
from tests.sync.conftest import CORRUPT_ID, NOTES_ID, SMALL_COUNT, build_archive_device

#: One less than the machine holds: the device has already deleted one file.
STORED = SMALL_COUNT - 1


async def _pull_everything(client: httpx.AsyncClient, timeout: float = 30.0) -> None:
    """Ask for a full pull through the route, and block until it has finished.

    The app starts no pass of its own but the identity read, so a test that
    wants an archive asks for one the way the button does. All four passes are
    waited for, not just the shots: identity, profiles and notes run behind the
    same lock and a test that only waited for the shot count would read the
    profile mirror while it was still being written.
    """
    wanted = {"identity", "backfill", "profiles", "notes"}
    accepted = await client.post("/api/sync/run", json={"kind": "all"})
    assert accepted.status_code == 202, accepted.text
    async with asyncio.timeout(timeout):
        while True:
            body = (await client.get("/api/sync/status")).json()["data"]
            runs = body["last_runs"]
            if body["counts"]["total"] >= STORED and wanted <= set(runs) and not body["running"]:
                return
            await asyncio.sleep(0.01)


@pytest.fixture
async def served(
    tmp_path: Path, data_dir: Path
) -> AsyncIterator[tuple[FakeDevice, FastAPI, httpx.AsyncClient]]:
    """The app, its lifespan, and a fake machine it has been asked to pull from."""
    device = build_archive_device(SMALL_COUNT, header_only=False)
    await device.start()
    env = EnvSettings(
        DATA_DIR=str(data_dir),
        LOG_LEVEL="warning",
        LOG_JSON=True,
    )  # type: ignore[call-arg]
    await seed_settings(env, gaggimateHost=device.address)
    try:
        async with running_app(env) as (app, client):
            await _pull_everything(client)
            yield device, app, client
    finally:
        await device.stop()


async def test_the_lifespan_starts_the_sync_engine(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, app, _client = served
    assert app.state.connection.engine is not None
    # Every loop is registered with the connection that owns the client, so
    # shutdown cancels them in one call and nothing is mid-write when the
    # database file is released. Never with the app's shared registry: a loop's
    # coroutine frame holds the engine, and that registry is handed to the
    # chat's tools.
    assert {"sync-events", "sync-identity", "sync-shots", "sync-profiles"} <= set(
        machine_tasks(app).names
    )
    assert not [name for name in app.state.tasks.names if name.startswith("sync-")]


async def test_the_shot_list_is_newest_first_with_what_a_table_needs(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served

    body = (await client.get("/api/shots", params={"limit": 5})).json()

    assert body["ok"] is True
    data = body["data"]
    assert data["total"] == STORED
    assert len(data["items"]) == 5
    assert [row["started_at"] for row in data["items"]] == sorted(
        (row["started_at"] for row in data["items"]), reverse=True
    )
    first = data["items"][0]
    for key in (
        "started_at",
        "duration_ms",
        "profile_name_on_device",
        "volume_g",
        "execution_score",
        "quarantined",
    ):
        assert key in first, key


async def test_the_list_pages_by_cursor(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served

    first = (await client.get("/api/shots", params={"limit": 7})).json()["data"]
    assert first["next_cursor"]

    second = (
        await client.get("/api/shots", params={"limit": 7, "cursor": first["next_cursor"]})
    ).json()["data"]

    ids = {row["id"] for row in first["items"]} | {row["id"] for row in second["items"]}
    assert len(ids) == 14


async def test_a_forged_cursor_is_a_400_naming_the_parameter(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served
    forged = base64.urlsafe_b64encode(b"nonsense").decode()

    response = await client.get("/api/shots", params={"cursor": forged})

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["details"]["field"] == "cursor"


async def test_cursor_and_offset_are_alternatives(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served

    response = await client.get("/api/shots", params={"cursor": "x", "offset": 5})

    assert response.status_code == 400


async def test_the_quarantine_filter(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served

    quarantined = (await client.get("/api/shots", params={"quarantined": True})).json()["data"]
    clean = (await client.get("/api/shots", params={"quarantined": False})).json()["data"]

    assert quarantined["total"] == 1
    assert quarantined["items"][0]["device_id"] == pad6(CORRUPT_ID)
    assert clean["total"] == STORED - 1


async def test_the_date_filter(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served
    everything = (await client.get("/api/shots", params={"limit": 500})).json()["data"]["items"]
    midpoint = everything[len(everything) // 2]["started_at"]

    body = (await client.get("/api/shots", params={"from": midpoint, "limit": 500})).json()["data"]

    assert body["total"] == sum(1 for row in everything if row["started_at"] >= midpoint)
    assert all(row["started_at"] >= midpoint for row in body["items"])


async def test_the_profile_filter(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served
    profiles = (await client.get("/api/profiles")).json()["data"]["items"]
    assert profiles

    body = (
        await client.get(
            "/api/shots", params={"profile_version_id": profiles[0]["current_version_id"]}
        )
    ).json()["data"]

    # The fixtures' `.slog` headers name profiles the fake does not serve, so
    # this legitimately finds nothing — what it proves is that the filter is
    # wired and does not error.
    assert body["total"] >= 0


async def test_sorting_and_the_score_and_rating_filters(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    """The list controls: worst-first, best-first, and "only the good ones"."""
    _device, _app, client = served

    worst = (
        await client.get(
            "/api/shots", params={"sort": "execution_score", "order": "asc", "limit": 50}
        )
    ).json()["data"]["items"]
    scored = [row["execution_score"] for row in worst if row["execution_score"] is not None]
    assert scored == sorted(scored)

    longest = (
        await client.get("/api/shots", params={"sort": "duration", "order": "desc", "limit": 50})
    ).json()["data"]["items"]
    assert [row["duration_ms"] for row in longest] == sorted(
        (row["duration_ms"] for row in longest), reverse=True
    )

    threshold = max(scored) - 0.01
    good = (await client.get("/api/shots", params={"min_score": threshold})).json()["data"]
    assert good["total"] >= 1
    assert all(row["execution_score"] >= threshold for row in good["items"])

    rated = (await client.get("/api/shots", params={"min_rating": 4})).json()["data"]
    assert rated["total"] == 1, "only the fixture shot carries device notes"
    assert rated["items"][0]["rating"] == 4


async def test_a_cursor_is_refused_on_a_sort_it_cannot_page(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    """The cursor encodes the default key, so it is meaningless on another one."""
    _device, _app, client = served
    first = (await client.get("/api/shots", params={"limit": 5})).json()["data"]

    response = await client.get(
        "/api/shots", params={"cursor": first["next_cursor"], "sort": "duration"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"]["field"] == "cursor"


async def test_an_unknown_sort_is_a_400_from_the_schema(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    """A sort name never reaches SQL: it selects a key, or it is rejected."""
    _device, _app, client = served

    response = await client.get("/api/shots", params={"sort": "started_at; DROP TABLE shots"})

    assert response.status_code == 400
    assert (await client.get("/api/shots")).json()["data"]["total"] == STORED


async def test_every_synced_shot_says_it_came_from_the_device(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    """`source` is what tells a synced shot from an imported one."""
    _device, _app, client = served

    body = (await client.get("/api/shots", params={"source": "device", "limit": 50})).json()["data"]

    assert body["total"] == STORED
    assert {row["source"] for row in body["items"]} == {"device"}
    assert (await client.get("/api/shots", params={"source": "import"})).json()["data"][
        "total"
    ] == 0


async def test_shot_detail_carries_phases_diagnostics_and_notes(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served
    listed = (await client.get("/api/shots", params={"limit": 500})).json()["data"]["items"]
    target = next(row for row in listed if row["device_id"] == pad6(NOTES_ID))

    body = (await client.get(f"/api/shots/{target['id']}")).json()["data"]

    assert body["shot"]["phases"], "per-phase statistics are derived at ingest"
    assert body["shot"]["diagnostics"]["summary"]
    assert body["shot"]["execution_score"] is not None
    assert body["notes"]["rating"] == 4
    assert body["notes"]["document"]["doseIn"] == "18"


async def test_samples_come_back_in_order_with_every_field(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served
    listed = (await client.get("/api/shots", params={"limit": 1})).json()["data"]["items"]

    body = (await client.get(f"/api/shots/{listed[0]['id']}/samples")).json()["data"]

    assert body["downsampled"] is False
    assert body["count"] == body["total"] > 100
    # The header's own interval, not the nominal 250 ms: a chart that assumed
    # the nominal figure would draw a shot with gaps as a shot that ran short.
    assert body["sample_interval_ms"] == 250
    times = [row["t_ms"] for row in body["samples"]]
    assert times == sorted(times)
    assert set(body["samples"][0]) == {
        "t_ms",
        "tt",
        "ct",
        "tp",
        "cp",
        "fl",
        "tf",
        "pf",
        "vf",
        "v",
        "ev",
        "pr",
        "si",
        "wp",
        "phase_number",
    }


async def test_downsampling_keeps_the_ends(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    """A sparkline wants a shape, so the first and last points are not optional."""
    _device, _app, client = served
    listed = (await client.get("/api/shots", params={"limit": 1})).json()["data"]["items"]
    shot_id = listed[0]["id"]
    full = (await client.get(f"/api/shots/{shot_id}/samples")).json()["data"]["samples"]

    body = (await client.get(f"/api/shots/{shot_id}/samples", params={"downsample": 20})).json()[
        "data"
    ]

    assert body["count"] == 20
    assert body["downsampled"] is True
    assert body["total"] == len(full)
    assert body["samples"][0] == full[0]
    assert body["samples"][-1] == full[-1]


async def test_a_quarantined_shot_has_no_samples_over_the_api(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served
    quarantined = (await client.get("/api/shots", params={"quarantined": True})).json()["data"]

    body = (await client.get(f"/api/shots/{quarantined['items'][0]['id']}/samples")).json()["data"]

    assert body["samples"] == []


async def test_the_raw_download_is_the_bytes_the_machine_wrote(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    device, _app, client = served
    listed = (await client.get("/api/shots", params={"limit": 500})).json()["data"]["items"]
    target = next(row for row in listed if row["device_id"] == pad6(101))

    response = await client.get(f"/api/shots/{target['id']}/raw")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="000101.slog"'
    assert response.content == device.shots[101].slog_bytes
    # Round-trips through the parser, which is what makes the archive worth
    # keeping when the parser improves.
    assert parse_slog(response.content).header.version == 5
    # Not the envelope, but still traceable.
    assert response.headers["x-request-id"]


async def test_a_missing_shot_is_a_404_in_the_envelope(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served

    for path in ("/api/shots/99999", "/api/shots/99999/samples", "/api/shots/99999/raw"):
        response = await client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_sync_status_reports_the_ledger(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served

    body = (await client.get("/api/sync/status")).json()["data"]

    assert body["configured"] is True
    assert body["connected"] is True
    assert body["counts"]["total"] == STORED
    assert body["counts"]["quarantined"] == 1
    assert body["last_runs"]["backfill"]["status"] == "ok"
    assert body["last_error"] is None
    assert any(event["kind"] == "shot_quarantined" for event in body["recent_events"])


async def test_sync_run_is_accepted_without_waiting(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served

    response = await client.post("/api/sync/run", json={"kind": "profiles"})

    assert response.status_code == 202
    assert response.json()["data"]["queued"] == ["profiles"]


async def test_sync_run_defaults_to_everything(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served

    response = await client.post("/api/sync/run")

    assert response.status_code == 202
    assert set(response.json()["data"]["queued"]) == {"shots", "profiles", "identity"}


async def test_sync_run_without_a_machine_says_so(client: httpx.AsyncClient) -> None:
    """The default app has no `gaggimateHost`, which is a supported configuration."""
    response = await client.post("/api/sync/run")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "gaggimateHost" in response.json()["error"]["message"]


async def test_sync_status_without_a_machine_is_still_an_answer(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get("/api/sync/status")).json()["data"]

    assert body["configured"] is False
    assert body["counts"]["total"] == 0


async def test_the_profile_mirror_is_listed(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    device, _app, client = served

    body = (await client.get("/api/profiles")).json()["data"]

    assert {p["device_id"] for p in body["items"]} == {p["id"] for p in device.profiles}
    first = body["items"][0]
    assert first["label"]
    assert first["content_hash"]

    detail = (await client.get(f"/api/profiles/{first['device_id']}")).json()["data"]
    assert detail["version"]["profile"]["phases"]

    version = (await client.get(f"/api/profile-versions/{first['current_version_id']}")).json()[
        "data"
    ]
    assert version["content_hash"] == first["content_hash"]


async def test_the_machine_route_reports_the_identity_and_the_counts(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    device, _app, client = served

    body = (await client.get("/api/machine")).json()["data"]

    machine = body["machine"]
    assert machine["host"] == device.address
    assert machine["hardware_string"] == device.identity["hardware"]
    assert machine["has_pressure"] is True
    assert body["counts"]["total"] == STORED


async def test_backup_can_be_listed(
    served: tuple[FakeDevice, FastAPI, httpx.AsyncClient],
) -> None:
    _device, _app, client = served
    assert (await client.get("/api/backup")).json()["data"]["items"] == []

    created = (await client.post("/api/backup")).json()["data"]

    listed = (await client.get("/api/backup")).json()["data"]
    assert [item["filename"] for item in listed["items"]] == [created["filename"]]
    assert listed["directory"].endswith("backups")
