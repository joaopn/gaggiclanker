"""A profile push or rollback in flight holds off a machine-settings change.

A push is a save, a read-back and a mirror; a change of host between the save and
the read-back would verify the profile against the wrong machine, or against
none. Both run inside the request, so the connection knows about them because
the draft service registers each one for as long as it talks to the machine.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import data, error
from tests.drafts.test_api import a_draft
from tests.llm.conftest import FakeProvider

ELSEWHERE = "127.0.0.1:9"


async def refused_while(app: FastAPI, client: httpx.AsyncClient, running: str) -> None:
    async with asyncio.timeout(5.0):
        while app.state.connection.busy() != running:
            await asyncio.sleep(0.01)
    before = app.state.connection.client
    response = await client.patch("/api/settings", json={"gaggimateHost": ELSEWHERE})
    assert response.status_code == 409, response.text
    assert running in error(response)["message"]
    settings = (await client.get("/api/settings")).json()["data"]
    assert settings["gaggimateHost"]["value"] == before.host
    assert app.state.connection.client is before


async def test_a_profile_push_holds_off_a_connection_change(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client = writes_on
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    machine = app.state.connection.client
    original = machine.load_profile
    release = asyncio.Event()

    async def held(profile_id: str) -> Any:
        # Saved already, not yet read back: the worst moment to lose the machine.
        await release.wait()
        return await original(profile_id)

    monkeypatch.setattr(machine, "load_profile", held)
    push = asyncio.create_task(client.post(f"/api/profile-drafts/{draft['id']}/push", json={}))
    try:
        await refused_while(app, client, "a profile push")
    finally:
        release.set()
        response = await push

    assert data(response)["draft"]["status"] == "pushed"
    assert app.state.connection.busy() is None


async def test_a_profile_rollback_holds_off_a_connection_change(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
    provider: FakeProvider,
    fake_device: FakeDevice,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client = writes_on
    fake_device.mutate_on_save = lambda stored: {**stored, "temperature": 91}
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    data(await client.post(f"/api/profile-drafts/{draft['id']}/push", json={}))
    machine = app.state.connection.client
    original = machine.delete_profile
    release = asyncio.Event()

    async def held(profile_id: str) -> None:
        await release.wait()
        await original(profile_id)

    monkeypatch.setattr(machine, "delete_profile", held)
    rollback = asyncio.create_task(
        client.post(f"/api/profile-drafts/{draft['id']}/rollback", json={})
    )
    try:
        await refused_while(app, client, "a profile rollback")
    finally:
        release.set()
        response = await rollback

    assert data(response)["pushed_device_profile_id"] is None
