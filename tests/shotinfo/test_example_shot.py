"""Which shot Settings → Shot information takes its examples from."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository


@pytest.fixture
async def shots(tmp_path: Path) -> AsyncIterator[ShotsRepository]:
    db = Database(tmp_path / "example.db")
    await db.connect()
    await run_migrations(db)
    try:
        yield ShotsRepository(db)
    finally:
        await db.close()


async def _shot(
    shots: ShotsRepository, device_id: str, started_at: str | None, *, quarantined: bool = False
) -> int:
    return await shots.insert(
        ShotInsert(
            device_id=device_id,
            raw_slog=b"not-a-slog",
            started_at=started_at,
            duration_ms=28_000,
            quarantined=quarantined,
        )
    )


async def _judge(shots: ShotsRepository, shot_id: int, **verdict: object) -> None:
    await JudgementsRepository(shots.db).upsert(
        shot_id, JudgementWrite.model_validate(verdict or {"rating": 3})
    )


async def test_an_empty_archive_has_no_example(shots: ShotsRepository) -> None:
    assert await shots.example_shot() is None


async def test_the_newest_shot_when_none_is_judged(shots: ShotsRepository) -> None:
    await _shot(shots, "000001", "2026-04-01T08:00:00.000Z")
    newest = await _shot(shots, "000002", "2026-04-02T08:00:00.000Z")
    await _shot(shots, "000003", "2026-03-30T08:00:00.000Z")

    example = await shots.example_shot()

    assert example is not None
    assert (example.id, example.started_at, example.judged) == (
        newest,
        "2026-04-02T08:00:00.000Z",
        False,
    )


async def test_a_judged_shot_beats_a_newer_unjudged_one(shots: ShotsRepository) -> None:
    older_judged = await _shot(shots, "000001", "2026-04-01T08:00:00.000Z")
    newer_judged = await _shot(shots, "000002", "2026-04-02T08:00:00.000Z")
    await _shot(shots, "000003", "2026-04-03T08:00:00.000Z")
    await _judge(shots, older_judged)
    await _judge(shots, newer_judged)

    example = await shots.example_shot()

    assert example is not None
    assert (example.id, example.judged) == (newer_judged, True)


async def test_a_quarantined_shot_is_never_the_example(shots: ShotsRepository) -> None:
    plain = await _shot(shots, "000001", "2026-04-01T08:00:00.000Z")
    broken = await _shot(shots, "000002", "2026-04-02T08:00:00.000Z", quarantined=True)
    await _judge(shots, broken)

    example = await shots.example_shot()

    assert example is not None
    assert (example.id, example.judged) == (plain, False)


async def test_an_archive_of_only_quarantined_shots_has_no_example(
    shots: ShotsRepository,
) -> None:
    await _shot(shots, "000001", "2026-04-01T08:00:00.000Z", quarantined=True)

    assert await shots.example_shot() is None


async def test_newest_is_the_shot_list_s_order(shots: ShotsRepository) -> None:
    """No start time sorts oldest; the same start time goes to the higher id."""
    undated = await _shot(shots, "000001", None)
    first = await _shot(shots, "000002", "2026-04-01T08:00:00.000Z")
    second = await _shot(shots, "000003", "2026-04-01T08:00:00.000Z")

    example = await shots.example_shot()

    assert example is not None
    assert example.id == second
    assert undated < first < second


@pytest.mark.parametrize("verdict", [{"rating": 4}, {"decision": "discard"}])
async def test_a_rating_or_a_decision_beats_a_newer_judgement_without_either(
    shots: ShotsRepository, verdict: dict[str, object]
) -> None:
    """The machine's notes card can seed a judgement holding only a dose."""
    judged = await _shot(shots, "000001", "2026-04-01T08:00:00.000Z")
    dose_only = await _shot(shots, "000002", "2026-04-02T08:00:00.000Z")
    await _shot(shots, "000003", "2026-04-03T08:00:00.000Z")
    await _judge(shots, judged, **verdict)
    await _judge(shots, dose_only, dose_out_g=36.5)

    example = await shots.example_shot()

    assert example is not None
    assert (example.id, example.judged) == (judged, True)


async def test_a_judgement_without_a_verdict_still_beats_a_newer_shot_with_none(
    shots: ShotsRepository,
) -> None:
    dose_only = await _shot(shots, "000001", "2026-04-01T08:00:00.000Z")
    await _shot(shots, "000002", "2026-04-02T08:00:00.000Z")
    await _judge(shots, dose_only, dose_out_g=36.5)

    example = await shots.example_shot()

    assert example is not None
    assert (example.id, example.judged) == (dose_only, False)
