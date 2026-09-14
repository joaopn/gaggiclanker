"""Layer 4: every profile, and a generated draft, written to the real firmware.

The other three layers are cheap enough to run on every request and none of them
can answer the question this one asks. A schema cannot tell that the firmware
silently dropped a target type it did not recognise. A policy cannot tell that a
document it approved will not brew. A round trip can tell that the machine
stored what we sent and nothing at all about whether the machine can *run* it.

So this saves every profile fixture and one LLM-drafted profile to
`src/display/` compiled natively, reads each back, brews with one of them, and
deletes everything it created. What passes here is what the machine on the bench
accepts, because it is the same parser and the same brew code.

Two things are borrowed from `test_e2e.py` and for the same reasons. The brew is
driven from a **throwaway socket of this test's own**, because `GaggimateClient`
may not send `req:change-mode` — that frame is on the forbidden list in
`tests/device/test_public_surface.py` and it stays there. And the socket is
closed the moment the shot is saved, because the firmware allows three clients
in total and gaggiclanker is already holding one.

Device writes are enabled **in this test only**, through the settings service,
the way a person would. The offline suite never turns them on except in its own
`writes_on` fixture, so the shipped default keeps being the thing under test
everywhere else.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import websockets
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.domain.models import Profile, canonical_profile_json, with_app_suffix
from gaggiclanker.drafts.service import ProfileDraftService
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app
from tests.drafts.conftest import every_profile_fixture
from tests.llm.conftest import FakeProvider
from tests.simulator.test_e2e import (
    MODE_BREW,
    SIM_HOST,
    _simulator_is_up,
    data,
)

pytestmark = pytest.mark.simulator

#: How long to let a brew run before giving up on it. The profile this test
#: brews with is the shipped 9 Bar (28 s, volumetric 36 g) and the simulator has
#: no scale, so it exits on duration.
BREW_TIMEOUT_S = 90.0


@pytest.fixture
async def sim_env(env: EnvSettings, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[EnvSettings]:
    if not await _simulator_is_up():
        pytest.skip(f"no simulator on http://{SIM_HOST} — start one with `scripts/sim.sh serve`")
    monkeypatch.setenv("GAGGIMATE_HOST", SIM_HOST)
    monkeypatch.setenv("GAGGIMATE_TIMEOUT_S", "15")
    yield env


@pytest.fixture
def provider() -> FakeProvider:
    """The model is faked here, as everywhere: no money, no network.

    What layer 4 tests is the firmware's acceptance of a document, not what a
    model would put in one. The document this provider returns is a real edit to
    a real profile, which is all the firmware can tell the difference about.
    """
    return FakeProvider()


@pytest.fixture
async def live(
    sim_env: EnvSettings, provider: FakeProvider
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    async with running_app(sim_env) as (app, client):
        app.state.llm = LlmService(
            app.state.settings_service,
            observer=app.state.llm.observer,
            budget=RateLimitBudget(retries=0),
            mode_memory=ModeMemory(),
            provider_factory=lambda _config, _name: provider,
        )
        app.state.drafts = ProfileDraftService(
            app.state.db,
            app.state.llm,
            PromptService(app.state.drafts.prompts.repo),
            app.state.settings_service,
            connection=app.state.connection,
        )
        assert await app.state.connection.client.wait_connected(20.0), (
            "the simulator did not connect"
        )
        # Here, and only here. The shipped default is off and the offline suite
        # is what proves it stays off.
        await app.state.settings_service.apply({"deviceWritesEnabled": True})
        await app.state.connection.engine.sync_profiles(trigger="test")
        yield app, client


async def brew_with(profile_id: str) -> bool:
    """Select ``profile_id``, brew a shot with it, and say whether one was saved.

    The select and the brew are two different sockets on purpose: selecting is
    something gaggiclanker is allowed to do and does through its own gated
    client, and `req:change-mode` is something it is not, so that half happens
    here. The socket is opened for the brew and closed immediately — the
    firmware allows three clients and the app is holding one of them.
    """
    async with websockets.connect(f"ws://{SIM_HOST}/ws", max_size=None) as socket:
        # `req:change-mode` is ignored until the controller reports SYSTEM_READY.
        await asyncio.sleep(2)
        await socket.send(json.dumps({"tp": "req:change-mode", "mode": MODE_BREW}))
        await asyncio.sleep(1)
        await socket.send(json.dumps({"tp": "req:process:activate", "ignoreWarnings": True}))
        saved = False
        try:
            async with asyncio.timeout(BREW_TIMEOUT_S):
                async for raw in socket:
                    if json.loads(raw).get("tp") == "evt:history-shot-saved":
                        saved = True
                        break
        except TimeoutError:
            saved = False
        finally:
            await socket.send(json.dumps({"tp": "req:process:deactivate"}))
            await asyncio.sleep(0.5)
        return saved


async def test_every_fixture_profile_survives_a_round_trip_through_the_firmware(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """Nine profiles in, nine profiles back, byte-identical in canonical form.

    This is the assertion that makes the push flow's comparison trustworthy: if
    the real `writeProfile` added a field `canonical_profile_json` does not drop,
    every push would report a mismatch and the feature would be unusable. The
    fake reproduces `writeProfile` from the source; this checks the source.

    Everything it creates is deleted at the end, including on failure — the
    simulator's filesystem persists between runs and a test that littered would
    make the next run's profile list a different list.
    """
    app, _client = live
    client = app.state.connection.client
    created: list[str] = []
    try:
        for name, document in every_profile_fixture():
            sent = Profile.model_validate(document).for_new_device_profile(
                label=with_app_suffix(f"{name} gate")
            )
            stored = await client.save_profile(sent)
            assert stored.id is not None, name
            created.append(stored.id)

            served = await client.load_profile(stored.id)
            assert canonical_profile_json(served) == canonical_profile_json(sent), (
                f"{name} came back different:\n"
                f"  sent:   {canonical_profile_json(sent)}\n"
                f"  loaded: {canonical_profile_json(served)}"
            )
            # The firmware auto-favourites new profiles. Undo it, or nine gate
            # profiles end up on somebody's home screen.
            await client.unfavorite_profile(stored.id)
    finally:
        for profile_id in created:
            await client.delete_profile(profile_id)

    listed = {profile.id for profile in await client.list_profiles()}
    assert not (set(created) & listed), "the gate left profiles on the machine"


async def test_a_generated_draft_is_pushed_verified_brewed_and_deleted(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """The whole feature against the real firmware, one shot included.

    Draft -> approve -> push -> round trip -> select -> brew to completion ->
    roll back. The brew is what only this layer can check: a profile the
    firmware stores faithfully and then refuses to run is a profile all three
    cheap layers call valid.
    """
    app, client = live
    profiles = ProfilesRepository(app.state.db)

    # The first non-utility profile the simulator ships. A backflush profile
    # would draft and push fine and would never brew, which is the one thing
    # this test is here to check.
    page = await profiles.list_versions(limit=200)
    usable = next((version for version in page.items if not version.utility), None)
    assert usable is not None, "the simulator listed no usable profile"
    base = await profiles.get_version(usable.id)
    assert base is not None and base.profile is not None

    # A real edit to a real profile: one phase's pump pressure, nothing else.
    # Not a stop condition — that path has its own test offline, and moving a
    # target here would make this test require an acknowledgement as well.
    document: dict[str, Any] = dict(base.profile)
    document["phases"] = [dict(phase) for phase in document["phases"]]
    first = document["phases"][0]
    if isinstance(first.get("pump"), dict):
        first["pump"] = {**first["pump"], "pressure": 8}
    else:
        first["pump"] = max(int(first.get("pump", 100)) - 10, 0)
    provider.script = [
        json.dumps({"profile": document, "change_summary": "Eight bar instead of nine."})
    ]

    draft = data(
        await client.post(
            "/api/profile-drafts",
            json={"base_version_id": base.id, "notes": "gentler peak pressure"},
        )
    )
    approved = data(
        await client.post(
            f"/api/profile-drafts/{draft['id']}/approve",
            json={"acknowledge_stop_changes": True},
        )
    )
    assert approved["status"] == "approved"

    pushed = data(await client.post(f"/api/profile-drafts/{draft['id']}/push", json={}))["draft"]
    device_id = pushed["pushed_device_profile_id"]

    # Inside the try, with everything else: a push that saved and then failed
    # verification still has a profile on the simulator, and asserting before
    # the cleanup is how the next run finds it there.
    try:
        assert pushed["status"] == "pushed", pushed.get("error")
        # The machine's own list agrees, which is a stronger statement than the
        # load the push already did: `req:profiles:list` re-reads every file.
        listed = {
            profile.id: profile for profile in await app.state.connection.client.list_profiles()
        }
        assert device_id in listed
        assert listed[device_id].label.endswith("[AI]")

        # Selecting is a separate, deliberate action — a push never does it.
        await app.state.connection.client.select_profile(device_id)
        assert await brew_with(device_id), (
            f"the simulator would not brew the pushed profile within {BREW_TIMEOUT_S:.0f}s; "
            "see /tmp/gaggimate-sim.log"
        )
    finally:
        if device_id:
            rolled = data(await client.post(f"/api/profile-drafts/{draft['id']}/rollback", json={}))
            assert rolled["pushed_device_profile_id"] is None

    assert device_id not in {
        profile.id for profile in await app.state.connection.client.list_profiles()
    }
    kinds = [
        (row.kind, row.result) for row in await DeviceWritesRepository(app.state.db).list_writes()
    ]
    assert ("profile_save", "ok") in kinds
    assert ("profile_select", "ok") in kinds
    assert ("profile_delete", "ok") in kinds
