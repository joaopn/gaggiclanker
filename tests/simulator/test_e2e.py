"""The whole prototype, end to end, against the real firmware in the simulator.

Opt-in: ``scripts/sim.sh test`` (which builds and starts the simulator), or
``uv run pytest -m simulator`` against one already running on :8080.

Everything else in this repository tests a layer. This tests the claim the
README makes: point it at a machine, pull the shot in, and it is archived with
its curve, judged, grouped, analysed and backed up without anybody touching the
database. It runs against `src/display/` compiled natively, so what passes here
is what the machine on the bench does — which is how we found `OtaSettings`
rejecting every real identity frame.

**The brew is driven from a throwaway WebSocket in this file, and that is
deliberate.** `GaggimateClient` is read-only by design and pinned that way by
`tests/device/test_public_surface.py`
(`req:change-mode` and `req:process:activate` are both on its forbidden list),
so the only honest way to make the simulator brew is to open a second socket
here and send the two frames the machine's own web UI sends
(`web/src/pages/Home/useDashboardState.js`). The device's three-client limit
makes that socket a real cost, so it is opened for the brew and closed again
immediately — never held for the length of the test.

The one provider that is faked is the LLM: this suite must not spend money or
need a network, and what it is checking is that the route, the row and the
suggestion all line up, not what a model says.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import websockets
from fastapi import FastAPI

from gaggiclanker.analyzer.service import AnalyzerService
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings import EnvSettings
from tests.analyzer.conftest import GOOD_OUTPUT
from tests.conftest import running_app
from tests.llm.conftest import FakeProvider

pytestmark = pytest.mark.simulator

#: Port 80 is remapped to 8080 in the sim so it needs no root (`sim/README.md`).
SIM_HOST = os.environ.get("GAGGIMATE_SIM_HOST", "127.0.0.1:8080")

#: `MODE_BREW` from `src/display/core/constants.h`.
MODE_BREW = 1

#: How long to wait for the simulator's brew to finish and the file to be
#: written. A simulated shot runs about thirty seconds; the rest is the save.
BREW_TIMEOUT_S = 120.0

#: How long a requested pull gets to read the index, fetch the `.slog` and
#: derive the curve. Generous: the point is whether it happens at all.
INGEST_TIMEOUT_S = 60.0


@pytest.fixture
async def sim_env(env: EnvSettings, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[EnvSettings]:
    if not await _simulator_is_up():
        pytest.skip(f"no simulator on http://{SIM_HOST} — start one with `scripts/sim.sh serve`")
    monkeypatch.setenv("GAGGIMATE_HOST", SIM_HOST)
    monkeypatch.setenv("GAGGIMATE_TIMEOUT_S", "15")
    yield env


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(script=[json.dumps(GOOD_OUTPUT)])


@pytest.fixture
async def live(
    sim_env: EnvSettings, provider: FakeProvider
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """The real app, talking to the real firmware, with a fake model behind it."""
    async with running_app(sim_env) as (app, client):
        _rewire_llm(app, provider)
        yield app, client


def _rewire_llm(app: FastAPI, provider: FakeProvider) -> None:
    """Point the app's LLM service, and the analyzer holding it, at the fake.

    Same substitution `tests/analyzer/test_api.py` makes and for the same
    reason: the analyzer is app-scoped, so replacing ``app.state.llm`` alone
    would leave it talking to the real provider factory.
    """
    app.state.llm = LlmService(
        app.state.settings_service,
        observer=app.state.llm.observer,
        budget=RateLimitBudget(retries=0),
        mode_memory=ModeMemory(),
        provider_factory=lambda _config, _name: provider,
    )
    app.state.analyzer = AnalyzerService(
        app.state.db,
        app.state.llm,
        PromptService(PromptsRepository(app.state.db)),
        bus=app.state.events,
    )


async def _simulator_is_up() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as probe:
            return (await probe.get(f"http://{SIM_HOST}/api/status")).status_code == 200
    except httpx.HTTPError:
        return False


def data(response: httpx.Response) -> Any:
    assert response.status_code < 400, response.text
    body = response.json()
    assert body["ok"] is True, body
    return body["data"]


async def _until(predicate: Any, timeout: float, what: str) -> Any:
    """Poll ``predicate`` until it returns something truthy, or fail saying what."""
    try:
        async with asyncio.timeout(timeout):
            while True:
                result = await predicate()
                if result:
                    return result
                await asyncio.sleep(0.5)
    except TimeoutError:
        pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}")


async def trigger_a_brew() -> int | None:
    """Brew one shot on the simulator and return the id it saved, or ``None``.

    **Test-only.** This sends two frames gaggiclanker itself is forbidden to
    send (see this module's docstring). It is here, in the test, rather than
    anywhere near `gaggiclanker/device/` precisely so that the read-only surface
    stays exactly as narrow as `tests/device/test_public_surface.py` says it is.

    The socket is closed as soon as the shot is saved: the firmware allows three
    WebSocket clients in total and gaggiclanker is already holding one of them.
    """
    async with websockets.connect(f"ws://{SIM_HOST}/ws", max_size=None) as socket:
        # `req:change-mode` is ignored unless the controller reports SYSTEM_READY
        # (WebSocketHandler.cpp), which it does a second or two after boot.
        await asyncio.sleep(2)
        await socket.send(json.dumps({"tp": "req:change-mode", "mode": MODE_BREW}))
        await asyncio.sleep(1)
        await socket.send(json.dumps({"tp": "req:process:activate", "ignoreWarnings": True}))

        saved: int | None = None
        try:
            async with asyncio.timeout(BREW_TIMEOUT_S):
                async for raw in socket:
                    message = json.loads(raw)
                    if message.get("tp") == "evt:history-shot-saved":
                        # Unpadded on the event, padded everywhere else — the
                        # quirk `gaggiclanker/domain/ids.py` exists for.
                        saved = int(message["id"])
                        break
        except TimeoutError:
            saved = None
        finally:
            await socket.send(json.dumps({"tp": "req:process:deactivate"}))
            await asyncio.sleep(0.5)
        return saved


async def test_the_whole_prototype_against_the_simulator(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    _app, client = live

    # 1. Identity. The machine row is written by the identity pass at boot, and
    #    it is what the UI's device pill reads.
    status = await _until(
        lambda: _device_identified(client), timeout=30.0, what="the device pill to go green"
    )
    assert status["connected"] is True

    # Polled, not asserted straight after the pill goes green. "Connected" is
    # the socket; `display_version` is written by the identity pass, which needs
    # the `res:ota-settings` broadcast to have arrived and been stored — a
    # moment later. Asserting on the row the instant the socket is up fails
    # about one run in ten, and it is the test that is wrong, not the app.
    machine = await _until(
        lambda: _machine_identified(client), timeout=30.0, what="the identity pass to fill the row"
    )
    assert machine["display_version"], machine

    # 2. Profiles. A fresh install ships the seed profiles in `data/p/` — and
    #    nothing mirrors them until somebody asks, so this is the first pull.
    assert data(await client.post("/api/sync/run", json={"kind": "profiles"}))["queued"]
    profiles = await _until(
        lambda: _profiles_mirrored(client), timeout=60.0, what="the profile mirror to run"
    )
    assert profiles, "the simulator listed no profiles"

    before = data(await client.get("/api/shots"))["total"]

    # 3. A brew, driven from a socket of this test's own (see the docstring).
    device_shot_id = await trigger_a_brew()
    # Not an xfail. The simulator brews headlessly — that was established when
    # this test was written — so a brew that does not arrive is a regression in
    # something, and an expected failure is how a regression goes unnoticed for
    # a month. If the firmware ever stops allowing it, this assertion is the
    # right place to find that out.
    assert device_shot_id is not None, (
        f"the simulator saved no shot within {BREW_TIMEOUT_S:.0f}s of "
        "req:change-mode + req:process:activate; see /tmp/gaggimate-sim.log"
    )

    # 4. Ask for the shot. Nothing moves off the machine until somebody does —
    #    the archive is pulled into, not pushed at — and this is the request the
    #    "Pull from machine" button makes. The assertion is still that the app
    #    does the rest: no polling from the test, no reaching into the database.
    assert data(await client.post("/api/sync/run", json={"kind": "all"}))["queued"]
    listed = await _until(
        lambda: _shot_arrived(client, before),
        timeout=INGEST_TIMEOUT_S,
        what="the shot to be synced",
    )
    shot_id = int(listed["id"])
    assert listed["quarantined"] is False, "the simulator's own .slog failed to parse"

    # 5. Curves and diagnostics — DoD item 3.
    detail = data(await client.get(f"/api/shots/{shot_id}"))
    assert detail["shot"]["sample_count"] > 0
    assert detail["shot"]["diagnostics"] is not None
    samples = data(await client.get(f"/api/shots/{shot_id}/samples"))["samples"]
    assert len(samples) > 10
    assert any(sample["ct"] is not None for sample in samples)

    # 6. Judge it — DoD item 4, first half.
    judgement = data(
        await client.put(
            f"/api/shots/{shot_id}/judgement",
            json={
                "rating": 3,
                "balance": "sour",
                "taste_tags": ["sour"],
                "notes": "Simulated, and sharp.",
            },
        )
    )
    assert judgement["rating"] == 3

    # 7. Put it in a Set.
    bean = data(
        await client.post("/api/beans", json={"name": "Simulator blend", "roaster": "nobody"})
    )
    grinder = data(await client.post("/api/grinders", json={"name": "Simulated grinder"}))
    stored_set = data(
        await client.post(
            "/api/sets",
            json={
                "name": "Simulator baseline",
                "bean_id": bean["id"],
                "grinder_id": grinder["id"],
                "version": {
                    "grind_setting": "22",
                    "grind_value": 22.0,
                    "dose_g": 18.0,
                    "target_yield_g": 36.0,
                },
            },
        )
    )
    version_id = stored_set["current_version_id"]
    assigned = data(
        await client.put(f"/api/shots/{shot_id}/set-version", json={"set_version_id": version_id})
    )
    assert assigned["set_version_id"] == version_id

    # 8. Analyse it with the fake provider — DoD item 4, second half.
    #    `?wait=1` blocks until the task is done, which is what this route
    #    offers for exactly this case.
    analysis = data(
        await client.post(f"/api/shots/{shot_id}/analyses?wait=1", json={}, timeout=60.0)
    )
    assert analysis["status"] == "ok", analysis.get("error")
    assert provider.calls, "the analyzer never called a provider"
    assert analysis["suggestions"], "the analysis produced no suggestions"

    # 9. Accept one — DoD item 5. The new version must name the old as parent.
    suggestion = analysis["suggestions"][0]
    accepted = data(await client.post(f"/api/suggestions/{suggestion['id']}/accept", json={}))
    new_version = accepted["version"]
    assert new_version["parent_version_id"] == version_id
    assert new_version["origin"] == "analysis"

    # 10. Back up — DoD item 8. The file is what a restore copies.
    backup = data(await client.post("/api/backup"))
    assert backup["size_bytes"] > 0
    listed_backups = data(await client.get("/api/backup"))["items"]
    assert any(entry["filename"] == backup["filename"] for entry in listed_backups)

    # And the database the app is still using is untouched by the copy.
    assert data(await client.get(f"/api/shots/{shot_id}"))["shot"]["id"] == shot_id


async def _device_identified(client: httpx.AsyncClient) -> dict[str, Any] | None:
    status = data(await client.get("/api/device/status"))
    return status if status.get("connected") else None


async def _machine_identified(client: httpx.AsyncClient) -> dict[str, Any] | None:
    """The machine row, once it carries a firmware version."""
    row: dict[str, Any] = data(await client.get("/api/machine"))["machine"]
    return row if row.get("display_version") else None


async def _profiles_mirrored(client: httpx.AsyncClient) -> list[dict[str, Any]] | None:
    items: list[dict[str, Any]] = data(await client.get("/api/profiles"))["items"]
    return items or None


async def _shot_arrived(client: httpx.AsyncClient, before: int) -> dict[str, Any] | None:
    listing = data(await client.get("/api/shots?limit=1"))
    if listing["total"] <= before:
        return None
    row: dict[str, Any] = listing["items"][0]
    # A shot appears in the index before its samples are derived; waiting for
    # the sample count is what makes step 5 an assertion rather than a race.
    return row if row.get("sample_count") else None
