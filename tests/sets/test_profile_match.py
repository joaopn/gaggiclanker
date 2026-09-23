"""The matcher: a shot goes to the one Set offered to it that brews its profile.

This is the only path into a Set that nobody chose by hand, for a pull, an
import and the button alike. The rule is "only when it is not a guess". Each
test below pins one way it could become a guess — two Sets on the profile, a
Set that names no profile, a Set that has moved on to another profile — or one
way a Set is not a candidate at all (archived, or `automatch` off), or one way
it could undo somebody's decision (a shot already filed).
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetVersionPatch, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.imports.service import ImportService
from tests.sets.conftest import Fixtures, make_profile_version, make_shot
from tests.sets.test_assignment import (
    _device_profile_ids,
    _device_with_two_shots,
    _mirror,
    _set_naming,
)
from tests.sync.conftest import archive_for

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


async def _set_on(
    wired: Fixtures, name: str, profile_version_id: int | None, *, automatch: bool = True
) -> int:
    """A Set whose current version names this profile, offered to the matcher."""
    row = await wired.sets.create(
        SetWrite(name=name, bean_id=wired.bean_id),
        SetVersionWrite(profile_version_id=profile_version_id, dose_g=18.0),
        automatch=automatch,
    )
    return row.id


async def _current(wired: Fixtures, set_id: int) -> int:
    version = await wired.sets.current_version(set_id)
    assert version is not None
    return version.id


async def _filed(wired: Fixtures, shot_id: int) -> int | None:
    shot = await wired.shots.get(shot_id)
    assert shot is not None
    return shot.set_version_id


class TestProfileMatch:
    async def test_the_one_set_on_the_profile_takes_the_shot(self, wired: Fixtures) -> None:
        profile = await make_profile_version(wired.db, "Adaptive v2")
        other = await make_profile_version(wired.db, "9 Bar Espresso")
        mine = await _set_on(wired, "Guji", profile)
        await _set_on(wired, "Kenya", other)
        shot_id = await make_shot(wired.db, "000500", profile_version_id=profile)

        match = await wired.sets.profile_match(
            shot_id, profile_version_id=profile, device_profile_id=""
        )

        assert match.outcome == "matched"
        assert match.set_version_id == await _current(wired, mine)
        assert await _filed(wired, shot_id) == match.set_version_id

    async def test_two_sets_on_the_profile_is_ambiguous(self, wired: Fixtures) -> None:
        profile = await make_profile_version(wired.db, "Adaptive v2")
        await _set_on(wired, "Guji", profile)
        await _set_on(wired, "Kenya", profile)
        shot_id = await make_shot(wired.db, "000501", profile_version_id=profile)

        match = await wired.sets.profile_match(
            shot_id, profile_version_id=profile, device_profile_id=""
        )

        assert match.outcome == "ambiguous"
        assert await _filed(wired, shot_id) is None

    async def test_an_archived_set_neither_matches_nor_makes_it_ambiguous(
        self, wired: Fixtures
    ) -> None:
        profile = await make_profile_version(wired.db, "Adaptive v2")
        finished = await _set_on(wired, "Finished bag", profile)
        await wired.sets.archive(finished)
        live = await _set_on(wired, "Guji", profile)
        shot_id = await make_shot(wired.db, "000502", profile_version_id=profile)

        match = await wired.sets.profile_match(
            shot_id, profile_version_id=profile, device_profile_id=""
        )
        assert match.set_version_id == await _current(wired, live)

        await wired.sets.archive(live)
        second = await make_shot(wired.db, "000503", profile_version_id=profile)
        after = await wired.sets.profile_match(
            second, profile_version_id=profile, device_profile_id=""
        )
        assert after.outcome == "unmatched"
        assert await _filed(wired, second) is None

    async def test_a_set_the_matcher_is_not_offered_neither_matches_nor_blocks(
        self, wired: Fixtures
    ) -> None:
        """`automatch` off means "not mine to file", not "file it here last".

        It has to be both: a Set out of the running that still made its profile
        ambiguous would stop the Set that *is* in the running from collecting
        anything.
        """
        profile = await make_profile_version(wired.db, "Adaptive v2")
        resting = await _set_on(wired, "Bag in the cupboard", profile, automatch=False)
        shot_id = await make_shot(wired.db, "000510", profile_version_id=profile)

        match = await wired.sets.profile_match(
            shot_id, profile_version_id=profile, device_profile_id=""
        )
        assert match.outcome == "unmatched"
        assert await _filed(wired, shot_id) is None

        live = await _set_on(wired, "Bag in the hopper", profile)
        second = await make_shot(wired.db, "000511", profile_version_id=profile)
        after = await wired.sets.profile_match(
            second, profile_version_id=profile, device_profile_id=""
        )
        assert after.set_version_id == await _current(wired, live)

        # And it comes back into the running when the flag goes on, which is
        # what makes the two Sets ambiguous from then on.
        await wired.sets.set_automatch(resting, True)
        third = await make_shot(wired.db, "000512", profile_version_id=profile)
        assert (
            await wired.sets.profile_match(third, profile_version_id=profile, device_profile_id="")
        ).outcome == "ambiguous"

    async def test_a_set_that_names_no_profile_is_not_configured_for_this_one(
        self, wired: Fixtures
    ) -> None:
        profile = await make_profile_version(wired.db, "Adaptive v2")
        await _set_on(wired, "Whatever is loaded", None)
        shot_id = await make_shot(wired.db, "000504", profile_version_id=profile)
        match = await wired.sets.profile_match(
            shot_id, profile_version_id=profile, device_profile_id=""
        )
        assert match.outcome == "unmatched"

        mine = await _set_on(wired, "Guji", profile)
        match = await wired.sets.profile_match(
            shot_id, profile_version_id=profile, device_profile_id=""
        )
        assert match.set_version_id == await _current(wired, mine)

    async def test_only_the_current_version_counts(self, wired: Fixtures) -> None:
        """A Set that moved on to another profile no longer brews this one."""
        old = await make_profile_version(wired.db, "Adaptive v2")
        new = await make_profile_version(wired.db, "Adaptive v3")
        moved = await _set_on(wired, "Guji", old)
        await wired.sets.add_version(moved, SetVersionPatch(profile_version_id=new, intent="v3"))
        stayed = await _set_on(wired, "Kenya", old)
        shot_id = await make_shot(wired.db, "000505", profile_version_id=old)

        match = await wired.sets.profile_match(
            shot_id, profile_version_id=old, device_profile_id=""
        )

        # Not ambiguous: only one Set brews `old` today, and the shot goes to
        # that Set's current version, not to the other's history.
        assert match.set_version_id == await _current(wired, stayed)

    async def test_the_device_profile_id_stands_in_until_the_mirror_catches_up(
        self, wired: Fixtures
    ) -> None:
        profile = await make_profile_version(wired.db, "Adaptive v2")
        profiles = ProfilesRepository(wired.db)
        await profiles.upsert_device_profile(device_id="abc123", version_id=profile)
        mine = await _set_on(wired, "Guji", profile)
        shot_id = await make_shot(wired.db, "000506", profile_id_on_device="abc123")

        match = await wired.sets.profile_match(
            shot_id, profile_version_id=None, device_profile_id="abc123"
        )
        assert match.set_version_id == await _current(wired, mine)

    async def test_a_tombstoned_device_profile_id_says_nothing(self, wired: Fixtures) -> None:
        profile = await make_profile_version(wired.db, "Adaptive v2")
        await wired.db.execute(
            """
            INSERT INTO device_profiles (device_id, current_version_id, first_seen_at,
                                         last_seen_at, deleted_at)
            VALUES ('gone01', ?, '2026-01-01', '2026-01-01', '2026-01-02')
            """,
            (profile,),
        )
        await _set_on(wired, "Guji", profile)
        shot_id = await make_shot(wired.db, "000507", profile_id_on_device="gone01")

        match = await wired.sets.profile_match(
            shot_id, profile_version_id=None, device_profile_id="gone01"
        )
        assert match.outcome == "unmatched"
        assert await _filed(wired, shot_id) is None

    async def test_a_filed_shot_is_never_moved(self, wired: Fixtures) -> None:
        profile = await make_profile_version(wired.db, "Adaptive v2")
        elsewhere = await _set_on(wired, "By hand", None)
        await _set_on(wired, "Guji", profile)
        shot_id = await make_shot(wired.db, "000508", profile_version_id=profile)
        by_hand = await _current(wired, elsewhere)
        await wired.sets.assign_shot(shot_id, by_hand)

        match = await wired.sets.profile_match(
            shot_id, profile_version_id=profile, device_profile_id=""
        )
        assert match.outcome == "unmatched"
        assert await _filed(wired, shot_id) == by_hand
        summary = await wired.sets.match_unfiled([shot_id])
        assert summary.model_dump() == {"matched": 0, "ambiguous": 0, "unmatched": 0}
        assert await _filed(wired, shot_id) == by_hand


class TestMatchUnfiled:
    async def test_every_waiting_shot_is_offered_and_counted(self, wired: Fixtures) -> None:
        single = await make_profile_version(wired.db, "Adaptive v2")
        shared = await make_profile_version(wired.db, "9 Bar Espresso")
        lonely = await make_profile_version(wired.db, "Blooming")
        mine = await _set_on(wired, "Guji", single)
        await _set_on(wired, "Kenya", shared)
        await _set_on(wired, "Brazil", shared)

        matched = [
            await make_shot(wired.db, "000600", profile_version_id=single),
            await make_shot(wired.db, "000601", profile_version_id=single),
        ]
        ambiguous = await make_shot(wired.db, "000602", profile_version_id=shared)
        no_set = await make_shot(wired.db, "000603", profile_version_id=lonely)
        unknown = await make_shot(wired.db, "000604")

        summary = await wired.sets.match_unfiled()

        assert summary.model_dump() == {"matched": 2, "ambiguous": 1, "unmatched": 2}
        for shot_id in matched:
            assert await _filed(wired, shot_id) == await _current(wired, mine)
        for shot_id in (ambiguous, no_set, unknown):
            assert await _filed(wired, shot_id) is None

    async def test_quarantined_shots_are_left_alone(self, wired: Fixtures) -> None:
        profile = await make_profile_version(wired.db, "Adaptive v2")
        await _set_on(wired, "Guji", profile)
        shot_id = await ShotsRepository(wired.db).insert(
            ShotInsert(
                device_id="000700",
                raw_slog=b"broken",
                profile_version_id=profile,
                quarantined=True,
                quarantine_reason="did not parse",
            ),
            [],
        )
        summary = await wired.sets.match_unfiled()
        assert summary.model_dump() == {"matched": 0, "ambiguous": 0, "unmatched": 0}
        assert await _filed(wired, shot_id) is None

    async def test_a_list_narrows_it_to_those_shots(self, wired: Fixtures) -> None:
        profile = await make_profile_version(wired.db, "Adaptive v2")
        await _set_on(wired, "Guji", profile)
        asked = await make_shot(wired.db, "000800", profile_version_id=profile)
        left = await make_shot(wired.db, "000801", profile_version_id=profile)

        summary = await wired.sets.match_unfiled([asked])

        assert summary.matched == 1
        assert await _filed(wired, asked) is not None
        assert await _filed(wired, left) is None


class TestIngest:
    async def test_an_import_is_matched_with_no_switch_to_turn_on(self, wired: Fixtures) -> None:
        version_id = await make_profile_version(wired.db, "Gratus 16:32 trad")
        # shot-129's header names this profile id; the mirror is what maps it.
        await ProfilesRepository(wired.db).upsert_device_profile(
            device_id="rV4GhUcSZc", version_id=version_id
        )
        mine = await _set_on(wired, "Imported archive", version_id)

        result = await ImportService(wired.db).import_shot(
            (FIXTURES / "exports" / "shot-129.json").read_bytes(), filename="shot-129.json"
        )

        assert result.shot_id is not None
        assert await _filed(wired, result.shot_id) == await _current(wired, mine)

    async def test_an_import_waits_when_two_sets_brew_the_profile(self, wired: Fixtures) -> None:
        version_id = await make_profile_version(wired.db, "Gratus 16:32 trad")
        await ProfilesRepository(wired.db).upsert_device_profile(
            device_id="rV4GhUcSZc", version_id=version_id
        )
        await _set_on(wired, "Imported archive", version_id)
        await _set_on(wired, "Same profile, other bag", version_id)

        result = await ImportService(wired.db).import_shot(
            (FIXTURES / "exports" / "shot-129.json").read_bytes(), filename="shot-129.json"
        )

        assert result.shot_id is not None
        assert await _filed(wired, result.shot_id) is None


class TestSync:
    async def test_each_pulled_shot_goes_to_the_set_that_brews_its_profile(
        self, tmp_path: Path
    ) -> None:
        """Through the real engine, with two Sets collecting at the same time.

        The two-grinder morning this rule exists for: each Set names its own
        profile, both are offered to the matcher, and each shot lands where its
        profile says it belongs.
        """
        matching, other = _device_profile_ids()
        device = _device_with_two_shots()
        await device.start()
        try:
            async with archive_for(device, tmp_path) as archive:
                await archive.engine.sync_identity()
                wanted = await _mirror(archive, matching, "Adaptive v2")
                second = await _mirror(archive, other, "9 Bar Espresso")
                bean = await BeansRepository(archive.db).create(BeanWrite(name="Kenya AA"))
                sets = archive.engine.sets
                on_other = await sets.create(
                    SetWrite(name="Kenya", bean_id=bean.id),
                    SetVersionWrite(profile_version_id=second),
                )
                on_wanted = await _set_naming(archive, wanted)

                await archive.engine.sync_shots()

                first = await archive.engine.shots.get_by_device_id("000300")
                later = await archive.engine.shots.get_by_device_id("000301")
                assert first is not None and later is not None
                wanted_version = await sets.current_version(on_wanted)
                other_version = await sets.current_version(on_other.id)
                assert wanted_version is not None and other_version is not None
                assert first.set_version_id == wanted_version.id
                assert later.set_version_id == other_version.id
        finally:
            await device.stop()


class TestApi:
    async def test_the_button_matches_every_waiting_shot(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        db = app.state.db
        profile = await make_profile_version(db, "Adaptive v2")
        bean = await client.post("/api/beans", json={"name": "Ethiopia Guji"})
        bean_id = bean.json()["data"]["id"]
        created = await client.post(
            "/api/sets",
            json={
                "name": "Guji",
                "bean_id": bean_id,
                "version": {"profile_version_id": profile, "dose_g": 18.0},
            },
        )
        assert created.status_code == 201, created.text
        shot_id = await make_shot(db, "001000", profile_version_id=profile)
        await make_shot(db, "001001")

        response = await client.post("/api/shots/profile-match", json={})

        assert response.status_code == 200, response.text
        assert response.json()["data"] == {"matched": 1, "ambiguous": 0, "unmatched": 1}
        detail = (await client.get(f"/api/shots/{shot_id}")).json()["data"]
        assert detail["shot"]["set_version_id"] is not None

    async def test_one_shot_and_an_unknown_one(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/api/shots/profile-match", json={"shot_ids": [99999]})
        assert response.status_code == 404
        assert response.json()["ok"] is False

    async def test_there_is_no_switch_to_turn_matching_on(self, client: httpx.AsyncClient) -> None:
        """The flag on the Set replaced it; a stored value must not linger."""
        settings = (await client.get("/api/settings")).json()["data"]
        assert "shotsProfileAutomatch" not in settings
        refused = await client.patch("/api/settings", json={"shotsProfileAutomatch": True})
        assert refused.status_code == 400, refused.text
