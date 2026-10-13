"""A shot brewed on a pin's copy is filed under the pinned Set version.

The filing rule gains one step ahead of the profile match: a shot whose profile is the copy of a
pin (live or unpinned) goes to that pin's Set version, only into a NULL, only while the Set's own
"file matching shots here" is on and the Set is not archived. Every caller goes through it: the
sync engine, the importer and the Match by profile button.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.pins import PinsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetVersionPatch
from gaggiclanker.imports.service import ImportService
from tests.sets.conftest import Fixtures, make_pin, make_profile_version, make_shot
from tests.sets.test_assignment import (
    _device_profile_ids,
    _device_with_two_shots,
    _mirror,
    _set_naming,
)
from tests.sets.test_profile_match import FIXTURES, _filed, _set_on
from tests.sync.conftest import archive_for


async def _pinned(wired: Fixtures, *, automatch: bool = True) -> tuple[int, int, int, int, int]:
    """A Set on "Blooming" whose v1 is pinned and which has moved on to v1.1:
    (set id, v1, v1.1, the pin, the copy's profile version)."""
    original = await make_profile_version(wired.db, "Blooming")
    set_id = await _set_on(wired, "Guji", original, automatch=automatch)
    v1 = await wired.sets.current_version(set_id)
    assert v1 is not None
    second = await wired.sets.add_version(set_id, SetVersionPatch(dose_g=19.0))
    assert second is not None
    pin, copy, _row = await make_pin(wired.db, v1.id, "Blooming v1")
    return set_id, v1.id, second.id, pin, copy


class TestProfileMatch:
    async def test_a_shot_on_the_copy_is_filed_under_the_pinned_version_not_the_current_one(
        self, wired: Fixtures
    ) -> None:
        _set, pinned_version, current, _pin, copy = await _pinned(wired)
        shot = await make_shot(wired.db, "000901", profile_version_id=copy)

        match = await wired.sets.profile_match(shot, profile_version_id=copy, device_profile_id="")

        assert (match.outcome, match.set_version_id) == ("matched", pinned_version)
        assert await _filed(wired, shot) == pinned_version != current

    async def test_the_copy_files_by_its_device_id_when_the_shot_has_no_link_yet(
        self, wired: Fixtures
    ) -> None:
        _set, pinned_version, _current, _pin, copy = await _pinned(wired)
        await ProfilesRepository(wired.db).upsert_device_profile(
            device_id="pin001", version_id=copy
        )
        shot = await make_shot(wired.db, "000902", profile_id_on_device="pin001")

        match = await wired.sets.profile_match(
            shot, profile_version_id=None, device_profile_id="pin001"
        )

        assert match.set_version_id == pinned_version

    async def test_a_filed_shot_is_never_moved_by_a_pin(self, wired: Fixtures) -> None:
        _set, _pinned_version, current, _pin, copy = await _pinned(wired)
        shot = await make_shot(wired.db, "000903", profile_version_id=copy)
        await wired.sets.assign_shot(shot, current)

        match = await wired.sets.profile_match(shot, profile_version_id=copy, device_profile_id="")

        assert match.outcome == "unmatched" and await _filed(wired, shot) == current

    async def test_an_unpinned_copy_still_files_its_shots(self, wired: Fixtures) -> None:
        """A shot brewed between the Unpin and the sync that removes the file."""
        _set, pinned_version, _current, pin, copy = await _pinned(wired)
        await PinsRepository(wired.db).mark_unpinned(pin)
        shot = await make_shot(wired.db, "000904", profile_version_id=copy)

        match = await wired.sets.profile_match(shot, profile_version_id=copy, device_profile_id="")

        assert match.set_version_id == pinned_version

    async def test_with_the_sets_flag_off_the_shot_waits_and_the_button_files_it_later(
        self, wired: Fixtures
    ) -> None:
        set_id, pinned_version, _current, _pin, copy = await _pinned(wired, automatch=False)
        shot = await make_shot(wired.db, "000905", profile_version_id=copy)

        match = await wired.sets.profile_match(shot, profile_version_id=copy, device_profile_id="")
        assert match.outcome == "unmatched" and await _filed(wired, shot) is None
        waiting = await wired.sets.match_unfiled()
        assert waiting.model_dump() == {"matched": 0, "unmatched": 1}

        await wired.sets.set_automatch(set_id, True)
        summary = await wired.sets.match_unfiled()

        assert summary.matched == 1 and await _filed(wired, shot) == pinned_version

    async def test_an_archived_set_files_nothing_on_its_pins(self, wired: Fixtures) -> None:
        set_id, _pinned_version, _current, _pin, copy = await _pinned(wired)
        # Archived with the flag still on (archiving clears it; the rule must not lean on that).
        await wired.db.execute("UPDATE sets SET archived = 1 WHERE id = ?", (set_id,))
        shot = await make_shot(wired.db, "000906", profile_version_id=copy)

        match = await wired.sets.profile_match(shot, profile_version_id=copy, device_profile_id="")

        assert match.outcome == "unmatched" and await _filed(wired, shot) is None

    async def test_a_pin_does_not_change_where_the_originals_shots_go(
        self, wired: Fixtures
    ) -> None:
        set_id, _pinned_version, current, _pin, _copy = await _pinned(wired)
        version = await wired.sets.current_version(set_id)
        assert version is not None and version.profile_version_id is not None
        original = version.profile_version_id
        shot = await make_shot(wired.db, "000907", profile_version_id=original)

        match = await wired.sets.profile_match(
            shot, profile_version_id=original, device_profile_id=""
        )

        assert (match.outcome, match.set_version_id) == ("matched", current)


class TestIngest:
    async def test_an_import_files_a_shot_on_the_copy_under_the_pinned_version(
        self, wired: Fixtures
    ) -> None:
        original = await make_profile_version(wired.db, "Gratus 16:32 trad")
        set_id = await _set_on(wired, "Imported archive", original)
        v1 = await wired.sets.current_version(set_id)
        assert v1 is not None
        await wired.sets.add_version(set_id, SetVersionPatch(dose_g=19.5))
        _pin, copy, _row = await make_pin(wired.db, v1.id, "Gratus pinned")
        # shot-129's header names this profile id; the mirror maps it to the pin's copy.
        await ProfilesRepository(wired.db).upsert_device_profile(
            device_id="rV4GhUcSZc", version_id=copy
        )

        result = await ImportService(wired.db).import_shot(
            (FIXTURES / "exports" / "shot-129.json").read_bytes(), filename="shot-129.json"
        )

        assert result.shot_id is not None
        assert await _filed(wired, result.shot_id) == v1.id


class TestSync:
    async def test_a_pulled_shot_on_the_copy_goes_to_the_pinned_version(
        self, tmp_path: Path
    ) -> None:
        matching, other = _device_profile_ids()
        device = _device_with_two_shots()
        await device.start()
        try:
            async with archive_for(device, tmp_path) as archive:
                await archive.engine.sync_identity()
                original = await _mirror(archive, matching, "Adaptive v2")
                await _mirror(archive, other, "9 Bar Espresso")
                set_id = await _set_naming(archive, original)
                sets = archive.engine.sets
                v1 = await sets.current_version(set_id)
                assert v1 is not None
                await sets.add_version(set_id, SetVersionPatch(dose_g=19.0))
                # The machine brews this shot on the pin's copy.
                _pin, copy, _row = await make_pin(archive.db, v1.id, "Adaptive v2 v1")
                await ProfilesRepository(archive.db).upsert_device_profile(
                    device_id=matching, version_id=copy
                )

                await archive.engine.sync_shots()

                first = await archive.engine.shots.get_by_device_id("000300")
                assert first is not None and first.set_version_id == v1.id
        finally:
            await device.stop()


class TestButton:
    async def test_the_button_files_a_waiting_pinned_shot_once_the_flag_is_on(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        db = app.state.db
        original = await make_profile_version(db, "Blooming")
        bean = await client.post("/api/beans", json={"name": "Ethiopia Guji"})
        created = await client.post(
            "/api/sets",
            json={
                "name": "Guji",
                "bean_id": bean.json()["data"]["id"],
                "automatch": False,
                "version": {"profile_version_id": original, "dose_g": 18.0},
            },
        )
        assert created.status_code == 201, created.text
        set_id = created.json()["data"]["id"]
        version_id = int(
            (await client.get(f"/api/sets/{set_id}")).json()["data"]["versions"][0]["version"]["id"]
        )
        _pin, copy, _row = await make_pin(db, version_id, "Blooming v1")
        shot = await make_shot(db, "001100", profile_version_id=copy)

        waiting = await client.post("/api/shots/profile-match", json={})
        assert waiting.json()["data"] == {"matched": 0, "unmatched": 1}
        flagged = await client.put(f"/api/sets/{set_id}/automatch", json={"automatch": True})
        assert flagged.status_code == 200, flagged.text
        filed = await client.post("/api/shots/profile-match", json={"shot_ids": [shot]})

        assert filed.json()["data"] == {"matched": 1, "unmatched": 0}
        detail = (await client.get(f"/api/shots/{shot}")).json()["data"]
        assert detail["shot"]["set_version_id"] == version_id
