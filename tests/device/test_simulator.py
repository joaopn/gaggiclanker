"""Against the real firmware, in the simulator. Opt-in: ``uv run pytest -m simulator``.

The offline suite proves the client against our own fake, which is only ever as
good as our reading of the GaggiMate firmware source. This file
is the check on that reading: the same calls, against the actual
`src/display/` C++ compiled natively.

Start it with ``scripts/sim.sh serve`` (or run the whole thing with
``scripts/sim.sh test``, which builds it too). The build needs one patch, kept
in ``scripts/sim-patches/`` and applied to a scratch clone rather than to the
reference checkout: the firmware's simulator shim never grew the
``AsyncURIMatcher`` that ``WebUIPlugin.cpp`` uses for the OTA filter.

It pays for itself. The first run against it found `OtaSettings` rejecting
*every* real identity frame — the device sends a heap and filesystem
diagnostics block that our fake did not — and a shutdown that took ten seconds
because the close handshake was unbounded. Neither was visible against the fake,
which is the whole argument for this file existing.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.events import ShotSaved, StatusChanged

pytestmark = pytest.mark.simulator

#: Port 80 is remapped to 8080 in the sim so it needs no root (`sim/README.md`).
SIM_HOST = os.environ.get("GAGGIMATE_SIM_HOST", "127.0.0.1:8080")


@pytest.fixture
async def sim_client():
    client = GaggimateClient(SIM_HOST, timeout=10.0)
    await client.start()
    if not await client.wait_connected(15.0):
        await client.stop()
        pytest.fail(f"no simulator on ws://{SIM_HOST}/ws — start one with `scripts/sim.sh serve`")
    try:
        yield client
    finally:
        await client.stop()


async def test_identity_names_the_simulated_board(sim_client: GaggimateClient) -> None:
    identity = await sim_client.get_ota_settings()
    assert identity.display_version
    assert identity.hardware is not None


async def test_http_status_answers(sim_client: GaggimateClient) -> None:
    status = await sim_client.get_status()
    assert "ct" in status


async def test_settings_are_readable(sim_client: GaggimateClient) -> None:
    settings = await sim_client.get_settings()
    assert "mdnsName" in settings


async def test_profiles_list_and_load(sim_client: GaggimateClient) -> None:
    """The seed profiles in `data/p/` are what a fresh install ships with."""
    profiles = await sim_client.list_profiles()
    assert profiles
    first = profiles[0]
    assert first.id is not None
    loaded = await sim_client.load_profile(first.id)
    assert loaded.label == first.label


async def test_the_index_and_one_shot_file(sim_client: GaggimateClient) -> None:
    """Needs at least one shot in `sim_data/`; run a brew in the sim first."""
    index = await sim_client.fetch_index()
    if index is None or not index.live():
        pytest.skip("the simulator has no shot history yet — brew one in the UI")

    newest = index.live()[0]
    fetched = await sim_client.fetch_slog(newest.id)
    assert fetched is not None
    assert fetched.raw[:4] == b"SHOT"
    assert fetched.slog is not None, fetched.parse_error
    assert fetched.slog.samples

    if newest.has_notes:
        assert await sim_client.get_shot_notes(newest.id) is not None


async def test_live_status_streams(sim_client: GaggimateClient) -> None:
    """2 Hz telemetry, merged. The first frame is the state snapshot on connect."""
    subscription = sim_client.subscribe()
    try:
        async with asyncio.timeout(10):
            while True:
                event = await anext(subscription)
                if isinstance(event, StatusChanged) and event.status.ct is not None:
                    break
    finally:
        await subscription.aclose()
    assert sim_client.last_status is not None


@pytest.mark.skip(reason="superseded by tests/simulator/test_e2e.py, which drives the brew")
async def test_a_simulated_brew_produces_a_shot_saved(sim_client: GaggimateClient) -> None:
    """Press brew in the sim window within 60 s of starting this.

    Kept for the manual case and skipped by default. It turned out that the sim
    *can* be brewed headlessly — `req:change-mode` then `req:process:activate`,
    the two frames its own web UI sends — and `tests/simulator/test_e2e.py` does
    exactly that from a throwaway socket of its own. It has to be a socket of
    its own: this client is read-only and both frames are on the forbidden list
    in `tests/device/test_public_surface.py`, which is the point.
    """
    subscription = sim_client.subscribe()
    try:
        async with asyncio.timeout(60):
            while True:
                event = await anext(subscription)
                if isinstance(event, ShotSaved):
                    break
    finally:
        await subscription.aclose()

    fetched = await sim_client.fetch_slog(event.shot_id)
    assert fetched is not None
    assert fetched.slog is not None
