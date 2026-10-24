"""A Recipe follows its profile: every change of a profile's active version is a version of its
Recipe.

One function does it (``RecipesRepository.follow_profile``), in the same transaction as the change.
These tests walk every path that changes a profile's active version, and the Recipe's own revert,
and after each one the database must satisfy what a Recipe and its profile owe each other
(``assert_recipes_follow_their_profiles``).
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import (
    assert_recipes_follow_their_profiles,
    assert_versions_owned,
    make_recipe_on,
    recipe_version_ids,
    same_name_draft,
)
from tests.drafts.test_board import adopted, edit_on_display, get_board, pull, put, resolve, row_for
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _recipe_on_the_base(
    app: FastAPI, client: httpx.AsyncClient
) -> tuple[int, dict[str, Any]]:
    """A Recipe with a grind, dose and yield on the base profile, and the profile's row."""
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    recipe_id = await make_recipe_on(client, "Followed", row["current_version_id"])
    data(
        await client.post(
            f"/api/recipes/{recipe_id}/versions",
            json={"grind_setting": "21", "grind_value": 21, "intent": "a finer grind"},
        )
    )
    return recipe_id, row


async def _versions(client: httpx.AsyncClient, recipe_id: int) -> list[dict[str, Any]]:
    """The Recipe's versions, oldest first."""
    listed = data(await client.get(f"/api/recipes/{recipe_id}"))["versions"]
    return [entry["version"] for entry in reversed(listed)]


async def _current(client: httpx.AsyncClient, recipe_id: int) -> dict[str, Any]:
    return next(v for v in await _versions(client, recipe_id) if v["is_current"])


async def _older_version_of(client: httpx.AsyncClient, row_id: int) -> int:
    listed = data(await client.get(f"/api/profile-board/{row_id}/versions"))
    active = listed["active_version_id"]
    return int(next(v["version_id"] for v in listed["versions"] if v["version_id"] != active))


async def test_a_recipe_following_its_profile_carries_the_drink_and_the_notes(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    recipe_id, row = await _recipe_on_the_base(app, client)
    data(
        await client.post(
            f"/api/recipes/{recipe_id}/versions",
            json={"drink": "latte", "drink_notes": "oat milk, 200 ml", "intent": "for milk"},
        )
    )
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    older = await _older_version_of(client, row["id"])

    response = await client.put(
        f"/api/profile-board/{row['id']}/active-version", json={"version_id": older}
    )

    assert response.status_code == 200, response.text
    made = await _current(client, recipe_id)
    assert made["profile_version_id"] == older
    assert (made["drink"], made["drink_notes"]) == ("latte", "oat milk, 200 ml")


async def test_making_a_version_active_gives_the_recipe_a_minor_version_naming_it(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    recipe_id, row = await _recipe_on_the_base(app, client)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    before = await _versions(client, recipe_id)
    older = await _older_version_of(client, row["id"])

    response = await client.put(
        f"/api/profile-board/{row['id']}/active-version", json={"version_id": older}
    )

    assert response.status_code == 200, response.text
    after = await _versions(client, recipe_id)
    assert len(after) == len(before) + 1
    made = await _current(client, recipe_id)
    assert made["profile_version_id"] == older and made["version_label"] == "v1.3"
    assert made["parent_version_id"] == before[-1]["id"] and made["origin"] == "manual"
    # Grind, dose, yield and intent are carried from the version the Recipe was on.
    parent = before[-1]
    assert (made["grind_setting"], made["dose_g"], made["target_yield_g"], made["intent"]) == (
        parent["grind_setting"],
        parent["dose_g"],
        parent["target_yield_g"],
        parent["intent"],
    )
    assert made["prediction"] == "" and made["compares_to_version_id"] is None
    await assert_versions_owned(app.state.db)


async def test_making_the_active_version_active_again_adds_nothing(adopted: Adopted) -> None:
    app, client, _ = adopted
    recipe_id, row = await _recipe_on_the_base(app, client)
    before = await recipe_version_ids(app, recipe_id)

    response = await client.put(
        f"/api/profile-board/{row['id']}/active-version",
        json={"version_id": row["current_version_id"]},
    )

    assert response.status_code == 200
    assert await recipe_version_ids(app, recipe_id) == before


async def test_a_profile_with_no_recipe_changes_without_one(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    older = await _older_version_of(client, row["id"])

    response = await client.put(
        f"/api/profile-board/{row['id']}/active-version", json={"version_id": older}
    )

    assert response.status_code == 200
    assert int(await app.state.db.fetch_value("SELECT COUNT(*) FROM recipes")) == 0


async def test_keeping_the_machines_side_of_a_conflict_gives_the_recipe_a_version(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, row = await _recipe_on_the_base(app, client)
    await pull(app)
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    await edit_on_display(fake, file)
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['id']}/conflict"))
    before = await recipe_version_ids(app, recipe_id)

    response = await resolve(client, row["id"], "machine", seen["machine"]["content_hash"])

    assert response.status_code == 200, response.text
    assert len(await recipe_version_ids(app, recipe_id)) == len(before) + 1
    made = await _current(client, recipe_id)
    after = row_for(await get_board(client), BASE_LABEL)["row"]
    assert made["profile_version_id"] == after["current_version_id"]
    await assert_versions_owned(app.state.db)


async def test_keeping_the_apps_side_of_a_conflict_adds_no_version(adopted: Adopted) -> None:
    app, client, fake = adopted
    recipe_id, row = await _recipe_on_the_base(app, client)
    await pull(app)
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    await edit_on_display(fake, file)
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['id']}/conflict"))
    before = await recipe_version_ids(app, recipe_id)

    await resolve(client, row["id"], "app", seen["machine"]["content_hash"])

    assert await recipe_version_ids(app, recipe_id) == before
    await assert_versions_owned(app.state.db)


async def test_an_edit_the_sync_records_changes_no_active_version_and_so_no_recipe_version(
    adopted: Adopted,
) -> None:
    """The sync lists what the machine's file holds as a version of the profile; the active
    version stays the person's choice, so the Recipe has nothing to follow."""
    app, client, fake = adopted
    recipe_id, _ = await _recipe_on_the_base(app, client)
    await pull(app)
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    before = await recipe_version_ids(app, recipe_id)
    await edit_on_display(fake, file)

    await pull(app)

    assert await recipe_version_ids(app, recipe_id) == before
    await assert_versions_owned(app.state.db)


async def test_an_archived_recipe_still_follows_its_profile(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """Archiving changes nothing about the profile: it stays the Recipe's, and the Recipe keeps
    saying what it brews."""
    app, client, _ = adopted
    recipe_id, row = await _recipe_on_the_base(app, client)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    data(await client.post(f"/api/recipes/{recipe_id}/archive"))
    older = await _older_version_of(client, row["id"])

    await client.put(f"/api/profile-board/{row['id']}/active-version", json={"version_id": older})

    assert (await _current(client, recipe_id))["profile_version_id"] == older
    await assert_versions_owned(app.state.db)


# ── the Recipe's own revert ─────────────────────────────────────────────────


async def test_a_revert_makes_the_profile_follow_the_recipe_and_writes_no_version(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    recipe_id, row = await _recipe_on_the_base(app, client)
    first = (await _versions(client, recipe_id))[0]
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    before = await recipe_version_ids(app, recipe_id)
    assert first["profile_version_id"] != row["current_version_id"] or len(before) == 3

    response = await client.post(
        f"/api/recipes/{recipe_id}/rollback",
        json={"to_version_id": first["id"], "note": "too sharp"},
    )

    assert response.status_code == 200, response.text
    assert await recipe_version_ids(app, recipe_id) == before, "going back writes no version"
    current = await _current(client, recipe_id)
    assert current["id"] == first["id"]
    now = row_for(await get_board(client), BASE_LABEL)["row"]
    assert now["current_version_id"] == first["profile_version_id"]
    assert now["previous_version_id"] is not None
    await assert_versions_owned(app.state.db)


async def test_a_revert_that_the_profile_cannot_follow_moves_nothing(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """Everything that can refuse making a version active refuses the revert, as one change."""
    app, client, _ = adopted
    recipe_id, _row = await _recipe_on_the_base(app, client)
    first = (await _versions(client, recipe_id))[0]
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    before = await recipe_version_ids(app, recipe_id)
    # The bounds are tightened since: the older version is outside them.
    await app.state.settings_service.apply({"profilePolicyPressureMaxBar": 6.0})
    current_before = await _current(client, recipe_id)

    response = await client.post(
        f"/api/recipes/{recipe_id}/rollback", json={"to_version_id": first["id"], "note": "x"}
    )

    assert response.status_code == 409, response.text
    assert error(response)["message"].startswith("That version cannot go on the machine")
    assert await _current(client, recipe_id) == current_before
    assert await recipe_version_ids(app, recipe_id) == before
    assert int(await app.state.db.fetch_value("SELECT COUNT(*) FROM recipe_version_reverts")) == 0
    await assert_recipes_follow_their_profiles(app.state.db)


async def test_a_revert_to_the_version_the_profile_already_brews_only_moves_the_pointer(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    recipe_id, row = await _recipe_on_the_base(app, client)
    versions = await _versions(client, recipe_id)
    # Both versions brew the profile's active version: only the Recipe's grind differs.
    assert versions[0]["profile_version_id"] == versions[1]["profile_version_id"]

    response = await client.post(
        f"/api/recipes/{recipe_id}/rollback", json={"to_version_id": versions[0]["id"], "note": ""}
    )

    assert response.status_code == 200, response.text
    now = row_for(await get_board(client), BASE_LABEL)["row"]
    assert now["current_version_id"] == row["current_version_id"]
    await assert_versions_owned(app.state.db)


async def _waiting_proposal(app: FastAPI, recipe_id: int, dose: float) -> None:
    from gaggiclanker.db.repos.recipe_proposals import ProposalWrite, RecipeProposalsRepository
    from gaggiclanker.db.repos.recipes import RecipeVersionPatch

    made = await RecipeProposalsRepository(app.state.db).create(
        recipe_id,
        ProposalWrite(
            patch=RecipeVersionPatch(dose_g=dose),
            reason="A little more.",
            prediction="Compared to the current version: a touch more body, and no slower.",
        ),
    )
    assert made.proposal is not None, made.refused


async def _nothing_waits(app: FastAPI, recipe_id: int) -> bool:
    from gaggiclanker.db.repos.recipe_proposals import RecipeProposalsRepository

    return await RecipeProposalsRepository(app.state.db).waiting(recipe_id) is None


async def test_making_a_version_active_leaves_a_proposal_waiting_on_the_recipe_stale(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    recipe_id, row = await _recipe_on_the_base(app, client)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    older = await _older_version_of(client, row["id"])
    await _waiting_proposal(app, recipe_id, 18.7)

    await client.put(f"/api/profile-board/{row['id']}/active-version", json={"version_id": older})

    assert await _nothing_waits(app, recipe_id)


async def test_keeping_the_machines_side_leaves_a_proposal_waiting_on_the_recipe_stale(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, row = await _recipe_on_the_base(app, client)
    await pull(app)
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    await edit_on_display(fake, file)
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['id']}/conflict"))
    await _waiting_proposal(app, recipe_id, 18.9)

    await resolve(client, row["id"], "machine", seen["machine"]["content_hash"])

    assert await _nothing_waits(app, recipe_id)
