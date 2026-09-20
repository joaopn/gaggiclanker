"""How much a Set's shots vary when nothing in the recipe changed.

Two shots pulled with the same grind, the same dose and the same profile do not
land on the same numbers, and the difference between them is not an experiment
— it is the noise floor of the whole setup: the grinder, the puck, the basket,
the scale and the bag. Until you know roughly how big it is, "31 s against 34 s"
says nothing at all.

This module works that number out, per measure, from shots the archive already
holds, with no model anywhere near it. It is deliberately pure: plain values in,
plain values out, so the same shots always give the same answer and a grade can
be traced back to arithmetic somebody can redo on paper.

**Pooled within-recipe deviation.** Shots brewed with the same five recipe
fields are repeats of each other. Each repeat group contributes its own shots'
distance from *its own* mean, and those distances are pooled across every group
of the Set:

    spread = sqrt( sum_g sum_i (x_gi - mean_g)^2 / sum_g (n_g - 1) )

Pooled rather than "the standard deviation of all the Set's shots", because the
Set's shots are meant to differ: that figure would measure the dial-in itself
and grow every time a change worked. Pooled rather than "the deviation of the
one version with the most shots", because a Set is usually many versions of two
or three shots each, and the pooling is what turns those into one usable figure.
A group of one contributes nothing — one shot has no distance from itself — but
it costs nothing either, so a Set of singletons simply has no spread yet.

**Three degrees of freedom.** With one or two, the estimate is worth less than
the floor beside it: a sample standard deviation on 2 degrees of freedom has a
relative error around 50%, and "±0.4 s" read off two pairs of shots would be
taken seriously by a reader who has no way of seeing how thin it is. So the
number is still served — it is the only evidence there is — but it is flagged
"not measured yet" and the floors are what a difference is held against.

**Floors.** A conservative minimum per measure, so a young Set does not declare
a 0.3 s difference significant. They are first numbers, to be tuned with use:
they are constants here rather than settings because a person tuning them by
hand would be tuning what counts as evidence, one Set at a time.

**Two standard errors.** A difference between two means is held against the
standard error of that difference, `spread * sqrt(1/n_this + 1/n_other)`, times
two: two standard errors is the ordinary "outside the noise" bar (roughly 95%
for a normal difference), and it is the one number that shrinks properly as
either side collects more shots. The floor is still applied as a minimum, so a
measured spread that has become implausibly small cannot make everything
significant.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import sqrt
from typing import Literal

from pydantic import BaseModel, ConfigDict

from gaggiclanker.domain.vocab import (
    MEASURE_DECIMALS,
    MEASURE_DIFFERENCE_DECIMALS,
    SPREAD_MEASURES,
    SpreadMeasure,
)

__all__ = [
    "MEASURED_DEGREES_OF_FREEDOM",
    "MEASURE_DECIMALS",
    "MEASURE_FLOORS",
    "CountedShot",
    "EvidenceCounts",
    "MeasureEvidence",
    "MeasureSide",
    "MeasureSpread",
    "Spread",
    "Verdict",
    "VersionEvidence",
    "pooled_spreads",
    "spread_report",
    "version_evidence",
]

#: Below this the spread is "not measured yet". Three is the point where a
#: pooled estimate stops being mostly noise about itself — see the module
#: docstring. A number computed on fewer is still served, flagged, because it is
#: the only evidence there is; it is just not what a difference is held against.
MEASURED_DEGREES_OF_FREEDOM = 3

#: The smallest difference each measure is ever asked to take seriously. First
#: numbers, to be tuned once there are enough Sets to tune them against —
#: constants rather than settings, because tuning them per Set would be tuning
#: what counts as evidence.
MEASURE_FLOORS: dict[SpreadMeasure, float] = {
    "shot_time_s": 2.0,
    "first_drip_s": 1.0,
    "yield_g": 1.0,
    "peak_pressure_bar": 0.3,
    "brew_flow_ml_s": 0.2,
    "rating": 0.5,
}

#: What a difference turned out to be. `no_data` is not a failure: it is what a
#: measure the archive does not hold on one of the two sides honestly reads as,
#: and it is also every measure of a version that compares against nothing.
type Verdict = Literal["beyond", "inside", "no_data"]


class CountedShot(BaseModel):
    """One shot that counts towards a Set's spread, and what it measured.

    "Counts" is decided before this model: filed under a version of the Set, not
    quarantined, not incomplete, not labelled Discard. An unlabelled shot counts
    — it is plain data, and leaving it out would mean the spread depended on how
    diligent somebody had been about labelling.

    Every measure is optional and every missing one is simply not counted for
    that shot, rather than counted as a zero: a machine with no pressure sensor
    records no peak pressure, and a shot nobody rated has no rating. The five
    recipe fields are the *version's*, carried on the shot so the grouping is a
    pass over one list.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: int
    version_id: int
    version_no: int

    #: The version's recipe, which is what makes two shots repeats of each
    #: other. A roll back copies these five, so its shots join the group of the
    #: version it restored with no special case anywhere.
    profile_version_id: int | None = None
    grind_setting: str | None = None
    grind_value: float | None = None
    dose_g: float | None = None
    target_yield_g: float | None = None

    shot_time_s: float | None = None
    first_drip_s: float | None = None
    yield_g: float | None = None
    peak_pressure_bar: float | None = None
    brew_flow_ml_s: float | None = None
    rating: float | None = None

    #: Shown beside the measures as plain facts and never compared against a
    #: yardstick: they are words, not quantities, and "two sour against one
    #: bitter" is for a person to read rather than for arithmetic to grade.
    balance: str | None = None
    decision: str | None = None

    @property
    def recipe(self) -> tuple[object, ...]:
        """The five fields, as the key two repeats share."""
        return (
            self.profile_version_id,
            self.grind_setting,
            self.grind_value,
            self.dose_g,
            self.target_yield_g,
        )

    def measure(self, name: SpreadMeasure) -> float | None:
        """This shot's value for a measure, read by its slug.

        The slugs in `vocab.SPREAD_MEASURES` are field names here on purpose: a
        measure added to the vocabulary and forgotten on this model raises
        rather than serving an empty column nobody notices.
        """
        value: float | None = getattr(self, name)
        return value


@dataclass(frozen=True, slots=True)
class Spread:
    """One measure's pooled deviation, unrounded, with what it rests on.

    Unrounded because this is what differences are held against, and rounding
    the yardstick before comparing would move the verdict of a difference that
    sits near it. The rounded form a page reads is :class:`MeasureSpread`.
    """

    measure: SpreadMeasure
    #: ``None`` when no repeat group had two values for this measure.
    value: float | None
    #: The shots in the groups that contributed: the basis, as in "from 9
    #: repeat shots".
    shots: int
    degrees_of_freedom: int
    #: Counted shots holding a value for this measure at all, repeats or not.
    #: Not the same question as ``shots``: a Set whose every version has one
    #: shot records plenty of shot times and has no repeats, and a page that
    #: could not tell that from "this archive holds no such number" would drop
    #: the line instead of saying it is not measured yet.
    recorded: int

    @property
    def measured(self) -> bool:
        return self.degrees_of_freedom >= MEASURED_DEGREES_OF_FREEDOM

    @property
    def floor(self) -> float:
        return MEASURE_FLOORS[self.measure]

    def yardstick(self, n_this: int, n_other: int) -> float:
        """What a difference between two means of this measure is held against.

        The floor alone until the spread is measured: before that there is no
        estimate worth multiplying. Afterwards, two standard errors of the
        difference, and never less than the floor.
        """
        if not self.measured or self.value is None:
            return self.floor
        standard_error = self.value * sqrt(1.0 / n_this + 1.0 / n_other)
        return max(self.floor, 2.0 * standard_error)


class MeasureSpread(BaseModel):
    """One line of the Set page's Spread block: the number and its basis."""

    model_config = ConfigDict(extra="forbid")

    measure: SpreadMeasure
    #: Rounded for reading. ``None`` when nothing has been repeated yet.
    value: float | None = None
    #: Whether it is worth holding a difference against. False is "not measured
    #: yet", and then the floor is the yardstick.
    measured: bool = False
    #: The shots in the groups that contributed.
    shots: int = 0
    #: What ``measured`` is decided on. The Spread block reads it too, as the
    #: other half of the basis: subtracted from ``shots`` it is the number of
    #: repeat groups that contributed — one degree goes on each group's own
    #: average — so the line can say "from 9 repeat shots of 3 recipes", which
    #: is a different piece of evidence from 9 shots of one.
    degrees_of_freedom: int = 0
    #: Counted shots holding a value for this measure at all, repeats or not.
    #: Zero is what makes the page leave the measure out altogether.
    recorded: int = 0
    floor: float


class MeasureSide(BaseModel):
    """One version's shots, for one measure: what they averaged and how many."""

    model_config = ConfigDict(extra="forbid")

    mean: float | None = None
    n: int = 0


class MeasureEvidence(BaseModel):
    """One row of a version's evidence table."""

    model_config = ConfigDict(extra="forbid")

    measure: SpreadMeasure
    this: MeasureSide
    #: ``None`` when the version compares against nothing at all. A compared
    #: version that simply holds no value for this measure still has a side,
    #: with ``n`` zero, because "you have no yields recorded for v3" is a
    #: different sentence from "this prediction compares against nothing".
    other: MeasureSide | None = None
    #: This side minus the other, rounded. Signed: the direction is half of what
    #: a prediction claimed.
    difference: float | None = None
    verdict: Verdict = "no_data"
    #: What the difference was held against, rounded, so the page can say so.
    #: ``None`` when there was nothing to hold.
    yardstick: float | None = None


class EvidenceCounts(BaseModel):
    """One side's plain facts: how the cup went, and how it was labelled.

    No verdict on any of them. There is no Discard count because a discarded
    shot is not a counted shot — it says the shot went wrong, not that the
    recipe did.
    """

    model_config = ConfigDict(extra="forbid")

    version_id: int
    version_no: int
    shots: int = 0
    sour: int = 0
    balanced: int = 0
    bitter: int = 0
    keep: int = 0
    improve: int = 0
    unlabelled: int = 0


class VersionEvidence(BaseModel):
    """This version's shots against the compared version's, measure by measure.

    Served on a version that carries a prediction and on no other: it is the
    numbers that prediction is graded on, and hanging it off a version nobody
    predicted anything about would be a table answering no question.
    """

    model_config = ConfigDict(extra="forbid")

    version_id: int
    compares_to_version_id: int | None = None
    measures: list[MeasureEvidence]
    this: EvidenceCounts
    #: ``None`` for a version compared against nothing — the first version of a
    #: Set, or one deliberately graded on the numbers it states. Its own side is
    #: still worth showing: those are the numbers.
    other: EvidenceCounts | None = None


def pooled_spreads(shots: Sequence[CountedShot]) -> dict[SpreadMeasure, Spread]:
    """Every measure's pooled within-recipe deviation, unrounded.

    One entry per measure in the vocabulary, always, including the ones this Set
    holds nothing for: a caller that had to ask whether a key was there would
    end up with its own opinion about what "no data" looks like.
    """
    groups = _repeat_groups(shots)
    return {measure: _pool(groups, measure) for measure in SPREAD_MEASURES}


def spread_report(spreads: Mapping[SpreadMeasure, Spread]) -> list[MeasureSpread]:
    """The spreads as the API serves them: rounded, in vocabulary order."""
    return [
        MeasureSpread(
            measure=name,
            value=_round(name, spreads[name].value),
            measured=spreads[name].measured,
            shots=spreads[name].shots,
            degrees_of_freedom=spreads[name].degrees_of_freedom,
            recorded=spreads[name].recorded,
            floor=MEASURE_FLOORS[name],
        )
        for name in SPREAD_MEASURES
    ]


def version_evidence(
    shots: Sequence[CountedShot],
    spreads: Mapping[SpreadMeasure, Spread],
    *,
    version_id: int,
    version_no: int,
    compares_to_version_id: int | None = None,
    compares_to_version_no: int | None = None,
) -> VersionEvidence:
    """What this version's shots did, beside the compared version's.

    Both sides are *all* of that version's counted shots, never a chosen one: a
    prediction graded against the best shot of the version before it is graded
    against a memory.
    """
    mine = _of_version(shots, version_id)
    # One question, asked once: does this version name a version to compare
    # against? Not "does that version have any shots" — a compared version with
    # nothing recorded is a side with no values, which is a different answer
    # from having no side at all, and asking the question twice is how the
    # measure rows came to disagree with the counts beside them.
    theirs: list[CountedShot] | None = None
    other: EvidenceCounts | None = None
    if compares_to_version_id is not None and compares_to_version_no is not None:
        theirs = _of_version(shots, compares_to_version_id)
        other = _counts(theirs, compares_to_version_id, compares_to_version_no)
    return VersionEvidence(
        version_id=version_id,
        compares_to_version_id=compares_to_version_id,
        measures=[_measure_evidence(mine, theirs, spreads[name], name) for name in SPREAD_MEASURES],
        this=_counts(mine, version_id, version_no),
        other=other,
    )


# ── the arithmetic ───────────────────────────────────────────────────


def _repeat_groups(shots: Sequence[CountedShot]) -> list[list[CountedShot]]:
    """The counted shots grouped by their version's recipe, in shot-id order.

    The sort is load-bearing, and for one specific reason: :func:`_pool` adds
    each group's sum of squares onto a running total with ``+=``, which is a
    left fold, and floating-point addition is not associative. A Set with one
    wildly scattered recipe and two tight ones totals differently depending on
    which group is added first, so the group order has to follow the data
    rather than however a query happened to return the rows. Sorting by shot id
    fixes both the order of the groups (first appearance) and the order within
    each one. `tests/domain/test_spread.py` walks every permutation of a Set
    built to show it.
    """
    groups: dict[tuple[object, ...], list[CountedShot]] = {}
    for shot in sorted(shots, key=lambda shot: shot.shot_id):
        groups.setdefault(shot.recipe, []).append(shot)
    return list(groups.values())


def _pool(groups: Sequence[Sequence[CountedShot]], measure: SpreadMeasure) -> Spread:
    """Pool one measure's within-group deviations over the groups that have two."""
    sum_of_squares = 0.0
    degrees_of_freedom = 0
    shots = 0
    recorded = 0
    for group in groups:
        values = [value for shot in group if (value := shot.measure(measure)) is not None]
        recorded += len(values)
        if len(values) < 2:
            # One value has no distance from its own mean. It is not an error
            # and not a zero: it simply contributes nothing, which is what lets
            # a Set of many two-shot versions still produce a figure.
            continue
        mean = sum(values) / len(values)
        sum_of_squares += sum((value - mean) ** 2 for value in values)
        degrees_of_freedom += len(values) - 1
        shots += len(values)
    return Spread(
        measure=measure,
        value=sqrt(sum_of_squares / degrees_of_freedom) if degrees_of_freedom else None,
        shots=shots,
        degrees_of_freedom=degrees_of_freedom,
        recorded=recorded,
    )


def _of_version(shots: Sequence[CountedShot], version_id: int | None) -> list[CountedShot]:
    """One version's counted shots, in the same shot-id order as the grouping.

    Not load-bearing the way the grouping's sort is: nothing here folds across
    sub-totals — a side's mean is one ``sum`` over the whole side — so this is
    for reading, and for building both sides of a comparison the same way.
    """
    if version_id is None:
        return []
    return sorted(
        (shot for shot in shots if shot.version_id == version_id),
        key=lambda shot: shot.shot_id,
    )


def _measure_evidence(
    mine: Sequence[CountedShot],
    theirs: Sequence[CountedShot] | None,
    spread: Spread,
    measure: SpreadMeasure,
) -> MeasureEvidence:
    """One row: this side, the other side when there is one, and the verdict.

    ``theirs`` is ``None`` for "this version compares against nothing" and a
    (possibly empty) list for "it compares against that version". The two are
    not the same answer and the row says so differently: no other column at all,
    against an other column reading "nothing recorded".
    """
    ours = [value for shot in mine if (value := shot.measure(measure)) is not None]
    this = MeasureSide(mean=_round(measure, _mean(ours)), n=len(ours))
    if theirs is None:
        # No compared version at all. The version's own numbers are still the
        # evidence — there is simply nothing to subtract them from.
        return MeasureEvidence(measure=measure, this=this)
    others = [value for shot in theirs if (value := shot.measure(measure)) is not None]
    other = MeasureSide(mean=_round(measure, _mean(others)), n=len(others))
    if not ours or not others:
        return MeasureEvidence(measure=measure, this=this, other=other)
    # Compared unrounded, reported rounded: a difference of 1.96 s against a
    # yardstick of 2.0 s is inside it, and rounding either before the
    # comparison would make that a coin toss decided by the display format.
    difference = sum(ours) / len(ours) - sum(others) / len(others)
    yardstick = spread.yardstick(len(ours), len(others))
    return MeasureEvidence(
        measure=measure,
        this=this,
        other=other,
        # A decimal finer than the means, so the row cannot read as a
        # contradiction of its own verdict: "+2.04 s, beyond 2.00 s" rather
        # than "+2.0 s, beyond 2.0 s". A tie at that last decimal is still
        # possible, and the verdict is the arithmetic's, not the display's.
        difference=_round_fine(measure, difference),
        # Strictly beyond: a difference exactly the size of the yardstick is
        # what the yardstick says is ordinary, so it is inside.
        verdict="beyond" if abs(difference) > yardstick else "inside",
        yardstick=_round_fine(measure, yardstick),
    )


def _counts(shots: Sequence[CountedShot], version_id: int, version_no: int) -> EvidenceCounts:
    return EvidenceCounts(
        version_id=version_id,
        version_no=version_no,
        shots=len(shots),
        sour=sum(1 for shot in shots if shot.balance == "sour"),
        balanced=sum(1 for shot in shots if shot.balance == "balanced"),
        bitter=sum(1 for shot in shots if shot.balance == "bitter"),
        keep=sum(1 for shot in shots if shot.decision == "keep"),
        improve=sum(1 for shot in shots if shot.decision == "improve"),
        unlabelled=sum(1 for shot in shots if shot.decision is None),
    )


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(measure: SpreadMeasure, value: float | None) -> float | None:
    """A mean or a spread, at the precision the measure is read in."""
    return None if value is None else round(value, MEASURE_DECIMALS[measure])


def _round_fine(measure: SpreadMeasure, value: float | None) -> float | None:
    """A difference or a yardstick: one decimal finer than the means it came from."""
    return None if value is None else round(value, MEASURE_DIFFERENCE_DECIMALS[measure])
