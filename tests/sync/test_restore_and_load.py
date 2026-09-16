"""Two properties that only a real file and a real clock can show.

**Restore** — the whole install is one SQLite file, so a backup is a file copy
and a restore is copying it back. That is the entire disaster-recovery story for
this project, and it is worth proving rather than documenting: take a backup of
a stocked archive, drop it into a fresh `DATA_DIR`, boot, and every shot and
every sample row is still there.

**Responsiveness** — a pull is background work owned by the app lifespan rather
than work done inside the request that asked for it, and the point of that is
that a two-hundred-shot first pull against a slow machine does not make the
archive browser unusable while it runs. With a 50 ms delay on every device
fetch, `/health` still answers in well under 200 ms.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import httpx
import pytest

from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app, seed_settings
from tests.sync.conftest import SMALL_COUNT, build_archive_device

STORED = SMALL_COUNT - 1

#: The acceptance criterion's own numbers: two hundred shots, 50 ms per fetch.
LOAD_SHOT_COUNT = 200
FETCH_DELAY_S = 0.05

#: What "responsive" means here — the chunk's own figure. A health check that
#: takes longer than this while a sync runs would fail a container healthcheck
#: and get the box restarted mid-backfill, which is how a slow sync becomes a
#: sync that never finishes.
#:
#: Read at the 95th percentile rather than the maximum. The property under test
#: is "the backfill does not hold the event loop", and on a shared build box a
#: single request can lose half a second to the *host* scheduler with nothing
#: wrong here at all. A separate, much looser ceiling catches the failure this
#: really guards against — one long synchronous block, which would show up as
#: many slow requests rather than one.
HEALTH_BUDGET_S = 0.2
HEALTH_CEILING_S = 1.5


def _env(data_dir: Path) -> EnvSettings:
    return EnvSettings(
        DATA_DIR=str(data_dir),
        LOG_LEVEL="warning",
        LOG_JSON=True,
    )  # type: ignore[call-arg]


async def _pull(client: httpx.AsyncClient) -> None:
    """Ask for a pull. Nothing comes off the machine unasked."""
    accepted = await client.post("/api/sync/run", json={"kind": "all"})
    assert accepted.status_code == 202, accepted.text


async def _wait_for(client: httpx.AsyncClient, total: int, timeout: float = 60.0) -> None:
    async with asyncio.timeout(timeout):
        while True:
            envelope = (await client.get("/api/sync/status")).json()
            assert envelope["ok"], envelope
            if envelope["data"]["counts"]["total"] >= total:
                return
            await asyncio.sleep(0.05)


async def test_a_backup_restores_into_a_fresh_data_dir(tmp_path: Path) -> None:
    device = build_archive_device(SMALL_COUNT, header_only=False)
    await device.start()
    original = tmp_path / "original"
    original.mkdir()
    restored = tmp_path / "restored"
    restored.mkdir()

    try:
        original_env = _env(original)
        await seed_settings(original_env, gaggimateHost=device.address)
        async with running_app(original_env) as (_app, client):
            await _pull(client)
            await _wait_for(client, STORED)
            before = (await client.get("/api/sync/status")).json()["data"]["counts"]
            backup = (await client.post("/api/backup")).json()["data"]
    finally:
        await device.stop()

    # The documented restore: stop the container, copy the file into place,
    # start it again. No machine configured this time, because an archive
    # browser has to work with the machine unplugged — and because a sync
    # against a device would muddy the comparison.
    shutil.copyfile(backup["path"], restored / "gaggiclanker.db")

    async with running_app(_env(restored)) as (_app, client):
        after = (await client.get("/api/sync/status")).json()["data"]["counts"]
        listed = (await client.get("/api/shots", params={"limit": 500})).json()["data"]
        detail = (await client.get(f"/api/shots/{listed['items'][0]['id']}")).json()["data"]
        samples = (await client.get(f"/api/shots/{listed['items'][0]['id']}/samples")).json()[
            "data"
        ]
        raw = await client.get(f"/api/shots/{listed['items'][0]['id']}/raw")

    assert after == before
    assert after["total"] == STORED
    assert after["quarantined"] == 1
    assert len(listed["items"]) == STORED
    assert detail["shot"]["diagnostics"] is not None
    assert samples["count"] == detail["shot"]["sample_count"]
    # The raw bytes came through the copy too: they are the thing that makes
    # every derived column rebuildable, so a backup without them is not one.
    assert raw.content[:4] == b"SHOT"


@pytest.mark.parametrize("shots", [LOAD_SHOT_COUNT])
async def test_the_api_stays_responsive_during_a_backfill(tmp_path: Path, shots: int) -> None:
    device = build_archive_device(count=shots, header_only=False)
    device.history_delay_s = FETCH_DELAY_S
    await device.start()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    timings: list[float] = []
    try:
        env = _env(data_dir)
        await seed_settings(env, gaggimateHost=device.address)
        async with running_app(env) as (_app, client):
            await _pull(client)
            # Poll health while the pull runs. It cannot finish before the
            # fetches do: two hundred files, two at a time, 50 ms each.
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                started = time.monotonic()
                response = await client.get("/health")
                assert response.status_code == 200
                timings.append(time.monotonic() - started)
                await asyncio.sleep(0.01)

            status = (await client.get("/api/sync/status")).json()["data"]
            assert status["counts"]["total"] > 0, "the pull was under way, not finished"
            assert status["counts"]["total"] < shots - 1, "…and not yet done"

        # …and the block exits with the pull still running, which exercises
        # the other half of "background work owned by the lifespan": shutdown
        # cancels the loops and closes the database with a pass in flight.
    finally:
        await device.stop()

    assert len(timings) > 25
    timings.sort()
    p95 = timings[int(len(timings) * 0.95)]
    assert p95 < HEALTH_BUDGET_S, f"the 95th-percentile /health took {p95:.3f}s"
    assert timings[-1] < HEALTH_CEILING_S, f"the slowest /health took {timings[-1]:.3f}s"
