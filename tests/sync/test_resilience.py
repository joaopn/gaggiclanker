"""Three ways a sync goes wrong, and what has to survive each.

* the events loop is the only reader of a **lossy** queue, so it must never
  wait on anything that can take minutes;
* a machine that vanishes mid-backfill has to end the run as a failure and
  leave nothing half-written;
* the list query has to use its index, because "correct but sorts the whole
  archive on every page" is the kind of bug nothing ever reports.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gaggiclanker.db.repos.shots import SORT_KEYS
from gaggiclanker.device.fake import FakeDevice, synthetic_slog_bytes
from gaggiclanker.domain.ids import pad6
from gaggiclanker.infra.tasks import TaskRegistry
from tests.sync.conftest import Archive, archive_for, build_archive_device

#: Slow enough that a backfill is unmistakably in flight while the test acts.
SLOW_FETCH_S = 0.3
BUSY_SHOTS = 10
NEW_SHOT_ID = 900

#: More than the per-subscriber queue depth (`infra/sse.py`: 256). A parked
#: events loop drops its oldest events under this, and the oldest is the
#: `ShotSaved` that arrived first.
FLOOD_FRAMES = 320


@pytest.fixture
async def slow_device() -> FakeDevice:
    device = build_archive_device(BUSY_SHOTS, header_only=False)
    device.history_delay_s = SLOW_FETCH_S
    await device.start()
    return device


async def _stored(archive: Archive, device_id: str) -> bool:
    return await archive.engine.shots.get_by_device_id(1, device_id) is not None


async def test_a_shot_saved_during_a_backfill_is_not_dropped(
    slow_device: FakeDevice, tmp_path: Path
) -> None:
    """The regression behind "never block the events loop".

    The device event bus is lossy by design: a subscriber that falls behind
    drops its **oldest** events. The events loop used to await `sync_identity()`
    on the identity broadcast, which takes the engine lock — so during a
    backfill it was parked, the queue filled with 2 Hz telemetry, and the
    `ShotSaved` for the shot the user had just pulled was the first thing thrown
    away. The shot then waited for the fifteen-minute re-diff.

    Everything the events loop does is now synchronous: it records and pokes,
    and the worker loops do the waiting.
    """
    registry = TaskRegistry()
    try:
        async with archive_for(slow_device, tmp_path) as archive:
            await archive.engine.start(registry)

            # Wait until the backfill is genuinely under way — the lock is held
            # and, before the fix, the events loop was behind it.
            async with asyncio.timeout(10):
                while (await archive.engine.shots.counts()).total == 0:
                    await asyncio.sleep(0.02)

            await slow_device.run_brew(
                NEW_SHOT_ID, slog_bytes=synthetic_slog_bytes(shot_id=NEW_SHOT_ID)
            )
            for _ in range(FLOOD_FRAMES):
                await slow_device.emit_status(ct=93.0, tt=93.0, pr=9.0)

            async with asyncio.timeout(20):
                while not await _stored(archive, pad6(NEW_SHOT_ID)):
                    await asyncio.sleep(0.05)

            # Frames *were* dropped — 320 arriving faster than any consumer
            # can drain a 256-deep queue is the bus working as designed, and a
            # lost telemetry frame at 2 Hz is invisible. What must survive is
            # the `ShotSaved`, and it does because the loop is never parked long
            # enough for it to reach the front of the queue.
            assert archive.client.events.dropped > 0, "the flood did not actually flood"
            stored = await archive.engine.shots.get_by_device_id(1, pad6(NEW_SHOT_ID))
            assert stored is not None
            assert stored.sample_count == 40
    finally:
        await registry.cancel_all()
        await slow_device.stop()


async def test_a_machine_that_vanishes_mid_backfill_fails_the_run(
    slow_device: FakeDevice, tmp_path: Path
) -> None:
    """The run ends as an error, nothing is half-written, and the next pass resumes.

    Two things used to be wrong here. The run was recorded `ok` — only an
    `update.error` made it a failure, and a fetch that died only bumped a
    counter — so `GET /api/sync/status` reported a healthy sync of an archive
    that had silently stopped filling. And the pass kept going, paying a full
    request timeout for every remaining shot, two at a time, with the lock held
    and both other loops waiting behind it.
    """
    host, port = slow_device.host, slow_device.port
    async with archive_for(slow_device, tmp_path) as archive:
        pass_one = asyncio.create_task(archive.engine.sync_shots(trigger="test"))

        async with asyncio.timeout(10):
            while (await archive.engine.shots.counts()).total == 0:
                await asyncio.sleep(0.02)
        await slow_device.stop()

        run = await asyncio.wait_for(pass_one, timeout=30)

        assert run.status == "error"
        assert run.error
        assert run.errors > 0
        last_error = await archive.engine.runs.last_error()
        assert last_error is not None and last_error.id == run.id

        # Whatever landed before the machine went is whole: the shot and its
        # samples are one transaction, so there is no such thing as half a shot.
        partial = await archive.engine.shots.list_shots(limit=200)
        for row in partial.items:
            samples = await archive.engine.shots.samples(row.id)
            assert len(samples) == row.sample_count
        assert 0 < partial.total < BUSY_SHOTS

        # The machine comes back; the next pass picks up where this one stopped.
        revived = build_archive_device(BUSY_SHOTS, header_only=False)
        await revived.start(port=port, host=host)
        try:
            resumed = await archive.engine.sync_shots(trigger="connected")
            assert resumed.status == "ok"
            assert (await archive.engine.shots.counts()).total == BUSY_SHOTS - 1
        finally:
            await revived.stop()


async def test_the_backfill_gives_up_rather_than_timing_out_per_shot(
    slow_device: FakeDevice, tmp_path: Path
) -> None:
    """Three consecutive transport failures mean the machine, not the shot."""
    async with archive_for(slow_device, tmp_path) as archive:
        started = asyncio.get_running_loop().time()
        task = asyncio.create_task(archive.engine.sync_shots(trigger="test"))
        async with asyncio.timeout(10):
            while (await archive.engine.shots.counts()).total == 0:
                await asyncio.sleep(0.02)
        await slow_device.stop()
        run = await asyncio.wait_for(task, timeout=30)

    elapsed = asyncio.get_running_loop().time() - started
    assert run.status == "error"
    # The client's per-request timeout here is 2 s. Nine remaining shots two at
    # a time would be ~9 s of pure waiting; giving up after three is ~2 s.
    assert elapsed < 12, f"the pass took {elapsed:.1f}s after the machine went away"


async def test_the_list_query_uses_its_index(archive: Archive) -> None:
    """No TEMP B-TREE: the sort key and the index are the same expression.

    `idx_shots_list` is an index on `COALESCE(started_at,'') DESC, id DESC`
    because that is the sort key — a plain index on `started_at` is never used,
    the planner cannot see through the COALESCE, and the archive gets sorted in
    memory on every page. That failure is invisible until the archive is big, so
    it is pinned here.
    """
    await archive.engine.sync_shots(trigger="test")
    page = await archive.engine.shots.list_shots(limit=5)
    assert page.next_cursor

    for label, params in (
        ("default page", {"limit": 5}),
        ("cursor page", {"limit": 5, "cursor": page.next_cursor}),
    ):
        plan = await _explain(archive, **params)
        assert "TEMP B-TREE" not in plan, f"{label}: {plan}"
        assert "idx_shots_list" in plan, f"{label}: {plan}"


async def _explain(archive: Archive, **kwargs: object) -> str:
    """The planner's own words for the query `list_shots` runs.

    Built by asking the repository for the same SQL rather than copying it: a
    copy would keep passing after the real query stopped using the index.
    """
    captured: list[tuple[str, object]] = []
    original = archive.db.fetch_all

    async def spy(sql: str, params: object = ()) -> list:  # type: ignore[type-arg]
        if "ORDER BY" in sql:
            captured.append((sql, params))
        return await original(sql, params)  # type: ignore[arg-type]

    archive.db.fetch_all = spy  # type: ignore[method-assign]
    try:
        await archive.engine.shots.list_shots(**kwargs)  # type: ignore[arg-type]
    finally:
        archive.db.fetch_all = original  # type: ignore[method-assign]

    sql, params = captured[-1]
    rows = await archive.db.fetch_all(f"EXPLAIN QUERY PLAN {sql}", params)  # type: ignore[arg-type]
    return " | ".join(str(row["detail"]) for row in rows)


def test_every_sort_the_route_offers_is_one_the_repository_knows() -> None:
    """The OpenAPI enum and the SQL map have to be the same set.

    A sort name is never pasted into SQL — it selects a key from `SORT_KEYS` —
    so a route offering a name the map does not have would be a 400 that reads
    like a server bug.
    """
    from gaggiclanker.api.shots import SortKey

    assert set(SortKey.__value__.__args__) == set(SORT_KEYS)
