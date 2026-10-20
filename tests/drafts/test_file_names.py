"""The mirror stores the name each machine file has, every sync, and the machine as the mirror
describes it carries those names."""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.drafts.board import machine_from_mirror
from tests.drafts.conftest import BASE_LABEL
from tests.drafts.helpers import manual_draft
from tests.drafts.test_board import adopted, get_board, pull, put, row_for

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _label_of_file(app: FastAPI, device_id: str) -> str:
    return str(
        await app.state.db.fetch_value(
            "SELECT label FROM device_profiles WHERE device_id = ?", (device_id,)
        )
    )


async def test_every_file_is_mirrored_with_the_name_it_has(adopted: Adopted) -> None:
    app, _client, fake = adopted
    rows = await app.state.db.fetch_all("SELECT device_id, label FROM device_profiles")
    assert {r["device_id"]: r["label"] for r in rows} == {
        str(p["id"]): str(p["label"]) for p in fake.profiles
    }


async def test_a_file_renamed_on_the_display_is_mirrored_under_its_new_name_at_the_next_sync(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    next(p for p in fake.profiles if p["id"] == file)["label"] = "Renamed on the display"

    await pull(app)

    assert await _label_of_file(app, file) == "Renamed on the display"


async def test_a_file_the_app_saved_is_mirrored_under_the_name_it_was_saved_with(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    row = await put(client, await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8))
    await pull(app)
    stored = await app.state.board.board.get(row["id"])
    assert stored is not None
    file = stored.device_profile_id
    assert file is not None and await _label_of_file(app, file) == "Soft Bloom"


async def test_the_listing_of_files_carries_the_stored_name(adopted: Adopted) -> None:
    app, client, _fake = adopted
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    await app.state.db.execute(
        "UPDATE device_profiles SET label = 'Name on the file' WHERE device_id = ?", (file,)
    )
    summary = await ProfilesRepository(app.state.db).get_device_profile_summary(file)
    assert summary is not None and summary.label == "Name on the file"


async def test_the_machine_as_the_mirror_describes_it_has_each_file_under_its_stored_name(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    file = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    await app.state.db.execute(
        "UPDATE device_profiles SET label = 'Name on the file' WHERE device_id = ?", (file,)
    )

    state = await machine_from_mirror(ProfilesRepository(app.state.db))

    assert state.profiles[file].label == "Name on the file"


async def test_the_mirror_is_handed_the_name_the_machine_served_not_one_worked_out_later(
    adopted: Adopted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Until a version's content stops including its name the two texts are equal, so what is
    written is pinned where the name is handed over."""
    app, client, fake = adopted
    calls: list[dict[str, object]] = []
    original = ProfilesRepository.upsert_device_profile

    async def spy(self: ProfilesRepository, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await original(self, **kwargs)

    monkeypatch.setattr(ProfilesRepository, "upsert_device_profile", spy)

    await pull(app)
    assert {c["device_id"]: c["label"] for c in calls} == {
        str(p["id"]): str(p["label"]) for p in fake.profiles
    }

    calls.clear()
    row = await put(client, await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8))
    await pull(app)
    stored = await app.state.board.board.get(row["id"])
    assert stored is not None
    saved = [c for c in calls if c["device_id"] == stored.device_profile_id]
    assert saved and all(c["label"] == "Soft Bloom" for c in saved)


# ── which version a file holds when no profile stands on it ─────────────


def _document(label: str) -> Any:
    from gaggiclanker.domain.models import Profile
    from tests.drafts.conftest import profile_fixture

    document = profile_fixture("docs-medium-18g")
    document["label"] = label
    # A version is what is brewed, whatever it is called: the description makes two names two brews.
    document["description"] = label
    return Profile.model_validate(document)


async def test_a_file_no_profile_stands_on_holds_the_version_exactly_one_profile_owns(
    adopted: Adopted,
) -> None:
    from gaggiclanker.db.repos.profile_board import BoardRowWrite, ProfileBoardRepository

    app, _client, _ = adopted
    profiles = ProfilesRepository(app.state.db)
    board = ProfileBoardRepository(app.state.db)
    document = _document("Held by one")
    loose, _ = await profiles.ensure_version(document, owner=None)
    row = await board.insert(
        BoardRowWrite(label="Held by one", current_version_id=loose.id, origin="adopted")
    )

    version, inserted = await profiles.version_for_file(document, "an-extra-copy")

    assert (version.id, version.board_id, inserted) == (loose.id, row.id, False)
    versions = await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_versions")
    again, _ = await profiles.version_for_file(document, "an-extra-copy")
    assert again.id == loose.id
    assert await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_versions") == versions


async def test_a_file_nobody_or_several_profiles_own_the_content_of_holds_a_loose_version(
    adopted: Adopted,
) -> None:
    from gaggiclanker.db.repos.profile_board import BoardRowWrite, ProfileBoardRepository

    app, _client, _ = adopted
    profiles = ProfilesRepository(app.state.db)
    board = ProfileBoardRepository(app.state.db)

    nobody, inserted = await profiles.version_for_file(_document("Nobody has it"), "file-a")
    assert (nobody.board_id, inserted) == (None, True)

    shared = _document("Shared brew")
    first, _ = await profiles.ensure_version(shared, owner=None)
    await board.insert(BoardRowWrite(label="One", current_version_id=first.id, origin="adopted"))
    second_start, _ = await profiles.ensure_version(_document("Other"), owner=None)
    second = await board.insert(
        BoardRowWrite(label="Two", current_version_id=second_start.id, origin="adopted")
    )
    await board.add_version(second.id, first.id, "edit")  # a copy of the same brew, owned by Two

    several, inserted = await profiles.version_for_file(shared, "file-b")
    assert (several.board_id, inserted) == (None, True)


async def test_a_second_file_with_a_profiles_content_is_not_news_and_holds_its_own_profiles_version(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    extra = copy.deepcopy(next(p for p in fake.profiles if p["id"] == row["machine"]["device_id"]))
    extra["id"] = "extra-copy"
    fake.profiles.append(extra)
    await pull(app)

    second = await pull(app)

    twin = row_for(await get_board(client), f"{BASE_LABEL} (2)")["row"]
    held = await app.state.db.fetch_value(
        "SELECT current_version_id FROM device_profiles WHERE device_id = 'extra-copy'"
    )
    assert twin["device_profile_id"] == "extra-copy"
    assert held == twin["current_version_id"] != row["row"]["current_version_id"]
    assert second.profiles_changed == 1, "the one write is the new name saved over its file"
    assert (await pull(app)).profiles_changed == 0


async def test_attaching_a_file_moves_only_that_files_shots_to_the_profiles_version(
    adopted: Adopted,
) -> None:
    from tests.recipes.conftest import make_shot

    app, _client, _ = adopted
    profiles = ProfilesRepository(app.state.db)
    old, _ = await profiles.ensure_version(_document("Moves"), owner=None)
    mine = await profiles.ensure_version(_document("Moves too"), owner=None)
    await profiles.upsert_device_profile(device_id="file-x", version_id=old.id)
    here = await make_shot(
        app.state.db, "000901", profile_id_on_device="file-x", profile_version_id=old.id
    )
    elsewhere = await make_shot(
        app.state.db, "000902", profile_id_on_device="file-y", profile_version_id=old.id
    )

    await profiles.point_file_at("file-x", mine[0].id)

    linked = {
        r["id"]: r["profile_version_id"]
        for r in await app.state.db.fetch_all("SELECT id, profile_version_id FROM shots")
    }
    assert linked[here] == mine[0].id and linked[elsewhere] == old.id


async def test_a_file_that_joins_as_a_new_profile_holds_the_new_profiles_own_version(
    adopted: Adopted,
) -> None:
    """The content is a deleted profile's: the mirror maps the unstood file to the one version a
    profile owns with it, and the profile the file then becomes takes it over."""
    from tests.drafts.helpers import tombstone

    app, client, fake = adopted
    gone = row_for(await get_board(client), BASE_LABEL)
    extra = copy.deepcopy(next(p for p in fake.profiles if p["id"] == gone["machine"]["device_id"]))
    extra["id"] = "joins-later"
    fake.profiles.append(extra)
    await tombstone(client, gone["row"]["id"])

    await pull(app)

    joined = await app.state.db.fetch_one(
        "SELECT b.id, b.current_version_id, d.current_version_id AS held FROM profile_board b "
        "JOIN device_profiles d ON d.device_id = b.device_profile_id "
        "WHERE b.device_profile_id = 'joins-later' AND b.deleted_at IS NULL"
    )
    assert joined is not None and joined["held"] == joined["current_version_id"]
    owner = await app.state.db.fetch_value(
        "SELECT board_id FROM profile_versions WHERE id = ?", (joined["held"],)
    )
    assert owner == joined["id"]
