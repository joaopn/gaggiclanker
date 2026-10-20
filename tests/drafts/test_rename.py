"""Renaming a profile: the route, what it writes, what it refuses and what the sync then does.

A rename writes the profile's name and nothing else, whatever the Writes switch says and whether or
not the row is a pin's copy. The machine hears of it at the next sync with Writes on (one save over
the profile's own file), which the other files cover; here the route is walked.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import ProfileBoardRepository
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import APP_LABEL
from tests.drafts.test_board import adopted, app_row, get_board, pull, row_for, write_frames
from tests.drafts.test_names_per_file import (
    file_called,
    file_hash,
    rename_at_the_machine,
)
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]

#: Every table a rename could write but must not, besides the profile's own row.
UNTOUCHED = (
    "profile_versions",
    "profile_board_versions",
    "profile_drafts",
    "profile_signatures",
    "signature_expectations",
    "recipes",
    "recipe_versions",
    "recipe_version_pins",
    "device_profiles",
    "device_writes",
)


async def snapshot(app: FastAPI) -> dict[str, list[tuple[Any, ...]]]:
    db = app.state.db
    out: dict[str, list[tuple[Any, ...]]] = {}
    for table in UNTOUCHED:
        rows = await db.fetch_all(f"SELECT * FROM {table} ORDER BY 1")  # noqa: S608 - constants
        out[table] = [tuple(r) for r in rows]
    return out


async def rename(client: httpx.AsyncClient, row_id: int, label: str) -> httpx.Response:
    return await client.put(f"/api/profile-board/{row_id}/name", json={"label": label})


async def own_row(client: httpx.AsyncClient, label: str = BASE_LABEL) -> dict[str, Any]:
    return row_for(await get_board(client), label)


async def test_a_rename_writes_the_profiles_name_and_nothing_else(adopted: Adopted) -> None:
    app, client, fake = adopted
    fake.ws_requests.clear()
    row = await own_row(client)
    before = await snapshot(app)
    stored = dict(row["row"])

    response = await rename(client, row["row"]["id"], "  A new name  ")

    assert response.status_code == 200, response.text
    assert data(response)["label"] == "A new name", "the name is stored trimmed"
    assert await snapshot(app) == before, "no version, draft, Set, pin, signature or file row"
    after = dict((await ProfileBoardRepository(app.state.db).get(row["row"]["id"])).model_dump())  # type: ignore[union-attr]
    changed = {k for k in stored if stored[k] != after[k]}
    assert changed == {"label", "updated_at"}
    assert "req:profiles:save" not in fake.ws_requests, "nothing is sent by the rename itself"


async def test_the_machine_hears_of_it_at_the_next_sync_in_one_save_over_its_own_file(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = await own_row(client)
    file = row["machine"]["device_id"]
    await rename(client, row["row"]["id"], "A new name")
    renamed = await own_row(client, "A new name")
    [planned] = renamed["planned"]
    assert (planned["kind"], planned["reason"]) == ("push", "renamed")
    assert planned["detail"] == "Will be renamed on the machine at the next sync"
    fake.ws_requests.clear()

    await pull(app)

    assert write_frames(fake) == ["req:profiles:save"]
    assert file_called(fake, file) == "A new name"


async def test_an_empty_name_is_unprocessable_and_nothing_changes(adopted: Adopted) -> None:
    app, client, _fake = adopted
    row = await own_row(client)
    before = await snapshot(app)
    for blank in ("", "   ", "\t\n"):
        response = await rename(client, row["row"]["id"], blank)
        assert response.status_code == 422, blank
        assert error(response)["message"] == "A profile needs a name"
    assert (await own_row(client))["row"]["label"] == BASE_LABEL
    assert await snapshot(app) == before


async def test_a_name_another_live_profile_has_is_refused_with_the_persons_sentence(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    other = await own_row(client, APP_LABEL)
    row = await own_row(client)

    for typed in (APP_LABEL, APP_LABEL.upper(), f"  {APP_LABEL.lower()} "):
        response = await rename(client, row["row"]["id"], typed)
        assert response.status_code == 409, typed
        assert error(response)["message"] == (
            f"There is already a profile called {other['row']['label']}. Choose another name."
        ), "the stored name, not what was typed"
    assert (await own_row(client))["row"]["label"] == BASE_LABEL


async def test_the_same_name_is_ok_and_writes_nothing(adopted: Adopted) -> None:
    app, client, _fake = adopted
    row = await own_row(client)
    before = await ProfileBoardRepository(app.state.db).get(row["row"]["id"])
    assert before is not None

    for typed in (BASE_LABEL, f"  {BASE_LABEL}  "):
        response = await rename(client, row["row"]["id"], typed)
        assert response.status_code == 200, response.text

    after = await ProfileBoardRepository(app.state.db).get(row["row"]["id"])
    assert after == before, "not even the row's update time moved"


async def test_a_change_of_case_only_is_a_rename(adopted: Adopted) -> None:
    app, client, fake = adopted
    row = await own_row(client)
    file = row["machine"]["device_id"]

    response = await rename(client, row["row"]["id"], BASE_LABEL.swapcase())

    assert response.status_code == 200, response.text
    assert data(response)["label"] == BASE_LABEL.swapcase()
    await pull(app)
    assert file_called(fake, file) == BASE_LABEL.swapcase()


async def test_the_name_is_at_most_200_characters_as_the_approve_field(adopted: Adopted) -> None:
    _app, client, _fake = adopted
    row = await own_row(client)

    ok = await rename(client, row["row"]["id"], "n" * 200)
    too_long = await rename(client, row["row"]["id"], "n" * 201)

    assert ok.status_code == 200 and too_long.status_code == 400, (
        "a validation refusal, as the Approve field's"
    )
    assert (await own_row(client, "n" * 200))["row"]["id"] == row["row"]["id"]


async def test_it_works_with_writes_off_and_the_machine_follows_when_they_are_on(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = await own_row(client)
    file = row["machine"]["device_id"]
    await app.state.settings_service.apply({"deviceWritesEnabled": False})

    response = await rename(client, row["row"]["id"], "Renamed with writes off")
    assert response.status_code == 200, response.text
    fake.ws_requests.clear()
    await pull(app)
    assert write_frames(fake) == [] and file_called(fake, file) == BASE_LABEL
    [planned] = (await own_row(client, "Renamed with writes off"))["planned"]
    assert planned["detail"] == "Will be renamed on the machine at the next sync"

    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    await pull(app)
    assert file_called(fake, file) == "Renamed with writes off"


async def test_an_unknown_or_deleted_profile_is_not_found(adopted: Adopted) -> None:
    _app, client, _fake = adopted
    assert (await rename(client, 99999, "Anything")).status_code == 404


async def test_a_rename_never_takes_a_name_that_changes_nothing_else(adopted: Adopted) -> None:
    """Extra fields are refused, so a body cannot carry anything but the name."""
    _app, client, _fake = adopted
    row = await own_row(client)
    response = await client.put(
        f"/api/profile-board/{row['row']['id']}/name",
        json={"label": "X", "on_machine": False},
    )
    assert response.status_code == 400


# ── a pinned row ─────────────────────────────────────────────────────


async def test_a_pinned_row_can_be_renamed_and_its_brew_stays_frozen(adopted: Adopted) -> None:
    from tests.drafts.test_pins import _pin, _set_with_version

    app, client, fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    pinned = data(await _pin(client, set_id, version_id))
    row_id = pinned["board_id"]
    before = await snapshot(app)

    response = await rename(client, row_id, "My frozen copy")

    assert response.status_code == 200, response.text
    assert data(response)["label"] == "My frozen copy"
    assert await snapshot(app) == before
    board = await get_board(client)
    assert row_for(board, "My frozen copy")["pin"]["set_id"] == set_id
    await pull(app)
    file = row_for(await get_board(client), "My frozen copy")["machine"]["device_id"]
    assert file_called(fake, file) == "My frozen copy"


# ── a name conflict: renaming is the app's side ─────────────────────


async def test_renaming_a_profile_whose_machine_name_is_taken_keeps_the_apps_side(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    mine = await own_row(client, APP_LABEL)
    other = await own_row(client)
    file = mine["machine"]["device_id"]
    rename_at_the_machine(fake, file, other["row"]["label"])
    await pull(app)
    assert (await own_row(client, APP_LABEL))["in_conflict"]
    fake.ws_requests.clear()

    response = await rename(client, mine["row"]["id"], "Another name entirely")

    assert response.status_code == 200, response.text
    settled = await own_row(client, "Another name entirely")
    assert not settled["in_conflict"], "the rename answered the conflict"
    assert write_frames(fake) == [], "nothing is sent by the rename itself"
    await pull(app)
    assert write_frames(fake) == ["req:profiles:save"]
    assert file_called(fake, file) == "Another name entirely"
    assert not (await own_row(client, "Another name entirely"))["in_conflict"]


async def test_renaming_a_profile_renamed_on_both_sides_keeps_the_apps_side(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = await own_row(client)
    file = row["machine"]["device_id"]
    await rename(client, row["row"]["id"], "Renamed here")
    rename_at_the_machine(fake, file, "Renamed there")
    await pull(app)
    assert (await own_row(client, "Renamed here"))["in_conflict"]

    response = await rename(client, row["row"]["id"], "Renamed here, again")

    assert response.status_code == 200, response.text
    assert not (await own_row(client, "Renamed here, again"))["in_conflict"]
    await pull(app)
    assert file_called(fake, file) == "Renamed here, again"
    assert file_hash(fake, file)


async def test_renaming_a_profile_in_a_brew_conflict_does_not_overrule_the_brew(
    adopted: Adopted,
) -> None:
    from tests.drafts.test_board import edit_on_display

    app, client, fake = adopted
    row = await own_row(client)
    file = row["machine"]["device_id"]
    await edit_on_display(fake, file, 3)
    await pull(app)
    assert (await own_row(client))["in_conflict"]

    response = await rename(client, row["row"]["id"], "Renamed in a brew conflict")

    assert response.status_code == 200, response.text
    assert (await own_row(client, "Renamed in a brew conflict"))["in_conflict"]
    fake.ws_requests.clear()
    await pull(app)
    assert write_frames(fake) == [], "a brew conflict still waits for a person's choice"


async def test_renaming_a_profile_whose_file_has_an_older_brew_and_a_taken_name_answers_it(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """A name conflict is one where the machine's brew is one the profile has had, an older one
    included: renaming answers it, and the next sync saves the active version under the new name."""
    from tests.drafts.test_names_per_file import older_brew_on_the_display

    app, client, fake = adopted
    mine, file = await older_brew_on_the_display(adopted, provider)
    other = await own_row(client)
    rename_at_the_machine(fake, file, other["row"]["label"])
    await pull(app)
    held = await own_row(client, APP_LABEL)
    assert held["in_conflict"]

    response = await rename(client, mine["row"]["id"], "Answered by a rename")

    assert response.status_code == 200, response.text
    assert not (await own_row(client, "Answered by a rename"))["in_conflict"]
    fake.ws_requests.clear()
    await pull(app)
    assert write_frames(fake) == ["req:profiles:save"]
    assert file_called(fake, file) == "Answered by a rename"
    assert (await own_row(client, "Answered by a rename"))["machine"]["holds_current"]


# ── what reads the name ──────────────────────────────────────────────


async def test_the_sets_the_pickers_and_the_version_list_show_the_new_name(
    adopted: Adopted,
) -> None:
    from tests.drafts.test_pins import _set_with_version

    app, client, _fake = adopted
    set_id, _version_id = await _set_with_version(app, client)
    free = next(r for r in (await get_board(client))["rows"] if r["set"] is None)
    brewing = next(r for r in (await get_board(client))["rows"] if r["set"] is not None)
    await rename(client, brewing["row"]["id"], "Renamed brewing profile")
    await rename(client, free["row"]["id"], "Renamed free profile")

    sets = data(await client.get("/api/sets"))
    assert "Renamed brewing profile" in str(sets) and BASE_LABEL not in str(sets)
    detail = data(await client.get(f"/api/sets/{set_id}"))
    assert "Renamed brewing profile" in str(detail) and BASE_LABEL not in str(detail)
    choices = [i["label"] for i in data(await client.get("/api/sets/profile-choices"))["items"]]
    assert "Renamed free profile" in choices and free["row"]["label"] not in choices
    listed = data(await client.get("/api/profile-versions"))["items"]
    names = {i["board_id"]: i["profile_name"] for i in listed}
    assert names[free["row"]["id"]] == "Renamed free profile"
    assert names[brewing["row"]["id"]] == "Renamed brewing profile"
