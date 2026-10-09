"""Where a first recipe's profile draft stands once the design paths have discarded it.

A draft the design paths discard (a newer recipe, the Set's first version written another way, the
Set discarded) is not a person's decline, so the standing read does not call it declined: it
answers ``replaced`` with the reason recorded where the draft was discarded. Only a card the
person declined leaves a draft with no recorded reason.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    DesignBrief,
    SetRow,
    SetsRepository,
    SetVersionPatch,
    SetWrite,
)
from gaggiclanker.drafts import standing as words
from tests.sets.conftest import make_profile_version
from tests.sets.test_design_api import data


async def _standing(client: httpx.AsyncClient, draft: Any) -> dict[str, Any]:
    return dict(data(await client.get(f"/api/profile-drafts/{draft.id}/standing")))


async def _designed(app: FastAPI) -> SetRow:
    db = app.state.db
    bean = await BeansRepository(db).create(BeanWrite(name="Kenya AA"))
    grinder = await GrindersRepository(db).create(GrinderWrite(name="Niche Zero"))
    return await SetsRepository(db).create_design(
        SetWrite(name="Designed", bean_id=bean.id, grinder_id=grinder.id), DesignBrief()
    )


async def _card(app: FastAPI, row: SetRow, name: str = "Soft Bloom") -> tuple[Any, Any]:
    """A first-recipe card whose profile is a new draft under ``name``."""
    db = app.state.db
    base = await make_profile_version(db, "Library profile")
    version = await ProfilesRepository(db).get_version(base)
    assert version is not None and version.profile is not None
    document = {**version.profile, "label": name}
    draft = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=document, is_new=True
    )
    proposal = (
        await SetProposalsRepository(db).create(
            row.id,
            ProposalWrite(
                kind="design",
                draft_id=draft.id,
                reason="A longer, gentler shot.",
                patch=SetVersionPatch(
                    profile_version_id=draft.draft_version_id,
                    grind_setting="20",
                    grind_value=20,
                    dose_g=18,
                    target_yield_g=40,
                ),
            ),
        )
    ).proposal
    assert proposal is not None
    return draft, proposal


async def _another_card(app: FastAPI, row: SetRow, name: str) -> tuple[Any, Any]:
    return await _card(app, row, name)


async def test_a_card_replaced_by_a_newer_one_is_replaced_not_declined(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    first, _ = await _card(app, row, "First Bloom")
    assert (await _standing(client, first))["state"] == "waiting"

    second, _ = await _another_card(app, row, "Second Bloom")

    got = await _standing(client, first)
    assert got["draft"]["status"] == "discarded"
    assert (got["state"], got["reason"]) == ("replaced", words.NEWER_FIRST_RECIPE)
    assert (await _standing(client, second))["state"] == "waiting"


async def test_a_first_version_written_another_way_replaces_the_waiting_recipe(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, _ = await _card(app, row)

    await SetsRepository(app.state.db).add_version(
        row.id, SetVersionPatch(dose_g=18, grind_setting="21")
    )

    got = await _standing(client, draft)
    assert got["draft"]["status"] == "discarded"
    assert (got["state"], got["reason"]) == ("replaced", words.SET_WRITTEN_OTHERWISE)


async def test_a_discarded_design_replaces_the_waiting_recipe(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, _ = await _card(app, row)

    assert await SetsRepository(app.state.db).discard_design(row.id) is None

    got = await _standing(client, draft)
    assert got["draft"]["status"] == "discarded"
    assert (got["state"], got["reason"]) == ("replaced", words.SET_DISCARDED)


async def test_a_card_the_person_declined_is_declined(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row)

    declined = await client.post(
        f"/api/sets/{row.id}/proposals/{proposal.id}/decline", json={"note": "no"}
    )
    assert declined.status_code == 200, declined.text

    got = await _standing(client, draft)
    assert got["draft"]["status"] == "discarded"
    assert (got["state"], got["reason"]) == ("declined", None)
