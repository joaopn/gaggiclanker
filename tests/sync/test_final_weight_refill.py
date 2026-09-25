"""Shots archived with no final weight get one at boot when their bytes have it.

A shot whose scale dropped to zero as it ended was stored with no final weight
before the rule learnt to look past the drop. The bytes are the source of
truth, so the next boot reads them again and fills the column; a shot the rule
finds nothing in, or one without a scale, is left alone.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.slog import encode_slog, parse_slog
from gaggiclanker.settings import EnvSettings
from gaggiclanker.sync.derive import derive_shot, refill_final_weights
from tests.conftest import running_app
from tests.domain.helpers import make_slog

SCALE = 0x04  # the system-info bit for "a scale is connected"


def _raw(weights: list[float], *, si: int = SCALE) -> bytes:
    samples: list[dict[str, Any]] = [
        {"t": i * 250, "v": w, "si": si} for i, w in enumerate(weights)
    ]
    return encode_slog(make_slog(samples, phases=[(0, 0, "brew")]))


DROPOUT = _raw([*[round(31.8 * i / 119, 1) for i in range(120)], 0.0, 0.0, 0.0])
LONG_ZEROS = _raw([*[round(31.8 * i / 119, 1) for i in range(120)], *[0.0] * 40])
NO_SCALE = _raw([0.0] * 120, si=0)


async def _store_as_before(db: Database, device_id: str, raw: bytes) -> int:
    """Insert a shot the way the old rule stored it: no final weight."""
    derived = derive_shot(parse_slog(raw), raw, device_id=device_id)
    derived.shot.final_weight_g = None
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


async def _final_weight(db: Database, shot_id: int) -> float | None:
    stored = await ShotsRepository(db).get(shot_id)
    assert stored is not None
    return stored.final_weight_g


async def test_boot_fills_the_weight_before_the_drop(env: EnvSettings) -> None:
    db = Database(env.database_path)
    await db.connect()
    try:
        await run_migrations(db)
        dropout = await _store_as_before(db, "000001", DROPOUT)
        long_zeros = await _store_as_before(db, "000002", LONG_ZEROS)
        no_scale = await _store_as_before(db, "000003", NO_SCALE)
    finally:
        await db.close()

    async with running_app(env) as (app, _client):
        db = app.state.db
        assert await _final_weight(db, dropout) == pytest.approx(31.8)
        assert await _final_weight(db, long_zeros) is None
        assert await _final_weight(db, no_scale) is None


async def test_the_refill_touches_nothing_on_a_second_run(env: EnvSettings) -> None:
    db = Database(env.database_path)
    await db.connect()
    try:
        await run_migrations(db)
        shots = ShotsRepository(db)
        dropout = await _store_as_before(db, "000001", DROPOUT)
        await _store_as_before(db, "000002", LONG_ZEROS)
        weighed = derive_shot(parse_slog(DROPOUT), DROPOUT, device_id="000004")
        weighed.shot.final_weight_g = 30.0  # a weight already stored is never re-read
        kept = await shots.insert(weighed.shot, weighed.samples)

        assert await refill_final_weights(shots) == 1
        assert await refill_final_weights(shots) == 0
        assert await _final_weight(db, dropout) == pytest.approx(31.8)
        assert await _final_weight(db, kept) == pytest.approx(30.0)
    finally:
        await db.close()
