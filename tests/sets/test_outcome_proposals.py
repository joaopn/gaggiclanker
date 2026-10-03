"""An agent's grade of a version waits for the person, and nothing reads it until they accept.

The properties the loop depends on, against the real file because most of them
are a constraint or a read inside the transaction that writes: one grade waits
per version (a partial unique index), a newer grade replaces it, every answer is
one transaction, and a grade that is waiting, dismissed or superseded is never a
recorded outcome.
"""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.outcome_proposals import (
    OutcomeProposalsRepository,
    OutcomeProposalWrite,
)
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
    VersionOutcomeWrite,
    VersionPredictionWrite,
)
from tests.sets.conftest import Fixtures, make_shot

NOTE = "Time held (31 s against 28 s); the sourness did not move; the yield held."


async def _graded_ready(
    wired: Fixtures, *, shots: int = 1, name: str = "Guji on the Niche", base: int = 700
) -> tuple[int, int]:
    """A Set whose current version has a prediction and ``shots`` Keep shots."""
    row = await wired.sets.create(
        SetWrite(name=name, bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        SetVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22"),
    )
    current = await wired.sets.current_version(row.id)
    assert current is not None
    await wired.sets.set_prediction(
        row.id,
        current.id,
        VersionPredictionWrite.model_validate(
            {"prediction": "Expect about 30 s.", "compares_to_version_id": None}
        ),
    )
    for number in range(shots):
        await _keep_shot(wired, current.id, f"{base + number:06d}")
    return row.id, current.id


async def _keep_shot(wired: Fixtures, version_id: int, device_id: str) -> int:
    shot_id = await make_shot(wired.db, device_id)
    assert await wired.sets.assign_shot(shot_id, version_id)
    await JudgementsRepository(wired.db).upsert(
        shot_id, JudgementWrite.model_validate({"decision": "keep"})
    )
    return shot_id


def _write(outcome: str = "partly_held", note: str = NOTE) -> OutcomeProposalWrite:
    return OutcomeProposalWrite.model_validate({"outcome": outcome, "note": note})


class TestWriting:
    async def test_a_proposal_waits_and_records_nothing(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired, shots=3)
        proposals = OutcomeProposalsRepository(wired.db)

        result = await proposals.create(set_id, version_id, _write())

        assert result.refused is None
        stored = result.proposal
        assert stored is not None
        assert stored.status == "proposed"
        assert stored.outcome == "partly_held"
        assert stored.counted_shots == 3
        assert stored.version_label == "v1"
        # The version's own grade is untouched: this is words until a person answers.
        version = await wired.sets.get_version(version_id)
        assert version is not None
        assert version.outcome is None
        assert version.outcome_state == "open"

    async def test_the_shots_it_rests_on_are_counted_when_written_and_now(
        self, wired: Fixtures
    ) -> None:
        set_id, version_id = await _graded_ready(wired, shots=2)
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, _write())).proposal
        assert stored is not None
        await _keep_shot(wired, version_id, "000799")

        read = await proposals.get(set_id, stored.id)
        assert read is not None
        assert (read.counted_shots, read.counted_shots_now) == (2, 3)

    async def test_a_newer_grade_replaces_the_waiting_one(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        first = (await proposals.create(set_id, version_id, _write("held"))).proposal
        assert first is not None

        second = await proposals.create(set_id, version_id, _write("failed"))

        assert second.replaced is not None and second.replaced.id == first.id
        old = await proposals.get(set_id, first.id)
        assert old is not None and old.status == "superseded"
        waiting = await proposals.waiting_for_version(version_id)
        assert waiting is not None and waiting.id == second.proposal.id  # type: ignore[union-attr]
        assert [p.status for p in await proposals.for_set(set_id)] == ["proposed", "superseded"]

    async def test_one_waits_per_version_whatever_the_repository_says(
        self, wired: Fixtures
    ) -> None:
        """The partial unique index is the guard for the stdio child, a second process."""
        set_id, version_id = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        await proposals.create(set_id, version_id, _write())
        with pytest.raises(sqlite3.IntegrityError):
            await wired.db.execute(
                "INSERT INTO set_outcome_proposals (set_id, set_version_id, outcome, note) "
                "VALUES (?, ?, 'held', 'x')",
                (set_id, version_id),
            )

    async def test_each_version_has_its_own_waiting_grade(self, wired: Fixtures) -> None:
        set_id, first = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        await proposals.create(set_id, first, _write())
        await wired.sets.set_outcome(set_id, first, VersionOutcomeWrite(outcome="held"))
        second = await wired.sets.add_version(
            set_id,
            SetVersionPatch.model_validate(
                {"grind_setting": "21", "prediction": "Expect about 32 s."}
            ),
        )
        assert second is not None
        await _keep_shot(wired, second.id, "000790")

        again = await proposals.create(set_id, second.id, _write("held"))
        assert again.refused is None
        assert await proposals.waiting_for_version(first) is not None
        assert await proposals.waiting_for_version(second.id) is not None

    async def test_a_version_with_no_prediction_has_nothing_to_grade(self, wired: Fixtures) -> None:
        row = await wired.sets.create(
            SetWrite(name="By hand", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
            SetVersionWrite(dose_g=18, target_yield_g=36),
        )
        current = await wired.sets.current_version(row.id)
        assert current is not None
        await _keep_shot(wired, current.id, "000711")

        result = await OutcomeProposalsRepository(wired.db).create(row.id, current.id, _write())
        assert result.refused == "no_prediction"

    async def test_a_version_with_no_counted_shot_has_nothing_to_grade(
        self, wired: Fixtures
    ) -> None:
        set_id, version_id = await _graded_ready(wired, shots=0)
        proposals = OutcomeProposalsRepository(wired.db)
        assert (await proposals.create(set_id, version_id, _write())).refused == "nothing_to_grade"

    async def test_a_discarded_shot_is_not_a_counted_one(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired, shots=0)
        shot_id = await make_shot(wired.db, "000722")
        await wired.sets.assign_shot(shot_id, version_id)
        await JudgementsRepository(wired.db).upsert(
            shot_id, JudgementWrite.model_validate({"decision": "discard"})
        )
        proposals = OutcomeProposalsRepository(wired.db)
        assert (await proposals.create(set_id, version_id, _write())).refused == "nothing_to_grade"

    async def test_a_version_of_another_set_is_refused(self, wired: Fixtures) -> None:
        set_id, _ = await _graded_ready(wired)
        _, other_version = await _graded_ready(wired, name="Other", base=800)
        proposals = OutcomeProposalsRepository(wired.db)
        assert (await proposals.create(set_id, other_version, _write())).refused == "no_version"

    async def test_a_thread_of_another_set_is_refused(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired)
        cursor = await wired.db.execute("INSERT INTO chat_threads (title) VALUES ('general')")
        spec = OutcomeProposalWrite.model_validate(
            {"outcome": "held", "note": NOTE, "thread_id": cursor.lastrowid}
        )
        result = await OutcomeProposalsRepository(wired.db).create(set_id, version_id, spec)
        assert result.refused == "bad_thread"

    def test_only_the_outcome_vocabulary_is_accepted(self) -> None:
        for bad in ("open", "no_prediction", "great"):
            with pytest.raises(ValidationError):
                _write(bad)
        with pytest.raises(ValidationError):
            _write(note="   ")


class TestAnswering:
    async def test_accept_records_the_grade_and_marks_the_proposal(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, _write("held"))).proposal
        assert stored is not None

        result = await proposals.accept(set_id, stored.id)

        assert result.refused is None
        assert result.proposal is not None and result.proposal.status == "accepted"
        assert result.version is not None
        assert (result.version.outcome, result.version.outcome_note) == ("held", NOTE)
        assert result.version.outcome_state == "held"

    async def test_accept_with_another_outcome_records_the_persons_choice(
        self, wired: Fixtures
    ) -> None:
        set_id, version_id = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, _write("held"))).proposal
        assert stored is not None

        result = await proposals.change(set_id, stored.id, "inconclusive")

        assert result.proposal is not None
        assert result.proposal.status == "changed"
        assert result.proposal.outcome == "held"  # what the agent wrote is kept
        assert result.proposal.recorded_outcome == "inconclusive"
        assert result.version is not None and result.version.outcome == "inconclusive"
        # The agent's per-claim lines stay with the grade unless the person wrote their own.
        assert result.version.outcome_note == NOTE

    async def test_a_changed_grade_may_carry_the_persons_own_note(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, _write("held"))).proposal
        assert stored is not None
        result = await proposals.change(set_id, stored.id, "failed", "Tasted worse than the log.")
        assert result.version is not None
        assert result.version.outcome_note == "Tasted worse than the log."

    async def test_dismiss_records_nothing_and_keeps_the_note(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, _write())).proposal
        assert stored is not None

        result = await proposals.dismiss(set_id, stored.id, "  one shot is not enough  ")

        assert result.proposal is not None
        assert (result.proposal.status, result.proposal.decision_note) == (
            "dismissed",
            "one shot is not enough",
        )
        version = await wired.sets.get_version(version_id)
        assert version is not None and version.outcome is None
        assert await proposals.waiting_for_version(version_id) is None
        last = await proposals.last_answered(version_id)
        assert last is not None and last.id == stored.id

    @pytest.mark.parametrize("answer", ["accept", "change", "dismiss"])
    async def test_an_answered_proposal_answers_409_to_every_further_press(
        self, wired: Fixtures, answer: str
    ) -> None:
        set_id, version_id = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, _write("held"))).proposal
        assert stored is not None
        await proposals.dismiss(set_id, stored.id)

        again = {
            "accept": await proposals.accept(set_id, stored.id),
            "change": await proposals.change(set_id, stored.id, "failed"),
            "dismiss": await proposals.dismiss(set_id, stored.id),
        }[answer]

        assert again.refused == "not_waiting"
        version = await wired.sets.get_version(version_id)
        assert version is not None and version.outcome is None

    async def test_a_superseded_proposal_cannot_be_accepted(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        first = (await proposals.create(set_id, version_id, _write("held"))).proposal
        assert first is not None
        await proposals.create(set_id, version_id, _write("failed"))

        assert (await proposals.accept(set_id, first.id)).refused == "not_waiting"

    async def test_an_unknown_proposal_or_another_sets_is_not_found(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired)
        _, other_version = await _graded_ready(wired, name="Other", base=800)
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, _write())).proposal
        assert stored is not None
        assert (await proposals.accept(set_id, 9999)).refused == "no_proposal"
        other_set = (await wired.sets.get_version(other_version)).set_id  # type: ignore[union-attr]
        assert (await proposals.accept(other_set, stored.id)).refused == "no_proposal"

    async def test_an_answer_that_cannot_be_recorded_is_one_transaction(
        self, wired: Fixtures
    ) -> None:
        """The shots went away since the card was written: refused, and still waiting."""
        set_id, version_id = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, _write("held"))).proposal
        assert stored is not None
        await wired.db.execute(
            "UPDATE shot_judgements SET decision = 'discard' WHERE 1 = 1",
        )

        result = await proposals.accept(set_id, stored.id)

        assert result.refused == "nothing_to_grade"
        still = await proposals.get(set_id, stored.id)
        assert still is not None and still.status == "proposed"
        version = await wired.sets.get_version(version_id)
        assert version is not None and version.outcome is None


class TestWhatReadsAGrade:
    async def test_a_recorded_outcome_survives_a_waiting_new_grade_until_accept(
        self, wired: Fixtures
    ) -> None:
        set_id, version_id = await _graded_ready(wired)
        await wired.sets.set_outcome(
            set_id, version_id, VersionOutcomeWrite(outcome="held", note="by hand")
        )
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, _write("failed"))).proposal
        assert stored is not None

        before = await wired.sets.get_version(version_id)
        assert before is not None and before.outcome == "held"
        # The card carries both, so the person sees the disagreement.
        assert (stored.version_outcome, stored.outcome) == ("held", "failed")

        await proposals.accept(set_id, stored.id)
        after = await wired.sets.get_version(version_id)
        assert after is not None and after.outcome == "failed"

    async def test_setting_the_outcome_on_the_page_leaves_a_waiting_grade_waiting(
        self, wired: Fixtures
    ) -> None:
        set_id, version_id = await _graded_ready(wired)
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, _write("failed"))).proposal
        assert stored is not None
        await wired.sets.set_outcome(set_id, version_id, VersionOutcomeWrite(outcome="held"))

        waiting = await proposals.waiting_for_version(version_id)
        assert waiting is not None and waiting.id == stored.id
        assert waiting.version_outcome == "held"

    async def test_deleting_the_conversation_leaves_the_proposal(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired)
        cursor = await wired.db.execute(
            "INSERT INTO chat_threads (title, set_id, set_version_id) VALUES ('v1', ?, ?)",
            (set_id, version_id),
        )
        spec = OutcomeProposalWrite.model_validate(
            {"outcome": "held", "note": NOTE, "thread_id": cursor.lastrowid}
        )
        proposals = OutcomeProposalsRepository(wired.db)
        stored = (await proposals.create(set_id, version_id, spec)).proposal
        assert stored is not None and stored.thread_id == cursor.lastrowid

        await wired.db.execute("DELETE FROM chat_threads WHERE id = ?", (cursor.lastrowid,))
        read = await proposals.get(set_id, stored.id)
        assert read is not None and read.thread_id is None


NEXT_PREDICTION = "Compared to v1: two to four seconds longer and less sour."


async def _next_version(wired: Fixtures, set_id: int):  # type: ignore[no-untyped-def]
    proposals = SetProposalsRepository(wired.db)
    return proposals, await proposals.create(
        set_id,
        ProposalWrite(
            patch=SetVersionPatch(grind_setting="21"),
            reason="one click finer, chasing the sourness out",
            prediction=NEXT_PREDICTION,
        ),
    )


class TestTheNextVersionRecordsTheGrade:
    """Rule 4: one click when the grade and the next version come together."""

    async def test_an_open_outcome_with_nothing_proposed_still_refuses(
        self, wired: Fixtures
    ) -> None:
        set_id, _ = await _graded_ready(wired)
        _, result = await _next_version(wired, set_id)
        assert result.refused == "outcome_open"

    async def test_a_waiting_grade_lets_the_next_version_be_proposed(self, wired: Fixtures) -> None:
        set_id, version_id = await _graded_ready(wired)
        await OutcomeProposalsRepository(wired.db).create(set_id, version_id, _write())
        _, result = await _next_version(wired, set_id)
        assert result.refused is None
        assert result.proposal is not None
        # Still words: proposing the version recorded nothing.
        version = await wired.sets.get_version(version_id)
        assert version is not None and version.outcome is None

    async def test_accepting_the_version_records_the_grade_and_appends_in_one_transaction(
        self, wired: Fixtures
    ) -> None:
        set_id, version_id = await _graded_ready(wired)
        grades = OutcomeProposalsRepository(wired.db)
        grade = (await grades.create(set_id, version_id, _write("held"))).proposal
        assert grade is not None
        proposals, made = await _next_version(wired, set_id)
        assert made.proposal is not None

        result = await proposals.accept(set_id, made.proposal.id)

        assert result.refused is None
        assert result.version is not None and result.version.version_label == "v1.1"
        assert result.graded is not None and result.graded.id == grade.id
        recorded = await wired.sets.get_version(version_id)
        assert recorded is not None
        assert (recorded.outcome, recorded.outcome_note) == ("held", NOTE)
        answered = await grades.get(set_id, grade.id)
        assert answered is not None and answered.status == "accepted"

    async def test_a_failure_after_the_grade_leaves_neither_written(
        self, wired: Fixtures, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_id, version_id = await _graded_ready(wired)
        grades = OutcomeProposalsRepository(wired.db)
        grade = (await grades.create(set_id, version_id, _write("held"))).proposal
        assert grade is not None
        proposals, made = await _next_version(wired, set_id)
        assert made.proposal is not None

        async def boom(*args: object, **kwargs: object) -> int:
            raise RuntimeError("append failed")

        monkeypatch.setattr(proposals.sets, "append_version", boom)
        with pytest.raises(RuntimeError):
            await proposals.accept(set_id, made.proposal.id)

        unchanged = await wired.sets.get_version(version_id)
        assert unchanged is not None and unchanged.outcome is None
        still = await grades.get(set_id, grade.id)
        assert still is not None and still.status == "proposed"
        assert len(await wired.sets.versions(set_id)) == 1

    async def test_a_dismissed_grade_leaves_the_version_proposal_refused_as_open(
        self, wired: Fixtures
    ) -> None:
        set_id, version_id = await _graded_ready(wired)
        grades = OutcomeProposalsRepository(wired.db)
        grade = (await grades.create(set_id, version_id, _write())).proposal
        assert grade is not None
        proposals, made = await _next_version(wired, set_id)
        assert made.proposal is not None
        await grades.dismiss(set_id, grade.id)

        result = await proposals.accept(set_id, made.proposal.id)

        assert result.refused == "outcome_open"
        assert len(await wired.sets.versions(set_id)) == 1
        still = await proposals.get(set_id, made.proposal.id)
        assert still is not None and still.status == "proposed"

    async def test_a_dismissed_grade_does_not_unlock_a_new_proposal_either(
        self, wired: Fixtures
    ) -> None:
        set_id, version_id = await _graded_ready(wired)
        grades = OutcomeProposalsRepository(wired.db)
        grade = (await grades.create(set_id, version_id, _write())).proposal
        assert grade is not None
        await grades.dismiss(set_id, grade.id)
        _, result = await _next_version(wired, set_id)
        assert result.refused == "outcome_open"

    async def test_a_grade_that_cannot_be_recorded_refuses_the_whole_accept(
        self, wired: Fixtures
    ) -> None:
        set_id, version_id = await _graded_ready(wired)
        grades = OutcomeProposalsRepository(wired.db)
        await grades.create(set_id, version_id, _write())
        proposals, made = await _next_version(wired, set_id)
        assert made.proposal is not None
        await wired.db.execute("UPDATE shot_judgements SET decision = 'discard' WHERE 1 = 1")

        result = await proposals.accept(set_id, made.proposal.id)

        assert result.refused == "grade_unrecordable"
        assert len(await wired.sets.versions(set_id)) == 1

    async def test_with_the_outcome_already_recorded_the_waiting_grade_is_still_recorded(
        self, wired: Fixtures
    ) -> None:
        """A newer grade replaces a recorded one only because the person accepted it."""
        set_id, version_id = await _graded_ready(wired)
        await wired.sets.set_outcome(
            set_id, version_id, VersionOutcomeWrite(outcome="held", note="by hand")
        )
        grades = OutcomeProposalsRepository(wired.db)
        await grades.create(set_id, version_id, _write("failed"))
        proposals, made = await _next_version(wired, set_id)
        assert made.proposal is not None

        result = await proposals.accept(set_id, made.proposal.id)

        assert result.refused is None
        recorded = await wired.sets.get_version(version_id)
        assert recorded is not None and recorded.outcome == "failed"

    async def test_a_grade_of_an_older_version_does_not_unlock_the_current_one(
        self, wired: Fixtures
    ) -> None:
        set_id, first = await _graded_ready(wired)
        await wired.sets.set_outcome(set_id, first, VersionOutcomeWrite(outcome="held"))
        second = await wired.sets.add_version(
            set_id,
            SetVersionPatch.model_validate(
                {"grind_setting": "21", "prediction": "Expect about 32 s."}
            ),
        )
        assert second is not None
        await _keep_shot(wired, second.id, "000790")
        grades = OutcomeProposalsRepository(wired.db)
        await grades.create(set_id, first, _write("failed"))  # for v1, not v1.1

        _, result = await _next_version(wired, set_id)
        assert result.refused == "outcome_open"
