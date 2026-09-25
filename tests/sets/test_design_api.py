"""`POST /api/sets/design`, `DELETE /api/sets/{id}/design`, and the initial recipe's card.

Through the real app. The route creates a Set with an empty version 1 and opens
that version's conversation; its refusals name the field and never echo what
was sent; the discard is refused, with its own code, for anything but a design
nobody brewed under; and the proposal routes carry the card's kind and draft.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.chat import ChatRepository
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch
from tests.sets.conftest import make_profile_version, make_shot


def data(response: httpx.Response) -> Any:
    body = response.json()
    assert body["ok"] is True, body
    return body["data"]


def error(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert body["ok"] is False, body
    assert body["meta"]["request_id"]
    return dict(body["error"])


@pytest.fixture
async def kit(client: httpx.AsyncClient) -> dict[str, int]:
    bean = data(await client.post("/api/beans", json={"name": "Kenya AA", "roast_level": "light"}))
    grinder = data(
        await client.post("/api/grinders", json={"name": "Niche Zero", "step_unit": "numbers"})
    )
    return {"bean_id": int(bean["id"]), "grinder_id": int(grinder["id"])}


async def _design(client: httpx.AsyncClient, kit: dict[str, int], **over: Any) -> Any:
    response = await client.post("/api/sets/design", json={**kit, **over})
    assert response.status_code == 201, response.text
    return data(response)


# -- starting a design -----------------------------------------------------------


async def test_a_design_is_a_set_with_an_empty_version_1_and_its_conversation(
    app: FastAPI, client: httpx.AsyncClient, kit: dict[str, int]
) -> None:
    fork = await make_profile_version(app.state.db, "Fork me")

    created = await _design(
        client, kit, fork_profile_version_id=fork, usual_grind="21", goal="More body."
    )

    row = created["set"]
    assert row["name"] == "Kenya AA on the Niche Zero"
    assert row["designing"] is True
    assert row["automatch"] is True
    assert row["design_brief"] == {
        "fork_profile_version_id": fork,
        "usual_grind": "21",
        "goal": "More body.",
    }
    version = created["version"]
    assert (version["version_no"], version["profile_version_id"], version["dose_g"]) == (
        1,
        None,
        None,
    )
    thread = await ChatRepository(app.state.db).get_thread(created["thread_id"])
    assert thread is not None
    assert (thread.set_id, thread.set_version_id) == (row["id"], version["id"])
    # The Set page reads it the same way.
    page = data(await client.get(f"/api/sets/{row['id']}"))
    assert page["set"]["designing"] is True


async def test_a_design_may_be_named(client: httpx.AsyncClient, kit: dict[str, int]) -> None:
    created = await _design(client, kit, name="Kenya, but juicier")

    assert created["set"]["name"] == "Kenya, but juicier"


@pytest.mark.parametrize(
    ("field", "value"),
    [("bean_id", 999_999), ("grinder_id", 999_999), ("fork_profile_version_id", 999_999)],
)
async def test_an_unknown_reference_is_a_404_naming_the_field_not_the_value(
    client: httpx.AsyncClient, kit: dict[str, int], field: str, value: int
) -> None:
    response = await client.post("/api/sets/design", json={**kit, field: value})

    assert response.status_code == 404
    refused = error(response)
    assert refused["code"] == "NOT_FOUND"
    assert refused["details"]["field"] == field
    assert "999999" not in str(refused)


async def test_a_grinder_is_required(client: httpx.AsyncClient, kit: dict[str, int]) -> None:
    response = await client.post("/api/sets/design", json={"bean_id": kit["bean_id"]})

    assert response.status_code == 400
    assert error(response)["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "extra", [{"goal": "x" * 2001}, {"usual_grind": "x" * 101}, {"dose_g": 18}, {"name": ""}]
)
async def test_the_body_is_as_strict_as_the_brief(
    client: httpx.AsyncClient, kit: dict[str, int], extra: dict[str, Any]
) -> None:
    response = await client.post("/api/sets/design", json={**kit, **extra})

    assert response.status_code == 400
    assert error(response)["code"] == "INVALID_REQUEST"
    assert "x" * 50 not in response.text


# -- discarding ------------------------------------------------------------------


async def test_a_design_nobody_brewed_under_is_discarded(
    client: httpx.AsyncClient, kit: dict[str, int]
) -> None:
    created = await _design(client, kit)
    set_id = created["set"]["id"]

    response = await client.delete(f"/api/sets/{set_id}/design")

    assert data(response) == {"set_id": set_id, "discarded": True}
    assert (await client.get(f"/api/sets/{set_id}")).status_code == 404
    assert (await client.get(f"/api/chat/threads/{created['thread_id']}")).status_code == 404


async def test_discarding_anything_else_is_refused_with_its_own_code(
    app: FastAPI, client: httpx.AsyncClient, kit: dict[str, int]
) -> None:
    ordinary = data(
        await client.post("/api/sets", json={"name": "By hand", "bean_id": kit["bean_id"]})
    )
    designed = await _design(client, kit)
    shot = await make_shot(app.state.db, "000501")
    await SetsRepository(app.state.db).assign_shot(shot, designed["version"]["id"])

    not_designing = await client.delete(f"/api/sets/{ordinary['id']}/design")
    has_shots = await client.delete(f"/api/sets/{designed['set']['id']}/design")
    missing = await client.delete("/api/sets/999999/design")

    assert (not_designing.status_code, error(not_designing)["code"]) == (409, "NOT_DESIGNING")
    assert (has_shots.status_code, error(has_shots)["code"]) == (409, "DESIGN_HAS_SHOTS")
    assert missing.status_code == 404
    assert (await client.get(f"/api/sets/{designed['set']['id']}")).status_code == 200


# -- the card --------------------------------------------------------------------


async def _card(app: FastAPI, set_id: int, thread_id: int) -> tuple[int, int, int]:
    """An initial recipe waiting on a design: the proposal, its draft and its profile."""
    db = app.state.db
    base = await make_profile_version(db, "Library")
    drafted = await make_profile_version(db, "Designed [AI]", temperature=94)
    draft = await ProfileDraftsRepository(db).create(
        ProfileDraftWrite(base_version_id=base, draft_version_id=drafted)
    )
    result = await SetProposalsRepository(db).create(
        set_id,
        ProposalWrite(
            kind="design",
            draft_id=draft.id,
            thread_id=thread_id,
            reason="A longer bloom for body.",
            patch=SetVersionPatch(
                profile_version_id=drafted, grind_setting="20", dose_g=18, target_yield_g=40
            ),
        ),
    )
    assert result.proposal is not None, result.refused
    return result.proposal.id, draft.id, drafted


async def test_the_card_is_served_with_its_kind_and_draft_and_accepts_into_version_1(
    app: FastAPI, client: httpx.AsyncClient, kit: dict[str, int]
) -> None:
    created = await _design(client, kit)
    set_id = created["set"]["id"]
    proposal_id, draft_id, drafted = await _card(app, set_id, created["thread_id"])

    waiting = data(await client.get(f"/api/sets/{set_id}"))["proposal"]
    assert (waiting["kind"], waiting["draft_id"], waiting["status"]) == (
        "design",
        draft_id,
        "proposed",
    )
    # The card's lines are version 1 as it would be filled, against the empty one.
    assert {change["field"] for change in waiting["changes"]} >= {
        "profile_version_id",
        "grind_setting",
        "dose_g",
        "target_yield_g",
    }

    accepted = data(await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept"))

    assert accepted["proposal"]["status"] == "accepted"
    assert accepted["proposal"]["resulting_version_no"] == 1
    version = accepted["version"]
    assert (version["id"], version["version_no"], version["origin"]) == (
        created["version"]["id"],
        1,
        "chat",
    )
    assert version["profile_version_id"] == drafted
    page = data(await client.get(f"/api/sets/{set_id}"))
    assert page["set"]["designing"] is False
    assert [item["version"]["version_no"] for item in page["versions"]] == [1]
    assert page["versions"][0]["chat_thread_id"] == created["thread_id"]


async def test_an_accepted_card_still_shows_the_recipe_it_set(
    app: FastAPI, client: httpx.AsyncClient, kit: dict[str, int]
) -> None:
    created = await _design(client, kit)
    set_id = created["set"]["id"]
    proposal_id, _, _ = await _card(app, set_id, created["thread_id"])
    waiting = data(await client.get(f"/api/sets/{set_id}"))["proposal"]["changes"]
    assert waiting

    accepted = data(await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept"))
    listed = data(await client.get(f"/api/sets/{set_id}/proposals"))["items"]

    # Version 1 now holds this very recipe; the card is still drawn against the
    # empty one it filled, so scrolling back shows what was agreed.
    assert accepted["proposal"]["changes"] == waiting
    assert next(item for item in listed if item["id"] == proposal_id)["changes"] == waiting


async def test_a_card_overtaken_by_a_hand_written_recipe_still_shows_only_its_own(
    app: FastAPI, client: httpx.AsyncClient, kit: dict[str, int]
) -> None:
    created = await _design(client, kit)
    set_id = created["set"]["id"]
    # A relative grind: the card sets the words and no dial number.
    proposal_id, _, _ = await _card(app, set_id, created["thread_id"])
    waiting = data(await client.get(f"/api/sets/{set_id}"))["proposal"]["changes"]
    assert "grind_value" not in {change["field"] for change in waiting}

    # The person fills version 1 by hand, with a number on the dial.
    data(
        await client.post(
            f"/api/sets/{set_id}/versions",
            json={"grind_setting": "19", "grind_value": 19, "dose_g": 18},
        )
    )
    listed = data(await client.get(f"/api/sets/{set_id}/proposals"))["items"]
    stale = next(item for item in listed if item["id"] == proposal_id)

    # Still the card as it was proposed: no dial number borrowed from the
    # recipe that overtook it.
    assert stale["status"] == "stale"
    assert stale["changes"] == waiting


async def test_declining_the_card_discards_its_draft(
    app: FastAPI, client: httpx.AsyncClient, kit: dict[str, int]
) -> None:
    created = await _design(client, kit)
    set_id = created["set"]["id"]
    proposal_id, draft_id, _ = await _card(app, set_id, created["thread_id"])

    declined = data(
        await client.post(
            f"/api/sets/{set_id}/proposals/{proposal_id}/decline", json={"note": "Too long."}
        )
    )

    assert declined["proposal"]["status"] == "declined"
    draft = await ProfileDraftsRepository(app.state.db).get(draft_id)
    assert draft is not None and draft.status == "discarded"


async def test_accepting_the_card_after_a_shot_was_filed_is_a_409(
    app: FastAPI, client: httpx.AsyncClient, kit: dict[str, int]
) -> None:
    created = await _design(client, kit)
    set_id = created["set"]["id"]
    proposal_id, _, _ = await _card(app, set_id, created["thread_id"])
    shot = await make_shot(app.state.db, "000502")
    await SetsRepository(app.state.db).assign_shot(shot, created["version"]["id"])

    response = await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept")

    assert response.status_code == 409
    assert error(response)["code"] == "DESIGN_HAS_SHOTS"


async def test_the_add_a_version_form_ends_the_design_and_retires_the_card(
    app: FastAPI, client: httpx.AsyncClient, kit: dict[str, int]
) -> None:
    created = await _design(client, kit)
    set_id = created["set"]["id"]
    proposal_id, draft_id, _ = await _card(app, set_id, created["thread_id"])

    response = await client.post(
        f"/api/sets/{set_id}/versions", json={"grind_setting": "19", "dose_g": 18}
    )

    version = data(response)
    assert (version["id"], version["version_no"]) == (created["version"]["id"], 1)
    page = data(await client.get(f"/api/sets/{set_id}"))
    assert page["set"]["designing"] is False
    assert page["proposal"] is None
    listed = data(await client.get(f"/api/sets/{set_id}/proposals"))["items"]
    assert [(item["id"], item["status"]) for item in listed] == [(proposal_id, "stale")]
    draft = await ProfileDraftsRepository(app.state.db).get(draft_id)
    assert draft is not None and draft.status == "discarded"
