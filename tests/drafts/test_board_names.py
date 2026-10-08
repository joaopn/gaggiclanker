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

from gaggiclanker.db.repos.lineage import taken_name_sentence
from gaggiclanker.db.repos.profile_board import BoardRowPatch, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from tests.drafts.conftest import BASE_LABEL, base_version_id, error
from tests.drafts.helpers import manual_draft, same_name_draft
from tests.drafts.test_board import (
    adopted,
    get_board,
    pull,
    row_for,
    summary_of,
    write_frames,
)
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

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


# ── the twin of the creation guard, at the put ───────────────────────


async def _display_made(app: FastAPI, fake: FakeDevice, device_id: str, label: str) -> None:
    """A profile somebody makes on the display; the next sync adopts it into the list."""
    fake.profiles.append(_file(fake, device_id, label, 95))
    await pull(app)


async def test_a_renamed_draft_is_not_put_once_its_name_became_a_profile(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writes_on
    draft = await manual_draft(app, client, BASE_LABEL, "Bloom", 7)  # the name is free now
    await _display_made(app, fake_device, "bloomdisp", "Bloom")  # and then it is not
    rows = len((await get_board(client))["rows"])

    refused = await client.post("/api/profile-board", json={"draft_id": draft["id"]})

    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence("Bloom")
    assert len((await get_board(client))["rows"]) == rows
    assert (await client.get(f"/api/profile-drafts/{draft['id']}")).json()["data"]["draft"][
        "status"
    ] == "draft", "refused whole: not half approved"


async def test_a_new_draft_is_not_put_once_its_name_became_a_profile(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    base = await ProfilesRepository(app.state.db).empty_base()
    version = await ProfilesRepository(app.state.db).get_version(base)
    assert version is not None and version.profile is not None
    new = await app.state.draft_proposals.create_manual(
        base_version_id=base, document={**version.profile, "label": "Bloom"}, is_new=True
    )
    await _display_made(app, fake_device, "bloomdisp", "Bloom")

    refused = await client.post("/api/profile-board", json={"draft_id": new.id})

    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence("Bloom")


async def test_a_change_that_keeps_its_base_versions_name_is_still_put(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client = writes_on
    await pull(app)
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 7)

    put = await client.post("/api/profile-board", json={"draft_id": draft["id"]})

    assert put.status_code == 201


# ── names that differ only in case or surrounding whitespace ─────────


@pytest.mark.parametrize("typed", ["bloom", "BLOOM", "Bloom ", " Bloom", "  bloom  "])
async def test_a_name_differing_only_in_case_or_whitespace_is_taken(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice, typed: str
) -> None:
    app, client = writes_on
    await _display_made(app, fake_device, "bloomdisp", "Bloom")
    version = await ProfilesRepository(app.state.db).get_version(
        await base_version_id(app, BASE_LABEL)
    )
    assert version is not None and version.profile is not None
    document = {**version.profile, "label": typed}

    refused = await client.post(
        "/api/profile-drafts", json={"base_version_id": version.id, "profile": document}
    )

    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence(typed.strip())


async def test_a_new_profiles_surrounding_whitespace_is_not_stored(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, _client = writes_on
    await pull(app)
    base = await ProfilesRepository(app.state.db).empty_base()
    version = await ProfilesRepository(app.state.db).get_version(base)
    assert version is not None and version.profile is not None

    new = await app.state.draft_proposals.create_manual(
        base_version_id=base, document={**version.profile, "label": "  Bloom  "}, is_new=True
    )

    assert new.draft_label == "Bloom"


async def test_a_new_names_surrounding_whitespace_is_not_stored(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await pull(app)
    draft = await manual_draft(app, client, BASE_LABEL, "  Fresh idea  ", 7)

    assert draft["draft_label"] == "Fresh idea"


async def test_a_name_taken_by_case_after_the_draft_was_made_is_not_put(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writes_on
    draft = await manual_draft(app, client, BASE_LABEL, "bloom", 7)
    await _display_made(app, fake_device, "bloomdisp", "Bloom")

    refused = await client.post("/api/profile-board", json={"draft_id": draft["id"]})

    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence("bloom")


async def test_a_profile_whose_own_name_has_trailing_whitespace_still_takes_the_name(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writes_on
    await _display_made(app, fake_device, "bloomdisp", "Bloom ")
    version = await ProfilesRepository(app.state.db).get_version(
        await base_version_id(app, BASE_LABEL)
    )
    assert version is not None and version.profile is not None

    refused = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": version.id, "profile": {**version.profile, "label": "Bloom"}},
    )

    assert refused.status_code == 409


# ── one rule for "this draft continues its base's profile" ───────────


async def _listed_other_name(
    app: FastAPI, row: dict[str, Any], name: str, temperature: float
) -> dict[str, Any]:
    """A version the profile lists under another name (a display rename kept as history)."""
    profiles = ProfilesRepository(app.state.db)
    current = await profiles.get_version(row["current_version_id"])
    assert current is not None and current.profile is not None
    version, _ = await profiles.ensure_version(
        Profile.model_validate({**current.profile, "label": name, "temperature": temperature})
    )
    await ProfileBoardRepository(app.state.db).add_version(
        row["id"], version.id, "edited_on_machine"
    )
    assert version.profile is not None
    return {"id": version.id, "profile": dict(version.profile)}


async def _landing_of(client: httpx.AsyncClient, draft_id: int) -> dict[str, Any]:
    board = await get_board(client)
    [found] = [x for x in board["landings"] if x["draft_id"] == draft_id]
    return dict(found["plain"])


async def test_restoring_the_profiles_name_from_a_version_listed_under_another_lands_on_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    morning = await _listed_other_name(app, row, "Morning", 90)

    created = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": morning["id"],
            "profile": {**morning["profile"], "label": BASE_LABEL, "temperature": 91},
        },
    )

    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    assert draft["draft_label"] == BASE_LABEL
    landing = await _landing_of(client, draft["id"])
    assert landing["row_id"] == row["id"] and landing["refused"] is None
    put = await client.post("/api/profile-board", json={"draft_id": draft["id"]})
    assert put.status_code == 201 and put.json()["data"]["id"] == row["id"]


async def test_a_draft_from_a_version_listed_under_a_taken_name_is_refused_everywhere(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """P lists a version named like another live profile Q. Keeping that name is not P's name,
    so it is a rename onto Q: refused when drafted, and (if Q appeared after) by landing and put."""
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    levertest = await _listed_other_name(app, row, "Lever test", 90)
    body = {
        "base_version_id": levertest["id"],
        "profile": {**levertest["profile"], "temperature": 91},
    }
    early = (await client.post("/api/profile-drafts", json=body)).json()["data"]  # Q is not live
    await _display_made(app, fake, "levertest", "Lever test")

    late = await client.post("/api/profile-drafts", json=body)

    sentence = taken_name_sentence("Lever test")
    assert late.status_code == 409 and error(late)["message"] == sentence
    assert (await _landing_of(client, early["id"]))["refused"] == sentence
    put = await client.post("/api/profile-board", json={"draft_id": early["id"]})
    assert put.status_code == 409 and error(put)["message"] == sentence


async def test_the_landing_and_the_put_agree_on_every_case(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    morning = await _listed_other_name(app, row, "Morning", 90)
    drafts: dict[str, int] = {}
    ok = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    drafts["change keeping the name"] = ok["id"]
    restore = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": morning["id"],
            "profile": {**morning["profile"], "label": BASE_LABEL, "temperature": 92},
        },
    )
    drafts["restoring the name from a renamed version"] = restore.json()["data"]["id"]
    drafts["a fork whose name appears"] = (await manual_draft(app, client, BASE_LABEL, "Late", 6))[
        "id"
    ]
    base = await ProfilesRepository(app.state.db).empty_base()
    version = await ProfilesRepository(app.state.db).get_version(base)
    assert version is not None and version.profile is not None
    new = await app.state.draft_proposals.create_manual(
        base_version_id=base, document={**version.profile, "label": "Late New"}, is_new=True
    )
    drafts["a new profile whose name appears"] = new.id
    await _display_made(app, fake, "late1", "Late")
    await _display_made(app, fake, "late2", "late new")

    for case, draft_id in drafts.items():
        landing = await _landing_of(client, draft_id)
        put = await client.post("/api/profile-board", json={"draft_id": draft_id})
        if landing["refused"] is None:
            assert put.status_code == 201, (case, put.text)
        else:
            assert put.status_code == 409, case
            assert error(put)["message"] == landing["refused"], case
    assert sum(1 for c in drafts if "appears" in c) == 2


async def test_a_continuation_through_an_unlisted_version_keeps_the_profiles_own_name(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """A stored version in no profile's list, named like a live profile but for its case. A draft
    from it that keeps that label continues the profile, and is stored under the profile's name:
    a name never changes through a version, and the put writes the draft's label to the row."""
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    current = await ProfilesRepository(app.state.db).get_version(row["current_version_id"])
    assert current is not None and current.profile is not None
    lower = BASE_LABEL.lower()
    stray, _ = await ProfilesRepository(app.state.db).ensure_version(
        Profile.model_validate({**current.profile, "label": lower, "temperature": 91})
    )
    assert stray.profile is not None

    created = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": stray.id, "profile": {**stray.profile, "temperature": 92}},
    )

    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    assert draft["draft_label"] == BASE_LABEL
    stored = await ProfilesRepository(app.state.db).get_version(draft["draft_version_id"])
    assert stored is not None and stored.label == BASE_LABEL
    put = await client.post("/api/profile-board", json={"draft_id": draft["id"]})
    assert put.status_code == 201 and put.json()["data"]["id"] == row["id"]
    assert put.json()["data"]["label"] == BASE_LABEL
