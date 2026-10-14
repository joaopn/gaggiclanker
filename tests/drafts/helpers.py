"""Shared helpers for the draft and board tests: drafts made from the mirror, Sets to put them on.

What used to live in the staged push's replace tests (``test_replace``), which went with the
staged box; the helpers are what the board's tests still build their scenarios from.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.device_writes import DeviceWriteRow, DeviceWritesRepository
from gaggiclanker.db.repos.profile_board import BoardRowPatch
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, base_profile, base_version_id, data
from tests.llm.conftest import FakeProvider

APP_LABEL = f"{BASE_LABEL} [AI]"


def forked(label: str) -> str:
    """A name of its own for a fork of ``label``.

    The app adds nothing to a name; a test that wants a second profile beside the one it forks
    gives it a different name, and this is the one the suite uses.
    """
    return label if label.endswith(" [AI]") else f"{label} [AI]"


async def tombstone(client: httpx.AsyncClient, row_id: int) -> httpx.Response:
    """Mark a board row deleted, the state the sync's removal rules are written for.

    Nothing in the app does this any more (a profile is switched off, never deleted), but a row
    deleted by an earlier version can still be in a database until the list is built, and the
    plan keeps its rules for such a row. The repository is reached through the app the client
    is wired to, so the scenarios that pin those rules keep their setup.
    """
    app = client._transport.app  # type: ignore[attr-defined]
    row = await app.state.board.board.update(
        row_id,
        BoardRowPatch(deleted_at=utc_now(), pending_draft_id=None),
    )
    assert row is not None
    return httpx.Response(200)


Live = tuple[FastAPI, httpx.AsyncClient]


async def draft_of(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider, label: str, bar: float
) -> dict[str, Any]:
    """A draft of the mirrored profile ``label`` with its first pump set to ``bar``.

    A fork under another name (``forked(label)``); when that fork is already a profile on the
    board, a name cannot be taken twice, so it is a change to that profile, which keeps its name
    (the earlier behaviour: a second fork of one name was a version of the first).
    """
    rows = data(await client.get("/api/profile-board"))["rows"]
    if any(r["row"]["label"] == forked(label) for r in rows):
        return await same_name_draft(app, client, provider, forked(label), bar)
    profile = await base_profile(app, label)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    # The agent writing a fork: a profile of its own under a name of its own. (A change that keeps
    # the profile's name is a version of it; ``same_name_draft`` below makes that one.)
    document["label"] = forked(label)
    provider.script = [json.dumps({"profile": document, "change_summary": f"{bar} bar."})]
    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": await base_version_id(app, label), "notes": "change it"},
    )
    return dict(data(response))


async def same_name_draft(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider, label: str, bar: float
) -> dict[str, Any]:
    """The agent changing a profile and keeping its name: a new version of that profile.

    Based on the profile's own active version (what the agent is shown), not on whichever stored
    version carries the label, which may be another draft's.
    """
    board = data(await client.get("/api/profile-board"))["rows"]
    row = next(r["row"] for r in board if r["row"]["label"] == label)
    version = data(await client.get(f"/api/profile-versions/{row['current_version_id']}"))
    document = dict(version["profile"])
    document["phases"] = [dict(p) for p in document["phases"]]
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    provider.script = [json.dumps({"profile": document, "change_summary": f"{bar} bar."})]
    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": row["current_version_id"], "notes": "change it"},
    )
    return dict(data(response))


async def make_set(client: httpx.AsyncClient, name: str = "Replace test") -> int:
    """A Set with one version, so a push has somewhere to record itself."""
    bean = data(await client.post("/api/beans", json={"name": name, "roaster": "nobody"}))
    stored = data(
        await client.post(
            "/api/sets",
            json={
                "name": name,
                "bean_id": bean["id"],
                "version": {"dose_g": 18.0, "target_yield_g": 36.0},
            },
        )
    )
    return int(stored["id"])


async def manual_draft(
    app: FastAPI,
    client: httpx.AsyncClient,
    base_label: str,
    label: str,
    bar: float,
    *,
    same_document: bool = False,
) -> dict[str, Any]:
    """A draft that carries its whole document, as a fork or a stage-as-is does."""
    profile = await base_profile(app, base_label)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["label"] = label
    if not same_document:
        document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    response = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": await base_version_id(app, base_label),
            "profile": document,
            "change_summary": "manual",
        },
    )
    assert response.status_code == 201, response.text
    return dict(data(response))


async def set_version_ids(app: FastAPI, set_id: int) -> list[int]:
    """The ids of a Set's versions, oldest first."""
    rows = await app.state.db.fetch_all(
        "SELECT id FROM set_versions WHERE set_id = ? ORDER BY created_at, id", (set_id,)
    )
    return [int(row["id"]) for row in rows]


def document_of(fake: FakeDevice, device_id: str) -> dict[str, Any]:
    """A profile on the machine as a document a draft can carry."""
    found = next(p for p in fake.profiles if p["id"] == device_id)
    return {k: v for k, v in found.items() if k not in ("id", "favorite", "selected")}


async def manual_from(
    client: httpx.AsyncClient, base_version_id: int, document: dict[str, Any], bar: float
) -> dict[str, Any]:
    """A draft of an explicit document, made from a given stored version."""
    edited = copy.deepcopy(document)
    edited["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": base_version_id, "profile": edited, "change_summary": f"{bar}"},
    )
    assert response.status_code == 201, response.text
    return dict(data(response))


async def make_set_on(client: httpx.AsyncClient, name: str, profile_version_id: int) -> int:
    """A Set whose first version names a stored profile, as one made from the library does."""
    bean = data(await client.post("/api/beans", json={"name": name, "roaster": "nobody"}))
    response = await client.post(
        "/api/sets",
        json={
            "name": name,
            "bean_id": bean["id"],
            "version": {
                "dose_g": 18.0,
                "target_yield_g": 36.0,
                "profile_version_id": profile_version_id,
            },
        },
    )
    assert response.status_code < 300, response.text
    return int(data(response)["id"])


async def make_set_on_a_free_profile(app: FastAPI, client: httpx.AsyncClient, name: str) -> int:
    """A Set on the first live profile that has no Set (a profile has at most one) and is no
    pin's copy."""
    row = await app.state.db.fetch_one(
        "SELECT b.current_version_id AS version FROM profile_board b "
        "WHERE b.deleted_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM sets s WHERE s.board_id = b.id) "
        "AND NOT EXISTS (SELECT 1 FROM set_version_pins p WHERE p.board_id = b.id) "
        "ORDER BY b.id"
    )
    assert row is not None, "every live profile has a Set"
    return await make_set_on(client, name, int(row["version"]))


async def grind_change(client: httpx.AsyncClient, set_id: int) -> None:
    """A version that changes only the grind: it pushes nothing and names no device id."""
    response = await client.post(f"/api/sets/{set_id}/versions", json={"grind_value": 21.0})
    assert response.status_code < 300, response.text


def on_machine(fake: FakeDevice) -> list[str]:
    return [str(p["id"]) for p in fake.profiles]


def ids_labelled(fake: FakeDevice, label: str) -> list[str]:
    return [str(p["id"]) for p in fake.profiles if p.get("label") == label]


async def audit(app: FastAPI) -> list[DeviceWriteRow]:
    """Every device write, oldest first."""
    rows = await DeviceWritesRepository(app.state.db).list_writes(limit=500)
    return list(reversed(rows))


def kinds(rows: list[DeviceWriteRow], *wanted: str) -> list[tuple[str, str | None, str]]:
    return [(r.kind, r.device_id, r.result) for r in rows if r.kind in wanted]


async def assert_versions_owned(db: Any) -> None:
    """The rules of version ownership, as the database stands: what every writer must leave true.

    * a version a profile lists is that profile's;
    * a profile's active version is one it lists;
    * a profile's own columns name only versions it owns;
    * no profile holds one brew twice, and no two versions on no profile share a brew and a label;
    * every stored hash is the brew hash of the stored document (a version is what it brews);
    * a profile records the name of its file's document exactly when it records the file's version;
    * a file the mirror holds holds a version of the profile standing on it.
    """
    from gaggiclanker.domain.models import Profile, brew_hash

    unowned = await db.fetch_all(
        "SELECT pbv.board_id, pbv.version_id, pv.board_id AS owner FROM profile_board_versions pbv "
        "JOIN profile_versions pv ON pv.id = pbv.version_id WHERE pv.board_id IS NOT pbv.board_id"
    )
    assert [dict(r) for r in unowned] == [], "a listed version is not its lister's"
    for column in (
        "current_version_id",
        "device_version_id",
        "failed_version_id",
        "previous_version_id",
        "back_from_version_id",
    ):
        stray = await db.fetch_all(
            f"SELECT pb.id, pb.{column} AS version, pv.board_id AS owner FROM profile_board pb "  # noqa: S608
            f"JOIN profile_versions pv ON pv.id = pb.{column} WHERE pv.board_id IS NOT pb.id"
        )
        assert [dict(r) for r in stray] == [], f"{column} names another profile's version"
    unlisted = await db.fetch_all(
        "SELECT pb.id FROM profile_board pb WHERE NOT EXISTS (SELECT 1 FROM profile_board_versions "
        "pbv WHERE pbv.board_id = pb.id AND pbv.version_id = pb.current_version_id)"
    )
    assert [dict(r) for r in unlisted] == [], "a profile's active version is not one of its own"
    twice = await db.fetch_all(
        "SELECT board_id, content_hash, COUNT(*) AS n FROM profile_versions "
        "GROUP BY board_id, content_hash, CASE WHEN board_id IS NULL THEN label END HAVING n > 1"
    )
    assert [dict(r) for r in twice] == [], "a brew is held twice by one profile"
    for stored in await db.fetch_all("SELECT id, json, content_hash FROM profile_versions"):
        live = brew_hash(Profile.model_validate(json.loads(stored["json"])))
        assert stored["content_hash"] == live, f"version {stored['id']} is not under its brew hash"
    unnamed = await db.fetch_all(
        "SELECT id FROM profile_board WHERE (device_version_id IS NULL) != (device_label IS NULL)"
    )
    assert [dict(r) for r in unnamed] == [], "a recorded file has no name, or a name with no file"
    wrong_file = await db.fetch_all(
        "SELECT d.device_id, pv.board_id AS owner, b.id AS standing "
        "FROM device_profiles d JOIN profile_versions pv ON pv.id = d.current_version_id "
        "JOIN profile_board b ON b.device_profile_id = d.device_id "
        "AND b.id = (SELECT b2.id FROM profile_board b2 WHERE b2.device_profile_id = d.device_id "
        " ORDER BY (b2.deleted_at IS NOT NULL), b2.id DESC LIMIT 1) "
        "WHERE d.deleted_at IS NULL AND pv.board_id IS NOT b.id"
    )
    assert [dict(r) for r in wrong_file] == [], (
        f"a file holds a version of a profile not on it {[dict(r) for r in wrong_file]}"
    )
    await assert_sets_follow_their_profiles(db)


async def assert_sets_follow_their_profiles(db: Any) -> None:
    """What a Set and its profile owe each other, as the database stands.

    * every Set version names a version its Set's profile owns (a Set with no profile names
      only versions nobody owns, or none);
    * no two Sets share a profile;
    * a Set that is not being designed has, as its current version, the version its profile has
      active: the Set follows its profile (a revert makes the profile follow the Set).
    """
    foreign = await db.fetch_all(
        "SELECT v.id, v.set_id, pv.board_id AS owner, s.board_id AS mine FROM set_versions v "
        "JOIN sets s ON s.id = v.set_id JOIN profile_versions pv ON pv.id = v.profile_version_id "
        "WHERE pv.board_id IS NOT s.board_id"
    )
    assert [dict(r) for r in foreign] == [], "a Set version brews another profile's version"
    shared = await db.fetch_all(
        "SELECT board_id, COUNT(*) AS n FROM sets WHERE board_id IS NOT NULL "
        "GROUP BY board_id HAVING n > 1"
    )
    assert [dict(r) for r in shared] == [], "a profile has two Sets"
    behind = await db.fetch_all(
        "SELECT s.id AS set_id, cur.profile_version_id AS brews, b.current_version_id AS active "
        "FROM sets s JOIN profile_board b ON b.id = s.board_id "
        "JOIN set_versions cur ON cur.id = s.current_version_id "
        "WHERE s.designing = 0 AND cur.profile_version_id IS NOT b.current_version_id"
    )
    assert [dict(r) for r in behind] == [], "a Set does not brew its profile's active version"
