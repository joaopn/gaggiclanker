"""The routes a person presses to answer a proposal to delete a general insight.

Over the real app. What these pin: the card's read (with what the insight applies to and how many
Recipes it reaches while it exists), Delete and Keep as the only removal, a 409 that says what
became of the proposal and never "already answered", a Recipe's routes and the general routes
never answering each other's proposals, and the Recipe's list never carrying a general one.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.chat.context import general_insights_block, opening_context
from gaggiclanker.db.repos.chat import ChatThreadWrite
from gaggiclanker.db.repos.insight_deletions import (
    INSIGHT_CHANGED_SENTENCE,
    INSIGHT_GONE_SENTENCE,
    InsightDeletionsRepository,
    InsightDeletionWrite,
)
from gaggiclanker.db.repos.knowledge_insights import InsightScope, InsightWrite
from gaggiclanker.tools.scope import ToolScope
from tests.knowledge.test_general_requests import API, Kitchen, data, kitchen

REASON = "The person said the Niche note is wrong now."


async def proposed(world: Kitchen, text: str = "Old Niche note.", **scope: Any) -> tuple[int, int]:
    insight = await world.insights.insert(
        InsightWrite(text=text, scope=InsightScope(**scope), source="user", confirmed=True)
    )
    result = await InsightDeletionsRepository(world.db).propose_general(
        InsightDeletionWrite(thread_id=world.thread, insight_id=insight, reason=REASON)
    )
    assert result.proposal is not None
    return insight, result.proposal.id


def url(proposal: int, verb: str = "") -> str:
    return f"{API}/insight-deletions/{proposal}" + (f"/{verb}" if verb else "")


async def conflict(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 409, response.text
    problem: dict[str, Any] = response.json()["error"]
    return problem


async def test_the_card_reads_the_proposal_with_what_the_insight_applies_to(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world, grinder_id=world.niche)

    body = await data(await client.get(url(proposal)))

    assert body["id"] == proposal and body["insight_id"] == insight
    assert (body["insight_text"], body["reason"], body["status"]) == (
        "Old Niche note.",
        REASON,
        "proposed",
    )
    assert body["scope_labels"] == ["grinder Niche Zero"] and body["reach"] == 2


async def test_delete_removes_it_and_the_card_keeps_only_the_words(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world)

    body = await data(await client.post(url(proposal, "accept")))

    assert (body["status"], body["insight_id"], body["insight_text"]) == (
        "deleted",
        None,
        "Old Niche note.",
    )
    assert body["scope_labels"] == [] and body["reach"] is None
    assert (await client.get(f"{API}/insights/{insight}")).status_code == 404


async def test_keep_changes_nothing_but_the_card(app: FastAPI, client: httpx.AsyncClient) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world)
    before = await data(await client.get(f"{API}/insights/{insight}"))

    body = await data(await client.post(url(proposal, "keep")))

    assert body["status"] == "kept" and body["reach"] == 3
    assert await data(await client.get(f"{API}/insights/{insight}")) == before


async def test_a_press_on_an_answered_card_says_what_became_of_it(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    _insight, kept = await proposed(world, "Kept one.")
    _other, deleted = await proposed(world, "Deleted one.")
    await client.post(url(kept, "keep"))
    await client.post(url(deleted, "accept"))

    for proposal, words in ((kept, "It was kept already."), (deleted, "It was deleted already.")):
        for verb in ("accept", "keep"):
            problem = await conflict(await client.post(url(proposal, verb)))
            assert problem["code"] == "INSIGHT_DELETION_DECIDED"
            assert problem["details"]["message"] == words
            assert "already answered" not in str(problem)


async def test_words_changed_since_the_agent_asked_are_not_deleted(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world)
    assert (
        await client.patch(f"{API}/insights/{insight}", json={"text": "Something else now."})
    ).status_code == 200

    problem = await conflict(await client.post(url(proposal, "accept")))

    assert problem["details"]["message"] == INSIGHT_CHANGED_SENTENCE
    assert (await client.get(f"{API}/insights/{insight}")).status_code == 200
    assert (await data(await client.get(url(proposal))))["status"] == "stale"
    # And a second press says the same, not that it was answered.
    again = await conflict(await client.post(url(proposal, "keep")))
    assert again["details"]["message"] == INSIGHT_CHANGED_SENTENCE


async def test_an_insight_deleted_on_the_knowledge_page_leaves_a_stale_card(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world)
    assert (await client.delete(f"{API}/insights/{insight}")).status_code == 200

    body = await data(await client.get(url(proposal)))

    assert (body["status"], body["insight_id"], body["insight_text"]) == ("stale", None, "")
    assert (await conflict(await client.post(url(proposal, "accept"))))["code"] == (
        "INSIGHT_DELETION_DECIDED"
    )


async def test_an_unknown_proposal_is_a_404(app: FastAPI, client: httpx.AsyncClient) -> None:
    await kitchen(app)
    for call in (
        client.get(url(9999)),
        client.post(url(9999, "accept")),
        client.post(url(9999, "keep")),
    ):
        assert (await call).status_code == 404


async def test_a_recipe_proposal_is_not_answered_here_nor_a_general_one_there(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    _general, general_proposal = await proposed(world)
    own = await world.insights.insert(
        InsightWrite(text="A lesson.", source="chat", confirmed=True, recipe_id=world.a)
    )
    # The Recipe route needs a thread of that Recipe.
    created = await world.chat.create_thread(ChatThreadWrite(recipe_id=world.a))
    assert created.thread is not None
    made = await InsightDeletionsRepository(world.db).propose(
        world.a, InsightDeletionWrite(thread_id=created.thread.id, insight_id=own, reason=REASON)
    )
    assert made.proposal is not None
    recipe_proposal = made.proposal.id

    assert (await client.get(url(recipe_proposal))).status_code == 404
    assert (await client.post(url(recipe_proposal, "accept"))).status_code == 404
    assert (await client.post(url(recipe_proposal, "keep"))).status_code == 404
    recipe_url = f"/api/recipes/{world.a}/insight-deletions"
    assert (await client.post(f"{recipe_url}/{general_proposal}/accept")).status_code == 404
    assert (await client.post(f"{recipe_url}/{general_proposal}/keep")).status_code == 404
    # Nothing was decided by the wrong door.
    assert (await data(await client.get(url(general_proposal))))["status"] == "proposed"
    assert (await client.get(f"{API}/insights/{own}")).status_code == 200


async def test_a_recipes_list_never_carries_a_general_proposal(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    await proposed(world)
    created = await world.chat.create_thread(ChatThreadWrite(recipe_id=world.a))
    assert created.thread is not None

    plain = await data(await client.get(f"/api/recipes/{world.a}/insight-deletions"))
    # Even asking for the thread that proposed it: a general proposal has no Recipe.
    by_thread = await data(
        await client.get(
            f"/api/recipes/{world.a}/insight-deletions", params={"thread_id": world.thread}
        )
    )
    assert plain["items"] == [] and by_thread["items"] == []


# ── why a proposal went stale, and what a stale or waiting card may say ──


def line_for(context: str, insight: int) -> str:
    return next(line for line in context.splitlines() if line.startswith(f"- #{insight} "))


async def test_a_stale_proposal_for_an_edited_insight_is_told_as_changed_not_gone(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world)
    await client.patch(f"{API}/insights/{insight}", json={"text": "Niche: 8 now."})
    assert (await client.post(url(proposal, "accept"))).status_code == 409
    assert (await client.get(f"{API}/insights/{insight}")).status_code == 200

    line = line_for(await opening_context(world.db, ToolScope(), thread_id=world.thread), insight)
    assert "already gone" not in line and "it is still on the Knowledge page" in line
    body = await data(await client.get(url(proposal)))
    assert (body["status"], body["stale_cause"]) == ("stale", "changed")
    problem = await conflict(await client.post(url(proposal, "keep")))
    assert problem["details"]["message"] == INSIGHT_CHANGED_SENTENCE


async def test_a_stale_proposal_for_a_taken_back_insight_is_changed_too(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world)
    await client.patch(f"{API}/insights/{insight}", json={"confirmed": False})

    body = await data(await client.get(url(proposal)))
    assert (body["status"], body["stale_cause"], body["insight_id"]) == (
        "stale",
        "changed",
        insight,
    )


async def test_a_stale_proposal_for_a_deleted_insight_is_told_as_gone(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world)
    await client.delete(f"{API}/insights/{insight}")

    line = next(
        line
        for line in (
            await opening_context(world.db, ToolScope(), thread_id=world.thread)
        ).splitlines()
        if "(general; " in line
    )
    assert "already gone, nothing to do" in line
    body = await data(await client.get(url(proposal)))
    assert body["stale_cause"] == "deleted"
    problem = await conflict(await client.post(url(proposal, "accept")))
    assert problem["details"]["message"] == INSIGHT_GONE_SENTENCE
    assert INSIGHT_CHANGED_SENTENCE not in str(problem)


async def test_a_taken_back_insight_is_told_to_no_recipe_but_keeps_its_scope_words(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world, grinder_id=world.niche)
    assert (await data(await client.get(url(proposal))))["reach"] == 2
    await client.patch(f"{API}/insights/{insight}", json={"confirmed": False})

    body = await data(await client.get(url(proposal)))
    # Not told to anyone, but its scope is still true: the card must not read "every Recipe".
    assert (body["reach"], body["scope_labels"]) == (None, ["grinder Niche Zero"])
    assert body["insight_id"] == insight and body["status"] == "stale"


async def test_editing_the_words_stales_a_waiting_general_proposal_at_once(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world, "Old Niche note.")

    assert (
        await client.patch(f"{API}/insights/{insight}", json={"text": "Niche: 8 now."})
    ).status_code == 200

    body = await data(await client.get(url(proposal)))
    assert (body["status"], body["insight_text"], body["decided_at"] is not None) == (
        "stale",
        "Old Niche note.",
        True,
    )
    # The conversation is no longer told "waiting".
    context = await opening_context(world.db, ToolScope(), thread_id=world.thread)
    assert "waiting: nothing is deleted" not in context


async def test_editing_a_recipe_insights_words_stales_its_waiting_proposal_too(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    own = await world.insights.insert(
        InsightWrite(text="A lesson.", source="chat", confirmed=True, recipe_id=world.a)
    )
    created = await world.chat.create_thread(ChatThreadWrite(recipe_id=world.a))
    assert created.thread is not None
    made = await InsightDeletionsRepository(world.db).propose(
        world.a, InsightDeletionWrite(thread_id=created.thread.id, insight_id=own, reason=REASON)
    )
    assert made.proposal is not None

    assert (
        await client.patch(f"{API}/insights/{own}", json={"text": "Another lesson."})
    ).status_code == 200

    listed = await data(await client.get(f"/api/recipes/{world.a}/insight-deletions"))
    assert [(row["status"], row["stale_cause"]) for row in listed["items"]] == [
        ("stale", "changed")
    ]


async def test_a_scope_or_recasing_edit_leaves_a_waiting_proposal_alone(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world, "Old Niche note.")
    await client.patch(f"{API}/insights/{insight}", json={"scope": {"grinder_id": world.niche}})
    await client.patch(f"{API}/insights/{insight}", json={"text": " OLD  niche NOTE. "})

    assert (await data(await client.get(url(proposal))))["status"] == "proposed"


async def test_a_waiting_replacement_whose_old_insight_was_taken_back_says_so(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    old = await world.insights.insert(
        InsightWrite(text="Old Niche note.", source="user", confirmed=True)
    )
    message = await world.say("forget the Niche note, 7 is fine now")
    new = await world.insights.insert(
        InsightWrite(
            text="Niche 7 is fine.",
            source="user",
            confirmed=False,
            thread_id=world.thread,
            from_message_id=message,
            replaces_id=old,
        )
    )
    waiting = line_for("\n".join(await general_insights_block(world.db, world.thread)), new)
    assert f"replacing #{old}" in waiting

    await client.patch(f"{API}/insights/{old}", json={"confirmed": False})

    line = line_for("\n".join(await general_insights_block(world.db, world.thread)), new)
    assert f"replacing #{old}" not in line
    assert f"#{old} has changed or gone since: adding this will not delete anything" in line


async def test_an_edit_closes_the_card_while_the_insight_is_still_told(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight, proposal = await proposed(world, grinder_id=world.niche)
    await client.patch(f"{API}/insights/{insight}", json={"text": "Niche: 8 now."})

    body = await data(await client.get(url(proposal)))

    assert (body["status"], body["stale_cause"]) == ("stale", "changed")
    # Still added, still told: the card's "told to 2 Recipes now" is true.
    assert (body["reach"], body["scope_labels"]) == (2, ["grinder Niche Zero"])


async def test_a_recipe_proposal_staled_by_an_edit_still_has_its_insight(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    insight = await world.insights.insert(
        InsightWrite(text="Recipe lesson.", recipe_id=world.a, source="chat", confirmed=True)
    )
    thread = (await world.chat.create_thread(ChatThreadWrite(recipe_id=world.a))).thread
    assert thread is not None
    made = await InsightDeletionsRepository(world.db).propose(
        world.a, InsightDeletionWrite(thread_id=thread.id, insight_id=insight, reason=REASON)
    )
    assert made.proposal is not None
    await client.patch(f"{API}/insights/{insight}", json={"text": "Recipe lesson, reworded."})

    listed = await data(
        await client.get(
            f"/api/recipes/{world.a}/insight-deletions", params={"thread_id": thread.id}
        )
    )
    row = next(item for item in listed["items"] if item["id"] == made.proposal.id)
    assert (row["status"], row["insight_id"], row["stale_cause"]) == ("stale", insight, "changed")
    assert (await client.get(f"{API}/insights/{insight}")).status_code == 200


async def test_the_changed_line_says_where_the_insight_still_is_by_its_kind(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    world = await kitchen(app)
    # A general insight is on the Knowledge page; a Recipe's own is on that Recipe's page, and
    # the Knowledge page lists general insights only.
    general, _proposal = await proposed(world)
    await client.patch(f"{API}/insights/{general}", json={"text": "Niche: 8 now."})
    context = await opening_context(world.db, ToolScope(), thread_id=world.thread)
    assert "it is still on the Knowledge page" in line_for(context, general)

    own = await world.insights.insert(
        InsightWrite(text="Recipe lesson.", recipe_id=world.a, source="chat", confirmed=True)
    )
    thread = (await world.chat.create_thread(ChatThreadWrite(recipe_id=world.a))).thread
    assert thread is not None
    made = await InsightDeletionsRepository(world.db).propose(
        world.a, InsightDeletionWrite(thread_id=thread.id, insight_id=own, reason=REASON)
    )
    assert made.proposal is not None
    await client.patch(f"{API}/insights/{own}", json={"text": "Recipe lesson, reworded."})

    listed = await data(await client.get(f"{API}/insights"))
    assert own not in [row["id"] for row in listed["items"]]
    scope = await ToolScope.resolve(world.db, world.a)
    block = (
        (await opening_context(world.db, scope, thread_id=thread.id))
        .split("INSIGHT DELETIONS YOU PROPOSED IN THIS CONVERSATION", 1)[1]
        .splitlines()[1:]
    )
    line = next(x for x in block if x.startswith(f"- #{own} "))
    assert "it is still on this Recipe's page" in line and "Knowledge page" not in line
