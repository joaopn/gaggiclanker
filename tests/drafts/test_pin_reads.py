"""What the screens read of a pin, field by field.

The Recipe page reads the live pins and which version has one; the Profiles page reads the pin a row
carries the copy of; the recipe pickers read which profile versions are copies.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.recipes import RecipesRepository
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, base_version_id, data
from tests.drafts.test_board import adopted, get_board, row_for
from tests.drafts.test_pins import _pin, _recipe_with_version
from tests.recipes.conftest import make_shot

__all__ = ["adopted"]  # the fixture, re-exported for this module

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _file_shots(app: FastAPI, set_version_id: int, copy: int, original: int) -> None:
    """Three shots under the version: two brewed on the copy, one on the original."""
    recipes = RecipesRepository(app.state.db)
    for number, profile in ((1, copy), (2, copy), (3, original)):
        shot = await make_shot(app.state.db, f"00130{number}", profile_version_id=profile)
        assert await recipes.assign_shot(shot, set_version_id)


async def test_the_recipe_page_lists_live_pins_and_marks_the_version_that_has_one(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    set_id, v1 = await _recipe_with_version(app, client)
    before = data(await client.get(f"/api/sets/{set_id}"))
    assert before["pins"] == [] and before["versions"][0]["pinned"] is None
    data(await client.post(f"/api/sets/{set_id}/versions", json={"dose_g": 18.5}))
    versions = data(await client.get(f"/api/sets/{set_id}"))["versions"]
    (v2,) = [int(v["version"]["id"]) for v in versions if v["version"]["id"] != v1]
    older = data(await _pin(client, set_id, v1))
    newer = data(await _pin(client, set_id, v2))
    original = await base_version_id(app, BASE_LABEL)
    await _file_shots(app, v1, older["pin"]["profile_version_id"], original)

    detail = data(await client.get(f"/api/sets/{set_id}"))

    assert [p["pin_id"] for p in detail["pins"]] == [newer["pin"]["id"], older["pin"]["id"]]
    first = detail["pins"][1]
    assert first == {
        "pin_id": older["pin"]["id"],
        "set_version_id": v1,
        "version_label": "v1",
        "name": f"{BASE_LABEL} v1",
        "board_id": older["board_id"],
        "pinned_at": older["pin"]["pinned_at"],
        "shot_count": 2,
    }
    by_version = {v["version"]["id"]: v for v in detail["versions"]}
    assert by_version[v1]["pinned"] == {"pin_id": older["pin"]["id"], "shot_count": 2}
    assert by_version[v1]["version"]["shot_count"] == 3
    assert by_version[v2]["pinned"] == {"pin_id": newer["pin"]["id"], "shot_count": 0}


async def test_an_unpinned_version_leaves_the_card_and_the_marks(adopted: Adopted) -> None:
    app, client, _fake = adopted
    set_id, v1 = await _recipe_with_version(app, client)
    pin = data(await _pin(client, set_id, v1))["pin"]

    data(await client.post(f"/api/pins/{pin['id']}/unpin"))
    detail = data(await client.get(f"/api/sets/{set_id}"))

    assert detail["pins"] == [] and detail["versions"][0]["pinned"] is None


async def test_the_profile_list_says_which_row_carries_a_pin_and_of_what(adopted: Adopted) -> None:
    app, client, _fake = adopted
    set_id, v1 = await _recipe_with_version(app, client)
    made = data(await _pin(client, set_id, v1))
    original = await base_version_id(app, BASE_LABEL)
    await _file_shots(app, v1, made["pin"]["profile_version_id"], original)

    board = await get_board(client)

    row = row_for(board, f"{BASE_LABEL} v1")
    assert row["pin"] == {
        "pin_id": made["pin"]["id"],
        "set_id": set_id,
        "set_name": "Guji",
        "set_version_id": v1,
        "version_label": "v1",
        "original_label": BASE_LABEL,
        "original_version_id": original,
        "shot_count": 2,
    }
    assert row["active_version"]["shots_brewed"] == 2
    assert [r["pin"] for r in board["rows"] if r["row"]["label"] != f"{BASE_LABEL} v1"] == [
        None
    ] * (len(board["rows"]) - 1)


async def test_the_version_list_marks_the_copy_of_a_pin_even_after_the_unpin(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    set_id, v1 = await _recipe_with_version(app, client)
    made = data(await _pin(client, set_id, v1))
    copy = made["pin"]["profile_version_id"]

    def copies(page: dict[str, object]) -> dict[int, object]:
        items = page["items"]
        assert isinstance(items, list)
        return {i["id"]: i["pin"] for i in items if i["pin"] is not None}

    live = copies(data(await client.get("/api/profile-versions", params={"limit": 500})))
    assert live == {copy: {"pin_id": made["pin"]["id"], "set_id": set_id, "set_version_id": v1}}

    data(await client.post(f"/api/pins/{made['pin']['id']}/unpin"))
    after = copies(data(await client.get("/api/profile-versions", params={"limit": 500})))
    assert after == live
