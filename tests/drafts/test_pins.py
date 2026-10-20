"""Pin a Recipe version to the machine, preview the pin, unpin it.

A pin is a copy of the version's profile under another name, put on the profile list by the one
copy function, plus a record naming the Recipe version. These tests walk the routes: the answer of
each state, that a refusal writes nothing, that the preview and the pin agree, and that a pin
reaches the machine (and leaves it) through the ordinary sync.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.recipes import RecipesRepository, RecipeVersionWrite, RecipeWrite
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.drafts.copies import ProfileCopier
from gaggiclanker.drafts.proposals import PinnedCopyName
from tests.drafts.conftest import BASE_LABEL, base_version_id, data, error
from tests.drafts.helpers import make_recipe_on, make_recipe_on_a_free_profile
from tests.drafts.test_board import WRITE_FRAMES, adopted, get_board, pull, row_for
from tests.profile_rows import own_version

__all__ = ["adopted"]  # the fixture, re-exported for this module

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _recipe_with_version(
    app: FastAPI, client: httpx.AsyncClient, name: str = "Guji"
) -> tuple[int, int]:
    """A Recipe and its v1: (recipe id, version id). The first is on the base profile; a profile has
    at most one Recipe, so any other is on the first profile that has none."""
    if name == "Guji":
        base = await own_version(app.state.db, await base_version_id(app, BASE_LABEL))
        recipe_id = await make_recipe_on(client, name, base)
    else:
        recipe_id = await make_recipe_on_a_free_profile(app, client, name)
    detail = data(await client.get(f"/api/recipes/{recipe_id}"))
    return recipe_id, int(detail["versions"][0]["version"]["id"])


async def _pin(
    client: httpx.AsyncClient, recipe_id: int, version_id: int, **body: Any
) -> httpx.Response:
    return await client.post(f"/api/recipes/{recipe_id}/versions/{version_id}/pin", json=body)


async def _preview(
    client: httpx.AsyncClient, recipe_id: int, version_id: int, label: str | None = None
) -> httpx.Response:
    params = {} if label is None else {"label": label}
    return await client.get(
        f"/api/recipes/{recipe_id}/versions/{version_id}/pin-preview", params=params
    )


async def _counts(app: FastAPI) -> tuple[int, ...]:
    db = app.state.db
    counts = []
    for table in (
        "recipe_version_pins",
        "profile_board",
        "profile_drafts",
        "profile_versions",
        "recipe_versions",
    ):
        counts.append(int(await db.fetch_value(f"SELECT COUNT(*) FROM {table}")))  # noqa: S608
    return tuple(counts)


async def test_a_pin_makes_a_copy_on_the_list_and_answers_with_the_pin_and_the_row(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    base = await base_version_id(app, BASE_LABEL)

    response = await _pin(client, recipe_id, version_id)

    body = data(response)
    pin = body["pin"]
    name = f"{BASE_LABEL} v1"
    assert set(pin) == {
        "id",
        "recipe_version_id",
        "board_id",
        "profile_version_id",
        "pinned_at",
        "unpinned_at",
        "recipe_id",
        "recipe_name",
        "recipe_archived",
        "version_label",
        "original_version_id",
        "original_label",
        "name",
        "shot_count",
    }
    assert (pin["recipe_version_id"], pin["recipe_id"], pin["recipe_name"]) == (
        version_id,
        recipe_id,
        "Guji",
    )
    assert (pin["name"], pin["version_label"], pin["original_label"]) == (name, "v1", BASE_LABEL)
    assert (pin["original_version_id"], pin["unpinned_at"], pin["shot_count"]) == (base, None, 0)
    assert pin["profile_version_id"] != base and body["board_id"] == pin["board_id"]
    row = row_for(await get_board(client), name)
    assert row["row"]["id"] == body["board_id"]
    assert (row["on_machine"], row["starred"], row["row"]["origin"]) == (True, True, "draft")
    assert (await get_board(client))["proposals"] == []


async def test_a_label_is_stripped_and_a_blank_one_is_unprocessable(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    before = await _counts(app)

    blank = await _pin(client, recipe_id, version_id, label="   ")
    assert blank.status_code == 422 and await _counts(app) == before
    assert (await _preview(client, recipe_id, version_id, "   ")).status_code == 422

    named = data(await _pin(client, recipe_id, version_id, label="  Good enough  "))
    assert named["pin"]["name"] == "Good enough"


async def test_an_unknown_recipe_or_a_version_of_another_recipe_is_not_found(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    _, other_version = await _recipe_with_version(app, client, "Kenya")
    before = await _counts(app)

    for url_recipe, url_version in (
        (9999, version_id),
        (recipe_id, 9999),
        (recipe_id, other_version),
    ):
        assert (await _pin(client, url_recipe, url_version)).status_code == 404
        assert (await _preview(client, url_recipe, url_version)).status_code == 404

    # The words a person reads are the duplicate's: one wording for both screens that ask.
    for response in (
        await _pin(client, 9999, version_id),
        await _preview(client, 9999, version_id),
    ):
        assert error(response)["message"] == "That Recipe is not there any more."
    for response in (
        await _pin(client, recipe_id, other_version),
        await _preview(client, recipe_id, 9999),
    ):
        assert error(response)["message"] == "That version is not one of this Recipe's."

    assert await _counts(app) == before


async def _state(app: FastAPI, client: httpx.AsyncClient, kind: str) -> tuple[int, int, str | None]:
    """A Recipe version in each state a pin can meet: (recipe id, version id, a label to pin
    under)."""
    if kind == "default":
        return (*(await _recipe_with_version(app, client)), None)
    if kind == "taken":
        other_recipe, other_version = await _recipe_with_version(app, client, "Other")
        first = data(await _pin(client, other_recipe, other_version))["pin"]
        # Another Recipe pinned under the name the first pin's copy has.
        return (*(await _recipe_with_version(app, client, "Second")), first["name"])
    if kind == "policy":
        await app.state.settings_service.apply({"profilePolicyPressureMaxBar": 7.5})
        return (*(await _recipe_with_version(app, client)), None)
    if kind == "archived":
        recipe_id, version_id = await _recipe_with_version(app, client)
        data(await client.post(f"/api/recipes/{recipe_id}/archive"))
        return recipe_id, version_id, None
    if kind == "designing":
        recipe_id, version_id = await _recipe_with_version(app, client)
        await app.state.db.execute("UPDATE recipes SET designing = 1 WHERE id = ?", (recipe_id,))
        return recipe_id, version_id, None
    if kind == "no_profile":
        bean = data(await client.post("/api/beans", json={"name": "Bare", "roaster": "x"}))
        made = await RecipesRepository(app.state.db).create(
            RecipeWrite(name="Bare", bean_id=bean["id"]),
            RecipeVersionWrite(drink="espresso", dose_g=18),
        )
        detail = data(await client.get(f"/api/recipes/{made.id}"))
        return int(made.id), int(detail["versions"][0]["version"]["id"]), None
    assert kind == "already_pinned"
    recipe_id, version_id = await _recipe_with_version(app, client)
    data(await _pin(client, recipe_id, version_id))
    return recipe_id, version_id, None


@pytest.mark.parametrize(
    ("kind", "code", "taken"),
    [
        ("default", None, False),
        ("taken", "PIN_NAME_TAKEN", True),
        ("policy", "PIN_POLICY_REFUSED", False),
        ("archived", "RECIPE_ARCHIVED", False),
        ("designing", "DESIGNING", False),
        ("no_profile", "VERSION_HAS_NO_PROFILE", False),
        ("already_pinned", "ALREADY_PINNED", False),
    ],
)
async def test_the_preview_and_the_pin_agree_in_every_state(
    adopted: Adopted, kind: str, code: str | None, taken: bool
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id, label = await _state(app, client, kind)
    before = await _counts(app)

    preview = data(await _preview(client, recipe_id, version_id, label))
    pinned = await _pin(
        client, recipe_id, version_id, **({} if label is None else {"label": label})
    )

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
    recipe_id, version_id = await _recipe_with_version(app, client)

    refused = await _pin(client, recipe_id, version_id, label=BASE_LABEL.upper())

    assert error(refused)["code"] == "PIN_NAME_TAKEN"
    assert error(refused)["message"] == (
        f"There is already a profile called {BASE_LABEL}. Choose another name."
    )


async def test_two_pins_at_once_make_one_pin_and_the_other_is_refused(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)

    first, second = await asyncio.gather(
        _pin(client, recipe_id, version_id), _pin(client, recipe_id, version_id)
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert int(await app.state.db.fetch_value("SELECT COUNT(*) FROM recipe_version_pins")) == 1
    refused = first if first.status_code == 409 else second
    assert error(refused)["code"] == "ALREADY_PINNED"


async def test_several_versions_of_one_recipe_may_each_be_pinned(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, v1 = await _recipe_with_version(app, client)
    assert (
        await client.post(f"/api/recipes/{recipe_id}/versions", json={"dose_g": 18.5})
    ).status_code < 300
    versions = data(await client.get(f"/api/recipes/{recipe_id}"))["versions"]
    (v2,) = [int(v["version"]["id"]) for v in versions if v["version"]["id"] != v1]

    one = data(await _pin(client, recipe_id, v1))["pin"]
    two = data(await _pin(client, recipe_id, v2))["pin"]

    assert one["name"] != two["name"] and one["profile_version_id"] != two["profile_version_id"]
    assert (one["version_label"], two["version_label"]) == ("v1", "v1.1")


async def test_a_pin_records_nothing_on_the_recipe_and_reaches_the_machine(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    name = f"{BASE_LABEL} v1"
    pin = data(await _pin(client, recipe_id, version_id))["pin"]
    versions_before = int(await app.state.db.fetch_value("SELECT COUNT(*) FROM recipe_versions"))

    run = await pull(app)

    assert run.status == "ok", run.error
    assert name in [p["label"] for p in fake.profiles]
    assert (
        int(await app.state.db.fetch_value("SELECT COUNT(*) FROM recipe_versions"))
        == versions_before
    )
    row = row_for(await get_board(client), name)
    assert row["machine"]["holds_current"] and row["row"]["id"] == pin["board_id"]
    assert row["row"]["pending_draft_id"] is None


async def test_unpin_takes_the_copy_off_at_the_next_sync_and_leaves_no_proposal(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    name = f"{BASE_LABEL} v1"
    pin = data(await _pin(client, recipe_id, version_id))["pin"]
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
    detail = data(await client.get(f"/api/recipes/{recipe_id}"))
    assert detail["versions"][0]["version"]["id"] == version_id


async def test_unpinning_before_any_sync_leaves_nothing_behind(adopted: Adopted) -> None:
    app, client, fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    pin = data(await _pin(client, recipe_id, version_id))["pin"]

    data(await client.post(f"/api/pins/{pin['id']}/unpin"))
    run = await pull(app)

    assert run.status == "ok", run.error
    assert f"{BASE_LABEL} v1" not in [p["label"] for p in fake.profiles]
    board = await get_board(client)
    assert board["proposals"] == [] and board["pending_removals"] == []


async def test_unpin_refuses_an_unknown_pin_and_one_already_unpinned(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    pin = data(await _pin(client, recipe_id, version_id))["pin"]

    assert (await client.post("/api/pins/9999/unpin")).status_code == 404
    data(await client.post(f"/api/pins/{pin['id']}/unpin"))
    again = await client.post(f"/api/pins/{pin['id']}/unpin")

    assert again.status_code == 409 and error(again)["code"] == "NOT_PINNED"


async def test_pinning_the_same_version_again_brings_its_pin_and_row_back(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    first = data(await _pin(client, recipe_id, version_id))["pin"]
    data(await client.post(f"/api/pins/{first['id']}/unpin"))
    before = await _counts(app)

    again = data(await _pin(client, recipe_id, version_id))

    assert again["pin"]["id"] == first["id"] and again["board_id"] == first["board_id"]
    assert again["pin"]["unpinned_at"] is None and again["pin"]["pinned_at"] >= first["pinned_at"]
    assert await _counts(app) == before, "no second record, row, draft or profile version"
    row = row_for(await get_board(client), first["name"])
    assert row["row"]["deleted_at"] is None and row["on_machine"] is True


async def test_a_name_freed_by_an_unpin_cannot_be_reused_by_another_version_with_the_same_content(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    first = data(await _pin(client, recipe_id, version_id))["pin"]
    data(await client.post(f"/api/pins/{first['id']}/unpin"))
    # Another Recipe that brews the same thing (a Duplicate: a profile of its own, the same brew).
    bean = data(await client.get(f"/api/recipes/{recipe_id}"))["recipe"]["bean_id"]
    made = data(
        await client.post(
            f"/api/recipes/{recipe_id}/versions/{version_id}/duplicate",
            json={
                "recipe_name": "Second",
                "profile_name": "Second profile",
                "insight_ids": [],
                "bean_id": bean,
            },
        )
    )["recipe"]
    other_recipe = int(made["id"])
    other_version = int(
        data(await client.get(f"/api/recipes/{other_recipe}"))["versions"][0]["version"]["id"]
    )
    before = await _counts(app)

    preview = data(await _preview(client, other_recipe, other_version, first["name"]))
    refused = await _pin(client, other_recipe, other_version, label=first["name"])

    assert preview["taken"] is True and preview["refused"] == error(refused)["message"]
    assert refused.status_code == 409 and error(refused)["code"] == "PIN_NAME_TAKEN"
    assert error(refused)["message"] == (
        f"{first['name']} is the name of the copy pinned from Guji v1. Choose another name."
    )
    assert await _counts(app) == before


async def test_an_ordinary_copy_cannot_land_on_a_pins_copy_even_after_the_unpin(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    first = data(await _pin(client, recipe_id, version_id))["pin"]
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


async def test_a_new_recipe_is_refused_the_profile_of_a_pins_copy(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    copy = data(await _pin(client, recipe_id, version_id))["pin"]["profile_version_id"]
    bean = data(await client.post("/api/beans", json={"name": "Other", "roaster": "x"}))
    before = await _counts(app)

    new_recipe = await client.post(
        "/api/recipes",
        json={
            "name": "Other",
            "bean_id": bean["id"],
            "version": {"drink": "espresso", "profile_version_id": copy},
        },
    )
    # (A new version of a Recipe has no profile to name: it brews the Recipe's own.)
    assert new_recipe.status_code == 409 and error(new_recipe)["code"] == "PROFILE_IS_A_PIN"
    assert await _counts(app) == before


async def test_a_profile_some_recipe_version_already_brews_is_not_a_copy_to_pin(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    base = await app.state.board.proposals.base_profile(await base_version_id(app, BASE_LABEL))
    lookalike, _ = await app.state.board.profiles.ensure_version(
        base.model_copy(update={"label": f"{BASE_LABEL} v1"}), source="draft", owner=None
    )
    await make_recipe_on(
        client, "Brews the lookalike", await own_version(app.state.db, lookalike.id)
    )
    # Its profile is switched off, so the name is free in the list; the Recipe still brews the copy.
    await app.state.db.execute(
        "UPDATE profile_board SET deleted_at = '2026-10-13T10:00:00.000Z' WHERE label = ?",
        (f"{BASE_LABEL} v1",),
    )
    before = await _counts(app)

    preview = data(await _preview(client, recipe_id, version_id))
    refused = await _pin(client, recipe_id, version_id)

    assert preview["taken"] is True and preview["refused"] == error(refused)["message"]
    assert refused.status_code == 409 and error(refused)["code"] == "PIN_NAME_TAKEN"
    assert error(refused)["message"] == (
        f"Brews the lookalike v1 brews a profile called {BASE_LABEL} v1. Choose another name."
    )
    assert await _counts(app) == before


async def test_archiving_a_recipe_unpins_every_pin_in_one_step_and_the_sync_takes_them_off(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, v1 = await _recipe_with_version(app, client)
    data(await client.post(f"/api/recipes/{recipe_id}/versions", json={"dose_g": 18.5}))
    versions = data(await client.get(f"/api/recipes/{recipe_id}"))["versions"]
    (v2,) = [int(v["version"]["id"]) for v in versions if v["version"]["id"] != v1]
    names = [data(await _pin(client, recipe_id, v))["pin"]["name"] for v in (v1, v2)]
    await pull(app)
    assert set(names) <= {p["label"] for p in fake.profiles}

    archived = data(await client.post(f"/api/recipes/{recipe_id}/archive"))

    assert archived["archived"] is True and archived["automatch"] is False
    assert int(await app.state.db.fetch_value("SELECT COUNT(*) FROM recipe_version_pins")) == 2
    assert (
        int(
            await app.state.db.fetch_value(
                "SELECT COUNT(*) FROM recipe_version_pins WHERE unpinned_at IS NULL"
            )
        )
        == 0
    )
    board = await get_board(client)
    assert not set(names) & {r["row"]["label"] for r in board["rows"]}
    assert board["proposals"] == []
    run = await pull(app)
    assert run.status == "ok", run.error
    assert not set(names) & {p["label"] for p in fake.profiles}
    assert (await client.post("/api/recipes/9999/archive")).status_code == 404


async def test_unpinning_the_profile_selected_on_the_machine_selects_another_one_first(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    pin = data(await _pin(client, recipe_id, version_id))["pin"]
    await pull(app)
    name = pin["name"]
    [file_id] = [str(p["id"]) for p in fake.profiles if p["label"] == name]
    fake.selected_profile_id = file_id
    data(await client.post(f"/api/pins/{pin['id']}/unpin"))

    run = await pull(app)

    assert run.status == "ok", run.error
    assert file_id not in [str(p["id"]) for p in fake.profiles]
    assert fake.selected_profile_id in [str(p["id"]) for p in fake.profiles]


async def test_with_writes_off_a_pin_and_an_unpin_are_stored_and_nothing_is_sent(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = live
    recipe_id, version_id = await _recipe_with_version(app, client)
    fake_device.ws_requests.clear()

    pin = data(await _pin(client, recipe_id, version_id))["pin"]
    data(await client.post(f"/api/pins/{pin['id']}/unpin"))
    await pull(app)

    sent = [t for t in fake_device.ws_requests if t in WRITE_FRAMES]
    assert sent == [], "Writes are off: the copy and its removal are only stored"
    assert pin["name"] not in [p["label"] for p in fake_device.profiles]
