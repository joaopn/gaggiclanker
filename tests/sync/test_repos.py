"""The repositories on their own: a real SQLite file, no device in sight.

What is worth pinning here is the part the sync tests cannot see — the
transaction boundary, the keyset cursor, and the rule that a quarantined shot
carries no samples, enforced by the repository rather than by the caller
remembering.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.machines import MachineRepository, MachineUpsert
from gaggiclanker.db.repos.shots import ShotInsert, ShotSampleRow, ShotsRepository
from gaggiclanker.db.repos.sync import SyncRepository, SyncRunUpdate
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.sync.derive import derive_shot

RAW = b"SHOT" + bytes(508) + bytes(30)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "archive.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


def a_shot(device_id: str, *, started_at: str | None = None) -> ShotInsert:
    return ShotInsert(
        device_id=device_id,
        raw_slog=RAW,
        started_at=started_at or f"2026-01-01T{int(device_id) % 24:02d}:00:00.000Z",
        start_epoch=1_770_000_000 + int(device_id),
        duration_ms=28_000,
        profile_id_on_device="9bar",
        profile_name_on_device="9 Bar Espresso",
        sample_count=2,
    )


def samples(count: int = 2) -> list[ShotSampleRow]:
    return [
        ShotSampleRow(t_ms=index * 250, ct=92.0 + index, cp=9.0, si=0x0004)
        for index in range(count)
    ]


async def test_a_shot_and_its_samples_land_together(db: Database) -> None:
    repo = ShotsRepository(db)

    shot_id = await repo.insert(a_shot("000101"), samples())

    stored = await repo.get(shot_id)
    assert stored is not None
    assert stored.device_id == "000101"
    assert [s.t_ms for s in await repo.samples(shot_id)] == [0, 250]


async def test_a_failed_sample_insert_takes_the_shot_with_it(db: Database) -> None:
    """One transaction, so a half-written curve is impossible.

    A shot whose samples only half-landed would render as an extraction that
    stops in the middle, and nothing downstream could tell that apart from a
    machine that lost power.
    """
    repo = ShotsRepository(db)
    duplicated = [*samples(), ShotSampleRow(t_ms=0)]  # violates PK (shot_id, t_ms)

    with pytest.raises(Exception):  # noqa: B017 - the driver's IntegrityError
        await repo.insert(a_shot("000102"), duplicated)

    assert (await repo.counts()).total == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM shot_samples") == 0


async def test_a_quarantined_shot_may_not_carry_samples(db: Database) -> None:
    repo = ShotsRepository(db)
    shot = a_shot("000103")
    shot.quarantined = True
    shot.quarantine_reason = "bad magic"

    with pytest.raises(ValueError, match="quarantined"):
        await repo.insert(shot, samples())


async def test_deleting_a_shot_takes_its_samples(db: Database) -> None:
    """ON DELETE CASCADE, which only works because the connection sets foreign_keys."""
    repo = ShotsRepository(db)
    shot_id = await repo.insert(a_shot("000104"), samples())

    await db.execute("DELETE FROM shots WHERE id = ?", (shot_id,))

    assert await db.fetch_value("SELECT COUNT(*) FROM shot_samples") == 0


async def test_the_same_shot_cannot_be_stored_twice(db: Database) -> None:
    """UNIQUE (device_id): the diff's idempotence has a backstop."""
    repo = ShotsRepository(db)
    await repo.insert(a_shot("000105"))

    with pytest.raises(Exception):  # noqa: B017 - the driver's IntegrityError
        await repo.insert(a_shot("000105"))


async def test_a_shot_with_no_timestamp_sorts_last(db: Database) -> None:
    """`startEpoch < 10000` is "NTP never synced", not 1970.

    It has to have a place in the total order or keyset pagination cannot walk
    past it, and last is where an undated shot belongs.
    """
    repo = ShotsRepository(db)
    undated = a_shot("000106")
    undated.started_at = None
    undated.start_epoch = 0
    await repo.insert(undated)
    await repo.insert(a_shot("000107", started_at="2026-01-02T09:00:00.000Z"))

    page = await repo.list_shots(limit=10)

    assert [row.device_id for row in page.items] == ["000107", "000106"]


async def test_the_cursor_walks_the_whole_archive_exactly_once(db: Database) -> None:
    repo = ShotsRepository(db)
    for index in range(25):
        await repo.insert(
            a_shot(f"{index:06d}", started_at=f"2026-01-01T{index % 24:02d}:00:00.000Z")
        )

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(20):
        page = await repo.list_shots(limit=7, cursor=cursor)
        seen.extend(row.device_id for row in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert len(seen) == 25
    assert len(set(seen)) == 25


async def test_a_cursor_we_did_not_issue_is_a_value_error(db: Database) -> None:
    """The route turns this into a 400 naming the parameter, not a 500."""
    with pytest.raises(ValueError, match="cursor"):
        await ShotsRepository(db).list_shots(cursor="not-a-cursor-$$$")


async def test_counts_split_the_archive_the_way_the_status_page_reads_it(db: Database) -> None:
    repo = ShotsRepository(db)
    await repo.insert(a_shot("000108"), samples(3))
    quarantined = a_shot("000109")
    quarantined.quarantined = True
    quarantined.quarantine_reason = "bad magic"
    await repo.insert(quarantined)
    deleted = a_shot("000110")
    deleted.deleted_on_device = True
    await repo.insert(deleted)

    counts = await repo.counts()

    assert counts.total == 3
    assert counts.quarantined == 1
    assert counts.deleted_on_device == 1
    assert counts.samples == 3


async def test_the_machine_upsert_never_blanks_what_it_does_not_know(db: Database) -> None:
    """A reconnect that catches no state frame must not erase the board's capabilities."""
    repo = MachineRepository(db)
    await repo.update_identity(
        MachineUpsert(host="machine.local", has_pressure=True, name="Gaggia")
    )

    again = await repo.update_identity(
        MachineUpsert(host="machine.local", display_version="v1.9.0")
    )

    assert again.has_pressure is True
    assert again.name == "Gaggia"
    assert again.display_version == "v1.9.0"
    assert await db.fetch_value("SELECT COUNT(*) FROM machines") == 1


async def test_a_new_address_moves_the_machine_rather_than_forking_the_archive(
    db: Database,
) -> None:
    """A display board that changed IP used to become a second machine.

    Everything it had pulled stayed behind on the old row: its shots, its
    profiles, its Sets, and the active Set that stopped auto-assigning. The host
    is a setting now, so a new address is an update.
    """
    repo = MachineRepository(db)
    await repo.update_identity(MachineUpsert(host="192.168.1.40", name="Kitchen"))
    await ShotsRepository(db).insert(a_shot("000200"))

    moved = await repo.update_identity(MachineUpsert(host="192.168.1.77"))

    assert moved.host == "192.168.1.77"
    assert moved.name == "Kitchen"
    assert await db.fetch_value("SELECT COUNT(*) FROM machines") == 1
    assert await db.fetch_value("SELECT COUNT(*) FROM shots") == 1


async def test_a_run_is_recorded_and_closed(db: Database) -> None:
    repo = SyncRepository(db)
    run_id = await repo.start_run("backfill", "startup")

    open_run = await repo.get_run(run_id)
    assert open_run is not None
    assert open_run.status == "running"
    assert open_run.finished_at is None

    await repo.finish_run(run_id, SyncRunUpdate(shots_inserted=3))

    closed = await repo.get_run(run_id)
    assert closed is not None
    assert closed.status == "ok"
    assert closed.shots_inserted == 3
    assert closed.finished_at is not None


async def test_a_run_with_an_error_is_a_failure(db: Database) -> None:
    repo = SyncRepository(db)
    run_id = await repo.start_run("profiles", "connected")

    await repo.finish_run(run_id, SyncRunUpdate(errors=1, error="the machine is not connected"))

    assert (await repo.last_error()) is not None
    runs = await repo.last_runs()
    assert runs["profiles"].status == "error"


async def test_the_event_feed_is_trimmed(db: Database) -> None:
    """An unbounded audit table on an appliance is a disk-full incident in waiting."""
    repo = SyncRepository(db)
    for index in range(30):
        await repo.add_event("shot_ingested", message=str(index))

    removed = await repo.trim_events(keep=10)

    assert removed == 20
    remaining = await repo.recent_events(limit=50)
    assert len(remaining) == 10
    assert remaining[0].message == "29"


async def test_a_shot_can_be_derived_and_stored_without_a_live_device(
    db: Database, tmp_path: Path
) -> None:
    """The seam the JSON importer imports through.

    An importer has a parsed `.slog` and no live machine: the shot it is reading
    was deleted from the device months ago, which is the whole reason to import
    it. So the derivation is a free function and nothing on the way in needs the
    machine to have been connected — the singleton row is already there.
    """
    raw = (Path(__file__).resolve().parents[1] / "fixtures" / "slog").glob("*.slog")
    payload = next(iter(sorted(raw))).read_bytes()

    derived = derive_shot(
        parse_slog(payload),
        payload,
        device_id="000196",
        source="import",
        has_pressure=True,
    )
    shot_id = await ShotsRepository(db).insert(derived.shot, derived.samples)

    stored = await ShotsRepository(db).get(shot_id)
    assert stored is not None
    assert stored.source == "import"
    assert stored.execution_score is not None
    assert stored.diagnostics is not None
    assert derived.diagnostics_error is None
    assert len(await ShotsRepository(db).samples(shot_id)) == stored.sample_count > 100
