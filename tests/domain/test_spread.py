"""The spread, the yardstick and the evidence, by hand.

Every number in this file was worked out on paper first, which is the point of
the module being pure: what a version's prediction is graded against has to be
arithmetic somebody can check, not a figure that came out of a service.

The worked example most of these lean on:

    group A (one recipe):   28 s, 30 s        mean 29, squares 1 + 1  = 2, df 1
    group B (another):      32 s, 35 s, 38 s  mean 35, squares 9+0+9 = 18, df 2

    pooled = sqrt((2 + 18) / (1 + 2)) = sqrt(20/3) = 2.581988897...
"""

from __future__ import annotations

from itertools import permutations
from math import sqrt

import pytest

from gaggiclanker.domain.spread import (
    MEASURE_FLOORS,
    MEASURED_DEGREES_OF_FREEDOM,
    CountedShot,
    MeasureEvidence,
    pooled_spreads,
    spread_report,
    version_evidence,
)
from gaggiclanker.domain.vocab import MEASURE_DECIMALS, MEASURE_DIFFERENCE_DECIMALS, SPREAD_MEASURES

#: The two recipes the worked example uses. Five fields, because that is what
#: makes two shots repeats of each other.
RECIPE_A = {"profile_version_id": 7, "grind_setting": "22", "grind_value": 22.0, "dose_g": 18.0}
RECIPE_B = {"profile_version_id": 7, "grind_setting": "21", "grind_value": 21.0, "dose_g": 18.0}

POOLED_SHOT_TIME = sqrt(20 / 3)


def shot(shot_id: int, version_id: int, recipe: dict[str, object], **values: object) -> CountedShot:
    """One counted shot: its version, its version's recipe, what it measured."""
    return CountedShot.model_validate(
        {
            "shot_id": shot_id,
            "version_id": version_id,
            "version_no": version_id,
            "target_yield_g": 36.0,
            **recipe,
            **values,
        }
    )


def worked_example() -> list[CountedShot]:
    """Two repeat groups: two shots on v1's recipe, three on v2's."""
    return [
        shot(1, 1, RECIPE_A, shot_time_s=28.0),
        shot(2, 1, RECIPE_A, shot_time_s=30.0),
        shot(3, 2, RECIPE_B, shot_time_s=32.0),
        shot(4, 2, RECIPE_B, shot_time_s=35.0),
        shot(5, 2, RECIPE_B, shot_time_s=38.0),
    ]


# ── the pooled spread ────────────────────────────────────────────────


def test_the_spread_pools_each_group_around_its_own_mean() -> None:
    spread = pooled_spreads(worked_example())["shot_time_s"]

    assert spread.value == pytest.approx(POOLED_SHOT_TIME)
    assert spread.degrees_of_freedom == 3
    assert spread.shots == 5
    assert spread.recorded == 5
    assert spread.measured is True


def test_a_group_of_one_contributes_nothing_but_is_still_recorded() -> None:
    """One shot has no distance from itself — and it is not evidence of zero."""
    lonely = shot(6, 3, {"grind_setting": "19", "grind_value": 19.0}, shot_time_s=99.0)
    spread = pooled_spreads([*worked_example(), lonely])["shot_time_s"]

    assert spread.value == pytest.approx(POOLED_SHOT_TIME)
    assert spread.degrees_of_freedom == 3
    assert spread.shots == 5
    # The archive does hold a sixth shot time; it just has nothing to repeat.
    assert spread.recorded == 6


def test_a_roll_backs_shots_are_repeats_of_the_version_it_copied() -> None:
    """Same five fields, same group — no special case for the roll back."""
    rolled_back = [
        shot(1, 1, RECIPE_A, shot_time_s=28.0),
        shot(2, 2, RECIPE_B, shot_time_s=35.0),
        # v3 restores v1: it copies the recipe, so its shot repeats v1's.
        shot(3, 3, RECIPE_A, shot_time_s=30.0),
    ]

    spread = pooled_spreads(rolled_back)["shot_time_s"]

    assert spread.degrees_of_freedom == 1
    assert spread.shots == 2
    # 28 and 30 around 29: squares 1 + 1 over one degree of freedom.
    assert spread.value == pytest.approx(sqrt(2.0))


def test_a_changed_grind_is_a_different_group() -> None:
    """The same three shots, with v3 on its own recipe, repeat nothing."""
    separated = [
        shot(1, 1, RECIPE_A, shot_time_s=28.0),
        shot(2, 2, RECIPE_B, shot_time_s=35.0),
        shot(3, 3, {"grind_setting": "20", "grind_value": 20.0}, shot_time_s=30.0),
    ]

    spread = pooled_spreads(separated)["shot_time_s"]

    assert spread.value is None
    assert spread.degrees_of_freedom == 0
    assert spread.shots == 0
    assert spread.recorded == 3


def test_a_measure_missing_on_some_shots_uses_the_shots_that_have_it() -> None:
    """A shot with no scale still counts for its shot time."""
    shots = [
        shot(1, 1, RECIPE_A, shot_time_s=28.0, yield_g=36.0),
        shot(2, 1, RECIPE_A, shot_time_s=30.0, yield_g=38.0),
        shot(3, 1, RECIPE_A, shot_time_s=32.0),
    ]

    spreads = pooled_spreads(shots)

    assert spreads["shot_time_s"].degrees_of_freedom == 2
    assert spreads["shot_time_s"].shots == 3
    # 36 and 38 around 37: squares 1 + 1, one degree of freedom.
    assert spreads["yield_g"].value == pytest.approx(sqrt(2.0))
    assert spreads["yield_g"].degrees_of_freedom == 1
    assert spreads["yield_g"].shots == 2
    assert spreads["yield_g"].recorded == 2


def test_a_measure_nothing_recorded_is_empty_rather_than_zero() -> None:
    spreads = pooled_spreads(worked_example())

    assert spreads["peak_pressure_bar"].value is None
    assert spreads["peak_pressure_bar"].recorded == 0
    assert spreads["peak_pressure_bar"].measured is False


@pytest.mark.parametrize(("times", "measured"), [((28.0, 30.0), False), ((28.0, 30.0, 32.0), True)])
def test_three_degrees_of_freedom_is_where_it_becomes_measured(
    times: tuple[float, ...], measured: bool
) -> None:
    """Two groups of two is 2 degrees of freedom; one more shot makes it 3."""
    shots = [
        shot(1, 1, RECIPE_A, shot_time_s=28.0),
        shot(2, 1, RECIPE_A, shot_time_s=30.0),
        *[shot(10 + i, 2, RECIPE_B, shot_time_s=value) for i, value in enumerate(times)],
    ]

    spread = pooled_spreads(shots)["shot_time_s"]

    assert spread.degrees_of_freedom == len(times)
    assert spread.measured is measured
    # The number is served either way: it is the only evidence there is.
    assert spread.value is not None


def test_the_threshold_is_three_degrees_of_freedom() -> None:
    assert MEASURED_DEGREES_OF_FREEDOM == 3


def test_the_floors_are_the_numbers_the_design_named() -> None:
    """Conservative first numbers, one per measure, and no measure without one."""
    assert MEASURE_FLOORS == {
        "shot_time_s": 2.0,
        "first_drip_s": 1.0,
        "yield_g": 1.0,
        "peak_pressure_bar": 0.3,
        "brew_flow_ml_s": 0.2,
        "rating": 0.5,
    }
    assert tuple(MEASURE_FLOORS) == SPREAD_MEASURES


# ── the yardstick ────────────────────────────────────────────────────


def test_the_yardstick_is_the_floor_until_the_spread_is_measured() -> None:
    shots = [
        shot(1, 1, RECIPE_A, shot_time_s=28.0),
        shot(2, 1, RECIPE_A, shot_time_s=30.0),
    ]

    spread = pooled_spreads(shots)["shot_time_s"]

    assert spread.measured is False
    # However many shots are on either side: there is no estimate to multiply.
    assert spread.yardstick(2, 3) == 2.0
    assert spread.yardstick(20, 30) == 2.0


def test_a_measured_yardstick_is_two_standard_errors_of_the_difference() -> None:
    spread = pooled_spreads(worked_example())["shot_time_s"]

    # 2 x sqrt(20/3) x sqrt(1/2 + 1/3) = 2 x sqrt(100/18) = 4.714045...
    assert spread.yardstick(2, 3) == pytest.approx(2 * POOLED_SHOT_TIME * sqrt(1 / 2 + 1 / 3))
    assert spread.yardstick(2, 3) == pytest.approx(4.714045207910317)
    # It shrinks as either side collects shots.
    assert spread.yardstick(20, 30) < spread.yardstick(2, 3)


def test_the_floor_is_the_minimum_even_once_the_spread_is_measured() -> None:
    """Four identical shots have a spread of zero, and zero proves nothing."""
    shots = [shot(i, 1, RECIPE_A, shot_time_s=30.0) for i in range(1, 6)]

    spread = pooled_spreads(shots)["shot_time_s"]

    assert spread.measured is True
    assert spread.value == pytest.approx(0.0)
    assert spread.yardstick(5, 5) == MEASURE_FLOORS["shot_time_s"]


# ── the served report ────────────────────────────────────────────────


def test_the_report_is_one_rounded_entry_per_measure_in_vocabulary_order() -> None:
    report = spread_report(pooled_spreads(worked_example()))

    assert [entry.measure for entry in report] == list(SPREAD_MEASURES)
    times = report[0]
    assert times.measure == "shot_time_s"
    assert times.value == 2.6  # sqrt(20/3) to one decimal
    assert times.measured is True
    assert times.shots == 5
    assert times.degrees_of_freedom == 3
    assert times.floor == 2.0
    # Every measure is served, including the ones this Set holds nothing for.
    assert report[3].measure == "peak_pressure_bar"
    assert report[3].value is None
    assert report[3].recorded == 0


def test_bar_and_flow_are_served_to_two_decimals() -> None:
    shots = [
        shot(1, 1, RECIPE_A, peak_pressure_bar=9.0, brew_flow_ml_s=2.0),
        shot(2, 1, RECIPE_A, peak_pressure_bar=9.25, brew_flow_ml_s=2.15),
    ]

    report = {entry.measure: entry for entry in spread_report(pooled_spreads(shots))}

    # A pair's pooled deviation is its gap over sqrt(2): 0.25 bar and 0.15 ml/s.
    assert report["peak_pressure_bar"].value == 0.18
    assert report["brew_flow_ml_s"].value == 0.11
    # Rounded to one decimal both would read 0.2, which is the whole floor.
    assert report["peak_pressure_bar"].floor == 0.3


# ── the evidence ─────────────────────────────────────────────────────


def _two_versions(this: list[float], other: list[float]) -> list[CountedShot]:
    """Shot times on v2 and on v1, two recipes, nothing else recorded."""
    return [
        *[shot(10 + i, 1, RECIPE_A, shot_time_s=value) for i, value in enumerate(other)],
        *[shot(20 + i, 2, RECIPE_B, shot_time_s=value) for i, value in enumerate(this)],
    ]


def _evidence_for(shots: list[CountedShot], measure: str = "shot_time_s") -> MeasureEvidence:
    evidence = version_evidence(
        shots,
        pooled_spreads(shots),
        version_id=2,
        version_no=2,
        compares_to_version_id=1,
        compares_to_version_no=1,
    )
    return next(row for row in evidence.measures if row.measure == measure)


def test_a_difference_larger_than_the_yardstick_is_beyond_the_spread() -> None:
    row = _evidence_for(_two_versions([31.0, 31.0], [28.0, 28.0]))

    assert row.this.mean == 31.0
    assert row.this.n == 2
    assert row.other is not None
    assert row.other.mean == 28.0
    assert row.other.n == 2
    assert row.difference == 3.0
    assert row.yardstick == 2.0  # not measured yet: the floor
    assert row.verdict == "beyond"


def test_a_difference_exactly_the_size_of_the_yardstick_is_inside_it() -> None:
    """The yardstick says what is ordinary, so landing on it is ordinary."""
    row = _evidence_for(_two_versions([30.0, 30.0], [28.0, 28.0]))

    assert row.difference == 2.0
    assert row.yardstick == 2.0
    assert row.verdict == "inside"


def test_a_difference_is_signed_the_way_the_prediction_reads() -> None:
    row = _evidence_for(_two_versions([28.0, 28.0], [31.0, 31.0]))

    assert row.difference == -3.0
    assert row.verdict == "beyond"


def test_a_measure_neither_side_records_is_no_data() -> None:
    evidence_rating = _evidence_for(_two_versions([30.0, 30.0], [28.0, 28.0]), "rating")

    assert evidence_rating.verdict == "no_data"
    assert evidence_rating.difference is None
    assert evidence_rating.yardstick is None
    assert evidence_rating.this.n == 0


def test_a_measure_only_one_side_records_is_no_data_with_that_side_shown() -> None:
    shots = [
        shot(10, 1, RECIPE_A, shot_time_s=28.0),
        shot(20, 2, RECIPE_B, shot_time_s=30.0, rating=4.0),
    ]

    row = _evidence_for(shots, "rating")

    assert row.verdict == "no_data"
    assert row.this.mean == 4.0
    assert row.other is not None
    assert row.other.mean is None
    assert row.other.n == 0


def test_a_version_compared_against_nothing_gets_its_own_side_only() -> None:
    shots = [
        shot(1, 1, RECIPE_A, shot_time_s=28.0, rating=4.0),
        shot(2, 1, RECIPE_A, shot_time_s=30.0, rating=5.0),
    ]

    evidence = version_evidence(
        shots, pooled_spreads(shots), version_id=1, version_no=1, compares_to_version_id=None
    )

    assert evidence.other is None
    assert evidence.this.version_no == 1
    assert evidence.this.shots == 2
    assert [row.verdict for row in evidence.measures] == ["no_data"] * len(SPREAD_MEASURES)
    times = evidence.measures[0]
    assert times.this.mean == 29.0
    assert times.this.n == 2
    assert times.other is None


def test_the_measured_yardstick_is_what_a_difference_is_held_against() -> None:
    """Five shots over two recipes: 3 degrees of freedom, so the estimate counts."""
    shots = worked_example()

    row = _evidence_for(shots)

    assert row.this.mean == 35.0  # 32, 35, 38
    assert row.other is not None
    assert row.other.mean == 29.0  # 28, 30
    assert row.difference == 6.0
    assert row.yardstick == 4.71
    assert row.verdict == "beyond"


def test_the_counts_are_plain_facts_with_no_verdict_on_them() -> None:
    shots = [
        shot(10, 1, RECIPE_A, shot_time_s=28.0, balance="sour", decision="improve"),
        shot(11, 1, RECIPE_A, shot_time_s=30.0, balance="sour"),
        shot(20, 2, RECIPE_B, shot_time_s=31.0, balance="balanced", decision="keep"),
        shot(21, 2, RECIPE_B, shot_time_s=31.0, balance="bitter", decision="keep"),
    ]

    evidence = version_evidence(
        shots,
        pooled_spreads(shots),
        version_id=2,
        version_no=2,
        compares_to_version_id=1,
        compares_to_version_no=1,
    )

    assert evidence.this.model_dump() == {
        "version_id": 2,
        "version_no": 2,
        "shots": 2,
        "sour": 0,
        "balanced": 1,
        "bitter": 1,
        "keep": 2,
        "improve": 0,
        "unlabelled": 0,
    }
    assert evidence.other is not None
    assert (evidence.other.sour, evidence.other.improve, evidence.other.unlabelled) == (2, 1, 1)


# ── the other side is that version's own shots, all of them ──────────


def test_the_other_side_is_the_compared_versions_own_shots_only() -> None:
    """A roll back's shots pool with the recipe it copied — and grade nothing.

    v3 restores v1's recipe, so for the *spread* its shots are repeats of v1's:
    that is the whole point of grouping by recipe. For the *evidence* of a
    prediction against v1, they are not v1's shots and must not be averaged into
    v1's side. Reading the compared side off the recipe group instead of the
    version would turn v1's 29 s into 40 s here.
    """
    shots = [
        shot(1, 1, RECIPE_A, shot_time_s=28.0),
        shot(2, 1, RECIPE_A, shot_time_s=30.0),
        shot(3, 2, RECIPE_B, shot_time_s=34.0),
        shot(4, 2, RECIPE_B, shot_time_s=36.0),
        shot(5, 3, RECIPE_A, shot_time_s=50.0),
        shot(6, 3, RECIPE_A, shot_time_s=52.0),
    ]
    spreads = pooled_spreads(shots)

    # The spread pools all four of recipe A's shots with recipe B's two.
    assert spreads["shot_time_s"].shots == 6

    evidence = version_evidence(
        shots,
        spreads,
        version_id=2,
        version_no=2,
        compares_to_version_id=1,
        compares_to_version_no=1,
    )

    row = evidence.measures[0]
    assert row.measure == "shot_time_s"
    assert row.other is not None
    assert row.other.n == 2
    assert row.other.mean == 29.0  # v1's own two shots, not v3's 51
    assert evidence.other is not None
    assert evidence.other.shots == 2


def test_a_compared_version_with_no_shots_is_a_side_with_no_values() -> None:
    """Not the same answer as "this prediction compares against nothing".

    A version can name an earlier one that never collected a shot — a recipe
    tried once and changed again the same morning. There is a side; it simply
    holds nothing, and the row has to say that rather than pretend the
    prediction was absolute.
    """
    shots = [
        shot(20, 2, RECIPE_B, shot_time_s=34.0),
        shot(21, 2, RECIPE_B, shot_time_s=36.0),
    ]

    evidence = version_evidence(
        shots,
        pooled_spreads(shots),
        version_id=2,
        version_no=2,
        compares_to_version_id=1,
        compares_to_version_no=1,
    )

    assert evidence.other is not None
    assert evidence.other.version_no == 1
    assert evidence.other.shots == 0
    row = evidence.measures[0]
    assert row.this.n == 2
    # A side with no values, not the absence of a side.
    assert row.other is not None
    assert row.other.model_dump() == {"mean": None, "n": 0}
    assert row.verdict == "no_data"
    assert row.difference is None


# ── compared unrounded, written a decimal finer ──────────────────────


def test_a_difference_and_its_yardstick_are_written_finer_than_the_means() -> None:
    """Otherwise the row reads "+2.0 s, beyond 2.0 s" and argues with itself."""
    row = _evidence_for(_two_versions([32.04, 32.04], [30.0, 30.0]))

    assert row.this.mean == 32.0  # a mean is read at the measure's own precision
    assert row.other is not None
    assert row.other.mean == 30.0
    assert row.difference == 2.04
    assert row.yardstick == 2.0
    assert row.verdict == "beyond"
    assert MEASURE_DIFFERENCE_DECIMALS["shot_time_s"] == MEASURE_DECIMALS["shot_time_s"] + 1


@pytest.mark.parametrize(
    ("mean_this", "difference", "verdict"),
    [(32.04, 2.04, "beyond"), (31.96, 1.96, "inside")],
)
def test_a_difference_is_judged_on_the_number_not_on_the_rounding(
    mean_this: float, difference: float, verdict: str
) -> None:
    """2.04 s is beyond a 2 s floor and 1.96 s is inside it.

    Both would read "2.0" at the means' precision, and a comparison made after
    rounding would call them the same thing.
    """
    row = _evidence_for(_two_versions([mean_this, mean_this], [30.0, 30.0]))

    assert row.yardstick == 2.0
    assert row.difference == difference
    assert row.verdict == verdict


def test_a_difference_that_rounds_onto_the_yardstick_is_still_judged_on_the_number() -> None:
    """The same rule one decimal down, where even the finer display ties.

    A tie at the last written decimal cannot be designed away; what can be is
    the comparison being made on the display, and it is not.
    """
    row = _evidence_for(_two_versions([32.004, 32.004], [30.0, 30.0]))

    assert (row.difference, row.yardstick) == (2.0, 2.0)
    assert row.verdict == "beyond"


def test_bar_differences_get_a_third_decimal() -> None:
    shots = [
        shot(10, 1, RECIPE_A, peak_pressure_bar=9.0),
        shot(11, 1, RECIPE_A, peak_pressure_bar=9.0),
        shot(20, 2, RECIPE_B, peak_pressure_bar=9.304),
        shot(21, 2, RECIPE_B, peak_pressure_bar=9.304),
    ]

    row = _evidence_for(shots, "peak_pressure_bar")

    assert row.this.mean == 9.3
    assert row.difference == 0.304
    assert row.yardstick == 0.3
    assert row.verdict == "beyond"


# ── determinism ──────────────────────────────────────────────────────


#: A Set whose pooled total depends on the order its groups are added in.
#:
#: One recipe scattered over 200 million (a sum of squares of 2e16) and two
#: tight ones (2 each). Added biggest-first the two 2s fall off the end of the
#: mantissa; added last they do not, and the totals differ in the last bit.
#: Chosen for its arithmetic rather than its plausibility as a set of shot
#: times: this is the property that makes the grouping's sort a decision rather
#: than a formality.
_SCATTERED = (1e8, 3e8)
_TIGHT = (29.0, 31.0)
_ALSO_TIGHT = (39.0, 41.0)
_RECIPE_C = {"profile_version_id": 7, "grind_setting": "20", "grind_value": 20.0, "dose_g": 18.0}


def _order_sensitive() -> list[CountedShot]:
    return [
        *[shot(1 + i, 1, RECIPE_A, shot_time_s=value) for i, value in enumerate(_SCATTERED)],
        *[shot(3 + i, 2, RECIPE_B, shot_time_s=value) for i, value in enumerate(_TIGHT)],
        *[shot(5 + i, 3, _RECIPE_C, shot_time_s=value) for i, value in enumerate(_ALSO_TIGHT)],
    ]


def test_the_same_shots_in_any_order_give_the_same_numbers() -> None:
    """Every ordering of six shots, compared at full precision.

    A real property rather than a tautology: the pooled total is folded across
    groups with ``+=``, floating-point addition is not associative, and these
    six shots are built so the fold's answer depends on which group comes
    first. It holds because the grouping sorts by shot id — take that sort out
    and this test fails.
    """
    shots = _order_sensitive()
    expected = pooled_spreads(shots)["shot_time_s"]
    expected_evidence = version_evidence(
        shots,
        pooled_spreads(shots),
        version_id=2,
        version_no=2,
        compares_to_version_id=1,
        compares_to_version_no=1,
    ).model_dump()

    for ordering in permutations(shots):
        shuffled = list(ordering)
        spreads = pooled_spreads(shuffled)
        # The unrounded figure, not the served one: rounding would hide exactly
        # the difference this is about.
        assert spreads["shot_time_s"] == expected
        assert (
            version_evidence(
                shuffled,
                spreads,
                version_id=2,
                version_no=2,
                compares_to_version_id=1,
                compares_to_version_no=1,
            ).model_dump()
            == expected_evidence
        )


def test_the_served_report_is_the_same_whatever_order_the_shots_arrive_in() -> None:
    shots = _order_sensitive()
    expected = [entry.model_dump() for entry in spread_report(pooled_spreads(shots))]

    for ordering in permutations(shots):
        report = spread_report(pooled_spreads(list(ordering)))
        assert [entry.model_dump() for entry in report] == expected
