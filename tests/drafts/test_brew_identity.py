"""A version is what its profile brews, and the name is the profile's.

The board plan compares a machine file as its brew and its name and decides the two separately
(``test_names_per_file`` has the name rules); a brew the profile never had is a conflict whatever
the name. Documents bound for the machine, the editor and the agent are built from a version and the
profile's name. The upgrade's own tests are in ``tests/test_upgrade_level_003.py``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import BoardRowPatch, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository, document_for
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile, brew_hash, document_hash
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import (
    APP_LABEL,
    assert_versions_owned,
    make_recipe_on,
    manual_draft,
    same_name_draft,
)
from tests.drafts.test_board import (
    adopted,
    app_row,
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


def rename_on_display(fake: FakeDevice, device_id: str, name: str) -> dict[str, Any]:
    found = next(p for p in fake.profiles if p["id"] == device_id)
    found["label"] = name
    return found


def file_hash(fake: FakeDevice, device_id: str) -> str:
    found = next(p for p in fake.profiles if p["id"] == device_id)
    return document_hash(Profile.model_validate(found))


async def versions(app: FastAPI) -> int:
    return int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_versions"))


async def renamed_on_the_display(
    adopted: Adopted, provider: FakeProvider, new: str = "Renamed on the display"
) -> tuple[dict[str, Any], str]:
    """An app profile whose file the display renamed (same brew); the sync that sees it gives the
    profile the machine's name."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    device_id = row["machine"]["device_id"]
    rename_on_display(fake, device_id, new)
    await pull(app)
    return row, device_id


async def renamed_on_both_sides(
    adopted: Adopted,
    provider: FakeProvider,
    here: str = "Renamed here",
    there: str = "Renamed there",
) -> tuple[dict[str, Any], str]:
    """An app profile renamed in the app (through the repository) and, since the last sync, at the
    machine too: the sync that sees it asks."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    device_id = row["machine"]["device_id"]
    await ProfileBoardRepository(app.state.db).update(row["row"]["id"], BoardRowPatch(label=here))
    rename_on_display(fake, device_id, there)
    await pull(app)
    return row, device_id


# ── 9: the plan reads a file as its brew and its name ────────────────────


async def test_a_file_with_the_profiles_brew_under_another_name_gives_the_profile_that_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    device_id = row["machine"]["device_id"]
    rename_on_display(fake, device_id, "Renamed on the display")
    before = await versions(app)
    fake.ws_requests.clear()

    run = await pull(app)

    assert summary_of(run)["conflicts"] == []
    assert write_frames(fake) == [], "the machine is not written for a name it gave itself"
    view = row_for(await get_board(client), "Renamed on the display")
    assert view["row"]["id"] == row["row"]["id"] and not view["in_conflict"]
    assert view["row"]["device_label"] == "Renamed on the display"
    assert await versions(app) == before, "a rename is never a version"


async def test_both_sides_renamed_is_a_conflict_over_the_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row, device_id = await renamed_on_both_sides(adopted, provider)
    fake.ws_requests.clear()

    run = await pull(app)

    assert [i["label"] for i in summary_of(run)["conflicts"]] == ["Renamed here"]
    assert write_frames(fake) == []
    view = row_for(await get_board(client), "Renamed here")
    assert view["in_conflict"] and view["planned"] == []
    assert view["conflict"]["content_hash"] == file_hash(fake, device_id)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    # What the conflict is told is the document hash of the machine's file: its brew and its name.
    assert seen["machine"]["content_hash"] == file_hash(fake, device_id)
    assert seen["machine_profile"]["label"] == "Renamed there"
    assert seen["app_profile"]["label"] == "Renamed here"
    assert (seen["app_name"], seen["machine_name"], seen["recorded_name"]) == (
        "Renamed here",
        "Renamed there",
        APP_LABEL,
    )
    assert seen["machine_name_held_by"] is None


async def test_keeping_the_machines_name_makes_no_version_and_takes_the_files_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row, _device_id = await renamed_on_both_sides(adopted, provider)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    before = await versions(app)

    kept = await resolve(client, row["row"]["id"], "machine", seen["machine"]["content_hash"])

    assert kept.status_code == 200, kept.text
    assert await versions(app) == before
    body = data(kept)
    assert body["label"] == "Renamed there"
    assert body["current_version_id"] == row["row"]["current_version_id"]
    assert body["device_label"] == "Renamed there"
    fake.ws_requests.clear()
    run = await pull(app)
    assert summary_of(run)["conflicts"] == [] and write_frames(fake) == []
    await assert_versions_owned(app.state.db)


async def test_keeping_the_machines_name_is_refused_when_a_live_profile_has_it(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    other = row_for(await get_board(client), BASE_LABEL)
    rename_on_display(fake, row["machine"]["device_id"], other["row"]["label"].upper())
    await pull(app)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    assert seen["machine_name_held_by"] == other["row"]["label"]

    refused = await resolve(client, row["row"]["id"], "machine", seen["machine"]["content_hash"])

    assert refused.status_code == 409
    assert error(refused)["details"]["reason"] == "duplicate_label"
    assert row_for(await get_board(client), APP_LABEL)["in_conflict"]


async def test_keeping_the_apps_side_of_a_name_conflict_saves_the_apps_name_over_the_file(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row, device_id = await renamed_on_both_sides(adopted, provider)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))

    kept = await resolve(client, row["row"]["id"], "app", seen["machine"]["content_hash"])

    assert kept.status_code == 200, kept.text
    assert data(kept)["conflict_overruled_hash"] == file_hash(fake, device_id)
    assert data(kept)["label"] == "Renamed here"
    fake.ws_requests.clear()
    # The next sync saves the profile's name over the file, in place, and asks nothing more.
    run = await pull(app)
    assert summary_of(run)["conflicts"] == []
    assert write_frames(fake) == ["req:profiles:save"]
    assert [p["label"] for p in fake.profiles if p["id"] == device_id] == ["Renamed here"]


async def test_a_stale_conflict_hash_is_refused_after_a_second_rename(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row, device_id = await renamed_on_both_sides(adopted, provider)
    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
    rename_on_display(fake, device_id, "Renamed again")
    await pull(app)

    refused = await resolve(client, row["row"]["id"], "machine", seen["machine"]["content_hash"])

    assert refused.status_code == 409
    assert error(refused)["details"]["reason"] == "stale_conflict"


async def test_the_file_the_app_pushed_is_the_profiles_name_and_the_recorded_name_goes_with_it(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)["row"]

    assert (
        row["device_label"] == APP_LABEL and row["device_version_id"] == row["current_version_id"]
    )
    # The recorded name is what the app recorded, not the name the mirror has now: renamed on
    # both sides, the profile is in conflict and the record stays what it was.
    await ProfileBoardRepository(app.state.db).update(row["id"], BoardRowPatch(label="Mine"))
    rename_on_display(fake, row["device_profile_id"], "Somebody else's name")
    await pull(app)
    again = await ProfileBoardRepository(app.state.db).get(row["id"])
    assert again is not None and again.device_label == APP_LABEL


async def test_content_the_profile_has_had_under_its_name_is_not_a_conflict(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """Somebody puts an older version back on the display, keeping the name: recorded, not asked."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    first = row_for(await get_board(client), APP_LABEL)
    older_file = dict(next(p for p in fake.profiles if p["id"] == first["machine"]["device_id"]))
    change = await same_name_draft(app, client, provider, APP_LABEL, 7.3)
    await put(client, change)
    await pull(app)
    row = row_for(await get_board(client), APP_LABEL)
    newer = next(p for p in fake.profiles if p["id"] == row["machine"]["device_id"])
    newer.update({k: v for k, v in older_file.items() if k not in ("id", "favorite", "selected")})

    run = await pull(app)

    assert summary_of(run)["conflicts"] == []
    assert any(r["label"] == APP_LABEL for r in summary_of(run)["recorded"])


async def test_a_file_renamed_back_to_an_older_name_of_its_profile_is_a_plain_rename(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """The name is the profile's and a file is matched by its id: a machine rename to a name the
    profile once had is a rename like any other (the name is free, so the profile takes it)."""
    app, client, fake = adopted
    row, device_id = await renamed_on_the_display(adopted, provider, "The new name")
    rename_on_display(fake, device_id, APP_LABEL)  # the old name, the same brew

    run = await pull(app)

    assert summary_of(run)["conflicts"] == []
    assert not row_for(await get_board(client), APP_LABEL)["in_conflict"]
    assert row_for(await get_board(client), APP_LABEL)["row"]["id"] == row["row"]["id"]


async def test_a_never_seen_file_with_a_known_brew_and_an_older_name_joins_as_a_new_profile(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    _row, device_id = await renamed_on_the_display(adopted, provider, "The new name")
    await pull(app)
    twin = dict(next(p for p in fake.profiles if p["id"] == device_id))
    twin.update({"id": "newfile001", "label": APP_LABEL})
    fake.profiles.append(twin)

    run = await pull(app)

    assert summary_of(run)["conflicts"] == []
    board = await get_board(client)
    assert sorted(
        r["row"]["label"] for r in board["rows"] if r["row"]["label"] in (APP_LABEL, "The new name")
    ) == sorted(["The new name", APP_LABEL])
    await assert_versions_owned(app.state.db)


# ── 10: the machine is sent exactly what it was ──────────────────────────


async def test_a_push_is_the_active_version_under_the_rows_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    _row, _device_id = await renamed_on_the_display(adopted, provider, "The new name")
    await pull(app)
    change = await same_name_draft(app, client, provider, "The new name", 7.7)
    await put(client, change)

    await pull(app)

    pushed = row_for(await get_board(client), "The new name")
    stored = await ProfilesRepository(app.state.db).get_version(pushed["row"]["current_version_id"])
    assert stored is not None
    expected = document_for(stored, "The new name").to_device()
    on_machine = next(p for p in fake.profiles if p["id"] == pushed["machine"]["device_id"])
    assert on_machine["label"] == "The new name"
    for key in ("phases", "temperature", "type", "description"):
        assert json.dumps(on_machine[key], sort_keys=True) == json.dumps(
            expected[key], sort_keys=True
        )


async def test_a_missing_file_is_pushed_under_the_profiles_name_not_the_stored_one(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row, device_id = await renamed_on_the_display(adopted, provider, "The new name")
    await pull(app)
    version = await ProfilesRepository(app.state.db).get_version(row["row"]["current_version_id"])
    assert version is not None and version.label == APP_LABEL  # as first stored
    fake.profiles[:] = [p for p in fake.profiles if p["id"] != device_id]

    run = await pull(app)

    assert [x["label"] for x in summary_of(run)["pushed"]] == ["The new name"]
    recorded = row_for(await get_board(client), "The new name")["row"]
    assert recorded["device_label"] == "The new name"
    pushed = [p for p in fake.profiles if p["label"] in ("The new name", APP_LABEL)]
    assert [p["label"] for p in pushed] == ["The new name"]
    assert document_hash(Profile.model_validate(pushed[0])) == document_hash(
        document_for(version, "The new name")
    )


async def test_a_file_recorded_under_a_name_the_profile_no_longer_has_is_saved_over_not_asked_about(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """The recorded file is its brew and the name it was recorded under (``device_label``), not the
    profile's name now: the display still shows what was recorded, and that is no conflict."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    row = row_for(await get_board(client), APP_LABEL)
    device_id = row["machine"]["device_id"]
    await app.state.db.execute(
        "UPDATE profile_board SET label = ? WHERE id = ?", ("Called this now", row["row"]["id"])
    )

    run = await pull(app)

    assert summary_of(run)["conflicts"] == []
    assert [x["label"] for x in summary_of(run)["pushed"]] == ["Called this now"]
    labels = [p["label"] for p in fake.profiles]
    assert "Called this now" in labels and APP_LABEL not in labels
    assert device_id in [p["id"] for p in fake.profiles], "the same file, saved over in place"


# ── 3: the stored document is what was first stored ──────────────────────


async def test_a_version_keeps_the_document_it_was_first_stored_with_and_is_served_under_the_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _fake = adopted
    row, _device_id = await renamed_on_the_display(adopted, provider, "The new name")
    version_id = row["row"]["current_version_id"]

    stored = json.loads(
        await app.state.db.fetch_value(
            "SELECT json FROM profile_versions WHERE id = ?", (version_id,)
        )
    )
    served = data(await client.get(f"/api/profile-versions/{version_id}"))

    assert stored["label"] == APP_LABEL  # as first stored
    assert served["label"] == "The new name" and served["profile"]["label"] == "The new name"


# ── 11: the editor and the agent start from the profile's name ───────────


async def test_editing_a_version_stored_under_an_old_name_is_a_change_to_its_profile(
    adopted: Adopted, provider: FakeProvider
) -> None:
    _app, client, _fake = adopted
    row, _device_id = await renamed_on_the_display(adopted, provider, "The new name")
    version_id = row["row"]["current_version_id"]
    # What the web editor opens: the version as the board serves it, and as the versions route does.
    board_row = row_for(await get_board(client), "The new name")
    listed = data(await client.get(f"/api/profile-board/{row['row']['id']}/versions"))
    document = next(v["profile"] for v in listed["versions"] if v["version_id"] == version_id)
    assert document["label"] == "The new name"
    assert board_row["active_version"]["profile"]["label"] == "The new name"
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": 6.5, "flow": 0}

    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": version_id, "profile": document, "change_summary": "by hand"},
    )

    created = data(response)
    assert response.status_code == 201, response.text
    assert created["new_profile"] is False
    assert created["destination_board_id"] == row["row"]["id"]
    assert created["draft_label"] == "The new name"


async def test_the_agent_is_shown_the_profiles_name_not_the_first_stored_one(
    adopted: Adopted, provider: FakeProvider
) -> None:
    from gaggiclanker.db.repos.profiles import stored_document

    app, _client, _fake = adopted
    row, _device_id = await renamed_on_the_display(adopted, provider, "The new name")
    profiles = ProfilesRepository(app.state.db)
    version = await profiles.get_version(row["row"]["current_version_id"])
    assert version is not None

    assert await profiles.name_of(version) == "The new name"
    assert stored_document(version, await profiles.name_of(version))["label"] == "The new name"
    base = await app.state.draft_proposals.base_profile(version.id)
    assert base.label == "The new name"


# ── 13: Make active changes only the active version ──────────────────────


async def test_making_an_older_version_active_keeps_the_name_and_has_no_name_refusal(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    # The profile was renamed on the display and the machine's name kept: its first version is
    # stored under a name the profile no longer has.
    row, _ = await renamed_on_the_display(adopted, provider, "The new name")
    first = row["row"]["current_version_id"]
    change = await same_name_draft(app, client, provider, "The new name", 7.3)
    await put(client, change)
    # Another profile takes the name the first version was stored under.
    other = await manual_draft(app, client, BASE_LABEL, APP_LABEL, 6.2)
    await put(client, other)

    made = await client.put(
        f"/api/profile-board/{row['row']['id']}/active-version", json={"version_id": first}
    )

    assert made.status_code == 200, made.text
    assert data(made)["label"] == "The new name" and data(made)["current_version_id"] == first
    await pull(app)
    assert "The new name" in [p["label"] for p in fake.profiles]


# ── 14: a put writes no name from a version, stores no version for one ───


async def test_a_put_stores_no_version_for_a_name_and_leaves_the_profiles_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _fake = adopted
    _row, _device_id = await renamed_on_the_display(adopted, provider, "The new name")
    change = await same_name_draft(app, client, provider, "The new name", 7.7)
    before = await versions(app)

    landed = await put(client, change)

    assert landed["label"] == "The new name"
    # The change brews something new, so it is one new version; nothing more for the name.
    assert await versions(app) - before <= 1
    await assert_versions_owned(app.state.db)


# ── 15: a version is found by its owner and its brew ─────────────────────


async def test_ensure_version_finds_the_same_brew_under_any_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _fake = adopted
    profiles = ProfilesRepository(app.state.db)
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    version = await profiles.get_version(row["current_version_id"])
    assert version is not None
    renamed = document_for(version, "Some other name")

    again, created = await profiles.ensure_version(renamed, owner=row["id"])

    assert created is False and again.id == version.id
    assert brew_hash(renamed) == version.content_hash
    elsewhere, made = await profiles.ensure_version(renamed, owner=None)
    assert made is True and elsewhere.id != version.id


# ── 12: names shown are the profile's ────────────────────────────────────


async def test_the_pin_detail_names_the_copy_by_the_pin_row(
    adopted: Adopted, provider: FakeProvider
) -> None:
    _app, client, _fake = adopted
    base = row_for(await get_board(client), BASE_LABEL)
    set_id = await make_recipe_on(client, "Guji", base["row"]["current_version_id"])
    set_version = data(await client.get(f"/api/sets/{set_id}"))["versions"][0]["version"]["id"]
    made = await client.post(
        f"/api/sets/{set_id}/versions/{set_version}/pin", json={"label": "Guji copy"}
    )
    assert made.status_code < 300, made.text
    pin = data(made)["pin"]

    assert pin["name"] == "Guji copy"
    assert pin["original_label"] == BASE_LABEL


# ── the recorded name is written wherever a file is recorded ─────────────


async def test_adopting_the_machine_records_every_file_under_its_own_name(
    adopted: Adopted,
) -> None:
    _app, client, fake = adopted
    board = await get_board(client)
    names = {p["id"]: p["label"] for p in fake.profiles}
    recorded = [
        (r["row"]["device_profile_id"], r["row"]["device_label"])
        for r in board["rows"]
        if r["row"]["device_profile_id"]
    ]
    assert recorded and all(label == names[device_id] for device_id, label in recorded)
    assert all(r["row"]["device_label"] for r in board["rows"] if r["row"]["device_version_id"])


async def test_a_file_attaching_to_a_profile_is_recorded_under_its_own_name(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """A file the app never saw, called what the profile is called and holding a brew the profile
    has had, attaches to it (no conflict), and what is recorded is that file's name."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider)
    first = row_for(await get_board(client), APP_LABEL)
    older = dict(next(p for p in fake.profiles if p["id"] == first["machine"]["device_id"]))
    await put(client, await same_name_draft(app, client, provider, APP_LABEL, 7.3))
    await pull(app)
    row = row_for(await get_board(client), APP_LABEL)
    fake.profiles[:] = [p for p in fake.profiles if p["id"] != row["machine"]["device_id"]]
    older.update({"id": "returned01", "label": APP_LABEL})
    fake.profiles.append(older)
    await app.state.settings_service.apply({"deviceWritesEnabled": False})

    await pull(app)

    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    run = await pull(app)
    assert summary_of(run)["conflicts"] == []
    attached = row_for(await get_board(client), APP_LABEL)["row"]
    assert attached["device_label"] == APP_LABEL and attached["device_profile_id"] is not None


async def test_a_replacing_push_records_the_profiles_name_beside_the_new_version(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _fake = adopted
    _row, _ = await renamed_on_the_display(adopted, provider, "The new name")
    await pull(app)
    await put(client, await same_name_draft(app, client, provider, "The new name", 7.7))

    await pull(app)

    after = row_for(await get_board(client), "The new name")["row"]
    assert after["device_label"] == "The new name"
    assert after["device_version_id"] == after["current_version_id"]
    await assert_versions_owned(app.state.db)


async def test_a_starred_change_keeps_the_files_own_name_in_the_mirror(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _fake = adopted
    row, device_id = await renamed_on_the_display(adopted, provider, "The new name")
    await pull(app)
    starred = (
        await client.put(f"/api/profile-board/{row['row']['id']}/starred", json={"starred": False})
    ).status_code
    assert starred == 200

    await pull(app)

    mirrored = await app.state.db.fetch_value(
        "SELECT label FROM device_profiles WHERE device_id = ?", (device_id,)
    )
    assert mirrored == "The new name"  # not the label the version was first stored with


# ── a deleted profile comes back by its brew and its name ────────────────


async def test_a_deleted_profile_is_revived_only_by_a_draft_of_its_brew_and_name(
    adopted: Adopted,
) -> None:
    from tests.drafts.helpers import tombstone

    app, client, _fake = adopted
    first = await put(client, await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8))
    await pull(app)
    assert (
        await client.put(f"/api/profile-board/{first['id']}/on-machine", json={"on": False})
    ).is_success
    await tombstone(client, first["id"])

    other = await put(client, await manual_draft(app, client, BASE_LABEL, "Other Bloom", 8))
    assert other["id"] != first["id"], "the same brew under another name is another profile"

    back = await put(client, await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8))
    assert back["id"] == first["id"] and back["deleted_at"] is None
