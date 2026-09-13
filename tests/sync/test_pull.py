"""Nothing is archived until somebody asks for it, and then everything is.

This is the only file that runs the engine's loops rather than calling its
methods, because the loops are the thing under test. The property they carry is
a negative one and negatives are what rot quietly: after a shot is saved on the
machine, the archive must stay exactly as it was — no timer, no reconnect and
no `evt:history-shot-saved` may put it in — until `request_shot_sync` is
called. The one pass that still runs unasked is identity, and it is here too so
that "unasked" stays a list of one.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from gaggiclanker.device.fake import synthetic_slog_bytes
from gaggiclanker.domain.ids import pad6
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.sync.engine import SHOT_INGESTED_EVENT, _Poke
from tests.sync.conftest import SMALL_COUNT, Archive

#: How long a pull of a loopback fake gets before the test calls it broken.
PULL_DEADLINE_S = 5.0

#: How long to watch an engine that should be doing nothing. Long enough that a
#: pass triggered by a connect or a save would have finished several times over
#: — the archive here is fifty shots off a loopback server — and short enough
#: to sit in a suite that runs on every commit.
IDLE_WATCH_S = 2.0

NEW_SHOT_ID = 900


async def _wait_for_shot(archive: Archive, device_id: str, timeout: float) -> None:
    async with asyncio.timeout(timeout):
        while True:
            if await archive.engine.shots.get_by_device_id(1, device_id) is not None:
                return
            await asyncio.sleep(0.02)


async def _pull(archive: Archive, timeout: float = 20.0) -> None:
    """Ask for a shot pass and wait for the archive to hold the fixture's shots."""
    archive.engine.request_shot_sync("manual")
    async with asyncio.timeout(timeout):
        while (await archive.engine.shots.counts()).total < SMALL_COUNT - 1:
            await asyncio.sleep(0.02)


async def test_a_saved_shot_waits_for_a_pull(small_archive: Archive, tasks: TaskRegistry) -> None:
    """The acceptance criterion, in one test.

    The machine finishes a shot with the socket up and the engine running, and
    the archive does not move. It moves when it is asked to.
    """
    await small_archive.engine.start(tasks)
    await _pull(small_archive)
    small_archive.drain()

    await small_archive.device.run_brew(
        NEW_SHOT_ID, slog_bytes=synthetic_slog_bytes(shot_id=NEW_SHOT_ID)
    )

    # Nothing. Not on the push, not on a timer, not on the reconnect the brew
    # did not cause either.
    await asyncio.sleep(IDLE_WATCH_S)
    assert await small_archive.engine.shots.get_by_device_id(1, pad6(NEW_SHOT_ID)) is None
    assert not [event for event in small_archive.drain() if event.event == SHOT_INGESTED_EVENT]

    small_archive.engine.request_shot_sync("manual")

    event = await small_archive.wait_for(SHOT_INGESTED_EVENT, timeout=PULL_DEADLINE_S)
    assert event.data["device_id"] == pad6(NEW_SHOT_ID)

    stored = await small_archive.engine.shots.get_by_device_id(1, pad6(NEW_SHOT_ID))
    assert stored is not None
    assert stored.sample_count == 40
    assert stored.quarantined is False
    assert len(await small_archive.engine.shots.samples(stored.id)) == 40


async def test_nothing_but_identity_runs_at_startup(
    small_archive: Archive, tasks: TaskRegistry
) -> None:
    """Booting next to a machine full of shots must not start reading it.

    The machine has fifty shots and a socket that comes up immediately, which is
    exactly the state the old engine treated as "go". A box restarted while
    somebody is pulling a shot should cost the machine one settings request,
    not a backfill over its two HTTP slots.
    """
    await small_archive.engine.start(tasks)

    await asyncio.sleep(IDLE_WATCH_S)

    assert (await small_archive.engine.shots.counts()).total == 0
    runs = await small_archive.engine.runs.last_runs()
    assert set(runs) == {"identity"}, runs


async def test_a_reconnect_rereads_identity_and_nothing_else(
    small_archive: Archive, tasks: TaskRegistry
) -> None:
    """The socket coming back says the firmware may have changed, nothing more."""
    await small_archive.engine.start(tasks)
    await _pull(small_archive)
    before = await small_archive.engine.runs.last_runs()

    await small_archive.device.drop_connections()
    assert await small_archive.client.wait_connected(10.0)

    async with asyncio.timeout(10.0):
        while True:
            runs = await small_archive.engine.runs.last_runs()
            if runs["identity"].id != before["identity"].id:
                break
            await asyncio.sleep(0.02)

    # The shot pass is the one from `_pull`, not a new one the reconnect started.
    assert runs["backfill"].id == before["backfill"].id


async def test_the_loops_have_no_timeout(small_archive: Archive) -> None:
    """A timer is the one trigger a test cannot wait out, so it is read directly.

    `_Poke.wait` takes no timeout argument at all, which is what makes "no
    periodic pass" a property of the type rather than of a number somebody
    could set back to 900.
    """
    assert list(inspect.signature(_Poke.wait).parameters) == ["self"]
    assert small_archive.engine is not None  # the fixture is what wires a real engine


async def test_an_announced_shot_the_index_has_not_listed_is_still_pulled(
    small_archive: Archive, tasks: TaskRegistry
) -> None:
    """The index row and the event are written in quick succession, not atomically.

    Somebody who presses pull the moment the machine beeps is inside that
    window. A pull that only ever trusted the index would tell them there is
    nothing new, and be right only about the index.
    """
    await small_archive.engine.start(tasks)
    await _pull(small_archive)

    small_archive.device.add_shot(NEW_SHOT_ID, synthetic_slog_bytes(shot_id=NEW_SHOT_ID))
    # The file is servable, the index does not list it yet.
    small_archive.device.hidden_from_index.add(NEW_SHOT_ID)
    await small_archive.device.emit_shot_saved(NEW_SHOT_ID)
    # Give the announcement time to be recorded — and to not start a pass.
    await asyncio.sleep(0.2)
    assert await small_archive.engine.shots.get_by_device_id(1, pad6(NEW_SHOT_ID)) is None

    small_archive.engine.request_shot_sync("manual")
    await _wait_for_shot(small_archive, pad6(NEW_SHOT_ID), timeout=PULL_DEADLINE_S)


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
async def test_the_announced_id_is_padded_for_the_url(
    small_archive: Archive, tasks: TaskRegistry, shot_id: int
) -> None:
    """`evt:history-shot-saved` carries an unpadded int; `/h/` wants six digits.

    An unpadded id reads a file that does not exist and looks exactly like "no
    such shot", which is the kind of bug that hides for a month.
    """
    await small_archive.engine.start(tasks)
    await _pull(small_archive)

    await small_archive.device.run_brew(shot_id, slog_bytes=synthetic_slog_bytes(shot_id=shot_id))
    small_archive.engine.request_shot_sync("manual")
    await _wait_for_shot(small_archive, pad6(shot_id), timeout=PULL_DEADLINE_S)

    assert f"/api/history/{pad6(shot_id)}.slog" in small_archive.device.requests
