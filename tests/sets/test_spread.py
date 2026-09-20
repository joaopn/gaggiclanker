"""Which shots count, where each measure is read from, and what the page gets.

The arithmetic is hand-checked in `tests/domain/test_spread.py`. What is under
test here is everything around it: the one query that decides which shots a
Set's spread rests on, the columns and JSON paths each measure is read out of,
and the two fields the Set page gains — the Set's spread and, on a version that
predicted something, the evidence for it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.sets import (
    RollbackWrite,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
)
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.domain.vocab import SPREAD_MEASURES
from tests.sets.conftest import Fixtures


def diagnostics(*, first_drip_s: Any = 6.5, max_bar: Any = 9.2, brew_flow: Any = 1.8) -> str:
    """A diagnostics document shaped the way the ingest path writes one.

    Only the three paths the spread reads are filled in. Written out here rather
    than run through the diagnostics engine on purpose: the point of the query
    is that it reads what is already stored, so the test has to state what
    "already stored" looks like — including the shapes a hand-edited row or an
    older document could hold.

    ``max_bar=None`` is the machine with no pressure sensor: the engine writes
    no pressure block at all, rather than a block with nothing in it.
    """
    pressure = None if max_bar is None else {"max_bar": max_bar}
    return json.dumps(
        {
            "summary": {
                "flow": {"time_to_first_drip_s": first_drip_s},
                "pressure": pressure,
            },
            "diagnostics": {"extraction": {"flow_avg_brew_ml_s": brew_flow}},
        }
    )


async def store_shot(
    shots: ShotsRepository,
    device_id: str,
    *,
    duration_ms: int = 28_000,
    final_weight_g: float | None = 36.0,
    index_volume_g: float | None = None,
    diagnostics_json: str | None = None,
    quarantined: bool = False,
    incomplete: bool = False,
) -> int:
    return await shots.insert(
        ShotInsert(
            device_id=device_id,
            raw_slog=b"not-a-slog",
            started_at="2026-04-01T08:00:00.000Z",
            duration_ms=duration_ms,
            final_weight_g=final_weight_g,
            index_volume_g=index_volume_g,
            diagnostics_json=diagnostics_json,
            quarantined=quarantined,
            incomplete=incomplete,
        )
    )


async def a_set(wired: Fixtures) -> tuple[int, int]:
    """A Set with one version, and both ids."""
    row = await wired.sets.create(
        SetWrite(name="Guji on the Niche", bean_id=wired.bean_id),
        SetVersionWrite(dose_g=18.0, target_yield_g=36.0, grind_setting="22"),
    )
    assert row.current_version_id is not None
    return row.id, row.current_version_id


class TestWhichShotsCount:
    async def test_discards_quarantines_and_incompletes_are_left_out(self, wired: Fixtures) -> None:
        """Three exclusions, and an unlabelled shot is not one of them."""
        set_id, version_id = await a_set(wired)
        judgements = JudgementsRepository(wired.db)
        plain = await store_shot(wired.shots, "000001")
        kept = await store_shot(wired.shots, "000002")
        discarded = await store_shot(wired.shots, "000003")
        quarantined = await store_shot(wired.shots, "000004", quarantined=True)
        incomplete = await store_shot(wired.shots, "000005", incomplete=True)
        for shot_id in (plain, kept, discarded, quarantined, incomplete):
            assert await wired.sets.assign_shot(shot_id, version_id)
        await judgements.upsert(kept, JudgementWrite(decision="keep"))
        await judgements.upsert(discarded, JudgementWrite(decision="discard"))

        counted = await wired.sets.counted_shots(set_id)

        assert [shot.shot_id for shot in counted] == [plain, kept]
        assert [shot.decision for shot in counted] == [None, "keep"]

    async def test_a_shot_filed_under_another_set_is_not_this_sets_evidence(
        self, wired: Fixtures
    ) -> None:
        set_id, version_id = await a_set(wired)
        other = await wired.sets.create(
            SetWrite(name="Kenya on the Niche", bean_id=wired.bean_id),
            SetVersionWrite(dose_g=18.0),
        )
        mine = await store_shot(wired.shots, "000001")
        theirs = await store_shot(wired.shots, "000002")
        unfiled = await store_shot(wired.shots, "000003")
        assert await wired.sets.assign_shot(mine, version_id)
        assert await wired.sets.assign_shot(theirs, other.current_version_id)
        assert unfiled  # filed under no Set at all

        counted = await wired.sets.counted_shots(set_id)

        assert [shot.shot_id for shot in counted] == [mine]


class TestWhereEachMeasureComesFrom:
    async def test_every_measure_is_read_where_the_archive_already_holds_it(
        self, wired: Fixtures
    ) -> None:
        """Two columns, three JSON paths out of the stored diagnostics, one judgement."""
        set_id, version_id = await a_set(wired)
        shot_id = await store_shot(
            wired.shots, "000001", diagnostics_json=diagnostics(), final_weight_g=36.4
        )
        assert await wired.sets.assign_shot(shot_id, version_id)
        await JudgementsRepository(wired.db).upsert(
            shot_id, JudgementWrite(rating=4, balance="sour", decision="improve")
        )

        counted = (await wired.sets.counted_shots(set_id))[0]

        assert counted.shot_time_s == 28.0
        assert counted.first_drip_s == 6.5
        assert counted.yield_g == 36.4
        assert counted.peak_pressure_bar == 9.2
        assert counted.brew_flow_ml_s == 1.8
        assert counted.rating == 4
        assert (counted.balance, counted.decision) == ("sour", "improve")

    async def test_a_measure_the_shot_does_not_hold_is_absent_rather_than_zero(
        self, wired: Fixtures
    ) -> None:
        """No diagnostics, no scale, no clock, no judgement: five honest nulls.

        A duration of zero is the header's "not recorded", not a shot that took
        no time, and a machine with no pressure sensor stores no pressure block
        at all.
        """
        set_id, version_id = await a_set(wired)
        bare = await store_shot(wired.shots, "000001", duration_ms=0, final_weight_g=None)
        blind = await store_shot(
            wired.shots, "000002", diagnostics_json=diagnostics(max_bar=None, first_drip_s=None)
        )
        for shot_id in (bare, blind):
            assert await wired.sets.assign_shot(shot_id, version_id)

        counted = {shot.shot_id: shot for shot in await wired.sets.counted_shots(set_id)}

        assert counted[bare].shot_time_s is None
        assert counted[bare].yield_g is None
        assert counted[bare].first_drip_s is None
        assert counted[bare].peak_pressure_bar is None
        assert counted[bare].rating is None
        assert counted[blind].peak_pressure_bar is None
        assert counted[blind].first_drip_s is None
        assert counted[blind].brew_flow_ml_s == 1.8

    async def test_a_zero_the_engine_writes_for_nothing_is_read_as_nothing(
        self, wired: Fixtures
    ) -> None:
        """An average over no samples is written 0.0, and it is not a reading.

        `_safe_mean([])` and `max([]) if ... else 0.0` are how the diagnostics
        engine says "there was nothing here". A shot that averaged exactly no
        flow and peaked at exactly no pressure would be a shot that never
        happened, so both are read as absent — the same rule the header's zero
        duration already gets.
        """
        set_id, version_id = await a_set(wired)
        empty = await store_shot(
            wired.shots, "000001", diagnostics_json=diagnostics(max_bar=0.0, brew_flow=0.0)
        )
        assert await wired.sets.assign_shot(empty, version_id)

        counted = (await wired.sets.counted_shots(set_id))[0]

        assert counted.peak_pressure_bar is None
        assert counted.brew_flow_ml_s is None

    async def test_a_first_drip_at_zero_seconds_is_a_reading(self, wired: Fixtures) -> None:
        """The one path where the engine can mean zero: the drip on sample one.

        It is already nullable — NULL when the flow never rose — so nothing is
        hidden behind a zero there, and reading 0.0 as absent would throw away
        the fastest shots.
        """
        set_id, version_id = await a_set(wired)
        fast = await store_shot(
            wired.shots, "000001", diagnostics_json=diagnostics(first_drip_s=0.0)
        )
        assert await wired.sets.assign_shot(fast, version_id)

        counted = (await wired.sets.counted_shots(set_id))[0]

        assert counted.first_drip_s == 0.0

    @pytest.mark.parametrize("odd", ["9.2", {"bar": 9.2}, True, [9.2], None], ids=str)
    async def test_a_path_holding_something_that_is_not_a_number_is_no_value(
        self, wired: Fixtures, odd: Any
    ) -> None:
        """A string, an object, a boolean, an array or a JSON null at a path.

        Guarded by `json_type` exactly as a profile's temperature is, because a
        Set page that answered 500 over one odd blob in one shot's diagnostics
        would be a bad trade for a number that is only ever an average.
        """
        set_id, version_id = await a_set(wired)
        shot_id = await store_shot(
            wired.shots,
            "000001",
            diagnostics_json=diagnostics(first_drip_s=odd, max_bar=odd, brew_flow=odd),
        )
        assert await wired.sets.assign_shot(shot_id, version_id)

        counted = (await wired.sets.counted_shots(set_id))[0]

        assert counted.first_drip_s is None
        assert counted.peak_pressure_bar is None
        assert counted.brew_flow_ml_s is None

    @pytest.mark.parametrize(
        ("dose_out_g", "final_weight_g", "index_volume_g", "expected"),
        [
            (35.0, 36.0, 37.0, 35.0),
            (None, 36.0, 37.0, 36.0),
            (None, None, 37.0, 37.0),
            (None, None, None, None),
        ],
        ids=["typed wins", "scale next", "index last", "no yield at all"],
    )
    async def test_the_yield_follows_the_archives_existing_precedence(
        self,
        wired: Fixtures,
        dose_out_g: float | None,
        final_weight_g: float | None,
        index_volume_g: float | None,
        expected: float | None,
    ) -> None:
        """The judgement's typed yield, then the scale, then the device index.

        The same order `starting/similar.py` scores a version's ratio by. A
        machine with no scale records nothing, so the only yield that exists is
        the one somebody wrote down; and where both exist the typed one is that
        person correcting the scale.
        """
        set_id, version_id = await a_set(wired)
        shot_id = await store_shot(
            wired.shots,
            "000001",
            final_weight_g=final_weight_g,
            index_volume_g=index_volume_g,
        )
        assert await wired.sets.assign_shot(shot_id, version_id)
        if dose_out_g is not None:
            await JudgementsRepository(wired.db).upsert(
                shot_id, JudgementWrite(dose_out_g=dose_out_g)
            )

        counted = (await wired.sets.counted_shots(set_id))[0]

        assert counted.yield_g == expected

    async def test_each_shot_carries_its_versions_recipe(self, wired: Fixtures) -> None:
        """Which is what makes a roll back's shots repeats of what it copied."""
        set_id, first = await a_set(wired)
        second = await wired.sets.add_version(
            set_id, SetVersionPatch(grind_setting="21", intent="one finer")
        )
        assert second is not None
        rolled_back = await wired.sets.rollback(
            set_id, RollbackWrite(to_version_id=first, intent="back to 22")
        )
        assert rolled_back.version is not None
        for index, version_id in enumerate((first, second.id, rolled_back.version.id)):
            shot_id = await store_shot(wired.shots, f"00000{index + 1}")
            assert await wired.sets.assign_shot(shot_id, version_id)

        counted = await wired.sets.counted_shots(set_id)

        recipes = [shot.recipe for shot in counted]
        assert recipes[0] == recipes[2], "a roll back copies the recipe, so its shots repeat it"
        assert recipes[0] != recipes[1]


class TestTheSetDetail:
    """`GET /api/sets/{id}` — the two fields the Set page gained."""

    @staticmethod
    def data(response: httpx.Response) -> Any:
        body = response.json()
        assert body["ok"] is True, body
        assert body["meta"]["request_id"]
        return body["data"]

    @staticmethod
    def version(detail: dict[str, Any], version_no: int) -> dict[str, Any]:
        return next(
            entry for entry in detail["versions"] if entry["version"]["version_no"] == version_no
        )

    @pytest.fixture
    async def seeded(self, client: httpx.AsyncClient, app: FastAPI) -> dict[str, Any]:
        """Three versions, two of which predicted something and collected shots.

        v1 predicted against nothing and was pulled at 28 s and 30 s, rated 3.
        v2 predicted against v1 and was pulled at 34 s and 36 s, rated 5. v3
        changed the dose, predicted nothing and has no shots.

        Two repeat groups of two, so the pooled spread is
        sqrt((1 + 1 + 1 + 1) / 2) = 1.414 s on 2 degrees of freedom — one short
        of the three it takes to be measured, so a difference is held against
        the 2 s floor.
        """
        bean = self.data(
            await client.post("/api/beans", json={"name": "Ethiopia Guji", "roast_level": "light"})
        )
        created = self.data(
            await client.post(
                "/api/sets",
                json={
                    "name": "Guji on the Niche",
                    "bean_id": bean["id"],
                    "version": {
                        "dose_g": 18.0,
                        "target_yield_g": 36.0,
                        "grind_setting": "22",
                        "prediction": "around 30 s, balanced",
                    },
                },
            )
        )
        second = self.data(
            await client.post(
                f"/api/sets/{created['id']}/versions",
                json={
                    "grind_setting": "21",
                    "intent": "one finer",
                    "prediction": "a longer shot, a point better",
                },
            )
        )
        third = self.data(
            await client.post(
                f"/api/sets/{created['id']}/versions",
                json={"dose_g": 18.5, "intent": "a touch more coffee"},
            )
        )
        shots = ShotsRepository(app.state.db)
        plan = [
            (second["id"], "000001", 34_000, 5),
            (second["id"], "000002", 36_000, 5),
            (created["current_version_id"], "000003", 28_000, 3),
            (created["current_version_id"], "000004", 30_000, 3),
        ]
        for version_id, device_id, duration_ms, rating in plan:
            shot_id = await store_shot(shots, device_id, duration_ms=duration_ms)
            await client.put(
                f"/api/shots/{shot_id}/set-version", json={"set_version_id": version_id}
            )
            await client.put(
                f"/api/shots/{shot_id}/judgement",
                json={"rating": rating, "balance": "balanced", "decision": "improve"},
            )
        return {
            "set_id": created["id"],
            "v1": created["current_version_id"],
            "v2": second["id"],
            "v3": third["id"],
        }

    async def test_the_detail_carries_the_spread_with_its_basis(
        self, client: httpx.AsyncClient, seeded: dict[str, Any]
    ) -> None:
        detail = self.data(await client.get(f"/api/sets/{seeded['set_id']}"))

        spread = {entry["measure"]: entry for entry in detail["spread"]}
        assert [entry["measure"] for entry in detail["spread"]] == list(SPREAD_MEASURES)
        assert spread["shot_time_s"] == {
            "measure": "shot_time_s",
            "value": 1.4,
            "measured": False,
            "shots": 4,
            "degrees_of_freedom": 2,
            "recorded": 4,
            "floor": 2.0,
        }
        # Every shot weighed the same and every rating repeated, so both of
        # those spreads are an honest zero rather than an absence.
        assert spread["yield_g"]["recorded"] == 4
        assert spread["rating"]["value"] == 0.0
        # Nothing here has diagnostics, so there is no pressure to speak of.
        assert spread["peak_pressure_bar"]["recorded"] == 0
        assert spread["peak_pressure_bar"]["value"] is None

    async def test_a_version_with_a_prediction_carries_its_evidence(
        self, client: httpx.AsyncClient, seeded: dict[str, Any]
    ) -> None:
        detail = self.data(await client.get(f"/api/sets/{seeded['set_id']}"))

        evidence = self.version(detail, 2)["evidence"]
        assert evidence["version_id"] == seeded["v2"]
        assert evidence["compares_to_version_id"] == seeded["v1"]
        times = next(row for row in evidence["measures"] if row["measure"] == "shot_time_s")
        assert times["this"] == {"mean": 35.0, "n": 2}
        assert times["other"] == {"mean": 29.0, "n": 2}
        assert times["difference"] == 6.0
        assert times["yardstick"] == 2.0  # not measured yet: the floor
        assert times["verdict"] == "beyond"
        # Two whole stars, well past the half-star floor.
        ratings = next(row for row in evidence["measures"] if row["measure"] == "rating")
        assert (ratings["difference"], ratings["verdict"]) == (2.0, "beyond")
        # A measure neither side records has no verdict to give.
        pressure = next(
            row for row in evidence["measures"] if row["measure"] == "peak_pressure_bar"
        )
        assert pressure["verdict"] == "no_data"
        # Plain facts, with nothing held against them.
        assert evidence["this"]["balanced"] == 2
        assert evidence["this"]["improve"] == 2
        assert evidence["other"]["version_no"] == 1

    async def test_a_version_with_no_prediction_has_no_evidence(
        self, client: httpx.AsyncClient, seeded: dict[str, Any]
    ) -> None:
        """It is evidence *for* a prediction; a version that made none needs none."""
        detail = self.data(await client.get(f"/api/sets/{seeded['set_id']}"))

        third = self.version(detail, 3)
        assert third["version"]["prediction"] == ""
        assert third["evidence"] is None

    async def test_a_prediction_compared_against_nothing_gets_its_own_side_only(
        self, client: httpx.AsyncClient, seeded: dict[str, Any]
    ) -> None:
        detail = self.data(await client.get(f"/api/sets/{seeded['set_id']}"))

        evidence = self.version(detail, 1)["evidence"]
        assert evidence["compares_to_version_id"] is None
        assert evidence["other"] is None
        assert evidence["this"]["shots"] == 2
        times = next(row for row in evidence["measures"] if row["measure"] == "shot_time_s")
        assert times["this"] == {"mean": 29.0, "n": 2}
        assert times["other"] is None
        assert times["verdict"] == "no_data"

    async def test_a_compared_version_with_no_shots_is_a_side_with_no_values(
        self, client: httpx.AsyncClient, app: FastAPI, seeded: dict[str, Any]
    ) -> None:
        """v3 collected nothing, and v4 predicts against it.

        There is a compared version, so there is a compared side — it simply
        holds nothing. Saying that is not the same as saying the prediction was
        absolute, and the two used to come out of the same branch.
        """
        fourth = self.data(
            await client.post(
                f"/api/sets/{seeded['set_id']}/versions",
                json={
                    "grind_setting": "20",
                    "intent": "two finer",
                    "prediction": "shorter than v3",
                    "compares_to_version_id": seeded["v3"],
                },
            )
        )
        shots = ShotsRepository(app.state.db)
        for device_id in ("000010", "000011"):
            shot_id = await store_shot(shots, device_id, duration_ms=31_000)
            await client.put(
                f"/api/shots/{shot_id}/set-version", json={"set_version_id": fourth["id"]}
            )

        detail = self.data(await client.get(f"/api/sets/{seeded['set_id']}"))

        evidence = self.version(detail, 4)["evidence"]
        assert evidence["compares_to_version_id"] == seeded["v3"]
        # The side exists and is empty, rather than being absent.
        assert evidence["other"] == {
            "version_id": seeded["v3"],
            "version_no": 3,
            "shots": 0,
            "sour": 0,
            "balanced": 0,
            "bitter": 0,
            "keep": 0,
            "improve": 0,
            "unlabelled": 0,
        }
        times = next(row for row in evidence["measures"] if row["measure"] == "shot_time_s")
        assert times["this"] == {"mean": 31.0, "n": 2}
        assert times["other"] == {"mean": None, "n": 0}
        assert times["verdict"] == "no_data"
        assert times["difference"] is None
        # Every measure, not only the one this version happens to record.
        assert all(row["other"] == {"mean": None, "n": 0} for row in evidence["measures"])

    async def test_a_difference_is_served_a_decimal_finer_than_the_means(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        """32.04 s against 30.0 s is +2.04 s, beyond a 2 s floor.

        Served at the means' own precision the row would read "32.0, 30.0, +2.0,
        beyond 2.0" and argue with itself. Both shot times are exact in the
        header's milliseconds, so this is the archive's own arithmetic end to
        end.
        """
        bean = self.data(await client.post("/api/beans", json={"name": "Kenya AA"}))
        created = self.data(
            await client.post(
                "/api/sets",
                json={
                    "name": "Kenya",
                    "bean_id": bean["id"],
                    "version": {"dose_g": 18.0, "grind_setting": "22"},
                },
            )
        )
        second = self.data(
            await client.post(
                f"/api/sets/{created['id']}/versions",
                json={"grind_setting": "21", "intent": "one finer", "prediction": "a hair longer"},
            )
        )
        shots = ShotsRepository(app.state.db)
        plan = [
            (created["current_version_id"], "000020", 30_000),
            (created["current_version_id"], "000021", 30_000),
            (second["id"], "000022", 32_040),
            (second["id"], "000023", 32_040),
        ]
        for version_id, device_id, duration_ms in plan:
            shot_id = await store_shot(shots, device_id, duration_ms=duration_ms)
            await client.put(
                f"/api/shots/{shot_id}/set-version", json={"set_version_id": version_id}
            )

        detail = self.data(await client.get(f"/api/sets/{created['id']}"))

        times = next(
            row
            for row in self.version(detail, 2)["evidence"]["measures"]
            if row["measure"] == "shot_time_s"
        )
        assert times["this"]["mean"] == 32.0
        assert times["other"]["mean"] == 30.0
        assert times["difference"] == 2.04
        assert times["yardstick"] == 2.0
        assert times["verdict"] == "beyond"

    async def test_a_set_with_no_shots_still_answers_with_every_measure(
        self, client: httpx.AsyncClient
    ) -> None:
        bean = self.data(await client.post("/api/beans", json={"name": "Kenya AA"}))
        created = self.data(
            await client.post("/api/sets", json={"name": "Kenya", "bean_id": bean["id"]})
        )

        detail = self.data(await client.get(f"/api/sets/{created['id']}"))

        assert len(detail["spread"]) == 6
        assert all(entry["value"] is None for entry in detail["spread"])
        assert all(entry["recorded"] == 0 for entry in detail["spread"])
