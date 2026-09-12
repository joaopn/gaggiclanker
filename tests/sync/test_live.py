"""Live ingest: the machine finishes a shot and the small_archive has it seconds later.

This is the only file that runs the engine's loops rather than calling its
methods, because the loops are the thing under test — `evt:history-shot-saved`
arriving on the socket has to reach SQLite without anybody asking.
"""

from __future__ import annotations

import asyncio

import pytest

from gaggiclanker.device.fake import synthetic_slog_bytes
from gaggiclanker.domain.ids import pad6
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.sync.engine import SHOT_INGESTED_EVENT
from tests.sync.conftest import SMALL_COUNT, Archive

#: The chunk's acceptance figure. Generous for a loopback fake; the point is
#: that the path is a push and not a poll.
INGEST_DEADLINE_S = 2.0

NEW_SHOT_ID = 900


async def _wait_for_shot(small_archive: Archive, device_id: str, timeout: float) -> None:
    async with asyncio.timeout(timeout):
        while True:
            if await small_archive.engine.shots.get_by_device_id(1, device_id) is not None:
                return
            await asyncio.sleep(0.02)


async def _settle(small_archive: Archive, timeout: float = 20.0) -> None:
    """Wait for the pass the engine starts at boot to finish.

    Every test here is about what happens *after* the small_archive has caught up, and
    the startup backfill publishes forty-eight ingest events of its own. Waiting
    for the count rather than for one shot is what stops a later assertion
    picking up a leftover.
    """
    async with asyncio.timeout(timeout):
        while (await small_archive.engine.shots.counts()).total < SMALL_COUNT - 1:
            await asyncio.sleep(0.02)


async def test_a_saved_shot_is_stored_and_announced(
    small_archive: Archive, tasks: TaskRegistry
) -> None:
    await small_archive.engine.start(tasks)
    await _settle(small_archive)
    small_archive.drain()

    await small_archive.device.run_brew(
        NEW_SHOT_ID, slog_bytes=synthetic_slog_bytes(shot_id=NEW_SHOT_ID)
    )

    event = await small_archive.wait_for(SHOT_INGESTED_EVENT, timeout=INGEST_DEADLINE_S)
    assert event.data["device_id"] == pad6(NEW_SHOT_ID)

    stored = await small_archive.engine.shots.get_by_device_id(1, pad6(NEW_SHOT_ID))
    assert stored is not None
    assert stored.sample_count == 40
    assert stored.quarantined is False
    assert len(await small_archive.engine.shots.samples(stored.id)) == 40


async def test_the_live_run_is_labelled_live(small_archive: Archive, tasks: TaskRegistry) -> None:
    """`backfill` and `live` are the same code and different ledger entries.

    Worth separating: "the periodic diff has not succeeded since Tuesday" and
    "no push has landed since Tuesday" are different faults with different
    causes, and only the ledger can tell them apart afterwards.
    """
    await small_archive.engine.start(tasks)
    await _settle(small_archive)

    await small_archive.device.run_brew(
        NEW_SHOT_ID, slog_bytes=synthetic_slog_bytes(shot_id=NEW_SHOT_ID)
    )
    await _wait_for_shot(small_archive, pad6(NEW_SHOT_ID), timeout=INGEST_DEADLINE_S)

    runs = await small_archive.engine.runs.last_runs()
    assert "live" in runs
    assert runs["live"].trigger == "shot_saved"


async def test_a_push_that_beats_the_index_is_still_fetched(
    small_archive: Archive, tasks: TaskRegistry
) -> None:
    """The index row and the event are written in quick succession, not atomically.

    A diff that only ever trusted the index would small_archive this shot a quarter of
    an hour late, on the next periodic pass, for no reason.
    """
    await small_archive.engine.start(tasks)
    await _settle(small_archive)

    small_archive.device.add_shot(NEW_SHOT_ID, synthetic_slog_bytes(shot_id=NEW_SHOT_ID))
    # The file is servable, the index does not list it yet.
    small_archive.device.hidden_from_index.add(NEW_SHOT_ID)
    await small_archive.device.emit_shot_saved(NEW_SHOT_ID)

    await _wait_for_shot(small_archive, pad6(NEW_SHOT_ID), timeout=INGEST_DEADLINE_S)


async def test_identity_is_read_on_connect(small_archive: Archive, tasks: TaskRegistry) -> None:
    await small_archive.engine.start(tasks)

    async with asyncio.timeout(10.0):
        while True:
            machine = small_archive.engine.machine
            if machine is not None and machine.hardware_string:
                break
            await asyncio.sleep(0.02)

    assert machine is not None
    assert machine.host == small_archive.device.address
    assert machine.display_version
    # The capability flags come from the status *state* frame, which the device
    # sends once per connection; pressure gating depends on them being right.
    assert machine.has_pressure is True
    assert machine.has_dimming is True
    # …and the PID and brew delay from GET /api/settings, which is read-only.
    assert machine.pid
    assert machine.brew_delay_ms == 800
    assert machine.settings is not None


async def test_a_machine_that_goes_away_is_an_error_not_a_crash(small_archive: Archive) -> None:
    """A machine that is switched off is an ordinary Tuesday, not an exception.

    The run is recorded as a failure — "the last six runs all failed" is a thing
    an operator needs to be able to see — and the loops carry on, because the
    machine coming back is the normal case and a dead task would mean nobody
    ever notices that it did.
    """
    await small_archive.device.stop()

    run = await small_archive.engine.sync_shots(trigger="test")

    assert run.status == "error"
    assert run.error
    assert (await small_archive.engine.shots.counts()).total == 0

    last_error = await small_archive.engine.runs.last_error()
    assert last_error is not None
    assert last_error.id == run.id


@pytest.mark.parametrize("shot_id", [1, 999_999])
async def test_the_pushed_id_is_padded_for_the_url(
    small_archive: Archive, tasks: TaskRegistry, shot_id: int
) -> None:
    """`evt:history-shot-saved` carries an unpadded int; `/h/` wants six digits.

    An unpadded id reads a file that does not exist and looks exactly like "no
    such shot", which is the kind of bug that hides for a month.
    """
    await small_archive.engine.start(tasks)
    await _settle(small_archive)

    await small_archive.device.run_brew(shot_id, slog_bytes=synthetic_slog_bytes(shot_id=shot_id))
    await _wait_for_shot(small_archive, pad6(shot_id), timeout=INGEST_DEADLINE_S)

    assert f"/api/history/{pad6(shot_id)}.slog" in small_archive.device.requests
