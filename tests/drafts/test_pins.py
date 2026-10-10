"""Pin a Set version to the machine, preview the pin, unpin it.

A pin is a copy of the version's profile under another name, put on the profile list by the one
copy function, plus a record naming the Set version. These tests walk the routes: the answer of
each state, that a refusal writes nothing, that the preview and the pin agree, and that a pin
reaches the machine (and leaves it) through the ordinary sync.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.drafts.copies import ProfileCopier
from gaggiclanker.drafts.proposals import PinnedCopyName
from tests.drafts.conftest import BASE_LABEL, base_version_id, data, error
from tests.drafts.helpers import make_set_on
from tests.drafts.test_board import adopted, get_board, pull, row_for

__all__ = ["adopted"]  # the fixture, re-exported for this module

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _set_with_version(
    app: FastAPI, client: httpx.AsyncClient, name: str = "Guji"
) -> tuple[int, int]:
    """A Set on the base profile and its v1: (set id, version id)."""
    set_id = await make_set_on(client, name, await base_version_id(app, BASE_LABEL))
    detail = data(await client.get(f"/api/sets/{set_id}"))
    return set_id, int(detail["versions"][0]["version"]["id"])


async def _pin(
    client: httpx.AsyncClient, set_id: int, version_id: int, **body: Any
) -> httpx.Response:
    return await client.post(f"/api/sets/{set_id}/versions/{version_id}/pin", json=body)


async def _preview(
    client: httpx.AsyncClient, set_id: int, version_id: int, label: str | None = None
) -> httpx.Response:
    params = {} if label is None else {"label": label}
    return await client.get(f"/api/sets/{set_id}/versions/{version_id}/pin-preview", params=params)


async def _counts(app: FastAPI) -> tuple[int, ...]:
    db = app.state.db
    counts = []
    for table in (
        "set_version_pins",
        "profile_board",
        "profile_drafts",
        "profile_versions",
        "set_versions",
    ):
        counts.append(int(await db.fetch_value(f"SELECT COUNT(*) FROM {table}")))  # noqa: S608
    return tuple(counts)


async def test_a_pin_makes_a_copy_on_the_list_and_answers_with_the_pin_and_the_row(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    base = await base_version_id(app, BASE_LABEL)

    response = await _pin(client, set_id, version_id)

    body = data(response)
    pin = body["pin"]
    name = f"{BASE_LABEL} v1"
    assert set(pin) == {
        "id",
        "set_version_id",
        "board_id",
        "profile_version_id",
        "pinned_at",
        "unpinned_at",
        "set_id",
        "set_name",
        "set_archived",
        "version_label",
        "original_version_id",
        "original_label",
        "name",
        "shot_count",
    }
    assert (pin["set_version_id"], pin["set_id"], pin["set_name"]) == (version_id, set_id, "Guji")
    assert (pin["name"], pin["version_label"], pin["original_label"]) == (name, "v1", BASE_LABEL)
    assert (pin["original_version_id"], pin["unpinned_at"], pin["shot_count"]) == (base, None, 0)
    assert pin["profile_version_id"] != base and body["board_id"] == pin["board_id"]
    row = row_for(await get_board(client), name)
    assert row["row"]["id"] == body["board_id"]
    assert (row["on_machine"], row["starred"], row["row"]["origin"]) == (True, True, "draft")
    assert row["row"]["pending_set_id"] is None
    assert (await get_board(client))["proposals"] == []


async def test_a_label_is_stripped_and_a_blank_one_is_unprocessable(adopted: Adopted) -> None:
    app, client, _fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    before = await _counts(app)

    blank = await _pin(client, set_id, version_id, label="   ")
    assert blank.status_code == 422 and await _counts(app) == before
    assert (await _preview(client, set_id, version_id, "   ")).status_code == 422

    named = data(await _pin(client, set_id, version_id, label="  Good enough  "))
    assert named["pin"]["name"] == "Good enough"


async def test_an_unknown_set_or_a_version_of_another_set_is_not_found(adopted: Adopted) -> None:
    app, client, _fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    _, other_version = await _set_with_version(app, client, "Kenya")
    before = await _counts(app)

    for url_set, url_version in ((9999, version_id), (set_id, 9999), (set_id, other_version)):
        assert (await _pin(client, url_set, url_version)).status_code == 404
        assert (await _preview(client, url_set, url_version)).status_code == 404

    assert await _counts(app) == before


async def _state(app: FastAPI, client: httpx.AsyncClient, kind: str) -> tuple[int, int, str | None]:
    """A Set version in each state a pin can meet: (set id, version id, a label to pin under)."""
    if kind == "default":
        return (*(await _set_with_version(app, client)), None)
    if kind == "taken":
        other_set, other_version = await _set_with_version(app, client, "Other")
        data(await _pin(client, other_set, other_version))
        return (*(await _set_with_version(app, client, "Second")), None)
    if kind == "policy":
        await app.state.settings_service.apply({"profilePolicyPressureMaxBar": 7.5})
        return (*(await _set_with_version(app, client)), None)
    if kind == "archived":
        set_id, version_id = await _set_with_version(app, client)
        data(await client.post(f"/api/sets/{set_id}/archive"))
        return set_id, version_id, None
    if kind == "designing":
        set_id, version_id = await _set_with_version(app, client)
        await app.state.db.execute("UPDATE sets SET designing = 1 WHERE id = ?", (set_id,))
        return set_id, version_id, None
    if kind == "no_profile":
        bean = data(await client.post("/api/beans", json={"name": "Bare", "roaster": "x"}))
        made = data(
            await client.post(
                "/api/sets", json={"name": "Bare", "bean_id": bean["id"], "version": {"dose_g": 18}}
            )
        )
        detail = data(await client.get(f"/api/sets/{made['id']}"))
        return int(made["id"]), int(detail["versions"][0]["version"]["id"]), None
    assert kind == "already_pinned"
    set_id, version_id = await _set_with_version(app, client)
    data(await _pin(client, set_id, version_id))
    return set_id, version_id, None


@pytest.mark.parametrize(
    ("kind", "code", "taken"),
    [
        ("default", None, False),
        ("taken", "PIN_NAME_TAKEN", True),
        ("policy", "PIN_POLICY_REFUSED", False),
        ("archived", "SET_ARCHIVED", False),
        ("designing", "DESIGNING", False),
        ("no_profile", "VERSION_HAS_NO_PROFILE", False),
        ("already_pinned", "ALREADY_PINNED", False),
    ],
)
async def test_the_preview_and_the_pin_agree_in_every_state(
    adopted: Adopted, kind: str, code: str | None, taken: bool
) -> None:
    app, client, _fake = adopted
    set_id, version_id, label = await _state(app, client, kind)
    before = await _counts(app)

    preview = data(await _preview(client, set_id, version_id, label))
    pinned = await _pin(client, set_id, version_id, **({} if label is None else {"label": label}))

    assert set(preview) == {"name", "taken", "refused", "clamp_changes"}
    assert preview["taken"] is taken
    if code is None:
        assert preview["refused"] is None and preview["clamp_changes"] == []
        assert pinned.status_code == 200
        assert data(pinned)["pin"]["name"] == preview["name"]
        return
    assert preview["refused"] is not None
    assert pinned.status_code == 409
    assert error(pinned)["code"] == code
    assert error(pinned)["message"] == preview["refused"]
    if kind != "already_pinned":
        assert await _counts(app) == before, "a refusal writes nothing"
    if code == "PIN_NAME_TAKEN":
        assert error(pinned)["details"] == {"name": preview["name"]}
        assert preview["refused"] == (
            f"There is already a profile called {preview['name']}. Choose another name."
        )
    if code == "PIN_POLICY_REFUSED":
        assert (
            preview["clamp_changes"] and "outside the current safety bounds" in preview["refused"]
        )
        assert error(pinned)["details"] == {"clamp_changes": preview["clamp_changes"]}


async def test_the_taken_name_sentence_is_the_stored_names_not_the_typed_ones_case(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    set_id, version_id = await _set_with_version(app, client)

    refused = await _pin(client, set_id, version_id, label=BASE_LABEL.upper())

    assert error(refused)["code"] == "PIN_NAME_TAKEN"
    assert error(refused)["message"] == (
        f"There is already a profile called {BASE_LABEL}. Choose another name."
    )


async def test_two_pins_at_once_make_one_pin_and_the_other_is_refused(adopted: Adopted) -> None:
    app, client, _fake = adopted
    set_id, version_id = await _set_with_version(app, client)

    first, second = await asyncio.gather(
        _pin(client, set_id, version_id), _pin(client, set_id, version_id)
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert int(await app.state.db.fetch_value("SELECT COUNT(*) FROM set_version_pins")) == 1
    refused = first if first.status_code == 409 else second
    assert error(refused)["code"] == "ALREADY_PINNED"


async def test_several_versions_of_one_set_may_each_be_pinned(adopted: Adopted) -> None:
    app, client, _fake = adopted
    set_id, v1 = await _set_with_version(app, client)
    assert (
        await client.post(f"/api/sets/{set_id}/versions", json={"dose_g": 18.5})
    ).status_code < 300
    versions = data(await client.get(f"/api/sets/{set_id}"))["versions"]
    (v2,) = [int(v["version"]["id"]) for v in versions if v["version"]["id"] != v1]

    one = data(await _pin(client, set_id, v1))["pin"]
    two = data(await _pin(client, set_id, v2))["pin"]

    assert one["name"] != two["name"] and one["profile_version_id"] != two["profile_version_id"]
    assert (one["version_label"], two["version_label"]) == ("v1", "v1.1")


async def test_a_pin_records_nothing_on_the_set_and_reaches_the_machine(adopted: Adopted) -> None:
    app, client, fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    name = f"{BASE_LABEL} v1"
    pin = data(await _pin(client, set_id, version_id))["pin"]
    versions_before = int(await app.state.db.fetch_value("SELECT COUNT(*) FROM set_versions"))

    run = await pull(app)

    assert run.status == "ok", run.error
    assert name in [p["label"] for p in fake.profiles]
    assert (
        int(await app.state.db.fetch_value("SELECT COUNT(*) FROM set_versions")) == versions_before
    )
    row = row_for(await get_board(client), name)
    assert row["machine"]["holds_current"] and row["row"]["id"] == pin["board_id"]
    assert row["row"]["pending_draft_id"] is None


async def test_unpin_takes_the_copy_off_at_the_next_sync_and_leaves_no_proposal(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    name = f"{BASE_LABEL} v1"
    pin = data(await _pin(client, set_id, version_id))["pin"]
    await pull(app)
    assert name in [p["label"] for p in fake.profiles]

    response = await client.post(f"/api/pins/{pin['id']}/unpin")

    answer = data(response)
    assert answer["board_id"] == pin["board_id"]
    assert answer["pin"]["id"] == pin["id"] and answer["pin"]["unpinned_at"] is not None
    board = await get_board(client)
    assert name not in [r["row"]["label"] for r in board["rows"]]
    assert board["proposals"] == []
    run = await pull(app)
    assert run.status == "ok", run.error
    assert name not in [p["label"] for p in fake.profiles]
    # The shots already filed stay filed, and the pin is not a live one any more.
    detail = data(await client.get(f"/api/sets/{set_id}"))
    assert detail["versions"][0]["version"]["id"] == version_id


async def test_unpinning_before_any_sync_leaves_nothing_behind(adopted: Adopted) -> None:
    app, client, fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    pin = data(await _pin(client, set_id, version_id))["pin"]

    data(await client.post(f"/api/pins/{pin['id']}/unpin"))
    run = await pull(app)

    assert run.status == "ok", run.error
    assert f"{BASE_LABEL} v1" not in [p["label"] for p in fake.profiles]
    board = await get_board(client)
    assert board["proposals"] == [] and board["pending_removals"] == []


async def test_unpin_refuses_an_unknown_pin_and_one_already_unpinned(adopted: Adopted) -> None:
    app, client, _fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    pin = data(await _pin(client, set_id, version_id))["pin"]

    assert (await client.post("/api/pins/9999/unpin")).status_code == 404
    data(await client.post(f"/api/pins/{pin['id']}/unpin"))
    again = await client.post(f"/api/pins/{pin['id']}/unpin")

    assert again.status_code == 409 and error(again)["code"] == "NOT_PINNED"


async def test_pinning_the_same_version_again_brings_its_pin_and_row_back(adopted: Adopted) -> None:
    app, client, _fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    first = data(await _pin(client, set_id, version_id))["pin"]
    data(await client.post(f"/api/pins/{first['id']}/unpin"))
    before = await _counts(app)

    again = data(await _pin(client, set_id, version_id))

    assert again["pin"]["id"] == first["id"] and again["board_id"] == first["board_id"]
    assert again["pin"]["unpinned_at"] is None and again["pin"]["pinned_at"] >= first["pinned_at"]
    assert await _counts(app) == before, "no second record, row, draft or profile version"
    row = row_for(await get_board(client), first["name"])
    assert row["row"]["deleted_at"] is None and row["on_machine"] is True


async def test_a_name_freed_by_an_unpin_cannot_be_reused_by_another_version_with_the_same_content(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    first = data(await _pin(client, set_id, version_id))["pin"]
    data(await client.post(f"/api/pins/{first['id']}/unpin"))
    other_set, other_version = await _set_with_version(app, client, "Second")
    before = await _counts(app)

    preview = data(await _preview(client, other_set, other_version, first["name"]))
    refused = await _pin(client, other_set, other_version, label=first["name"])

    assert preview["taken"] is True and preview["refused"] == error(refused)["message"]
    assert refused.status_code == 409 and error(refused)["code"] == "PIN_NAME_TAKEN"
    assert await _counts(app) == before


async def test_an_ordinary_copy_cannot_land_on_a_pins_copy_even_after_the_unpin(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    set_id, version_id = await _set_with_version(app, client)
    first = data(await _pin(client, set_id, version_id))["pin"]
    data(await client.post(f"/api/pins/{first['id']}/unpin"))
    base = await base_version_id(app, BASE_LABEL)
    before = await _counts(app)

    with pytest.raises(PinnedCopyName) as refused:
        async with app.state.db.transaction():
            await ProfileCopier(app.state.board).copy_profile(
                base, first["name"], carry_signature=False
            )

    # The pin is gone and no live profile has the name: the sentences say what is true.
    assert refused.value.message == (
        f"{first['name']} is the name of the copy pinned from Guji v1. "
        "Choose another name for a new profile."
    )
    assert refused.value.person == (
        f"{first['name']} is the name of the copy pinned from Guji v1. Choose another name."
    )
    assert await _counts(app) == before
