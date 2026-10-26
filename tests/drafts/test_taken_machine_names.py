"""One question, "is this name taken?", asked the same way by every path that can give a profile
a name: typed, given at the display, kept from a conflict, or carried by a file the app has never
seen. Another live profile's machine name (its own name and its Recipe's version) is taken like
its own name; a profile's own machine name never counts against itself.

Each test drives one caller with the name another profile is called on the machine, so a caller
that goes back to comparing plain names fails exactly its own test.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import BoardRowPatch, ProfileBoardRepository
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import grind_change
from tests.drafts.test_board import (
    adopted,
    draft_from,
    edit_on_display,
    events,
    get_board,
    pull,
    put,
    row_for,
    summary_of,
    variant_draft,
)
from tests.drafts.test_brewed_as import shot_for
from tests.drafts.test_machine_names import settled
from tests.drafts.test_names_per_file import file_called, rename_at_the_machine
from tests.drafts.test_pins import _recipe_with_version
from tests.drafts.test_rename import rename
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]

SHOWN = f"{BASE_LABEL} v1"  # what 9 Bar Espresso is called on the machine
OTHER = "Cremina Lever"  # a profile with no Recipe
SENTENCE = f"{SHOWN} is what {BASE_LABEL} is called on the machine. Choose another name."


async def other_row(client: httpx.AsyncClient, label: str = OTHER) -> dict[str, Any]:
    return row_for(await get_board(client), label)


async def test_a_display_rename_to_another_profiles_machine_name_is_a_taken_name_conflict(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    await settled(app, client)
    other = await other_row(client)
    assert (await rename(client, other["row"]["id"], SHOWN)).status_code == 409, "typed: refused"
    rename_at_the_machine(fake, other["machine"]["device_id"], SHOWN)

    run = await pull(app)

    assert run.status == "ok", run.error
    assert summary_of(run)["renamed"] == []
    row = await other_row(client)
    assert row["row"]["label"] == OTHER and row["in_conflict"]
    assert row["conflict"]["name_only"] and row["conflict"]["machine_name"] == SHOWN
    assert row["conflict"]["machine_name_held_by"] == BASE_LABEL
    assert file_called(fake, other["machine"]["device_id"]) == SHOWN, "nothing was written"


async def test_with_writes_off_a_display_rename_to_a_machine_name_is_not_taken_either(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    await settled(app, client)
    other = await other_row(client)
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    rename_at_the_machine(fake, other["machine"]["device_id"], SHOWN)

    await pull(app)

    row = await other_row(client)
    assert row["row"]["label"] == OTHER and row["row"]["device_label"] == OTHER
    assert [r["row"]["label"] for r in (await get_board(client))["rows"]].count(SHOWN) == 0


async def test_a_display_rename_to_the_version_a_recipe_has_just_moved_on_from_is_not_taken(
    adopted: Adopted,
) -> None:
    """The file still holds "9 Bar Espresso v1" when the Recipe moves to v1.1: until the save
    renames it, that is what the profile is called on the machine."""
    app, client, fake = adopted
    await settled(app, client)
    recipe_id = int(await app.state.db.fetch_value("SELECT id FROM recipes ORDER BY id DESC"))
    other = await other_row(client)
    rename_at_the_machine(fake, other["machine"]["device_id"], SHOWN)
    await grind_change(client, recipe_id)
    await app.state.settings_service.apply({"deviceWritesEnabled": False})

    await pull(app)

    row = await other_row(client)
    assert row["row"]["label"] == OTHER and row["in_conflict"]
    assert row["conflict"]["machine_name_held_by"] == BASE_LABEL


async def test_a_name_that_became_another_profiles_machine_name_between_plan_and_write_is_not_taken(
    adopted: Adopted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name was free when the plan was made; the Recipe's profile is renamed so that its
    machine name is that name by the time the profile would take it."""
    from gaggiclanker.drafts import board as board_module

    app, client, fake = adopted
    await settled(app, client)
    mine = await other_row(client)
    other = await other_row(client, BASE_LABEL)
    rename_at_the_machine(fake, mine["machine"]["device_id"], "Contested v1")
    real = board_module.BoardService._take_names

    async def another_takes_it_first(self: Any, phase: Any, computed: Any) -> None:
        # "Contested" with the Recipe's v1 after it is the name the machine file already has.
        await ProfileBoardRepository(app.state.db).update(
            other["row"]["id"], BoardRowPatch(label="Contested")
        )
        await real(self, phase, computed)

    monkeypatch.setattr(board_module.BoardService, "_take_names", another_takes_it_first)

    run = await pull(app)

    monkeypatch.undo()
    assert summary_of(run)["renamed"] == []
    assert [e.kind for e in await events(app, run.id)].count("profile_renamed_on_machine") == 0
    labels = [r["row"]["label"] for r in (await get_board(client))["rows"]]
    assert "Contested v1" not in labels and OTHER in labels


async def test_an_unseen_file_called_another_profiles_machine_name_joins_as_a_second(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    await settled(app, client)
    twin = copy.deepcopy(next(p for p in fake.profiles if p["label"] == SHOWN))
    twin.update(id="newfile1", favorite=False, selected=False)
    fake.profiles.append(twin)

    await pull(app)

    labels = [r["row"]["label"] for r in (await get_board(client))["rows"]]
    assert SHOWN not in labels and f"{SHOWN} (2)" in labels
    assert (
        row_for(await get_board(client), f"{SHOWN} (2)")["row"]["device_profile_id"] == "newfile1"
    )


async def test_keeping_the_machines_name_in_a_both_sides_conflict_refuses_a_machine_name(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    await settled(app, client)
    other = await other_row(client)
    row_id = other["row"]["id"]
    assert (await rename(client, row_id, "Mine")).status_code == 200
    rename_at_the_machine(fake, other["machine"]["device_id"], SHOWN)
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row_id}/conflict"))
    assert seen["machine_name"] == SHOWN and seen["machine"]["name_only"]

    kept = await client.post(
        f"/api/profile-board/{row_id}/conflict",
        json={"keep": "machine", "content_hash": seen["machine"]["content_hash"]},
    )

    assert kept.status_code == 409 and error(kept)["message"] == SENTENCE
    assert (await other_row(client, "Mine"))["row"]["id"] == row_id


async def test_keeping_the_machines_side_of_a_brew_conflict_refuses_a_machine_name(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    await settled(app, client)
    other = await other_row(client)
    row_id = other["row"]["id"]
    rename_at_the_machine(fake, other["machine"]["device_id"], SHOWN)
    await edit_on_display(fake, other["machine"]["device_id"])
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row_id}/conflict"))
    assert seen["machine_name"] == SHOWN and not seen["machine"]["name_only"]

    kept = await client.post(
        f"/api/profile-board/{row_id}/conflict",
        json={"keep": "machine", "content_hash": seen["machine"]["content_hash"]},
    )

    assert kept.status_code == 409 and error(kept)["message"] == SENTENCE
    assert (await other_row(client))["row"]["id"] == row_id


async def test_a_profile_may_keep_its_own_machine_name_when_the_machines_side_is_kept(
    adopted: Adopted,
) -> None:
    """A profile's own machine name never counts against itself: a conflict whose machine side is
    this very profile's name and version is no name conflict at all."""
    app, client, fake = adopted
    row = await settled(app, client)
    file = row["machine"]["device_id"]
    await edit_on_display(fake, file)  # a brew the profile never had, under its own machine name
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    assert seen["machine_name"] == SHOWN

    kept = await client.post(
        f"/api/profile-board/{row['row']['id']}/conflict",
        json={"keep": "machine", "content_hash": seen["machine"]["content_hash"]},
    )

    assert kept.status_code == 200, kept.text
    assert data(kept)["label"] == BASE_LABEL


async def test_a_new_profile_made_from_a_copy_is_refused_a_machine_name(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    await pull(app)

    refused = await client.post(
        f"/api/recipes/{recipe_id}/versions/{version_id}/duplicate",
        json={
            "recipe_name": "Copy",
            "profile_name": SHOWN,
            "insight_ids": [],
            "bean_id": data(await client.get(f"/api/recipes/{recipe_id}"))["recipe"]["bean_id"],
        },
    )

    assert refused.status_code == 409 and error(refused)["message"] == SENTENCE


async def test_the_name_check_and_the_put_of_a_new_profile_refuse_a_machine_name(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    await settled(app, client)
    draft = await variant_draft(app, client, "Fresh", 7)

    check = data(
        await client.post(f"/api/profile-drafts/{draft['id']}/name-check", json={"label": SHOWN})
    )
    refused = await client.post(
        "/api/profile-board", json={"draft_id": draft["id"], "label": SHOWN}
    )

    assert check["refused"] == SENTENCE
    assert refused.status_code == 409 and error(refused)["message"] == SENTENCE
    assert await put(client, draft) is not None


# ── the machine's name in the verification, and who the suffix belongs to ──


async def test_the_verification_records_the_name_that_was_sent_and_loaded(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _fake = adopted
    row = await settled(app, client)
    draft = await draft_from(app, client, provider, row["row"], 7)
    await put(client, draft)

    await pull(app)

    stored = await ProfileDraftsRepository(app.state.db).get(draft["id"])
    assert stored is not None and stored.status == "pushed" and stored.verification is not None
    assert row_for(await get_board(client), BASE_LABEL)["machine_name"] == f"{BASE_LABEL} v1.1"
    assert stored.verification["sent"]["label"] == f"{BASE_LABEL} v1.1"
    assert stored.verification["loaded"]["label"] == f"{BASE_LABEL} v1.1"


async def test_a_profile_with_no_recipe_takes_a_name_that_only_looks_like_a_version(
    adopted: Adopted,
) -> None:
    """The app writes no suffix for a profile no Recipe brews, so any other name is a rename, as it
    always was: "Adaptive v2" renamed to "Adaptive v2 v2" is taken whole."""
    app, client, fake = adopted
    row = await other_row(client, "Adaptive v2")
    rename_at_the_machine(fake, row["machine"]["device_id"], "Adaptive v2 v2")

    run = await pull(app)

    assert [i["label"] for i in summary_of(run)["renamed"]] == ["Adaptive v2 v2"]
    assert (await other_row(client, "Adaptive v2 v2"))["row"]["id"] == row["row"]["id"]
    cremina = await other_row(client)
    rename_at_the_machine(fake, cremina["machine"]["device_id"], f"{OTHER} v3")
    run = await pull(app)
    assert [i["label"] for i in summary_of(run)["renamed"]] == [f"{OTHER} v3"]
    fake.ws_requests.clear()
    await pull(app)
    assert file_called(fake, cremina["machine"]["device_id"]) == f"{OTHER} v3"


async def test_the_suffix_is_the_apps_only_for_a_profile_a_recipe_brews(adopted: Adopted) -> None:
    app, client, fake = adopted
    row = await settled(app, client)
    rename_at_the_machine(fake, row["machine"]["device_id"], f"{BASE_LABEL} v3")

    run = await pull(app)

    assert summary_of(run)["renamed"] == []
    assert file_called(fake, row["machine"]["device_id"]) == SHOWN, "put right, not taken"


async def test_a_shot_in_no_recipe_brewed_under_a_suffixed_name_is_brewed_as_that_name(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    row = await settled(app, client)
    cremina = await other_row(client)
    plain = await shot_for(adopted, cremina["machine"]["device_id"], f"{OTHER} v1", 951)
    filed = await shot_for(adopted, row["machine"]["device_id"], SHOWN, 952)

    assert plain["recipe_badge"] is None and plain["brewed_as"] == f"{OTHER} v1"
    assert filed["recipe_badge"] is not None and filed["brewed_as"] is None


async def joined_labels(adopted: Adopted, name: str) -> list[str]:
    """The Recipe moves on to v1.1 with Writes off, so the plan sees the profile's file still
    called v1 and its machine name v1.1; an unseen file called ``name`` appears beside it."""
    app, client, fake = adopted
    await settled(app, client)
    recipe_id = int(await app.state.db.fetch_value("SELECT id FROM recipes ORDER BY id DESC"))
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    await grind_change(client, recipe_id)
    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    twin = copy.deepcopy(next(p for p in fake.profiles if p["label"] == SHOWN))
    twin.update(id="newfile1", label=name, favorite=False, selected=False)
    fake.profiles.append(twin)

    await pull(app)

    return [r["row"]["label"] for r in (await get_board(client))["rows"]]


async def test_an_unseen_file_called_a_recipes_current_machine_name_does_not_join_under_it(
    adopted: Adopted,
) -> None:
    labels = await joined_labels(adopted, f"{BASE_LABEL} v1.1")

    assert f"{BASE_LABEL} v1.1" not in labels and f"{BASE_LABEL} v1.1 (2)" in labels


async def test_an_unseen_file_called_what_a_recipes_file_is_called_now_does_not_join_under_it(
    adopted: Adopted,
) -> None:
    labels = await joined_labels(adopted, SHOWN)

    assert SHOWN not in labels and f"{SHOWN} (2)" in labels
