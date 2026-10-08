"""A draft that is a new profile is served and landed as one, not as an edit of its base."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.lineage import taken_name_sentence
from gaggiclanker.db.repos.profiles import (
    SYNTHETIC_BASE_LABEL,
    ProfilesRepository,
)
from gaggiclanker.db.schema import create_schema
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from gaggiclanker.infra.errors import Conflict
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import manual_draft
from tests.drafts.test_board import adopted, get_board, put
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module


async def _detail(client: httpx.AsyncClient, draft_id: int) -> dict[str, Any]:
    return dict(data(await client.get(f"/api/profile-drafts/{draft_id}")))


async def _draft_of_row(
    app: FastAPI, client: httpx.AsyncClient, row: dict[str, Any], *, new: bool
) -> dict[str, Any]:
    """A draft of the board row's own version, keeping its label; ``new`` marks it a new profile."""
    version = data(await client.get(f"/api/profile-versions/{row['current_version_id']}"))
    document = copy.deepcopy(version["profile"])
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": 7, "flow": 0}
    draft = await app.state.draft_proposals.create_manual(
        base_version_id=row["current_version_id"], document=document, is_new=new
    )
    return {"id": draft.id}


async def test_the_detail_of_a_new_draft_says_so_and_serves_no_base(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    base = await ProfilesRepository(app.state.db).empty_base()
    document = {
        "label": "From zero",
        "type": "pro",
        "temperature": 92,
        "phases": [
            {
                "name": "Extraction",
                "phase": "brew",
                "valve": 1,
                "duration": 30,
                "pump": {"target": "pressure", "pressure": 9, "flow": 0},
                "targets": [{"type": "volumetric", "operator": "gte", "value": 36}],
            }
        ],
    }
    new = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=document, is_new=True
    )

    detail = await _detail(client, new.id)

    assert detail["is_new"] is True and detail["draft"]["is_new"] is True
    assert detail["base_profile"] is None
    assert detail["draft"]["base_label"] is None
    assert detail["draft"]["base_is_current"] is True
    assert detail["draft_profile"]["label"] == "From zero"
    assert SYNTHETIC_BASE_LABEL not in json.dumps(detail)


async def test_the_detail_of_an_edit_still_serves_its_diff(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    edit = await manual_draft(app, client, "9 Bar Espresso", "Tweaked", 7)

    detail = await _detail(client, edit["id"])

    assert detail["is_new"] is False and detail["draft"]["is_new"] is False
    assert detail["base_profile"]["label"] == "9 Bar Espresso"
    assert detail["draft"]["base_label"] == "9 Bar Espresso"


async def test_a_new_draft_named_like_an_existing_profile_is_refused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """A profile written from scratch never continues a profile that exists, and the app adds
    nothing to its name to keep them apart: a name that is already a profile is refused, in the
    one sentence every path uses, and nothing is stored."""
    app, client, _ = adopted
    before = len((await get_board(client))["rows"])
    base = await ProfilesRepository(app.state.db).empty_base()
    version = await ProfilesRepository(app.state.db).get_version(base)
    assert version is not None and version.profile is not None
    document = copy.deepcopy(version.profile)
    document["label"] = BASE_LABEL
    drafts_before = len(data(await client.get("/api/profile-drafts"))["items"])

    with pytest.raises(Conflict) as refused:
        await app.state.draft_proposals.create_manual(
            base_version_id=base, document=document, is_new=True
        )

    assert str(refused.value) == taken_name_sentence(BASE_LABEL)
    assert len(data(await client.get("/api/profile-drafts"))["items"]) == drafts_before
    assert len((await get_board(client))["rows"]) == before


async def test_two_new_drafts_with_one_name_make_one_profile_and_the_second_put_is_refused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    base = await ProfilesRepository(app.state.db).empty_base()
    version = await ProfilesRepository(app.state.db).get_version(base)
    assert version is not None and version.profile is not None
    drafts = []
    for duration in (20, 25):
        document = copy.deepcopy(version.profile)
        document["label"] = "Zero"
        document["phases"][0]["duration"] = duration
        drafts.append(
            await app.state.draft_proposals.create_manual(
                base_version_id=base, document=document, is_new=True
            )
        )

    first = await put(client, {"id": drafts[0].id})
    refused = await client.post("/api/profile-board", json={"draft_id": drafts[1].id})

    assert first["label"] == "Zero"
    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence("Zero")
    versions = data(await client.get(f"/api/profile-board/{first['id']}/versions"))["versions"]
    assert len(versions) == 1


async def test_a_refinement_of_a_new_draft_that_is_now_a_profile_is_refused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """Once a new draft is put, its name is a live profile. A refinement is still a new profile's
    draft (it continues nothing), so the same refusal applies, and it says what to do: draft a
    change from the profile. (After the sync the draft is pushed and cannot be refined at all.)"""
    app, client, _ = adopted
    base = await ProfilesRepository(app.state.db).empty_base()
    version = await ProfilesRepository(app.state.db).get_version(base)
    assert version is not None and version.profile is not None
    document = copy.deepcopy(version.profile)
    document["label"] = "Zero"
    new = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=document, is_new=True
    )
    made = await put(client, {"id": new.id})
    document["phases"][0]["duration"] = 25
    provider.script = [json.dumps({"profile": document, "change_summary": "shorter"})]

    refused = await client.post(f"/api/profile-drafts/{new.id}/refine", json={"notes": "shorter"})

    assert refused.status_code == 409
    assert error(refused)["message"] == taken_name_sentence("Zero")
    board = await get_board(client)
    assert [p for p in board["proposals"] if p["row_id"] == made["id"]] == []


async def test_a_refinement_of_a_new_draft_is_new(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    base = await ProfilesRepository(app.state.db).empty_base()
    version = await ProfilesRepository(app.state.db).get_version(base)
    assert version is not None and version.profile is not None
    document = copy.deepcopy(version.profile)
    document["label"] = "Zero"
    new = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=document, is_new=True
    )
    document["phases"][0]["duration"] = 25
    provider.script = [json.dumps({"profile": document, "change_summary": "shorter"})]

    refined = data(
        await client.post(f"/api/profile-drafts/{new.id}/refine", json={"notes": "shorter"})
    )

    assert (await _detail(client, refined["id"]))["is_new"] is True


async def test_the_versions_list_does_not_hold_the_synthetic_base(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    await ProfilesRepository(app.state.db).empty_base()

    page = data(await client.get("/api/profile-versions?limit=200"))

    assert page["items"]
    assert SYNTHETIC_BASE_LABEL not in [item["label"] for item in page["items"]]
    assert page["total"] == len(page["items"])


# ── stop conditions: a new profile changes none ──────────────────────


def _document(volume: float) -> dict[str, Any]:
    return {
        "label": "Stops",
        "type": "pro",
        "temperature": 92,
        "phases": [
            {
                "name": "Extraction",
                "phase": "brew",
                "valve": 1,
                "duration": 30,
                "pump": {"target": "pressure", "pressure": 9, "flow": 0},
                "targets": [{"type": "volumetric", "operator": "gte", "value": volume}],
            }
        ],
    }


async def test_a_new_draft_has_no_stop_condition_changes_and_an_edit_still_does(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    base = await ProfilesRepository(app.state.db).empty_base()  # stops at 36
    new = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=_document(44), is_new=True
    )
    edit = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=_document(44)
    )

    assert new.stop_condition_changes == []
    assert len(edit.stop_condition_changes or []) == 1
    detail = await _detail(client, new.id)
    assert detail["draft"]["stop_condition_changes"] == []


async def test_a_new_draft_and_an_edit_that_moves_a_stop_are_put_without_any_flag(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    base = await ProfilesRepository(app.state.db).empty_base()
    new = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=_document(44), is_new=True
    )
    edit = await app.state.draft_proposals.create_manual(
        base_version_id=base, document={**_document(45), "label": "Other"}
    )

    row = await put(client, {"id": new.id})
    # Renamed from a base whose stop it moves: it lands as a new profile, and the person has no
    # checkbox for it, so nothing may be asked.
    renamed = await client.post("/api/profile-board", json={"draft_id": edit.id})

    assert row["pending_draft_id"] == new.id
    assert renamed.status_code == 201
    assert renamed.json()["data"]["pending_draft_id"] == edit.id


# ── refining a new draft ─────────────────────────────────────────────


async def test_refining_a_new_draft_does_not_show_the_stand_in_base_as_the_machine_profile(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    base = await ProfilesRepository(app.state.db).empty_base()
    new = await app.state.draft_proposals.create_manual(
        base_version_id=base, document={**_document(44), "label": "Zero"}, is_new=True
    )
    provider.script = [json.dumps({"profile": _document(44), "change_summary": "same"})]

    refined = data(
        await client.post(f"/api/profile-drafts/{new.id}/refine", json={"notes": "a bit longer"})
    )

    user = "\n".join(m.content for m in provider.calls[-1].messages)
    assert SYNTHETIC_BASE_LABEL not in user and "An empty baseline" not in user
    assert "this is a new profile that is not on the machine" in user
    assert "Zero" in user  # the draft being refined is the document to work from
    assert refined["is_new"] is True and refined["stop_condition_changes"] == []


async def test_refining_an_edit_still_shows_the_profile_it_edits(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, _ = adopted
    edit = await manual_draft(app, client, "9 Bar Espresso", "Tweaked", 7)
    provider.script = [json.dumps({"profile": _document(44), "change_summary": "same"})]

    await client.post(f"/api/profile-drafts/{edit['id']}/refine", json={"notes": "more"})

    user = "\n".join(m.content for m in provider.calls[-1].messages)
    assert "9 Bar Espresso" in user and "not on the machine" not in user


# ── the warning is not raised for a new draft on a stale stand-in base ──


async def test_a_new_draft_whose_stored_base_moved_on_the_machine_does_not_warn(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    base_id = int(
        await app.state.db.fetch_value(
            "SELECT d.current_version_id FROM device_profiles d "
            "JOIN profile_versions v ON v.id = d.current_version_id "
            "WHERE v.label = '9 Bar Espresso'"
        )
    )
    new = await app.state.draft_proposals.create_manual(
        base_version_id=base_id, document=_document(44), is_new=True
    )
    edit = await app.state.draft_proposals.create_manual(
        base_version_id=base_id, document={**_document(45), "label": "Other"}
    )
    # The machine's file now holds something else than the base the drafts were made against.
    await app.state.db.execute(
        "UPDATE device_profiles SET current_version_id = ("
        "SELECT MAX(id) FROM profile_versions WHERE id != ?) WHERE current_version_id = ?",
        (base_id, base_id),
    )

    assert (await _detail(client, edit.id))["draft"]["base_is_current"] is False
    assert (await _detail(client, new.id))["draft"]["base_is_current"] is True


# ── the synthetic base is nobody's profile ───────────────────────────


@pytest.fixture
async def plain_db(tmp_path: Path) -> Any:
    db = Database(tmp_path / "m.db")
    await db.connect()
    await create_schema(db)
    try:
        yield db
    finally:
        await db.close()


async def test_the_synthetic_base_is_not_a_candidate_a_default_base_or_a_row_of_v_profiles(
    plain_db: Database,
) -> None:
    from gaggiclanker.starting.context import profile_candidates

    profiles = ProfilesRepository(plain_db)
    real, _ = await profiles.ensure_version(Profile.model_validate(_document(40)))
    # Stored after the real one, so it is the newest: the tie-break would pick it.
    synthetic = await profiles.empty_base()

    assert [c.label for c in await profile_candidates(plain_db)] == [real.label]
    assert await profiles.default_draft_base() == real.id
    labels = [r["label"] for r in await plain_db.fetch_all("SELECT label FROM v_profiles")]
    assert labels == [real.label]
    # With nothing else in the library the stand-in is still what a draft is stored against.
    await plain_db.execute("DELETE FROM profile_versions WHERE id = ?", (real.id,))
    assert await profiles.default_draft_base() == synthetic
