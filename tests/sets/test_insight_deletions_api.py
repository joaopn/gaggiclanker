"""The routes a person presses to answer a proposed deletion or add a replacement.

Against the real app: the envelope, the codes, `details` that echo nothing, and the
one property the whole feature rests on: after an insight is removed by any path,
the Set's payloads are byte-for-byte what they were before it existed.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.insight_deletions import (
    InsightDeletionsRepository,
    InsightDeletionWrite,
)
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository, InsightWrite
from gaggiclanker.db.repos.sets import SetsRepository
from tests.sets.conftest import make_shot
from tests.sets.test_api import _make_set, data, error

REASON = "The two newest shots contradict it on every measure."
SECRET = "a doubtful claim about the grinder"


@pytest.fixture
async def bean_id(client: httpx.AsyncClient) -> int:
    response = await client.post(
        "/api/beans", json={"name": "Ethiopia Guji", "roast_level": "light"}
    )
    assert response.status_code == 201
    return int(data(response)["id"])


async def _set_with_thread(
    client: httpx.AsyncClient, app: FastAPI, bean: int, name: str = "Guji"
) -> tuple[int, int, int]:
    """A Set, its version and a conversation about it, with one shot filed."""
    created = await _make_set(client, bean, name=name)
    set_id = int(created["id"])
    version_id = int(created["current_version_id"])
    shot = await make_shot(app.state.db, f"{set_id}00001")
    assert await SetsRepository(app.state.db).assign_shot(shot, version_id)
    made = await ChatRepository(app.state.db).create_thread(ChatThreadWrite(set_id=set_id))
    assert made.thread is not None
    return set_id, version_id, made.thread.id


async def _added(app: FastAPI, set_id: int, version_id: int, text: str = SECRET) -> int:
    return await InsightsRepository(app.state.db).insert(
        InsightWrite(
            text=text, source="chat", confirmed=True, set_id=set_id, set_version_id=version_id
        )
    )


async def _propose_deletion(app: FastAPI, set_id: int, thread: int, insight: int) -> int:
    result = await InsightDeletionsRepository(app.state.db).propose(
        set_id, InsightDeletionWrite(thread_id=thread, insight_id=insight, reason=REASON)
    )
    assert result.proposal is not None
    return result.proposal.id


def _url(set_id: int, proposal_id: int, verb: str) -> str:
    return f"/api/sets/{set_id}/insight-deletions/{proposal_id}/{verb}"


class TestAnswering:
    async def test_delete_removes_the_insight_and_answers_with_the_card(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        insight = await _added(app, set_id, version_id)
        proposal = await _propose_deletion(app, set_id, thread, insight)

        response = await client.post(_url(set_id, proposal, "accept"))

        card = data(response)["proposal"]
        assert (card["status"], card["insight_id"], card["insight_text"]) == (
            "deleted",
            None,
            SECRET,
        )
        assert (await client.get(f"/api/knowledge/insights/{insight}")).status_code == 404
        listed = data(await client.get(f"/api/sets/{set_id}/insight-deletions"))["items"]
        assert [(row["id"], row["status"]) for row in listed] == [(proposal, "deleted")]

    async def test_keep_changes_nothing_but_the_card(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        insight = await _added(app, set_id, version_id)
        before = data(await client.get(f"/api/knowledge/insights/{insight}"))
        proposal = await _propose_deletion(app, set_id, thread, insight)

        response = await client.post(_url(set_id, proposal, "keep"))

        assert data(response)["proposal"]["status"] == "kept"
        assert data(await client.get(f"/api/knowledge/insights/{insight}")) == before

    async def test_a_second_press_is_a_conflict_that_echoes_nothing(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        insight = await _added(app, set_id, version_id)
        proposal = await _propose_deletion(app, set_id, thread, insight)
        await client.post(_url(set_id, proposal, "keep"))

        for verb in ("accept", "keep"):
            response = await client.post(_url(set_id, proposal, verb))
            assert response.status_code == 409
            problem = error(response)
            assert problem["code"] == "INSIGHT_DELETION_DECIDED"
            assert SECRET not in response.text and REASON not in response.text

    async def test_an_insight_taken_back_since_says_so(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        insight = await _added(app, set_id, version_id)
        proposal = await _propose_deletion(app, set_id, thread, insight)
        # The repository stales the proposal when the insight is taken back; this is the
        # guard for a write that did not.
        await app.state.db.execute(
            "UPDATE knowledge_insights SET confirmed = 0 WHERE id = ?", (insight,)
        )

        response = await client.post(_url(set_id, proposal, "accept"))

        assert response.status_code == 409
        assert error(response)["code"] == "INSIGHT_NOT_ADDED"
        assert (await client.get(f"/api/knowledge/insights/{insight}")).status_code == 200

    async def test_an_unknown_or_another_sets_proposal_is_not_found(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        other, _, _ = await _set_with_thread(client, app, bean_id, name="Other")
        insight = await _added(app, set_id, version_id)
        proposal = await _propose_deletion(app, set_id, thread, insight)

        for url in (_url(set_id, 9999, "accept"), _url(other, proposal, "accept")):
            assert (await client.post(url)).status_code == 404
        assert (await client.get("/api/sets/9999/insight-deletions")).status_code == 404
        assert (await client.get(f"/api/knowledge/insights/{insight}")).status_code == 200


class TestTakingAnInsightBack:
    async def test_it_stales_the_waiting_proposal_so_the_page_stops_showing_it(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        insight = await _added(app, set_id, version_id)
        proposal = await _propose_deletion(app, set_id, thread, insight)

        await client.patch(f"/api/knowledge/insights/{insight}", json={"confirmed": False})

        body = data(await client.get(f"/api/knowledge/insights?set_id={set_id}"))
        assert body["waiting_deletions"] == []
        response = await client.post(_url(set_id, proposal, "accept"))
        assert response.status_code == 409
        assert error(response)["code"] == "INSIGHT_DELETION_DECIDED"


class TestWhatThePageReads:
    async def test_a_waiting_deletion_is_listed_with_the_sets_insights(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        insight = await _added(app, set_id, version_id)
        proposal = await _propose_deletion(app, set_id, thread, insight)

        body = data(await client.get(f"/api/knowledge/insights?set_id={set_id}"))
        assert [
            (row["id"], row["insight_id"], row["thread_id"]) for row in body["waiting_deletions"]
        ] == [(proposal, insight, thread)]
        # The Knowledge page's own list carries none.
        assert data(await client.get("/api/knowledge/insights"))["waiting_deletions"] == []

        await client.post(_url(set_id, proposal, "keep"))
        body = data(await client.get(f"/api/knowledge/insights?set_id={set_id}"))
        assert body["waiting_deletions"] == []

    async def test_an_insight_payload_carries_what_it_rests_on_and_replaces(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        old = await _added(app, set_id, version_id, "Old claim.")
        new = await InsightsRepository(app.state.db).insert(
            InsightWrite(
                text="New claim.",
                source="chat",
                set_id=set_id,
                set_version_id=version_id,
                thread_id=thread,
                replaces_id=old,
                replaces_text="Old claim.",
            )
        )

        row = data(await client.get(f"/api/knowledge/insights/{new}"))

        assert (row["replaces_id"], row["replaces_text"], row["replaced"]) == (
            old,
            "Old claim.",
            None,
        )
        assert row["rests_on"] == []

    async def test_adding_a_replacement_answers_with_how_it_ended(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        old = await _added(app, set_id, version_id, "Old claim.")
        new = await InsightsRepository(app.state.db).insert(
            InsightWrite(
                text="New claim.",
                source="chat",
                set_id=set_id,
                set_version_id=version_id,
                thread_id=thread,
                replaces_id=old,
                replaces_text="Old claim.",
            )
        )

        row = data(await client.patch(f"/api/knowledge/insights/{new}", json={"confirmed": True}))

        assert row["confirmed"] is True and row["replaced"] == "deleted"
        assert (await client.get(f"/api/knowledge/insights/{old}")).status_code == 404


class TestRemovedMeansRemoved:
    """The Set's payloads after a removal are byte-identical to before the insight existed."""

    async def _payloads(self, client: httpx.AsyncClient, set_id: int) -> list[str]:
        """The response data of each, serialised: the request id in `meta` differs by design."""
        return [
            json.dumps(data(await client.get(url)), sort_keys=True)
            for url in (
                f"/api/knowledge/insights?set_id={set_id}",
                f"/api/sets/{set_id}",
                f"/api/sets/{set_id}/insight-deletions",
            )
        ]

    async def test_after_the_persons_delete(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        before = await self._payloads(client, set_id)
        insight = await _added(app, set_id, version_id)
        proposal = await _propose_deletion(app, set_id, thread, insight)

        assert (await client.delete(f"/api/knowledge/insights/{insight}")).status_code == 200

        stored = data(await client.get(f"/api/sets/{set_id}/insight-deletions"))["items"]
        assert [(row["id"], row["status"]) for row in stored] == [(proposal, "stale")]
        after = await self._payloads(client, set_id)
        # Only the conversation's own record of the proposal differs: the list of its cards.
        assert after[:2] == before[:2]

    async def test_after_an_accepted_deletion(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        before = await self._payloads(client, set_id)
        insight = await _added(app, set_id, version_id)
        proposal = await _propose_deletion(app, set_id, thread, insight)

        await client.post(_url(set_id, proposal, "accept"))

        assert (await self._payloads(client, set_id))[:2] == before[:2]

    async def test_after_a_replacements_add_only_the_replacement_remains(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        control = data(await client.get(f"/api/knowledge/insights?set_id={set_id}"))
        old = await _added(app, set_id, version_id, "Old claim.")
        new = await InsightsRepository(app.state.db).insert(
            InsightWrite(
                text="New claim.",
                source="chat",
                set_id=set_id,
                set_version_id=version_id,
                thread_id=thread,
                replaces_id=old,
                replaces_text="Old claim.",
            )
        )

        await client.patch(f"/api/knowledge/insights/{new}", json={"confirmed": True})

        listed = data(await client.get(f"/api/knowledge/insights?set_id={set_id}"))
        assert control["items"] == []
        assert [item["id"] for item in listed["items"]] == [new]
        assert (
            "Old claim." not in (await client.get(f"/api/knowledge/insights?set_id={set_id}")).text
        )

    async def test_deleting_a_conversation_removes_its_dismissed_insights_only(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        repo = InsightsRepository(app.state.db)
        dismissed = await repo.insert(
            InsightWrite(
                text="Turned down.",
                source="chat",
                set_id=set_id,
                set_version_id=version_id,
                thread_id=thread,
            )
        )
        await client.post(f"/api/knowledge/insights/{dismissed}/dismiss")
        waiting = await repo.insert(
            InsightWrite(
                text="Waiting.",
                source="chat",
                set_id=set_id,
                set_version_id=version_id,
                thread_id=thread,
            )
        )

        assert (await client.delete(f"/api/chat/threads/{thread}")).status_code == 200

        assert (await client.get(f"/api/knowledge/insights/{dismissed}")).status_code == 404
        assert (await client.get(f"/api/knowledge/insights/{waiting}")).status_code == 200


class TestACardFindsItsProposalHoweverOld:
    async def test_the_thread_list_is_not_capped(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        insight = await _added(app, set_id, version_id)
        oldest = await _propose_deletion(app, set_id, thread, insight)
        await client.post(_url(set_id, oldest, "keep"))
        for number in range(120):
            await app.state.db.execute(
                "INSERT INTO set_insight_deletions "
                "(set_id, thread_id, insight_text, reason, status) VALUES (?, ?, ?, ?, 'kept')",
                (set_id, thread, f"older {number}", REASON),
            )

        capped = data(await client.get(f"/api/sets/{set_id}/insight-deletions"))["items"]
        by_thread = data(
            await client.get(f"/api/sets/{set_id}/insight-deletions?thread_id={thread}")
        )["items"]

        assert oldest not in {row["id"] for row in capped}
        assert len(by_thread) == 121 and oldest in {row["id"] for row in by_thread}

    async def test_another_threads_proposals_are_not_listed_under_this_set(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        set_id, version_id, thread = await _set_with_thread(client, app, bean_id)
        other, _, other_thread = await _set_with_thread(client, app, bean_id, name="Other")
        insight = await _added(app, set_id, version_id)
        await _propose_deletion(app, set_id, thread, insight)

        body = data(await client.get(f"/api/sets/{other}/insight-deletions?thread_id={thread}"))
        assert body["items"] == []
        assert other_thread != thread
