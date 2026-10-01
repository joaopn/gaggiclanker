"""The write primitives, directly: ``place`` and ``remove_if_ours``.

The staged push used to be the only way to reach them, so their guards were pinned through
its HTTP routes. The profile board's write phase calls them now, and its own tests reach them
only through a whole sync; these are the one-rule-each tests, against the fake machine and the
real audit, for what each primitive must and must never do:

* ``place`` saves a document unless the machine already holds its exact content, reads it back
  and says when what came back is not what went out (the profile is then on the machine either
  way);
* ``remove_if_ours`` takes a file off only when this app saved that id, the label on the
  machine still carries the app suffix, the content is what the archive recorded, nobody else
  stands on it, and twice (before the star and selection move, and again just before the
  delete); it moves the star and the selection to the successor first.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile, profile_content_hash, with_app_suffix
from gaggiclanker.drafts.machine import (
    CHANGED_SINCE,
    GONE,
    NOT_OURS,
    place,
    read_machine,
    remove_if_ours,
)
from tests.drafts.conftest import BASE_LABEL, base_profile
from tests.drafts.helpers import Live, audit, kinds


async def app_profile(app: Any, name: str, pressure: float) -> Profile:
    """A profile carrying the app suffix, as a draft's document does."""
    base = await base_profile(app, BASE_LABEL)
    document = base.model_dump(mode="json", exclude={"annotations", "id"})
    document["label"] = with_app_suffix(name)
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": pressure, "flow": 0}
    return Profile.model_validate(document)


def writes(fake: FakeDevice, since: int) -> list[str]:
    return [
        t
        for t in fake.ws_requests[since:]
        if t.startswith("req:profiles:")
        and t
        not in (
            "req:profiles:list",
            "req:profiles:load",
        )
    ]


def edit_on_display(fake: FakeDevice, device_id: str, **changes: Any) -> None:
    for profile in fake.profiles:
        if profile["id"] == device_id:
            profile.update(changes)


# ── place ────────────────────────────────────────────────────────────


async def test_place_saves_reads_back_and_leaves_the_machine_holding_the_document(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    client = app.state.connection.client
    machine = await read_machine(client)
    profile = await app_profile(app, "Placed", 8)

    placed = await place(client, profile, machine)

    assert placed.problem is None and placed.reused is False and placed.served is not None
    assert profile_content_hash(placed.served) == profile_content_hash(profile)
    assert placed.device_id in {str(p["id"]) for p in fake_device.profiles}
    assert machine.profiles[placed.device_id] is placed.served, "the state is kept current"
    assert kinds(await audit(app), "profile_save") == [("profile_save", placed.device_id, "ok")]


async def test_place_does_not_save_content_the_machine_already_holds(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    client = app.state.connection.client
    machine = await read_machine(client)
    profile = await app_profile(app, "Twice", 8)
    first = await place(client, profile, machine)
    saves = kinds(await audit(app), "profile_save")
    before = len(fake_device.profiles)
    mark = len(fake_device.ws_requests)

    again = await place(client, profile, await read_machine(client))

    assert again.reused is True and again.device_id == first.device_id
    assert len(fake_device.profiles) == before
    assert kinds(await audit(app), "profile_save") == saves
    assert writes(fake_device, mark) == []


async def test_place_says_when_the_machine_stored_something_else_and_keeps_both_documents(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    client = app.state.connection.client
    fake_device.mutate_on_save = lambda stored: {**stored, "temperature": 80}
    profile = await app_profile(app, "Mutated", 8)

    placed = await place(client, profile, await read_machine(client))

    assert placed.problem == "mismatch" and placed.served is not None
    assert placed.verification is not None
    assert (
        placed.verification["sent"]["temperature"] != placed.verification["loaded"]["temperature"]
    )
    assert placed.verification["sent_canonical"] != placed.verification["loaded_canonical"]
    assert placed.device_id in {str(p["id"]) for p in fake_device.profiles}, "it is on the machine"


async def test_place_says_when_what_it_saved_cannot_be_read_back(
    writes_on: Live, fake_device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = writes_on
    client = app.state.connection.client
    real = GaggimateClient.load_profile
    saved: list[bool] = []

    async def unreadable(self: GaggimateClient, profile_id: str) -> Profile:
        if saved:
            raise DeviceError("the load did not answer")
        return await real(self, profile_id)

    real_save = GaggimateClient.save_profile

    async def remember(self: GaggimateClient, profile: Profile) -> Profile:
        stored = await real_save(self, profile)
        saved.append(True)
        return stored

    monkeypatch.setattr(GaggimateClient, "save_profile", remember)
    monkeypatch.setattr(GaggimateClient, "load_profile", unreadable)
    profile = await app_profile(app, "Unreadable", 8)

    placed = await place(client, profile, await read_machine_before(client, real))

    assert placed.problem == "unreadable" and placed.served is None
    assert "could not be read back" in placed.error
    assert placed.verification == {"sent": profile.to_device(), "loaded": None}


async def read_machine_before(client: GaggimateClient, real: Any) -> Any:
    """A machine state read with the unpatched load (the patched one fails after a save)."""
    from gaggiclanker.drafts.machine import MachineState

    state = MachineState()
    for listed in await client.list_profiles():
        state.profiles[listed.id or ""] = await real(client, listed.id or "")
    return state


async def test_place_refused_with_writes_off_sends_nothing_and_is_audited(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    client = app.state.connection.client
    machine = await read_machine(client)
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    mark = len(fake_device.ws_requests)

    with pytest.raises(DeviceError):
        await place(client, await app_profile(app, "Off", 8), machine)

    assert writes(fake_device, mark) == []
    assert kinds(await audit(app), "profile_save")[-1][2] == "refused"


# ── remove_if_ours ───────────────────────────────────────────────────


async def two_copies(app: Any, fake: FakeDevice) -> tuple[Profile, str, Profile, str]:
    """The app's copy of a profile and the next version of it, both on the machine."""
    client = app.state.connection.client
    machine = await read_machine(client)
    old = await app_profile(app, "Replaced", 8)
    new = await app_profile(app, "Replaced", 7)
    first = await place(client, old, machine)
    second = await place(client, new, machine)
    assert first.problem is None and second.problem is None
    return old, first.device_id, new, second.device_id


async def test_removal_moves_the_star_and_the_selection_before_it_deletes(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    client = app.state.connection.client
    old, old_id, _, new_id = await two_copies(app, fake_device)
    fake_device.selected_profile_id = old_id
    fake_device.favorite_profile_ids.add(old_id)
    fake_device.favorite_profile_ids.discard(new_id)
    mark = len(fake_device.ws_requests)

    removal = await remove_if_ours(
        client,
        DeviceWritesRepository(app.state.db),
        device_id=old_id,
        expected_hash=profile_content_hash(old),
        successor=new_id,
    )

    assert removal.removed and removal.favorite_carried and removal.selected_carried
    assert old_id not in {str(p["id"]) for p in fake_device.profiles}
    assert new_id in fake_device.favorite_profile_ids
    assert fake_device.selected_profile_id == new_id
    assert writes(fake_device, mark) == [
        "req:profiles:favorite",
        "req:profiles:select",
        "req:profiles:delete",
    ]
    assert kinds(await audit(app), "profile_delete")[-1] == ("profile_delete", old_id, "ok")


@pytest.mark.parametrize("startup", [True, False])
async def test_the_startup_profile_is_reported_only_when_the_firmware_cleared_it(
    writes_on: Live, fake_device: FakeDevice, startup: bool
) -> None:
    app, _ = writes_on
    old, old_id, _, new_id = await two_copies(app, fake_device)
    if startup:
        fake_device.device_settings["startupProfile"] = old_id

    removal = await remove_if_ours(
        app.state.connection.client,
        DeviceWritesRepository(app.state.db),
        device_id=old_id,
        expected_hash=profile_content_hash(old),
        successor=new_id,
    )

    assert removal.removed and removal.startup_cleared is startup


async def test_a_profile_the_person_made_is_never_removed_whatever_it_is_called(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    """The label alone proves nothing: this app never saved that id."""
    app, _ = writes_on
    handmade = copy.deepcopy(next(p for p in fake_device.profiles if p["label"] == BASE_LABEL))
    handmade.update(id="hand0001", label="Hand made [AI]")
    fake_device.profiles.append(handmade)
    mark = len(fake_device.ws_requests)

    removal = await remove_if_ours(
        app.state.connection.client,
        DeviceWritesRepository(app.state.db),
        device_id="hand0001",
        expected_hash=None,
        successor=None,
    )

    assert not removal.removed and removal.reason == NOT_OURS
    assert "hand0001" in {str(p["id"]) for p in fake_device.profiles}
    assert writes(fake_device, mark) == []
    assert kinds(await audit(app), "profile_delete") == []


async def test_a_copy_this_app_saved_but_renamed_on_the_display_is_not_removed(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    """The audit alone proves nothing either: the label on the machine right now counts."""
    app, _ = writes_on
    old, old_id, _, _ = await two_copies(app, fake_device)
    edit_on_display(fake_device, old_id, label="Renamed by hand")

    removal = await remove_if_ours(
        app.state.connection.client,
        DeviceWritesRepository(app.state.db),
        device_id=old_id,
        expected_hash=profile_content_hash(old),
        successor=None,
    )

    assert not removal.removed and removal.reason == NOT_OURS
    assert old_id in {str(p["id"]) for p in fake_device.profiles}


async def test_a_copy_edited_on_the_display_since_is_kept(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    old, old_id, _, new_id = await two_copies(app, fake_device)
    fake_device.selected_profile_id = old_id
    edit_on_display(fake_device, old_id, temperature=91)
    mark = len(fake_device.ws_requests)

    removal = await remove_if_ours(
        app.state.connection.client,
        DeviceWritesRepository(app.state.db),
        device_id=old_id,
        expected_hash=profile_content_hash(old),
        successor=new_id,
    )

    assert not removal.removed and removal.reason == CHANGED_SINCE
    assert writes(fake_device, mark) == [], "nothing moved either: the checks come first"
    assert fake_device.selected_profile_id == old_id


async def test_an_edit_made_while_the_star_moves_is_not_deleted(
    writes_on: Live, fake_device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The content is checked again immediately before the delete."""
    app, _ = writes_on
    old, old_id, _, new_id = await two_copies(app, fake_device)
    fake_device.selected_profile_id = old_id
    real = GaggimateClient.select_profile

    async def select_then_edit(self: GaggimateClient, profile_id: str) -> None:
        await real(self, profile_id)
        edit_on_display(fake_device, old_id, temperature=91)

    monkeypatch.setattr(GaggimateClient, "select_profile", select_then_edit)
    mark = len(fake_device.ws_requests)

    removal = await remove_if_ours(
        app.state.connection.client,
        DeviceWritesRepository(app.state.db),
        device_id=old_id,
        expected_hash=profile_content_hash(old),
        successor=new_id,
    )

    assert not removal.removed and removal.reason == CHANGED_SINCE
    assert "req:profiles:delete" not in fake_device.ws_requests[mark:]
    assert old_id in {str(p["id"]) for p in fake_device.profiles}


async def test_somebody_elses_claim_keeps_the_file_and_is_the_reason_given(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    old, old_id, _, new_id = await two_copies(app, fake_device)
    mark = len(fake_device.ws_requests)

    removal = await remove_if_ours(
        app.state.connection.client,
        DeviceWritesRepository(app.state.db),
        device_id=old_id,
        expected_hash=profile_content_hash(old),
        successor=new_id,
        blocked="still the current version of the Set 'Other'",
    )

    assert not removal.removed and removal.reason == "still the current version of the Set 'Other'"
    assert writes(fake_device, mark) == []


async def test_a_profile_under_another_label_is_not_this_replacements_to_remove(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    old, old_id, _, _ = await two_copies(app, fake_device)

    removal = await remove_if_ours(
        app.state.connection.client,
        DeviceWritesRepository(app.state.db),
        device_id=old_id,
        expected_hash=profile_content_hash(old),
        successor=None,
        expected_label="Some other lineage [AI]",
    )

    assert removal.unrelated and not removal.removed
    assert old_id in {str(p["id"]) for p in fake_device.profiles}


async def test_a_profile_that_is_not_on_the_machine_is_gone_not_a_failure(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    removal = await remove_if_ours(
        app.state.connection.client,
        DeviceWritesRepository(app.state.db),
        device_id="nothing01",
        expected_hash=None,
        successor=None,
    )

    assert removal.gone and removal.reason == GONE and not removal.removed


@pytest.mark.parametrize("failing", ["req:profiles:select", "req:profiles:favorite"])
async def test_a_failure_between_the_carry_and_the_delete_leaves_both_profiles(
    writes_on: Live, fake_device: FakeDevice, failing: str
) -> None:
    app, _ = writes_on
    old, old_id, _, new_id = await two_copies(app, fake_device)
    fake_device.selected_profile_id = old_id
    fake_device.favorite_profile_ids.add(old_id)
    fake_device.error_requests.add(failing)
    mark = len(fake_device.ws_requests)

    removal = await remove_if_ours(
        app.state.connection.client,
        DeviceWritesRepository(app.state.db),
        device_id=old_id,
        expected_hash=profile_content_hash(old),
        successor=new_id,
    )

    assert not removal.removed and removal.reason
    assert {old_id, new_id} <= {str(p["id"]) for p in fake_device.profiles}
    assert "req:profiles:delete" not in fake_device.ws_requests[mark:]


async def test_a_removal_the_switch_refuses_mid_way_is_a_reason_not_an_exception(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    old, old_id, _, new_id = await two_copies(app, fake_device)
    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    mark = len(fake_device.ws_requests)

    removal = await remove_if_ours(
        app.state.connection.client,
        DeviceWritesRepository(app.state.db),
        device_id=old_id,
        expected_hash=profile_content_hash(old),
        successor=new_id,
    )

    assert not removal.removed and "switched off" in removal.reason
    assert writes(fake_device, mark) == []
    assert old_id in {str(p["id"]) for p in fake_device.profiles}


async def test_a_copy_that_did_not_verify_is_removed_against_what_the_machine_stored(
    writes_on: Live, fake_device: FakeDevice
) -> None:
    """``expected_hash=None`` is for the one file whose content is known to differ."""
    app, _ = writes_on
    client = app.state.connection.client
    fake_device.mutate_on_save = lambda stored: {**stored, "temperature": 80}
    placed = await place(
        client, await app_profile(app, "Did not verify", 8), await read_machine(client)
    )
    assert placed.problem == "mismatch"

    removal = await remove_if_ours(
        client,
        DeviceWritesRepository(app.state.db),
        device_id=placed.device_id,
        expected_hash=None,
        successor=None,
    )

    assert removal.removed
    assert placed.device_id not in {str(p["id"]) for p in fake_device.profiles}
