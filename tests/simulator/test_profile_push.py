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

Profiles reach the machine the way they do in the app: a draft is put on the profile board and
the next sync (the board's write phase) saves, verifies and replaces. Nothing here calls the
write primitives by hand except where a test says it does.

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
from gaggiclanker.domain.models import (
    Profile,
    canonical_profile_json,
    profile_content_hash,
)
from gaggiclanker.drafts.service import ProfileDraftService
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app, seed_settings
from tests.drafts.conftest import every_profile_fixture
from tests.llm.conftest import FakeProvider
from tests.simulator.test_e2e import (
    MODE_BREW,
    SIM_HOST,
    data,
    require_simulator,
)

pytestmark = pytest.mark.simulator

#: How long to let a brew run before giving up on it. The profile this test
#: brews with is the shipped 9 Bar (28 s, volumetric 36 g) and the simulator has
#: no scale, so it exits on duration.
BREW_TIMEOUT_S = 90.0

#: What this file adds to the name of every profile it makes on the machine, so that a test can
#: tell its own profiles from the simulator's and clean up exactly those. The app adds nothing.
TEST_MARK = " [sim test]"


def with_test_mark(label: str) -> str:
    return label if label.endswith(TEST_MARK) else f"{label}{TEST_MARK}"


async def delete_now(client: Any, profile_id: str) -> None:
    """Delete a profile this test made, against what the machine holds right now."""
    held = await client.load_profile(profile_id)
    await client.delete_profile(profile_id, expected_hash=profile_content_hash(held))


@pytest.fixture
async def sim_env(env: EnvSettings) -> AsyncIterator[EnvSettings]:
    """The bootstrap settings, plus an archive already holding the simulator's address."""
    await require_simulator()
    await seed_settings(env, gaggimateHost=SIM_HOST, gaggimateTimeoutSeconds=15)
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
            proposals=app.state.draft_proposals,
        )
        assert await app.state.connection.client.wait_connected(20.0), (
            "the simulator did not connect"
        )
        # The mirror is taken first, with writes still off, so the drafts below have profiles to
        # be made from. Then writes on, here and only here. The shipped default is off and the
        # offline suite is what proves it stays off.
        await app.state.connection.engine.sync_profiles(trigger="test")
        await app.state.settings_service.apply({"deviceWritesEnabled": True})
        yield app, client


async def sync(app: FastAPI) -> dict[str, Any]:
    """One sync (the profile pass and the board's write phase); its summary, after it ran ok."""
    run = await app.state.connection.engine.sync_profiles(trigger="test")
    assert run.status == "ok", run.error
    return dict(run.summary or {})


async def adopt(app: FastAPI) -> None:
    """The first sync with writes on takes the simulator's own profiles and writes nothing."""
    summary = await sync(app)
    assert summary["writes"] == 0 and summary["adopted"], summary


async def put_on_board(
    app: FastAPI,
    client: httpx.AsyncClient,
    provider: FakeProvider,
    base_version_id: int,
    bar: float,
) -> dict[str, Any]:
    """A real edit to a real profile (one phase's pump pressure), put on the board."""
    base = await ProfilesRepository(app.state.db).get_version(base_version_id)
    assert base is not None and base.profile is not None
    document: dict[str, Any] = dict(base.profile)
    document["phases"] = [dict(phase) for phase in document["phases"]]
    # A fork: the agent writing a profile of its own beside the simulator's (a change that kept
    # the name would be a new version of that profile).
    document["label"] = with_test_mark(str(document["label"]))
    first = document["phases"][0]
    first["pump"] = (
        {**first["pump"], "pressure": bar}
        if isinstance(first.get("pump"), dict)
        else max(int(first.get("pump", 100)) - int(bar), 0)
    )
    provider.script = [json.dumps({"profile": document, "change_summary": f"{bar} bar."})]
    draft = data(
        await client.post(
            "/api/profile-drafts", json={"base_version_id": base_version_id, "notes": "edit"}
        )
    )
    row = data(await client.post("/api/profile-board", json={"draft_id": draft["id"]}))
    return {"draft": draft, "row": row}


async def usable_version(app: FastAPI) -> int:
    """The first non-utility profile the simulator ships: a backflush would never brew."""
    page = await ProfilesRepository(app.state.db).list_versions(limit=200)
    usable = next((version for version in page.items if not version.utility), None)
    assert usable is not None, "the simulator listed no usable profile"
    return int(usable.id)


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
                label=with_test_mark(f"{name} gate")
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
            await delete_now(client, profile_id)

    listed = {profile.id for profile in await client.list_profiles()}
    assert not (set(created) & listed), "the gate left profiles on the machine"


async def test_a_generated_draft_is_synced_verified_brewed_and_deleted(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """The whole feature against the real firmware, one shot included.

    Draft -> put on the board -> sync (save, round trip) -> select -> brew to completion ->
    delete from the board -> sync (removal). The brew is what only this layer can check: a
    profile the firmware stores faithfully and then refuses to run is a profile all three
    cheap layers call valid.
    """
    app, client = live
    device = app.state.connection.client
    await adopt(app)
    # A real edit to a real profile, and not a stop condition: the stop-condition list has its
    # own tests offline, and this one is about the pressure edit reaching the machine.
    placed = await put_on_board(app, client, provider, await usable_version(app), 8)

    summary = await sync(app)
    [pushed] = summary["pushed"]
    device_id = str(pushed["device_id"])
    row_id = placed["row"]["id"]

    # Inside the try, with everything else: a copy that saved and then failed verification
    # still has a profile on the simulator, and asserting before the cleanup is how the next
    # run finds it there.
    try:
        draft = data(await client.get(f"/api/profile-drafts/{placed['draft']['id']}"))["draft"]
        assert draft["status"] == "pushed", summary
        assert draft["pushed_device_profile_id"] == device_id
        # The machine's own list agrees, which is a stronger statement than the load the
        # round trip already did: `req:profiles:list` re-reads every file.
        listed = {profile.id: profile for profile in await device.list_profiles()}
        assert device_id in listed
        assert listed[device_id].label.endswith(TEST_MARK)

        # Selecting is a separate, deliberate action: a sync never does it.
        await device.select_profile(device_id)
        assert await brew_with(device_id), (
            f"the simulator would not brew the synced profile within {BREW_TIMEOUT_S:.0f}s; "
            "see /tmp/gaggimate-sim.log"
        )
    finally:
        deleted = await client.put(f"/api/profile-board/{row_id}/on-machine", json={"on": False})
        assert deleted.status_code == 200, deleted.text
        removal = await sync(app)
        if device_id not in [i["device_id"] for i in removal["removed"]]:
            # Whatever the sync could not take off, the test does: the simulator's filesystem
            # persists between runs.
            if device_id in {p.id for p in await device.list_profiles()}:
                await delete_now(device, device_id)

    assert device_id not in {profile.id for profile in await device.list_profiles()}
    kinds = [
        (row.kind, row.result) for row in await DeviceWritesRepository(app.state.db).list_writes()
    ]
    assert ("profile_save", "ok") in kinds
    assert ("profile_select", "ok") in kinds
    assert ("profile_delete", "ok") in kinds


async def test_a_second_version_replaces_the_first_and_making_the_first_active_restores_it(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """Replace and restore against the real firmware: star, selection, delete, save.

    The first version is made from the simulator's own profile, which this app did not create,
    so it is a copy beside it. The second is the next version of that copy, so one sync saves
    it, moves the star and the selection to it and then deletes the first. Making the first
    version active again saves it as a new file and removes the second, carrying the selection
    back: the machine ends with one copy holding the first version.
    """
    app, client = live
    device = app.state.connection.client
    await adopt(app)
    created: list[str] = []
    try:
        first = await put_on_board(app, client, provider, await usable_version(app), 8)
        [pushed] = (await sync(app))["pushed"]
        first_id = str(pushed["device_id"])
        created.append(first_id)

        await device.select_profile(first_id)
        second = await put_on_board(app, client, provider, first["draft"]["draft_version_id"], 7)
        assert second["row"]["id"] == first["row"]["id"], "the same profile, its next version"
        summary = await sync(app)
        [pushed] = summary["pushed"]
        second_id = str(pushed["device_id"])
        created.append(second_id)
        assert [item["device_id"] for item in summary["removed"]] == [first_id]
        listed = {p.id for p in await device.list_profiles()}
        assert second_id in listed and first_id not in listed
        assert (await device.load_profile(second_id)).selected, "the selection moved first"

        went = await client.put(
            f"/api/profile-board/{second['row']['id']}/active-version",
            json={"version_id": first["draft"]["draft_version_id"]},
        )
        assert went.status_code == 200, went.text
        summary = await sync(app)
        [pushed] = summary["pushed"]
        restored_id = str(pushed["device_id"])
        created.append(restored_id)
        assert [item["device_id"] for item in summary["removed"]] == [second_id]
        listed = {p.id for p in await device.list_profiles()}
        assert restored_id in listed and second_id not in listed
        restored = await device.load_profile(restored_id)
        assert restored.selected, "the selection went back with it"
        kept = await ProfilesRepository(app.state.db).get_version(
            first["draft"]["draft_version_id"]
        )
        assert kept is not None and kept.profile is not None
        assert canonical_profile_json(restored) == canonical_profile_json(
            Profile.model_validate(kept.profile).for_new_device_profile()
        )
    finally:
        listed = {p.id for p in await device.list_profiles()}
        for device_id in created:
            if device_id in listed:
                await delete_now(device, device_id)


async def test_one_sync_pushes_replaces_and_clears_a_favourite_on_the_real_firmware(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """The profile board's write phase against the real firmware, in one sync.

    The first sync here, with the switch on, adopts the simulator's own profiles (nothing
    written). Then a draft goes on the board and a sync saves it; a second draft of that copy
    replaces the first on the board, the person takes it off the home screen, and one sync
    saves the new version, removes the old one and clears the star the firmware gave the new
    one. The firmware's own `saveProfile`, `deleteProfile` and favourite handling are what
    answer, so a quirk the fake lacks (a star that does not clear, a delete that leaves the
    file) fails here.
    """
    app, client = live
    device = app.state.connection.client
    created: list[str] = []
    try:
        await adopt(app)
        first = await put_on_board(app, client, provider, await usable_version(app), 8)
        [pushed] = (await sync(app))["pushed"]
        first_id = str(pushed["device_id"])
        created.append(first_id)
        assert (await device.load_profile(first_id)).favorite, "the firmware stars a new profile"

        second = await put_on_board(app, client, provider, first["draft"]["draft_version_id"], 7)
        assert second["row"]["id"] == first["row"]["id"], "same profile, next version"
        off = await client.put(
            f"/api/profile-board/{second['row']['id']}/starred", json={"starred": False}
        )
        assert off.status_code == 200, off.text

        summary = await sync(app)

        [pushed] = summary["pushed"]
        second_id = str(pushed["device_id"])
        created.append(second_id)
        assert [item["device_id"] for item in summary["removed"]] == [first_id]
        listed = {p.id for p in await device.list_profiles()}
        assert second_id in listed and first_id not in listed
        assert not (await device.load_profile(second_id)).favorite, "the star was cleared"
        rows = await DeviceWritesRepository(app.state.db).list_writes(limit=200)
        deletes = [r.device_id for r in rows if r.kind == "profile_delete" and r.result == "ok"]
        assert deletes == [first_id]

        quiet = await sync(app)
        assert quiet["writes"] == 0, "the next sync has nothing left to do"
    finally:
        listed = {p.id for p in await device.list_profiles()}
        for device_id in created:
            if device_id in listed:
                await delete_now(device, device_id)


# ── the profile list against the real firmware ──────────────────────


async def display_edit(profile_id: str, **changes: Any) -> None:
    """Edit a profile's file the way the display does: a save under the same id.

    From a throwaway socket of this test's own, because `GaggimateClient` never overwrites a
    profile (it refuses a save that carries an id), and that is what a person at the machine
    does. The firmware keeps the id and replaces the content.
    """
    async with websockets.connect(f"ws://{SIM_HOST}/ws", max_size=None) as socket:
        await socket.send(json.dumps({"tp": "req:profiles:load", "id": profile_id, "rid": "l"}))
        document: dict[str, Any] | None = None
        async with asyncio.timeout(10):
            async for raw in socket:
                message = json.loads(raw)
                if message.get("rid") == "l":
                    document = dict(message["profile"])
                    break
        assert document is not None
        document.update(changes)
        await socket.send(json.dumps({"tp": "req:profiles:save", "profile": document, "rid": "s"}))
        async with asyncio.timeout(10):
            async for raw in socket:
                if json.loads(raw).get("rid") == "s":
                    break


async def made_on_the_display(device: Any, label: str) -> str:
    """A profile the app never pushed: saved straight through the client, as a display would."""
    base = next(p for p in await device.list_profiles() if not p.utility)
    stored = await device.save_profile(base.for_new_device_profile(label=label))
    assert stored.id is not None
    return str(stored.id)


async def test_a_file_the_app_never_saw_joins_the_list_and_switching_it_off_removes_it(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = live
    device = app.state.connection.client
    await adopt(app)
    file = await made_on_the_display(device, "Made on the display (sim)")
    try:
        found = await sync(app)
        assert [i["reason"] for i in found["adopted"]] == ["unseen"]
        assert found["removed"] == [], "the sync that finds a file never removes it"
        board = data(await client.get("/api/profile-board"))
        row = next(r for r in board["rows"] if r["row"]["device_profile_id"] == file)
        assert row["on_machine"] is True

        await client.put(f"/api/profile-board/{row['row']['id']}/on-machine", json={"on": False})
        removal = await sync(app)

        assert [i["device_id"] for i in removal["removed"]] == [file]
        assert file not in {p.id for p in await device.list_profiles()}
        # A profile switched on whose file is gone is pushed again.
        await client.put(f"/api/profile-board/{row['row']['id']}/on-machine", json={"on": True})
        back = await sync(app)
        [pushed] = back["pushed"]
        created = str(pushed["device_id"])
        assert created != file
    finally:
        for p in await device.list_profiles():
            if p.label == "Made on the display (sim)":
                await delete_now(device, str(p.id))


async def test_an_older_version_made_active_replaces_the_newer_file_on_the_real_firmware(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    app, client = live
    device = app.state.connection.client
    await adopt(app)
    try:
        first = await put_on_board(app, client, provider, await usable_version(app), 8)
        await sync(app)
        second = await put_on_board(app, client, provider, first["draft"]["draft_version_id"], 7)
        [pushed] = (await sync(app))["pushed"]
        newer_id = str(pushed["device_id"])
        row_id = second["row"]["id"]
        versions = data(await client.get(f"/api/profile-board/{row_id}/versions"))["versions"]
        older = versions[1]

        done = await client.put(
            f"/api/profile-board/{row_id}/active-version", json={"version_id": older["version_id"]}
        )
        assert done.status_code == 200, done.text
        summary = await sync(app)

        [restored] = summary["pushed"]
        assert [i["device_id"] for i in summary["removed"]] == [newer_id]
        listed = {p.id for p in await device.list_profiles()}
        assert str(restored["device_id"]) in listed and newer_id not in listed
    finally:
        for p in await device.list_profiles():
            if str(p.label).endswith(TEST_MARK):
                await delete_now(device, str(p.id))


async def test_a_profile_edited_on_the_display_is_a_conflict_and_keeping_the_apps_side_replaces_it(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    app, client = live
    device = app.state.connection.client
    await adopt(app)
    try:
        placed = await put_on_board(app, client, provider, await usable_version(app), 8)
        [pushed] = (await sync(app))["pushed"]
        file = str(pushed["device_id"])
        row_id = placed["row"]["id"]
        await display_edit(
            file, temperature=float((await device.load_profile(file)).temperature) + 2
        )

        held = await sync(app)

        assert [i["label"] for i in held["conflicts"]] == [pushed["label"]]
        assert held["pushed"] == [] and held["removed"] == [] and held["writes"] == 0
        assert file in {p.id for p in await device.list_profiles()}
        seen = data(await client.get(f"/api/profile-board/{row_id}/conflict"))
        kept = await client.post(
            f"/api/profile-board/{row_id}/conflict",
            json={"keep": "app", "content_hash": seen["machine"]["content_hash"]},
        )
        assert kept.status_code == 200, kept.text

        replaced = await sync(app)

        [new] = replaced["pushed"]
        assert [i["device_id"] for i in replaced["removed"]] == [file]
        listed = {p.id for p in await device.list_profiles()}
        assert str(new["device_id"]) in listed and file not in listed
    finally:
        for p in await device.list_profiles():
            if str(p.label).endswith(TEST_MARK):
                await delete_now(device, str(p.id))


async def snapshot_profiles(device: Any) -> list[dict[str, Any]]:
    """Every profile on the simulator exactly as the firmware serves it: id, star, selection."""
    return [
        (await device.load_profile(str(p.id))).to_device() for p in await device.list_profiles()
    ]


async def restore_profiles(device: Any, snapshot: list[dict[str, Any]]) -> None:
    """Put the simulator's profile files, ids, stars and selection back as they were."""
    for p in await device.list_profiles():
        await delete_now(device, str(p.id))
    for document in snapshot:
        saved = {k: v for k, v in document.items() if k not in ("selected", "favorite")}
        await save_with_id(saved)
    for document in snapshot:
        profile_id = str(document["id"])
        if document.get("favorite"):
            await device.favorite_profile(profile_id)
        else:
            await device.unfavorite_profile(profile_id)
    for document in snapshot:
        if document.get("selected"):
            await device.select_profile(str(document["id"]))


async def save_with_id(document: dict[str, Any]) -> None:
    """Save a profile under its own id, as the display does (the client never overwrites)."""
    async with websockets.connect(f"ws://{SIM_HOST}/ws", max_size=None) as socket:
        await socket.send(json.dumps({"tp": "req:profiles:save", "profile": document, "rid": "s"}))
        async with asyncio.timeout(10):
            async for raw in socket:
                if json.loads(raw).get("rid") == "s":
                    break


async def test_a_firmware_default_switched_off_is_removed_and_back_on_pushed_again(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """The machine's own selected profile, on the real firmware, starting from any state.

    A fresh simulator holds a single selected "Default", which is correctly left when nothing
    else is on the machine, so the test puts an app profile on the board first: it is pushed
    in the same sync and takes the selection before the default goes.
    """
    app, client = live
    device = app.state.connection.client
    await adopt(app)
    before = await snapshot_profiles(device)
    try:
        await put_on_board(app, client, provider, await usable_version(app), 8)
        await sync(app)
        board = data(await client.get("/api/profile-board?live=true"))
        target = next(
            r
            for r in board["rows"]
            if not r["utility"] and not r["row"]["label"].endswith(TEST_MARK)
        )
        row_id, label = target["row"]["id"], target["row"]["label"]
        file = target["machine"]["device_id"]
        await device.select_profile(file)  # whatever earlier runs left selected
        await client.put(f"/api/profile-board/{row_id}/on-machine", json={"on": False})

        off = await sync(app)

        assert [i["device_id"] for i in off["removed"]] == [file] and not off["failures"]
        loaded = [await device.load_profile(str(p.id)) for p in await device.list_profiles()]
        assert file not in {p.id for p in loaded}
        assert any(p.selected for p in loaded), "the selection moved before the file went"

        await client.put(f"/api/profile-board/{row_id}/on-machine", json={"on": True})
        back = await sync(app)

        [pushed] = back["pushed"]
        assert pushed["label"] == label and not back["failures"]
        assert (await sync(app))["writes"] == 0
    finally:
        await restore_profiles(device, before)


async def test_a_wiped_machine_pauses_and_resuming_puts_the_profiles_back(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = live
    device = app.state.connection.client
    await adopt(app)
    before = await snapshot_profiles(device)
    labels = sorted(str(d["label"]) for d in before)
    try:
        for p in await device.list_profiles():
            await delete_now(device, str(p.id))
        paused = await sync(app)

        assert "looks reset" in str(paused["paused"]) and paused["writes"] == 0
        board = data(await client.get("/api/profile-board?live=true"))
        assert board["resume_preview"]["push"] == len(before)
        resumed = await client.post("/api/profile-board/resume")
        assert resumed.status_code == 200, resumed.text

        back = await sync(app)

        assert len(back["pushed"]) == len(before) and not back["failures"]
        assert sorted(str(p.label) for p in await device.list_profiles()) == labels
    finally:
        # The simulator's files persist between runs: restore them exactly, ids and selection
        # included, whatever happened above.
        await restore_profiles(device, before)
