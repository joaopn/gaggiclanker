"""A phase that ended before the machine logged a sample of it, where a person or a model reads it.

The real shot: the profile's Fill (exit on 2.8 bar) was over before the first sample, so the log
opens in the Ramp. The phase list the chat and the shot page read carries a row for the Fill, in
the profile's order, with its reason, its time, no samples and nothing measured. The stored list
(everything that divides by a sample count, draws a span or averages) never holds it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.db.schema import create_schema
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.shotinfo import ITEMS, ShotFacts, Tier, default_tiers, load_shots, render_shot
from gaggiclanker.shotinfo.fields import shot_fields_of
from gaggiclanker.sync.derive import derive_shot
from tests.domain.helpers import fill_ended_shot
from tests.lever_shot import LEVER_PROFILE, lever_shot


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "unsampled.db")
    await database.connect()
    await create_schema(database)
    try:
        yield database
    finally:
        await database.close()


async def _facts(db: Database, *, lever: bool = False) -> ShotFacts:
    if lever:
        slog, profile = lever_shot(), LEVER_PROFILE
    else:
        slog, _, profile = fill_ended_shot()
    derived = derive_shot(slog, slog_to_raw(slog), device_id="000225", profile=profile)
    shot_id = await ShotsRepository(db).insert(derived.shot, derived.samples)
    [facts] = await load_shots(db, [shot_id])
    return facts


async def test_the_chat_reads_a_row_for_the_fill_in_the_profile_s_order(db: Database) -> None:
    facts = await _facts(db)

    lines = render_shot(facts, "base", default_tiers(), curve_points=60).splitlines()
    start = lines.index("[Phases]")

    assert lines[start + 1] == (
        "phase 0 · Fill: duration 0.0 s; ended on its pressure target before the first sample"
    )
    assert lines[start + 2].startswith("phase 1 · Ramp: duration 4.2 s; ended by Duration")
    assert lines[start + 3].startswith("phase 2 · Decline:")
    # Nothing measured is written for it, and no zero stands in for a number.
    assert "bar" not in lines[start + 1] and " g" not in lines[start + 1]


async def test_every_tier_gives_it_a_row_with_the_numbers_it_has_and_no_others(
    db: Database,
) -> None:
    facts = await _facts(db)
    everything: dict[str, Tier] = dict.fromkeys(ITEMS, "base")

    text = render_shot(facts, "base", everything, curve_points=20)
    [row] = [line for line in text.splitlines() if line.startswith("phase 0 · Fill")]

    assert row == (
        "phase 0 · Fill: start 0.0 s; duration 0.0 s; "
        "ended on its pressure target before the first sample; samples 0"
    )


async def test_the_stored_phase_list_does_not_hold_it(db: Database) -> None:
    facts = await _facts(db)

    assert [p["phase_number"] for p in facts.phases] == [1, 2]
    assert [p["phase_number"] for p in facts.listed_phases] == [0, 1, 2]
    assert all(p["sample_count"] > 0 for p in facts.phases)


async def test_the_page_is_served_the_row_marked_as_having_no_samples(db: Database) -> None:
    served = shot_fields_of(await _facts(db))

    assert [(p.number, p.name, p.sampled) for p in served.phases] == [
        (0, "Fill", False),
        (1, "Ramp", True),
        (2, "Decline", True),
    ]
    fill = served.phases[0]
    assert (fill.start_s, fill.duration_s) == (0.0, 0.0)
    by_key = {field.key: field for field in fill.fields}
    assert by_key["phase_ended_by"].text == "ended on its pressure target before the first sample"
    assert by_key["phase_ended_by"].value == 2
    # Nothing it did not measure is served: no pressure, no flow, no cup.
    assert set(by_key) <= {
        "phase_name",
        "phase_start",
        "phase_duration",
        "phase_ended_by",
        "phase_samples",
    }


async def test_a_shot_with_no_such_phase_reads_exactly_the_stored_list(db: Database) -> None:
    facts = await _facts(db, lever=True)

    assert facts.listed_phases == facts.phases
    assert all(p.sampled for p in shot_fields_of(facts).phases)


async def test_a_phase_whose_reason_the_log_does_not_hold_says_so(db: Database) -> None:
    facts = await _facts(db)
    [fill] = [p for p in facts.listed_phases if p["phase_number"] == 0]
    unknown = {**fill, "metrics": {"ended_by": 0}}

    assert ITEMS["phase_ended_by"].phase is not None
    assert ITEMS["phase_ended_by"].phase(facts, unknown) == (
        "ended before the first sample; the machine did not log why"
    )
