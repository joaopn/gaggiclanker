"""What a Set's insight rests on, and what happens when one replaces another.

The rules pinned here, against the real file because most of them are a read or a
write inside one transaction:

* an insight rests on shots and on versions of **its own Set** that have a recorded
  outcome at the moment of writing; the outcome it had then never changes, the
  outcome now is read from the version every time;
* an insight proposed as a replacement names an **added** insight of the same Set,
  and the person's Add deletes the old one in the same transaction (or, when the old
  one has changed in the meantime, adds the new one as an ordinary insight and says so);
* every delete path leaves nothing behind and nothing waiting on it;
* a dismissed insight goes with the conversation that proposed it.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.db.repos.chat import ChatRepository
from gaggiclanker.db.repos.insight_deletions import InsightDeletionWrite
from gaggiclanker.db.repos.knowledge_insights import InsightWrite
from tests.knowledge.insight_world import ProvenanceWorld, rows_containing

REASON = "The two newest shots contradict it on every measure."


async def _propose(
    world: ProvenanceWorld,
    text: str = "A finer grind lengthens the shot.",
    *,
    versions: list[int] | None = None,
    shots: list[int] | None = None,
    replaces: int | None = None,
    thread: int | None = None,
) -> int:
    result = await world.insights.propose(
        InsightWrite(
            text=text,
            source="chat",
            set_id=world.set_id,
            set_version_id=world.v3,
            thread_id=world.thread if thread is None else thread,
            evidence_shot_ids=shots or [],
            replaces_id=replaces,
        ),
        version_ids=versions or [],
    )
    assert result.refused is None, result.refused
    assert result.insight_id is not None
    return result.insight_id


class TestWhatItRestsOn:
    async def test_the_outcome_then_is_stored_and_the_outcome_now_is_read(
        self, provenance: ProvenanceWorld
    ) -> None:
        insight_id = await _propose(provenance, versions=[provenance.v1, provenance.v2])

        row = await provenance.insights.get(insight_id)
        assert row is not None
        assert [
            (item.label, item.outcome_then, item.outcome_now, item.changed) for item in row.rests_on
        ] == [
            ("v1", "held", "held", False),
            ("v1.1", "held", "held", False),
        ]

    async def test_a_regrade_moves_now_and_never_then(self, provenance: ProvenanceWorld) -> None:
        insight_id = await _propose(provenance, versions=[provenance.v2])

        await provenance.regrade(provenance.v2, "failed")
        row = await provenance.insights.get(insight_id)
        assert row is not None
        (item,) = row.rests_on
        assert (item.outcome_then, item.outcome_now, item.changed) == ("held", "failed", True)
        assert "v1.1 held → now failed" in row.render()

        # Re-recorded back to what it was: the mark goes, "then" was never touched.
        await provenance.regrade(provenance.v2, "held")
        row = await provenance.insights.get(insight_id)
        assert row is not None
        assert row.rests_on[0].outcome_then == "held" and not row.rests_on[0].changed

    async def test_a_cleared_outcome_reads_no_outcome_now(
        self, provenance: ProvenanceWorld
    ) -> None:
        insight_id = await _propose(provenance, versions=[provenance.v1])
        await provenance.sets.clear_outcome(provenance.set_id, provenance.v1)

        row = await provenance.insights.get(insight_id)
        assert row is not None
        (item,) = row.rests_on
        assert item.outcome_then == "held" and item.outcome_now is None
        assert "v1 held → no outcome now" in row.render()
        stored = await provenance.db.fetch_value(
            "SELECT rests_on_json FROM knowledge_insights WHERE id = ?", (insight_id,)
        )
        assert '"held"' in stored and "null" not in stored

    async def test_the_line_names_its_id_its_age_its_versions_and_its_shots(
        self, provenance: ProvenanceWorld
    ) -> None:
        insight_id = await _propose(
            provenance,
            versions=[provenance.v1, provenance.v2],
            shots=[provenance.shot2, provenance.shot1],
        )
        await provenance.regrade(provenance.v2, "failed")
        await provenance.insights.set_confirmed(insight_id, True)

        row = await provenance.insights.get(insight_id)
        assert row is not None
        assert row.render() == (
            f"#{insight_id} [this Set, learned at v1.2; rests on v1 held, v1.1 held → now failed; "
            f"shots {provenance.shot1}, {provenance.shot2}] A finer grind lengthens the shot."
        )

    async def test_a_cut_text_never_cuts_the_facts(self, provenance: ProvenanceWorld) -> None:
        insight_id = await _propose(provenance, "word " * 100, versions=[provenance.v1])
        row = await provenance.insights.get(insight_id)
        assert row is not None
        line = row.render(text_chars=40)
        assert line.startswith(f"#{insight_id} [this Set, learned at v1.2; rests on v1 held] ")
        assert line.endswith("…")

    async def test_a_list_of_insights_is_one_query_for_what_they_rest_on(
        self, provenance: ProvenanceWorld
    ) -> None:
        for number in range(5):
            await _propose(provenance, f"Insight {number}.", versions=[provenance.v1])
        statements: list[str] = []
        original = provenance.db.fetch_all

        async def spy(sql: str, params: object = ()) -> object:
            statements.append(sql)
            return await original(sql, params)  # type: ignore[arg-type]

        provenance.db.fetch_all = spy  # type: ignore[method-assign,assignment]
        try:
            rows = await provenance.insights.own(provenance.set_id, include_dismissed=True)
        finally:
            provenance.db.fetch_all = original  # type: ignore[method-assign]
        assert len(rows) == 5 and all(row.rests_on for row in rows)
        assert len(statements) == 1


class TestWhatItMayRestOn:
    async def test_it_needs_a_shot_or_a_version(self, provenance: ProvenanceWorld) -> None:
        result = await provenance.insights.propose(
            InsightWrite(text="A bare claim.", source="chat", set_id=provenance.set_id),
            version_ids=[],
        )
        assert result.refused == "nothing_to_rest_on"
        assert await provenance.insights.own(provenance.set_id, include_dismissed=True) == []

    async def test_a_shot_alone_is_enough(self, provenance: ProvenanceWorld) -> None:
        assert await _propose(provenance, shots=[provenance.shot1])

    async def test_another_sets_version_is_refused(self, provenance: ProvenanceWorld) -> None:
        result = await provenance.insights.propose(
            InsightWrite(text="x", source="chat", set_id=provenance.set_id),
            version_ids=[provenance.stranger_version],
        )
        assert (result.refused, result.subject) == ("bad_version", provenance.stranger_version)

    async def test_a_version_without_an_outcome_is_refused(
        self, provenance: ProvenanceWorld
    ) -> None:
        result = await provenance.insights.propose(
            InsightWrite(text="x", source="chat", set_id=provenance.set_id),
            version_ids=[provenance.v3],
        )
        assert (result.refused, result.subject) == ("no_outcome", provenance.v3)

    async def test_a_cleared_outcome_is_refused_at_the_moment_of_writing(
        self, provenance: ProvenanceWorld
    ) -> None:
        await provenance.sets.clear_outcome(provenance.set_id, provenance.v1)
        result = await provenance.insights.propose(
            InsightWrite(text="x", source="chat", set_id=provenance.set_id),
            version_ids=[provenance.v1],
        )
        assert result.refused == "no_outcome"

    async def test_a_general_insight_cannot_rest_on_versions(
        self, provenance: ProvenanceWorld
    ) -> None:
        from pydantic import ValidationError

        from gaggiclanker.db.repos.knowledge_insights import RestsOn

        with pytest.raises(ValidationError):
            InsightWrite(text="x", rests_on=[RestsOn(set_version_id=1, outcome="held")])


class TestReplacing:
    async def test_it_names_an_added_insight_of_the_same_set(
        self, provenance: ProvenanceWorld
    ) -> None:
        old = await provenance.own("Old claim.")
        new = await _propose(provenance, versions=[provenance.v1], replaces=old)

        row = await provenance.insights.get(new)
        assert row is not None
        assert (row.replaces_id, row.replaces_text) == (old, "Old claim.")
        assert row.confirmed is False and row.replaced is None
        # Proposing changed nothing about the old one.
        assert await provenance.insights.get(old) is not None

    @pytest.mark.parametrize(
        ("case", "refusal"),
        [
            ("waiting", "replaces_not_added"),
            ("dismissed", "replaces_not_added"),
            ("general", "replaces_general"),
            ("other_set", "bad_replaces"),
            ("missing", "bad_replaces"),
        ],
    )
    async def test_it_refuses_what_may_not_be_replaced(
        self, provenance: ProvenanceWorld, case: str, refusal: str
    ) -> None:
        if case == "waiting":
            target = await provenance.own("Waiting.", confirmed=False)
        elif case == "dismissed":
            target = await provenance.own("Dismissed.", confirmed=False)
            await provenance.insights.dismiss(target)
        elif case == "general":
            target = await provenance.insights.insert(
                InsightWrite(text="General.", source="user", confirmed=True)
            )
        elif case == "other_set":
            target = await provenance.own(
                "Theirs.", set_id=provenance.stranger_set, version_id=provenance.stranger_version
            )
        else:
            target = 9999
        result = await provenance.insights.propose(
            InsightWrite(
                text="x",
                source="chat",
                set_id=provenance.set_id,
                evidence_shot_ids=[provenance.shot1],
                replaces_id=target,
            ),
            version_ids=[],
        )
        assert (result.refused, result.subject) == (refusal, target)

    async def test_adding_it_deletes_the_old_one_in_one_step(
        self, provenance: ProvenanceWorld
    ) -> None:
        old = await provenance.own("Old claim.")
        new = await _propose(provenance, versions=[provenance.v1], replaces=old)

        assert await provenance.insights.set_confirmed(new, True)

        assert await provenance.insights.get(old) is None
        row = await provenance.insights.get(new)
        assert row is not None
        assert row.confirmed and row.replaced == "deleted"
        # Nothing of the old one is kept: no link, no copy of its text.
        assert (row.replaces_id, row.replaces_text) == (None, "")
        assert not await provenance.db.fetch_value(
            "SELECT COUNT(*) FROM knowledge_insights WHERE text = 'Old claim.'"
        )

    async def test_an_old_one_already_gone_leaves_an_ordinary_insight_and_says_so(
        self, provenance: ProvenanceWorld
    ) -> None:
        old = await provenance.own("Old claim.")
        new = await _propose(provenance, versions=[provenance.v1], replaces=old)
        assert await provenance.insights.delete(old)

        waiting = await provenance.insights.get(new)
        assert waiting is not None
        assert (waiting.replaces_id, waiting.replaces_text, waiting.replaced) == (
            None,
            "",
            "old_changed",
        )
        assert await provenance.insights.set_confirmed(new, True)
        row = await provenance.insights.get(new)
        assert row is not None and row.confirmed and row.replaced == "old_changed"

    async def test_an_old_one_taken_back_in_the_window_is_not_deleted(
        self, provenance: ProvenanceWorld
    ) -> None:
        old = await provenance.own("Old claim.")
        new = await _propose(provenance, versions=[provenance.v1], replaces=old)
        await provenance.insights.set_confirmed(old, False)

        assert await provenance.insights.set_confirmed(new, True)

        survivor = await provenance.insights.get(old)
        assert survivor is not None and survivor.confirmed is False
        row = await provenance.insights.get(new)
        assert row is not None and row.confirmed and row.replaced == "old_changed"

    async def test_the_old_one_changing_inside_the_transaction_is_read_there(
        self, provenance: ProvenanceWorld
    ) -> None:
        """The check and the delete are one transaction: nothing can slip between them."""
        old = await provenance.own("Old claim.")
        new = await _propose(provenance, versions=[provenance.v1], replaces=old)
        seen: list[bool] = []
        original = provenance.insights.delete_in_transaction

        async def spy(insight_id: int, **kw: Any) -> bool:
            try:
                async with provenance.db.transaction():
                    pass
            except RuntimeError:  # nested: we are inside the caller's transaction
                seen.append(True)
            else:  # pragma: no cover - would mean the delete ran outside one
                seen.append(False)
            return await original(insight_id, **kw)

        provenance.insights.delete_in_transaction = spy  # type: ignore[method-assign]
        await provenance.insights.set_confirmed(new, True)
        assert seen == [True]

    async def test_a_failure_part_way_leaves_both_insights_as_they_were(
        self, provenance: ProvenanceWorld
    ) -> None:
        old = await provenance.own("Old claim.")
        new = await _propose(provenance, versions=[provenance.v1], replaces=old)

        async def boom(insight_id: int, **_: object) -> bool:
            raise RuntimeError("disk full")

        provenance.insights.delete_in_transaction = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await provenance.insights.set_confirmed(new, True)

        row = await provenance.insights.get(new)
        assert row is not None and not row.confirmed and row.replaces_id == old
        assert await provenance.insights.get(old) is not None

    async def test_taking_it_back_puts_only_the_new_one_back_to_waiting(
        self, provenance: ProvenanceWorld
    ) -> None:
        old = await provenance.own("Old claim.")
        new = await _propose(provenance, versions=[provenance.v1], replaces=old)
        await provenance.insights.set_confirmed(new, True)

        await provenance.insights.set_confirmed(new, False)
        row = await provenance.insights.get(new)
        assert row is not None and not row.confirmed
        assert await provenance.insights.get(old) is None
        # Adding it again deletes nothing a second time and still says how it ended.
        await provenance.insights.set_confirmed(new, True)
        again = await provenance.insights.get(new)
        assert again is not None and again.confirmed and again.replaced == "deleted"


class TestRemovedMeansRemoved:
    async def _waiting(self, world: ProvenanceWorld, target: int) -> tuple[int, int]:
        """A waiting deletion proposal and a waiting replacement, both naming ``target``."""
        proposal = (
            await world.deletions.propose(
                world.set_id,
                InsightDeletionWrite(thread_id=world.thread, insight_id=target, reason=REASON),
            )
        ).proposal
        assert proposal is not None
        replacement = await _propose(world, "A newer claim.", versions=[world.v1], replaces=target)
        return proposal.id, replacement

    async def _assert_gone(
        self, world: ProvenanceWorld, target: int, proposal_id: int, replacement: int
    ) -> None:
        assert await world.insights.get(target) is None
        assert (
            await world.db.fetch_value(
                "SELECT COUNT(*) FROM knowledge_insights WHERE id = ?", (target,)
            )
            == 0
        )
        # Nothing waits on it.
        stored = await world.deletions.get(world.set_id, proposal_id)
        assert stored is not None and stored.status in {"stale", "deleted"}
        assert stored.insight_id is None
        row = await world.insights.get(replacement)
        assert row is not None
        assert (row.replaces_id, row.replaces_text, row.replaced) == (None, "", "old_changed")

    async def test_the_persons_delete(self, provenance: ProvenanceWorld) -> None:
        target = await provenance.own("Doomed claim.")
        proposal_id, replacement = await self._waiting(provenance, target)

        assert await provenance.insights.delete(target)

        await self._assert_gone(provenance, target, proposal_id, replacement)
        stored = await provenance.deletions.get(provenance.set_id, proposal_id)
        assert stored is not None and stored.status == "stale"

    async def test_an_accepted_deletion_proposal(self, provenance: ProvenanceWorld) -> None:
        target = await provenance.own("Doomed claim.")
        proposal_id, replacement = await self._waiting(provenance, target)

        result = await provenance.deletions.accept(provenance.set_id, proposal_id)

        assert result.refused is None
        await self._assert_gone(provenance, target, proposal_id, replacement)
        stored = await provenance.deletions.get(provenance.set_id, proposal_id)
        assert stored is not None and stored.status == "deleted"

    async def test_a_replacements_add(self, provenance: ProvenanceWorld) -> None:
        target = await provenance.own("Doomed claim.")
        proposal_id, replacement = await self._waiting(provenance, target)
        # A second replacement naming the same one: the first Add takes the old one.
        other = await _propose(
            provenance, "Another newer claim.", versions=[provenance.v2], replaces=target
        )

        await provenance.insights.set_confirmed(replacement, True)

        stored = await provenance.deletions.get(provenance.set_id, proposal_id)
        assert stored is not None and stored.status == "stale" and stored.insight_id is None
        assert await provenance.insights.get(target) is None
        added = await provenance.insights.get(replacement)
        assert added is not None and added.replaced == "deleted"
        left = await provenance.insights.get(other)
        assert left is not None and left.replaced == "old_changed" and left.replaces_text == ""

    async def test_deleting_a_conversation_deletes_its_dismissed_insights_only(
        self, provenance: ProvenanceWorld
    ) -> None:
        dismissed = await provenance.own(
            "Turned down.", confirmed=False, thread_id=provenance.thread
        )
        await provenance.insights.dismiss(dismissed)
        waiting = await provenance.own(
            "Still waiting.", confirmed=False, thread_id=provenance.thread
        )
        added = await provenance.own("Added.", thread_id=provenance.thread)
        elsewhere = await provenance.own(
            "Other talk.", confirmed=False, thread_id=provenance.other_thread
        )
        await provenance.insights.dismiss(elsewhere)

        assert await ChatRepository(provenance.db).delete_thread(provenance.thread)

        assert await provenance.insights.get(dismissed) is None
        for survivor in (waiting, added, elsewhere):
            assert await provenance.insights.get(survivor) is not None
        kept = await provenance.insights.get(waiting)
        assert kept is not None and kept.thread_id is None

    async def test_a_failed_thread_delete_keeps_the_dismissed_insight(
        self, provenance: ProvenanceWorld
    ) -> None:
        dismissed = await provenance.own(
            "Turned down.", confirmed=False, thread_id=provenance.thread
        )
        await provenance.insights.dismiss(dismissed)
        chat = ChatRepository(provenance.db)
        assert not await chat.delete_thread(999_999)
        assert await provenance.insights.get(dismissed) is not None


class TestOnlyTheDecidingProposalKeepsTheText:
    """After any delete the removed words are in one row of the whole file, or none."""

    WORDS = "a phrase nothing else says: quokka-7731"

    async def _proposals(self, world: ProvenanceWorld, target: int) -> dict[str, int]:
        """A waiting, a kept and a superseded proposal for ``target`` (and one waiting last)."""
        made: dict[str, int] = {}
        first = (
            await world.deletions.propose(
                world.set_id,
                InsightDeletionWrite(
                    thread_id=world.thread, insight_id=target, reason=REASON + " one"
                ),
            )
        ).proposal
        assert first is not None
        await world.deletions.keep(world.set_id, first.id)
        made["kept"] = first.id
        second = (
            await world.deletions.propose(
                world.set_id,
                InsightDeletionWrite(
                    thread_id=world.thread, insight_id=target, reason=REASON + " two"
                ),
            )
        ).proposal
        assert second is not None
        made["superseded"] = second.id
        last = (
            await world.deletions.propose(
                world.set_id,
                InsightDeletionWrite(
                    thread_id=world.thread, insight_id=target, reason=REASON + " three"
                ),
            )
        ).proposal
        assert last is not None
        made["waiting"] = last.id
        return made

    async def test_a_direct_delete_leaves_the_words_nowhere(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own(self.WORDS)
        made = await self._proposals(provenance, target)
        assert await rows_containing(provenance.db, "quokka-7731")

        await provenance.insights.delete(target)

        assert await rows_containing(provenance.db, "quokka-7731") == []
        expected = {"kept": "stale", "waiting": "stale", "superseded": "superseded"}
        for name, proposal_id in made.items():
            row = await provenance.deletions.get(provenance.set_id, proposal_id)
            assert row is not None and row.insight_text == "" and row.status == expected[name]

    async def test_an_accepted_deletion_leaves_them_in_that_one_proposal(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own(self.WORDS)
        made = await self._proposals(provenance, target)

        await provenance.deletions.accept(provenance.set_id, made["waiting"])

        assert await rows_containing(provenance.db, "quokka-7731") == [
            f"set_insight_deletions#{made['waiting']}"
        ]
        deciding = await provenance.deletions.get(provenance.set_id, made["waiting"])
        assert deciding is not None and deciding.status == "deleted"
        for name, status in (("kept", "stale"), ("superseded", "superseded")):
            other = await provenance.deletions.get(provenance.set_id, made[name])
            assert other is not None and other.status == status and other.insight_text == ""

    async def test_a_replacements_add_leaves_them_nowhere(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own(self.WORDS)
        await self._proposals(provenance, target)
        new = await _propose(
            provenance, "The newer claim.", versions=[provenance.v1], replaces=target
        )

        await provenance.insights.set_confirmed(new, True)

        assert await rows_containing(provenance.db, "quokka-7731") == []


class TestTakingAnInsightBack:
    async def test_it_stales_the_waiting_proposals_in_the_same_step_and_keeps_their_text(
        self, provenance: ProvenanceWorld
    ) -> None:
        target = await provenance.own("Doubtful claim.")
        proposal = (
            await provenance.deletions.propose(
                provenance.set_id,
                InsightDeletionWrite(thread_id=provenance.thread, insight_id=target, reason=REASON),
            )
        ).proposal
        assert proposal is not None

        await provenance.insights.set_confirmed(target, False)

        row = await provenance.deletions.get(provenance.set_id, proposal.id)
        assert row is not None and row.status == "stale"
        assert row.insight_text == "Doubtful claim." and row.insight_id == target
        assert await provenance.deletions.waiting_for_set(provenance.set_id) == []


class TestADamagedRow:
    async def test_bad_json_reads_as_resting_on_nothing_and_never_errors_the_set(
        self, provenance: ProvenanceWorld
    ) -> None:
        good = await _propose(provenance, "Good.", versions=[provenance.v1])
        damaged = await _propose(provenance, "Damaged.", versions=[provenance.v1])
        await provenance.db.execute(
            "UPDATE knowledge_insights SET rests_on_json = '{not json' WHERE id = ?", (damaged,)
        )

        rows = {
            row.id: row
            for row in await provenance.insights.own(provenance.set_id, include_dismissed=True)
        }

        assert rows[damaged].rests_on == []
        assert [item.label for item in rows[good].rests_on] == ["v1"]
        assert "rests on" not in rows[damaged].render()


class TestTheLineListsOnlyShotsStillFiledHere:
    async def test_a_shot_filed_elsewhere_since_is_left_out(
        self, provenance: ProvenanceWorld
    ) -> None:
        insight = await _propose(provenance, shots=[provenance.shot1, provenance.shot2])
        before = await provenance.insights.get(insight)
        assert before is not None
        assert f"shots {provenance.shot1}, {provenance.shot2}" in before.render()

        assert await provenance.sets.assign_shot(provenance.shot2, provenance.stranger_version)

        row = await provenance.insights.get(insight)
        assert row is not None
        line = row.render()
        assert f"shots {provenance.shot1}]" in line and f", {provenance.shot2}" not in line
        # The stored evidence is untouched: the shot is simply no longer this Set's.
        assert row.evidence_shot_ids == [provenance.shot1, provenance.shot2]


class TestTheThreadDeleteIsOneTransaction:
    async def test_a_failing_thread_delete_keeps_every_dismissed_insight(
        self, provenance: ProvenanceWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The thread's own delete fails, so what ran before it must roll back with it.

        Failing the *thread* delete is what tells one transaction from two, or from a
        delete per insight: the insight deletes have already happened by then.
        """
        first = await provenance.own("Turned down.", confirmed=False, thread_id=provenance.thread)
        second = await provenance.own("Also down.", confirmed=False, thread_id=provenance.thread)
        for insight_id in (first, second):
            await provenance.insights.dismiss(insight_id)
        original = provenance.db.execute

        async def execute(sql: str, params: Any = ()) -> Any:
            if sql.strip().startswith("DELETE FROM chat_threads"):
                raise RuntimeError("disk full")
            return await original(sql, params)

        monkeypatch.setattr(provenance.db, "execute", execute)
        with pytest.raises(RuntimeError):
            await ChatRepository(provenance.db).delete_thread(provenance.thread)
        monkeypatch.undo()

        for insight_id in (first, second):
            assert await provenance.insights.get(insight_id) is not None
        assert await ChatRepository(provenance.db).get_thread(provenance.thread) is not None
