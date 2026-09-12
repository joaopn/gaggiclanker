"""`GET /api/sync/events` over a real socket.

A real uvicorn, not the ASGI transport: `httpx.ASGITransport` awaits the
application to completion before it hands back a response, and an event stream
by design never completes, so the in-process transport deadlocks on it. The same
reason `tests/device/conftest.py` grew `serving()`, reused here.

Both tests wait for the bus to have a subscriber before doing anything that
publishes. That is not politeness, it is the bus's contract: a subscriber only
receives what is published after it subscribes, and the response headers reach
the client *before* the route's generator has taken its subscription. The front
end lives with the same fact — every event means "go and re-read", and the
periodic index diff is the backstop behind it — but a test that raced it would
fail one run in six and look like a broken stream.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice, synthetic_slog_bytes
from gaggiclanker.domain.ids import pad6
from gaggiclanker.settings import EnvSettings
from gaggiclanker.sync.engine import SHOT_INGESTED_EVENT, SYNC_PROGRESS_EVENT
from tests.device.conftest import parse_sse_frame, serving
from tests.sync.conftest import SMALL_COUNT, build_archive_device

NEW_SHOT_ID = 900


@pytest.fixture
async def stocked(monkeypatch: pytest.MonkeyPatch) -> FakeDevice:
    device = build_archive_device(SMALL_COUNT, header_only=False)
    await device.start()
    monkeypatch.setenv("GAGGIMATE_HOST", device.address)
    monkeypatch.setenv("GAGGIMATE_TIMEOUT_S", "2")
    return device


@pytest.fixture
def sync_env(data_dir: Path, stocked: FakeDevice) -> EnvSettings:
    return EnvSettings(
        DATA_DIR=str(data_dir),
        LOG_LEVEL="warning",
        LOG_JSON=True,
        _env_file=None,  # type: ignore[call-arg]
    )


#: The four passes the engine runs at boot. A test that waited only for the
#: shot count would still be racing the profiles mirror, whose events would then
#: arrive on the stream ahead of the one it is looking for.
BOOT_RUNS = {"identity", "backfill", "profiles", "notes"}


async def _settle(app: FastAPI, timeout: float = 20.0) -> None:
    """Wait for every pass the engine starts at boot to finish."""
    async with asyncio.timeout(timeout):
        while True:
            counts = await app.state.sync.shots.counts()
            runs = await app.state.sync.runs.last_runs()
            if counts.total >= SMALL_COUNT - 1 and BOOT_RUNS <= set(runs):
                if all(run.finished_at for run in runs.values()):
                    return
            await asyncio.sleep(0.02)


async def read_until(
    response: httpx.Response, name: str, *, timeout: float = 8.0
) -> list[dict[str, Any]]:
    """Read frames until one named ``name`` arrives, and return everything read.

    One pass over the body, because `aiter_text()` can only be consumed once.
    The stream is a feed, not a queue: a run publishes progress, profile and
    shot events together and their order is the engine's business, not the
    reader's, so asserting on "the first frame" would be asserting on an
    ordering nothing promises.
    """
    seen: list[dict[str, Any]] = []
    buffer = ""
    async with asyncio.timeout(timeout):
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\r\n\r\n" in buffer or "\n\n" in buffer:
                separator = "\r\n\r\n" if "\r\n\r\n" in buffer else "\n\n"
                frame, buffer = buffer.split(separator, 1)
                parsed = parse_sse_frame(frame)
                if parsed is None:
                    continue
                seen.append(parsed)
                if parsed["event"] == name:
                    return seen
    return seen


async def _wait_for_subscriber(app: FastAPI, timeout: float = 5.0) -> None:
    """Wait until the route's generator has actually subscribed to the bus."""
    async with asyncio.timeout(timeout):
        while app.state.events.subscriber_count == 0:
            await asyncio.sleep(0.01)


async def test_the_stream_reports_a_run_and_the_shots_in_it(
    sync_env: EnvSettings, stocked: FakeDevice
) -> None:
    """A run starting, a shot landing, the run finishing — the UI's whole feed."""
    try:
        async with serving(sync_env) as (app, base_url):
            await _settle(app)
            stocked.add_shot(NEW_SHOT_ID, synthetic_slog_bytes(shot_id=NEW_SHOT_ID))

            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as http:
                async with http.stream("GET", "/api/sync/events") as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith("text/event-stream")
                    await _wait_for_subscriber(app)
                    await http.post("/api/sync/run", json={"kind": "shots"})
                    events = await read_until(response, SHOT_INGESTED_EVENT)
    finally:
        await stocked.stop()

    by_name = [event["event"] for event in events]
    assert SYNC_PROGRESS_EVENT in by_name
    ingested = [e for e in events if e["event"] == SHOT_INGESTED_EVENT]
    assert ingested, events
    assert ingested[0]["data"]["device_id"] == pad6(NEW_SHOT_ID), (
        "ids travel padded, as the device's URLs use them"
    )
    started = [e for e in events if e["data"].get("status") == "started"]
    assert started and started[0]["data"]["trigger"] == "manual"


async def test_a_live_shot_reaches_an_open_stream(
    sync_env: EnvSettings, stocked: FakeDevice
) -> None:
    """A browser tab left open sees the shot the machine has just finished."""
    try:
        async with serving(sync_env) as (app, base_url):
            await _settle(app)

            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as http:
                async with http.stream("GET", "/api/sync/events") as response:
                    await _wait_for_subscriber(app)
                    await stocked.run_brew(
                        NEW_SHOT_ID, slog_bytes=synthetic_slog_bytes(shot_id=NEW_SHOT_ID)
                    )
                    events = await read_until(response, SHOT_INGESTED_EVENT)
    finally:
        await stocked.stop()

    ingested = [e for e in events if e["event"] == SHOT_INGESTED_EVENT]
    assert ingested, events
    assert ingested[0]["data"]["device_id"] == pad6(NEW_SHOT_ID)
