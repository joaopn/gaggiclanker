"""`GET /api/sets/{id}/trends` — the shape the Set chart draws.

The chunk's fourth acceptance criterion is "a version trend chart renders for a
Set with three versions and ten shots", so that is what this builds: three
versions, ten shots spread across them, judgements on some of them and not
others, and assertions that the absences come back as nulls rather than zeros.
"""

from __future__ import annotations

from gaggiclanker.db.repos.judgements import JudgementWrite
from gaggiclanker.db.repos.sets import SetVersionPatch, SetVersionWrite, SetWrite
from tests.sets.conftest import Fixtures, make_shot


async def _three_versions_and_ten_shots(wired: Fixtures) -> int:
    row = await wired.sets.create(
        SetWrite(name="Guji on the Niche", bean_id=wired.bean_id, machine_id=wired.machine_id),
        SetVersionWrite(dose_g=18.0, target_yield_g=36.0, grind_setting="22"),
    )
    versions = [(await wired.sets.current_version(row.id))]
    for grind, intent in (("21", "finer"), ("20", "finer still")):
        versions.append(
            await wired.sets.add_version(
                row.id, SetVersionPatch(grind_setting=grind, intent=intent)
            )
        )

    # Ten shots: four on v1, three on v2, three on v3. Scores climb, which is
    # what a dial-in that worked looks like.
    plan = [(0, 4, 6.0), (1, 3, 7.5), (2, 3, 9.0)]
    shot_number = 0
    for index, count, score in plan:
        version = versions[index]
        assert version is not None
        for _ in range(count):
            shot_id = await make_shot(
                wired.db,
                wired.machine_id,
                f"0006{shot_number:02d}",
                execution_score=score,
                duration_ms=26_000 + shot_number * 500,
                started_at=f"2026-04-01T0{shot_number % 9}:00:00.000Z",
            )
            await wired.sets.assign_shot(shot_id, version.id)
            # Only some shots get a verdict: most shots in a real archive do not
            # have one, and the averages must not treat that as a zero.
            if shot_number % 2 == 0:
                await wired.judgements.upsert(
                    shot_id,
                    JudgementWrite(rating=3 + index, dose_in_g=18.0, dose_out_g=36.0),
                )
            shot_number += 1
    return row.id


class TestTrends:
    async def test_the_shape_the_chart_draws(self, wired: Fixtures) -> None:
        set_id = await _three_versions_and_ten_shots(wired)
        trends = await wired.sets.trends(set_id)

        assert trends.set_id == set_id
        assert len(trends.shots) == 10
        # Oldest version first: a trend reads left to right.
        assert [version.version_no for version in trends.versions] == [1, 2, 3]
        assert [version.shots for version in trends.versions] == [4, 3, 3]
        assert [version.avg_execution_score for version in trends.versions] == [6.0, 7.5, 9.0]
        assert [version.intent for version in trends.versions] == ["", "finer", "finer still"]

        first = trends.shots[0]
        assert first.version_no == 1
        assert first.duration_s == 26.0
        assert first.ratio == 2.0
        assert first.rating == 3

    async def test_a_shot_with_no_judgement_has_no_ratio_and_no_rating(
        self, wired: Fixtures
    ) -> None:
        set_id = await _three_versions_and_ten_shots(wired)
        trends = await wired.sets.trends(set_id)
        unjudged = [point for point in trends.shots if point.rating is None]
        assert unjudged, "the fixture is supposed to leave some shots unjudged"
        # The dose only ever exists because a person typed it, so no judgement
        # means no ratio — not a ratio invented from the nominal basket.
        assert all(point.ratio is None for point in unjudged)

    async def test_a_version_with_no_shots_averages_to_nothing_rather_than_zero(
        self, wired: Fixtures
    ) -> None:
        """A zero would draw a bar at the bottom saying the recipe was terrible."""
        row = await wired.sets.create(
            SetWrite(name="Fresh start", bean_id=wired.bean_id, machine_id=wired.machine_id),
            SetVersionWrite(),
        )
        trends = await wired.sets.trends(row.id)
        assert trends.shots == []
        assert len(trends.versions) == 1
        summary = trends.versions[0]
        assert summary.shots == 0
        assert summary.avg_execution_score is None
        assert summary.avg_rating is None
        assert summary.avg_ratio is None

    async def test_the_serialised_shape_is_the_one_the_chart_reads(self, wired: Fixtures) -> None:
        """The field names, which the front end's generated types mirror.

        The route itself is exercised in `test_api.py::TestTrendsRoute`; this is
        about the payload's keys, which are what a chart component destructures.
        """
        set_id = await _three_versions_and_ten_shots(wired)
        payload = (await wired.sets.trends(set_id)).model_dump(mode="json")
        assert set(payload) == {"set_id", "versions", "shots"}
        assert set(payload["shots"][0]) == {
            "shot_id",
            "device_id",
            "set_version_id",
            "version_no",
            "started_at",
            "execution_score",
            "duration_s",
            "ratio",
            "rating",
        }
