"""A major version means a different profile, and a profile is a list entry.

Moving a Set to a newer version of the same profile is dialling in (a minor);
moving it to another entry of the profile list is a functional change (a major).
A profile version that no entry holds is a profile of its own. The person's box
wins in both directions, and a pushed draft stays minor whatever it names (pinned
in `tests/drafts/test_record_on_set.py`).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.profile_identity import profile_entry_id, same_profile
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetVersionPatch, SetVersionWrite, SetWrite
from tests.sets.conftest import Fixtures, make_profile_version

PREDICTION = "Compared to v1: a rounder cup, no slower, less of the dry finish."


async def make_entry(
    db: Database, label: str, versions: list[int], *, deleted: bool = False
) -> int:
    """A profile list entry that has had these versions (the first is active)."""
    cursor = await db.execute(
        "INSERT INTO profile_board (label, current_version_id, origin, deleted_at) "
        "VALUES (?, ?, 'adopted', ?)",
        (label, versions[0], "2026-01-01T00:00:00.000Z" if deleted else None),
    )
    entry = int(cursor.lastrowid or 0)
    for version in versions:
        await db.execute(
            "INSERT INTO profile_board_versions (board_id, version_id, source) "
            "VALUES (?, ?, 'machine')",
            (entry, version),
        )
    return entry


class World:
    """Two entries of two versions each, and two versions in no entry."""

    def __init__(self) -> None:
        self.nine_old = self.nine_new = self.eight_old = self.eight_new = 0
        self.loose = self.other_loose = 0
        self.nine_entry = self.eight_entry = 0


async def world(db: Database) -> World:
    w = World()
    w.nine_old = await make_profile_version(db, "9 Bar")
    w.nine_new = await make_profile_version(db, "9 Bar", temperature=94)
    w.eight_old = await make_profile_version(db, "8 Bar")
    w.eight_new = await make_profile_version(db, "8 Bar", temperature=94)
    w.loose = await make_profile_version(db, "Loose one")
    w.other_loose = await make_profile_version(db, "Loose two")
    w.nine_entry = await make_entry(db, "9 Bar", [w.nine_new, w.nine_old])
    w.eight_entry = await make_entry(db, "8 Bar", [w.eight_new, w.eight_old])
    return w


async def _new_set(wired: Fixtures, profile: int | None) -> int:
    row = await wired.sets.create(
        SetWrite(name="Guji", bean_id=wired.bean_id),
        SetVersionWrite(profile_version_id=profile, dose_g=18),
    )
    return row.id


class TestWhatTheSameProfileIs:
    async def test_the_identity_rule(self, wired: Fixtures) -> None:
        w = await world(wired.db)

        async def same(a: int | None, b: int | None) -> bool:
            return await same_profile(wired.db, a, b)

        # Versions of one entry are one profile; another entry's are another.
        assert await same(w.nine_old, w.nine_new)
        assert not await same(w.nine_old, w.eight_old)
        # A version in no entry is its own profile: the same as itself, as nothing else.
        assert await same(w.loose, w.loose)
        assert not await same(w.loose, w.other_loose)
        assert not await same(w.loose, w.nine_old)
        # No profile is the same as no profile, and different from any profile.
        assert await same(None, None)
        assert not await same(None, w.nine_old)
        assert not await same(w.nine_old, None)
        assert await profile_entry_id(wired.db, w.loose) is None
        assert await profile_entry_id(wired.db, w.nine_old) == w.nine_entry

    async def test_a_deleted_entry_still_names_its_versions(self, wired: Fixtures) -> None:
        old = await make_profile_version(wired.db, "Gone")
        new = await make_profile_version(wired.db, "Gone", temperature=95)
        await make_entry(wired.db, "Gone", [new, old], deleted=True)
        assert await same_profile(wired.db, old, new)


class TestWhichEntryAVersionBelongsTo:
    async def test_a_live_entry_comes_before_a_deleted_one_whatever_the_ids(
        self, wired: Fixtures
    ) -> None:
        version = await make_profile_version(wired.db, "Shared")
        deleted = await make_entry(wired.db, "Old copy", [version], deleted=True)
        live = await make_entry(wired.db, "Live copy", [version])
        assert deleted < live
        assert await profile_entry_id(wired.db, version) == live

    async def test_otherwise_the_lowest_id_is_the_stable_pick(self, wired: Fixtures) -> None:
        version = await make_profile_version(wired.db, "Shared two")
        first = await make_entry(wired.db, "First", [version])
        await make_entry(wired.db, "Second", [version])
        assert await profile_entry_id(wired.db, version) == first
        gone_first = await make_profile_version(wired.db, "Shared three")
        low = await make_entry(wired.db, "Low", [gone_first], deleted=True)
        await make_entry(wired.db, "High", [gone_first], deleted=True)
        assert await profile_entry_id(wired.db, gone_first) == low


class TestTheDefaultOnTheForm:
    @pytest.mark.parametrize(
        ("start", "to", "expected"),
        [
            ("nine_old", "nine_new", "v1.1"),  # another version of the same entry: minor
            ("nine_old", "eight_old", "v2"),  # another entry: major
            ("nine_old", "loose", "v2"),  # a version in no entry: major
            ("loose", "other_loose", "v2"),  # two loose versions are two profiles
            ("loose", "loose", "v1.1"),  # the very same version: nothing moved
            ("nine_old", None, "v2"),  # no profile is a different profile
            (None, "nine_old", "v2"),
        ],
    )
    async def test_by_what_the_set_moves_to(
        self, wired: Fixtures, start: str | None, to: str | None, expected: str
    ) -> None:
        w = await world(wired.db)
        ids = vars(w)
        set_id = await _new_set(wired, ids[start] if start else None)
        patch = {"profile_version_id": ids[to] if to else None, "dose_g": 18.5}
        added = await wired.sets.add_version(set_id, SetVersionPatch.model_validate(patch))
        assert added is not None
        assert added.version_label == expected

    async def test_the_persons_box_wins_in_both_directions(self, wired: Fixtures) -> None:
        w = await world(wired.db)
        set_id = await _new_set(wired, w.nine_old)
        minor = await wired.sets.add_version(
            set_id, SetVersionPatch(profile_version_id=w.eight_old), major=False
        )
        major = await wired.sets.add_version(
            set_id, SetVersionPatch(profile_version_id=w.eight_new), major=True
        )
        assert minor is not None and major is not None
        assert (minor.version_label, major.version_label) == ("v1.1", "v2")


class TestTheDefaultOnAProposal:
    async def _accept(self, wired: Fixtures, start: int, to: int) -> tuple[bool, str]:
        set_id = await _new_set(wired, start)
        proposals = SetProposalsRepository(wired.db)
        created = await proposals.create(
            set_id,
            ProposalWrite(
                patch=SetVersionPatch(profile_version_id=to),
                reason="a different profile for more body",
                prediction=PREDICTION,
            ),
        )
        waiting = created.proposal
        assert waiting is not None, created.refused
        default = await proposals.default_major(waiting)
        result = await proposals.accept(set_id, waiting.id)
        assert result.version is not None
        return default, result.version.version_label

    async def test_another_version_of_the_same_profile_is_minor(self, wired: Fixtures) -> None:
        w = await world(wired.db)
        assert await self._accept(wired, w.nine_old, w.nine_new) == (False, "v1.1")

    async def test_another_entry_is_major(self, wired: Fixtures) -> None:
        w = await world(wired.db)
        assert await self._accept(wired, w.nine_old, w.eight_old) == (True, "v2")

    async def test_a_version_in_no_entry_is_major(self, wired: Fixtures) -> None:
        w = await world(wired.db)
        assert await self._accept(wired, w.nine_old, w.loose) == (True, "v2")


class TestWhatTheWebIsServed:
    async def test_every_version_and_every_option_names_its_entry(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        db = app.state.db
        w = await world(db)
        bean = (await client.post("/api/beans", json={"name": "Guji"})).json()["data"]["id"]
        created = await client.post(
            "/api/sets",
            json={"name": "S", "bean_id": bean, "version": {"profile_version_id": w.nine_old}},
        )
        set_id = created.json()["data"]["id"]
        await client.post(f"/api/sets/{set_id}/versions", json={"profile_version_id": w.eight_new})

        detail: Any = (await client.get(f"/api/sets/{set_id}")).json()["data"]
        served = {
            entry["version"]["profile_version_id"]: entry["version"]["profile_entry_id"]
            for entry in detail["versions"]
        }
        assert served == {w.nine_old: w.nine_entry, w.eight_new: w.eight_entry}
        options: Any = (await client.get("/api/profile-versions?limit=200")).json()["data"]
        by_id = {item["id"]: item["profile_entry_id"] for item in options["items"]}
        assert by_id[w.nine_old] == by_id[w.nine_new] == w.nine_entry
        assert by_id[w.eight_old] == by_id[w.eight_new] == w.eight_entry
        assert by_id[w.loose] is None and by_id[w.other_loose] is None
