"""On a profile's own file the brew and the name are decided separately.

The name is the profile's, and the last name the app recorded for the file (``device_label``)
says who changed it: only the app (the next sync saves the name over the file, in place), only
the machine (the profile takes the machine's name, a read), or both (a person chooses). The brew
is decided as it always was. A rename never writes a version, and nothing in these tests renames
a profile from the app's side except by setting the row's name through the repository: the route
that does it is not part of what is tested here.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import BoardRowPatch, ProfileBoardRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile, document_hash
from tests.drafts.conftest import BASE_LABEL, data
from tests.drafts.helpers import APP_LABEL, assert_versions_owned, same_name_draft
from tests.drafts.test_board import (
    adopted,
    app_row,
    edit_on_display,
    events,
    get_board,
    pull,
    put,
    resolve,
    row_for,
    summary_of,
    write_frames,
)
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def rename_in_the_app(app: FastAPI, row_id: int, name: str) -> None:
    await ProfileBoardRepository(app.state.db).update(row_id, BoardRowPatch(label=name))


def rename_at_the_machine(fake: FakeDevice, device_id: str, name: str) -> None:
    next(p for p in fake.profiles if p["id"] == device_id)["label"] = name


def file_called(fake: FakeDevice, device_id: str) -> str:
    return str(next(p for p in fake.profiles if p["id"] == device_id)["label"])


def file_hash(fake: FakeDevice, device_id: str) -> str:
    return document_hash(
        Profile.model_validate(next(p for p in fake.profiles if p["id"] == device_id))
    )


async def versions(app: FastAPI) -> int:
    return int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_versions"))


async def listed(app: FastAPI, row_id: int) -> list[int]:
    return [v.version_id for v in await ProfileBoardRepository(app.state.db).list_versions(row_id)]


async def person(adopted: Adopted) -> tuple[dict[str, Any], str]:
    """The person's own profile, starred and selected, and its file."""
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    file = row["machine"]["device_id"]
    starred = await client.put(
        f"/api/profile-board/{row['row']['id']}/starred", json={"starred": True}
    )
    assert starred.status_code == 200, starred.text
    fake.selected_profile_id = file
    await pull(app)
    assert file in fake.favorite_profile_ids
    return row_for(await get_board(client), BASE_LABEL), file


# ── only the app changed the name ───────────────────────────────────


async def test_a_name_only_the_app_changed_is_saved_over_the_file_at_the_next_sync(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row, file = await person(adopted)
    before, shown = await versions(app), await listed(app, row["row"]["id"])
    await rename_in_the_app(app, row["row"]["id"], "Renamed in the app")
    [planned] = row_for(await get_board(client), "Renamed in the app")["planned"]
    assert (planned["kind"], planned["reason"], planned["device_id"]) == ("push", "renamed", file)
    assert planned["detail"] == "Becomes Renamed in the app on the machine at the next sync"
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    assert write_frames(fake) == ["req:profiles:save"], "one save, no select, favourite or delete"
    assert file_called(fake, file) == "Renamed in the app"
    assert [str(p["id"]) for p in fake.profiles if p["label"] == "Renamed in the app"] == [file]
    assert fake.selected_profile_id == file and file in fake.favorite_profile_ids
    assert [i["reason"] for i in summary_of(run)["pushed"]] == ["renamed"]
    settled = row_for(await get_board(client), "Renamed in the app")
    assert settled["row"]["device_label"] == "Renamed in the app"
    assert settled["machine"]["holds_current"] is True and settled["planned"] == []
    assert await versions(app) == before, "a rename is never a version"
    assert await listed(app, row["row"]["id"]) == shown
    assert (await pull(app)).profiles_changed == 0
    await assert_versions_owned(app.state.db)


async def test_a_rename_made_in_the_app_waits_for_the_switch_and_nothing_is_sent_without_it(
    adopted: Adopted,
) -> None:
    app, _client, fake = adopted
    row, file = await person(adopted)
    await rename_in_the_app(app, row["row"]["id"], "Renamed in the app")
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    fake.ws_requests.clear()

    await pull(app)

    assert write_frames(fake) == [] and file_called(fake, file) == BASE_LABEL
    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    await pull(app)
    assert file_called(fake, file) == "Renamed in the app"


async def test_a_save_of_a_name_is_refused_over_a_file_edited_since_it_was_recorded(
    adopted: Adopted, monkeypatch: Any
) -> None:
    """The guard is the same one: an edit made at the machine is never saved over."""
    app, _client, fake = adopted
    row, file = await person(adopted)
    await rename_in_the_app(app, row["row"]["id"], "Renamed in the app")
    engine = app.state.connection.engine
    real = type(engine)._pull_shots_before_saving

    async def pull_then_edit(self: Any) -> bool:
        done: bool = await real(self)
        await edit_on_display(fake, file)
        return done

    monkeypatch.setattr(type(engine), "_pull_shots_before_saving", pull_then_edit)
    fake.ws_requests.clear()

    run = await pull(app)

    assert "req:profiles:save" not in fake.ws_requests
    assert file_called(fake, file) == BASE_LABEL
    assert [i["device_id"] for i in summary_of(run)["left"]] == [file]


# ── only the machine changed the name ───────────────────────────────


async def test_a_name_only_the_machine_changed_is_taken_and_nothing_is_written(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row, file = await person(adopted)
    before, shown = await versions(app), await listed(app, row["row"]["id"])
    rename_at_the_machine(fake, file, "Renamed at the machine")
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    assert write_frames(fake) == []
    taken = row_for(await get_board(client), "Renamed at the machine")
    assert taken["row"]["id"] == row["row"]["id"] and not taken["in_conflict"]
    assert taken["row"]["device_label"] == "Renamed at the machine"
    assert taken["machine"]["holds_current"] is True and taken["planned"] == []
    [event] = [e for e in await events(app, run.id) if e.kind == "profile_renamed_on_machine"]
    assert json.loads(event.data_json or "{}") == {
        "row_id": row["row"]["id"],
        "from": BASE_LABEL,
        "to": "Renamed at the machine",
    }
    assert [i["reason"] for i in summary_of(run)["renamed"]] == ["renamed_on_machine"]
    assert await versions(app) == before and await listed(app, row["row"]["id"]) == shown
    assert (await pull(app)).profiles_changed == 0
    await assert_versions_owned(app.state.db)


async def test_a_name_the_machine_gave_is_taken_with_the_switch_off_too(
    adopted: Adopted,
) -> None:
    """It is a read, not a write: taken whatever the Writes switch says, and nothing is sent."""
    app, client, fake = adopted
    row, file = await person(adopted)
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    rename_at_the_machine(fake, file, "Renamed at the machine")
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    assert write_frames(fake) == []
    assert run.profiles_changed >= 1
    taken = row_for(await get_board(client), "Renamed at the machine")
    assert taken["row"]["id"] == row["row"]["id"]
    assert taken["row"]["device_label"] == "Renamed at the machine", "the recorded name follows"
    assert [e.kind for e in await events(app, run.id)].count("profile_renamed_on_machine") == 1
    again = await pull(app)
    assert [e.kind for e in await events(app, again.id)].count("profile_renamed_on_machine") == 0


async def test_a_name_the_machine_gave_that_the_app_already_has_is_no_question(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row, file = await person(adopted)
    await rename_in_the_app(app, row["row"]["id"], "Same name both sides")
    rename_at_the_machine(fake, file, "Same name both sides")
    fake.ws_requests.clear()

    run = await pull(app)

    assert summary_of(run)["conflicts"] == [] and write_frames(fake) == []
    assert [e.kind for e in await events(app, run.id)].count("profile_renamed_on_machine") == 0
    settled = row_for(await get_board(client), "Same name both sides")
    assert settled["row"]["device_label"] == "Same name both sides"


# ── both changed it, or the machine's name is another profile's ─────


async def test_a_machine_name_that_is_another_profiles_is_a_conflict_that_names_the_holder(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    mine = row_for(await get_board(client), APP_LABEL)
    other = row_for(await get_board(client), BASE_LABEL)
    rename_at_the_machine(fake, mine["machine"]["device_id"], other["row"]["label"])
    fake.ws_requests.clear()

    run = await pull(app)

    assert [i["label"] for i in summary_of(run)["conflicts"]] == [APP_LABEL]
    assert write_frames(fake) == []
    seen = data(await client.get(f"/api/profile-board/{mine['row']['id']}/conflict"))
    assert seen["machine_name"] == other["row"]["label"]
    assert seen["machine_name_held_by"] == other["row"]["label"]
    assert seen["app_name"] == APP_LABEL and seen["recorded_name"] == APP_LABEL
    refused = await resolve(client, mine["row"]["id"], "machine", seen["machine"]["content_hash"])
    assert refused.status_code == 409
    # Keeping the app's side saves the profile's own name over the file, in place.
    kept = await resolve(client, mine["row"]["id"], "app", seen["machine"]["content_hash"])
    assert kept.status_code == 200, kept.text
    fake.ws_requests.clear()
    await pull(app)
    assert write_frames(fake) == ["req:profiles:save"]
    assert file_called(fake, mine["machine"]["device_id"]) == APP_LABEL


async def test_a_brew_the_profile_never_had_and_a_new_name_are_one_conflict(
    adopted: Adopted,
) -> None:
    """The brew decides: a conflict covers both, and keeping the app's side saves both back."""
    app, client, fake = adopted
    row, file = await person(adopted)
    rename_at_the_machine(fake, file, "Edited and renamed")
    await edit_on_display(fake, file, 3)
    fake.ws_requests.clear()

    run = await pull(app)

    assert [i["label"] for i in summary_of(run)["conflicts"]] == [BASE_LABEL]
    assert write_frames(fake) == []
    assert row_for(await get_board(client), BASE_LABEL)["row"]["label"] == BASE_LABEL
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    assert seen["machine_name"] == "Edited and renamed" and seen["app_name"] == BASE_LABEL
    assert seen["machine_profile"]["temperature"] != seen["app_profile"]["temperature"]
    kept = await resolve(client, row["row"]["id"], "app", seen["machine"]["content_hash"])
    assert kept.status_code == 200, kept.text
    # Nothing was ever recorded as having worked: the version is asked for once more.
    await pull(app)
    held = next(p for p in fake.profiles if p["id"] == file)
    assert (
        held["label"] == BASE_LABEL
        and float(held["temperature"]) != seen["machine_profile"]["temperature"]
    )


# ── a brew the profile has had, with a name difference ──────────────


async def older_brew_on_the_display(
    adopted: Adopted, provider: FakeProvider
) -> tuple[dict[str, Any], str]:
    """A profile with two versions whose file somebody put the older one back on."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    first = row_for(await get_board(client), APP_LABEL)
    file = first["machine"]["device_id"]
    older = {
        k: v for k, v in next(p for p in fake.profiles if p["id"] == file).items() if k != "id"
    }
    await put(client, await same_name_draft(app, client, provider, APP_LABEL, 7.3))
    await pull(app)
    held = next(p for p in fake.profiles if p["id"] == file)
    held.update({k: v for k, v in older.items() if k not in ("favorite", "selected")})
    return row_for(await get_board(client), APP_LABEL), file


async def test_an_older_brew_and_a_name_only_the_app_changed_are_saved_in_one_save(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row, file = await older_brew_on_the_display(adopted, provider)
    await rename_in_the_app(app, row["row"]["id"], "Renamed in the app")
    fake.ws_requests.clear()

    run = await pull(app)

    assert summary_of(run)["conflicts"] == [], "a brew the profile has had is recorded"
    assert write_frames(fake) == ["req:profiles:save"]
    held = next(p for p in fake.profiles if p["id"] == file)
    assert held["label"] == "Renamed in the app"
    assert row_for(await get_board(client), "Renamed in the app")["machine"]["holds_current"]


async def test_an_older_brew_and_a_machine_name_take_the_name_then_save_the_version(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row, file = await older_brew_on_the_display(adopted, provider)
    rename_at_the_machine(fake, file, "Renamed at the machine")
    fake.ws_requests.clear()

    run = await pull(app)

    assert summary_of(run)["conflicts"] == []
    assert write_frames(fake) == ["req:profiles:save"]
    held = next(p for p in fake.profiles if p["id"] == file)
    assert held["label"] == "Renamed at the machine", "the machine's name is kept"
    settled = row_for(await get_board(client), "Renamed at the machine")
    assert settled["machine"]["holds_current"] and settled["row"]["id"] == row["row"]["id"]


async def test_an_older_brew_under_a_name_both_sides_changed_is_a_conflict(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, _client, fake = adopted
    row, file = await older_brew_on_the_display(adopted, provider)
    await rename_in_the_app(app, row["row"]["id"], "Renamed here")
    rename_at_the_machine(fake, file, "Renamed there")
    fake.ws_requests.clear()

    run = await pull(app)

    assert [i["label"] for i in summary_of(run)["conflicts"]] == ["Renamed here"]
    assert write_frames(fake) == []


# ── the planned line ────────────────────────────────────────────────


async def test_a_profile_keeps_its_id_through_a_rename_a_new_version_and_a_rename_at_the_machine(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """The scenario the simulator test runs against the real firmware, on the fake machine: the
    same facts hold on both (the id, the name, the star and the selection after each step)."""
    app, client, fake = adopted
    first, file = await person(adopted)
    row_id = first["row"]["id"]
    versions_before = await versions(app)

    # Renamed in the app: the same id carries the new name, star and selection unchanged.
    await rename_in_the_app(app, row_id, "Renamed in the app")
    run = await pull(app)
    [pushed] = summary_of(run)["pushed"]
    assert pushed["device_id"] == file and pushed["reason"] == "renamed"
    assert summary_of(run)["removed"] == [] and summary_of(run)["writes"] == 1
    assert file_called(fake, file) == "Renamed in the app"
    assert fake.selected_profile_id == file and file in fake.favorite_profile_ids

    # A new version: the same id again.
    await put(client, await same_name_draft(app, client, provider, "Renamed in the app", 7))
    run = await pull(app)
    [pushed] = summary_of(run)["pushed"]
    assert pushed["device_id"] == file and summary_of(run)["removed"] == []
    assert file_called(fake, file) == "Renamed in the app"
    assert fake.selected_profile_id == file and file in fake.favorite_profile_ids

    # Renamed at the machine: the profile takes the name, and nothing is written.
    rename_at_the_machine(fake, file, "Renamed at the machine")
    saves = fake.ws_requests.count("req:profiles:save")
    run = await pull(app)
    summary = summary_of(run)
    assert summary["writes"] == 0 and summary["pushed"] == [] and summary["conflicts"] == []
    assert [i["label"] for i in summary["renamed"]] == ["Renamed at the machine"]
    assert fake.ws_requests.count("req:profiles:save") == saves
    row = row_for(await get_board(client), "Renamed at the machine")
    assert row["row"]["id"] == row_id and row["row"]["device_profile_id"] == file
    assert fake.selected_profile_id == file and file in fake.favorite_profile_ids
    assert (await pull(app)).profiles_changed == 0
    # Two renames and one version: the version is the only one made.
    assert await versions(app) - versions_before == 1


async def test_a_name_taken_between_the_plan_and_the_write_is_not_taken_twice(
    adopted: Adopted, provider: FakeProvider, monkeypatch: Any
) -> None:
    """The name was free when the plan was made; another profile has it by the time the profile
    would take it. It does not (two profiles never share a name), and the next sync asks."""
    from gaggiclanker.drafts import board as board_module

    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    mine = row_for(await get_board(client), BASE_LABEL)
    other = row_for(await get_board(client), APP_LABEL)
    rename_at_the_machine(fake, mine["machine"]["device_id"], "Contested name")
    real = board_module.BoardService._take_names

    async def another_takes_it_first(self: Any, phase: Any, computed: Any) -> None:
        await rename_in_the_app(app, other["row"]["id"], "Contested name")
        await real(self, phase, computed)

    monkeypatch.setattr(board_module.BoardService, "_take_names", another_takes_it_first)

    run = await pull(app)

    monkeypatch.undo()
    assert summary_of(run)["renamed"] == []
    assert [e.kind for e in await events(app, run.id)].count("profile_renamed_on_machine") == 0
    board = await get_board(client)
    assert row_for(board, BASE_LABEL)["row"]["id"] == mine["row"]["id"]
    labels = [r["row"]["label"] for r in board["rows"]]
    assert labels.count("Contested name") == 1
    nxt = await pull(app)
    assert [i["label"] for i in summary_of(nxt)["conflicts"]] == [BASE_LABEL]


async def test_keeping_the_machines_name_over_an_older_brew_takes_the_name_and_leaves_the_brew(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """Both sides renamed a file that also holds an older brew the profile had: the machine's side
    takes the name only (no version, no change of the active one), and the next sync replaces the
    brew in place by its own rule."""
    app, client, fake = adopted
    row, file = await older_brew_on_the_display(adopted, provider)
    await rename_in_the_app(app, row["row"]["id"], "Renamed here")
    rename_at_the_machine(fake, file, "Renamed there")
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    versions_before = await versions(app)
    active_before = row_for(await get_board(client), "Renamed here")["row"]["current_version_id"]

    kept = await resolve(client, row["row"]["id"], "machine", seen["machine"]["content_hash"])

    assert kept.status_code == 200, kept.text
    assert data(kept)["label"] == "Renamed there"
    assert data(kept)["current_version_id"] == active_before
    assert await versions(app) == versions_before
    fake.ws_requests.clear()
    run = await pull(app)
    assert summary_of(run)["conflicts"] == []
    assert write_frames(fake) == ["req:profiles:save"]
    held = next(p for p in fake.profiles if p["id"] == file)
    assert held["label"] == "Renamed there"
    assert row_for(await get_board(client), "Renamed there")["machine"]["holds_current"] is True
