"""Shots quarantined for a format the parser has since learned come back at start.

An app that did not yet read firmware 1.10's version 8 (or the legacy version 6 scale log)
stored such a shot quarantined: its bytes, the reason, the machine's index fields and no
samples. The bytes are the source of truth, so the next start reads them again: a shot that
parses is derived as a fetched one is and taken out of quarantine in one transaction; one that
still fails stays exactly as it was; a second start finds nothing to do.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.typing import EventDict, WrappedLogger

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.db.schema import create_schema
from gaggiclanker.domain.models import SI_ACTIVE_SCALE_CONNECTED, IndexEntry
from gaggiclanker.domain.slog import SlogError, parse_slog
from gaggiclanker.settings import EnvSettings
from gaggiclanker.sync import derive
from gaggiclanker.sync.derive import (
    DERIVATION_VERSION,
    derive_shot,
    index_fields,
    unquarantine_readable_shots,
)
from tests.conftest import running_app
from tests.domain.helpers import FIXTURES, edited_v8
from tests.recipes.conftest import make_profile_version
from tests.sync.conftest import corrupt_bytes

NEW = FIXTURES / "slog_firmware_1_10"
V8 = NEW / "shot_225_v8.slog"
LEGACY_V6 = NEW / "shot_196_legacy_v6.slog"

#: What an app that did not read version 8 wrote as the reason.
V8_REASON = "unsupported .slog version 8 (this build reads v1-v7)"


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The events the derive module logs, caught by a logger of the test's own."""
    events: list[dict[str, Any]] = []

    def catch(_: WrappedLogger, __: str, event: EventDict) -> EventDict:
        events.append(dict(event))
        raise structlog.DropEvent

    monkeypatch.setattr(
        derive,
        "log",
        structlog.wrap_logger(
            structlog.PrintLogger(),
            processors=[catch],
            wrapper_class=structlog.make_filtering_bound_logger(0),
        ),
    )
    return events


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "gaggiclanker.db")
    await database.connect()
    await create_schema(database)
    try:
        yield database
    finally:
        await database.close()


async def _quarantine(
    db: Database,
    device_id: str,
    raw: bytes,
    reason: str,
    *,
    indexed: bool = True,
    volume_g: float = 31.9,
) -> int:
    """Store a shot the way the sync engine stored one it could not read."""
    entry: IndexEntry | None = None
    try:
        header = parse_slog(raw).header
    except SlogError:
        pass
    else:
        if not indexed:
            header = header.model_copy(update={"start_epoch": 0})
        entry = IndexEntry(
            id=int(device_id),
            timestamp=header.start_epoch,
            duration_ms=header.duration_ms,
            volume_g=volume_g,
            rating=4,
            flags=0x01,
            profile_id=header.profile_id,
            profile_name=header.profile_name,
        )
    shot = ShotInsert(
        device_id=device_id,
        raw_slog=raw,
        quarantined=True,
        quarantine_reason=reason,
        **index_fields(entry),
    )
    return await ShotsRepository(db).insert(shot)


async def _row(db: Database, shot_id: int) -> dict[str, object]:
    row = await db.fetch_one("SELECT * FROM shots WHERE id = ?", (shot_id,))
    assert row is not None
    return dict(row)


async def _sample_count(db: Database, shot_id: int) -> int:
    return int(
        await db.fetch_value("SELECT COUNT(*) FROM shot_samples WHERE shot_id = ?", (shot_id,)) or 0
    )


def test_the_shots_were_unreadable_to_a_parser_without_the_format() -> None:
    """The precondition: garbage is garbage, and the two real shots parse today."""
    with pytest.raises(SlogError):
        parse_slog(corrupt_bytes())
    assert parse_slog(V8.read_bytes()).version == 8
    assert parse_slog(LEGACY_V6.read_bytes()).version == 6


async def test_a_quarantined_v8_shot_comes_back_with_its_samples_and_no_profile_link(
    db: Database,
) -> None:
    raw = V8.read_bytes()
    header = parse_slog(raw).header
    version_id = await make_profile_version(db, "A profile the machine holds")
    await ProfilesRepository(db).upsert_device_profile(
        device_id=header.profile_id, version_id=version_id
    )
    shot_id = await _quarantine(db, "000225", raw, V8_REASON)

    before = await _row(db, shot_id)
    assert before["quarantined"] == 1 and before["quarantine_reason"] == V8_REASON
    assert before["profile_version_id"] is None and before["diagnostics_json"] is None
    assert await _sample_count(db, shot_id) == 0

    shots = ShotsRepository(db)
    assert await unquarantine_readable_shots(shots) == 1

    after = await _row(db, shot_id)
    assert after["quarantined"] == 0 and after["quarantine_reason"] is None
    assert after["raw_slog"] == raw
    assert after["slog_version"] == 8
    assert await _sample_count(db, shot_id) == after["sample_count"] == 136
    # The mirror holds the header's profile id, and the shot is still not linked to it: no row
    # says since when the file has held that version, so nothing says it was the file's version
    # when this shot was brewed.
    assert after["profile_version_id"] is None
    assert after["derivation_version"] == DERIVATION_VERSION
    assert after["scale_connected"] == 1 and after["final_weight_g"] == pytest.approx(31.9)
    # What the machine's index said of the shot is still there, and it is the same row.
    assert after["index_volume_g"] == pytest.approx(31.9) and after["index_rating"] == 4
    assert after["id"] == shot_id and after["device_id"] == "000225"

    # It is the shot a fetch would have stored with no profile: the same derivation of the same
    # bytes, and the same samples.
    stored = await shots.get(shot_id)
    assert stored is not None
    fresh = derive_shot(parse_slog(raw), raw, device_id="000225")
    assert after["phases_json"] == fresh.shot.phases_json
    assert after["diagnostics_json"] == fresh.shot.diagnostics_json
    assert [(s.t_ms, s.v, s.si) for s in await shots.samples(shot_id)] == [
        (s.t_ms, s.v, s.si) for s in fresh.samples
    ]
    assert stored.has_scale

    # What the sync does with any shot that has no link, at its next profile pass: the file's
    # version at that moment. Nothing here prevents it; it is the rule for every late shot.
    assert await shots.link_unlinked_by_device_profile(header.profile_id, version_id) == 1


async def test_the_legacy_v6_scale_log_comes_back_too(db: Database) -> None:
    shot_id = await _quarantine(
        db,
        "000196",
        LEGACY_V6.read_bytes(),
        "sample size mismatch: the header says 26 bytes per sample, a version 6 file has 28",
    )
    assert await unquarantine_readable_shots(ShotsRepository(db)) == 1
    after = await _row(db, shot_id)
    assert after["quarantined"] == 0 and after["slog_version"] == 6
    assert await _sample_count(db, shot_id) > 100


async def test_a_shot_that_still_fails_stays_exactly_as_it_was(db: Database) -> None:
    bad = await _quarantine(db, "000300", corrupt_bytes(), "bad magic")
    # An imported shot's bytes are the export's JSON, never a `.slog`: it is not a candidate.
    imported = await _quarantine(db, "000301", b'{"id": "000301"}', "not a shot export")
    good = await _quarantine(db, "000225", V8.read_bytes(), V8_REASON)
    held = {shot_id: await _row(db, shot_id) for shot_id in (bad, imported)}

    assert await unquarantine_readable_shots(ShotsRepository(db)) == 1

    assert (await _row(db, good))["quarantined"] == 0
    for shot_id, was in held.items():
        assert await _row(db, shot_id) == was
        assert await _sample_count(db, shot_id) == 0


async def test_a_second_run_changes_nothing(db: Database) -> None:
    shots = ShotsRepository(db)
    good = await _quarantine(db, "000225", V8.read_bytes(), V8_REASON)
    bad = await _quarantine(db, "000300", corrupt_bytes(), "bad magic")

    assert await unquarantine_readable_shots(shots) == 1
    after_first = {shot_id: await _row(db, shot_id) for shot_id in (good, bad)}
    samples = await shots.samples(good)

    assert await unquarantine_readable_shots(shots) == 0
    assert {shot_id: await _row(db, shot_id) for shot_id in (good, bad)} == after_first
    assert await shots.samples(good) == samples


async def test_a_shot_that_cannot_be_stored_stays_quarantined(db: Database) -> None:
    """A shot another row already holds the identity of is left in quarantine, whole.

    The recovery is one transaction: when the write refuses, the quarantined row keeps its
    reason, its bytes and no samples, and the start goes on.
    """
    raw = V8.read_bytes()
    held = derive_shot(parse_slog(raw), raw, device_id="000225")
    await ShotsRepository(db).insert(held.shot, held.samples)
    # The same number stored with no start time (a shot announced before the index listed it):
    # reading its bytes gives it the start time the other shot holds, which one row may not.
    twin = await _quarantine(db, "000225", raw, V8_REASON, indexed=False)
    assert (await _row(db, twin))["start_epoch"] == 0
    was = await _row(db, twin)

    assert await unquarantine_readable_shots(ShotsRepository(db)) == 0
    assert await _row(db, twin) == was
    assert await _sample_count(db, twin) == 0


async def test_a_failure_of_the_last_write_leaves_the_shot_quarantined_whole(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recovery is one transaction: the samples are its last write, and when they fail the
    row's own update before them is undone."""
    shot_id = await _quarantine(db, "000225", V8.read_bytes(), V8_REASON)
    was = await _row(db, shot_id)
    shots = ShotsRepository(db)

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the disk refused the samples")

    monkeypatch.setattr(ShotsRepository, "_insert_samples", refuse)
    assert await unquarantine_readable_shots(shots) == 0
    assert await _row(db, shot_id) == was
    assert was["quarantined"] == 1 and await _sample_count(db, shot_id) == 0
    monkeypatch.undo()

    # The first half: the same shot comes back as soon as the write can be made.
    assert await unquarantine_readable_shots(shots) == 1
    assert (await _row(db, shot_id))["quarantined"] == 0


async def test_the_start_reads_quarantined_shots_again(
    env: EnvSettings, logs: list[dict[str, Any]]
) -> None:
    db = Database(env.database_path)
    await db.connect()
    try:
        await create_schema(db)
        good = await _quarantine(db, "000225", V8.read_bytes(), V8_REASON)
        bad = await _quarantine(db, "000300", corrupt_bytes(), "bad magic")
    finally:
        await db.close()

    async with running_app(env) as (app, _client):
        shots = ShotsRepository(app.state.db)
        recovered = await shots.get(good)
        assert recovered is not None and not recovered.quarantined
        assert recovered.sample_count == 136 and recovered.has_scale
        assert len(await shots.samples(good)) == 136
        still = await shots.get(bad)
        assert still is not None and still.quarantined
    unquarantined = [e for e in logs if e.get("event") == "shots_unquarantined"]
    assert [e["count"] for e in unquarantined] == [1]


async def test_the_index_volume_of_a_recovered_shot_is_read_against_its_header(
    db: Database,
) -> None:
    """A no-scale version 8 shot's index volume is the header's estimate; a typed one is kept.

    The machine's figure was stored while the shot was unreadable. The first half is that figure
    sitting in the row; the second is the recovery dropping the estimate and keeping a dose out
    somebody typed on the machine.
    """
    no_scale = edited_v8(lambda _i, sample: {"si": (sample.si or 0) & ~SI_ACTIVE_SCALE_CONNECTED})
    estimate = await _quarantine(db, "000301", no_scale, V8_REASON)
    typed = await _quarantine(db, "000302", no_scale, V8_REASON, volume_g=36.5)
    with_scale = await _quarantine(db, "000303", V8.read_bytes(), V8_REASON)
    for shot_id, volume in ((estimate, 31.9), (typed, 36.5), (with_scale, 31.9)):
        assert (await _row(db, shot_id))["index_volume_g"] == pytest.approx(volume)

    assert await unquarantine_readable_shots(ShotsRepository(db)) == 3

    assert (await _row(db, estimate))["index_volume_g"] is None
    assert (await _row(db, typed))["index_volume_g"] == pytest.approx(36.5)
    assert (await _row(db, with_scale))["index_volume_g"] == pytest.approx(31.9)
    recovered = await ShotsRepository(db).get(estimate)
    assert recovered is not None and recovered.volume_g is None and not recovered.has_scale
