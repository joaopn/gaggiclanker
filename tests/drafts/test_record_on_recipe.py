"""What a Recipe records when its profile gets a new active version.

A draft put on the board becomes the next active version of the profile it is a change to, and
the profile's Recipe, if it has one, follows in the same transaction
(``RecipesRepository.follow_profile``, the one place a Recipe version is made from a profile
change): the content-hashed profile version, the prediction when the draft was made for this very
Recipe, whether the version is a minor or a major one, and who proposed it. The sync that sends the
profile to the machine records nothing on the Recipe.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.recipe_proposals import ProposalWrite, RecipeProposalsRepository
from gaggiclanker.db.repos.recipes import RecipeVersionPatch
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, base_profile, base_version_id, data, error
from tests.drafts.helpers import make_recipe_on, same_name_draft
from tests.drafts.test_api import a_draft, lower_pressure
from tests.drafts.test_board import adopted, get_board, pull, put, row_for
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module


@pytest.fixture
async def a_recipe(adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice]) -> int:
    """A Recipe on the machine's base profile, so a change to that profile has a Recipe to
    follow."""
    _app, client, _ = adopted
    base = row_for(await get_board(client), BASE_LABEL)
    return await make_recipe_on(client, "Draft baseline", base["row"]["current_version_id"])


async def another_recipe(client: httpx.AsyncClient) -> dict[str, Any]:
    """A Recipe on a profile of its own, whose chat can argue a change to some other profile."""
    board = await get_board(client)
    other = next(r for r in board["rows"] if r["row"]["label"] != BASE_LABEL)
    bean = data(await client.post("/api/beans", json={"name": "Elsewhere", "roaster": "nobody"}))
    return dict(
        data(
            await client.post(
                "/api/recipes",
                json={
                    "name": "Another coffee",
                    "bean_id": bean["id"],
                    "automatch": False,
                    "version": {
                        "drink": "espresso",
                        "profile_version_id": other["row"]["current_version_id"],
                    },
                },
            )
        )
    )


async def drafted_for(
    app: FastAPI,
    recipe_id: int | None,
    *,
    prediction: str = "Compared to v1: less of the dry finish, and no slower.",
    compares_to_version_id: int | None = None,
    bar: float = 8.0,
    **more: Any,
) -> dict[str, Any]:
    """A change to the base profile as a Recipe's conversation leaves one: its Recipe and its
    prediction.

    Through `DraftProposals`, which is exactly what the chat tool is handed: the archive and
    the safety bounds, and nothing that could write to the machine. It keeps the profile's name,
    so it is a version of the base profile (and of the base profile's Recipe).
    """
    profile = await base_profile(app)
    document = lower_pressure(profile, bar)
    document["label"] = profile.label
    row = await app.state.draft_proposals.create_manual(
        base_version_id=await base_version_id(app),
        document=document,
        change_summary=f"Down to {bar:g} bar.",
        notes="Proposed in chat.",
        recipe_id=recipe_id,
        prediction=prediction if recipe_id is not None else "",
        compares_to_version_id=compares_to_version_id,
        **more,
    )
    return dict(row.model_dump(mode="json"))


async def versions_of(client: httpx.AsyncClient, recipe_id: int) -> list[dict[str, Any]]:
    """A Recipe's versions as the Recipe page lists them: the newest first."""
    return [e["version"] for e in data(await client.get(f"/api/recipes/{recipe_id}"))["versions"]]


async def draft_row(client: httpx.AsyncClient, draft_id: int) -> dict[str, Any]:
    return dict(data(await client.get(f"/api/profile-drafts/{draft_id}"))["draft"])


async def put_and_sync(
    app: FastAPI, client: httpx.AsyncClient, draft: dict[str, Any], **body: Any
) -> dict[str, Any]:
    """Put a draft on the board and run the sync that sends it; return the draft as it ends."""
    await put(client, draft, **body)
    run = await pull(app)
    assert run.status == "ok", run.error
    return await draft_row(client, draft["id"])


async def test_a_put_on_the_recipes_profile_records_the_version_at_once_and_no_invented_prediction(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider, a_recipe: int
) -> None:
    """The Recipe follows its profile: no opt-in, and no waiting for the machine."""
    app, client, _ = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)

    await put(client, draft)

    version = (await versions_of(client, a_recipe))[0]
    assert version["version_label"] == "v1.1"
    assert (
        version["profile_version_id"] == (await draft_row(client, draft["id"]))["draft_version_id"]
    )
    assert version["origin"] == "manual"
    assert version["prediction"] == "" and version["compares_to_version_id"] is None
    assert (version["dose_g"], version["target_yield_g"]) == (18.0, 36.0), "carried forward"
    assert (await draft_row(client, draft["id"]))["recorded_version_label"] == "v1.1"
    ended = await put_and_sync(app, client, await a_draft(app, client, provider))
    assert ended["status"] == "pushed"
    assert len(await versions_of(client, a_recipe)) == 2, "the sync records nothing on the Recipe"


async def test_a_recipes_own_draft_records_its_prediction_on_the_version(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int
) -> None:
    """A profile change argued in a Recipe's room becomes that Recipe's next experiment."""
    app, client, _ = adopted
    current_id = (await versions_of(client, a_recipe))[0]["id"]
    draft = await drafted_for(app, a_recipe, compares_to_version_id=current_id)

    await put(client, draft)

    version = (await versions_of(client, a_recipe))[0]
    assert version["prediction"].startswith("Compared to v1")
    assert version["compares_to_version_id"] == current_id
    assert version["compares_to_version_label"] == "v1"
    assert version["profile_version_id"] == draft["draft_version_id"]


async def test_a_recipes_draft_says_which_version_the_put_records_before_and_after(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int
) -> None:
    """The card names the version before the put and after it, from the archive.

    Before: the Recipe's next number, so the button can say "record it as v1.1". After: the
    version really recorded, so the card claims the prediction landed only when it did,
    including after a reload.
    """
    app, client, _ = adopted
    draft = await drafted_for(app, a_recipe)
    assert draft["recipe_next_minor_label"] == "v1.1" and draft["recorded_version_label"] is None

    await put(client, draft)

    reread = await draft_row(client, draft["id"])
    assert (
        reread["recorded_version_label"] == "v1.1" and reread["recipe_next_minor_label"] == "v1.2"
    )


async def test_a_put_records_a_minor_version_unless_marked_major(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int
) -> None:
    """A tuned copy is dialling in, so it is a minor version by default.

    The card is served both names before the put, from the query the insert numbers with.
    The person's box wins in both directions; the agent's suggestion rides on the draft for
    the card to show and changes nothing by itself.
    """
    app, client, _ = adopted
    first = await drafted_for(
        app,
        a_recipe,
        suggest_major=True,
        major_reason="Eight bar is a different kind of shot from nine, not a nudge.",
    )
    assert (
        first["suggest_major"],
        first["recipe_next_minor_label"],
        first["recipe_next_major_label"],
    ) == (
        True,
        "v1.1",
        "v2",
    )

    await put(client, first)

    assert (await draft_row(client, first["id"]))["recorded_version_label"] == "v1.1"
    second = await drafted_for(app, a_recipe, bar=7.0, prediction="Compared to v1.1: softer still.")
    reread = await draft_row(client, second["id"])
    assert (reread["recipe_next_minor_label"], reread["recipe_next_major_label"]) == ("v1.2", "v2")

    await put(client, second, major=True)

    assert (await draft_row(client, second["id"]))["recorded_version_label"] == "v2"
    assert (await draft_row(client, first["id"]))["recorded_version_label"] == "v1.1"


async def test_a_put_refuses_a_major_that_is_not_a_boolean_and_changes_nothing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int
) -> None:
    app, client, _ = adopted
    draft = await drafted_for(app, a_recipe)

    response = await client.post(
        "/api/profile-board", json={"draft_id": draft["id"], "major": "yes"}
    )

    assert response.status_code == 400
    assert error(response)["code"] == "INVALID_REQUEST"
    assert (await draft_row(client, draft["id"]))["status"] == "draft"
    assert all(
        r["row"]["pending_draft_id"] != draft["id"] for r in (await get_board(client))["rows"]
    )
    assert len(await versions_of(client, a_recipe)) == 1


async def test_a_fork_is_a_new_profile_and_records_nothing_on_any_recipe(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await a_draft(app, client, provider)

    ended = await put_and_sync(app, client, draft)

    assert ended["status"] == "pushed" and ended["recorded_version_label"] is None
    assert len(await versions_of(client, a_recipe)) == 1


async def test_a_draft_argued_in_another_recipes_chat_records_no_prediction_here(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int
) -> None:
    """The guess was about the other experiment and says nothing about this one."""
    app, client, _ = adopted
    other = await another_recipe(client)
    draft = await drafted_for(app, other["id"])

    await put(client, draft)

    version = (await versions_of(client, a_recipe))[0]
    assert version["recipe_id"] == a_recipe and version["version_label"] == "v1.1"
    assert version["prediction"] == "" and version["compares_to_version_id"] is None
    assert (await draft_row(client, draft["id"]))["recorded_version_label"] == "v1.1"
    assert len(await versions_of(client, other["id"])) == 1


async def test_a_draft_without_a_recipe_has_no_version_to_record(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await a_draft(app, client, provider)
    assert draft["recipe_next_minor_label"] is None and draft["recorded_version_label"] is None


async def test_a_prediction_against_a_version_of_another_recipe_falls_back_to_the_current_one(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int
) -> None:
    """A comparison that is not this Recipe's would render as a dangling reference."""
    app, client, _ = adopted
    other = await another_recipe(client)
    theirs = (await versions_of(client, other["id"]))[0]["id"]
    draft = await drafted_for(app, a_recipe, compares_to_version_id=theirs)

    await put(client, draft)

    mine = await versions_of(client, a_recipe)
    assert mine[0]["compares_to_version_id"] != theirs
    assert mine[0]["compares_to_version_id"] in {v["id"] for v in mine}


async def test_the_version_a_put_for_the_recipes_own_draft_records_says_the_chat_proposed_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int
) -> None:
    """ "Did following the advice help" is a GROUP BY on origin.

    A profile change the agent argued for, filed under `manual`, would credit the person with
    the model's idea: the version records `chat` exactly when it is also recording the agent's
    prediction.
    """
    app, client, _ = adopted
    draft = await drafted_for(app, a_recipe)

    await put(client, draft)

    assert (await versions_of(client, a_recipe))[0]["origin"] == "chat"


async def test_a_hand_drafted_put_stays_manual(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int
) -> None:
    app, client, _ = adopted
    draft = await drafted_for(app, None)

    await put(client, draft)

    assert (await versions_of(client, a_recipe))[0]["origin"] == "manual"


async def test_a_draft_made_from_an_analysis_before_it_was_retired_stays_an_analysis(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int
) -> None:
    """An older draft keeps its provenance, even with a Recipe and a prediction.

    Nothing makes such a draft any more; one made before the per-shot analysis was retired
    still carries `source_analysis_id`, and the version it becomes says where it came from.
    """
    app, client, _ = adopted
    draft = await drafted_for(app, a_recipe, prediction="Compared to v1: less of the dry finish.")
    await app.state.db.execute(
        "UPDATE profile_drafts SET source_analysis_id = 7 WHERE id = ?", (draft["id"],)
    )

    await put(client, draft)

    version = (await versions_of(client, a_recipe))[0]
    assert version["origin"] == "analysis" and version["prediction"].startswith("Compared to v1")
    assert (await draft_row(client, draft["id"]))["recorded_version_label"] == version[
        "version_label"
    ]


async def test_recording_a_version_retires_the_change_waiting_on_the_recipe(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_recipe: int
) -> None:
    """The Recipe moved on, so a proposal argued against the old recipe is not a question."""
    app, client, _ = adopted
    made = await RecipeProposalsRepository(app.state.db).create(
        a_recipe,
        ProposalWrite(
            patch=RecipeVersionPatch(dose_g=18.5),
            reason="Half a gram more.",
            prediction="Compared to v1: a touch more body and no slower.",
        ),
    )
    assert made.proposal is not None
    draft = await drafted_for(app, a_recipe)

    await put(client, draft)

    assert await RecipeProposalsRepository(app.state.db).waiting(a_recipe) is None
    assert data(await client.get(f"/api/recipes/{a_recipe}"))["proposal"] is None


async def test_a_refinement_keeps_the_agents_major_suggestion_and_the_recipe(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider, a_recipe: int
) -> None:
    """The next attempt at the same idea is no smaller a change, and on the same experiment."""
    app, client, _ = adopted
    parent = await drafted_for(
        app,
        a_recipe,
        suggest_major=True,
        major_reason="Eight bar is a different kind of shot from nine, not a nudge.",
    )
    provider.script = [
        json.dumps(
            {
                "profile": lower_pressure(await base_profile(app), 7.5),
                "change_summary": "7.5 bar instead.",
            }
        )
    ]

    refined = data(
        await client.post(f"/api/profile-drafts/{parent['id']}/refine", json={"notes": "Softer."})
    )

    assert (refined["suggest_major"], refined["major_reason"]) == (
        True,
        "Eight bar is a different kind of shot from nine, not a nudge.",
    )
    assert refined["recipe_id"] == a_recipe and refined["prediction"] == parent["prediction"]
    assert refined["compares_to_version_id"] == parent["compares_to_version_id"]
