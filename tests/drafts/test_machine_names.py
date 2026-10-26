"""A profile a Recipe brews shows its Recipe's version after its name on the machine.

The name a person gave the profile never carries it: the machine's name is derived, sent through
the sync's one in-place save, recorded exactly for the file, and read back without taking the
version for a rename.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import grind_change
from tests.drafts.test_board import (
    adopted,
    app_row,
    edit_on_display,
    events,
    get_board,
    pull,
    put,
    row_for,
    summary_of,
    variant_draft,
    write_frames,
)
from tests.drafts.test_brewed_as import shot_for
from tests.drafts.test_names_per_file import file_called, rename_at_the_machine
from tests.drafts.test_pins import _pin, _recipe_with_version
from tests.drafts.test_rename import own_row, rename
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]

V1 = f"{BASE_LABEL} v1"
V11 = f"{BASE_LABEL} v1.1"


async def settled(app: FastAPI, client: httpx.AsyncClient) -> dict[str, Any]:
    """A Recipe on the base profile, synced: the machine shows v1."""
    await _recipe_with_version(app, client)
    run = await pull(app)
    assert run.status == "ok", run.error
    return await own_row(client)


async def test_a_profile_a_recipe_brews_shows_its_version_on_the_machine(adopted: Adopted) -> None:
    app, client, fake = adopted
    await _recipe_with_version(app, client)
    before = await own_row(client)
    file = before["machine"]["device_id"]
    assert before["machine_name"] == V1
    [planned] = before["planned"]
    assert (planned["kind"], planned["reason"], planned["device_id"]) == ("push", "renamed", file)
    assert planned["detail"] == f"Becomes {V1} on the machine at the next sync"

    run = await pull(app)

    assert run.status == "ok", run.error
    assert file_called(fake, file) == V1
    row = await own_row(client)
    assert row["row"]["device_profile_id"] == file and row["machine"]["holds_current"]
    # The profile's own name never carries it: not the row, not any version it has had.
    assert row["row"]["label"] == BASE_LABEL and row["row"]["device_label"] == V1
    labels = [
        str(r["label"])
        for r in await app.state.db.fetch_all(
            "SELECT label FROM profile_versions WHERE board_id = ?", (row["row"]["id"],)
        )
    ]
    assert labels and not any(label.endswith(" v1") for label in labels)
    assert (await get_board(client))["actions"] == []


async def test_a_profile_with_no_recipe_keeps_its_plain_name(adopted: Adopted) -> None:
    app, client, fake = adopted
    await _recipe_with_version(app, client)
    fake.ws_requests.clear()
    await pull(app)

    board = await get_board(client)
    plain = [r for r in board["rows"] if r["row"]["label"] != BASE_LABEL]
    assert plain and all(r["machine_name"] == r["row"]["label"] for r in plain)
    saved = [r for r in fake.ws_requests if r == "req:profiles:save"]
    assert len(saved) == 1, "only the Recipe's profile is saved"


async def test_a_pins_copy_keeps_its_own_name_with_no_second_suffix(adopted: Adopted) -> None:
    app, client, fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    pin = data(await _pin(client, recipe_id, version_id))["pin"]
    await pull(app)

    row = row_for(await get_board(client), pin["name"])
    assert row["machine_name"] == pin["name"] == V1
    assert file_called(fake, row["machine"]["device_id"]) == V1
    assert row["machine"]["holds_current"]
    assert (await get_board(client))["actions"] == []


async def test_an_archived_recipe_keeps_its_last_version_on_the_machine(adopted: Adopted) -> None:
    app, client, fake = adopted
    recipe_id, _ = await _recipe_with_version(app, client)
    await grind_change(client, recipe_id)
    data(await client.post(f"/api/recipes/{recipe_id}/archive"))

    row = await own_row(client)
    assert row["machine_name"] == V11 and row["recipe"] is None
    await pull(app)
    assert file_called(fake, row["machine"]["device_id"]) == V11


async def test_a_new_current_version_renames_the_file_in_place_at_the_next_sync(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = await settled(app, client)
    file = row["machine"]["device_id"]
    fake.selected_profile_id = file
    starred = await client.put(
        f"/api/profile-board/{row['row']['id']}/starred", json={"starred": True}
    )
    assert starred.status_code == 200, starred.text
    await pull(app)
    assert file in fake.favorite_profile_ids
    recipe_id = int(await app.state.db.fetch_value("SELECT id FROM recipes ORDER BY id DESC"))
    shots_before = (await own_row(client))["active_version"]["shots_brewed"]

    await grind_change(client, recipe_id)  # a version that changes only the grind
    planned_row = await own_row(client)
    [planned] = planned_row["planned"]
    assert planned["detail"] == f"Becomes {V11} on the machine at the next sync"
    fake.ws_requests.clear()
    run = await pull(app)

    assert run.status == "ok", run.error
    assert fake.ws_requests.count("req:profiles:save") == 1
    assert [i["reason"] for i in summary_of(run)["pushed"]] == ["renamed"]
    assert file_called(fake, file) == V11
    assert [str(p["id"]) for p in fake.profiles].count(file) == 1, "saved over its own file"
    assert fake.selected_profile_id == file and file in fake.favorite_profile_ids
    after = await own_row(client)
    assert after["row"]["label"] == BASE_LABEL and after["row"]["device_label"] == V11
    assert after["active_version"]["shots_brewed"] == shots_before


async def test_nothing_changed_plans_no_write_and_logs_no_change(adopted: Adopted) -> None:
    app, client, fake = adopted
    row = await settled(app, client)
    recipe_id = int(await app.state.db.fetch_value("SELECT id FROM recipes ORDER BY id DESC"))
    await grind_change(client, recipe_id)
    await pull(app)

    for _ in range(2):
        fake.ws_requests.clear()
        run = await pull(app)
        assert run.status == "ok" and run.profiles_changed == 0, run.error
        assert write_frames(fake) == []
        assert "profile_changed" not in [e.kind for e in await events(app, run.id)]
        assert (await get_board(client))["actions"] == []
    assert file_called(fake, row["machine"]["device_id"]) == V11


async def test_with_writes_off_the_new_name_waits_and_nothing_is_sent(adopted: Adopted) -> None:
    app, client, fake = adopted
    row = await settled(app, client)
    file = row["machine"]["device_id"]
    recipe_id = int(await app.state.db.fetch_value("SELECT id FROM recipes ORDER BY id DESC"))
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    await grind_change(client, recipe_id)
    fake.ws_requests.clear()

    for _ in range(2):
        run = await pull(app)
        assert run.status == "ok", run.error
        assert write_frames(fake) == [] and file_called(fake, file) == V1
        row = await own_row(client)
        assert row["row"]["label"] == BASE_LABEL, "the suffix is never taken as a rename"
        [planned] = row["planned"]
        assert planned["detail"] == f"Becomes {V11} on the machine at the next sync"

    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    await pull(app)
    assert file_called(fake, file) == V11


async def test_a_stale_or_edited_suffix_is_put_right_and_never_taken_as_the_name(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = await settled(app, client)
    file = row["machine"]["device_id"]
    recipe_id = int(await app.state.db.fetch_value("SELECT id FROM recipes ORDER BY id DESC"))
    await grind_change(client, recipe_id)
    await pull(app)

    for shown in (f"{BASE_LABEL} v1", f"{BASE_LABEL} v9", f"{BASE_LABEL} v2.10"):
        rename_at_the_machine(fake, file, shown)
        run = await pull(app)
        assert run.status == "ok", run.error
        assert summary_of(run)["renamed"] == [] and summary_of(run)["conflicts"] == []
        assert [i["reason"] for i in summary_of(run)["pushed"]] == ["renamed"]
        assert file_called(fake, file) == V11
        assert (await own_row(client))["row"]["label"] == BASE_LABEL
        fake.ws_requests.clear()
        again = await pull(app)
        assert write_frames(fake) == [] and again.profiles_changed == 0


async def test_a_rename_at_the_display_loses_only_the_apps_own_suffix(adopted: Adopted) -> None:
    app, client, fake = adopted
    row = await settled(app, client)
    file = row["machine"]["device_id"]

    # "Y v1": the app's own suffix is cut, the name is Y, and the file already holds it.
    rename_at_the_machine(fake, file, "Y v1")
    fake.ws_requests.clear()
    run = await pull(app)
    assert [i["label"] for i in summary_of(run)["renamed"]] == ["Y"]
    assert write_frames(fake) == [] and file_called(fake, file) == "Y v1"
    row = await own_row(client, "Y")
    assert row["row"]["device_label"] == "Y v1" and row["machine_name"] == "Y v1"
    again = await pull(app)
    assert write_frames(fake) == [] and again.profiles_changed == 0

    # "Z": no suffix to cut; the profile is Z and the next sync writes "Z v1".
    rename_at_the_machine(fake, file, "Z")
    run = await pull(app)
    assert [i["label"] for i in summary_of(run)["renamed"]] == ["Z"]
    assert file_called(fake, file) == "Z"
    run = await pull(app)
    assert file_called(fake, file) == "Z v1"
    fake.ws_requests.clear()
    again = await pull(app)
    assert write_frames(fake) == [] and again.profiles_changed == 0
    assert (await own_row(client, "Z"))["row"]["label"] == "Z"


async def test_a_name_the_machine_cut_to_a_stale_suffix_is_cut_too(adopted: Adopted) -> None:
    """The Recipe moved on since the last save: "Y v1" is "Y" plus the recorded suffix."""
    app, client, fake = adopted
    row = await settled(app, client)
    file = row["machine"]["device_id"]
    recipe_id = int(await app.state.db.fetch_value("SELECT id FROM recipes ORDER BY id DESC"))
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    await grind_change(client, recipe_id)
    rename_at_the_machine(fake, file, "Y v1")

    await pull(app)

    assert (await own_row(client, "Y"))["row"]["device_label"] == "Y v1"
    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    await pull(app)
    assert file_called(fake, file) == "Y v1.1"


async def test_a_name_that_merely_looks_like_a_version_is_never_cut(adopted: Adopted) -> None:
    app, client, fake = adopted
    row = await own_row(client)  # no Recipe: the app never added a suffix
    file = row["machine"]["device_id"]
    rename_at_the_machine(fake, file, "Foo v1.3")

    run = await pull(app)

    assert [i["label"] for i in summary_of(run)["renamed"]] == ["Foo v1.3"]
    assert (await own_row(client, "Foo v1.3"))["row"]["id"] == row["row"]["id"]


async def test_two_files_with_one_machine_name_are_allowed_and_each_keeps_its_version(
    adopted: Adopted,
) -> None:
    """Pin the version the Recipe is on: two files are called the same. A new Recipe version
    renames the main one only, and a shot on each file is filed under its own version."""
    app, client, fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    pin = data(await _pin(client, recipe_id, version_id))["pin"]
    run = await pull(app)
    assert run.status == "ok", run.error
    main = await own_row(client)
    pinned = row_for(await get_board(client), pin["name"])
    assert main["machine_name"] == pinned["machine_name"] == V1
    assert [p["label"] for p in fake.profiles].count(V1) == 2
    assert (await get_board(client))["actions"] == [] and summary_of(run)["conflicts"] == []

    await grind_change(client, recipe_id)
    fake.ws_requests.clear()
    run = await pull(app)

    assert run.status == "ok", run.error
    assert fake.ws_requests.count("req:profiles:save") == 1
    assert file_called(fake, main["machine"]["device_id"]) == V11
    assert file_called(fake, pinned["machine"]["device_id"]) == V1
    assert summary_of(run)["conflicts"] == [] and summary_of(run)["renamed"] == []
    again = await pull(app)
    assert again.profiles_changed == 0 and (await get_board(client))["actions"] == []

    on_main = await shot_for(adopted, main["machine"]["device_id"], V11, 801)
    on_pin = await shot_for(adopted, pinned["machine"]["device_id"], V1, 802)
    assert on_main["recipe_badge"]["version_label"] == "v1.1"
    assert on_pin["recipe_badge"]["version_label"] == "v1"
    assert on_main["brewed_as"] is None and on_pin["brewed_as"] is None


async def test_a_shot_brewed_after_the_rename_is_filed_under_the_current_version(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    row = await settled(app, client)
    shot = await shot_for(adopted, row["machine"]["device_id"], V1, 811)
    assert shot["recipe_badge"]["version_label"] == "v1"
    assert shot["profile_label"] == BASE_LABEL and shot["brewed_as"] is None
    assert shot["profile_name_on_device"] == V1


# ── the name check ───────────────────────────────────────────────────


async def test_a_rename_may_not_take_what_another_profile_is_called_on_the_machine(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _fake = adopted
    await settled(app, client)
    other = await app_row(app, client, _fake, provider, 7, name="Second")

    refused = await rename(client, other["id"], V1)

    assert refused.status_code == 409
    assert error(refused)["message"] == (
        f"{V1} is what {BASE_LABEL} is called on the machine. Choose another name."
    )
    own = await rename(client, (await own_row(client))["row"]["id"], V1)
    assert own.status_code == 200, "a profile's own machine name is not a conflict with itself"


async def test_a_new_profile_may_not_take_what_another_profile_is_called_on_the_machine(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _fake = adopted
    await settled(app, client)
    draft = await variant_draft(app, client, "Fresh", 7)

    check = data(
        await client.post(f"/api/profile-drafts/{draft['id']}/name-check", json={"label": V1})
    )
    put_refused = await client.post(
        "/api/profile-board", json={"draft_id": draft["id"], "label": V1}
    )

    sentence = f"{V1} is what {BASE_LABEL} is called on the machine. Choose another name."
    assert check["refused"] == sentence
    assert put_refused.status_code == 409 and error(put_refused)["message"] == sentence
    free = data(
        await client.post(f"/api/profile-drafts/{draft['id']}/name-check", json={"label": "Fresh"})
    )
    assert free["refused"] is None
    assert await put(client, draft) is not None


# ── a conflict ───────────────────────────────────────────────────────


async def test_keeping_the_machines_side_of_a_name_conflict_takes_the_name_without_the_suffix(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = await settled(app, client)
    file = row["machine"]["device_id"]
    row_id = row["row"]["id"]
    assert (await rename(client, row_id, "Mine")).status_code == 200
    rename_at_the_machine(fake, file, "Theirs v1")
    await pull(app)

    seen = data(await client.get(f"/api/profile-board/{row_id}/conflict"))
    assert (seen["app_name"], seen["machine_name"]) == ("Mine v1", "Theirs v1")
    kept = await client.post(
        f"/api/profile-board/{row_id}/conflict",
        json={"keep": "machine", "content_hash": seen["machine"]["content_hash"]},
    )

    assert kept.status_code == 200, kept.text
    assert data(kept)["label"] == "Theirs" and data(kept)["device_label"] == "Theirs v1"
    run = await pull(app)
    assert run.status == "ok" and file_called(fake, file) == "Theirs v1"


async def test_a_version_made_from_a_file_edited_at_the_display_is_stored_without_the_suffix(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = await settled(app, client)
    file = row["machine"]["device_id"]
    await edit_on_display(fake, file)
    await pull(app)  # the file holds a brew the profile never had: recorded as a version

    stored = [
        str(r["label"])
        for r in await app.state.db.fetch_all(
            "SELECT label FROM profile_versions WHERE board_id = ?", (row["row"]["id"],)
        )
    ]
    assert len(stored) >= 2 and not any(label.endswith(" v1") for label in stored)
    assert (await own_row(client))["row"]["label"] == BASE_LABEL


async def test_a_display_edit_then_an_app_rename_stores_no_suffixed_version_label(
    adopted: Adopted,
) -> None:
    """The file is called "9 Bar Espresso v1" when it is edited and the profile renamed before the
    next sync: the version made from the file is stored under the profile's name, not under the
    name the file had."""
    app, client, fake = adopted
    row = await settled(app, client)
    await edit_on_display(fake, row["machine"]["device_id"])
    assert (await rename(client, row["row"]["id"], "Renamed")).status_code == 200

    await pull(app)

    stored = [
        str(r["label"])
        for r in await app.state.db.fetch_all(
            "SELECT label FROM profile_versions WHERE board_id = ?", (row["row"]["id"],)
        )
    ]
    assert len(stored) >= 2 and f"{BASE_LABEL} v1" not in stored


async def test_a_version_is_stored_without_the_suffix_only_for_a_profile_a_recipe_brews(
    adopted: Adopted,
) -> None:
    from gaggiclanker.db.repos.profiles import ProfilesRepository, document_for

    app, client, _fake = adopted
    recipe_row = await settled(app, client)
    plain_row = await own_row(client, "Cremina Lever")
    profiles = ProfilesRepository(app.state.db)

    async def stored_as(row: dict[str, Any], name: str) -> str:
        version = await profiles.get_version(row["row"]["current_version_id"])
        assert version is not None
        named = await profiles.named_as_stored(document_for(version, name), int(row["row"]["id"]))
        return named.label

    assert await stored_as(recipe_row, f"{BASE_LABEL} v7") == BASE_LABEL
    assert await stored_as(recipe_row, V1) == BASE_LABEL
    # No Recipe, no suffix: the document keeps the name it came with.
    assert await stored_as(plain_row, "Cremina Lever v1") == "Cremina Lever v1"
    assert await stored_as(plain_row, "Cremina Lever") == "Cremina Lever"
