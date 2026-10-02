"""`/api/profile-drafts`, end to end: draft, refine, validate, read, discard.

Through HTTP rather than through the service, because what this feature ships is a sequence of
button presses and the interesting failures are all at the seams: a draft that skipped the
safety policy, a refinement that lost its Set, a pushed draft somebody discards while its
profile is still on the display. What happens after a draft is put on the board is the board's
(`test_board*.py`, `test_record_on_set.py`).

The model is scripted (`FakeProvider`) and the machine is the fake. No money is spent and no
network is touched, here or anywhere else in the offline suite.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from tests.drafts.conftest import (
    BASE_LABEL,
    base_profile,
    base_version_id,
    data,
    error,
    mirror_only,
)
from tests.drafts.helpers import APP_LABEL, tombstone
from tests.drafts.test_board import adopted, get_board, pull, put, row_for
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module


async def a_draft(
    app: FastAPI,
    client: httpx.AsyncClient,
    provider: FakeProvider,
    *,
    edit: dict[str, Any] | None = None,
    notes: str = "Softer on the ramp, please.",
) -> dict[str, Any]:
    """Ask for one draft, with the model scripted to return an edited profile."""
    profile = await base_profile(app)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    if edit:
        document.update(edit)
    provider.script = [
        json.dumps({"profile": document, "change_summary": "Dropped the pressure to 8 bar."})
    ]
    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": await base_version_id(app), "notes": notes},
    )
    return dict(data(response))


def lower_pressure(profile: Profile, bar: float) -> dict[str, Any]:
    """The edit every test here makes: one number, in one phase."""
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    return document


# ── drafting ─────────────────────────────────────────────────────────


async def test_a_draft_stores_a_profile_version_with_the_suffix_applied(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """The document a person approves is the document that gets pushed.

    The suffix is applied here rather than at push time, so the version row, the
    diff, the bytes on the wire and the round-trip comparison are all one
    document with one content hash.
    """
    app, client = live
    draft = await a_draft(app, client, provider)

    assert draft["status"] == "draft"
    assert draft["change_summary"] == "Dropped the pressure to 8 bar."
    version = await ProfilesRepository(app.state.db).get_version(draft["draft_version_id"])
    assert version is not None
    assert version.source == "draft"
    assert version.label == "9 Bar Espresso [AI]"


async def test_the_prompt_carries_the_current_profile_the_advice_and_the_notes(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """What the model is told, asserted on the call the provider received.

    The bounds are in there too: a model told the range tends to stay inside it,
    and a draft that needed no clamping is easier to read than one that needed
    six.
    """
    app, client = live
    await a_draft(app, client, provider, notes="Less harsh on the finish.")

    sent = "\n".join(message.content for message in provider.calls[0].messages)
    assert "9 Bar Espresso" in sent
    assert "Less harsh on the finish." in sent
    assert "temperature: 60-100 °C" in sent
    assert "Return every phase, in order." in sent


async def test_a_draft_cannot_name_an_analysis_and_the_prompt_says_it_has_no_advice(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """The per-shot analysis and its suggestions are gone: notes are the brief.

    A body naming an analysis or a suggestion is refused as an unknown field,
    and the prompt's advice block says there is none rather than going missing
    (a person's edited copy of the prompt may still name it).
    """
    app, client = live
    refused = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": await base_version_id(app), "analysis_id": 1},
    )
    assert refused.status_code == 400
    assert provider.calls == []

    await a_draft(app, client, provider, notes="Less harsh on the finish.")
    sent = "\n".join(message.content for message in provider.calls[0].messages)
    assert "No suggestions were attached" in sent


async def test_a_draft_with_nothing_to_go_on_is_refused_before_the_model_is_called(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """A model rewriting somebody's profile for no stated reason is not a feature."""
    app, client = live
    response = await client.post(
        "/api/profile-drafts", json={"base_version_id": await base_version_id(app)}
    )
    assert response.status_code == 400
    assert "needs something to go on" in error(response)["message"]
    assert provider.calls == []


async def test_a_drafted_profile_is_clamped_and_the_clamps_are_recorded(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """The model asks for 140 °C. The person sees that it was moved to 100.

    Clamping without the list would be exactly the silent rewrite this policy
    exists to avoid — a profile nobody approved, presented as one they did.
    """
    app, client = live
    draft = await a_draft(app, client, provider, edit={"temperature": 140})

    assert [change["field"] for change in draft["clamp_changes"]] == ["temperature"]
    assert draft["clamp_changes"][0]["after"] == 100
    version = await ProfilesRepository(app.state.db).get_version(draft["draft_version_id"])
    assert version is not None
    assert version.profile is not None
    assert version.profile["temperature"] == 100


async def test_a_drafted_profile_the_policy_cannot_fix_is_refused_with_every_reason(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """Eleven phases. No draft row, no version row, and the whole list at once."""
    app, client = live
    profile = await base_profile(app)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["phases"] = document["phases"] * 11
    provider.script = [json.dumps({"profile": document, "change_summary": "more phases"})]

    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": await base_version_id(app), "notes": "more phases"},
    )

    assert response.status_code == 422
    assert "11 phases" in error(response)["details"]["violations"][0]["message"]
    assert data(await client.get("/api/profile-drafts"))["items"] == []


async def test_a_refinement_supersedes_the_draft_it_came_from(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """Not an edit in place: the chain of attempts is what makes the notes useful."""
    app, client = live
    first = await a_draft(app, client, provider)
    profile = await base_profile(app)
    provider.script = [
        json.dumps({"profile": lower_pressure(profile, 7), "change_summary": "7 bar now"})
    ]

    second = data(
        await client.post(
            f"/api/profile-drafts/{first['id']}/refine", json={"notes": "Still too harsh."}
        )
    )

    assert second["parent_draft_id"] == first["id"]
    assert "Still too harsh." in second["notes"]
    assert (
        data(await client.get(f"/api/profile-drafts/{first['id']}"))["draft"]["status"]
        == "superseded"
    )
    # The previous attempt and what it said go into the next prompt, which is
    # the entire reason a refinement is a new call rather than a re-run.
    sent = "\n".join(message.content for message in provider.calls[-1].messages)
    assert "Dropped the pressure to 8 bar." in sent


# ── the manual editor ────────────────────────────────────────────────


async def test_a_hand_edited_document_becomes_a_draft_without_calling_a_model(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    app, client = live
    profile = await base_profile(app)

    draft = data(
        await client.post(
            "/api/profile-drafts",
            json={
                "base_version_id": await base_version_id(app),
                "profile": lower_pressure(profile, 8),
                "change_summary": "Eight bar, by hand.",
            },
        )
    )

    assert provider.calls == []
    assert draft["change_summary"] == "Eight bar, by hand."
    assert draft["status"] == "draft"


async def test_a_hand_edited_document_that_is_not_a_profile_names_the_field(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """422 with the field and the problem — never the value, which may be anything."""
    app, client = live
    response = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": await base_version_id(app),
            "profile": {"label": "Broken", "type": "standard", "phases": []},
        },
    )
    assert response.status_code == 422
    errors = error(response)["details"]["schema_errors"]
    assert any(item.startswith("phases:") for item in errors)


async def test_preview_answers_two_hundred_for_a_document_that_is_still_wrong(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """Live validation. "Not valid yet" is the normal state of a half-typed edit."""
    app, client = live
    version_id = await base_version_id(app)

    broken = data(
        await client.post(
            "/api/profile-drafts/preview",
            json={"base_version_id": version_id, "profile": {"label": "x"}},
        )
    )
    assert broken["valid"] is False
    assert broken["schema_errors"]
    assert broken["profile"] is None

    profile = await base_profile(app)
    document = lower_pressure(profile, 8)
    document["temperature"] = 140
    clamped = data(
        await client.post(
            "/api/profile-drafts/preview",
            json={"base_version_id": version_id, "profile": document},
        )
    )
    assert clamped["valid"] is True
    assert clamped["clamp_changes"][0]["after"] == 100
    assert clamped["profile"]["label"] == "9 Bar Espresso [AI]"


# ── the queue ────────────────────────────────────────────────────────


async def test_the_queue_lists_what_is_still_waiting_for_a_person(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """`failed` counts as open: somebody still has to decide about it."""
    app, client = writes_on
    discarded = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{discarded['id']}/discard", json={})
    open_draft = await a_draft(app, client, provider)

    queue = data(await client.get("/api/profile-drafts?open=true"))["items"]

    assert [row["id"] for row in queue] == [open_draft["id"]]
    everything = data(await client.get("/api/profile-drafts"))["items"]
    assert {row["status"] for row in everything} == {"draft", "discarded"}


async def test_the_detail_route_carries_both_documents_for_the_diff(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    app, client = live
    draft = await a_draft(app, client, provider, edit={"description": "Softer."})
    detail = data(await client.get(f"/api/profile-drafts/{draft['id']}"))
    assert detail["base_profile"]["label"] == "9 Bar Espresso"
    assert detail["draft_profile"]["label"] == "9 Bar Espresso [AI]"
    assert detail["draft_profile"]["description"] == "Softer."


# ── discarding ───────────────────────────────────────────────────────


async def test_a_pushed_draft_cannot_be_discarded_until_its_profile_is_off_the_machine(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The row would then say `discarded` while the file was still on the display."""
    app, client, _ = adopted
    draft = await a_draft(app, client, provider)
    await put(client, draft)
    await pull(app)

    response = await client.post(f"/api/profile-drafts/{draft['id']}/discard", json={})

    assert response.status_code == 409
    message = error(response)["message"]
    assert "on the machine" in message and "Delete it from the board" in message
    # Delete the profile from the board and the sync that takes its file off discards the draft.
    row = row_for(await get_board(client), APP_LABEL)["row"]
    await tombstone(client, row["id"])
    await pull(app)
    ended = data(await client.get(f"/api/profile-drafts/{draft['id']}"))["draft"]
    assert ended["status"] == "discarded" and ended["pushed_device_profile_id"] is None


# ── a base that moved underneath the draft ───────────────────────────


async def edit_on_the_machine(app: FastAPI, fake_device: FakeDevice, temperature: float) -> None:
    """Somebody changes the base profile on the display, and the mirror catches up."""
    for profile in fake_device.profiles:
        if profile.get("label") == BASE_LABEL:
            profile["temperature"] = temperature
    await mirror_only(app)


async def test_a_fresh_draft_is_not_stale(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    app, client = live
    draft = await a_draft(app, client, provider)
    assert draft["base_is_current"] is True
    assert draft["base_device_profile_id"] == "9bar"


async def test_a_draft_of_an_imported_profile_is_never_stale(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """A base that was never on the machine has nothing to have drifted from.

    True rather than False on purpose: the alternative would make every draft of
    a draft, and every draft of an imported profile, demand an override for a
    change that cannot have happened.
    """
    app, client = live
    first = await a_draft(app, client, provider)
    second = data(
        await client.post(
            "/api/profile-drafts",
            json={"base_version_id": first["draft_version_id"], "notes": "again"},
        )
    )
    assert second["base_device_profile_id"] is None
    assert second["base_is_current"] is True


async def test_a_draft_goes_stale_when_its_base_changes_on_the_machine_and_can_still_be_put(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The diff that was read is against a version the display dropped, and the card says so.

    The board does not refuse it (the next sync puts this version beside what was changed on
    the display, and the person's own profile is never touched): the warning is the card's.
    """
    app, client, fake_device = adopted
    draft = await a_draft(app, client, provider)
    assert draft["base_is_current"] is True

    await edit_on_the_machine(app, fake_device, 91)

    detail = data(await client.get(f"/api/profile-drafts/{draft['id']}"))["draft"]
    assert detail["base_is_current"] is False
    assert (await put(client, draft))["pending_draft_id"] == draft["id"]


async def test_a_base_gone_from_the_machine_is_not_stale(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """A reset display holds nothing the base could have drifted from."""
    app, client = live
    draft = await a_draft(app, client, provider)

    fake_device.profiles[:] = [p for p in fake_device.profiles if p.get("label") != BASE_LABEL]
    await mirror_only(app)

    detail = data(await client.get(f"/api/profile-drafts/{draft['id']}"))["draft"]
    assert detail["base_is_current"] is True


# ── the staged box is gone ───────────────────────────────────────────


@pytest.mark.parametrize("action", ["approve", "push", "rollback"])
async def test_the_staged_routes_are_gone_and_a_draft_cannot_reach_the_machine_by_them(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
    provider: FakeProvider,
    action: str,
) -> None:
    """A draft reaches the machine only by being put on the board and synced.

    ``approve`` is gone because putting a draft on the board approves it; ``push`` and
    ``rollback`` because the board's write phase replaced them (going back a version is the
    board's rollback). A request to any of them, with the switch on and the machine
    connected, is a 404/405 that sends nothing.
    """
    app, client, fake_device = adopted
    draft = await a_draft(app, client, provider)
    fake_device.ws_requests.clear()

    response = await client.post(f"/api/profile-drafts/{draft['id']}/{action}", json={})

    assert response.status_code in (404, 405), response.text
    assert [t for t in fake_device.ws_requests if t.startswith("req:profiles:s")] == []
    assert (
        data(await client.get(f"/api/profile-drafts/{draft['id']}"))["draft"]["status"] == "draft"
    )
