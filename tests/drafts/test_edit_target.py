"""A change that keeps a profile's name is a new version of that profile, whoever made it.

The label used to decide, and only matched the version a profile was currently on: an edit of an
older version made a profile of its own whose name was taken, a dead end. A hand edit is also the
person's own, never the agent's (``made_by``).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.lineage import taken_name_sentence
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.infra.errors import Conflict
from tests.drafts.conftest import BASE_LABEL, base_profile, base_version_id, data, error
from tests.drafts.helpers import APP_LABEL, draft_of, make_set_on, same_name_draft
from tests.drafts.test_board import adopted, app_row, get_board, pull, put, row_for, summary_of
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]


async def edit_copy(
    app: FastAPI,
    client: httpx.AsyncClient,
    version_id: int,
    bar: float,
) -> dict[str, Any]:
    """What the editor sends: the document of a version, one pump changed, and its profile."""
    version = await ProfilesRepository(app.state.db).get_version(version_id)
    assert version is not None and version.profile is not None
    document = copy.deepcopy(dict(version.profile))
    document.pop("id", None)
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    body: dict[str, Any] = {
        "base_version_id": version_id,
        "profile": document,
        "change_summary": "Edited by hand.",
    }
    response = await client.post("/api/profile-drafts", json=body)
    assert response.status_code == 201, response.text
    return dict(data(response))


async def versions_of(client: httpx.AsyncClient, row_id: int) -> list[dict[str, Any]]:
    return list(data(await client.get(f"/api/profile-board/{row_id}/versions"))["versions"])


async def two_version_app_profile(
    app: FastAPI, client: httpx.AsyncClient, fake: FakeDevice, provider: FakeProvider
) -> dict[str, Any]:
    """An app-made profile with two versions: the first one is the older one."""
    await app_row(app, client, fake, provider, 8)
    row = row_for(await get_board(client), APP_LABEL)
    first = row["row"]["current_version_id"]
    second = await edit_copy(app, client, first, 7)
    await put(client, second)
    return {"id": row["row"]["id"], "first": first}


async def test_an_edit_of_the_active_version_lands_on_its_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    row = row_for(await get_board(client), APP_LABEL)["row"]

    draft = await edit_copy(app, client, row["current_version_id"], 6)
    landed = await put(client, draft)

    assert landed["id"] == row["id"]
    board = await get_board(client)
    assert [r["row"]["label"] for r in board["rows"]].count(APP_LABEL) == 1


async def test_an_edit_of_an_older_version_lands_on_its_profile_not_a_dead_end(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    profile = await two_version_app_profile(app, client, fake, provider)

    draft = await edit_copy(app, client, profile["first"], 5)
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]

    assert proposal["row_id"] == profile["id"]
    assert (await put(client, draft))["id"] == profile["id"]
    assert len(await versions_of(client, profile["id"])) == 3


async def test_an_edit_of_a_profile_the_app_did_not_make_is_a_version_of_it_with_its_name(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """The firmware's own profile: no second profile, and its name never changes."""
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]

    draft = await edit_copy(app, client, row["current_version_id"], 6)
    landed = await put(client, draft)

    assert draft["draft_label"] == BASE_LABEL
    assert landed["id"] == row["id"] and landed["label"] == BASE_LABEL
    assert len(await versions_of(client, row["id"])) == 2

    # It is pushed under the profile's own name, replacing the machine's file; the next sync
    # finds the machine holding exactly the stored version: no conflict.
    first = await pull(app)
    assert first.status == "ok", first.error
    assert [p["label"] for p in fake.profiles].count(BASE_LABEL) == 1
    assert [p["label"] for p in fake.profiles].count(APP_LABEL) == 0
    assert summary_of(first)["conflicts"] == []
    second = await pull(app)
    assert summary_of(second)["conflicts"] == []
    after = row_for(await get_board(client), BASE_LABEL)
    assert after["in_conflict"] is False and after["machine"]["holds_current"] is True


async def test_the_editors_live_preview_shows_the_name_the_copy_will_be_saved_under(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """The editor's "it would be saved as" line must be the label the draft really gets."""
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    version = await ProfilesRepository(app.state.db).get_version(row["current_version_id"])
    assert version is not None and version.profile is not None
    document = copy.deepcopy(dict(version.profile))
    document.pop("id", None)

    kept = data(
        await client.post(
            "/api/profile-drafts/preview",
            json={"base_version_id": version.id, "profile": document},
        )
    )
    document["label"] = "Something else"
    renamed = data(
        await client.post(
            "/api/profile-drafts/preview",
            json={"base_version_id": version.id, "profile": document},
        )
    )

    assert kept["profile"]["label"] == BASE_LABEL
    assert renamed["profile"]["label"] == "Something else"


async def test_the_agents_change_to_a_profile_the_app_did_not_make_is_a_version_of_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """``draft_profile``, refine, the starting point: the base's own name kept."""
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]

    draft = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]

    assert draft["draft_label"] == BASE_LABEL
    assert proposal["row_id"] == row["id"]
    assert (await put(client, draft))["id"] == row["id"]
    sources = {v["version_id"]: v["source"] for v in await versions_of(client, row["id"])}
    assert sources[draft["draft_version_id"]] == "agent"


async def test_the_agents_change_for_a_set_to_a_profile_the_app_did_not_make_is_a_version_of_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    set_id = await make_set_on(client, "Firmware set", row["current_version_id"])
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    await app.state.db.execute(
        "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, draft["id"])
    )

    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]

    assert proposal["landing"]["for_set"]["row_id"] == row["id"]
    assert proposal["landing"]["plain"]["row_id"] == row["id"]
    assert (await put(client, draft, set_id=set_id))["id"] == row["id"]


async def test_a_fork_under_another_name_is_a_profile_of_its_own_with_that_name(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """A fork under another name, as a design or a starting point writes one: a profile of its
    own, stored under exactly the name it was given."""
    app, client, _ = adopted

    forked = await draft_of(app, client, provider, BASE_LABEL, 7)
    landed = await put(client, forked)

    assert forked["draft_label"] == APP_LABEL
    assert landed["label"] == APP_LABEL
    assert landed["id"] != row_for(await get_board(client), BASE_LABEL)["row"]["id"]


async def test_an_edit_that_renames_the_profile_is_a_profile_of_its_own(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """A name never changes through a version: a changed name is a different profile."""
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    version = await ProfilesRepository(app.state.db).get_version(row["current_version_id"])
    assert version is not None and version.profile is not None
    document = copy.deepcopy(dict(version.profile))
    document.pop("id", None)
    document["label"] = "Something else"
    draft = dict(
        data(
            await client.post(
                "/api/profile-drafts",
                json={
                    "base_version_id": row["current_version_id"],
                    "profile": document,
                },
            )
        )
    )
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]

    assert draft["draft_label"] == "Something else"
    assert proposal["row_id"] is None
    landed = await put(client, draft)
    assert landed["id"] != row["id"]
    assert row_for(await get_board(client), BASE_LABEL)["row"]["label"] == BASE_LABEL


async def test_a_drafted_rename_onto_another_profiles_name_is_refused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The model's own draft (notes, no document) goes through the same constructor: the same
    refusal, the same sentence, and a draft that keeps the base's name is fine."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)  # a profile named APP_LABEL
    base = await base_profile(app, BASE_LABEL)
    document = base.model_dump(mode="json", exclude={"annotations", "id"})
    body = {"base_version_id": await base_version_id(app, BASE_LABEL), "notes": "rename it"}
    document["label"] = APP_LABEL
    provider.script = [json.dumps({"profile": document, "change_summary": "renamed"})]

    refused = await client.post("/api/profile-drafts", json=body)

    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence(APP_LABEL)
    document["label"] = BASE_LABEL
    provider.script = [json.dumps({"profile": document, "change_summary": "same name"})]
    assert (await client.post("/api/profile-drafts", json=body)).status_code == 201


async def test_two_edits_of_one_profile_are_independent_candidates(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """Making one active never blocks or undoes the other: both stay proposed until answered."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    row = row_for(await get_board(client), APP_LABEL)["row"]
    one = await edit_copy(app, client, row["current_version_id"], 6)
    two = await edit_copy(app, client, row["current_version_id"], 5)

    await put(client, one)

    board = await get_board(client)
    [still] = [p for p in board["proposals"] if p["draft"]["id"] == two["id"]]
    assert still["row_id"] == row["id"]
    assert (await put(client, two))["id"] == row["id"]
    assert len(await versions_of(client, row["id"])) == 3


async def test_a_proposal_older_than_a_waiting_one_is_still_offered_and_makes_active(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The newer proposal is put first and waits for a sync (the row's pending draft); the older
    one is neither hidden nor refused."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    row = row_for(await get_board(client), APP_LABEL)["row"]
    older = await edit_copy(app, client, row["current_version_id"], 6)
    newer = await edit_copy(app, client, row["current_version_id"], 5)

    await put(client, newer)
    waiting = row_for(await get_board(client), APP_LABEL)["row"]
    assert waiting["pending_draft_id"] == newer["id"]

    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == older["id"]]
    assert proposal["row_id"] == row["id"]
    assert (await put(client, older))["id"] == row["id"]


async def test_a_proposal_based_on_a_version_that_is_no_longer_active_still_lands_on_its_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The base is an older version of the profile, found through the profile's version list.
    Never a new profile with a taken name."""
    app, client, fake = adopted
    profile = await two_version_app_profile(app, client, fake, provider)

    stale = await edit_copy(app, client, profile["first"], 4)
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == stale["id"]]

    assert proposal["row_id"] == profile["id"]
    assert (await put(client, stale))["id"] == profile["id"]


async def test_a_proposal_based_on_a_version_the_machine_never_held_lands_on_its_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """With no file behind the base to fall back on, only the profile's version list finds it."""
    app, client, fake = adopted
    profile = await two_version_app_profile(app, client, fake, provider)
    versions = await versions_of(client, profile["id"])
    middle = versions[0]["version_id"]  # the second version: put, never synced
    third = await edit_copy(app, client, middle, 3)
    await put(client, third)

    stale = await edit_copy(app, client, middle, 2)
    assert stale["base_device_profile_id"] is None
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == stale["id"]]

    assert proposal["row_id"] == profile["id"]


async def test_a_hand_edit_is_recorded_as_the_persons_and_an_agent_draft_as_the_agents(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    row = row_for(await get_board(client), APP_LABEL)["row"]

    by_hand = await edit_copy(app, client, row["current_version_id"], 6)
    by_agent = await same_name_draft(app, client, provider, APP_LABEL, 5)

    assert by_hand["made_by"] == "edit" and by_agent["made_by"] == "agent"
    await put(client, by_hand)
    sources = {v["version_id"]: v["source"] for v in await versions_of(client, row["id"])}
    assert sources[by_hand["draft_version_id"]] == "edit"


async def test_a_change_based_on_an_unsynced_version_of_the_persons_profile_keeps_its_name(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The base was made active but never reached the machine: only the profile's version list
    knows it belongs to the person's profile, and the draft keeps that profile's name."""
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    unsynced = (await versions_of(client, row["id"]))[0]["version_id"]

    draft = await edit_copy(app, client, unsynced, 3)

    assert draft["base_device_profile_id"] is None
    assert draft["draft_label"] == BASE_LABEL
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]
    assert proposal["row_id"] == row["id"]


async def test_a_change_based_on_a_version_only_the_machine_file_holds_continues_that_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """A version mirrored under the file a profile stands on but not in its version list yet
    (the file was just edited on the display): found through the file."""
    from gaggiclanker.domain.models import Profile

    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    profiles = ProfilesRepository(app.state.db)
    base = await profiles.get_version(row["row"]["current_version_id"])
    assert base is not None and base.profile is not None
    document = copy.deepcopy(dict(base.profile))
    document["temperature"] = float(document["temperature"]) + 1
    held, _ = await profiles.ensure_version(
        Profile.model_validate(document), source="device", device_json="{}"
    )
    await app.state.db.execute(
        "UPDATE device_profiles SET current_version_id = ? WHERE device_id = ?",
        (held.id, row["machine"]["device_id"]),
    )

    draft = await edit_copy(app, client, held.id, 3)

    assert draft["base_device_profile_id"] == row["machine"]["device_id"]
    assert draft["draft_label"] == BASE_LABEL
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]
    assert proposal["row_id"] == row["row"]["id"]


async def test_a_renamed_set_draft_is_a_profile_of_its_own_and_the_firmware_profile_is_untouched(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The Set brews the firmware's profile; its draft renames it ("9 Bar Warm"). It is stored
    as "9 Bar Warm", lands as a profile of its own (the Set button and the plain one alike)
    and the next sync leaves the firmware profile where it is."""
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    set_id = await make_set_on(client, "Warm set", row["current_version_id"])
    version = await ProfilesRepository(app.state.db).get_version(row["current_version_id"])
    assert version is not None and version.profile is not None
    document = copy.deepcopy(dict(version.profile))
    document.pop("id", None)
    document["label"] = "9 Bar Warm"
    draft = dict(
        data(
            await client.post(
                "/api/profile-drafts",
                json={
                    "base_version_id": version.id,
                    "profile": document,
                    "change_summary": "warmer",
                },
            )
        )
    )
    await app.state.db.execute(
        "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, draft["id"])
    )

    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]
    assert draft["draft_label"] == "9 Bar Warm"
    # Same landing for the Set button and the plain one: a profile of its own.
    assert proposal["landing"]["plain"]["row_id"] is None
    assert proposal["landing"]["for_set"]["row_id"] is None

    made = await put(client, draft, set_id=set_id)

    assert made["id"] != row["id"] and made["label"] == "9 Bar Warm"
    assert row_for(await get_board(client), BASE_LABEL)["row"]["id"] == row["id"]
    run = await pull(app)
    assert run.status == "ok", run.error
    labels = [p["label"] for p in fake.profiles]
    assert BASE_LABEL in labels and "9 Bar Warm" in labels
    assert summary_of(run)["removed"] == []


async def test_an_edit_of_an_older_version_of_a_firmware_default_keeps_its_name(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The name comes from the profile the base belongs to, not from the version it is on now."""
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    middle = (await versions_of(client, row["id"]))[0]["version_id"]  # put, never on the machine
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 6))

    older = await edit_copy(app, client, middle, 3)
    assert older["base_device_profile_id"] is None, "only the version list knows its profile"

    assert older["draft_label"] == BASE_LABEL
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == older["id"]]
    assert proposal["row_id"] == row["id"]


async def test_a_from_scratch_draft_named_like_a_profile_is_refused_whatever_its_base(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """Stored against a real profile instead of the empty baseline, a from-scratch draft that
    carries that profile's own name does not continue it: it is refused, like any new profile
    that takes a name already in the list."""
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    version = await ProfilesRepository(app.state.db).get_version(row["current_version_id"])
    assert version is not None and version.profile is not None
    document = copy.deepcopy(dict(version.profile))
    document.pop("id", None)

    with pytest.raises(Conflict) as refused:
        await app.state.draft_proposals.create_manual(
            base_version_id=version.id, document=document, is_new=True
        )

    assert str(refused.value) == taken_name_sentence(BASE_LABEL)


async def test_a_rename_onto_another_profiles_name_is_refused_by_the_editors_route(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The JSON editor's route and an edited copy under a new name: the envelope's error, once,
    and no draft stored. A change that keeps the base profile's own name is not a rename."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)  # a second profile, named APP_LABEL
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    version = await ProfilesRepository(app.state.db).get_version(row["current_version_id"])
    assert version is not None and version.profile is not None
    document = copy.deepcopy(dict(version.profile))
    document.pop("id", None)
    drafts_before = len(data(await client.get("/api/profile-drafts"))["items"])

    document["label"] = APP_LABEL
    refused = await client.post(
        "/api/profile-drafts", json={"base_version_id": version.id, "profile": document}
    )

    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence(APP_LABEL)
    assert len(data(await client.get("/api/profile-drafts"))["items"]) == drafts_before
    document["label"] = BASE_LABEL
    kept = await client.post(
        "/api/profile-drafts", json={"base_version_id": version.id, "profile": document}
    )
    assert kept.status_code == 201


async def test_with_two_profiles_of_one_name_a_sets_draft_goes_to_the_sets_one(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """Adoption keeps a pair of profiles that share a name. The Set's own profile is the one a
    draft of that name continues for the Set; without the Set it is the first."""
    from gaggiclanker.db.repos.profile_board import BoardRowWrite, ProfileBoardRepository

    app, client, _ = adopted
    first = row_for(await get_board(client), BASE_LABEL)["row"]
    own = await same_name_draft(app, client, provider, BASE_LABEL, 5)  # a version only the twin has
    twin = await ProfileBoardRepository(app.state.db).insert(
        BoardRowWrite(
            label=BASE_LABEL,
            current_version_id=own["draft_version_id"],
            origin="adopted",
            on_machine=False,
        )
    )
    set_id = await make_set_on(client, "Twin set", own["draft_version_id"])
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    await app.state.db.execute(
        "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, draft["id"])
    )

    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]

    assert proposal["landing"]["plain"]["row_id"] == first["id"]
    assert proposal["landing"]["for_set"]["row_id"] == twin.id != first["id"]
