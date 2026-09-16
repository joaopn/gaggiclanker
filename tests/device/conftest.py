"""Fixtures for the device tests: a fake machine and a client wired to it.

Both are per-test. The fake binds port 0, so a hundred tests can run without
choosing a port, and the client's backoff is compressed to milliseconds so a
reconnect test finishes in the time a real one would spend on its first sleep.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sse_starlette.sse import AppStatus

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.fake import FakeDevice, build_fake_device
from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings
from tests.conftest import NO_WEB_DIST

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# The real curve is 1 s -> 60 s (client.BACKOFF_INITIAL_S). A test proves the
# *shape* — that it reconnects, and that repeated failures wait longer — and
# the shape is identical at 1/50th scale.
TEST_BACKOFF_INITIAL = 0.02
TEST_BACKOFF_MAX = 0.2


@pytest.fixture
async def fake_device() -> AsyncIterator[FakeDevice]:
    """A started fake GaggiMate loaded from `tests/fixtures`."""
    device = build_fake_device(FIXTURES)
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


@pytest.fixture
async def device_client(fake_device: FakeDevice) -> AsyncIterator[GaggimateClient]:
    """A started client, already connected to :func:`fake_device`."""
    client = GaggimateClient(
        fake_device.address,
        timeout=2.0,
        backoff_initial=TEST_BACKOFF_INITIAL,
        backoff_max=TEST_BACKOFF_MAX,
        slog_retry_budget=2.0,
    )
    await client.start()
    assert await client.wait_connected(5.0), "the fake device did not accept a connection"
    try:
        yield client
    finally:
        await client.stop()


async def read_events(
    response: httpx.Response, count: int, timeout: float = 5.0
) -> list[dict[str, Any]]:
    """Parse ``count`` SSE frames out of a streaming response.

    Hand-rolled rather than borrowed from the front end's parser because this
    asserts on what the *server* writes, including the CRLF separator
    `sse-starlette` uses by default — the exact detail that made the browser
    client see zero events in the front-end shell.
    """
    events: list[dict[str, Any]] = []
    buffer = ""
    async with asyncio.timeout(timeout):
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\r\n\r\n" in buffer or "\n\n" in buffer:
                separator = "\r\n\r\n" if "\r\n\r\n" in buffer else "\n\n"
                frame, buffer = buffer.split(separator, 1)
                parsed = parse_sse_frame(frame)
                if parsed is not None:
                    events.append(parsed)
                    if len(events) >= count:
                        return events
    return events


def parse_sse_frame(frame: str) -> dict[str, Any] | None:
    event = "message"
    data: list[str] = []
    for line in frame.splitlines():
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    if not data:
        return None
    return {"event": event, "data": json.loads("\n".join(data))}


def _clear_sse_shutdown_flag() -> None:
    """Undo sse-starlette's record that "the server is shutting down".

    `sse_starlette` monkey-patches `uvicorn.Server.handle_exit` and remembers
    the shutdown in a **module-global** (`AppStatus.should_exit`) that nothing
    ever clears. In production that is right — there is one server and one
    shutdown — but in a test session one `serving()` block ending would drain
    every server-sent stream every later test opens, wherever it lives, and the
    suite would pass or fail on file ordering alone.

    Clearing it around each block makes these tests order-independent. It
    touches only the library's own flag, not the app.
    """
    AppStatus.should_exit = False


@contextlib.asynccontextmanager
async def serving(env: EnvSettings) -> AsyncIterator[tuple[FastAPI, str]]:
    """The app on a real loopback port, for the tests ASGI cannot do.

    ``httpx.ASGITransport`` awaits the application to completion before it
    hands back a response (``httpx/_transports/asgi.py``: ``assert
    response_complete.is_set()``), so a server-sent event stream — which by
    design never completes — deadlocks it. Every other test in this repository
    is happier in-process; the ones that read an event stream need a socket, a
    real uvicorn and its lifespan.
    """
    app = create_app(env, web_dist=NO_WEB_DIST)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    # See `_clear_sse_shutdown_flag`: a previous block in this session may have
    # left the flag set, which would drain this server's streams the instant
    # they opened.
    _clear_sse_shutdown_flag()
    # `serve()` installs its own SIGINT/SIGTERM handlers and restores the
    # previous ones on the way out (`Server.capture_signals`), so pytest keeps
    # its own Ctrl-C behaviour once the block ends.
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield app, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
        _clear_sse_shutdown_flag()
