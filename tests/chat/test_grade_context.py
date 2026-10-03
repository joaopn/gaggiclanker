"""What a Set conversation is told about the grade and the insights it proposed.

Two things pinned together. The positive: this version's conversation is told
that its grade is waiting (so it does not repeat itself), and what the person did
with it, and the same for the insights it proposed. The negative, which is the
rule: **nothing an agent graded reaches a later chat unless a person accepted
it** — a waiting or dismissed grade appears in no other version's context, no
ledger, no track record and no tool's output; those render byte-identical to the
same Set without the proposals.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gaggiclanker.chat.context import opening_context
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository, InsightWrite
from gaggiclanker.db.repos.outcome_proposals import (
    OutcomeProposalsRepository,
    OutcomeProposalWrite,
)
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, track_record
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.chat import test_context as _base
from tests.chat.test_context import Experiment

#: The experiment fixture of the opening-context tests, re-exported so it is one archive.
experiment = _base.experiment

GOLDEN = Path(__file__).resolve().parent / "golden" / "set-chat-context-grade.txt"
NOTE = "Time went up by 2.5 s against v2.1 (target 2 to 3 s); the finish is still drying."
GRADE_HEADINGS = ("A GRADE YOU PROPOSED IS WAITING FOR THE PERSON", "THE LAST ANSWER TO YOUR GRADE")
INSIGHT_HEADING = "INSIGHTS YOU PROPOSED IN THIS CONVERSATION"


def _grade(outcome: str = "partly_held", note: str = NOTE) -> OutcomeProposalWrite:
    return OutcomeProposalWrite.model_validate({"outcome": outcome, "note": note})


def _without_blocks(text: str) -> str:
    """The context with the proposed-grade and proposed-insight blocks taken out."""
    for heading in (*GRADE_HEADINGS, INSIGHT_HEADING):
        text = re.sub(rf"\n\n{re.escape(heading)}\n(?:.+\n?)+?(?=\n\n)", "", text)
    return text


async def _contexts(experiment: Experiment) -> dict[str, str]:
    ids = {
        "v1": experiment.v1,
        "v2": experiment.v2,
        "v3": experiment.v3,
        "v4": experiment.v4,
        "v5": experiment.v5,
    }
    return {
        name: await opening_context(experiment.db, ToolScope.for_thread(experiment.set_id, vid))
        for name, vid in ids.items()
    }


class TestThisVersionsConversation:
    async def test_a_waiting_grade_is_told_to_its_own_version_only(
        self, experiment: Experiment
    ) -> None:
        grades = OutcomeProposalsRepository(experiment.db)
        await grades.create(experiment.set_id, experiment.v5, _grade())

        text = await opening_context(
            experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
        )

        assert "A GRADE YOU PROPOSED IS WAITING FOR THE PERSON" in text
        assert "You proposed partly held, on 1 counted shot (" in text
        assert "They have not answered it yet." in text
        assert "v2.2's outcome is still open." in text
        assert "do not propose it again" in text

    async def test_more_shots_since_are_said(self, experiment: Experiment) -> None:
        from tests.sets.conftest import make_shot

        grades = OutcomeProposalsRepository(experiment.db)
        await grades.create(experiment.set_id, experiment.v5, _grade())
        sets = SetsRepository(experiment.db)
        extra = await make_shot(experiment.db, "000950", started_at="2026-04-06T08:00:00.000Z")
        assert await sets.assign_shot(extra, experiment.v5)
        from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite

        await JudgementsRepository(experiment.db).upsert(
            extra, JudgementWrite.model_validate({"decision": "keep"})
        )

        text = await opening_context(
            experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
        )
        assert "1 shot counted since you wrote it" in text

    async def test_a_recorded_outcome_is_named_beside_a_waiting_new_grade(
        self, experiment: Experiment
    ) -> None:
        grades = OutcomeProposalsRepository(experiment.db)
        await grades.create(experiment.set_id, experiment.v3, _grade("failed"))
        text = await opening_context(
            experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v3)
        )
        assert "v2's outcome is still inconclusive, as the person recorded it." in text

    async def test_the_waiting_version_proposal_is_said_to_record_the_grade(
        self, experiment: Experiment
    ) -> None:
        grades = OutcomeProposalsRepository(experiment.db)
        await grades.create(experiment.set_id, experiment.v5, _grade())
        text = await opening_context(
            experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
        )
        assert "accepting that version records this grade" in text

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            ("accept", "They accepted it: the outcome is recorded as partly held."),
            ("change", "They recorded failed instead"),
            ("dismiss", 'They dismissed it. They said: "one shot is not a result"'),
        ],
    )
    async def test_what_the_person_did_is_told_next_turn(
        self, experiment: Experiment, answer: str, expected: str
    ) -> None:
        grades = OutcomeProposalsRepository(experiment.db)
        stored = (await grades.create(experiment.set_id, experiment.v5, _grade())).proposal
        assert stored is not None
        if answer == "accept":
            await grades.accept(experiment.set_id, stored.id)
        elif answer == "change":
            await grades.change(experiment.set_id, stored.id, "failed")
        else:
            await grades.dismiss(experiment.set_id, stored.id, "one shot is not a result")

        text = await opening_context(
            experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
        )

        assert "THE LAST ANSWER TO YOUR GRADE" in text
        assert "A GRADE YOU PROPOSED IS WAITING" not in text
        assert expected in text

    async def test_a_superseded_grade_is_not_an_answer(self, experiment: Experiment) -> None:
        grades = OutcomeProposalsRepository(experiment.db)
        await grades.create(experiment.set_id, experiment.v5, _grade("held"))
        await grades.create(experiment.set_id, experiment.v5, _grade("failed"))
        text = await opening_context(
            experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
        )
        assert text.count("You proposed") == 1
        assert "You proposed failed" in text

    async def test_a_version_nobody_graded_reads_as_it_always_did(
        self, experiment: Experiment
    ) -> None:
        text = await opening_context(
            experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
        )
        assert not any(heading in text for heading in (*GRADE_HEADINGS, INSIGHT_HEADING))

    async def test_the_insights_it_proposed_are_told_in_every_state(
        self, experiment: Experiment
    ) -> None:
        insights = InsightsRepository(experiment.db)
        ids = {}
        for name in ("waiting", "added", "dismissed"):
            ids[name] = await insights.insert(
                InsightWrite(
                    text=f"The {name} lesson.",
                    source="chat",
                    set_id=experiment.set_id,
                    set_version_id=experiment.v5,
                )
            )
        await insights.set_confirmed(ids["added"], True)
        await insights.dismiss(ids["dismissed"])

        text = await opening_context(
            experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
        )

        block = text.split(INSIGHT_HEADING + "\n")[1].split("\n\n")[0]
        assert block.splitlines() == [
            "- The waiting lesson. (waiting: the person has not answered, so it is not "
            "evidence yet)",
            "- The added lesson. (added by the person — it is in the confirmed list above)",
            "- The dismissed lesson. (dismissed by the person — do not offer it again)",
        ]
        confirmed = text.split("CONFIRMED INSIGHTS THAT APPLY HERE\n")[1].split("\n\n")[0]
        assert "The added lesson." in confirmed
        assert "waiting lesson" not in confirmed and "dismissed lesson" not in confirmed


class TestNothingUnansweredReachesAnyoneElse:
    """Rule 3: byte-identical to the same Set without the proposals."""

    async def _seed(self, experiment: Experiment) -> None:
        grades = OutcomeProposalsRepository(experiment.db)
        # Waiting on the version in question, waiting on a graded older one, and a
        # dismissed one on another.
        await grades.create(experiment.set_id, experiment.v5, _grade("held"))
        await grades.create(experiment.set_id, experiment.v3, _grade("failed"))
        dismissed = (await grades.create(experiment.set_id, experiment.v2, _grade("held"))).proposal
        assert dismissed is not None
        await grades.dismiss(experiment.set_id, dismissed.id, "no")
        insights = InsightsRepository(experiment.db)
        waiting = await insights.insert(
            InsightWrite(
                text="A waiting lesson about v5.",
                source="chat",
                set_id=experiment.set_id,
                set_version_id=experiment.v5,
            )
        )
        gone = await insights.insert(
            InsightWrite(
                text="A dismissed lesson about v2.",
                source="chat",
                set_id=experiment.set_id,
                set_version_id=experiment.v2,
            )
        )
        await insights.dismiss(gone)
        assert waiting

    async def test_every_context_is_the_same_but_for_its_own_blocks(
        self, experiment: Experiment
    ) -> None:
        before = await _contexts(experiment)
        await self._seed(experiment)
        after = await _contexts(experiment)

        # A version with no proposals of its own is byte-identical.
        for name in ("v1", "v4"):
            assert after[name] == before[name], name
        # The others differ only by their own block, and by nothing else.
        for name in ("v2", "v3", "v5"):
            assert after[name] != before[name], name
            assert _without_blocks(after[name]) == before[name], name
        # And no block names another version's proposal.
        assert "A waiting lesson about v5." not in after["v2"] + after["v3"] + after["v4"]
        assert "A dismissed lesson about v2." not in after["v5"] + after["v3"]
        assert "You proposed held" not in after["v3"] and "You proposed failed" not in after["v5"]

    async def test_the_ledger_and_the_track_record_read_recorded_outcomes_only(
        self, experiment: Experiment
    ) -> None:
        sets = SetsRepository(experiment.db)
        before_versions = [
            v.model_dump(mode="json") for v in await sets.versions(experiment.set_id)
        ]
        before_record = track_record(await sets.versions(experiment.set_id)).model_dump(mode="json")
        await self._seed(experiment)

        after_versions = [v.model_dump(mode="json") for v in await sets.versions(experiment.set_id)]
        after_record = track_record(await sets.versions(experiment.set_id)).model_dump(mode="json")

        assert after_versions == before_versions
        assert after_record == before_record
        v5 = next(v for v in await sets.versions(experiment.set_id) if v.id == experiment.v5)
        assert v5.outcome is None and v5.outcome_state == "open"

    async def test_no_tool_output_carries_a_proposed_grade(self, experiment: Experiment) -> None:
        def context() -> ToolContext:
            return ToolContext(
                db=experiment.db,
                settings=SettingsService(SettingsRepository(experiment.db)),
                knowledge=KnowledgeService(experiment.db),
                scope=ToolScope.for_thread(experiment.set_id, experiment.v4),
                caller="test",
                permissions=CHAT_PERMISSIONS,
            )

        calls: tuple[tuple[str, dict[str, object]], ...] = (
            ("get_set", {}),
            ("get_insights", {}),
            ("list_set_shots", {}),
        )
        before = {
            name: (await registry.dispatch(context(), name, args)).data for name, args in calls
        }
        await self._seed(experiment)
        after = {
            name: (await registry.dispatch(context(), name, args)).data for name, args in calls
        }

        assert json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)

    async def test_a_proposed_version_does_not_leak_a_grade_into_another_versions_context(
        self, experiment: Experiment
    ) -> None:
        """The version proposal block is Set-wide, so it must not mention a waiting grade."""
        await OutcomeProposalsRepository(experiment.db).create(
            experiment.set_id, experiment.v5, _grade("held")
        )
        result = await SetProposalsRepository(experiment.db).create(
            experiment.set_id,
            ProposalWrite(
                patch=SetVersionPatch(grind_setting="20"),
                reason="one finer again",
                prediction="Compared to v2.2: two seconds longer and a sweeter finish.",
            ),
        )
        assert result.proposal is not None
        older = await opening_context(
            experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v4)
        )
        assert "A PROPOSAL IS WAITING FOR THE PERSON" in older
        assert (
            "grade" not in older.split("A PROPOSAL IS WAITING FOR THE PERSON")[1].split("\n\n")[0]
        )
        assert "You proposed" not in older


async def test_the_context_with_a_grade_and_insights_matches_the_golden_file(
    experiment: Experiment, update_golden: bool
) -> None:
    grades = OutcomeProposalsRepository(experiment.db)
    await grades.create(experiment.set_id, experiment.v5, _grade())
    insights = InsightsRepository(experiment.db)
    for name in ("waiting", "added", "dismissed"):
        stored = await insights.insert(
            InsightWrite(
                text=f"The {name} lesson, which rests on the one Improve shot.",
                source="chat",
                set_id=experiment.set_id,
                set_version_id=experiment.v5,
                evidence_shot_ids=[],
            )
        )
        if name == "added":
            await insights.set_confirmed(stored, True)
        if name == "dismissed":
            await insights.dismiss(stored)
    await SetProposalsRepository(experiment.db).create(
        experiment.set_id,
        ProposalWrite(
            patch=SetVersionPatch(grind_setting="20"),
            reason="one finer again",
            prediction="Compared to v2.2: two seconds longer and a sweeter finish.",
        ),
    )

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    if update_golden:
        GOLDEN.write_text(rendered, encoding="utf-8")
        pytest.skip("golden file rewritten")
    assert GOLDEN.exists(), "run with --update-golden to create it"
    assert rendered == GOLDEN.read_text(encoding="utf-8")
