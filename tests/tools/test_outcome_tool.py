"""`propose_outcome`: the agent's grade of the conversation's own version, as a card.

Through the dispatcher, like the other tools, because that is what the chat and
the stdio server both use. What these pin: it grades the conversation's own
version and no other, it records nothing, every refusal has a sentence of its
own, and it exists in a Set's conversation only.
"""

from __future__ import annotations

from typing import Any

from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.outcome_proposals import OutcomeProposalsRepository
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionRow
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.review.conftest import Fixture
from tests.sets.conftest import make_shot
from tests.tools.conftest import _context

NOTE = "Time held at 31 s against 28 s; the sourness did not move; the yield held at 36 g."


async def call(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert outcome.ok, outcome.data
    return outcome.data


async def refuse(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert not outcome.ok
    return outcome.data


async def _predicted_version(archive: Fixture, *, shots: int = 1) -> SetVersionRow:
    """A version with a prediction and ``shots`` Keep shots filed under it."""
    sets = SetsRepository(archive.db)
    version = await sets.add_version(
        archive.set_id,
        SetVersionPatch.model_validate(
            {
                "intent": "one click finer",
                "grind_setting": "21",
                "prediction": "Expect two seconds longer and less sour.",
                "compares_to_version_id": None,
            }
        ),
    )
    assert version is not None
    for number in range(shots):
        shot_id = await make_shot(archive.db, f"0009{number:02d}")
        assert await sets.assign_shot(shot_id, version.id)
        await JudgementsRepository(archive.db).upsert(
            shot_id, JudgementWrite.model_validate({"decision": "keep"})
        )
    return version


def _in_version(
    archive: Fixture,
    settings: SettingsService,
    version: SetVersionRow,
    thread_id: int | None = None,
) -> ToolContext:
    ctx = _context(archive, settings, ToolScope.for_thread(archive.set_id, version.id))
    ctx.thread_id = thread_id
    return ctx


async def test_it_writes_a_waiting_card_and_records_nothing(
    archive: Fixture, settings: SettingsService
) -> None:
    version = await _predicted_version(archive, shots=2)
    ctx = _in_version(archive, settings, version)

    data = await call(ctx, "propose_outcome", outcome="partly_held", note=NOTE)

    assert (data["status"], data["outcome"], data["counted_shots"]) == (
        "proposed",
        "partly_held",
        2,
    )
    assert data["version"] == version.version_label
    assert "Nothing is recorded until the person accepts it" in data["note"]
    assert "2 counted shots" in data["note"]
    # Rule 1, at the tool: the version's outcome is untouched.
    read = await SetsRepository(archive.db).get_version(version.id)
    assert read is not None and read.outcome is None and read.outcome_state == "open"
    waiting = await OutcomeProposalsRepository(archive.db).waiting_for_version(version.id)
    assert waiting is not None and waiting.id == data["proposal_id"]


async def test_it_grades_the_conversations_own_version_and_no_other(
    archive: Fixture, settings: SettingsService
) -> None:
    """Rule 2. The version is the conversation's; there is no argument to name another."""
    other = await _predicted_version(archive)
    mine = await SetsRepository(archive.db).add_version(
        archive.set_id,
        SetVersionPatch.model_validate(
            {"grind_setting": "20", "prediction": "Expect a little more body."}
        ),
    )
    assert mine is not None
    ctx = _in_version(archive, settings, other)

    spec = registry.get("propose_outcome")
    assert spec is not None
    assert set(spec.input_model.model_json_schema()["properties"]) == {"outcome", "note"}

    data = await call(ctx, "propose_outcome", outcome="held", note=NOTE)
    assert data["version"] == other.version_label
    proposals = OutcomeProposalsRepository(archive.db)
    assert await proposals.waiting_for_version(other.id) is not None
    assert await proposals.waiting_for_version(mine.id) is None


async def test_a_version_argument_is_not_accepted(
    archive: Fixture, settings: SettingsService
) -> None:
    version = await _predicted_version(archive)
    ctx = _in_version(archive, settings, version)
    outcome = await registry.dispatch(
        ctx, "propose_outcome", {"outcome": "held", "note": NOTE, "set_version_id": 1}
    )
    assert not outcome.ok


async def test_a_scope_with_no_version_grades_the_current_one(
    archive: Fixture, settings: SettingsService
) -> None:
    version = await _predicted_version(archive)
    ctx = _context(archive, settings, ToolScope.for_thread(archive.set_id))
    data = await call(ctx, "propose_outcome", outcome="held", note=NOTE)
    assert data["version"] == version.version_label


async def test_a_second_grade_replaces_the_first_and_says_so(
    archive: Fixture, settings: SettingsService
) -> None:
    version = await _predicted_version(archive)
    ctx = _in_version(archive, settings, version)
    first = await call(ctx, "propose_outcome", outcome="held", note=NOTE)
    second = await call(ctx, "propose_outcome", outcome="inconclusive", note=NOTE)

    assert first["replaced_waiting_grade"] is False
    assert second["replaced_waiting_grade"] is True
    waiting = await OutcomeProposalsRepository(archive.db).waiting_for_version(version.id)
    assert waiting is not None and waiting.id == second["proposal_id"]


async def test_the_proposal_names_the_conversation_it_came_from(
    archive: Fixture, settings: SettingsService
) -> None:
    version = await _predicted_version(archive)
    cursor = await archive.db.execute(
        "INSERT INTO chat_threads (title, set_id, set_version_id) VALUES ('v', ?, ?)",
        (archive.set_id, version.id),
    )
    ctx = _in_version(archive, settings, version, int(cursor.lastrowid or 0))
    await call(ctx, "propose_outcome", outcome="held", note=NOTE)
    waiting = await OutcomeProposalsRepository(archive.db).waiting_for_version(version.id)
    assert waiting is not None and waiting.thread_id == cursor.lastrowid


class TestRefusals:
    """Each refusal is an error value with a sentence of its own."""

    async def test_no_prediction_means_nothing_to_grade(
        self, archive: Fixture, settings: SettingsService
    ) -> None:
        first = (await SetsRepository(archive.db).versions(archive.set_id))[0]
        ctx = _in_version(archive, settings, first)
        data = await refuse(ctx, "propose_outcome", outcome="held", note=NOTE)
        assert "has no prediction, so there is nothing to grade" in data["detail"]
        assert await OutcomeProposalsRepository(archive.db).for_set(archive.set_id) == []

    async def test_no_counted_shot_yet(self, archive: Fixture, settings: SettingsService) -> None:
        version = await _predicted_version(archive, shots=0)
        ctx = _in_version(archive, settings, version)
        data = await refuse(ctx, "propose_outcome", outcome="held", note=NOTE)
        assert "no shot the person has judged Keep or Improve yet" in data["detail"]

    async def test_an_outcome_outside_the_vocabulary(
        self, archive: Fixture, settings: SettingsService
    ) -> None:
        version = await _predicted_version(archive)
        ctx = _in_version(archive, settings, version)
        for bad in ("open", "no_prediction", "great"):
            data = await refuse(ctx, "propose_outcome", outcome=bad, note=NOTE)
            assert (
                "outcome must be one of held, partly_held, failed, inconclusive" in data["detail"]
            )

    async def test_a_note_under_twenty_characters(
        self, archive: Fixture, settings: SettingsService
    ) -> None:
        version = await _predicted_version(archive)
        ctx = _in_version(archive, settings, version)
        data = await refuse(ctx, "propose_outcome", outcome="held", note="it held")
        assert "A grade needs its reasons" in data["detail"]
        assert "at least 20 characters" in data["detail"]
        assert await OutcomeProposalsRepository(archive.db).for_set(archive.set_id) == []

    async def test_a_conversation_of_no_set_of_this_one(
        self, archive: Fixture, settings: SettingsService
    ) -> None:
        version = await _predicted_version(archive)
        cursor = await archive.db.execute("INSERT INTO chat_threads (title) VALUES ('general')")
        ctx = _in_version(archive, settings, version, int(cursor.lastrowid or 0))
        data = await refuse(ctx, "propose_outcome", outcome="held", note=NOTE)
        assert "not one of this Set's" in data["detail"]


class TestWhereItExists:
    async def test_it_is_absent_from_a_general_conversation(
        self, archive: Fixture, settings: SettingsService
    ) -> None:
        ctx = _context(archive, settings, ToolScope())
        outcome = await registry.dispatch(ctx, "propose_outcome", {"outcome": "held", "note": NOTE})
        assert outcome.status == "refused"
        assert "not available in a general conversation" in outcome.error

    async def test_it_is_absent_from_a_design_conversation(
        self, archive: Fixture, settings: SettingsService
    ) -> None:
        ctx = _context(archive, settings, ToolScope.for_thread(archive.set_id, designing=True))
        outcome = await registry.dispatch(ctx, "propose_outcome", {"outcome": "held", "note": NOTE})
        assert outcome.status == "refused"
        assert "designing a Set" in outcome.error

    async def test_it_is_in_the_set_scope_and_nowhere_else(self) -> None:
        from gaggiclanker.tools.scope import DESIGN_TOOLS, GENERAL_TOOLS, SET_TOOLS

        assert "propose_outcome" in SET_TOOLS
        assert "propose_outcome" not in GENERAL_TOOLS | DESIGN_TOOLS


class TestTheNextChangeAfterAGrade:
    """The tool-side twins of the repository rule: the same sentence, the same unlock."""

    async def test_propose_set_version_is_refused_with_the_new_way_out(
        self, archive: Fixture, settings: SettingsService
    ) -> None:
        version = await _predicted_version(archive)
        ctx = _in_version(archive, settings, version)
        data = await refuse(
            ctx,
            "propose_set_version",
            reason="Two clicks finer.",
            grind_setting="20",
            prediction="Compared to the last one: two to four seconds longer and less sour.",
        )
        assert "Propose its outcome first (propose_outcome)" in data["detail"]
        assert "Set page" not in data["detail"]

    async def test_a_waiting_grade_unlocks_the_proposal_and_the_draft(
        self, archive: Fixture, settings: SettingsService
    ) -> None:
        version = await _predicted_version(archive)
        ctx = _in_version(archive, settings, version)
        await call(ctx, "propose_outcome", outcome="partly_held", note=NOTE)

        proposed = await call(
            ctx,
            "propose_set_version",
            reason="Two clicks finer.",
            grind_setting="20",
            prediction="Compared to the last one: two to four seconds longer and less sour.",
        )
        assert proposed["status"] == "proposed"
        # And nothing was recorded by proposing it.
        read = await SetsRepository(archive.db).get_version(version.id)
        assert read is not None and read.outcome is None
        waiting = await SetProposalsRepository(archive.db).waiting(archive.set_id)
        assert waiting is not None

    async def test_the_draft_rule_is_the_same_rule(
        self, archive: Fixture, settings: SettingsService
    ) -> None:
        """`draft_profile` is blocked by an open outcome exactly as the proposal is."""
        from gaggiclanker.drafts.proposals import DraftProposals

        version = await _predicted_version(archive)
        ctx = _in_version(archive, settings, version)
        ctx.drafts = DraftProposals(archive.db, settings)
        arguments = {
            "base_version_id": archive.profile_version_id,
            "patch": {"temperature": 92},
            "reason": "A degree cooler.",
            "prediction": "Compared to the last one: less of the dry finish, and no slower.",
        }
        blocked = await refuse(ctx, "draft_profile", **arguments)
        assert "Propose its outcome first (propose_outcome)" in blocked["detail"]

        await call(ctx, "propose_outcome", outcome="held", note=NOTE)
        drafted = await call(ctx, "draft_profile", **arguments)
        assert drafted["status"] == "draft"


async def test_proposing_the_next_version_still_needs_the_repository_to_agree(
    archive: Fixture,
) -> None:
    """The repository and the tools state one rule: a dismissed grade unlocks nothing."""
    version = await _predicted_version(archive)
    grades = OutcomeProposalsRepository(archive.db)
    from gaggiclanker.db.repos.outcome_proposals import OutcomeProposalWrite

    stored = (
        await grades.create(
            archive.set_id,
            version.id,
            OutcomeProposalWrite.model_validate({"outcome": "held", "note": NOTE}),
        )
    ).proposal
    assert stored is not None
    await grades.dismiss(archive.set_id, stored.id)
    result = await SetProposalsRepository(archive.db).create(
        archive.set_id,
        ProposalWrite(
            patch=SetVersionPatch(grind_setting="20"),
            reason="finer",
            prediction="Compared to the last one: two to four seconds longer.",
        ),
    )
    assert result.refused == "outcome_open"


def test_it_is_a_propose_tool() -> None:
    spec = registry.get("propose_outcome")
    assert spec is not None and spec.permission == "propose"
