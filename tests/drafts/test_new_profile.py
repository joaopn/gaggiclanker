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
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations
from gaggiclanker.db.repos.profiles import (
    SYNTHETIC_BASE_DESCRIPTION,
    SYNTHETIC_BASE_LABEL,
    ProfilesRepository,
)
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from tests.drafts.conftest import data
from tests.drafts.helpers import APP_LABEL, manual_draft
from tests.drafts.test_board import adopted, app_row, approve, get_board, put
from tests.drafts.test_board_landings import landing
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
    assert detail["draft_profile"]["label"] == "From zero [AI]"
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


async def test_a_new_draft_never_continues_the_board_row_of_its_stored_base(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The labels agree here, which is all the lineage looked at before the flag."""
    app, client, fake = adopted
    row = await app_row(app, client, fake, provider, 8)
    edit = await _draft_of_row(app, client, row, new=False)
    new = await _draft_of_row(app, client, row, new=True)
    await approve(app, edit)
    await approve(app, new)

    board = await get_board(client)

    assert landing(board, edit)["plain"]["row_id"] == row["id"]
    found = landing(board, new)["plain"]
    assert found["row_id"] is None and found["revives_label"] is None
    assert found["taken_label"] == APP_LABEL


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


# ── the migration ────────────────────────────────────────────────────


async def _migrate(db: Database, tmp_path: Path, *, below: str | None) -> None:
    import shutil

    directory = tmp_path / f"upto-{below}"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if below is None or path.name < below:
            shutil.copy(path, directory / path.name)
    await run_migrations(db, directory)


@pytest.fixture
async def plain_db(tmp_path: Path) -> Any:
    db = Database(tmp_path / "m.db")
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


async def _version(db: Database, label: str, description: str, hash_: str) -> int:
    document = json.dumps({"label": label, "description": description})
    cursor = await db.execute(
        "INSERT INTO profile_versions (content_hash, label, type, json, source)"
        " VALUES (?, ?, 'pro', ?, 'draft')",
        (hash_, label, document),
    )
    return int(cursor.lastrowid or 0)


WIZARD = "Proposed by the starting-point wizard"


async def _draft(db: Database, base: int, drafted: int, notes: str = "") -> int:
    cursor = await db.execute(
        "INSERT INTO profile_drafts (base_version_id, draft_version_id, notes, status, created_at,"
        " updated_at, stop_condition_changes_json)"
        " VALUES (?, ?, ?, 'draft', 'x', 'x', '[{\"n\": 1}]')",
        (base, drafted, notes),
    )
    return int(cursor.lastrowid or 0)


async def test_existing_drafts_on_the_synthetic_base_become_new_and_no_others(
    plain_db: Database, tmp_path: Path
) -> None:
    await _migrate(plain_db, tmp_path, below="0035")
    synthetic = await _version(plain_db, SYNTHETIC_BASE_LABEL, SYNTHETIC_BASE_DESCRIPTION, "h1")
    # A person's profile that happens to carry the same label is not the synthetic base.
    lookalike = await _version(plain_db, SYNTHETIC_BASE_LABEL, "Mine", "h2")
    other = await _version(plain_db, "Other", "", "h3")
    on_synthetic = await _draft(plain_db, synthetic, other)
    on_lookalike = await _draft(plain_db, lookalike, other)
    on_real = await _draft(plain_db, other, lookalike)
    wizard = await _draft(plain_db, other, lookalike, notes=f"{WIZARD} (recommended).")
    redraft = await _draft(
        plain_db,
        other,
        lookalike,
        notes=f"{WIZARD} (recommended): the draft brews at 94 °C, and this profile is where "
        "that lives.",
    )

    refined = await _draft(
        plain_db, other, lookalike, notes=f"{WIZARD} (recommended).\nA bit longer, please."
    )
    # Only the exact first line counts: a person's own words that merely start alike do not.
    lookalike_notes = await _draft(
        plain_db, other, lookalike, notes=f"{WIZARD} (recommended). Mine\nmore"
    )

    await _migrate(plain_db, tmp_path, below=None)

    rows = await plain_db.fetch_all(
        "SELECT id, is_new, stop_condition_changes_json AS stops FROM profile_drafts"
    )
    assert {row["id"]: row["is_new"] for row in rows} == {
        on_synthetic: 1,
        on_lookalike: 0,
        on_real: 0,
        wizard: 1,
        redraft: 0,
        refined: 1,
        lookalike_notes: 0,
    }
    # The stored list was computed against the stand-in base: a new draft has none, an edit
    # keeps it.
    assert {row["id"]: row["stops"] for row in rows} == {
        id_: "[]" if new else '[{"n": 1}]'
        for id_, new in (
            (on_synthetic, True),
            (on_lookalike, False),
            (on_real, False),
            (wizard, True),
            (redraft, False),
            (refined, True),
            (lookalike_notes, False),
        )
    }


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


async def test_a_new_draft_is_put_without_an_acknowledgement_and_an_edit_needs_one(
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
    refused = await client.post("/api/profile-board", json={"draft_id": edit.id})

    assert row["pending_draft_id"] == new.id
    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["field"] == "acknowledge_stop_changes"


async def _apply_the_backfill(db: Database) -> None:
    """Run 0035's own UPDATE statements on a database that already holds the new column."""
    sql = "\n".join(
        line
        for line in (MIGRATIONS_DIR / "0035_draft_is_new.sql").read_text().splitlines()
        if not line.lstrip().startswith("--")
    )
    for statement in sql.split(";"):
        if statement.strip().startswith("UPDATE"):
            await db.execute(statement)


async def test_a_draft_made_before_the_upgrade_is_put_without_the_stale_acknowledgement(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """Its stored stop list was a diff against the stand-in base, so the old code demanded one."""
    app, client, _ = adopted
    base = await ProfilesRepository(app.state.db).empty_base()
    wizard_notes = f"{WIZARD} (recommended)."
    old = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=_document(44), notes=wizard_notes
    )
    real_base = int(
        await app.state.db.fetch_value(
            "SELECT d.current_version_id FROM device_profiles d "
            "JOIN profile_versions v ON v.id = d.current_version_id "
            "WHERE v.label = '9 Bar Espresso'"
        )
    )
    other = await app.state.draft_proposals.create_manual(
        base_version_id=real_base, document={**_document(45), "label": "Other"}
    )
    assert len(old.stop_condition_changes or []) == 1 and not old.is_new

    await _apply_the_backfill(app.state.db)

    migrated = (await _detail(client, old.id))["draft"]
    assert migrated["is_new"] is True and migrated["stop_condition_changes"] == []
    assert (await _detail(client, other.id))["draft"]["stop_condition_changes"] != []
    row = await put(client, {"id": old.id})
    assert row["pending_draft_id"] == old.id


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
    assert "Zero [AI]" in user  # the draft being refined is the document to work from
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


async def test_the_synthetic_base_is_not_a_candidate_a_default_base_or_a_row_of_v_profiles(
    plain_db: Database, tmp_path: Path
) -> None:
    from gaggiclanker.starting.context import profile_candidates

    await _migrate(plain_db, tmp_path, below=None)
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
