"""A push replaces the app's own previous copy on the machine; a rollback puts it back.

Everything here runs against the fake machine through HTTP, because what is under
test is the state the *display* ends up in: which profiles it holds, which one is
starred and selected, what it did with its startup setting, and what the audit says
was sent, in which order.

The set-up every replace test shares: a person's own profile ("9 Bar Espresso") is
drafted and pushed, which adds "9 Bar Espresso [AI]" beside it. That copy is the
app's, so a second draft made from it, once pushed, replaces it.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWriteRow, DeviceWritesRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile, profile_content_hash
from tests.drafts.conftest import BASE_LABEL, base_profile, base_version_id, data, error
from tests.llm.conftest import FakeProvider

APP_LABEL = f"{BASE_LABEL} [AI]"

Live = tuple[FastAPI, httpx.AsyncClient]


async def draft_of(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider, label: str, bar: float
) -> dict[str, Any]:
    """A draft of the mirrored profile ``label`` with its first pump set to ``bar``."""
    profile = await base_profile(app, label)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    provider.script = [json.dumps({"profile": document, "change_summary": f"{bar} bar."})]
    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": await base_version_id(app, label), "notes": "change it"},
    )
    return dict(data(response))


async def push(client: httpx.AsyncClient, draft: dict[str, Any], **body: Any) -> httpx.Response:
    approved = await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    return await client.post(f"/api/profile-drafts/{draft['id']}/push", json=body)


async def pushed(client: httpx.AsyncClient, draft: dict[str, Any], **body: Any) -> dict[str, Any]:
    return dict(data(await push(client, draft, **body))["draft"])


async def app_copy(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider, bar: float = 8
) -> dict[str, Any]:
    """Push a draft of the person's profile, so the machine holds an app-written copy."""
    first = await pushed(client, await draft_of(app, client, provider, BASE_LABEL, bar))
    assert first["status"] == "pushed"
    return first


async def make_set(client: httpx.AsyncClient, name: str = "Replace test") -> int:
    """A Set with one version, so a push has somewhere to record itself."""
    bean = data(await client.post("/api/beans", json={"name": name, "roaster": "nobody"}))
    stored = data(
        await client.post(
            "/api/sets",
            json={
                "name": name,
                "bean_id": bean["id"],
                "version": {"dose_g": 18.0, "target_yield_g": 36.0},
            },
        )
    )
    return int(stored["id"])


async def manual_draft(
    app: FastAPI,
    client: httpx.AsyncClient,
    base_label: str,
    label: str,
    bar: float,
    *,
    same_document: bool = False,
) -> dict[str, Any]:
    """A draft that carries its whole document, as a fork or a stage-as-is does."""
    profile = await base_profile(app, base_label)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["label"] = label
    if not same_document:
        document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    response = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": await base_version_id(app, base_label),
            "profile": document,
            "change_summary": "manual",
        },
    )
    assert response.status_code == 201, response.text
    return dict(data(response))


async def set_device_ids(app: FastAPI, set_id: int) -> list[str | None]:
    """The device profile each version of a Set names, oldest first."""
    rows = await app.state.db.fetch_all(
        "SELECT pushed_device_profile_id AS d FROM set_versions "
        "WHERE set_id = ? ORDER BY version_no",
        (set_id,),
    )
    return [row["d"] for row in rows]


def document_of(fake: FakeDevice, device_id: str) -> dict[str, Any]:
    """A profile on the machine as a document a draft can carry."""
    found = next(p for p in fake.profiles if p["id"] == device_id)
    return {k: v for k, v in found.items() if k not in ("id", "favorite", "selected")}


async def manual_from(
    client: httpx.AsyncClient, base_version_id: int, document: dict[str, Any], bar: float
) -> dict[str, Any]:
    """A draft of an explicit document, made from a given stored version."""
    edited = copy.deepcopy(document)
    edited["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": base_version_id, "profile": edited, "change_summary": f"{bar}"},
    )
    assert response.status_code == 201, response.text
    return dict(data(response))


async def make_set_on(client: httpx.AsyncClient, name: str, profile_version_id: int) -> int:
    """A Set whose first version names a stored profile, as one made from the library does."""
    bean = data(await client.post("/api/beans", json={"name": name, "roaster": "nobody"}))
    response = await client.post(
        "/api/sets",
        json={
            "name": name,
            "bean_id": bean["id"],
            "version": {
                "dose_g": 18.0,
                "target_yield_g": 36.0,
                "profile_version_id": profile_version_id,
            },
        },
    )
    assert response.status_code < 300, response.text
    return int(data(response)["id"])


async def grind_change(client: httpx.AsyncClient, set_id: int) -> None:
    """A version that changes only the grind: it pushes nothing and names no device id."""
    response = await client.post(f"/api/sets/{set_id}/versions", json={"grind_value": 21.0})
    assert response.status_code < 300, response.text


def on_machine(fake: FakeDevice) -> list[str]:
    return [str(p["id"]) for p in fake.profiles]


def ids_labelled(fake: FakeDevice, label: str) -> list[str]:
    return [str(p["id"]) for p in fake.profiles if p.get("label") == label]


async def audit(app: FastAPI) -> list[DeviceWriteRow]:
    """Every device write, oldest first."""
    rows = await DeviceWritesRepository(app.state.db).list_writes(limit=500)
    return list(reversed(rows))


def kinds(rows: list[DeviceWriteRow], *wanted: str) -> list[tuple[str, str | None, str]]:
    return [(r.kind, r.device_id, r.result) for r in rows if r.kind in wanted]


# ── no duplicate ─────────────────────────────────────────────────────


async def test_content_already_on_the_machine_is_not_saved_again(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider)
    saves = kinds(await audit(app), "profile_save")
    before = len(fake_device.profiles)

    second = await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8))

    assert second["status"] == "pushed"
    assert second["pushed_device_profile_id"] == first["pushed_device_profile_id"]
    assert len(fake_device.profiles) == before
    assert kinds(await audit(app), "profile_save") == saves
    assert second["outcome"]["reused_device_profile_id"] == first["pushed_device_profile_id"]
    assert "nothing was saved" in " ".join(second["outcome"]["lines"])


# ── replace ──────────────────────────────────────────────────────────


async def test_a_push_replaces_the_app_s_previous_copy_and_carries_star_selection_and_startup(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    old_id = first["pushed_device_profile_id"]
    fake_device.selected_profile_id = old_id
    fake_device.device_settings["startupProfile"] = old_id

    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7))

    new_id = second["pushed_device_profile_id"]
    assert new_id != old_id
    assert ids_labelled(fake_device, APP_LABEL) == [new_id]
    assert BASE_LABEL in [p.get("label") for p in fake_device.profiles]
    assert new_id in fake_device.favorite_profile_ids
    assert fake_device.selected_profile_id == new_id
    assert fake_device.device_settings["startupProfile"] == ""
    assert second["replaced_device_profile_id"] == old_id
    assert second["outcome"]["replaced_device_profile_id"] == old_id
    assert second["outcome"]["startup_profile_cleared"] is True
    assert "startup profile" in " ".join(second["outcome"]["lines"])

    rows = await audit(app)
    assert ("profile_delete", old_id, "ok") in kinds(rows, "profile_delete")
    assert ("profile_select", new_id, "ok") in kinds(rows, "profile_select")
    order = [(r.kind, r.device_id) for r in rows]
    assert order.index(("profile_select", new_id)) < order.index(("profile_delete", old_id))
    mirror = ProfilesRepository(app.state.db)
    gone = await mirror.get_device_profile(old_id)
    assert gone is not None and gone.deleted_at is not None
    here = await mirror.get_device_profile(new_id)
    assert here is not None and here.deleted_at is None and here.selected


async def test_the_star_moves_to_the_new_profile_even_when_the_firmware_would_not_add_it(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """The firmware favourites a new profile by itself; this isolates what the app does."""
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    fake_device.auto_favorite_new = False

    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7))

    assert second["pushed_device_profile_id"] in fake_device.favorite_profile_ids
    assert first["pushed_device_profile_id"] not in fake_device.favorite_profile_ids
    assert ("profile_favorite", second["pushed_device_profile_id"], "ok") in kinds(
        await audit(app), "profile_favorite"
    )


async def test_a_person_s_own_profile_is_added_beside_and_never_deleted(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    person_ids = ids_labelled(fake_device, BASE_LABEL)

    first = await app_copy(app, client, provider, 8)

    assert ids_labelled(fake_device, BASE_LABEL) == person_ids
    assert first["replaced_device_profile_id"] is None
    # Another label: a new profile beside the person's, not a new version of it, so the
    # push does not even weigh the person's profile for removal.
    assert first["outcome"]["kept_device_profile_id"] is None
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_copy_edited_on_the_display_since_is_kept_and_the_push_says_why(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    old_id = first["pushed_device_profile_id"]
    draft = await draft_of(app, client, provider, APP_LABEL, 7)
    for profile in fake_device.profiles:
        if profile["id"] == old_id:
            profile["temperature"] = 91

    refused = await push(client, draft)
    assert refused.status_code == 409
    assert "changed on the machine since" in error(refused)["message"]

    second = dict(data(await push(client, draft, allow_stale_base=True))["draft"])
    assert second["status"] == "pushed"
    assert old_id in [p["id"] for p in fake_device.profiles]
    assert second["replaced_device_profile_id"] is None
    assert second["outcome"]["kept_device_profile_id"] == old_id
    assert second["outcome"]["kept_reason"] == "changed on the display since"
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_base_that_is_gone_from_the_machine_is_not_stale_and_the_push_adds(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    old_id = first["pushed_device_profile_id"]
    draft = await draft_of(app, client, provider, APP_LABEL, 7)
    fake_device.profiles[:] = [p for p in fake_device.profiles if p["id"] != old_id]

    second = await pushed(client, draft)

    assert second["status"] == "pushed"
    assert ids_labelled(fake_device, APP_LABEL) == [second["pushed_device_profile_id"]]
    assert second["replaced_device_profile_id"] is None
    assert "no longer on the machine" in " ".join(second["outcome"]["lines"])
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_profile_renamed_to_end_in_the_suffix_is_still_not_ours(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """The label alone proves nothing: this box never saved that id."""
    app, client = writes_on
    handmade = copy.deepcopy(next(p for p in fake_device.profiles if p["label"] == BASE_LABEL))
    handmade.update(id="hand0001", label="Hand made [AI]")
    fake_device.profiles.append(handmade)
    await app.state.connection.engine.sync_profiles(trigger="test")

    pushed_draft = await pushed(client, await draft_of(app, client, provider, "Hand made [AI]", 7))

    assert pushed_draft["status"] == "pushed"
    assert "hand0001" in [p["id"] for p in fake_device.profiles]
    assert pushed_draft["outcome"]["kept_reason"] == "not created by this app"
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_copy_this_app_saved_but_renamed_on_the_display_is_not_removed(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """The audit alone proves nothing either: the label on the machine right now counts.

    Through a Set, where the predecessor is what the Set's version has on the machine
    whatever it is called (outside a Set a renamed copy is simply another profile).
    """
    app, client = writes_on
    set_id = await make_set(client)
    first = await pushed(
        client, await draft_of(app, client, provider, BASE_LABEL, 8), set_id=set_id
    )
    old_id = first["pushed_device_profile_id"]
    draft = await draft_of(app, client, provider, APP_LABEL, 7)
    for profile in fake_device.profiles:
        if profile["id"] == old_id:
            profile["label"] = "Renamed by hand"

    second = await pushed(client, draft, set_id=set_id, allow_stale_base=True)

    assert old_id in [p["id"] for p in fake_device.profiles]
    assert second["outcome"]["kept_reason"] == "not created by this app"
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_failed_read_back_leaves_both_profiles_on_the_machine(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    old_id = first["pushed_device_profile_id"]
    draft = await draft_of(app, client, provider, APP_LABEL, 7)

    def corrupt(stored: dict[str, Any]) -> dict[str, Any]:
        return {**stored, "temperature": 80}

    fake_device.mutate_on_save = corrupt

    second = await pushed(client, draft)

    assert second["status"] == "failed"
    assert old_id in [p["id"] for p in fake_device.profiles]
    assert len(ids_labelled(fake_device, APP_LABEL)) == 2
    assert second["replaced_device_profile_id"] is None
    assert kinds(await audit(app), "profile_delete") == []


async def test_the_new_profile_keeps_the_label_and_the_writes_go_in_the_safe_order(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """save, read back, star, select, and only then delete, on the machine's own wire."""
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    old_id = first["pushed_device_profile_id"]
    fake_device.selected_profile_id = old_id
    fake_device.auto_favorite_new = False
    fake_device.favorite_profile_ids.add(old_id)
    mark = len(fake_device.ws_requests)

    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7))

    wire = fake_device.ws_requests[mark:]
    save, load_back, star, select, delete = (
        wire.index("req:profiles:save"),
        len(wire) - 1 - wire[::-1].index("req:profiles:load"),
        wire.index("req:profiles:favorite"),
        wire.index("req:profiles:select"),
        wire.index("req:profiles:delete"),
    )
    assert save < star < select < delete
    assert load_back < delete
    assert ids_labelled(fake_device, APP_LABEL) == [second["pushed_device_profile_id"]]
    assert second["draft_label"] == first["draft_label"]


@pytest.mark.parametrize("failing", ["req:profiles:select", "req:profiles:favorite"])
async def test_a_failure_between_the_save_and_the_delete_leaves_both_profiles(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice, failing: str
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    old_id = first["pushed_device_profile_id"]
    fake_device.selected_profile_id = old_id
    fake_device.auto_favorite_new = False
    fake_device.favorite_profile_ids.add(old_id)
    fake_device.error_requests.add(failing)
    mark = len(fake_device.ws_requests)

    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7))

    assert second["status"] == "pushed"
    assert old_id in [p["id"] for p in fake_device.profiles]
    assert second["pushed_device_profile_id"] in [p["id"] for p in fake_device.profiles]
    assert "req:profiles:delete" not in fake_device.ws_requests[mark:]
    assert second["replaced_device_profile_id"] is None
    assert second["outcome"]["kept_device_profile_id"] == old_id
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_base_that_moved_to_another_id_is_not_stale_and_is_reported_gone(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """Stale is decided by content: the same profile under a new id is not a change."""
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    old_id = first["pushed_device_profile_id"]
    draft = await draft_of(app, client, provider, APP_LABEL, 7)
    for profile in fake_device.profiles:
        if profile["id"] == old_id:
            profile["id"] = "moved01"

    second = await pushed(client, draft)

    assert second["status"] == "pushed"
    assert "moved01" in [p["id"] for p in fake_device.profiles]
    assert second["replaced_device_profile_id"] is None
    assert "no longer on the machine" in " ".join(second["outcome"]["lines"])


# ── rollback ─────────────────────────────────────────────────────────


async def test_a_rollback_after_a_replace_puts_the_predecessor_back_before_removing_the_push(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    old_id = first["pushed_device_profile_id"]
    fake_device.selected_profile_id = old_id
    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7))
    pushed_id = second["pushed_device_profile_id"]
    assert second["replaced_device_profile_id"] == old_id
    assert fake_device.selected_profile_id == pushed_id
    fake_device.auto_favorite_new = False
    before = len(await audit(app))

    response = await client.post(f"/api/profile-drafts/{second['id']}/rollback", json={})

    rolled = data(response)
    restored_id = rolled["outcome"]["restored_device_profile_id"]
    assert restored_id and restored_id not in (old_id, pushed_id)
    assert ids_labelled(fake_device, APP_LABEL) == [restored_id]
    assert restored_id in fake_device.favorite_profile_ids
    assert fake_device.selected_profile_id == restored_id
    assert rolled["status"] == "discarded"
    assert rolled["pushed_device_profile_id"] is None
    assert rolled["replaced_device_profile_id"] is None
    # What is back is exactly what the archive recorded for the predecessor.
    restored = Profile.model_validate(
        next(p for p in fake_device.profiles if p["id"] == restored_id)
    )
    recorded = await ProfilesRepository(app.state.db).get_version(first["draft_version_id"])
    assert recorded is not None
    assert profile_content_hash(restored) == recorded.content_hash

    rows = (await audit(app))[before:]
    order = [(r.kind, r.device_id) for r in rows if r.result == "ok"]
    assert order.index(("profile_save", restored_id)) < order.index(("profile_delete", pushed_id))
    mirror = ProfilesRepository(app.state.db)
    gone = await mirror.get_device_profile(pushed_id)
    assert gone is not None and gone.deleted_at is not None
    back = await mirror.get_device_profile(restored_id)
    assert back is not None and back.deleted_at is None and back.selected


async def test_a_rollback_when_the_predecessor_is_still_there_only_removes_the_push(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    person_ids = ids_labelled(fake_device, BASE_LABEL)
    first = await app_copy(app, client, provider, 8)
    saves = kinds(await audit(app), "profile_save")

    rolled = data(await client.post(f"/api/profile-drafts/{first['id']}/rollback", json={}))

    assert rolled["status"] == "discarded"
    assert ids_labelled(fake_device, BASE_LABEL) == person_ids
    assert ids_labelled(fake_device, APP_LABEL) == []
    assert rolled["outcome"]["restored_device_profile_id"] is None
    assert rolled["outcome"]["removed_device_profile_id"] == first["pushed_device_profile_id"]
    assert kinds(await audit(app), "profile_save") == saves


async def test_a_rollback_keeps_a_push_that_was_edited_on_the_display(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    for profile in fake_device.profiles:
        if profile["id"] == first["pushed_device_profile_id"]:
            profile["temperature"] = 91

    rolled = data(await client.post(f"/api/profile-drafts/{first['id']}/rollback", json={}))

    assert rolled["status"] == "pushed"
    assert rolled["pushed_device_profile_id"] == first["pushed_device_profile_id"]
    assert rolled["outcome"]["kept_reason"] == "changed on the display since"
    assert first["pushed_device_profile_id"] in [p["id"] for p in fake_device.profiles]


async def test_a_rollback_that_cannot_bring_the_predecessor_back_removes_nothing(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await app_copy(app, client, provider, 8)
    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7))
    assert second["replaced_device_profile_id"]

    def corrupt(stored: dict[str, Any]) -> dict[str, Any]:
        return {**stored, "temperature": 80}

    fake_device.mutate_on_save = corrupt
    rolled = data(await client.post(f"/api/profile-drafts/{second['id']}/rollback", json={}))

    assert rolled["status"] == "pushed"
    assert rolled["pushed_device_profile_id"] == second["pushed_device_profile_id"]
    assert second["pushed_device_profile_id"] in [p["id"] for p in fake_device.profiles]
    assert "did not verify" in " ".join(rolled["outcome"]["lines"])


@pytest.mark.parametrize("startup", [True, False])
async def test_the_startup_profile_is_reported_only_when_the_firmware_cleared_it(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice, startup: bool
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    if startup:
        fake_device.device_settings["startupProfile"] = first["pushed_device_profile_id"]

    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7))

    assert second["outcome"]["startup_profile_cleared"] is startup
    said = "startup profile" in " ".join(second["outcome"]["lines"])
    assert said is startup


# ── one lineage: a push replaces only what it is a new version of ─────


async def test_a_fork_with_a_new_label_leaves_the_profile_it_was_forked_from(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    x = first["pushed_device_profile_id"]
    fake_device.selected_profile_id = x

    fork = await pushed(client, await manual_draft(app, client, APP_LABEL, "Brand new [AI]", 6))

    assert x in on_machine(fake_device)
    assert fork["replaced_device_profile_id"] is None
    assert fake_device.selected_profile_id == x
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_push_is_identical_to_its_base_and_the_base_is_not_removed(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """Stage as is: the machine already holds this content, which is also the predecessor."""
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    x = first["pushed_device_profile_id"]

    staged = await pushed(
        client, await manual_draft(app, client, APP_LABEL, APP_LABEL, 0, same_document=True)
    )

    assert staged["pushed_device_profile_id"] == x
    assert staged["pushed_saved"] is False
    assert x in on_machine(fake_device)
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_push_for_a_set_replaces_what_the_sets_version_has_on_the_machine(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """Two drafts of one version, and a draft of an older one, still leave one copy."""
    app, client = writes_on
    set_id = await make_set(client)
    await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8), set_id=set_id)
    d1 = await draft_of(app, client, provider, APP_LABEL, 7)
    d2 = await draft_of(app, client, provider, APP_LABEL, 6)

    await pushed(client, d1, set_id=set_id)
    second = await pushed(client, d2, set_id=set_id)

    assert ids_labelled(fake_device, APP_LABEL) == [second["pushed_device_profile_id"]]
    assert second["replaced_device_profile_id"] is not None
    assert (await set_device_ids(app, set_id))[-1] == second["pushed_device_profile_id"]


async def test_a_set_push_clears_the_versions_that_named_the_replaced_profile(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    set_id = await make_set(client)
    first = await pushed(
        client, await draft_of(app, client, provider, BASE_LABEL, 8), set_id=set_id
    )
    old_id = first["pushed_device_profile_id"]

    await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7), set_id=set_id)

    assert old_id not in await set_device_ids(app, set_id)


async def test_another_sets_current_version_keeps_its_profile_on_the_machine(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    mine, other = await make_set(client, "Mine"), await make_set(client, "Other")
    first = await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8), set_id=mine)
    x = first["pushed_device_profile_id"]
    # The other Set adopts the same profile: its current version names it too.
    await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8), set_id=other)

    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7), set_id=mine)

    assert x in on_machine(fake_device)
    assert second["replaced_device_profile_id"] is None
    assert "Other" in second["outcome"]["kept_reason"]


# ── rollback removes only what its own push created ──────────────────


async def test_rolling_back_a_push_that_reused_a_profile_does_not_remove_it(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    a = await app_copy(app, client, provider, 8)
    x = a["pushed_device_profile_id"]
    b = await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8))
    assert b["pushed_saved"] is False

    rolled = data(await client.post(f"/api/profile-drafts/{b['id']}/rollback", json={}))

    assert x in on_machine(fake_device)
    assert rolled["status"] == "discarded"
    assert rolled["outcome"]["kept_reason"] == "saved by another push"
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_rollback_is_refused_while_another_pushed_draft_stands_on_the_profile(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    a = await app_copy(app, client, provider, 8)
    x = a["pushed_device_profile_id"]
    await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8))

    rolled = data(await client.post(f"/api/profile-drafts/{a['id']}/rollback", json={}))

    assert x in on_machine(fake_device)
    assert rolled["status"] == "pushed"
    assert "another pushed draft" in rolled["outcome"]["kept_reason"]


async def test_a_rollback_is_refused_while_another_set_uses_the_profile(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    mine, other = await make_set(client, "Mine"), await make_set(client, "Other")
    first = await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8), set_id=mine)
    x = first["pushed_device_profile_id"]
    adopted = await client.post(
        f"/api/sets/{other}/versions", json={"pushed_device_profile_id": x, "intent": "adopt"}
    )
    assert adopted.status_code < 300, adopted.text

    rolled = data(await client.post(f"/api/profile-drafts/{first['id']}/rollback", json={}))

    assert x in on_machine(fake_device)
    assert "Other" in rolled["outcome"]["kept_reason"]


async def test_a_replaced_draft_has_nothing_to_roll_back_and_the_later_one_restores_it(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """W -> X -> Y: rolling X back would put W beside Y. It is refused; rolling Y back works."""
    app, client = writes_on
    await app_copy(app, client, provider, 8)
    a = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7))
    b = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 6))
    reread = data(await client.get(f"/api/profile-drafts/{a['id']}"))["draft"]
    assert reread["replaced_by_draft_id"] == b["id"]

    refused = await client.post(f"/api/profile-drafts/{a['id']}/rollback", json={})
    assert refused.status_code == 409
    assert "replaced" in error(refused)["message"]
    assert len(ids_labelled(fake_device, APP_LABEL)) == 1

    rolled = data(await client.post(f"/api/profile-drafts/{b['id']}/rollback", json={}))
    assert rolled["status"] == "discarded"
    assert len(ids_labelled(fake_device, APP_LABEL)) == 1


async def test_a_rollback_checks_the_pushed_profile_before_it_restores_anything(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await app_copy(app, client, provider, 8)
    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7))
    assert second["replaced_device_profile_id"]
    for profile in fake_device.profiles:
        if profile["id"] == second["pushed_device_profile_id"]:
            profile["temperature"] = 91
    saves = kinds(await audit(app), "profile_save")
    mark = len(fake_device.ws_requests)

    rolled = data(await client.post(f"/api/profile-drafts/{second['id']}/rollback", json={}))

    assert rolled["status"] == "pushed"
    assert rolled["outcome"]["kept_reason"] == "changed on the display since"
    assert kinds(await audit(app), "profile_save") == saves
    assert "req:profiles:save" not in fake_device.ws_requests[mark:]


async def test_a_restore_that_does_not_verify_leaves_no_extra_copy(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await app_copy(app, client, provider, 8)
    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7))
    fake_device.mutate_on_save = lambda stored: {**stored, "temperature": 80}

    rolled = data(await client.post(f"/api/profile-drafts/{second['id']}/rollback", json={}))
    again = data(await client.post(f"/api/profile-drafts/{second['id']}/rollback", json={}))

    assert ids_labelled(fake_device, APP_LABEL) == [second["pushed_device_profile_id"]]
    assert "removed again" in " ".join(rolled["outcome"]["lines"])
    assert again["status"] == "pushed"


async def test_a_rollback_repoints_only_the_versions_the_replace_cleared(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    mine, bystander = await make_set(client, "Mine"), await make_set(client, "Bystander")
    first = await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8), set_id=mine)
    old_id = first["pushed_device_profile_id"]
    # A version of another Set holding the same content, never pushed from here.
    added = await client.post(
        f"/api/sets/{bystander}/versions",
        json={"profile_version_id": first["draft_version_id"], "intent": "same content"},
    )
    assert added.status_code < 300, added.text
    # The bystander then moves on to another profile, so it no longer stands on this one.
    moved = await client.post(
        f"/api/sets/{bystander}/versions",
        json={"profile_version_id": await base_version_id(app), "intent": "moved on"},
    )
    assert moved.status_code < 300, moved.text
    second = await pushed(client, await draft_of(app, client, provider, APP_LABEL, 7), set_id=mine)
    assert second["replaced_device_profile_id"] == old_id

    data(await client.post(f"/api/profile-drafts/{second['id']}/rollback", json={}))

    restored = next(i for i in ids_labelled(fake_device, APP_LABEL))
    assert restored in await set_device_ids(app, mine)
    assert restored not in await set_device_ids(app, bystander)


# ── the switch, the gate and the race ────────────────────────────────


async def test_a_push_that_would_reuse_a_profile_is_still_refused_with_writes_off(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await app_copy(app, client, provider, 8)
    draft = await draft_of(app, client, provider, BASE_LABEL, 8)
    assert (
        await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    ).status_code == 200
    off = await client.patch("/api/settings", json={"deviceWritesEnabled": False})
    assert off.status_code == 200, off.text
    before = len(await audit(app))
    mark = len(fake_device.ws_requests)

    response = await client.post(f"/api/profile-drafts/{draft['id']}/push", json={})

    assert response.status_code == 403
    assert fake_device.ws_requests[mark:] == []
    assert [(r.kind, r.result) for r in (await audit(app))[before:]] == [
        ("profile_save", "refused")
    ]


async def test_a_rollback_is_refused_with_writes_off_before_the_machine_is_read(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    off = await client.patch("/api/settings", json={"deviceWritesEnabled": False})
    assert off.status_code == 200, off.text
    before = len(await audit(app))
    mark = len(fake_device.ws_requests)

    response = await client.post(f"/api/profile-drafts/{first['id']}/rollback", json={})

    assert response.status_code == 403
    assert fake_device.ws_requests[mark:] == []
    assert [(r.kind, r.result) for r in (await audit(app))[before:]] == [
        ("profile_delete", "refused")
    ]


async def test_a_refused_carry_after_the_save_still_records_the_saved_profile(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice, monkeypatch: Any
) -> None:
    from gaggiclanker.device.writes import DeviceWriteRefused
    from gaggiclanker.drafts.gate import SettingsWriteGate

    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    old_id = first["pushed_device_profile_id"]
    fake_device.selected_profile_id = old_id
    draft = await draft_of(app, client, provider, APP_LABEL, 7)
    real = SettingsWriteGate.authorize

    async def refuse_carry(self: Any, write: Any) -> None:
        if write.kind in ("profile_select", "profile_favorite", "profile_delete"):
            raise DeviceWriteRefused("switched off mid-push")
        await real(self, write)

    monkeypatch.setattr(SettingsWriteGate, "authorize", refuse_carry)

    second = await pushed(client, draft)

    assert second["status"] == "pushed"
    assert second["pushed_device_profile_id"] in on_machine(fake_device)
    assert old_id in on_machine(fake_device)
    assert "switched off mid-push" in second["outcome"]["kept_reason"]


async def test_an_edit_made_while_the_push_moves_the_star_is_not_deleted(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice, monkeypatch: Any
) -> None:
    """The content is checked again immediately before the delete."""
    from gaggiclanker.device.client import GaggimateClient

    app, client = writes_on
    first = await app_copy(app, client, provider, 8)
    old_id = first["pushed_device_profile_id"]
    fake_device.selected_profile_id = old_id
    draft = await draft_of(app, client, provider, APP_LABEL, 7)
    real = GaggimateClient.select_profile

    async def select_then_edit(self: Any, profile_id: str) -> None:
        await real(self, profile_id)
        for profile in fake_device.profiles:
            if profile["id"] == old_id:
                profile["temperature"] = 91

    monkeypatch.setattr(GaggimateClient, "select_profile", select_then_edit)
    mark = len(fake_device.ws_requests)

    second = await pushed(client, draft)

    assert old_id in on_machine(fake_device)
    assert "req:profiles:delete" not in fake_device.ws_requests[mark:]
    assert second["outcome"]["kept_reason"] == "changed on the display since"


# ── a Set's profile stays its own through versions that push nothing ──


async def test_a_set_push_after_a_grind_version_still_replaces_the_previous_profile(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    w = await app_copy(app, client, provider, 8)
    set_a = await make_set_on(client, "A", w["draft_version_id"])
    document = document_of(fake_device, w["pushed_device_profile_id"])
    p1 = await pushed(
        client, await manual_from(client, w["draft_version_id"], document, 7), set_id=set_a
    )
    x = p1["pushed_device_profile_id"]
    await grind_change(client, set_a)
    # The grind version carries the device id forward, so the stored data stays truthful.
    assert (await set_device_ids(app, set_a))[-1] == x

    p2 = await pushed(
        client,
        await manual_from(client, p1["draft_version_id"], document_of(fake_device, x), 6),
        set_id=set_a,
    )

    assert x not in on_machine(fake_device)
    assert p2["replaced_device_profile_id"] == x
    # The profile the Set was made from (not pushed for it) is still there beside.
    assert w["pushed_device_profile_id"] in on_machine(fake_device)


async def test_the_lookback_finds_the_profile_when_older_data_never_carried_the_id(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """Data from before the carry-forward: the grind version has no device id at all."""
    app, client = writes_on
    w = await app_copy(app, client, provider, 8)
    set_a = await make_set_on(client, "A", w["draft_version_id"])
    document = document_of(fake_device, w["pushed_device_profile_id"])
    p1 = await pushed(
        client, await manual_from(client, w["draft_version_id"], document, 7), set_id=set_a
    )
    x = p1["pushed_device_profile_id"]
    await grind_change(client, set_a)
    await app.state.db.execute(
        "UPDATE set_versions SET pushed_device_profile_id = NULL "
        "WHERE set_id = ? AND version_no = (SELECT MAX(version_no) FROM set_versions "
        "WHERE set_id = ?)",
        (set_a, set_a),
    )

    p2 = await pushed(
        client,
        await manual_from(client, p1["draft_version_id"], document_of(fake_device, x), 6),
        set_id=set_a,
    )

    assert p2["replaced_device_profile_id"] == x


async def test_a_set_rollback_takes_the_profile_the_restored_recipe_has_on_the_machine(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    w = await app_copy(app, client, provider, 8)
    set_a = await make_set_on(client, "A", w["draft_version_id"])
    p1 = await pushed(
        client,
        await manual_from(
            client,
            w["draft_version_id"],
            document_of(fake_device, w["pushed_device_profile_id"]),
            7,
        ),
        set_id=set_a,
    )
    await grind_change(client, set_a)
    versions = await app.state.db.fetch_all(
        "SELECT id FROM set_versions WHERE set_id = ? ORDER BY version_no", (set_a,)
    )
    target = versions[-2]["id"]  # the push itself

    rolled = await client.post(f"/api/sets/{set_a}/rollback", json={"to_version_id": target})
    assert rolled.status_code < 300, rolled.text

    assert (await set_device_ids(app, set_a))[-1] == p1["pushed_device_profile_id"]


async def test_another_set_that_picked_the_profile_from_the_library_keeps_it(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """Named by stored profile, not by device id: it was never pushed for that Set."""
    app, client = writes_on
    w = await app_copy(app, client, provider, 8)
    x = w["pushed_device_profile_id"]
    await make_set_on(client, "B", w["draft_version_id"])

    tweak = await pushed(
        client,
        await manual_from(client, w["draft_version_id"], document_of(fake_device, x), 7),
    )

    assert x in on_machine(fake_device)
    assert "B" in tweak["outcome"]["kept_reason"]


async def test_a_set_that_shared_a_profile_by_reuse_keeps_it_after_a_grind_change(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    w = await app_copy(app, client, provider, 8)
    set_a = await make_set_on(client, "A", w["draft_version_id"])
    set_b = await make_set_on(client, "B", w["draft_version_id"])
    document = document_of(fake_device, w["pushed_device_profile_id"])
    pa = await pushed(
        client, await manual_from(client, w["draft_version_id"], document, 7), set_id=set_a
    )
    x = pa["pushed_device_profile_id"]
    pb = await pushed(
        client, await manual_from(client, w["draft_version_id"], document, 7), set_id=set_b
    )
    assert pb["pushed_device_profile_id"] == x
    await grind_change(client, set_b)

    pa2 = await pushed(
        client,
        await manual_from(client, pa["draft_version_id"], document_of(fake_device, x), 6),
        set_id=set_a,
    )

    assert x in on_machine(fake_device)
    assert "B" in pa2["outcome"]["kept_reason"]


# ── a failed push is removed against what the machine stored ─────────


async def test_rolling_back_a_failed_push_keeps_a_profile_edited_on_the_display_since(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    fake_device.mutate_on_save = lambda stored: {**stored, "temperature": 80}
    failed = await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8))
    assert failed["status"] == "failed"
    stored_id = failed["pushed_device_profile_id"]
    for profile in fake_device.profiles:
        if profile["id"] == stored_id:
            profile["temperature"] = 91

    rolled = data(await client.post(f"/api/profile-drafts/{failed['id']}/rollback", json={}))

    assert stored_id in on_machine(fake_device)
    assert rolled["outcome"]["kept_reason"] == "changed on the display since"
    assert rolled["status"] == "failed"


async def test_rolling_back_a_failed_push_removes_what_the_machine_stored(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    fake_device.mutate_on_save = lambda stored: {**stored, "temperature": 80}
    failed = await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8))
    stored_id = failed["pushed_device_profile_id"]

    rolled = data(await client.post(f"/api/profile-drafts/{failed['id']}/rollback", json={}))

    assert stored_id not in on_machine(fake_device)
    assert rolled["pushed_device_profile_id"] is None


async def test_the_lookback_prefers_the_newest_version_naming_the_profile(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """Two versions hold one stored profile under different device ids: the newer id wins."""
    app, client = writes_on
    w = await app_copy(app, client, provider, 8)
    set_a = await make_set_on(client, "A", w["draft_version_id"])
    document = document_of(fake_device, w["pushed_device_profile_id"])
    first = await pushed(
        client, await manual_from(client, w["draft_version_id"], document, 7), set_id=set_a
    )
    old_id = first["pushed_device_profile_id"]
    # The first copy is deleted on the display; the same content is pushed again.
    fake_device.profiles[:] = [p for p in fake_device.profiles if p["id"] != old_id]
    again = await pushed(
        client, await manual_from(client, w["draft_version_id"], document, 7), set_id=set_a
    )
    new_id = again["pushed_device_profile_id"]
    assert new_id != old_id
    assert (await set_device_ids(app, set_a))[-2:] == [old_id, new_id]

    third = await pushed(
        client,
        await manual_from(client, first["draft_version_id"], document_of(fake_device, new_id), 6),
        set_id=set_a,
    )

    assert third["replaced_device_profile_id"] == new_id
    assert new_id not in on_machine(fake_device)


async def test_rolling_back_a_failed_push_is_refused_while_a_set_names_the_failed_copy(
    writes_on: Live, provider: FakeProvider, fake_device: FakeDevice
) -> None:
    app, client = writes_on
    fake_device.mutate_on_save = lambda stored: {**stored, "temperature": 80}
    failed = await pushed(client, await draft_of(app, client, provider, BASE_LABEL, 8))
    stored_id = failed["pushed_device_profile_id"]
    set_a = await make_set(client, "Names the failed copy")
    adopted = await client.post(
        f"/api/sets/{set_a}/versions", json={"pushed_device_profile_id": stored_id, "intent": "x"}
    )
    assert adopted.status_code < 300, adopted.text

    rolled = data(await client.post(f"/api/profile-drafts/{failed['id']}/rollback", json={}))

    assert stored_id in on_machine(fake_device)
    assert "Names the failed copy" in rolled["outcome"]["kept_reason"]
    assert rolled["status"] == "failed"
