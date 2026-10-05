"""`GET /api/sets/{id}/trends` — the shape the Set chart draws.

The chunk's fourth acceptance criterion is "a version trend chart renders for a
Set with three versions and ten shots", so that is what this builds: three
versions, ten shots spread across them, judgements on some of them and not
others, and assertions that the absences come back as nulls rather than zeros.
"""

from __future__ import annotations

import json

from gaggiclanker.db.repos.judgements import JudgementWrite
from gaggiclanker.db.repos.sets import SetVersionPatch, SetVersionWrite, SetWrite
from tests.sets.conftest import Fixtures, make_shot


async def _three_versions_and_ten_shots(wired: Fixtures) -> int:
    row = await wired.sets.create(
        SetWrite(name="Guji on the Niche", bean_id=wired.bean_id),
        SetVersionWrite(dose_g=18.0, target_yield_g=36.0, grind_setting="22"),
    )
    versions = [(await wired.sets.current_version(row.id))]
    for grind, intent in (("21", "finer"), ("20", "finer still")):
        versions.append(
            await wired.sets.add_version(
                row.id, SetVersionPatch(grind_setting=grind, intent=intent)
            )
        )

    # Ten shots: four on v1, three on v2, three on v3.
    plan = [(0, 4), (1, 3), (2, 3)]
    shot_number = 0
    for index, count in plan:
        version = versions[index]
        assert version is not None
        for _ in range(count):
            shot_id = await make_shot(
                wired.db,
                f"0006{shot_number:02d}",
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
        assert [version.version_label for version in trends.versions] == ["v1", "v1.1", "v1.2"]
        assert [version.shots for version in trends.versions] == [4, 3, 3]
        assert [version.intent for version in trends.versions] == ["", "finer", "finer still"]

        first = trends.shots[0]
        assert first.version_label == "v1"
        assert first.duration_s == 26.0
        assert first.ratio == 2.0
        assert first.rating == 3

    async def test_a_shot_with_no_judgement_takes_the_versions_dose_for_its_ratio(
        self, wired: Fixtures
    ) -> None:
        set_id = await _three_versions_and_ten_shots(wired)
        trends = await wired.sets.trends(set_id)
        unjudged = [point for point in trends.shots if point.rating is None]
        assert unjudged, "the fixture is supposed to leave some shots unjudged"
        # No dose typed: the version's 18 g and the scale's 36 g.
        assert all(point.ratio == 2.0 for point in unjudged)

    async def test_a_shot_with_no_dose_anywhere_has_no_ratio(self, wired: Fixtures) -> None:
        row = await wired.sets.create(
            SetWrite(name="No dose", bean_id=wired.bean_id), SetVersionWrite()
        )
        assert row.current_version_id is not None
        shot_id = await make_shot(wired.db, "000777")
        await wired.sets.assign_shot(shot_id, row.current_version_id)
        # Not a ratio invented from the nominal basket: nobody said what went in.
        (point,) = (await wired.sets.trends(row.id)).shots
        assert point.ratio is None

    async def test_a_version_with_no_shots_averages_to_nothing_rather_than_zero(
        self, wired: Fixtures
    ) -> None:
        """A zero would draw a bar at the bottom saying the recipe was terrible."""
        row = await wired.sets.create(
            SetWrite(name="Fresh start", bean_id=wired.bean_id),
            SetVersionWrite(),
        )
        trends = await wired.sets.trends(row.id)
        assert trends.shots == []
        assert len(trends.versions) == 1
        summary = trends.versions[0]
        assert summary.shots == 0
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
            "version_label",
            "started_at",
            "duration_s",
            "ratio",
            "rating",
        }


async def test_points_are_in_the_order_the_shots_were_pulled_not_the_versions_made(
    wired: Fixtures,
) -> None:
    """After going back, new shots of an older version come after the newer version's."""
    row = await wired.sets.create(
        SetWrite(name="Order", bean_id=wired.bean_id), SetVersionWrite(dose_g=18)
    )
    first = await wired.sets.current_version(row.id)
    assert first is not None
    minor = await wired.sets.add_version(row.id, SetVersionPatch(grind_setting="21"))
    major = await wired.sets.add_version(row.id, SetVersionPatch(grind_setting="20"), major=True)
    assert minor is not None and major is not None
    plan = [
        (minor.id, "2026-04-01T08:00:00.000Z"),
        (major.id, "2026-04-02T08:00:00.000Z"),
        (minor.id, "2026-04-03T08:00:00.000Z"),
    ]
    for index, (version_id, started) in enumerate(plan):
        shot = await make_shot(wired.db, f"00000{index}", started_at=started)
        assert await wired.sets.assign_shot(shot, version_id)

    trends = await wired.sets.trends(row.id)

    assert [point.version_label for point in trends.shots] == ["v1.1", "v2", "v1.1"]
    assert [point.started_at for point in trends.shots] == [started for _, started in plan]
    # The per-version bars stay in the order the versions were made.
    assert [v.version_label for v in trends.versions] == ["v1", "v1.1", "v2"]


def _diagnostics(first_drip_s: float, *, has_pressure: bool = True) -> str:
    return json.dumps(
        {
            "has_pressure": has_pressure,
            "summary": {"flow": {"time_to_first_drip_s": first_drip_s}},
        }
    )


async def _counted_set(wired: Fixtures) -> tuple[int, int]:
    """One version with five shots, three of which count.

    Counted: 30 s / 36 g / rating 4 / drip 6 s; 34 s / 38 g / rating 2 / drip 8 s;
    and a third nobody rated, with no scale reading.
    Not counted: a Discard and an incomplete shot, both far from the others.
    """
    row = await wired.sets.create(
        SetWrite(name="Counted", bean_id=wired.bean_id),
        SetVersionWrite(dose_g=18.0, target_yield_g=36.0),
    )
    assert row.current_version_id is not None
    version = row.current_version_id
    plan = [
        ("000801", 30_000, 36.0, 6.0, 4, None),
        ("000802", 34_000, 38.0, 8.0, 2, None),
        ("000803", 32_000, None, None, None, None),
        ("000804", 90_000, 80.0, 30.0, 1, "discard"),
        ("000805", 5_000, 4.0, 1.0, 5, None),
    ]
    ids = []
    for index, (device, duration, weight, drip, rating, decision) in enumerate(plan):
        shot = await make_shot(
            wired.db,
            device,
            duration_ms=duration,
            final_weight_g=weight,
            started_at=f"2026-04-01T08:0{index}:00.000Z",
        )
        if drip is not None:
            await wired.db.execute(
                "UPDATE shots SET diagnostics_json = ? WHERE id = ?", (_diagnostics(drip), shot)
            )
        assert await wired.sets.assign_shot(shot, version)
        if rating is not None or decision is not None:
            await wired.judgements.upsert(
                shot, JudgementWrite(rating=rating, decision="discard" if decision else None)
            )
        ids.append(shot)
    await wired.db.execute("UPDATE shots SET incomplete = 1 WHERE id = ?", (ids[4],))
    return row.id, version


class TestTheAveragesAreOverCountedShots:
    async def test_a_discarded_and_an_incomplete_shot_pull_no_mean(self, wired: Fixtures) -> None:
        set_id, _ = await _counted_set(wired)
        (version,) = (await wired.sets.trends(set_id)).versions

        assert version.shots == 5
        assert version.counted_shots == 3
        assert version.avg_duration_s == 32.0
        assert version.avg_yield_g == 37.0
        assert version.avg_rating == 3.0
        assert version.avg_first_drip_s == 7.0
        # 36/18 and 38/18 over the two shots with a yield; the third has no scale reading.
        assert version.avg_ratio == round((2.0 + 2.11) / 2, 3)

    async def test_each_mean_says_how_many_shots_it_is_over(self, wired: Fixtures) -> None:
        set_id, _ = await _counted_set(wired)
        (version,) = (await wired.sets.trends(set_id)).versions

        counts = version.averaged_over
        assert (counts.duration_s, counts.yield_g, counts.ratio) == (3, 2, 2)
        assert (counts.rating, counts.first_drip_s) == (2, 2)

    async def test_a_version_with_only_uncounted_shots_has_no_mean(self, wired: Fixtures) -> None:
        row = await wired.sets.create(
            SetWrite(name="Only a discard", bean_id=wired.bean_id), SetVersionWrite(dose_g=18.0)
        )
        assert row.current_version_id is not None
        shot = await make_shot(wired.db, "000810")
        await wired.sets.assign_shot(shot, row.current_version_id)
        await wired.judgements.upsert(shot, JudgementWrite(rating=1, decision="discard"))
        (version,) = (await wired.sets.trends(row.id)).versions

        assert (version.shots, version.counted_shots) == (1, 0)
        assert version.avg_duration_s is None
        assert version.avg_rating is None
        assert version.averaged_over.duration_s == 0

    async def test_the_first_drip_of_a_machine_without_a_pressure_sensor_is_absent(
        self, wired: Fixtures
    ) -> None:
        row = await wired.sets.create(
            SetWrite(name="Standard board", bean_id=wired.bean_id), SetVersionWrite(dose_g=18.0)
        )
        assert row.current_version_id is not None
        shot = await make_shot(wired.db, "000811")
        await wired.db.execute(
            "UPDATE shots SET diagnostics_json = ? WHERE id = ?",
            (_diagnostics(5.0, has_pressure=False), shot),
        )
        await wired.sets.assign_shot(shot, row.current_version_id)
        (version,) = (await wired.sets.trends(row.id)).versions

        assert version.avg_first_drip_s is None
        assert version.averaged_over.first_drip_s == 0
        assert version.avg_duration_s == 28.0


async def test_the_ratio_s_dose_is_the_judgement_s_when_one_was_typed(wired: Fixtures) -> None:
    """18 g in the version, 20 g typed on one shot: that shot's ratio is yield over 20."""
    row = await wired.sets.create(
        SetWrite(name="Typed dose", bean_id=wired.bean_id), SetVersionWrite(dose_g=18.0)
    )
    assert row.current_version_id is not None
    for device, dose_in in (("000820", None), ("000821", 20.0)):
        shot = await make_shot(wired.db, device, final_weight_g=40.0)
        await wired.sets.assign_shot(shot, row.current_version_id)
        await wired.judgements.upsert(shot, JudgementWrite(rating=3, dose_in_g=dose_in))

    trends = await wired.sets.trends(row.id)
    version = trends.versions[0]

    assert sorted(point.ratio or 0 for point in trends.shots) == [2.0, 2.22]
    assert version.avg_ratio == 2.11


async def test_trends_can_be_given_the_counted_shots_it_would_read(wired: Fixtures) -> None:
    set_id, _ = await _counted_set(wired)

    given = await wired.sets.trends(set_id, counted=await wired.sets.counted_shots(set_id))

    assert given == await wired.sets.trends(set_id)
