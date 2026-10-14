"""A version on no profile is the document it came as.

A machine file no profile stands on (the whole machine while Writes are off), an imported file, a
waiting draft for a new profile, a pin's copy and a Duplicate's profile have no profile name, so
each is its own version by brew **and** the name it carries: a second one with the same brew under
another name is not the first. Only a profile's own versions are one by brew alone. Each test is a
state the profile-owned reading of a brew would have broken: the Set on a machine file's version
keeps its version, and the shots brewed on that file keep filing under it.
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from gaggiclanker.device.fake import FakeDevice, build_fake_device
from tests.drafts.conftest import BASE_LABEL, FIXTURES, base_profile, base_version_id, data
from tests.drafts.helpers import assert_versions_owned, make_set_on, manual_draft
from tests.drafts.test_board import WRITE_FRAMES, get_board, pull, put, row_for

#: The file id the fixture shot 129 was brewed on.
SHOT_FILE = "rV4GhUcSZc"


@pytest.fixture
async def fake_device() -> AsyncIterator[FakeDevice]:
    device = build_fake_device(FIXTURES)
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


async def owner_of(app: Any, version_id: int) -> str | None:
    found = await app.state.db.fetch_value(
        "SELECT b.label FROM profile_versions v LEFT JOIN profile_board b ON b.id = v.board_id "
        "WHERE v.id = ?",
        (version_id,),
    )
    return None if found is None else str(found)


async def shot_filed_under(app: Any, client: Any, device_id: str = "000129") -> str | None:
    shots = data(await client.get("/api/shots?limit=200"))["items"]
    found = next(s for s in shots if s["device_id"] == device_id)
    if not found.get("set_version_id"):
        return None
    return str(
        await app.state.db.fetch_value(
            "SELECT st.name FROM set_versions v JOIN sets st ON st.id = v.set_id WHERE v.id = ?",
            (found["set_version_id"],),
        )
    )


async def the_nine_bar_file_brews_shot_129(app: Any, fake: FakeDevice) -> None:
    nine = next(p for p in fake.profiles if p["label"] == BASE_LABEL)
    nine["id"] = SHOT_FILE
    await pull(app)
    await app.state.connection.engine.sync_shots(kind="backfill", trigger="test")


async def test_a_copy_made_on_the_display_while_writes_are_off_is_not_the_originals_version(
    writes_on: Any, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    nine = row_for(await get_board(client), BASE_LABEL)
    await make_set_on(client, "Nine set", int(nine["row"]["current_version_id"]))
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    twin = copy.deepcopy(next(p for p in fake_device.profiles if p["label"] == BASE_LABEL))
    twin.update(id=SHOT_FILE, label="9 Bar copy", favorite=False, selected=False)
    fake_device.profiles.append(twin)

    await pull(app)
    await app.state.connection.engine.sync_shots(kind="backfill", trigger="test")

    version = await app.state.db.fetch_value(
        "SELECT current_version_id FROM device_profiles WHERE device_id = ?", (SHOT_FILE,)
    )
    assert await owner_of(app, int(version)) is None
    assert await shot_filed_under(app, client) is None
    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    await pull(app)
    # The copy joins the list as the profile its file is, and its shot is that profile's.
    row = row_for(await get_board(client), "9 Bar copy")
    assert row["machine"]["device_id"] == SHOT_FILE
    assert await shot_filed_under(app, client) is None
    await assert_versions_owned(app.state.db)


async def test_an_import_with_a_machine_files_brew_under_another_name_is_not_its_version(
    live: Any, fake_device: FakeDevice
) -> None:
    app, client = live
    loose = await base_version_id(app, BASE_LABEL)
    await make_set_on(client, "Nine set", loose)
    document = (await base_profile(app, BASE_LABEL)).model_dump(
        mode="json", exclude={"annotations", "id"}
    )
    document["label"] = "My Import"

    response = await client.post(
        "/api/import",
        files=[("files", ("my.json", json.dumps(document).encode(), "application/json"))],
    )

    assert [(i["status"], i["label"]) for i in data(response)["items"]] == [
        ("created", "My Import")
    ]
    assert await owner_of(app, loose) is None
    sets = await app.state.db.fetch_all("SELECT name, board_id FROM sets")
    assert [(r["name"], r["board_id"]) for r in sets] == [("Nine set", None)]
    await the_nine_bar_file_brews_shot_129(app, fake_device)
    assert await shot_filed_under(app, client) == "Nine set"


async def test_a_new_profile_with_a_machine_files_brew_under_another_name_is_its_own_version(
    live: Any, fake_device: FakeDevice
) -> None:
    app, client = live
    loose = await base_version_id(app, BASE_LABEL)
    await make_set_on(client, "Nine set", loose)
    draft = await manual_draft(app, client, BASE_LABEL, "Fork", 9, same_document=True)
    assert draft["draft_version_id"] != loose

    response = await client.post("/api/profile-board", json={"draft_id": draft["id"]})

    assert response.status_code == 201, response.text
    assert await owner_of(app, loose) is None
    sets = await app.state.db.fetch_all("SELECT name, board_id FROM sets")
    assert [(r["name"], r["board_id"]) for r in sets] == [("Nine set", None)]
    await the_nine_bar_file_brews_shot_129(app, fake_device)
    assert await shot_filed_under(app, client) == "Nine set"


async def test_an_older_brew_under_the_current_name_is_matched_and_replaced(
    writes_on: Any, fake_device: FakeDevice
) -> None:
    """The maintainer's standing rule: a file equal to an older version is matched, not a
    conflict. (Before the name left the hash it was a conflict only because the name was in it.)"""
    app, client = writes_on
    await pull(app)
    original = copy.deepcopy(next(p for p in fake_device.profiles if p["label"] == BASE_LABEL))
    await put(client, await manual_draft(app, client, BASE_LABEL, BASE_LABEL, 7.0))
    await pull(app)
    row = row_for(await get_board(client), BASE_LABEL)
    file_id = row["machine"]["device_id"]
    file = next(p for p in fake_device.profiles if p["id"] == file_id)
    file["label"] = "9 Bar Y"
    await pull(app)  # the profile takes the name the machine gave its file
    assert row_for(await get_board(client), "9 Bar Y")["row"]["id"] == row["row"]["id"]
    file = next(p for p in fake_device.profiles if p["id"] == file_id)
    file.clear()
    file.update({**copy.deepcopy(original), "id": file_id, "label": "9 Bar Y"})
    fake_device.ws_requests.clear()

    run = await pull(app)

    assert (run.summary or {})["conflicts"] == []
    assert [t for t in fake_device.ws_requests if t in WRITE_FRAMES] != []
    assert not row_for(await get_board(client), "9 Bar Y")["in_conflict"]
    await assert_versions_owned(app.state.db)


async def test_a_pin_of_a_set_version_on_a_machine_files_loose_version(
    live: Any, fake_device: FakeDevice
) -> None:
    app, client = live
    nine = next(p for p in fake_device.profiles if p["label"] == BASE_LABEL)
    nine["id"] = SHOT_FILE
    await pull(app)
    loose = await base_version_id(app, BASE_LABEL)
    set_id = await make_set_on(client, "Nine set", loose)
    v1 = data(await client.get(f"/api/sets/{set_id}"))["versions"][0]["version"]["id"]

    made = await client.post(f"/api/sets/{set_id}/versions/{v1}/pin", json={"label": "Pin A"})

    assert made.status_code < 300, made.text
    pins = await app.state.db.fetch_all(
        "SELECT p.profile_version_id AS copy, sv.profile_version_id AS original "
        "FROM set_version_pins p JOIN set_versions sv ON sv.id = p.set_version_id"
    )
    assert [(r["copy"] != r["original"]) for r in pins] == [True]
    assert await owner_of(app, loose) is None
    assert (await client.get(f"/api/sets/{set_id}")).status_code == 200
    added = await client.post(f"/api/sets/{set_id}/versions", json={"grind_value": 12.0})
    assert added.status_code == 201, added.text
    await app.state.connection.engine.sync_shots(kind="backfill", trigger="test")
    assert await shot_filed_under(app, client) == "Nine set"


async def test_a_duplicate_of_a_set_on_a_machine_files_loose_version(
    live: Any, fake_device: FakeDevice
) -> None:
    app, client = live
    nine = next(p for p in fake_device.profiles if p["label"] == BASE_LABEL)
    nine["id"] = SHOT_FILE
    await pull(app)
    loose = await base_version_id(app, BASE_LABEL)
    set_id = await make_set_on(client, "Nine set", loose)
    detail = data(await client.get(f"/api/sets/{set_id}"))
    v1 = detail["versions"][0]["version"]["id"]

    made = await client.post(
        f"/api/sets/{set_id}/versions/{v1}/duplicate",
        json={
            "set_name": "Copy",
            "profile_name": "Blooming milk",
            "insight_ids": [],
            "bean_id": detail["set"]["bean_id"],
        },
    )

    assert made.status_code == 201, made.text
    sets = await app.state.db.fetch_all(
        "SELECT s.name, b.label FROM sets s LEFT JOIN profile_board b ON b.id = s.board_id "
        "ORDER BY s.id"
    )
    assert [(r["name"], r["label"]) for r in sets] == [
        ("Nine set", None),
        ("Copy", "Blooming milk"),
    ]
    assert await owner_of(app, loose) is None
    await app.state.connection.engine.sync_shots(kind="backfill", trigger="test")
    assert await shot_filed_under(app, client) == "Nine set"
    added = await client.post(f"/api/sets/{set_id}/versions", json={"grind_value": 12.0})
    assert added.status_code == 201
