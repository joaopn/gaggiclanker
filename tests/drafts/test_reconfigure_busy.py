"""A board sync with a profile write in flight holds off a machine-settings change.

The write phase of a sync is a save, a read-back and a removal; a change of host between the
save and the read-back would verify the profile against the wrong machine, or against none.
The write phase runs inside the sync engine's pass, so the connection knows about it the way
it knows about any pull: it refuses a change until the pass has finished.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import error
from tests.drafts.helpers import draft_of
from tests.drafts.test_board import adopted, put
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module

ELSEWHERE = "127.0.0.1:9"


async def test_a_board_sync_holds_off_a_connection_change_between_the_save_and_the_read_back(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client, _ = adopted
    await put(client, await draft_of(app, client, provider, "9 Bar Espresso", 8))
    machine = app.state.connection.client
    original = machine.load_profile
    saved = asyncio.Event()
    release = asyncio.Event()
    holding = False

    async def held(profile_id: str) -> Any:
        # Saved already, not yet read back: the worst moment to lose the machine.
        nonlocal holding
        if saved.is_set() and not holding:
            holding = True
            await release.wait()
        return await original(profile_id)

    original_save = machine.save_profile

    async def noted_save(profile: Any) -> Any:
        stored = await original_save(profile)
        saved.set()
        return stored

    monkeypatch.setattr(machine, "load_profile", held)
    monkeypatch.setattr(machine, "save_profile", noted_save)
    sync = asyncio.create_task(app.state.connection.engine.sync_profiles(trigger="test"))
    try:
        async with asyncio.timeout(5.0):
            while not holding:
                await asyncio.sleep(0.01)
        assert app.state.connection.busy() == "a sync"
        before = app.state.connection.client
        response = await client.patch("/api/settings", json={"gaggimateHost": ELSEWHERE})
        assert response.status_code == 409, response.text
        assert "a sync" in error(response)["message"]
        assert app.state.connection.client is before
    finally:
        release.set()
        run = await sync

    assert run.status == "ok", run.error
    assert app.state.connection.busy() is None
