"""The five gated writes, against a fake machine that behaves like the firmware.

Not against a mock. What is being tested is a write to somebody's espresso
machine, and the interesting parts are all firmware behaviours: the id
`generateShortID` invents, the upsert `saveProfile` performs on an existing id,
the auto-favourite a new profile gets, and the exact document `writeProfile`
serialises back. A mock would assert that we called a mock.

The audit is asserted on every path, including the refusals, because the refusal
rows are the ones that answer "did anything try to write while this was off".
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.device.writes import DeviceWriteRefused
from gaggiclanker.domain.models import Profile, canonical_profile_json, with_app_suffix
from tests.drafts.conftest import base_profile, profile_fixture


async def a_new_profile(app: FastAPI, label: str | None = None) -> Profile:
    """The mirrored 9 Bar profile, ready to be saved as something of ours."""
    profile = await base_profile(app)
    return profile.for_new_device_profile(label=with_app_suffix(label or profile.label))


async def audit(app: FastAPI) -> list[tuple[str, str, str | None]]:
    rows = await DeviceWritesRepository(app.state.db).list_writes()
    return [(row.kind, row.result, row.device_id) for row in rows]


# ── the switch ───────────────────────────────────────────────────────


async def test_every_write_refuses_while_the_switch_is_off(
    live: tuple[FastAPI, object], fake_device: FakeDevice
) -> None:
    """The shipped default, and it is checked on all five rather than on one.

    A gate wired into four methods and forgotten on the fifth is exactly the
    shape of bug this costs nothing to rule out.
    """
    app, _ = live
    client = app.state.device
    before = len(fake_device.profiles)

    with pytest.raises(DeviceWriteRefused):
        await client.save_profile(await a_new_profile(app))
    for call in (
        client.select_profile("9bar"),
        client.favorite_profile("9bar"),
        client.unfavorite_profile("9bar"),
    ):
        with pytest.raises(DeviceWriteRefused):
            await call

    assert len(fake_device.profiles) == before, "something reached the machine"
    assert {result for _kind, result, _id in await audit(app)} == {"refused"}


async def test_a_refusal_is_recorded_with_the_kind_that_was_attempted(
    live: tuple[FastAPI, object],
) -> None:
    """An audit that says only "something was refused" answers nothing."""
    app, _ = live
    with pytest.raises(DeviceWriteRefused):
        await app.state.device.select_profile("9bar")
    rows = await DeviceWritesRepository(app.state.db).list_writes()
    assert rows[0].kind == "profile_select"
    assert rows[0].result == "refused"
    assert rows[0].host == app.state.device.host
    assert "switched off" in rows[0].error


# ── saving ───────────────────────────────────────────────────────────


async def test_a_save_creates_a_new_profile_and_returns_the_id_the_machine_chose(
    writes_on: tuple[FastAPI, object], fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    before = {profile["id"] for profile in fake_device.profiles}

    stored = await app.state.device.save_profile(await a_new_profile(app))

    assert stored.id is not None
    assert stored.id not in before, "the save overwrote an existing profile"
    assert stored.label.endswith("[AI]")
    assert ("profile_save", "ok", stored.id) in await audit(app)


async def test_a_new_profile_is_auto_favourited_by_the_firmware(
    writes_on: tuple[FastAPI, object], fake_device: FakeDevice
) -> None:
    """`ProfileManager.cpp:186-188`, and it is not something we asked for.

    A push therefore puts an unreviewed draft on the machine's home screen. The
    unfavorite method exists to undo that; this test is what says the problem is
    real.
    """
    app, _ = writes_on
    stored = await app.state.device.save_profile(await a_new_profile(app))
    assert stored.id in fake_device.favorite_profile_ids
    assert stored.favorite is True

    await app.state.device.unfavorite_profile(stored.id or "")
    assert stored.id not in fake_device.favorite_profile_ids


async def test_a_save_refuses_a_profile_that_already_has_an_id(
    writes_on: tuple[FastAPI, object], fake_device: FakeDevice
) -> None:
    """The upsert this client will not perform.

    `saveProfile` writes `/p/<id>.json`, so a save carrying somebody else's id
    replaces their profile. Refused before the gate is even consulted, and
    nothing reaches the machine.
    """
    app, _ = writes_on
    existing = await base_profile(app)
    with_id = existing.model_copy(update={"id": "9bar"})
    before = len(fake_device.profiles)

    with pytest.raises(DeviceWriteRefused) as caught:
        await app.state.device.save_profile(with_id)

    assert "creates new profiles only" in str(caught.value)
    assert len(fake_device.profiles) == before


async def test_what_comes_back_from_a_save_round_trips_to_the_same_canonical_form(
    writes_on: tuple[FastAPI, object],
) -> None:
    """The property the whole push flow rests on.

    `writeProfile` adds `id`, `favorite`, `selected` and `transition.target`,
    spells out a phase `temperature` of 0, and emits a transition where the
    author wrote none. None of that changes what the profile brews, and
    `canonical_profile_json` drops all of it — so a faithful machine compares
    equal and an unfaithful one does not.
    """
    app, _ = writes_on
    sent = await a_new_profile(app)
    stored = await app.state.device.save_profile(sent)
    loaded = await app.state.device.load_profile(stored.id or "")

    assert canonical_profile_json(loaded) == canonical_profile_json(sent)
    # And the added fields really are there, or the test above is vacuous.
    served = loaded.to_device()
    assert served["id"]
    assert served["phases"][0]["transition"]["target"] == "time"


# ── deleting ─────────────────────────────────────────────────────────


async def test_a_delete_needs_the_suffix_and_the_audit(
    writes_on: tuple[FastAPI, object], fake_device: FakeDevice
) -> None:
    """A profile we created, and that still says so, is the only deletable thing."""
    app, _ = writes_on
    stored = await app.state.device.save_profile(await a_new_profile(app))

    await app.state.device.delete_profile(stored.id or "")

    assert all(profile["id"] != stored.id for profile in fake_device.profiles)
    assert ("profile_delete", "ok", stored.id) in await audit(app)


async def test_a_delete_refuses_a_profile_this_box_did_not_create(
    writes_on: tuple[FastAPI, object], fake_device: FakeDevice
) -> None:
    """The provenance half. The label is right; the audit is empty.

    A person can rename a profile on the display to end in "[AI]", and after
    that the label proves nothing. The audit is what cannot be faked from the
    machine's side.
    """
    app, _ = writes_on
    fake_device.profiles.append(
        {**profile_fixture("firmware-9bar"), "id": "imposter", "label": "Not ours [AI]"}
    )

    with pytest.raises(DeviceWriteRefused) as caught:
        await app.state.device.delete_profile("imposter")

    assert "not created by this box" in str(caught.value)
    assert any(profile["id"] == "imposter" for profile in fake_device.profiles)
    assert ("profile_delete", "refused", "imposter") in await audit(app)


async def test_a_delete_refuses_a_label_without_the_suffix(
    writes_on: tuple[FastAPI, object], fake_device: FakeDevice
) -> None:
    """The label half, checked against the *machine*, not against our mirror.

    Somebody renames the profile on the display after we pushed it. The audit
    still says we created it; the display says it is now their profile. Their
    display wins.
    """
    app, _ = writes_on
    stored = await app.state.device.save_profile(await a_new_profile(app))
    for profile in fake_device.profiles:
        if profile["id"] == stored.id:
            profile["label"] = "Renamed by hand"

    with pytest.raises(DeviceWriteRefused) as caught:
        await app.state.device.delete_profile(stored.id or "")

    assert "does not carry the" in str(caught.value)
    assert any(profile["id"] == stored.id for profile in fake_device.profiles)


async def test_a_delete_of_something_that_is_not_there_says_which_rule_stopped_it(
    writes_on: tuple[FastAPI, object],
) -> None:
    """An id we never created is refused by the audit, before anything is read.

    The message is what matters: with the switch plainly on, "the switch is off"
    would send somebody to the wrong place. It names the provenance rule instead.
    """
    app, _ = writes_on
    with pytest.raises(DeviceWriteRefused) as caught:
        await app.state.device.delete_profile("nosuchid")
    assert "not created by this box" in str(caught.value)
    assert "switched off" not in str(caught.value)


async def test_a_delete_the_gate_refuses_never_reads_the_machine_either(
    live: tuple[FastAPI, object], fake_device: FakeDevice
) -> None:
    """The gate goes first, before the `req:profiles:load` that checks the label.

    The fake is told never to answer a load. If the refusal still comes back —
    immediately, and as a refusal rather than a timeout — then no frame went out.
    The machine has three WebSocket slots and a write the switch forbids should
    cost it none of them.
    """
    app, _ = live  # writes are off in this fixture
    fake_device.hang_requests.add("req:profiles:load")

    with pytest.raises(DeviceWriteRefused) as caught:
        await app.state.device.delete_profile("9bar")

    assert "switched off" in str(caught.value)
    assert ("profile_delete", "refused", "9bar") in await audit(app)


async def test_a_save_that_would_overwrite_is_audited_as_a_refusal(
    writes_on: tuple[FastAPI, object],
) -> None:
    """The row a person goes looking for: something tried to overwrite a profile.

    A refusal that leaves no trace is the one kind nobody can debug afterwards.
    """
    app, _ = writes_on
    existing = await base_profile(app)

    with pytest.raises(DeviceWriteRefused):
        await app.state.device.save_profile(existing.model_copy(update={"id": "9bar"}))

    assert ("profile_save", "refused", "9bar") in await audit(app)


# ── the rest of the surface ──────────────────────────────────────────


async def test_select_and_favorite_reach_the_machine_when_writes_are_on(
    writes_on: tuple[FastAPI, object], fake_device: FakeDevice
) -> None:
    app, _ = writes_on
    await app.state.device.select_profile("9bar")
    await app.state.device.favorite_profile("9bar")

    assert fake_device.selected_profile_id == "9bar"
    assert "9bar" in fake_device.favorite_profile_ids
    assert ("profile_select", "ok", "9bar") in await audit(app)
    assert ("profile_favorite", "ok", "9bar") in await audit(app)


async def test_a_write_the_machine_refuses_is_audited_as_failed(
    writes_on: tuple[FastAPI, object], fake_device: FakeDevice
) -> None:
    """`failed` means it reached the wire. `refused` means it did not.

    The two are fixed in different places — one is the machine, the other is a
    setting — so an audit that collapsed them would send people to the wrong one.
    """
    app, _ = writes_on
    fake_device.ota_in_progress = True

    with pytest.raises(DeviceError):
        await app.state.device.save_profile(await a_new_profile(app))

    kinds = await audit(app)
    assert ("profile_save", "failed", None) in kinds
