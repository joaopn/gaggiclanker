"""Predictions, outcomes, dead ends and the roll back, at the repository level.

The properties here are the ones that make a track record mean anything: a
prediction cannot be written after the shot it would grade itself against, an
outcome cannot be recorded on a version there is nothing to grade, and a roll
back is an ordinary version that happens to copy an old recipe. All of them are
asserted against the real file, because half of them are enforced by reading
another table inside the same transaction.
"""

from __future__ import annotations

from typing import cast

from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.sets import (
    RollbackWrite,
    SetVersionPatch,
    SetVersionRow,
    SetVersionWrite,
    SetWrite,
    VersionOutcomeWrite,
    VersionPredictionWrite,
    dead_end_ids,
    live_line,
    track_record,
)
from gaggiclanker.domain.vocab import VERSION_OUTCOMES, VersionOutcome
from tests.sets.conftest import Fixtures, make_profile_version, make_shot

#: Every field a new version copies from the version it is built on. Named here
#: rather than imported from the repository's private tuple so that dropping one
#: from the copy *and* from the tuple still fails this file.
INHERITED = (
    "profile_version_id",
    "grind_setting",
    "grind_value",
    "dose_g",
    "target_yield_g",
    "target_temperature_c",
)


async def _set_with_versions(wired: Fixtures, count: int = 2) -> tuple[int, list[SetVersionRow]]:
    """A Set with ``count`` versions, oldest first."""
    row = await wired.sets.create(
        SetWrite(name="Guji on the Niche", bean_id=wired.bean_id),
        SetVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22"),
    )
    for step in range(2, count + 1):
        await wired.sets.add_version(
            row.id, SetVersionPatch(grind_setting=str(23 - step), intent=f"step {step}")
        )
    versions = sorted(await wired.sets.versions(row.id), key=lambda v: v.version_no)
    return row.id, versions


async def _judged_shot(
    wired: Fixtures, version_id: int, device_id: str, decision: str | None
) -> int:
    """A shot on this version, labelled (or deliberately not)."""
    shot_id = await make_shot(wired.db, device_id)
    assert await wired.sets.assign_shot(shot_id, version_id)
    if decision is not None:
        await JudgementsRepository(wired.db).upsert(
            shot_id,
            JudgementWrite.model_validate({"decision": decision}),
        )
    return shot_id


class TestPrediction:
    async def test_it_defaults_to_comparing_against_the_parent(self, wired: Fixtures) -> None:
        """ "Less bitter" means "than the version I changed from", nine times in ten."""
        set_id, versions = await _set_with_versions(wired)

        result = await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="less bitter, a bit shorter")
        )

        assert result.refused is None
        stored = result.version
        assert stored is not None
        assert stored.compares_to_version_id == versions[0].id
        assert stored.compares_to_version_no == 1
        assert stored.prediction_at
        assert stored.outcome_state == "open"

    async def test_a_prediction_on_version_one_compares_against_nothing(
        self, wired: Fixtures
    ) -> None:
        """There is nothing earlier, so it is graded against its own numbers."""
        set_id, versions = await _set_with_versions(wired, count=1)

        result = await wired.sets.set_prediction(
            set_id, versions[0].id, VersionPredictionWrite(prediction="a clean 1:2 in 28 s")
        )

        assert result.version is not None
        assert result.version.compares_to_version_id is None
        assert result.version.outcome_state == "open"

    async def test_an_explicit_comparison_is_kept(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired, count=3)

        result = await wired.sets.set_prediction(
            set_id,
            versions[2].id,
            VersionPredictionWrite(
                prediction="back to where v1 was", compares_to_version_id=versions[0].id
            ),
        )

        assert result.version is not None
        assert result.version.compares_to_version_no == 1

    async def test_a_version_of_another_set_cannot_be_compared_against(
        self, wired: Fixtures
    ) -> None:
        """Two Sets are two coffees; the comparison would mean nothing."""
        set_id, versions = await _set_with_versions(wired)
        other_id, other_versions = await _set_with_versions(wired, count=1)

        result = await wired.sets.set_prediction(
            set_id,
            versions[1].id,
            VersionPredictionWrite(prediction="x", compares_to_version_id=other_versions[0].id),
        )

        assert result.refused == "bad_compare"
        assert other_id != set_id

    async def test_a_version_cannot_be_compared_against_itself(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired)

        result = await wired.sets.set_prediction(
            set_id,
            versions[1].id,
            VersionPredictionWrite(prediction="x", compares_to_version_id=versions[1].id),
        )

        assert result.refused == "bad_compare"

    async def test_a_newer_version_cannot_be_compared_against(self, wired: Fixtures) -> None:
        """ "Compared to v3" on v2 is a claim about a version that did not exist.

        The comparison is what the prediction is graded against, so it has to be
        something this version could have been expected to improve on.
        """
        set_id, versions = await _set_with_versions(wired, count=3)

        result = await wired.sets.set_prediction(
            set_id,
            versions[1].id,
            VersionPredictionWrite(prediction="x", compares_to_version_id=versions[2].id),
        )

        assert result.refused == "bad_compare"
        # And the older one is accepted, so the rule is about the direction
        # rather than about the field being rejected outright.
        allowed = await wired.sets.set_prediction(
            set_id,
            versions[1].id,
            VersionPredictionWrite(prediction="x", compares_to_version_id=versions[0].id),
        )
        assert allowed.version is not None

    async def test_it_is_refused_once_the_version_has_a_shot(self, wired: Fixtures) -> None:
        """The whole point: a prediction typed after the cup is a memory."""
        set_id, versions = await _set_with_versions(wired)
        await _judged_shot(wired, versions[1].id, "000701", None)

        result = await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="too late")
        )

        assert result.refused == "has_shots"
        stored = await wired.sets.get_version(versions[1].id)
        assert stored is not None and stored.prediction == ""

    async def test_an_empty_prediction_clears_the_comparison_with_it(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired)
        await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="less bitter")
        )

        result = await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="")
        )

        assert result.version is not None
        assert result.version.compares_to_version_id is None
        assert result.version.prediction_at is None
        assert result.version.outcome_state == "no_prediction"

    async def test_a_version_of_another_set_is_not_found(self, wired: Fixtures) -> None:
        _, versions = await _set_with_versions(wired)
        other_id, _ = await _set_with_versions(wired, count=1)

        result = await wired.sets.set_prediction(
            other_id, versions[1].id, VersionPredictionWrite(prediction="x")
        )

        assert result.refused == "no_version"

    async def test_an_explicit_null_comparison_is_kept_as_nothing(self, wired: Fixtures) -> None:
        """ "Not sent" and "sent as null" are two different requests.

        Omitting the field means "against the version I changed from". Sending
        it as null means "against nothing" — grade this on the numbers it states
        — and a repository that defaulted both to the parent would make the
        second choice unavailable on every version but the first.
        """
        set_id, versions = await _set_with_versions(wired)

        result = await wired.sets.set_prediction(
            set_id,
            versions[1].id,
            VersionPredictionWrite(prediction="a clean 1:2 in 28 s", compares_to_version_id=None),
        )

        assert result.version is not None
        assert result.version.compares_to_version_id is None
        assert result.version.compares_to_version_no is None

    async def test_a_new_version_can_be_told_to_compare_against_nothing(
        self, wired: Fixtures
    ) -> None:
        set_id, _ = await _set_with_versions(wired)

        version = await wired.sets.add_version(
            set_id,
            SetVersionPatch(
                intent="a fresh baseline", prediction="a clean 1:2", compares_to_version_id=None
            ),
        )

        assert version is not None
        assert version.compares_to_version_id is None

    async def test_whitespace_is_not_a_prediction(self, wired: Fixtures) -> None:
        """Three spaces are "no prediction", not a prediction nobody can read."""
        set_id, versions = await _set_with_versions(wired)

        result = await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="   ")
        )

        assert result.version is not None
        assert result.version.prediction == ""
        assert result.version.outcome_state == "no_prediction"
        assert result.version.compares_to_version_id is None

        # And the surrounding whitespace comes off a real one.
        kept = await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="  less bitter  ")
        )
        assert kept.version is not None and kept.version.prediction == "less bitter"

    async def test_a_prediction_is_refused_while_a_grade_stands(self, wired: Fixtures) -> None:
        """A grade is a statement about the words as they were written.

        The shot can be unfiled — which is how a version with an outcome can
        come to have no shots — but the grade still stands, and rewriting the
        prediction underneath it would leave it attached to something nobody
        ever predicted.
        """
        set_id, versions = await _set_with_versions(wired)
        await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="less bitter")
        )
        shot_id = await _judged_shot(wired, versions[1].id, "000740", "keep")
        await wired.sets.set_outcome(set_id, versions[1].id, VersionOutcomeWrite(outcome="held"))
        # Unfiling the shot reopens the "no shots" window; the grade does not.
        assert await wired.sets.assign_shot(shot_id, None)

        result = await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="something else entirely")
        )

        assert result.refused == "has_outcome"
        stored = await wired.sets.get_version(versions[1].id)
        assert stored is not None and stored.prediction == "less bitter"

        # Taking the grade back opens it again: the rule is against the habit,
        # not against somebody deliberately rewriting their own record.
        await wired.sets.clear_outcome(set_id, versions[1].id)
        rewritten = await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="something else entirely")
        )
        assert rewritten.version is not None

    async def test_a_new_version_can_carry_its_prediction_at_birth(self, wired: Fixtures) -> None:
        """The form asks for it beside the intent, so one request records both."""
        set_id, versions = await _set_with_versions(wired, count=1)

        version = await wired.sets.add_version(
            set_id,
            SetVersionPatch(grind_setting="21", intent="one finer", prediction="less sour"),
        )

        assert version is not None
        assert version.prediction == "less sour"
        assert version.compares_to_version_id == versions[0].id
        assert version.prediction_at


class TestOutcome:
    async def test_it_is_refused_with_no_prediction(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired)
        await _judged_shot(wired, versions[1].id, "000702", "keep")

        result = await wired.sets.set_outcome(
            set_id, versions[1].id, VersionOutcomeWrite(outcome="held")
        )

        assert result.refused == "no_prediction"

    async def test_it_is_refused_until_a_shot_has_been_judged(self, wired: Fixtures) -> None:
        """A discarded shot says the shot went wrong, not that the recipe did."""
        set_id, versions = await _set_with_versions(wired)
        await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="less bitter")
        )
        await _judged_shot(wired, versions[1].id, "000703", None)
        await _judged_shot(wired, versions[1].id, "000704", "discard")

        assert (
            await wired.sets.set_outcome(
                set_id, versions[1].id, VersionOutcomeWrite(outcome="held")
            )
        ).refused == "nothing_to_grade"

        await _judged_shot(wired, versions[1].id, "000705", "improve")
        result = await wired.sets.set_outcome(
            set_id,
            versions[1].id,
            VersionOutcomeWrite(outcome="partly_held", note="shorter, still bitter"),
        )

        assert result.version is not None
        assert result.version.outcome == "partly_held"
        assert result.version.outcome_note == "shorter, still bitter"
        assert result.version.outcome_at
        assert result.version.outcome_state == "partly_held"

    async def test_a_grade_can_be_cleared_but_not_changed_once_there_is_nothing_to_grade(
        self, wired: Fixtures
    ) -> None:
        """Clearing is always allowed; re-grading needs something to grade.

        This is the pair the outcome control on the Set page has to offer
        exactly: Clear on any graded version, Change only while a judged,
        non-discarded shot is still filed under it.
        """
        set_id, versions = await _set_with_versions(wired)
        await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="less bitter")
        )
        shot_id = await _judged_shot(wired, versions[1].id, "000741", "keep")
        await wired.sets.set_outcome(set_id, versions[1].id, VersionOutcomeWrite(outcome="held"))
        assert await wired.sets.assign_shot(shot_id, None)

        changed = await wired.sets.set_outcome(
            set_id, versions[1].id, VersionOutcomeWrite(outcome="failed")
        )
        assert changed.refused == "nothing_to_grade"

        cleared = await wired.sets.clear_outcome(set_id, versions[1].id)
        assert cleared.version is not None and cleared.version.outcome is None

    async def test_a_grade_can_be_changed_and_taken_back(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired)
        await wired.sets.set_prediction(
            set_id, versions[1].id, VersionPredictionWrite(prediction="less bitter")
        )
        await _judged_shot(wired, versions[1].id, "000706", "keep")
        await wired.sets.set_outcome(set_id, versions[1].id, VersionOutcomeWrite(outcome="held"))

        changed = await wired.sets.set_outcome(
            set_id,
            versions[1].id,
            VersionOutcomeWrite(outcome="failed", note="second one was awful"),
        )
        assert changed.version is not None and changed.version.outcome == "failed"

        cleared = await wired.sets.clear_outcome(set_id, versions[1].id)
        assert cleared.version is not None
        assert cleared.version.outcome is None
        assert cleared.version.outcome_note == ""
        assert cleared.version.outcome_at is None
        assert cleared.version.outcome_state == "open"


class TestRollback:
    async def test_it_copies_every_inherited_field_and_sets_the_three_references(
        self, wired: Fixtures
    ) -> None:
        # Two profiles, because the profile is the one inherited field a roll
        # back could plausibly be written to take from the *current* version
        # rather than the restored one, and a test whose two versions share a
        # profile would not notice.
        profile = await make_profile_version(wired.db, "9 Bar Espresso")
        other_profile = await make_profile_version(wired.db, "Turbo")
        row = await wired.sets.create(
            SetWrite(name="Guji on the Niche", bean_id=wired.bean_id),
            SetVersionWrite(
                profile_version_id=profile,
                grind_setting="22",
                grind_value=22,
                dose_g=18,
                target_yield_g=36,
                target_temperature_c=93,
            ),
        )
        first = await wired.sets.current_version(row.id)
        assert first is not None
        # A version that changed **every** inherited field, and was pushed to
        # the machine: each one is then guarded by the loop below.
        await wired.sets.add_version(
            row.id,
            SetVersionPatch(
                profile_version_id=other_profile,
                grind_setting="19",
                grind_value=19,
                dose_g=20,
                target_yield_g=50,
                target_temperature_c=90,
                intent="a turbo",
                pushed_device_profile_id="7",
            ),
        )
        current = await wired.sets.current_version(row.id)
        assert current is not None
        assert other_profile != profile
        for field in INHERITED:
            assert getattr(current, field) != getattr(first, field), field

        result = await wired.sets.rollback(
            row.id,
            RollbackWrite(
                to_version_id=first.id,
                intent="that was worse",
                prediction="back to the old balance",
            ),
        )

        rolled = result.version
        assert rolled is not None
        assert rolled.version_no == 3
        for field in INHERITED:
            assert getattr(rolled, field) == getattr(first, field), field
        # The parent is what was current, so the diff reads as the reversal.
        assert rolled.parent_version_id == current.id
        assert rolled.restores_version_id == first.id
        assert rolled.restores_version_no == 1
        assert rolled.compares_to_version_id == current.id
        assert rolled.origin == "manual"
        # A device id names a file on the display; a new version never inherits one.
        assert rolled.pushed_device_profile_id is None

    async def test_rolling_back_to_the_current_version_is_refused(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired)

        result = await wired.sets.rollback(set_id, RollbackWrite(to_version_id=versions[-1].id))

        assert result.refused == "current_version"

    async def test_a_version_of_another_set_is_not_a_target(self, wired: Fixtures) -> None:
        set_id, _ = await _set_with_versions(wired)
        _, other = await _set_with_versions(wired, count=1)

        result = await wired.sets.rollback(set_id, RollbackWrite(to_version_id=other[0].id))

        assert result.refused == "no_target"


class TestDeadEnds:
    """A version is a dead end when it is not on the line being brewed.

    The line is walked back from the current version: from a roll back to what
    it restored, from anything else to its parent. These tests are written in
    version numbers rather than ids, through :func:`_numbers`, because that is
    how the rule is stated and how anybody reading a failure will think.
    """

    async def _numbers(self, wired: Fixtures, set_id: int) -> tuple[set[int], list[int]]:
        versions = await wired.sets.versions(set_id)
        by_id = {version.id: version.version_no for version in versions}
        return (
            {by_id[version_id] for version_id in dead_end_ids(versions)},
            [by_id[version_id] for version_id in live_line(versions)],
        )

    async def test_one_roll_back_mutes_what_it_stepped_over(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired, count=5)
        rolled = await wired.sets.rollback(set_id, RollbackWrite(to_version_id=versions[2].id))
        assert rolled.version is not None

        muted, line = await self._numbers(wired, set_id)

        # v4 and v5 are the branch nobody is on; v3 came back and v6 is current.
        assert muted == {4, 5}
        assert line == [6, 3, 2, 1]

    async def test_two_successive_roll_backs(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired, count=5)
        # v6 restores v3: v4 and v5 are off the line.
        first = await wired.sets.rollback(set_id, RollbackWrite(to_version_id=versions[2].id))
        assert first.version is not None
        await wired.sets.add_version(set_id, SetVersionPatch(grind_setting="16", intent="again"))
        # v8 restores v6 — a roll back onto a version that was itself one.
        second = await wired.sets.rollback(set_id, RollbackWrite(to_version_id=first.version.id))
        assert second.version is not None

        muted, line = await self._numbers(wired, set_id)

        assert muted == {4, 5, 7}
        assert line == [8, 6, 3, 2, 1]

    async def test_a_roll_back_onto_a_former_dead_end_brings_it_back(self, wired: Fixtures) -> None:
        """v5 restores v2, v6 restores v4, v7 restores v3.

        Every one of v4, v5 and v6 is off the line, and v6 is off it although
        nothing later spans it: the only thing that makes a version live is
        being reachable by walking back from where the Set is now. This is the
        case a "muted between the roll back and its target" rule gets wrong.
        """
        set_id, versions = await _set_with_versions(wired, count=4)
        await wired.sets.rollback(set_id, RollbackWrite(to_version_id=versions[1].id))  # v5 → v2
        await wired.sets.rollback(set_id, RollbackWrite(to_version_id=versions[3].id))  # v6 → v4
        await wired.sets.rollback(set_id, RollbackWrite(to_version_id=versions[2].id))  # v7 → v3

        muted, line = await self._numbers(wired, set_id)

        assert muted == {4, 5, 6}
        assert line == [7, 3, 2, 1]

    async def test_a_set_with_no_roll_back_has_none(self, wired: Fixtures) -> None:
        set_id, _ = await _set_with_versions(wired, count=3)
        versions = await wired.sets.versions(set_id)
        assert dead_end_ids(versions) == set()
        assert len(live_line(versions)) == 3

    async def test_an_empty_set_is_not_a_crash(self, wired: Fixtures) -> None:
        assert dead_end_ids([]) == set()
        assert live_line([]) == []


class TestTheLineOnMalformedData:
    """The walk terminates on data no route can write, and says what it found.

    `restores_version_id` cannot point forwards or at itself through the API,
    and a parent cycle cannot be created either — but the Set page must not hang
    on a row that somehow does, and a `while` over a linked list is exactly the
    shape that would. These rows are built in memory: there is no database
    write that could produce them.
    """

    def _row(self, version_no: int, **over: object) -> SetVersionRow:
        return SetVersionRow.model_validate(
            {
                "id": version_no,
                "set_id": 1,
                "version_no": version_no,
                "created_at": "2026-04-01T08:00:00.000Z",
                **over,
            }
        )

    def test_a_restore_pointing_forwards(self) -> None:
        # v2 claims to restore v3, which is newer than it.
        rows = [
            self._row(1),
            self._row(2, parent_version_id=1, restores_version_id=3),
            self._row(3, parent_version_id=2),
        ]

        assert live_line(rows) == [3, 2]
        assert dead_end_ids(rows) == {1}

    def test_a_version_that_restores_itself(self) -> None:
        rows = [self._row(1), self._row(2, parent_version_id=1, restores_version_id=2)]

        assert live_line(rows) == [2]
        assert dead_end_ids(rows) == {1}

    def test_a_reference_to_an_id_that_is_not_here(self) -> None:
        rows = [self._row(1), self._row(2, parent_version_id=1, restores_version_id=909)]

        assert live_line(rows) == [2]
        assert dead_end_ids(rows) == {1}

    def test_a_parent_cycle(self) -> None:
        rows = [self._row(1, parent_version_id=2), self._row(2, parent_version_id=1)]

        assert live_line(rows) == [2, 1]
        assert dead_end_ids(rows) == set()


class TestTrackRecordAndTarget:
    async def test_every_outcome_is_counted_and_every_one_of_them_is_graded(
        self, wired: Fixtures
    ) -> None:
        """All four grades, so leaving any of them out of `graded` fails here.

        Parametrised over the vocabulary rather than over four literals: a fifth
        outcome added to `VersionOutcome` lands in this test on its own, and
        `graded` has to keep up with it.
        """
        # One version per grade, plus one open and one that predicted nothing.
        set_id, versions = await _set_with_versions(wired, count=len(VERSION_OUTCOMES) + 2)
        graded = list(zip(versions[1:], VERSION_OUTCOMES, strict=False))
        for index, (version, outcome) in enumerate(graded):
            await wired.sets.set_prediction(
                set_id, version.id, VersionPredictionWrite(prediction="less bitter")
            )
            await _judged_shot(wired, version.id, f"00074{index}", "keep")
            result = await wired.sets.set_outcome(
                set_id, version.id, VersionOutcomeWrite(outcome=cast("VersionOutcome", outcome))
            )
            assert result.version is not None, outcome
        # The last version is left open: a prediction nobody has graded yet.
        await wired.sets.set_prediction(
            set_id, versions[-1].id, VersionPredictionWrite(prediction="still guessing")
        )

        record = track_record(await wired.sets.versions(set_id))

        for outcome in VERSION_OUTCOMES:
            assert getattr(record, outcome) == 1, outcome
        assert record.open == 1
        assert record.no_prediction == 1
        assert record.graded == len(VERSION_OUTCOMES)

    async def test_the_roll_back_target_is_the_last_version_with_a_keep(
        self, wired: Fixtures
    ) -> None:
        set_id, versions = await _set_with_versions(wired, count=4)
        await _judged_shot(wired, versions[0].id, "000720", "keep")
        await _judged_shot(wired, versions[1].id, "000721", "keep")
        # Discarded and unlabelled shots are not somebody saying "it worked".
        await _judged_shot(wired, versions[2].id, "000722", "discard")
        await _judged_shot(wired, versions[2].id, "000723", None)
        # The current version's own Keep does not count: you are already there.
        await _judged_shot(wired, versions[3].id, "000724", "keep")

        assert await wired.sets.rollback_target(set_id) == versions[1].id

    async def test_there_is_no_target_when_nothing_was_ever_kept(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired, count=2)
        await _judged_shot(wired, versions[0].id, "000725", "improve")

        assert await wired.sets.rollback_target(set_id) is None

    async def test_the_label_counts_are_grouped_per_version(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired)
        await _judged_shot(wired, versions[1].id, "000730", "keep")
        await _judged_shot(wired, versions[1].id, "000731", "keep")
        await _judged_shot(wired, versions[1].id, "000732", "improve")
        await _judged_shot(wired, versions[1].id, "000733", None)

        counts = await wired.sets.label_counts(set_id)

        assert counts[versions[1].id].keep == 2
        assert counts[versions[1].id].improve == 1
        assert counts[versions[1].id].discard == 0
        assert counts[versions[1].id].unlabelled == 1
        assert versions[0].id not in counts
