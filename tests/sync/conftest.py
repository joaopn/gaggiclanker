"""Fixtures for the storage and sync suite: a stocked fake machine and an archive.

The fake device is loaded with about fifty shots built from the **real** `.slog`
fixtures (v5, 13 fields, 118-213 samples each) re-served under fresh ids and
timestamps, plus the maintainer's shot-129 export re-encoded. Cloning real bytes
rather than synthesising fifty files is the point: a backfill test that only ever
sees a generated shot proves the loop works on shots we already know parse.

Four of the fifty are deliberately awkward, because those are the cases that
break a sync loop at three in the morning:

* one file whose bytes are not a `.slog` at all (quarantine);
* one that is header-only for the first two requests (the device is still
  writing it);
* one whose index entry carries the DELETED flag (the file is gone);
* one that has notes.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.fake import FakeDevice, build_fake_device, default_notes
from gaggiclanker.domain.models import SHOT_FLAG_DELETED
from gaggiclanker.domain.slog import encode_slog, parse_slog
from gaggiclanker.infra.sse import EventBus, SseEvent, SseEventBus
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.sync.engine import SyncEngine

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: The seeded archive's shape. Ids start well above the fixtures' own so a test
#: can tell "the shot I added" from "the shot the builder cloned".
FIRST_ID = 100
SHOT_COUNT = 50
NOTES_ID = 102
CORRUPT_ID = 105
HEADER_ONLY_ID = 107
DELETED_ID = 109

#: Enough shots to contain all four awkward ones. The API and load tests use
#: this rather than the full fifty: what they exercise is the HTTP surface and
#: the scheduler, and paying for fifty diagnostics runs per test setup buys no
#: coverage the backfill suite does not already have.
SMALL_COUNT = 15

#: One hour between shots, so ordering assertions are about the data rather than
#: about clock resolution.
SECONDS_BETWEEN_SHOTS = 3600
FIRST_EPOCH = 1_770_000_000

#: The compressed backoff every device test uses: the real curve is 1 s -> 60 s
#: and a test proves the shape, not the constants.
TEST_BACKOFF_INITIAL = 0.02
TEST_BACKOFF_MAX = 0.2


def fixture_slogs() -> list[bytes]:
    """The real `.slog` bytes this repository ships, in filename order."""
    return [path.read_bytes() for path in sorted((FIXTURES / "slog").glob("*.slog"))]


def corrupt_bytes() -> bytes:
    """Bytes that are emphatically not a `.slog`.

    Long enough to get past the client's "still being written" check (which
    treats anything shorter than a header as not-ready and would retry for the
    whole budget) and wrong in the one byte that matters: the magic.
    """
    return b"NOPE" + bytes(range(256)) * 4


@cache
def _source_shots() -> tuple[tuple[bytes, ...], tuple[dict[str, Any], ...]]:
    """The fixture bytes and profiles, read and decoded once for the whole session.

    Cached because every test in this package builds a device, and re-reading
    four files and re-encoding fifty of them per test turned a fast suite into
    a slow one for no extra coverage.
    """
    source = build_fake_device(FIXTURES)
    return (
        tuple(shot.slog_bytes for _, shot in sorted(source.shots.items())),
        tuple(source.profiles),
    )


@cache
def _restamped(raw: bytes, epoch: int) -> bytes:
    """The same shot, recorded at a different time.

    Cloning a fixture without moving its `startEpoch` would give the archive
    fifty shots at three distinct timestamps, which quietly stops the ordering
    and date-range assertions from testing anything. Re-encoding through the
    codec (rather than patching four bytes) keeps the file honest: the header
    checksum-free format means a hand-patched epoch would parse, but a
    re-encode is what proves the bytes are still a `.slog` we can read.
    """
    slog = parse_slog(raw)
    slog.header.start_epoch = epoch
    return encode_slog(slog)


def build_archive_device(count: int = SHOT_COUNT, *, header_only: bool = True) -> FakeDevice:
    """A fake machine carrying ``count`` shots, four of them awkward.

    ``header_only=False`` drops the "still being written" shot. It is the one
    awkward case that costs real wall-clock time — the client backs off for
    three quarters of a second before it gets the whole file — and only the
    backfill suite asserts on it.
    """
    real, profiles = _source_shots()

    device = FakeDevice()
    # Deep copies: the profile documents are cached for the session and several
    # tests edit them in place to make the machine's profile list change.
    device.profiles = copy.deepcopy(list(profiles))

    for offset in range(count):
        shot_id = FIRST_ID + offset
        epoch = FIRST_EPOCH + offset * SECONDS_BETWEEN_SHOTS
        raw = (
            corrupt_bytes()
            if shot_id == CORRUPT_ID
            else _restamped(real[offset % len(real)], epoch)
        )
        device.add_shot(
            shot_id,
            raw,
            timestamp=epoch,
            profile_name=f"Fixture {offset % len(real)}",
            notes=default_notes(shot_id) if shot_id == NOTES_ID else None,
            header_only_requests=2 if header_only and shot_id == HEADER_ONLY_ID else 0,
        )

    # Deletion is flag-only on the device: the row stays in the index for ever
    # and the file behind it is gone, which is why the diff must not try to
    # fetch it.
    device.shots[DELETED_ID].entry.flags |= SHOT_FLAG_DELETED
    device.missing_files.add(DELETED_ID)
    return device


@dataclass(slots=True)
class Archive:
    """Everything a sync test needs, already wired together."""

    device: FakeDevice
    client: GaggimateClient
    db: Database
    bus: SseEventBus
    engine: SyncEngine
    #: Everything published on the SSE bus since the fixture opened. The
    #: subscription is taken before the first pass runs, because the bus only
    #: delivers to subscribers that already existed: a test registering lazily
    #: would silently assert on an empty list.
    queue: asyncio.Queue[SseEvent]

    def drain(self) -> list[SseEvent]:
        """Every event published so far, removing them from the queue."""
        events: list[SseEvent] = []
        while not self.queue.empty():
            events.append(self.queue.get_nowait())
        return events

    async def wait_for(self, name: str, timeout: float = 2.0) -> SseEvent:
        """Block until an event of this name arrives, or fail the test."""
        try:
            async with asyncio.timeout(timeout):
                while True:
                    event = await self.queue.get()
                    if event.event == name:
                        return event
        except TimeoutError:
            raise AssertionError(f"no {name!r} event within {timeout}s") from None


async def _open_database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "gaggiclanker.db")
    await db.connect()
    await run_migrations(db)
    return db


@contextlib.asynccontextmanager
async def archive_for(device: FakeDevice, tmp_path: Path) -> AsyncIterator[Archive]:
    """An engine over a real SQLite file, pointed at ``device``.

    The engine's loops are **not** started: a test calls `sync_shots()` and
    friends directly so its assertions run after the pass rather than racing it.
    ``tests/sync/test_live.py`` is the one that starts the loops, because "the
    push reaches the database" is the thing it is testing.
    """
    db = await _open_database(tmp_path)
    client = GaggimateClient(
        device.address,
        timeout=2.0,
        backoff_initial=TEST_BACKOFF_INITIAL,
        backoff_max=TEST_BACKOFF_MAX,
        slog_retry_budget=2.0,
    )
    await client.start()
    assert await client.wait_connected(5.0), "the fake device did not accept a connection"

    bus: SseEventBus = EventBus[SseEvent]()
    engine = SyncEngine(client, db, bus, index_interval=3600.0, profile_interval=3600.0)

    with bus.subscribe() as queue:
        try:
            yield Archive(device=device, client=client, db=db, bus=bus, engine=engine, queue=queue)
        finally:
            await client.stop()
            await db.close()


@pytest.fixture
async def stocked_device() -> AsyncIterator[FakeDevice]:
    """A started fake machine with the fifty-shot archive on it."""
    device = build_archive_device()
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


@pytest.fixture
async def archive(stocked_device: FakeDevice, tmp_path: Path) -> AsyncIterator[Archive]:
    async with archive_for(stocked_device, tmp_path) as wired:
        yield wired


@pytest.fixture
async def small_device() -> AsyncIterator[FakeDevice]:
    """The same archive at :data:`SMALL_COUNT`, and without the slow shot.

    For the suites whose subject is not the size of a backfill — notes,
    profiles. Fifty real shots cost a second of diagnostics per test setup and
    buy nothing the backfill suite does not already prove.
    """
    device = build_archive_device(SMALL_COUNT, header_only=False)
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


@pytest.fixture
async def small_archive(small_device: FakeDevice, tmp_path: Path) -> AsyncIterator[Archive]:
    async with archive_for(small_device, tmp_path) as wired:
        yield wired


@pytest.fixture
async def empty_device() -> AsyncIterator[FakeDevice]:
    """A started fake machine with no shots and no profiles."""
    device = FakeDevice()
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


@pytest.fixture
async def tasks() -> AsyncIterator[TaskRegistry]:
    registry = TaskRegistry()
    try:
        yield registry
    finally:
        await registry.cancel_all()
