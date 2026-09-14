"""Fixtures for cleanup and notes write-back: a stocked machine, a synced archive, writes on.

The app is the real app with the real lifespan, pointed at
:class:`~gaggiclanker.device.fake.FakeDevice`. That matters here for the reason
it matters in `tests/drafts`: the thing under test deletes shots off a machine
and overwrites its notes cards, and a mocked client would test the mock. The
fake reproduces both firmware behaviours from the source — a delete flags the
index entry and removes the file, a notes save stores the document verbatim and
rewrites the index's rating and (string-only) volume.

The archive is the fifteen-shot one from `tests/sync/conftest.py`, which is
deliberately awkward: one shot's bytes do not parse (quarantine), one is flagged
deleted on the device already, one has notes. Those are the rows the eligibility
matrix is about, so the fixture that provides them is the one the sync suite
already maintains rather than a second copy that would drift from it.

Writes are **off** in `live` and on in `writes_on`, the same split the draft tests use
and for the same reason: off is the shipped default, and a suite that turned
them on globally would never notice if the default moved.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.cleanup.service import CleanupService
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app

#: The ids the awkward shots carry in the seeded archive, re-exported so a test
#: reads as "the quarantined one" rather than as a number.
from tests.sync.conftest import (
    CORRUPT_ID,
    DELETED_ID,
    FIRST_ID,
    NOTES_ID,
    SMALL_COUNT,
    build_archive_device,
)

__all__ = [
    "CORRUPT_ID",
    "DELETED_ID",
    "FIRST_ID",
    "NOTES_ID",
    "SMALL_COUNT",
    "data",
    "drain_tasks",
    "error",
]

#: Free space the fake reports, in bytes, so a `free_space` policy has something
#: to act on. Exactly the registry's minimum floor (1024 KB) times two, so a
#: test can put the floor either side of it without running into the validator
#: that refuses a floor below the firmware's own 500 KB threshold.
FAKE_SPIFFS_TOTAL = 8 * 1024 * 1024
FAKE_SPIFFS_FREE = 2 * 1024 * 1024


@pytest.fixture
async def fake_device() -> AsyncIterator[FakeDevice]:
    """A started machine with fifteen shots on it, four of them awkward."""
    device = build_archive_device(SMALL_COUNT, header_only=False)
    device.identity.update(
        {
            "spiffsTotal": FAKE_SPIFFS_TOTAL,
            "spiffsUsed": FAKE_SPIFFS_TOTAL - FAKE_SPIFFS_FREE,
            "spiffsFree": FAKE_SPIFFS_FREE,
        }
    )
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


@pytest.fixture
async def live(
    env: EnvSettings, fake_device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """The app, connected to the fake machine, with its shots already archived.

    One `sync_shots` pass before the test starts, because everything here is
    about shots the archive *has*: a cleanup with an empty archive is a cleanup
    that correctly refuses every shot, which is a different test.
    """
    monkeypatch.setenv("GAGGIMATE_HOST", fake_device.address)
    monkeypatch.setenv("GAGGIMATE_TIMEOUT_S", "5")
    async with running_app(env) as (app, client):
        assert await app.state.connection.client.wait_connected(5.0)
        await app.state.connection.engine.sync_identity(trigger="test")
        await app.state.connection.engine.sync_shots(trigger="test")
        # The pace is the one thing a test cannot afford at its real value:
        # half a second per shot is the whole point of it existing, and forty
        # of those would be twenty seconds of suite. One test asserts on the
        # default; everything else runs flat out.
        app.state.cleanup.pace_seconds = 0.0
        yield app, client


@pytest.fixture
async def writes_on(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> tuple[FastAPI, httpx.AsyncClient]:
    """The same app with `deviceWritesEnabled` on, set the way a person would."""
    app, client = live
    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    return app, client


async def drain_tasks(app: FastAPI, prefix: str, *, timeout: float = 5.0) -> None:
    """Wait for every registered background task whose name starts with ``prefix``.

    The registry releases a name in a done-callback, so "the task finished" and
    "the name is free" are one loop turn apart; a test that asserts on either
    has to wait for the task rather than for the dictionary.
    """
    for _ in range(20):
        pending = [task for name, task in app.state.tasks._tasks.items() if name.startswith(prefix)]
        if not pending:
            return
        await asyncio.wait(pending, timeout=timeout)
        await asyncio.sleep(0)
    raise AssertionError(f"background tasks matching {prefix!r} did not finish")


def service(app: FastAPI) -> CleanupService:
    cleanup: CleanupService = app.state.cleanup
    return cleanup


def data(response: httpx.Response) -> Any:
    assert response.status_code < 400, response.text
    body = response.json()
    assert body["ok"] is True, body
    return body["data"]


def error(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert body["ok"] is False, body
    return dict(body["error"])
