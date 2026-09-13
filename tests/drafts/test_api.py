"""`/api/profile-drafts`, end to end: draft, approve, push, verify, roll back.

Through HTTP rather than through the service, because what this feature ships is
a sequence of button presses and the interesting failures are all at the seams:
an approval that forgot the acknowledgement, a push with the switch off, a
machine that stored something else. Each of those is a specific status code and
a specific message, and a caller that got the wrong one would send somebody to
the wrong place.

The model is scripted (`FakeProvider`) and the machine is the fake. No money is
spent and no network is touched, here or anywhere else in the offline suite.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from tests.drafts.conftest import BASE_LABEL, base_profile, base_version_id, data, error
from tests.llm.conftest import FakeProvider


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


async def test_a_draft_from_an_analysis_carries_its_profile_patch_into_the_prompt(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, analysis_id: int
) -> None:
    """The shot page's button. The patch is advice, not an edit to apply.

    `profile_patch` is a list of "phase 0, pressure: 9 -> 8" lines the analyzer
    recorded and never applied; this is the route that turns them into a
    profile, and the model is the thing that turns them into one.
    """
    app, client = live
    profile = await base_profile(app)
    provider.script = [
        json.dumps({"profile": lower_pressure(profile, 8), "change_summary": "8 bar"})
    ]

    draft = data(
        await client.post(
            "/api/profile-drafts",
            json={"base_version_id": await base_version_id(app), "analysis_id": analysis_id},
        )
    )

    assert draft["source_analysis_id"] == analysis_id
    sent = "\n".join(message.content for message in provider.calls[0].messages)
    assert "phase 0, pump.pressure: 9 -> 8" in sent
    assert "the puck was compacted" in sent


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


# ── approving ────────────────────────────────────────────────────────


async def test_approving_a_draft_that_moves_a_stop_condition_needs_the_acknowledgement(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """crema's rule, and the refusal carries the list it is asking about."""
    app, client = live
    profile = await base_profile(app)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["phases"][0]["targets"] = [{"type": "volumetric", "operator": "gte", "value": 44}]
    provider.script = [json.dumps({"profile": document, "change_summary": "Longer ratio."})]
    draft = data(
        await client.post(
            "/api/profile-drafts",
            json={"base_version_id": await base_version_id(app), "notes": "longer ratio"},
        )
    )
    assert len(draft["stop_condition_changes"]) == 1

    refused = await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    assert refused.status_code == 409
    body = error(refused)
    assert "how much coffee ends up in the cup" in body["message"]
    assert body["details"]["stop_condition_changes"][0]["after"]["value"] == 44

    approved = data(
        await client.post(
            f"/api/profile-drafts/{draft['id']}/approve",
            json={"acknowledge_stop_changes": True},
        )
    )
    assert approved["status"] == "approved"
    assert approved["acknowledged_stop_changes"] is True


async def test_a_draft_that_moves_nothing_is_approved_without_a_checkbox(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """An acknowledgement that is always required is one nobody reads."""
    app, client = live
    draft = await a_draft(app, client, provider)
    assert draft["stop_condition_changes"] == []
    approved = data(await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={}))
    assert approved["status"] == "approved"
    assert approved["acknowledged_stop_changes"] is False


# ── pushing ──────────────────────────────────────────────────────────


async def test_a_push_is_refused_while_device_writes_are_off(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """The default, from the outside. Nothing reaches the machine."""
    app, client = live
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    before = len(fake_device.profiles)

    response = await client.post(f"/api/profile-drafts/{draft['id']}/push", json={})

    assert response.status_code == 403
    assert "switched off" in error(response)["message"]
    assert len(fake_device.profiles) == before
    rows = await DeviceWritesRepository(app.state.db).list_writes()
    assert [(row.kind, row.result) for row in rows] == [("profile_save", "refused")]


async def test_an_unapproved_draft_cannot_be_pushed(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    app, client = writes_on
    draft = await a_draft(app, client, provider)
    response = await client.post(f"/api/profile-drafts/{draft['id']}/push", json={})
    assert response.status_code == 409
    assert "approve it first" in error(response)["message"]


async def test_a_push_saves_a_new_profile_reads_it_back_and_mirrors_it(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """The happy path, with every property that makes it safe asserted.

    A **new** profile — the base is still there, untouched. **Not selected** —
    the person is still brewing with whatever they were brewing with.
    **Verified** — the machine served back what we sent. **Mirrored** — the
    Profiles page shows it now, rather than in fifteen minutes.
    """
    app, client = writes_on
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    before = {profile["id"] for profile in fake_device.profiles}

    pushed = data(await client.post(f"/api/profile-drafts/{draft['id']}/push", json={}))["draft"]

    assert pushed["status"] == "pushed"
    device_id = pushed["pushed_device_profile_id"]
    assert device_id not in before
    assert "9bar" in {profile["id"] for profile in fake_device.profiles}
    assert fake_device.selected_profile_id != device_id

    mirrored = data(await client.get("/api/profiles"))["items"]
    assert any(row["device_id"] == device_id for row in mirrored)
    assert any(row["label"] == "9 Bar Espresso [AI]" for row in mirrored)


async def test_a_machine_that_stores_something_else_fails_with_both_documents(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """Layer 3, doing the only thing it can do that layers 1 and 2 cannot.

    The fake is told to drop one phase on the way in. The save is acknowledged,
    the profile exists on the machine, and it is not the profile anybody
    approved. Only reading it back can tell.
    """
    app, client = writes_on
    fake_device.mutate_on_save = lambda stored: {**stored, "temperature": 91}
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})

    pushed = data(await client.post(f"/api/profile-drafts/{draft['id']}/push", json={}))["draft"]

    assert pushed["status"] == "failed"
    assert "stored something other than what was sent" in pushed["error"]
    assert pushed["verification"]["sent"]["temperature"] == 93
    assert pushed["verification"]["loaded"]["temperature"] == 91
    # Still on the machine, which is precisely the problem the rollback solves.
    assert any(
        profile["id"] == pushed["pushed_device_profile_id"] for profile in fake_device.profiles
    )


async def test_rollback_deletes_the_machine_s_copy_and_the_draft_stops_naming_it(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """One click, and the draft keeps saying `failed` — what happened, happened."""
    app, client = writes_on
    fake_device.mutate_on_save = lambda stored: {**stored, "temperature": 91}
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    pushed = data(await client.post(f"/api/profile-drafts/{draft['id']}/push", json={}))["draft"]
    device_id = pushed["pushed_device_profile_id"]

    rolled = data(await client.post(f"/api/profile-drafts/{draft['id']}/rollback", json={}))

    assert rolled["status"] == "failed"
    assert rolled["pushed_device_profile_id"] is None
    assert all(profile["id"] != device_id for profile in fake_device.profiles)
    kinds = [
        (row.kind, row.result) for row in await DeviceWritesRepository(app.state.db).list_writes()
    ]
    assert ("profile_delete", "ok") in kinds
    # And the mirror no longer lists a profile the machine does not have.
    listed = data(await client.get("/api/profiles"))["items"]
    assert all(row["device_id"] != device_id for row in listed)


async def test_a_push_can_record_the_result_as_a_new_set_version(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, a_set: int
) -> None:
    """Opt-in: a person may try a draft without saying the Set now means it.

    When they do say so, the version carries both identities — the
    content-hashed profile version and the device id the firmware assigned —
    because "what did this Set brew" and "which file on the machine is that"
    are different questions with different answers.
    """
    app, client = writes_on
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})

    body = data(
        await client.post(f"/api/profile-drafts/{draft['id']}/push", json={"set_id": a_set})
    )

    version = body["set_version"]
    assert version is not None
    assert version["profile_version_id"] == draft["draft_version_id"]
    assert version["pushed_device_profile_id"] == body["draft"]["pushed_device_profile_id"]
    assert version["origin"] == "manual"
    assert version["profile_label"] == "9 Bar Espresso [AI]"


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


async def test_a_version_staged_unchanged_reaches_the_machine_and_round_trips(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The plainest path there is: this profile, as it stands, on the machine.

    A profile that is already right should not have to be edited to get there,
    so the document posted is byte-for-byte the stored version. Everything that
    makes a push safe still applies — the suffix, a new profile rather than an
    overwrite, nothing selected — and the machine serving back what was sent is
    what `verified` means.
    """
    app, client = writes_on
    version_id = await base_version_id(app)
    profile = await base_profile(app)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})

    draft = data(
        await client.post(
            "/api/profile-drafts",
            json={
                "base_version_id": version_id,
                "profile": document,
                "change_summary": f"Staged unchanged from {BASE_LABEL}",
            },
        )
    )
    assert draft["change_summary"] == f"Staged unchanged from {BASE_LABEL}"
    # Nothing moved, so there is nothing to acknowledge and nothing to clamp.
    assert draft["stop_condition_changes"] == []
    assert draft["clamp_changes"] == []

    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    before = {entry["id"] for entry in fake_device.profiles}

    pushed = data(await client.post(f"/api/profile-drafts/{draft['id']}/push", json={}))["draft"]

    assert pushed["status"] == "pushed"
    assert pushed["pushed_device_profile_id"] not in before
    assert fake_device.selected_profile_id != pushed["pushed_device_profile_id"]
    mirrored = data(await client.get("/api/profiles"))["items"]
    assert any(row["label"] == f"{BASE_LABEL} [AI]" for row in mirrored)


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


async def test_a_pushed_draft_cannot_be_discarded(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    """The row would then say `discarded` while the file was still on the display."""
    app, client = writes_on
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    await client.post(f"/api/profile-drafts/{draft['id']}/push", json={})

    response = await client.post(f"/api/profile-drafts/{draft['id']}/discard", json={})

    assert response.status_code == 409
    assert "on the machine" in error(response)["message"]


async def test_the_detail_route_carries_both_documents_for_the_diff(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    app, client = live
    draft = await a_draft(app, client, provider, edit={"description": "Softer."})
    detail = data(await client.get(f"/api/profile-drafts/{draft['id']}"))
    assert detail["base_profile"]["label"] == "9 Bar Espresso"
    assert detail["draft_profile"]["label"] == "9 Bar Espresso [AI]"
    assert detail["draft_profile"]["description"] == "Softer."


@pytest.fixture
async def a_set(live: tuple[FastAPI, httpx.AsyncClient]) -> int:
    """A Set with one version, so a push has somewhere to record itself."""
    _app, client = live
    machine = data(await client.get("/api/machines"))["items"][0]["machine"]
    bean = data(await client.post("/api/beans", json={"name": "Draft test", "roaster": "nobody"}))
    stored = data(
        await client.post(
            "/api/sets",
            json={
                "name": "Draft baseline",
                "bean_id": bean["id"],
                "machine_id": machine["id"],
                "version": {"dose_g": 18.0, "target_yield_g": 36.0},
            },
        )
    )
    return int(stored["id"])


@pytest.fixture
async def analysis_id(live: tuple[FastAPI, httpx.AsyncClient]) -> int:
    """An `ok` analysis carrying a profile patch, written straight to the table.

    Built through the repository rather than by running the analyzer: what this
    fixture is for is the *patch*, and going through a second scripted LLM call
    to obtain one would make every test that uses it depend on the analyzer's
    prompt as well as on its own.
    """
    app, _client = live
    from gaggiclanker.db.repos.analyses import AnalysesRepository, AnalysisStart
    from gaggiclanker.db.repos.shots import ShotsRepository

    # An analysis has a foreign key to a shot, so the archive needs one. The
    # `live` fixture only mirrors profiles — a draft does not need shots — so
    # this is the one place that pays for a shot sync.
    await app.state.sync.sync_shots(trigger="test")
    shots = await ShotsRepository(app.state.db).list_shots(limit=1)
    assert shots.items, "the fake device served no shots"
    shot_id = shots.items[0].id
    repo = AnalysesRepository(app.state.db)
    row_id = await repo.start(AnalysisStart(shot_id=shot_id, provider="fake", model="fake"))
    await repo.finish(
        row_id,
        status="ok",
        output={
            "shot_style": "classic_9bar",
            "execution": {"summary": "fine", "issues": []},
            "taste_prediction": {"balance": "bitter", "body": "heavy", "confidence": "medium"},
            "diagnosis": "It ran slow and the puck was compacted.",
            "suggestions": [],
            "profile_patch": [
                {
                    "phase_index": 0,
                    "field": "pump.pressure",
                    "from": "9",
                    "to": "8",
                    "reason": "a gentler peak on a dense puck",
                }
            ],
            "questions_for_user": [],
            "rules_used": [],
        },
    )
    return row_id


# ── taking a profile back off the machine ────────────────────────────


async def test_rolling_back_a_pushed_draft_discards_it_rather_than_lying(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """A pushed draft whose profile is gone must stop saying `pushed`.

    It was also a dead end: `discard` refuses a pushed draft, so before this the
    draft could never reach a terminal state at all — rollback cleared the id and
    left the status, and every later discard answered 409 for ever.
    """
    app, client = writes_on
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    pushed = data(await client.post(f"/api/profile-drafts/{draft['id']}/push", json={}))["draft"]
    device_id = pushed["pushed_device_profile_id"]

    rolled = data(await client.post(f"/api/profile-drafts/{draft['id']}/rollback", json={}))

    assert rolled["status"] == "discarded"
    assert rolled["pushed_device_profile_id"] is None
    assert all(profile["id"] != device_id for profile in fake_device.profiles)


async def test_a_rollback_stops_every_set_version_naming_the_deleted_profile(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, a_set: int
) -> None:
    """A version pointing at a file the machine does not have resolves to whatever
    inherits that id next. What it *brewed* is history and stays."""
    app, client = writes_on
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    body = data(
        await client.post(f"/api/profile-drafts/{draft['id']}/push", json={"set_id": a_set})
    )
    version_id = body["set_version"]["id"]
    assert body["set_version"]["pushed_device_profile_id"]

    await client.post(f"/api/profile-drafts/{draft['id']}/rollback", json={})

    entries = data(await client.get(f"/api/sets/{a_set}"))["versions"]
    after = next(entry["version"] for entry in entries if entry["version"]["id"] == version_id)
    assert after["pushed_device_profile_id"] is None
    assert after["profile_version_id"] == draft["draft_version_id"]


async def test_a_draft_that_was_never_pushed_cannot_be_rolled_back(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    app, client = writes_on
    draft = await a_draft(app, client, provider)

    response = await client.post(f"/api/profile-drafts/{draft['id']}/rollback", json={})

    assert response.status_code == 409
    assert "only a pushed or failed draft" in error(response)["message"]


# ── a base that moved underneath the draft ───────────────────────────


async def edit_on_the_machine(app: FastAPI, fake_device: FakeDevice, temperature: float) -> None:
    """Somebody changes the base profile on the display, and the mirror catches up."""
    for profile in fake_device.profiles:
        if profile.get("label") == BASE_LABEL:
            profile["temperature"] = temperature
    await app.state.sync.sync_profiles(trigger="test")


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


async def test_a_push_is_refused_once_the_base_has_changed_on_the_machine(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """The diff that was approved is a diff against a version the display dropped.

    Pushing anyway silently proposes undoing whatever was changed there, and this
    box does not get to decide which of the two edits was meant.
    """
    app, client = writes_on
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})

    await edit_on_the_machine(app, fake_device, 91)

    detail = data(await client.get(f"/api/profile-drafts/{draft['id']}"))["draft"]
    assert detail["base_is_current"] is False

    response = await client.post(f"/api/profile-drafts/{draft['id']}/push", json={})
    assert response.status_code == 409
    body = error(response)
    assert "changed on the machine since" in body["message"]
    assert body["details"]["field"] == "allow_stale_base"
    assert body["details"]["base_device_profile_id"] == "9bar"


async def test_a_stale_push_goes_through_with_the_override(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """Deliberate, and one field. The refusal is a question, not a wall."""
    app, client = writes_on
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})
    await edit_on_the_machine(app, fake_device, 91)

    pushed = data(
        await client.post(
            f"/api/profile-drafts/{draft['id']}/push", json={"allow_stale_base": True}
        )
    )["draft"]

    assert pushed["status"] == "pushed"


async def test_a_base_deleted_from_the_machine_counts_as_stale(
    writes_on: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider, fake_device: FakeDevice
) -> None:
    """The tombstone is the point: the mirror keeps the row, the display does not.

    A draft whose base is no longer on the machine at all is not a diff anybody
    can still check, so it gets the same question as one that merely moved.
    """
    app, client = writes_on
    draft = await a_draft(app, client, provider)
    await client.post(f"/api/profile-drafts/{draft['id']}/approve", json={})

    fake_device.profiles[:] = [p for p in fake_device.profiles if p.get("label") != BASE_LABEL]
    await app.state.sync.sync_profiles(trigger="test")

    response = await client.post(f"/api/profile-drafts/{draft['id']}/push", json={})
    assert response.status_code == 409
