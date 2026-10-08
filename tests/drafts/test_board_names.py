"""Names in the sync: a name is exactly what it says, a trailing marker included.

A person may call one profile "Bloom" and another "Bloom [AI]" on purpose, so the sync never
treats the two as one profile: a file it has never seen joins the list under its own name, and
matches a row only by content or by the row's exact name.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import BoardRowPatch, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from tests.drafts.test_board import get_board, pull, row_for, summary_of, write_frames

pytestmark = pytest.mark.usefixtures("fake_device")


def _file(fake: FakeDevice, device_id: str, label: str, temperature: float) -> dict[str, Any]:
    made = copy.deepcopy(fake.profiles[0])
    made.update(id=device_id, label=label, temperature=temperature)
    made["favorite"] = False
    return made


def _ids(fake: FakeDevice) -> set[str]:
    return {str(p["id"]) for p in fake.profiles}


async def test_two_profiles_named_bloom_and_bloom_ai_stay_two_profiles(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writes_on
    fake_device.profiles.append(_file(fake_device, "bloom1", "Bloom", 90))
    fake_device.profiles.append(_file(fake_device, "bloom2", "Bloom [AI]", 91))
    ids = _ids(fake_device)

    first = await pull(app)  # both are new to the list: two profiles, both on
    assert first.status == "ok", first.error
    board = await get_board(client)
    plain, marked = row_for(board, "Bloom"), row_for(board, "Bloom [AI]")
    assert plain["row"]["id"] != marked["row"]["id"]
    assert plain["machine"]["device_id"] == "bloom1"
    assert marked["machine"]["device_id"] == "bloom2"
    assert plain["row"]["on_machine"] and marked["row"]["on_machine"]
    assert board["reports"] == [] and board["actions"] == []

    # The next sync (writes on) changes nothing: no file removed, nothing pushed.
    fake_device.ws_requests.clear()
    again = await pull(app)
    assert summary_of(again)["removed"] == [] and summary_of(again)["pushed"] == []
    assert write_frames(fake_device) == []
    assert _ids(fake_device) == ids


async def test_a_new_file_does_not_join_the_profile_whose_name_differs_by_the_marker(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    before = len((await get_board(client))["rows"])
    for device_id, label in (("n1", "Bloom"), ("n2", "Bloom [AI]")):
        fake_device.profiles.append(_file(fake_device, device_id, label, 92))
    # Added on the display after the first sync: both are unseen, neither is a copy of the other.
    fake_device.profiles[-1]["temperature"] = 93.0
    fake_device.ws_requests.clear()

    found = await pull(app)

    assert sorted(i["label"] for i in summary_of(found)["adopted"]) == ["Bloom", "Bloom [AI]"]
    board = await get_board(client)
    assert len(board["rows"]) == before + 2
    assert row_for(board, "Bloom")["machine"]["device_id"] == "n1"
    assert row_for(board, "Bloom [AI]")["machine"]["device_id"] == "n2"
    assert [r for r in board["reports"] if r["reason"] == "extra_copy"] == []
    assert write_frames(fake_device) == []


async def test_a_file_matches_the_row_of_its_exact_name_and_not_the_other_one(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writes_on
    fake_device.profiles.append(_file(fake_device, "p1", "Bloom", 90))
    fake_device.profiles.append(_file(fake_device, "p2", "Bloom [AI]", 91))
    await pull(app)
    marked_row = row_for(await get_board(client), "Bloom [AI]")["row"]["id"]
    plain_row = row_for(await get_board(client), "Bloom")["row"]["id"]
    # The marked profile's file disappears and comes back under a new id with other content
    # (the person edited it on the display). It is the marked profile's conflict, never the plain
    # one's.
    fake_device.profiles[:] = [p for p in fake_device.profiles if p["id"] != "p2"]
    fake_device.profiles.append(_file(fake_device, "p3", "Bloom [AI]", 95))
    await ProfileBoardRepository(app.state.db).update(
        marked_row, BoardRowPatch(device_profile_id=None)
    )

    await pull(app)

    board = await get_board(client)
    attached = {r["row"]["id"]: r["machine"]["device_id"] for r in board["rows"] if r["machine"]}
    assert attached[plain_row] == "p1"
    assert attached[marked_row] == "p3"


async def test_a_file_carrying_the_marker_still_joins_the_row_that_holds_its_content(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The state the old equivalence served: a profile's stored history holds a version whose
    name carries the marker, and the machine holds that file under a new id. Content finds the
    profile; the name never had to."""
    app, client = writes_on
    await pull(app)
    row = row_for(await get_board(client), fake_device.profiles[0]["label"])["row"]
    old = _file(fake_device, "gone", f"{row['label']} [AI]", 88)
    fake_device.profiles[:] = [
        p for p in fake_device.profiles if p["id"] != row["device_profile_id"]
    ]
    version, _ = await ProfilesRepository(app.state.db).ensure_version(
        Profile.model_validate(
            {k: v for k, v in old.items() if k not in ("id", "favorite", "selected")}
        )
    )
    await ProfileBoardRepository(app.state.db).add_version(row["id"], version.id, "agent")
    fake_device.profiles.append(old)
    rows = len((await get_board(client))["rows"])

    found = await pull(app)

    board = await get_board(client)
    assert len(board["rows"]) == rows
    [joined] = [i for i in summary_of(found)["adopted"] if i["device_id"] == "gone"]
    assert joined["reason"] == "attached"
    assert row_for(board, row["label"])["machine"]["device_id"] == "gone"


async def test_a_never_seen_file_with_a_marker_is_not_a_conflict_on_the_profile_without_one(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """A live profile "X" whose file is gone from the machine, and a file "X [AI]" the app has
    never seen, with content X never had. The file is a profile of its own; it is no conflict on
    X (a name that merely starts the same is not X's name), and X is put back."""
    app, client = writes_on
    await pull(app)
    row = row_for(await get_board(client), fake_device.profiles[0]["label"])["row"]
    label = row["label"]
    fake_device.profiles[:] = [
        p for p in fake_device.profiles if p["id"] != row["device_profile_id"]
    ]
    fake_device.profiles.append(_file(fake_device, "marked", f"{label} [AI]", 77))

    found = await pull(app)

    summary = summary_of(found)
    assert [(i["reason"], i["label"]) for i in summary["adopted"]] == [("unseen", f"{label} [AI]")]
    assert summary["conflicts"] == []
    assert [i["label"] for i in summary["pushed"]] == [label]
    board = await get_board(client)
    assert row_for(board, f"{label} [AI]")["machine"]["device_id"] == "marked"
    assert row_for(board, label)["in_conflict"] is False
