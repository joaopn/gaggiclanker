"""A proposed change waits for the person, and only a person turns it into a version.

Every property here is one the loop depends on: a proposal changes nothing
until Accept, exactly one can be waiting at a time, the current prediction has
to have been graded before another change is proposed, and a proposal made
against a version the Set has since left is refused rather than applied to
whatever is current now.

Against the real file, because most of them are enforced by reading another
table inside the transaction that writes — and one of them, "one waiting
proposal per Set", is a partial unique index that no mock has.
"""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.set_proposals import (
    ProposalWrite,
    SetProposalsRepository,
    change_groups,
    recipe_patch,
)
from gaggiclanker.db.repos.sets import (
    RollbackWrite,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
    VersionOutcomeWrite,
    VersionPredictionWrite,
)
from tests.sets.conftest import Fixtures, make_profile_version, make_shot

PREDICTION = "Compared to v1: two to four seconds longer and less sour."


async def _set(wired: Fixtures) -> int:
    row = await wired.sets.create(
        SetWrite(name="Guji on the Niche", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        SetVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22"),
    )
    return row.id


async def _propose(
    wired: Fixtures, set_id: int, **over: object
) -> tuple[SetProposalsRepository, object]:
    proposals = SetProposalsRepository(wired.db)
    spec: dict[str, object] = {
        "patch": SetVersionPatch(grind_setting="21"),
        "reason": "one click finer, chasing the sourness out",
        "prediction": PREDICTION,
    }
    spec.update(over)
    return proposals, await proposals.create(set_id, ProposalWrite(**spec))  # type: ignore[arg-type]


class TestWhatOneChangeIs:
    def test_the_grind_text_and_its_number_are_one_change(self) -> None:
        groups = change_groups(SetVersionPatch(grind_setting="21", grind_value=21))
        assert groups == ["the grind"]

    def test_two_dials_are_two_changes(self) -> None:
        groups = change_groups(SetVersionPatch(grind_setting="21", dose_g=18.5))
        assert groups == ["the dose", "the grind"]

    def test_only_the_recipe_counts_as_a_change(self) -> None:
        """An intent and a prediction are not changes to the coffee."""
        patch = SetVersionPatch(intent="trying something", prediction=PREDICTION)
        assert change_groups(patch) == []
        assert recipe_patch(patch) == {}

    def test_clearing_a_field_is_a_change(self) -> None:
        """A null dose means "this Set no longer states one", which is a change."""
        patch = SetVersionPatch.model_validate({"dose_g": None})
        assert change_groups(patch) == ["the dose"]
        assert recipe_patch(patch) == {"dose_g": None}


class TestProposing:
    async def test_a_proposal_creates_no_version(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        proposals, result = await _propose(wired, set_id)
        assert result.refused is None  # type: ignore[attr-defined]
        assert len(await wired.sets.versions(set_id)) == 1

        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        assert waiting.status == "proposed"
        assert waiting.prediction == PREDICTION
        assert waiting.changed == ["the grind"]
        # Omitted means "against the version this is a change to".
        assert waiting.compares_to_version_no == 1
        assert waiting.base_is_current is True

    async def test_only_one_proposal_waits_at_a_time(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        await _propose(wired, set_id)
        _, second = await _propose(wired, set_id, patch=SetVersionPatch(dose_g=18.5))
        assert second.refused == "already_waiting"  # type: ignore[attr-defined]
        assert second.waiting is not None  # type: ignore[attr-defined]
        assert second.waiting.changed == ["the grind"]  # type: ignore[attr-defined]

    async def test_an_open_outcome_blocks_a_new_proposal(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        current = await wired.sets.current_version(set_id)
        assert current is not None
        await wired.sets.set_prediction(
            set_id,
            current.id,
            VersionPredictionWrite.model_validate(
                {"prediction": "Expect about 30 s.", "compares_to_version_id": None}
            ),
        )
        _, result = await _propose(wired, set_id)
        assert result.refused == "outcome_open"  # type: ignore[attr-defined]

    async def test_a_graded_outcome_does_not_block(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        current = await wired.sets.current_version(set_id)
        assert current is not None
        await wired.sets.set_prediction(
            set_id,
            current.id,
            VersionPredictionWrite.model_validate(
                {"prediction": "Expect about 30 s.", "compares_to_version_id": None}
            ),
        )
        shot_id = await make_shot(wired.db, "000401")
        await wired.sets.assign_shot(shot_id, current.id)
        await JudgementsRepository(wired.db).upsert(shot_id, JudgementWrite(decision="improve"))
        await wired.sets.set_outcome(set_id, current.id, VersionOutcomeWrite(outcome="failed"))

        _, result = await _propose(wired, set_id)
        assert result.refused is None  # type: ignore[attr-defined]

    async def test_a_version_with_no_prediction_never_blocks(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        _, result = await _propose(wired, set_id)
        assert result.refused is None  # type: ignore[attr-defined]

    async def test_a_comparison_outside_the_set_is_refused(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        other = await wired.sets.create(
            SetWrite(name="Another coffee", bean_id=wired.bean_id),
            SetVersionWrite(dose_g=18),
        )
        theirs = await wired.sets.current_version(other.id)
        assert theirs is not None
        _, result = await _propose(wired, set_id, compares_to_version_id=theirs.id)
        assert result.refused == "bad_compare"  # type: ignore[attr-defined]

    async def test_a_comparison_against_nothing_is_stored_as_nothing(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        proposals = SetProposalsRepository(wired.db)
        result = await proposals.create(
            set_id,
            ProposalWrite.model_validate(
                {
                    "patch": SetVersionPatch(dose_g=18.5),
                    "reason": "half a gram more",
                    "prediction": PREDICTION,
                    "compares_to_version_id": None,
                }
            ),
        )
        assert result.proposal is not None
        assert result.proposal.compares_to_version_id is None


class TestAccepting:
    async def test_accept_records_the_version_the_proposal_described(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None

        result = await proposals.accept(set_id, waiting.id)
        assert result.refused is None
        version = result.version
        assert version is not None
        assert version.version_no == 2
        assert version.origin == "chat"
        assert version.grind_setting == "21"
        # Everything not in the patch is inherited, as on any other version.
        assert version.dose_g == 18
        assert version.intent == "one click finer, chasing the sourness out"
        assert version.prediction == PREDICTION
        assert version.compares_to_version_no == 1

        assert result.proposal is not None
        assert result.proposal.status == "accepted"
        assert result.proposal.resulting_version_id == version.id
        assert result.proposal.resulting_version_no == 2
        assert await proposals.waiting(set_id) is None

    async def test_accept_keeps_a_comparison_against_nothing(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        proposals = SetProposalsRepository(wired.db)
        made = await proposals.create(
            set_id,
            ProposalWrite.model_validate(
                {
                    "patch": SetVersionPatch(dose_g=18.5),
                    "reason": "half a gram more",
                    "prediction": PREDICTION,
                    "compares_to_version_id": None,
                }
            ),
        )
        assert made.proposal is not None
        result = await proposals.accept(set_id, made.proposal.id)
        assert result.version is not None
        # Not the parent: the person accepted "graded on its own numbers".
        assert result.version.compares_to_version_id is None

    async def test_a_profile_change_is_recorded_and_nothing_is_sent(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        profile_id = await make_profile_version(wired.db, "Hotter [AI]", temperature=94)
        proposals, _ = await _propose(
            wired, set_id, patch=SetVersionPatch(profile_version_id=profile_id)
        )
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        preview = await proposals.preview(waiting)
        assert preview is not None
        assert preview.profile_label == "Hotter [AI]"
        assert preview.profile_temperature_c == 94

        result = await proposals.accept(set_id, waiting.id)
        assert result.version is not None
        assert result.version.profile_version_id == profile_id
        # A push is what puts a profile on the machine, and nothing here does.
        assert result.version.pushed_device_profile_id is None

    async def test_a_set_that_moved_on_leaves_nothing_to_accept(self, wired: Fixtures) -> None:
        """The Set overtook it, so it is already stale by the time anybody presses."""
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        await wired.sets.add_version(set_id, SetVersionPatch(dose_g=19, intent="by hand"))

        result = await proposals.accept(set_id, waiting.id)
        assert result.refused == "not_waiting"
        assert result.proposal is not None
        assert result.proposal.status == "stale"
        # No third version: the Set is where the hand-made change left it.
        assert len(await wired.sets.versions(set_id)) == 2

    async def test_accept_still_checks_the_base_itself(self, wired: Fixtures) -> None:
        """The defence behind the retirement, for the race the retirement cannot see.

        Both writes are transactional, so a proposal cannot in practice survive
        a version as `proposed` — this puts it back by hand to prove that Accept
        would still refuse rather than apply a change to a recipe nobody is
        brewing.
        """
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        await wired.sets.add_version(set_id, SetVersionPatch(dose_g=19, intent="by hand"))
        await wired.db.execute(
            "UPDATE set_version_proposals SET status = 'proposed' WHERE id = ?", (waiting.id,)
        )
        revived = await proposals.waiting(set_id)
        assert revived is not None
        assert revived.base_is_current is False

        result = await proposals.accept(set_id, waiting.id)
        assert result.refused == "stale"
        assert result.proposal is not None and result.proposal.status == "stale"
        assert len(await wired.sets.versions(set_id)) == 2

    async def test_an_outcome_opened_after_the_proposal_blocks_accept(
        self, wired: Fixtures
    ) -> None:
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        current = await wired.sets.current_version(set_id)
        assert current is not None
        await wired.sets.set_prediction(
            set_id,
            current.id,
            VersionPredictionWrite.model_validate(
                {"prediction": "Expect about 30 s.", "compares_to_version_id": None}
            ),
        )

        result = await proposals.accept(set_id, waiting.id)
        assert result.refused == "outcome_open"
        assert len(await wired.sets.versions(set_id)) == 1
        # Still waiting: the person grades, then accepts.
        assert (await proposals.waiting(set_id)) is not None

    async def test_accepting_twice_is_refused(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        await proposals.accept(set_id, waiting.id)
        again = await proposals.accept(set_id, waiting.id)
        assert again.refused == "not_waiting"
        assert len(await wired.sets.versions(set_id)) == 2


class TestDeclining:
    async def test_decline_records_the_note_and_creates_nothing(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None

        result = await proposals.decline(set_id, waiting.id, "tried that last week")
        assert result.proposal is not None
        assert result.proposal.status == "declined"
        assert result.proposal.decline_note == "tried that last week"
        assert len(await wired.sets.versions(set_id)) == 1
        assert await proposals.waiting(set_id) is None

    async def test_a_declined_proposal_frees_the_slot(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        await proposals.decline(set_id, waiting.id)
        _, second = await _propose(wired, set_id, patch=SetVersionPatch(dose_g=18.5))
        assert second.refused is None  # type: ignore[attr-defined]

    async def test_the_last_decided_proposal_is_readable(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        await proposals.decline(set_id, waiting.id, "not the grind")
        last = await proposals.last_decided(set_id)
        assert last is not None
        assert last.decline_note == "not the grind"


class TestTheChatItCameFrom:
    async def test_a_proposal_outlives_a_deleted_chat(self, wired: Fixtures) -> None:
        """Old conversations are disposable; the experiment log is not."""
        set_id = await _set(wired)
        current = await wired.sets.current_version(set_id)
        assert current is not None
        cursor = await wired.db.execute(
            "INSERT INTO chat_threads (title, set_id, set_version_id) VALUES (?, ?, ?)",
            ("About v1", set_id, current.id),
        )
        thread_id = int(cursor.lastrowid or 0)
        proposals, _ = await _propose(wired, set_id, thread_id=thread_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        assert waiting.thread_id == thread_id

        await wired.db.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))
        survived = await proposals.waiting(set_id)
        assert survived is not None
        assert survived.thread_id is None

    async def test_an_accepted_version_names_its_conversation(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        current = await wired.sets.current_version(set_id)
        assert current is not None
        cursor = await wired.db.execute(
            "INSERT INTO chat_threads (title, set_id, set_version_id) VALUES (?, ?, ?)",
            ("About v1", set_id, current.id),
        )
        thread_id = int(cursor.lastrowid or 0)
        proposals, _ = await _propose(wired, set_id, thread_id=thread_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        result = await proposals.accept(set_id, waiting.id)
        assert result.version is not None
        assert await proposals.accepted_threads(set_id) == {result.version.id: thread_id}


class TestWhatCreateAcceptsAcceptCanApply:
    """The person's press must never be what discovers a dangling reference.

    A proposal stored naming a row that does not exist passes every check
    :meth:`accept` makes and then fails on the insert — a 500, a rolled-back
    transaction, and a proposal still waiting that blocks every further one
    until somebody declines it. So every reference is checked where the
    proposal is made.
    """

    async def test_a_profile_version_that_does_not_exist_is_refused(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        _, result = await _propose(wired, set_id, patch=SetVersionPatch(profile_version_id=987_654))
        assert result.refused == "bad_profile"  # type: ignore[attr-defined]
        assert await SetProposalsRepository(wired.db).waiting(set_id) is None

    async def test_a_profile_version_that_exists_is_accepted(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        profile_id = await make_profile_version(wired.db, "Hotter [AI]", temperature=94)
        _, result = await _propose(
            wired, set_id, patch=SetVersionPatch(profile_version_id=profile_id)
        )
        assert result.refused is None  # type: ignore[attr-defined]

    async def test_clearing_the_profile_is_not_a_missing_profile(self, wired: Fixtures) -> None:
        """An explicit null means "this Set names no profile", which is real."""
        set_id = await _set(wired)
        proposals = SetProposalsRepository(wired.db)
        result = await proposals.create(
            set_id,
            ProposalWrite(
                patch=SetVersionPatch.model_validate({"profile_version_id": None}),
                reason="Stop pinning this Set to one profile.",
                prediction=PREDICTION,
            ),
        )
        assert result.refused is None
        assert result.proposal is not None
        accepted = await proposals.accept(set_id, result.proposal.id)
        assert accepted.version is not None
        assert accepted.version.profile_version_id is None

    async def test_every_field_of_the_patch_either_is_refused_or_accepts_cleanly(
        self, wired: Fixtures
    ) -> None:
        """The property, walked over every recipe field a proposal can carry.

        For each one, a value that does not resolve to anything: either
        `create` refuses it, or `accept` applies it without an integrity error.
        Nothing in between — a stored proposal that cannot be accepted is the
        state this whole class exists to rule out.
        """
        proposals = SetProposalsRepository(wired.db)
        candidates: list[dict[str, object]] = [
            {"profile_version_id": 987_654},
            {"profile_version_id": None},
            {"grind_setting": "not a number at all"},
            {"grind_value": 9999},
            {"dose_g": 99.5},
            {"target_yield_g": 499.5},
        ]
        for fields in candidates:
            set_id = await _set(wired)
            result = await proposals.create(
                set_id,
                ProposalWrite(
                    patch=SetVersionPatch.model_validate(fields),
                    reason="Walking the patch.",
                    prediction=PREDICTION,
                ),
            )
            if result.refused is not None:
                assert result.proposal is None, fields
                continue
            assert result.proposal is not None, fields
            accepted = await proposals.accept(set_id, result.proposal.id)
            assert accepted.refused is None, (fields, accepted.refused)
            assert accepted.version is not None, fields


class TestTheSetMovingOnRetiresWhatWasWaiting:
    """A proposal the Set has overtaken is not a question anybody can answer.

    Left `proposed` it would tell the next conversation the Set is somewhere it
    is not, and leave the person a button whose only possible answer is 409.
    """

    async def test_adding_a_version_by_hand_stales_it(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        await wired.sets.add_version(set_id, SetVersionPatch(dose_g=19, intent="by hand"))

        assert await proposals.waiting(set_id) is None
        last = await proposals.last_decided(set_id)
        assert last is not None and last.status == "stale"

    async def test_a_roll_back_stales_it(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        first = await wired.sets.current_version(set_id)
        assert first is not None
        await wired.sets.add_version(set_id, SetVersionPatch(dose_g=19, intent="by hand"))
        proposals, _ = await _propose(wired, set_id)

        await wired.sets.rollback(
            set_id, RollbackWrite(to_version_id=first.id, intent="back to the start")
        )

        assert await proposals.waiting(set_id) is None
        last = await proposals.last_decided(set_id)
        assert last is not None and last.status == "stale"

    async def test_accepting_does_not_stale_the_proposal_it_accepts(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None

        result = await proposals.accept(set_id, waiting.id)

        assert result.proposal is not None
        assert result.proposal.status == "accepted"

    async def test_another_set_s_proposal_is_left_alone(self, wired: Fixtures) -> None:
        mine = await _set(wired)
        theirs = await _set(wired)
        proposals, _ = await _propose(wired, theirs)
        await wired.sets.add_version(mine, SetVersionPatch(dose_g=19, intent="by hand"))

        assert await proposals.waiting(theirs) is not None

    async def test_a_fresh_proposal_is_made_and_accepted_after_a_staling(
        self, wired: Fixtures
    ) -> None:
        """The way out is proposing again, not declining what the Set overtook."""
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        await wired.sets.add_version(set_id, SetVersionPatch(dose_g=19, intent="by hand"))

        _, second = await _propose(wired, set_id, patch=SetVersionPatch(grind_setting="20"))
        assert second.refused is None  # type: ignore[attr-defined]
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        result = await proposals.accept(set_id, waiting.id)
        assert result.version is not None
        assert result.version.version_no == 3


class TestTheConversationAProposalNames:
    async def test_a_thread_of_another_set_is_refused(self, wired: Fixtures) -> None:
        mine = await _set(wired)
        theirs = await _set(wired)
        cursor = await wired.db.execute(
            "INSERT INTO chat_threads (title, set_id) VALUES (?, ?)", ("Elsewhere", theirs)
        )
        _, result = await _propose(wired, mine, thread_id=int(cursor.lastrowid or 0))
        assert result.refused == "bad_thread"  # type: ignore[attr-defined]

    async def test_a_thread_that_does_not_exist_is_refused(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        _, result = await _propose(wired, set_id, thread_id=987_654)
        assert result.refused == "bad_thread"  # type: ignore[attr-defined]

    async def test_a_general_conversation_is_not_one_of_this_set_s(self, wired: Fixtures) -> None:
        set_id = await _set(wired)
        cursor = await wired.db.execute("INSERT INTO chat_threads (title) VALUES (?)", ("General",))
        _, result = await _propose(wired, set_id, thread_id=int(cursor.lastrowid or 0))
        assert result.refused == "bad_thread"  # type: ignore[attr-defined]


class TestThePredictionIsNotOptional:
    async def test_an_empty_prediction_is_refused_by_the_model(self, wired: Fixtures) -> None:
        with pytest.raises(ValidationError):
            ProposalWrite(patch=SetVersionPatch(dose_g=18.5), reason="Because.", prediction="")

    async def test_a_prediction_of_spaces_is_an_empty_one(self, wired: Fixtures) -> None:
        with pytest.raises(ValidationError):
            ProposalWrite(patch=SetVersionPatch(dose_g=18.5), reason="Because.", prediction="    ")


class TestTheIndexIsTheRealGuard:
    async def test_a_second_waiting_row_is_refused_by_the_database_itself(
        self, wired: Fixtures
    ) -> None:
        """The repository's check is the sentence; the index is the guarantee.

        The stdio MCP server is a second process against the same file, so two
        conversations can reach `create` at once and only the index is there
        for the second of them.
        """
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None

        with pytest.raises(sqlite3.IntegrityError):
            await wired.db.execute(
                """
                INSERT INTO set_version_proposals
                    (set_id, base_version_id, patch_json, reason, prediction, status, created_at)
                VALUES (?, ?, '{"dose_g": 19}', 'second', 'p', 'proposed', '2026-04-01T00:00:00Z')
                """,
                (set_id, waiting.base_version_id),
            )

    async def test_a_decided_row_leaves_the_slot_free_for_the_index_too(
        self, wired: Fixtures
    ) -> None:
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        await proposals.decline(set_id, waiting.id)

        await wired.db.execute(
            """
            INSERT INTO set_version_proposals
                (set_id, base_version_id, patch_json, reason, prediction, status, created_at)
            VALUES (?, ?, '{"dose_g": 19}', 'second', 'p', 'proposed', '2026-04-01T00:00:00Z')
            """,
            (set_id, waiting.base_version_id),
        )
        assert await proposals.waiting(set_id) is not None


class TestAProposalNobodyCanRead:
    """A hand-edited row must not take the Set page down with it."""

    @staticmethod
    async def _damaged(wired: Fixtures, patch_json: str) -> tuple[SetProposalsRepository, int]:
        set_id = await _set(wired)
        proposals, _ = await _propose(wired, set_id)
        waiting = await proposals.waiting(set_id)
        assert waiting is not None
        await wired.db.execute(
            "UPDATE set_version_proposals SET patch_json = ? WHERE id = ?",
            (patch_json, waiting.id),
        )
        return proposals, set_id

    async def test_it_reads_back_as_unreadable_rather_than_raising(self, wired: Fixtures) -> None:
        for damaged in ('{"dose_g": "a lot"}', "not json at all", "[1, 2, 3]"):
            proposals, set_id = await self._damaged(wired, damaged)
            waiting = await proposals.waiting(set_id)
            assert waiting is not None, damaged
            assert waiting.readable is False, damaged
            assert waiting.changed == [], damaged
            assert await proposals.preview(waiting) is None, damaged

    async def test_accept_refuses_it_and_creates_nothing(self, wired: Fixtures) -> None:
        proposals, set_id = await self._damaged(wired, '{"dose_g": "a lot"}')
        waiting = await proposals.waiting(set_id)
        assert waiting is not None

        result = await proposals.accept(set_id, waiting.id)

        assert result.refused == "unreadable"
        assert len(await wired.sets.versions(set_id)) == 1

    async def test_decline_works_without_reading_the_patch(self, wired: Fixtures) -> None:
        proposals, set_id = await self._damaged(wired, "not json at all")
        waiting = await proposals.waiting(set_id)
        assert waiting is not None

        result = await proposals.decline(set_id, waiting.id, "whatever that was")

        assert result.proposal is not None
        assert result.proposal.status == "declined"
        assert await proposals.waiting(set_id) is None
