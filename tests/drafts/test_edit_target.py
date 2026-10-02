"""``Edit a copy`` records the profile it was opened on, and a put lands on that profile.

The label used to decide, and only matched the version a profile was currently on: an edit of an
older version made a profile of its own whose name was taken, a dead end. A hand edit is also the
person's own, never the agent's (``made_by``).
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos import lineage
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, data
from tests.drafts.helpers import APP_LABEL
from tests.drafts.test_board import adopted, app_row, get_board, put, row_for
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]


async def edit_copy(
    app: FastAPI,
    client: httpx.AsyncClient,
    version_id: int,
    bar: float,
    *,
    target: int | None,
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
    if target is not None:
        body["target_row_id"] = target
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
    second = await edit_copy(app, client, first, 7, target=row["row"]["id"])
    await put(client, second)
    return {"id": row["row"]["id"], "first": first}


async def test_an_edit_of_the_active_version_lands_on_its_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    row = row_for(await get_board(client), APP_LABEL)["row"]

    draft = await edit_copy(app, client, row["current_version_id"], 6, target=row["id"])
    landed = await put(client, draft)

    assert landed["id"] == row["id"]
    board = await get_board(client)
    assert row_for(board, APP_LABEL)["edit_lands_on_label"] == APP_LABEL
    assert [r["row"]["label"] for r in board["rows"]].count(APP_LABEL) == 1


async def test_an_edit_of_an_older_version_lands_on_its_profile_not_a_dead_end(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    profile = await two_version_app_profile(app, client, fake, provider)

    draft = await edit_copy(app, client, profile["first"], 5, target=profile["id"])
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]

    # Offered on the profile it was opened from: a landing with a row, never a taken label.
    assert proposal["row_id"] == profile["id"]
    assert proposal["landing"]["plain"]["taken_label"] is None
    assert (await put(client, draft))["id"] == profile["id"]
    assert len(await versions_of(client, profile["id"])) == 3


async def test_an_edit_of_a_profile_the_app_did_not_make_is_still_a_profile_of_its_own(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """The open question: today a separate ``[AI]`` profile beside it."""
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]

    draft = await edit_copy(app, client, row["current_version_id"], 6, target=row["id"])
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]

    assert draft["draft_label"] == APP_LABEL
    assert row_for(board, BASE_LABEL)["edit_lands_on_label"] is None
    assert proposal["row_id"] is None
    landed = await put(client, draft)
    assert landed["id"] != row["id"] and landed["label"] == APP_LABEL


async def test_the_answer_to_the_open_question_is_one_constant(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flip ``EDIT_CONTINUES_ANY_PROFILE`` and an edit of a profile of the person's is a new
    version of it, under its own name: both the gate and the label follow."""
    app, client, _ = adopted
    monkeypatch.setattr(lineage, "EDIT_CONTINUES_ANY_PROFILE", True)
    row = row_for(await get_board(client), BASE_LABEL)["row"]

    draft = await edit_copy(app, client, row["current_version_id"], 6, target=row["id"])
    landed = await put(client, draft)

    assert row_for(await get_board(client), BASE_LABEL)["edit_lands_on_label"] == BASE_LABEL
    assert draft["draft_label"] == BASE_LABEL
    assert landed["id"] == row["id"] and landed["label"] == BASE_LABEL
    assert len(await versions_of(client, row["id"])) == 2


async def test_a_second_edit_of_a_profile_the_app_did_not_make_is_a_version_of_the_first_copy(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """The first edit is a new ``X [AI]``; the second finds it and is one of its versions, so
    there is never a proposal whose name is taken and that can only be declined."""
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    copy_row = await put(
        client, await edit_copy(app, client, row["current_version_id"], 6, target=row["id"])
    )
    # The board now says, before the second edit is saved, where it would go.
    assert row_for(await get_board(client), BASE_LABEL)["edit_lands_on_label"] == APP_LABEL

    second = await edit_copy(app, client, row["current_version_id"], 5, target=row["id"])
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == second["id"]]

    assert proposal["row_id"] == copy_row["id"]
    assert proposal["landing"]["plain"]["taken_label"] is None
    assert (await put(client, second))["id"] == copy_row["id"]
    assert len(await versions_of(client, copy_row["id"])) == 2


async def test_two_edits_of_one_profile_are_independent_candidates(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """Making one active never blocks or undoes the other: both stay proposed until answered."""
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    row = row_for(await get_board(client), APP_LABEL)["row"]
    one = await edit_copy(app, client, row["current_version_id"], 6, target=row["id"])
    two = await edit_copy(app, client, row["current_version_id"], 5, target=row["id"])

    await put(client, one)

    board = await get_board(client)
    [still] = [p for p in board["proposals"] if p["draft"]["id"] == two["id"]]
    assert still["row_id"] == row["id"]
    assert still["landing"]["plain"]["taken_label"] is None
    assert (await put(client, two))["id"] == row["id"]
    assert len(await versions_of(client, row["id"])) == 3


async def test_a_proposal_based_on_a_version_that_is_no_longer_active_still_lands_on_its_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """Writes never pushed the newer version: the base is an older version of the profile, found
    through the profile's version list. Never a new profile with a taken name."""
    app, client, fake = adopted
    profile = await two_version_app_profile(app, client, fake, provider)

    # No target: what the agent's drafts and the starting-point wizard make.
    stale = await edit_copy(app, client, profile["first"], 4, target=None)
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == stale["id"]]

    assert proposal["row_id"] == profile["id"]
    assert proposal["landing"]["plain"]["taken_label"] is None
    assert (await put(client, stale))["id"] == profile["id"]


async def test_a_proposal_based_on_a_version_the_machine_never_held_lands_on_its_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """With no file behind the base to fall back on, only the profile's version list finds it."""
    app, client, fake = adopted
    profile = await two_version_app_profile(app, client, fake, provider)
    versions = await versions_of(client, profile["id"])
    middle = versions[0]["version_id"]  # the second version: put, never synced
    third = await edit_copy(app, client, middle, 3, target=profile["id"])
    await put(client, third)

    stale = await edit_copy(app, client, middle, 2, target=None)
    assert stale["base_device_profile_id"] is None
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == stale["id"]]

    assert proposal["row_id"] == profile["id"]
    assert proposal["landing"]["plain"]["taken_label"] is None


async def test_a_second_edit_does_not_continue_a_same_named_profile_the_app_did_not_make(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """If the profile named ``X [AI]`` is somebody's own (not the app's), a copy of ``X`` does not
    become a version of it: it would be a new profile whose name is taken, and says so."""
    from gaggiclanker.db.repos.profile_board import BoardRowPatch, ProfileBoardRepository

    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    copy_row = await put(
        client, await edit_copy(app, client, row["current_version_id"], 6, target=row["id"])
    )
    await ProfileBoardRepository(app.state.db).update(
        copy_row["id"], BoardRowPatch(origin="adopted")
    )

    second = await edit_copy(app, client, row["current_version_id"], 5, target=row["id"])
    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == second["id"]]

    assert proposal["row_id"] is None
    assert proposal["landing"]["plain"]["taken_label"] == APP_LABEL


async def test_a_hand_edit_is_recorded_as_the_persons_and_an_agent_draft_as_the_agents(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    row = row_for(await get_board(client), APP_LABEL)["row"]

    by_hand = await edit_copy(app, client, row["current_version_id"], 6, target=row["id"])
    from tests.drafts.helpers import draft_of

    by_agent = await draft_of(app, client, provider, APP_LABEL, 5)

    assert by_hand["made_by"] == "edit" and by_agent["made_by"] == "agent"
    await put(client, by_hand)
    sources = {v["version_id"]: v["source"] for v in await versions_of(client, row["id"])}
    assert sources[by_hand["draft_version_id"]] == "edit"


async def test_a_draft_aimed_at_a_deleted_profile_is_not_continued(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """A row deleted by an earlier version stays a tombstone: nothing lands on it."""
    from tests.drafts.helpers import tombstone

    app, client, fake = adopted
    await app_row(app, client, fake, provider, 8)
    row = row_for(await get_board(client), APP_LABEL)["row"]
    draft = await edit_copy(app, client, row["current_version_id"], 6, target=row["id"])
    await tombstone(client, row["id"])

    board = await get_board(client)
    [proposal] = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]

    assert proposal["row_id"] is None


async def test_a_draft_aimed_at_a_profile_that_is_not_there_is_refused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]

    response = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": row["current_version_id"],
            "profile": (
                await ProfilesRepository(app.state.db).get_version(row["current_version_id"])
            ).profile,  # type: ignore[union-attr]
            "target_row_id": 9999,
        },
    )

    assert response.status_code == 404
