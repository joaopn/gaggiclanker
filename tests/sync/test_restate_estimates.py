"""Version 8 shots stored with the machine's estimate as their weights are put right at start.

A build that read version 8 without telling a scale's weight from the estimate stored the
estimate as the shot's weights, its yield and the scale flag, and kept the index volume the
machine copied from its header. That build is reproduced by reading version 8 the old way (the
rule off) and storing the result. At start such a shot is derived again from its bytes (same
row, judgement and notes), once: what it holds afterwards is what a fresh derivation stores, so
a second start finds nothing to do.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.typing import EventDict, WrappedLogger

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.db.schema import create_schema
from gaggiclanker.domain import scale_weights
from gaggiclanker.domain.models import (
    SI_ACTIVE_SCALE_CONNECTED,
    SI_BLUETOOTH_SCALE_CONNECTED,
    SI_EXTENDED_RECORDING,
    IndexEntry,
)
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.settings import EnvSettings
from gaggiclanker.sync import derive
from gaggiclanker.sync.derive import DERIVATION_VERSION, derive_shot, restate_stored_estimates
from tests.conftest import running_app
from tests.domain.helpers import SCALE_LOST_AT, V7_TWIN, V8_SHOT, edited_v8
from tests.recipes.conftest import make_profile_version

NO_SCALE = edited_v8(lambda _i, sample: {"si": (sample.si or 0) & ~SI_ACTIVE_SCALE_CONNECTED})
SCALE_LOST = edited_v8(
    lambda index, sample: (
        {"si": (sample.si or 0) & ~SI_ACTIVE_SCALE_CONNECTED} if index >= SCALE_LOST_AT else {}
    )
)
HEADER_G = 31.9

#: A hardware scale: the active-scale bit on every sample and the Bluetooth bit on none.
HARDWARE = edited_v8(
    lambda _i, sample: {
        "si": ((sample.si or 0) & ~SI_BLUETOOTH_SCALE_CONNECTED) | SI_ACTIVE_SCALE_CONNECTED
    }
)
#: A scale that answers only in the extended recording after the stop.
TAIL_ONLY = edited_v8(
    lambda _i, sample: {
        "si": (sample.si or 0) | SI_ACTIVE_SCALE_CONNECTED
        if (sample.si or 0) & SI_EXTENDED_RECORDING
        else (sample.si or 0) & ~SI_ACTIVE_SCALE_CONNECTED
    }
)
#: The only sample with the active-scale bit is the last sample of the brew, and the row filter
#: drops it: the next sample has the same time and the last of two at one moment is the one stored.
_REAL = parse_slog(V8_SHOT.read_bytes()).samples
#: The last sample of the brew: the first sample of the extended recording shares its moment.
DROPPED_AT = next(i for i, s in enumerate(_REAL) if (s.si or 0) & SI_EXTENDED_RECORDING) - 1
BIT_ON_A_DROPPED_SAMPLE = edited_v8(
    lambda index, sample: (
        {"si": (sample.si or 0) | SI_ACTIVE_SCALE_CONNECTED}
        if index == DROPPED_AT
        else {"si": (sample.si or 0) & ~SI_ACTIVE_SCALE_CONNECTED, "t": _REAL[DROPPED_AT].t}
        if index == DROPPED_AT + 1
        else {"si": (sample.si or 0) & ~SI_ACTIVE_SCALE_CONNECTED}
    )
)


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


async def _store_as_the_earlier_build_did(db: Database, device_id: str, raw: bytes) -> int:
    """The shot as a build that read version 8 like version 7 stored it, index volume included."""
    slog = parse_slog(raw)
    entry = IndexEntry(
        id=int(device_id),
        timestamp=slog.timestamp,
        duration_ms=slog.duration_ms,
        volume_g=HEADER_G,
        rating=4,
        flags=0x01,
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scale_weights, "ACTIVE_SCALE_VERSION", 99)
        derived = derive_shot(slog, raw, device_id=device_id, entry=entry)
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


async def _row(db: Database, shot_id: int) -> dict[str, Any]:
    row = await db.fetch_one("SELECT * FROM shots WHERE id = ?", (shot_id,))
    assert row is not None
    return dict(row)


async def _weights(db: Database, shot_id: int) -> list[tuple[float | None, float | None]]:
    rows = await db.fetch_all(
        "SELECT v, vf FROM shot_samples WHERE shot_id = ? ORDER BY t_ms", (shot_id,)
    )
    return [(row["v"], row["vf"]) for row in rows]


async def test_the_earlier_build_stored_the_estimate(db: Database) -> None:
    """The first half: what the pass finds is really there to be put right."""
    shot_id = await _store_as_the_earlier_build_did(db, "000301", NO_SCALE)
    row = await _row(db, shot_id)
    assert row["final_weight_g"] == pytest.approx(HEADER_G)
    assert row["scale_connected"] == 1  # from the Bluetooth bit, which the file still carries
    assert row["index_volume_g"] == pytest.approx(HEADER_G)
    assert max(v or 0 for v, _ in await _weights(db, shot_id)) > 30
    assert await ShotsRepository(db).stored_estimates() == [shot_id]


async def test_a_stored_estimate_is_put_right_and_the_row_is_kept(db: Database) -> None:
    shots = ShotsRepository(db)
    shot_id = await _store_as_the_earlier_build_did(db, "000301", NO_SCALE)
    lost = await _store_as_the_earlier_build_did(db, "000302", SCALE_LOST)
    await JudgementsRepository(db).upsert(shot_id, JudgementWrite(rating=4, notes="mine"))
    version_id = await make_profile_version(db, "The profile the shot was brewed on")
    await shots.link_profile_version(shot_id, version_id)

    assert await restate_stored_estimates(shots) == 2

    row = await _row(db, shot_id)
    assert row["id"] == shot_id and row["device_id"] == "000301"
    assert row["final_weight_g"] is None
    assert row["scale_connected"] == 0
    assert row["index_volume_g"] is None
    assert row["index_rating"] == 4  # the machine's other figures stay
    assert row["derivation_version"] == DERIVATION_VERSION
    assert row["profile_version_id"] == version_id  # its link is kept
    assert all(weight == (None, None) for weight in await _weights(db, shot_id))
    detail = await shots.get(shot_id)
    assert detail is not None and not detail.has_scale and detail.volume_g is None
    judgement = await JudgementsRepository(db).get(shot_id)
    assert judgement is not None and judgement.notes == "mine"

    # A scale lost mid-shot keeps its weights up to the loss, and has no final weight.
    kept = await _weights(db, lost)
    assert max(v or 0 for v, _ in kept[:SCALE_LOST_AT]) > 5
    assert all(weight == (None, None) for weight in kept[SCALE_LOST_AT:])
    row = await _row(db, lost)
    assert row["final_weight_g"] is None and row["scale_connected"] == 1
    assert row["index_volume_g"] is None


async def test_what_is_already_right_is_not_touched_and_a_second_run_changes_nothing(
    db: Database,
) -> None:
    shots = ShotsRepository(db)
    with_scale = await _store_as_the_earlier_build_did(db, "000303", V8_SHOT.read_bytes())
    older = await _store_as_the_earlier_build_did(db, "000304", V7_TWIN.read_bytes())
    bad = await _store_as_the_earlier_build_did(db, "000301", NO_SCALE)
    untouched = {shot_id: await _row(db, shot_id) for shot_id in (with_scale, older)}

    assert await restate_stored_estimates(shots) == 1
    after_first = await _row(db, bad)
    assert await restate_stored_estimates(shots) == 0
    assert await _row(db, bad) == after_first
    for shot_id, was in untouched.items():
        assert await _row(db, shot_id) == was
    assert await shots.stored_estimates() == []


async def test_the_start_puts_a_stored_estimate_right_once(
    env: EnvSettings, logs: list[dict[str, Any]]
) -> None:
    db = Database(env.database_path)
    await db.connect()
    try:
        await create_schema(db)
        shot_id = await _store_as_the_earlier_build_did(db, "000301", NO_SCALE)
    finally:
        await db.close()

    async with running_app(env) as (app, _client):
        row = await _row(app.state.db, shot_id)
        assert row["final_weight_g"] is None and row["index_volume_g"] is None
        assert row["scale_connected"] == 0
        corrected = row
    async with running_app(env) as (app, _client):
        assert await _row(app.state.db, shot_id) == corrected

    counts = [e["count"] for e in logs if e.get("event") == "shots_estimates_restated"]
    assert counts == [1, 0]


async def test_a_hardware_scale_shot_stored_without_its_flag_is_found_and_put_right(
    db: Database,
) -> None:
    """The earlier build took the flag from the Bluetooth bit, which a hardware scale lacks: the
    weights were the scale's, only `scale_connected` was wrong, and nothing else says so."""
    shots = ShotsRepository(db)
    shot_id = await _store_as_the_earlier_build_did(db, "000305", HARDWARE)
    row = await _row(db, shot_id)
    assert row["scale_connected"] == 0
    assert max(v or 0 for v, _ in await _weights(db, shot_id)) > 30  # real weights, all kept
    assert await shots.stored_estimates() == [shot_id]

    assert await restate_stored_estimates(shots) == 1
    row = await _row(db, shot_id)
    assert row["scale_connected"] == 1 and row["final_weight_g"] == pytest.approx(HEADER_G)
    assert max(v or 0 for v, _ in await _weights(db, shot_id)) > 30
    assert await shots.stored_estimates() == []
    assert await restate_stored_estimates(shots) == 0


async def test_a_scale_that_answered_only_after_the_stop_is_put_right_too(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build that judged the bit over every sample stored the brew's estimate as a final
    weight with the scale flag set, and the tail's weights."""
    shots = ShotsRepository(db)
    with monkeypatch.context() as earlier:
        earlier.setattr(scale_weights, "_brew_samples", lambda samples: list(samples))
        shot_id = await _store_as_the_earlier_build_did(db, "000306", TAIL_ONLY)
        assert (await _row(db, shot_id))["scale_connected"] == 1
    assert await shots.stored_estimates() == [shot_id]

    assert await restate_stored_estimates(shots) == 1
    row = await _row(db, shot_id)
    assert row["scale_connected"] == 0 and row["final_weight_g"] is None
    assert all(weight == (None, None) for weight in await _weights(db, shot_id))
    assert await shots.stored_estimates() == []


async def test_the_pass_converges_on_a_shot_whose_only_bit_is_on_a_sample_the_rows_drop(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag is computed from the rows that are stored, so the pass finds what it left."""
    slog = parse_slog(BIT_ON_A_DROPPED_SAMPLE)
    assert slog.samples[DROPPED_AT].t == slog.samples[DROPPED_AT + 1].t
    assert (slog.samples[DROPPED_AT].si or 0) & SI_ACTIVE_SCALE_CONNECTED
    shots = ShotsRepository(db)

    derived = derive_shot(slog, BIT_ON_A_DROPPED_SAMPLE, device_id="000307")
    assert len(derived.samples) == len(slog.samples) - 1  # the dropped sample
    assert not any((row.si or 0) & SI_ACTIVE_SCALE_CONNECTED for row in derived.samples)
    assert not derived.shot.scale_connected  # what the stored rows say
    # The first half: judged over every parsed sample the bit is there, and the header's weight
    # (the estimator's peak over the brew) would be this shot's final weight.
    with monkeypatch.context() as every_sample:
        every_sample.setattr(
            scale_weights, "kept_samples", lambda samples: [s for s in samples if s.t is not None]
        )
        assert slog.volume_g == pytest.approx(HEADER_G)
    # The header, the flag and the weights agree: no sample the rows keep has the bit, so no scale
    # answered, and the header's weight (the estimator's peak) is no final weight.
    assert derived.shot.final_weight_g is None and slog.volume_g is None
    assert all(row.v is None and row.vf is None for row in derived.samples)

    # The first half: a flag worked out from the parsed samples says a scale answered, the rows
    # say it did not, and the shot is never right.
    derived.shot.scale_connected = True
    shot_id = await shots.insert(derived.shot, derived.samples)
    assert await shots.stored_estimates() == [shot_id]

    assert await restate_stored_estimates(shots) == 1
    assert (await _row(db, shot_id))["scale_connected"] == 0
    assert await shots.stored_estimates() == []
    assert await restate_stored_estimates(shots) == 0


async def test_a_weight_in_a_shot_no_scale_answered_during_the_brew_is_found(
    db: Database,
) -> None:
    """The second rule of the finder, on its own: the flag is right (no scale) and no brew sample
    is stale, but a weight sits in the after-stop tail where the shot has none."""
    shots = ShotsRepository(db)
    derived = derive_shot(parse_slog(TAIL_ONLY), TAIL_ONLY, device_id="000308")
    shot_id = await shots.insert(derived.shot, derived.samples)
    assert not derived.shot.scale_connected and await shots.stored_estimates() == []

    await db.execute(
        "UPDATE shot_samples SET v = 31.9 WHERE shot_id = ? "
        "AND t_ms = (SELECT MAX(t_ms) FROM shot_samples WHERE shot_id = ?)",
        (shot_id, shot_id),
    )
    assert await shots.stored_estimates() == [shot_id]
    assert await restate_stored_estimates(shots) == 1
    assert all(weight == (None, None) for weight in await _weights(db, shot_id))
