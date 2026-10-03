"""Predictions, outcomes, dead ends and the revert, at the repository level.

The properties here are the ones that make a track record mean anything: a
prediction cannot be written after the shot it would grade itself against, an
outcome cannot be recorded on a version there is nothing to grade, and going
back to an earlier version writes no version at all. All of them are
asserted against the real file, because half of them are enforced by reading
another table inside the same transaction.
"""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.sets import (
    DesignBrief,
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
    versions = sorted(await wired.sets.versions(row.id), key=lambda v: (v.created_at, v.id))
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
        assert stored.compares_to_version_label == "v1"
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
        assert result.version.compares_to_version_label == "v1"

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
        assert result.version.compares_to_version_label is None

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


class TestRevert:
    """Going back writes no version: the Set is on the target again, as it was."""

    async def test_it_writes_no_version_and_leaves_the_target_untouched(
        self, wired: Fixtures
    ) -> None:
        set_id, versions = await _set_with_versions(wired, count=3)
        target = versions[1]
        await wired.sets.set_prediction(
            set_id, target.id, VersionPredictionWrite(prediction="less bitter, a bit shorter")
        )
        shot_id = await _judged_shot(wired, target.id, "000001", "keep")
        await wired.sets.set_outcome(
            set_id, target.id, VersionOutcomeWrite(outcome="held", note="it did")
        )
        before = await wired.sets.get_version(target.id)
        assert before is not None
        rows_before = await wired.db.fetch_value("SELECT COUNT(*) FROM set_versions")

        result = await wired.sets.rollback(
            set_id, RollbackWrite(to_version_id=target.id, note="that was worse")
        )

        assert result.refused is None and result.version is not None
        assert await wired.db.fetch_value("SELECT COUNT(*) FROM set_versions") == rows_before
        row = await wired.sets.get(set_id)
        assert row is not None and row.current_version_id == target.id
        # What the version says is byte for byte what it said, apart from being current.
        after = await wired.sets.get_version(target.id)
        assert after is not None and after.is_current and not before.is_current
        assert after.model_dump(exclude={"is_current"}) == before.model_dump(exclude={"is_current"})
        assert (after.prediction, after.outcome, after.outcome_note) == (
            "less bitter, a bit shorter",
            "held",
            "it did",
        )
        assert after.shot_count == 1
        # Exactly one log row, naming both versions.
        [revert] = await wired.sets.reverts(set_id)
        assert (revert.from_version_id, revert.to_version_id) == (versions[2].id, target.id)
        assert (revert.from_version_label, revert.to_version_label) == ("v1.2", "v1.1")
        assert revert.note == "that was worse" and revert.created_at
        assert shot_id

    async def test_a_prediction_is_not_part_of_a_revert(self, wired: Fixtures) -> None:
        with pytest.raises(ValidationError):
            RollbackWrite.model_validate({"to_version_id": 1, "prediction": "back"})
        with pytest.raises(ValidationError):
            RollbackWrite.model_validate({"to_version_id": 1, "intent": "back"})

    async def test_reverting_to_the_current_version_is_refused(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired)

        result = await wired.sets.rollback(set_id, RollbackWrite(to_version_id=versions[-1].id))

        assert result.refused == "current_version"
        assert await wired.sets.reverts(set_id) == []

    async def test_a_version_of_another_set_is_not_a_target(self, wired: Fixtures) -> None:
        set_id, _ = await _set_with_versions(wired)
        _, other = await _set_with_versions(wired, count=1)

        result = await wired.sets.rollback(set_id, RollbackWrite(to_version_id=other[0].id))

        assert result.refused == "no_target"

    async def test_a_set_being_designed_has_nothing_to_go_back_to(self, wired: Fixtures) -> None:
        designed = await wired.sets.create_design(
            SetWrite(name="Designing", bean_id=wired.bean_id), DesignBrief()
        )
        assert designed.current_version_id is not None

        result = await wired.sets.rollback(
            designed.id, RollbackWrite(to_version_id=designed.current_version_id)
        )

        assert result.refused == "designing"

    async def test_new_shots_and_the_next_version_follow_the_target(self, wired: Fixtures) -> None:
        profile = await make_profile_version(wired.db, "Older profile")
        row = await wired.sets.create(
            SetWrite(name="Guji", bean_id=wired.bean_id),
            SetVersionWrite(profile_version_id=profile, dose_g=18),
        )
        first = await wired.sets.current_version(row.id)
        assert first is not None
        other = await make_profile_version(wired.db, "Newer profile")
        second = await wired.sets.add_version(
            row.id, SetVersionPatch(profile_version_id=other), major=True
        )
        assert second is not None

        await wired.sets.rollback(row.id, RollbackWrite(to_version_id=first.id))

        # automatch: the profile the Set is on again is the one that matches.
        shot = await make_shot(wired.db, "000001", profile_version_id=profile)
        match = await wired.sets.profile_match(
            shot, profile_version_id=profile, device_profile_id=""
        )
        assert (match.outcome, match.set_version_id) == ("matched", first.id)
        other_shot = await make_shot(wired.db, "000002", profile_version_id=other)
        assert (
            await wired.sets.profile_match(
                other_shot, profile_version_id=other, device_profile_id=""
            )
        ).outcome == "unmatched"
        # The next change is built on the target and is its child.
        added = await wired.sets.add_version(row.id, SetVersionPatch(dose_g=19))
        assert added is not None and added.parent_version_id == first.id
        assert added.version_label == "v1.1"


class TestTheWayBackOffer:
    """`rollback_target`: the nearest ancestor with a Keep shot, never one that is ahead."""

    async def test_after_going_back_the_offer_does_not_point_forward(self, wired: Fixtures) -> None:
        row = await wired.sets.create(
            SetWrite(name="Guji", bean_id=wired.bean_id), SetVersionWrite(dose_g=18)
        )
        v1 = await wired.sets.current_version(row.id)
        assert v1 is not None
        v11 = await wired.sets.add_version(row.id, SetVersionPatch(grind_setting="21"))
        v2 = await wired.sets.add_version(row.id, SetVersionPatch(grind_setting="20"), major=True)
        v21 = await wired.sets.add_version(row.id, SetVersionPatch(grind_setting="19"))
        assert v11 is not None and v2 is not None and v21 is not None
        await _judged_shot(wired, v1.id, "000001", "keep")
        await _judged_shot(wired, v2.id, "000002", "keep")
        assert await wired.sets.rollback_target(row.id) == v2.id

        await wired.sets.rollback(row.id, RollbackWrite(to_version_id=v11.id))

        assert await wired.sets.rollback_target(row.id) == v1.id

    async def test_an_old_roll_back_row_steps_to_what_it_restored(self, wired: Fixtures) -> None:
        set_id, versions = await _set_with_versions(wired, count=3)
        await _judged_shot(wired, versions[0].id, "000001", "keep")
        await _judged_shot(wired, versions[1].id, "000002", "keep")
        await wired.db.execute(
            "UPDATE set_versions SET restores_version_id = ? WHERE id = ?",
            (versions[0].id, versions[2].id),
        )

        assert await wired.sets.rollback_target(set_id) == versions[0].id


class TestDeadEnds:
    """A version is a dead end when it is not on the line being brewed.

    The line is walked back from the current version through the fork history
    (`parent_version_id`), as in version control; a row an old roll back wrote
    (`restores_version_id`) steps to what it restored instead.
    """

    async def _labels(self, wired: Fixtures, set_id: int) -> tuple[set[str], list[str]]:
        versions = await wired.sets.versions(set_id)
        current = (await wired.sets.get(set_id)).current_version_id  # type: ignore[union-attr]
        by_id = {version.id: version.version_label for version in versions}
        return (
            {by_id[version_id] for version_id in dead_end_ids(versions, current)},
            [by_id[version_id] for version_id in live_line(versions, current)],
        )

    async def _fork(self, wired: Fixtures) -> tuple[int, dict[str, int]]:
        """v1 -> v1.1 -> v2 -> v2.1, by name."""
        row = await wired.sets.create(
            SetWrite(name="Guji", bean_id=wired.bean_id), SetVersionWrite(dose_g=18)
        )
        ids = {"v1": (await wired.sets.current_version(row.id)).id}  # type: ignore[union-attr]
        for label, patch, major in (
            ("v1.1", SetVersionPatch(grind_setting="21"), False),
            ("v2", SetVersionPatch(grind_setting="20"), True),
            ("v2.1", SetVersionPatch(grind_setting="19"), False),
        ):
            version = await wired.sets.add_version(row.id, patch, major=major)
            assert version is not None and version.version_label == label
            ids[label] = version.id
        return row.id, ids

    async def test_a_revert_makes_what_it_went_back_past_a_dead_end(self, wired: Fixtures) -> None:
        set_id, ids = await self._fork(wired)
        await wired.sets.rollback(set_id, RollbackWrite(to_version_id=ids["v1.1"]))
        added = await wired.sets.add_version(set_id, SetVersionPatch(dose_g=19))
        assert added is not None and added.version_label == "v1.2"

        dead, line = await self._labels(wired, set_id)

        assert dead == {"v2", "v2.1"}
        assert line == ["v1.2", "v1.1", "v1"]

    async def test_going_back_again_to_a_dead_end_brings_it_back(self, wired: Fixtures) -> None:
        set_id, ids = await self._fork(wired)
        await wired.sets.rollback(set_id, RollbackWrite(to_version_id=ids["v1.1"]))
        await wired.sets.add_version(set_id, SetVersionPatch(dose_g=19))
        await wired.sets.rollback(set_id, RollbackWrite(to_version_id=ids["v2.1"]))

        dead, line = await self._labels(wired, set_id)

        assert dead == {"v1.2"}
        assert line == ["v2.1", "v2", "v1.1", "v1"]

    async def test_an_old_roll_back_row_still_jumps_to_what_it_restored(
        self, wired: Fixtures
    ) -> None:
        """Rows written before a revert became a move of the pointer keep their meaning."""
        set_id, ids = await self._fork(wired)
        # v2.1 stands for an old roll back of v1: parent v2, restores v1.
        await wired.db.execute(
            "UPDATE set_versions SET restores_version_id = ? WHERE id = ?",
            (ids["v1"], ids["v2.1"]),
        )

        dead, line = await self._labels(wired, set_id)

        assert line == ["v2.1", "v1"]
        assert dead == {"v1.1", "v2"}

    async def test_a_set_with_no_revert_has_no_dead_end(self, wired: Fixtures) -> None:
        set_id, _ = await _set_with_versions(wired, count=3)
        versions = await wired.sets.versions(set_id)
        current = (await wired.sets.get(set_id)).current_version_id  # type: ignore[union-attr]
        assert dead_end_ids(versions, current) == set()
        assert len(live_line(versions, current)) == 3

    async def test_an_empty_set_is_not_a_crash(self, wired: Fixtures) -> None:
        assert dead_end_ids([], None) == set()
        assert live_line([], None) == []


class TestTheLineOnMalformedData:
    """The walk terminates on data no route can write, and says what it found.

    `restores_version_id` cannot point forwards or at itself through the API,
    and a parent cycle cannot be created either — but the Set page must not hang
    on a row that somehow does, and a `while` over a linked list is exactly the
    shape that would. These rows are built in memory: there is no database
    write that could produce them.
    """

    def _row(self, number: int, **over: object) -> SetVersionRow:
        return SetVersionRow.model_validate(
            {
                "id": number,
                "set_id": 1,
                "version_major": number,
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

        assert live_line(rows, 3) == [3, 2]
        assert dead_end_ids(rows, 3) == {1}

    def test_a_version_that_restores_itself(self) -> None:
        rows = [self._row(1), self._row(2, parent_version_id=1, restores_version_id=2)]

        assert live_line(rows, 2) == [2]
        assert dead_end_ids(rows, 2) == {1}

    def test_a_reference_to_an_id_that_is_not_here(self) -> None:
        rows = [self._row(1), self._row(2, parent_version_id=1, restores_version_id=909)]

        assert live_line(rows, 2) == [2]
        assert dead_end_ids(rows, 2) == {1}

    def test_a_parent_cycle(self) -> None:
        rows = [self._row(1, parent_version_id=2), self._row(2, parent_version_id=1)]

        assert live_line(rows, 2) == [2, 1]
        assert dead_end_ids(rows, 2) == set()


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
