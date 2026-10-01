"""Taking one profile the machine holds onto the board, after the board has been adopted.

The route reads the archive's mirror and writes nothing to the machine: every scenario also
checks that no write frame went out.
"""

from __future__ import annotations

import asyncio
import copy

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository, DeviceWriteWrite
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile, profile_content_hash
from gaggiclanker.infra.errors import Conflict
from tests.drafts.conftest import data
from tests.drafts.test_board import get_board, pull, row_for, write_frames
from tests.drafts.test_replace import APP_LABEL, Live


async def take(client: httpx.AsyncClient, device_id: str) -> httpx.Response:
    return await client.post("/api/profile-board/take", json={"device_profile_id": device_id})


def new_file(
    fake: FakeDevice, name: str, label: str, *, favorite: bool = False
) -> dict[str, object]:
    twin = copy.deepcopy(fake.profiles[0])
    twin.update(id=name, label=label)
    fake.profiles.append(twin)
    if favorite:
        fake.favorite_profile_ids.add(name)
    return twin


async def test_a_profile_made_on_the_display_is_taken_as_the_persons_and_nothing_is_written(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)  # adoption
    new_file(fake_device, "later", "Made on the display", favorite=True)
    await pull(app)  # the mirror learns of it; the board does not take it by itself
    board = await get_board(client, live=False)
    assert all(r["row"]["label"] != "Made on the display" for r in board["rows"])
    before = len(write_frames(fake_device))

    response = await take(client, "later")

    assert response.status_code == 201, response.text
    row = data(response)
    assert (row["origin"], row["device_profile_id"], row["on_home_screen"]) == (
        "adopted",
        "later",
        True,
    )
    assert len(write_frames(fake_device)) == before, "taking a profile sent something"
    board = await get_board(client)
    assert row_for(board, "Made on the display")["planned"] == []
    assert board["actions"] == []


async def test_the_apps_own_saved_copy_becomes_an_app_row_as_at_first_adoption(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    twin = new_file(fake_device, "mine", APP_LABEL)
    await DeviceWritesRepository(app.state.db).record(
        DeviceWriteWrite(
            kind="profile_save",
            host=fake_device_host(app),
            device_id="mine",
            payload_hash=profile_content_hash(Profile.model_validate(twin)),
            result="ok",
        )
    )
    await pull(app)

    row = data(await take(client, "mine"))

    assert row["origin"] == "draft"


async def test_a_copy_that_looks_like_the_apps_but_was_never_saved_by_it_is_the_persons(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    new_file(fake_device, "legacy", APP_LABEL)
    await pull(app)

    assert data(await take(client, "legacy"))["origin"] == "adopted"


async def test_a_profile_already_on_the_board_is_refused(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    existing = str(fake_device.profiles[0]["id"])

    response = await take(client, existing)

    assert response.status_code == 409
    assert "already on the board" in response.text
    assert len((await get_board(client, live=False))["rows"]) == len(fake_device.profiles)


async def test_a_profile_the_mirror_does_not_show_is_not_found(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    assert (await take(client, "nope")).status_code == 404


async def test_before_the_board_is_adopted_there_is_nothing_to_take_onto(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    _, client = writes_on
    response = await take(client, str(fake_device.profiles[0]["id"]))
    assert response.status_code == 409
    assert "pull first" in response.text


async def test_a_deleted_row_whose_file_waits_to_be_dealt_with_is_refused(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    board = await get_board(client, live=False)
    row = board["rows"][0]["row"]
    deleted = await client.delete(f"/api/profile-board/{row['id']}")
    assert deleted.status_code == 200

    response = await take(client, row["device_profile_id"])

    assert response.status_code == 409
    assert "waiting" in response.text


def fake_device_host(app: FastAPI) -> str:
    return str(app.state.connection.client.host)


async def test_five_takes_at_once_make_one_row_and_four_refusals(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    new_file(fake_device, "later", "Made on the display")
    await pull(app)

    responses = await asyncio.gather(*(take(client, "later") for _ in range(5)))

    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409, 409]
    rows = (await get_board(client, live=False))["rows"]
    assert [r["row"]["device_profile_id"] for r in rows].count("later") == 1


async def test_five_service_takes_at_once_make_one_row(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    await pull(app)
    new_file(fake_device, "later", "Made on the display")
    await pull(app)

    results = await asyncio.gather(
        *(app.state.board.take("later") for _ in range(5)), return_exceptions=True
    )

    assert len([r for r in results if not isinstance(r, BaseException)]) == 1
    assert all(isinstance(r, Conflict) for r in results if isinstance(r, BaseException))


async def test_the_unique_index_is_the_same_refusal_never_a_500(
    writes_on: Live, fake_device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = writes_on
    await pull(app)
    new_file(fake_device, "later", "Made on the display")
    await pull(app)
    assert (await take(client, "later")).status_code == 201

    # The checks are blind to the first row, as a racing writer's would be: the index decides.
    async def none(*_: object, **__: object) -> list[object]:
        return []

    async def no_row(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(app.state.board.board, "list_rows", none)
    monkeypatch.setattr(app.state.board.board, "find_live_by_version", no_row)
    response = await take(client, "later")

    assert response.status_code == 409, response.text


async def test_a_file_holding_what_a_board_profile_already_stands_for_is_refused(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    twin = copy.deepcopy(fake_device.profiles[0])
    twin["id"] = "twin"
    fake_device.profiles.append(twin)  # the same content under another id
    await pull(app)

    response = await take(client, "twin")

    assert response.status_code == 409
    assert "already stands for" in response.text


async def test_a_profile_the_mirror_marks_deleted_is_refused(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    new_file(fake_device, "later", "Made on the display")
    await pull(app)
    fake_device.profiles = [p for p in fake_device.profiles if p["id"] != "later"]
    await pull(app)  # the mirror tombstones it

    assert (await take(client, "later")).status_code == 404
