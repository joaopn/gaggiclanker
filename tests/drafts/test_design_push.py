"""The profile a designed Recipe's accepted recipe puts on the list, and what a sync then does.

A Recipe being designed has no profile, so a put never touches it: the accepted card is what gives
the Recipe its profile, in the accept's own transaction (version 1 is filled with the version the
put ended on). The sync that follows sends the profile to the machine and records nothing on
the Recipe, and the first shot on that profile files under version 1.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.recipe_proposals import ProposalWrite, RecipeProposalsRepository
from gaggiclanker.db.repos.recipes import (
    RecipesRepository,
    RecipeVersionPatch,
)
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import base_profile, base_version_id, data
from tests.drafts.test_api import lower_pressure
from tests.drafts.test_board import adopted, pull, put
from tests.recipes.conftest import make_shot

__all__ = ["adopted"]  # the fixture, re-exported for this module


async def test_after_accept_and_a_sync_shots_on_the_new_profile_are_filed_under_the_recipe(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """Automatch is on from the start, and it needs nothing more than the put and a sync.

    The design's draft belongs to no Recipe and is put plainly by the accept itself. What files
    the next shot is the sync verifying the profile against the draft's own document: the
    machine's copy resolves to the version the accepted card put on version 1.
    """
    app, client, _ = adopted
    bean = data(await client.post("/api/beans", json={"name": "Kenya AA"}))
    grinder = data(await client.post("/api/grinders", json={"name": "Niche Zero"}))
    created = data(
        await client.post(
            "/api/recipes/design", json={"bean_id": bean["id"], "grinder_id": grinder["id"]}
        )
    )
    recipe_id = created["recipe"]["id"]
    draft = await app.state.draft_proposals.create_manual(
        base_version_id=await base_version_id(app),
        document={**lower_pressure(await base_profile(app), 8.5), "label": "Kenya AA body"},
        change_summary="A gentler peak for body.",
    )
    proposal = await RecipeProposalsRepository(app.state.db).create(
        recipe_id,
        ProposalWrite(
            kind="design",
            draft_id=draft.id,
            thread_id=created["thread_id"],
            reason="A gentler peak for body.",
            patch=RecipeVersionPatch(
                profile_version_id=draft.draft_version_id, grind_setting="20", dose_g=18
            ),
        ),
    )
    assert proposal.proposal is not None, proposal.refused
    data(await client.post(f"/api/recipes/{recipe_id}/proposals/{proposal.proposal.id}/accept"))

    run = await pull(app)

    assert run.status == "ok", run.error
    pushed = await ProfileDraftsRepository(app.state.db).get(draft.id)
    assert pushed is not None and pushed.status == "pushed"
    shot = await make_shot(
        app.state.db, "000601", profile_id_on_device=pushed.pushed_device_profile_id or ""
    )
    summary = await RecipesRepository(app.state.db).match_unfiled([shot])
    assert summary.matched == 1
    filed = await app.state.db.fetch_value(
        "SELECT recipe_version_id FROM shots WHERE id = ?", (shot,)
    )
    assert filed == created["version"]["id"]


async def test_accepting_after_the_profile_was_synced_and_a_later_version_made_active_leaves_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """The card's profile was approved on the Profiles page and synced, then a later version of
    it was approved and synced. The card's Accept records version 1 on the card's profile and
    does not make that older version active again."""
    app, client, _ = adopted
    bean = data(await client.post("/api/beans", json={"name": "Kenya AA"}))
    grinder = data(await client.post("/api/grinders", json={"name": "Niche Zero"}))
    created = data(
        await client.post(
            "/api/recipes/design", json={"bean_id": bean["id"], "grinder_id": grinder["id"]}
        )
    )
    recipe_id = created["recipe"]["id"]
    draft = await app.state.draft_proposals.create_manual(
        base_version_id=await base_version_id(app),
        document={**lower_pressure(await base_profile(app), 8.5), "label": "Kenya AA body"},
        change_summary="A gentler peak for body.",
    )
    proposal = await RecipeProposalsRepository(app.state.db).create(
        recipe_id,
        ProposalWrite(
            kind="design",
            draft_id=draft.id,
            thread_id=created["thread_id"],
            reason="A gentler peak for body.",
            patch=RecipeVersionPatch(
                profile_version_id=draft.draft_version_id, grind_setting="20", dose_g=18
            ),
        ),
    )
    assert proposal.proposal is not None, proposal.refused
    row = await put(client, {"id": draft.id})
    assert (await pull(app)).status == "ok"
    later = await app.state.draft_proposals.create_manual(
        base_version_id=draft.draft_version_id,
        document=lower_pressure(await base_profile(app), 7.5) | {"label": "Kenya AA body"},
    )
    moved = await put(client, {"id": later.id})
    assert moved["id"] == row["id"] and moved["current_version_id"] == later.draft_version_id
    assert (await pull(app)).status == "ok"

    accepted = data(
        await client.post(f"/api/recipes/{recipe_id}/proposals/{proposal.proposal.id}/accept")
    )

    after = await app.state.board.board.get(row["id"])
    assert after is not None and after.current_version_id == later.draft_version_id
    assert accepted["version"]["profile_version_id"] == draft.draft_version_id
    assert (accepted["profile_draft_id"], accepted["profile_row_id"]) == (draft.id, row["id"])
    stored = await ProfileDraftsRepository(app.state.db).get(draft.id)
    assert stored is not None and stored.status in ("pushed", "superseded")
