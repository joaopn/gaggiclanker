"""What a Set records when a profile reaches the machine for it.

A draft put on the board for a Set becomes that Set's next version at the moment the sync puts
the profile on the machine (``ProfileDraftService.attach_to_set``, the one place a Set version
is made from a draft): the content-hashed profile version and the device id the firmware
assigned, the prediction when the draft was made for this very Set, whether the version is a
minor or a major one, and who proposed it. These were pinned through the staged push; they are
pinned here through the board, which is how a profile gets there now.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetVersionPatch
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import base_profile, base_version_id, data, error
from tests.drafts.test_api import a_draft, lower_pressure
from tests.drafts.test_board import adopted, get_board, pull, put
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module


@pytest.fixture
async def a_set(adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice]) -> int:
    """A Set with one version, so a profile has somewhere to be recorded."""
    _app, client, _ = adopted
    bean = data(await client.post("/api/beans", json={"name": "Draft test", "roaster": "nobody"}))
    stored = data(
        await client.post(
            "/api/sets",
            json={
                "name": "Draft baseline",
                "bean_id": bean["id"],
                "version": {"dose_g": 18.0, "target_yield_g": 36.0},
            },
        )
    )
    return int(stored["id"])


async def another_set(client: httpx.AsyncClient) -> dict[str, Any]:
    bean = data(await client.post("/api/beans", json={"name": "Elsewhere", "roaster": "nobody"}))
    return dict(
        data(
            await client.post(
                "/api/sets",
                json={"name": "Another coffee", "bean_id": bean["id"], "automatch": False},
            )
        )
    )


async def drafted_for(
    app: FastAPI,
    set_id: int | None,
    *,
    prediction: str = "Compared to v1: less of the dry finish, and no slower.",
    compares_to_version_id: int | None = None,
    bar: float = 8.0,
    **more: Any,
) -> dict[str, Any]:
    """A draft as a Set's conversation leaves one: its Set and its prediction.

    Through `DraftProposals`, which is exactly what the chat tool is handed: the archive and
    the safety bounds, and nothing that could write to the machine.
    """
    profile = await base_profile(app)
    row = await app.state.draft_proposals.create_manual(
        base_version_id=await base_version_id(app),
        document=lower_pressure(profile, bar),
        change_summary=f"Down to {bar:g} bar.",
        notes="Proposed in chat.",
        set_id=set_id,
        prediction=prediction,
        compares_to_version_id=compares_to_version_id,
        **more,
    )
    return dict(row.model_dump(mode="json"))


async def versions_of(client: httpx.AsyncClient, set_id: int) -> list[dict[str, Any]]:
    """A Set's versions as the Set page lists them: the newest first."""
    return [e["version"] for e in data(await client.get(f"/api/sets/{set_id}"))["versions"]]


async def draft_row(client: httpx.AsyncClient, draft_id: int) -> dict[str, Any]:
    return dict(data(await client.get(f"/api/profile-drafts/{draft_id}"))["draft"])


async def put_and_sync(
    app: FastAPI, client: httpx.AsyncClient, draft: dict[str, Any], **body: Any
) -> dict[str, Any]:
    """Put a draft on the board and run the sync that sends it; return the draft as it ends."""
    await put(client, draft, **body)
    run = await pull(app)
    assert run.status == "ok", run.error
    return await draft_row(client, draft["id"])


async def test_a_put_for_a_set_records_both_identities_and_no_prediction_nobody_made(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider, a_set: int
) -> None:
    """Opt-in: a person may put a draft on the board without saying the Set now means it.

    When they do say so, the version carries the content-hashed profile version and the device
    id the firmware assigned, because "what did this Set brew" and "which file on the machine
    is that" are different questions with different answers.
    """
    app, client, _ = adopted
    draft = await a_draft(app, client, provider)

    ended = await put_and_sync(app, client, draft, set_id=a_set)

    assert ended["status"] == "pushed"
    version = (await versions_of(client, a_set))[0]
    assert version["profile_version_id"] == draft["draft_version_id"]
    assert version["pushed_device_profile_id"] == ended["pushed_device_profile_id"]
    assert version["origin"] == "manual"
    assert version["profile_label"] == "9 Bar Espresso [AI]"
    assert version["prediction"] == "" and version["compares_to_version_id"] is None


async def test_a_sets_own_draft_records_its_prediction_on_the_version(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_set: int
) -> None:
    """A profile change argued in a Set's room becomes that Set's next experiment."""
    app, client, _ = adopted
    current_id = (await versions_of(client, a_set))[0]["id"]
    draft = await drafted_for(app, a_set, compares_to_version_id=current_id)

    ended = await put_and_sync(app, client, draft, set_id=a_set)

    assert ended["status"] == "pushed"
    version = (await versions_of(client, a_set))[0]
    assert version["prediction"].startswith("Compared to v1")
    assert version["compares_to_version_id"] == current_id
    assert version["compares_to_version_no"] == 1
    assert version["profile_version_id"] == draft["draft_version_id"]
    assert version["pushed_device_profile_id"] == ended["pushed_device_profile_id"]


async def test_a_sets_draft_says_which_version_the_put_records_before_and_after(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_set: int
) -> None:
    """The card names the version before the put and after the sync, from the archive.

    Before: the Set's next number, so the button can say "record it as v1.1". After: the
    version really recorded, so the card claims the prediction landed only when it did,
    including after a reload.
    """
    app, client, _ = adopted
    draft = await drafted_for(app, a_set)
    assert draft["set_next_version_no"] == 2 and draft["recorded_version_no"] is None

    ended = await put_and_sync(app, client, draft, set_id=a_set)

    assert ended["recorded_version_no"] == 2
    assert (await draft_row(client, draft["id"]))["set_next_version_no"] == 3


async def test_a_put_for_a_set_records_a_minor_version_unless_marked_major(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_set: int
) -> None:
    """A tuned copy is dialling in, so it is a minor version by default.

    The card is served both names before the put, from the query the insert numbers with.
    The person's box wins in both directions; the agent's suggestion rides on the draft for
    the card to show and changes nothing by itself.
    """
    app, client, _ = adopted
    first = await drafted_for(
        app,
        a_set,
        suggest_major=True,
        major_reason="Eight bar is a different kind of shot from nine, not a nudge.",
    )
    assert (
        first["suggest_major"],
        first["set_next_minor_label"],
        first["set_next_major_label"],
    ) == (
        True,
        "v1.1",
        "v2",
    )

    ended = await put_and_sync(app, client, first, set_id=a_set)

    assert ended["recorded_version_label"] == "v1.1"
    second = await drafted_for(app, a_set, bar=7.0, prediction="Compared to v1.1: softer still.")
    reread = await draft_row(client, second["id"])
    assert (reread["set_next_minor_label"], reread["set_next_major_label"]) == ("v1.2", "v2")

    ended = await put_and_sync(app, client, second, set_id=a_set, major=True)

    assert ended["recorded_version_label"] == "v2"
    assert (await draft_row(client, first["id"]))["recorded_version_label"] == "v1.1"


async def test_a_put_refuses_a_major_that_is_not_a_boolean_and_changes_nothing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_set: int
) -> None:
    app, client, _ = adopted
    draft = await drafted_for(app, a_set)

    response = await client.post(
        "/api/profile-board", json={"draft_id": draft["id"], "set_id": a_set, "major": "yes"}
    )

    assert response.status_code == 400
    assert error(response)["code"] == "INVALID_REQUEST"
    assert (await draft_row(client, draft["id"]))["status"] == "draft"
    assert all(
        r["row"]["pending_draft_id"] != draft["id"] for r in (await get_board(client))["rows"]
    )


async def test_a_sets_draft_put_without_its_set_records_nothing_on_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_set: int
) -> None:
    app, client, _ = adopted
    draft = await drafted_for(app, a_set)

    ended = await put_and_sync(app, client, draft)

    assert ended["status"] == "pushed" and ended["recorded_version_no"] is None
    assert len(await versions_of(client, a_set)) == 1


async def test_a_sets_draft_put_for_another_set_records_no_prediction(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_set: int
) -> None:
    """The guess was about the other experiment and says nothing about this one."""
    app, client, _ = adopted
    other = await another_set(client)
    draft = await drafted_for(app, a_set)

    ended = await put_and_sync(app, client, draft, set_id=other["id"])

    version = (await versions_of(client, other["id"]))[0]
    assert version["set_id"] == other["id"] and version["version_no"] == 2
    assert version["prediction"] == "" and version["compares_to_version_id"] is None
    assert ended["recorded_version_no"] is None


async def test_a_draft_without_a_set_has_no_version_to_record(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await a_draft(app, client, provider)
    assert draft["set_next_version_no"] is None and draft["recorded_version_no"] is None


async def test_a_prediction_against_a_version_of_another_set_falls_back_to_the_current_one(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_set: int
) -> None:
    """A comparison that is not this Set's would render as a dangling reference."""
    app, client, _ = adopted
    other = await another_set(client)
    theirs = (await versions_of(client, other["id"]))[0]["id"]
    draft = await drafted_for(app, a_set, compares_to_version_id=theirs)

    await put_and_sync(app, client, draft, set_id=a_set)

    mine = await versions_of(client, a_set)
    assert mine[0]["compares_to_version_id"] != theirs
    assert mine[0]["compares_to_version_id"] in {v["id"] for v in mine}


async def test_the_version_a_put_for_the_sets_own_draft_records_says_the_chat_proposed_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_set: int
) -> None:
    """ "Did following the advice help" is a GROUP BY on origin.

    A profile change the agent argued for, filed under `manual`, would credit the person with
    the model's idea: the version records `chat` exactly when it is also recording the agent's
    prediction.
    """
    app, client, _ = adopted
    draft = await drafted_for(app, a_set)

    await put_and_sync(app, client, draft, set_id=a_set)

    assert (await versions_of(client, a_set))[0]["origin"] == "chat"


async def test_a_hand_drafted_put_stays_manual(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider, a_set: int
) -> None:
    app, client, _ = adopted
    draft = await a_draft(app, client, provider)

    await put_and_sync(app, client, draft, set_id=a_set)

    assert (await versions_of(client, a_set))[0]["origin"] == "manual"


async def test_a_draft_made_from_an_analysis_before_it_was_retired_stays_an_analysis(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_set: int
) -> None:
    """An older draft keeps its provenance, even with a Set and a prediction.

    Nothing makes such a draft any more; one made before the per-shot analysis was retired
    still carries `source_analysis_id`, and the version it becomes says where it came from.
    """
    app, client, _ = adopted
    draft = await drafted_for(app, a_set, prediction="Compared to v1: less of the dry finish.")
    await app.state.db.execute(
        "UPDATE profile_drafts SET source_analysis_id = 7 WHERE id = ?", (draft["id"],)
    )

    ended = await put_and_sync(app, client, draft, set_id=a_set)

    version = (await versions_of(client, a_set))[0]
    assert version["origin"] == "analysis" and version["prediction"].startswith("Compared to v1")
    assert ended["recorded_version_no"] == version["version_no"]


async def test_recording_a_version_retires_the_change_waiting_on_the_set(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], a_set: int
) -> None:
    """The Set moved on, so a proposal argued against the old recipe is not a question."""
    app, client, _ = adopted
    made = await SetProposalsRepository(app.state.db).create(
        a_set,
        ProposalWrite(
            patch=SetVersionPatch(dose_g=18.5),
            reason="Half a gram more.",
            prediction="Compared to v1: a touch more body and no slower.",
        ),
    )
    assert made.proposal is not None
    draft = await drafted_for(app, a_set)

    await put_and_sync(app, client, draft, set_id=a_set)

    assert await SetProposalsRepository(app.state.db).waiting(a_set) is None
    assert data(await client.get(f"/api/sets/{a_set}"))["proposal"] is None


async def test_a_refinement_keeps_the_agents_major_suggestion_and_the_set(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider, a_set: int
) -> None:
    """The next attempt at the same idea is no smaller a change, and on the same experiment."""
    app, client, _ = adopted
    parent = await drafted_for(
        app,
        a_set,
        suggest_major=True,
        major_reason="Eight bar is a different kind of shot from nine, not a nudge.",
    )
    provider.script = [
        json.dumps(
            {
                "profile": lower_pressure(await base_profile(app), 7.5),
                "change_summary": "7.5 bar instead.",
            }
        )
    ]

    refined = data(
        await client.post(f"/api/profile-drafts/{parent['id']}/refine", json={"notes": "Softer."})
    )

    assert (refined["suggest_major"], refined["major_reason"]) == (
        True,
        "Eight bar is a different kind of shot from nine, not a nudge.",
    )
    assert refined["set_id"] == a_set and refined["prediction"] == parent["prediction"]
    assert refined["compares_to_version_id"] == parent["compares_to_version_id"]
