"""Backfilling fifty real shots off the fake machine, awkward ones included.

The acceptance criteria this file pins:

* a backfill of the fixtures is **idempotent** — a second run inserts nothing;
* a corrupt `.slog` is **quarantined**, visible in the ledger, and produces no
  sample rows;
* samples come back in `t_ms` order with all fourteen fields plus the phase.
"""

from __future__ import annotations

import pytest

from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.sync.engine import SHOT_INGESTED_EVENT, SHOT_QUARANTINED_EVENT
from tests.sync.conftest import (
    CORRUPT_ID,
    DELETED_ID,
    HEADER_ONLY_ID,
    SHOT_COUNT,
    Archive,
)

#: Everything on the machine except the one whose file it has already deleted.
EXPECTED_STORED = SHOT_COUNT - 1


async def test_backfill_stores_every_live_shot(archive: Archive) -> None:
    run = await archive.engine.sync_shots(trigger="test")

    assert run.status == "ok"
    assert run.shots_seen == SHOT_COUNT
    assert run.shots_inserted == EXPECTED_STORED
    counts = await archive.engine.shots.counts()
    assert counts.total == EXPECTED_STORED
    assert counts.samples > 5000, "fifty real shots carry 118-213 samples each"


async def test_the_deleted_entry_is_never_fetched(archive: Archive) -> None:
    """A DELETED index row means the file is gone; asking for it is a wasted request.

    The device keeps index rows for ever and clears only the file, so a diff
    that fetched every unknown id would 404 on every deletion the machine has
    ever performed — forty of them after a month of storage pressure.
    """
    await archive.engine.sync_shots(trigger="test")

    stored = await archive.engine.shots.known_states(1)
    assert pad6(DELETED_ID) not in stored
    assert f"/api/history/{pad6(DELETED_ID)}.slog" not in archive.device.requests


async def test_a_corrupt_slog_is_quarantined_with_its_bytes(archive: Archive) -> None:
    await archive.engine.sync_shots(trigger="test")

    shot = await archive.engine.shots.get_by_device_id(1, pad6(CORRUPT_ID))
    assert shot is not None
    assert shot.quarantined is True
    assert shot.quarantine_reason
    assert shot.raw_bytes > 0, "the bytes are the point of quarantining rather than dropping"

    # The archive's central rule: bytes we could not parse never produce
    # samples, because there is nothing trustworthy to produce them from.
    assert await archive.engine.shots.samples(shot.id) == []

    counts = await archive.engine.shots.counts()
    assert counts.quarantined == 1


async def test_the_quarantined_bytes_are_the_bytes_the_device_served(archive: Archive) -> None:
    await archive.engine.sync_shots(trigger="test")

    shot = await archive.engine.shots.get_by_device_id(1, pad6(CORRUPT_ID))
    assert shot is not None
    raw = await archive.engine.shots.raw_slog(shot.id)
    assert raw == archive.device.shots[CORRUPT_ID].slog_bytes


async def test_a_header_only_file_is_retried_until_it_is_complete(archive: Archive) -> None:
    """The device serves a header with no samples while it is still writing.

    `evt:history-shot-saved` fires after the file is closed, but the index
    poller reaches a shot earlier than that, and a client that took the first
    answer would archive a shot with no curve — permanently, because the diff
    never looks at a shot it already holds.
    """
    await archive.engine.sync_shots(trigger="test")

    shot = await archive.engine.shots.get_by_device_id(1, pad6(HEADER_ONLY_ID))
    assert shot is not None
    assert shot.quarantined is False
    assert shot.sample_count > 100
    assert shot.incomplete is False
    assert archive.device.requests.count(f"/api/history/{pad6(HEADER_ONLY_ID)}.slog") == 3, (
        "two header-only answers, then the real file"
    )


async def test_samples_are_ordered_and_carry_every_field(archive: Archive) -> None:
    await archive.engine.sync_shots(trigger="test")

    shot = await archive.engine.shots.get_by_device_id(1, pad6(101))
    assert shot is not None
    samples = await archive.engine.shots.samples(shot.id)

    assert [s.t_ms for s in samples] == sorted(s.t_ms for s in samples)
    assert len(samples) == shot.sample_count

    first = samples[0]
    # The v5 fixtures carry thirteen of the fourteen fields (fieldsMask 0x1fff);
    # `wp` arrived in v7 and is legitimately NULL here, which is a different
    # fact from "zero" and is why the columns are nullable.
    for field in ("tt", "ct", "tp", "cp", "fl", "tf", "pf", "vf", "v", "ev", "pr", "si"):
        assert getattr(first, field) is not None, field
    assert first.wp is None
    assert {s.phase_number for s in samples} != {None}


async def test_stored_samples_match_the_parsed_file(archive: Archive) -> None:
    """The row values are the parser's, in real units — not a second decoding."""
    await archive.engine.sync_shots(trigger="test")

    shot = await archive.engine.shots.get_by_device_id(1, pad6(101))
    assert shot is not None
    raw = await archive.engine.shots.raw_slog(shot.id)
    assert raw is not None
    parsed = parse_slog(raw)

    stored = await archive.engine.shots.samples(shot.id)
    assert len(stored) == len(parsed.samples)
    assert stored[0].ct == parsed.samples[0].ct
    assert stored[-1].t_ms == parsed.samples[-1].t


async def test_a_second_run_inserts_nothing(archive: Archive) -> None:
    first = await archive.engine.sync_shots(trigger="test")
    before = await archive.engine.shots.counts()

    second = await archive.engine.sync_shots(trigger="test")

    assert first.shots_inserted == EXPECTED_STORED
    assert second.shots_inserted == 0
    assert second.shots_quarantined == 0
    assert second.shots_updated == 0
    assert await archive.engine.shots.counts() == before


async def test_derived_columns_are_filled_in(archive: Archive) -> None:
    await archive.engine.sync_shots(trigger="test")

    shot = await archive.engine.shots.get_by_device_id(1, pad6(101))
    assert shot is not None
    assert shot.phases, "per-phase statistics are derived at ingest"
    assert shot.diagnostics is not None
    assert shot.execution_score is not None
    assert 1.0 <= shot.execution_score <= 10.0
    assert shot.execution_reason
    assert shot.started_at is not None
    assert shot.duration_ms > 0
    assert shot.slog_version == 5
    assert shot.sample_interval_ms == 250


async def test_the_run_is_recorded_with_its_counts(archive: Archive) -> None:
    run = await archive.engine.sync_shots(kind="backfill", trigger="startup")

    ledger = await archive.engine.runs.get_run(run.id)
    assert ledger is not None
    assert ledger.kind == "backfill"
    assert ledger.trigger == "startup"
    assert ledger.finished_at is not None
    assert ledger.shots_inserted == EXPECTED_STORED
    assert ledger.shots_quarantined == 1

    events = await archive.engine.runs.recent_events(limit=200)
    assert any(e.kind == "shot_quarantined" for e in events)
    assert sum(1 for e in events if e.kind == "shot_ingested") == EXPECTED_STORED - 1


async def test_sse_reports_each_shot(archive: Archive) -> None:
    await archive.engine.sync_shots(trigger="test")

    names = [event.event for event in archive.drain()]
    assert names.count(SHOT_INGESTED_EVENT) == EXPECTED_STORED - 1
    assert names.count(SHOT_QUARANTINED_EVENT) == 1


async def test_an_index_rating_change_is_reconciled(archive: Archive) -> None:
    """The device rewrites index entries in place; the `.slog` never changes.

    A rating typed on the machine, or a dose entered in its notes card (which
    overrides `volume`), only ever shows up here. Re-fetching the file would
    learn nothing.
    """
    await archive.engine.sync_shots(trigger="test")
    entry = archive.device.shots[101].entry
    entry.rating = 5
    entry.volume_g = 41.5

    run = await archive.engine.sync_shots(trigger="test")

    assert run.shots_updated == 1
    shot = await archive.engine.shots.get_by_device_id(1, pad6(101))
    assert shot is not None
    assert shot.index_rating == 5
    assert shot.index_volume_g == 41.5


async def test_a_shot_deleted_on_the_device_is_flagged_not_dropped(archive: Archive) -> None:
    """gaggiclanker is the archive; the machine is a buffer.

    When `cleanupHistory()` frees space it flags the entry and deletes the file.
    Our copy stays — that is the whole point — and only gains a marker saying
    the machine no longer has it.
    """
    await archive.engine.sync_shots(trigger="test")
    archive.device.shots[101].entry.flags |= 0x02
    archive.device.missing_files.add(101)

    await archive.engine.sync_shots(trigger="test")

    shot = await archive.engine.shots.get_by_device_id(1, pad6(101))
    assert shot is not None
    assert shot.deleted_on_device is True
    assert shot.sample_count > 0, "the samples we archived are not the device's to delete"
    counts = await archive.engine.shots.counts()
    assert counts.deleted_on_device == 1


@pytest.mark.parametrize("kind", ["backfill", "live"])
async def test_both_shot_kinds_are_accepted_by_the_ledger(archive: Archive, kind: str) -> None:
    run = await archive.engine.sync_shots(kind=kind, trigger="test")
    assert run.kind == kind
    assert run.status == "ok"
