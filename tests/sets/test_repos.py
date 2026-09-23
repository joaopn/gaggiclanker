"""Beans, grinders, Sets and versions at the repository level.

The properties here are the ones the schema is supposed to guarantee — version
numbering, parent linkage, what the matcher is offered — so every one of them is
asserted against the real file rather than against a mock that would agree with
whatever the code did.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import (
    SetRow,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
    version_changes,
)
from tests.sets.conftest import Fixtures, make_profile_version, make_shot


async def _new_set(wired: Fixtures, name: str = "Guji on the Niche", **version: object) -> SetRow:
    return await wired.sets.create(
        SetWrite(name=name, bean_id=wired.bean_id),
        SetVersionWrite(**version),  # type: ignore[arg-type]
    )


class TestBeans:
    async def test_create_read_and_archive(self, db: Database) -> None:
        beans = BeansRepository(db)
        bean = await beans.create(
            BeanWrite(
                name="Kenya Kiambu",
                roaster="Square Mile",
                roast_level="medium-light",
                process="washed",
            )
        )
        assert bean.id > 0
        assert bean.archived is False
        assert [row.id for row in await beans.list_all()] == [bean.id]

        archived = await beans.set_archived(bean.id, archived=True)
        assert archived is not None and archived.archived is True
        # Archived is not deleted: it is out of the pickers and still readable.
        assert await beans.list_all() == []
        assert [row.id for row in await beans.list_all(include_archived=True)] == [bean.id]

    async def test_the_list_is_alphabetical(self, db: Database) -> None:
        """A bean is a type, so there is no "most recent" to sort by.

        Case-insensitively, because a list where `apple` sorts after `Zambia`
        is a list nobody can find a name in.
        """
        beans = BeansRepository(db)
        zambia = await beans.create(BeanWrite(name="Zambia Ngoli"))
        guji = await beans.create(BeanWrite(name="guji natural"))
        colombia = await beans.create(BeanWrite(name="Colombia Huila"))
        assert [row.id for row in await beans.list_all()] == [colombia.id, guji.id, zambia.id]

    async def test_a_bean_cannot_carry_a_roast_date(self, db: Database) -> None:
        """The one bag-shaped field there was. `extra="forbid"` is the check."""
        with pytest.raises(ValueError, match="roast_date"):
            BeanWrite(name="x", roast_date="2026-01-01")  # type: ignore[call-arg]

    async def test_a_closed_vocabulary_is_closed(self, db: Database) -> None:
        with pytest.raises(ValueError, match="roast_level"):
            BeanWrite(name="x", roast_level="charred")  # type: ignore[arg-type]

    async def test_set_count_is_what_the_archive_button_reads(self, wired: Fixtures) -> None:
        await _new_set(wired)
        bean = await BeansRepository(wired.db).get(wired.bean_id)
        assert bean is not None and bean.set_count == 1


class TestGrinders:
    async def test_create_and_edit(self, db: Database) -> None:
        grinders = GrindersRepository(db)
        grinder = await grinders.create(GrinderWrite(name="DF64", burr_type="flat"))
        assert grinder.step_unit == "clicks"
        edited = await grinders.update(
            grinder.id, GrinderWrite(name="DF64 v2", burr_type="flat", step_unit="microns")
        )
        assert edited is not None
        assert (edited.name, edited.step_unit) == ("DF64 v2", "microns")

    async def test_editing_one_that_is_not_there_is_not_an_error_here(self, db: Database) -> None:
        assert await GrindersRepository(db).update(404, GrinderWrite(name="ghost")) is None


class TestSets:
    async def test_create_makes_version_one_and_offers_it_to_the_matcher(
        self, wired: Fixtures
    ) -> None:
        row = await _new_set(wired, dose_g=18.0, target_yield_g=36.0, grind_setting="22")
        assert row.current_version_no == 1
        assert row.automatch is True
        assert row.archived is False
        assert row.bean_name == "Ethiopia Guji"

        versions = await wired.sets.versions(row.id)
        assert len(versions) == 1
        assert versions[0].parent_version_id is None
        assert versions[0].dose_g == 18.0
        assert versions[0].origin == "manual"

    async def test_a_new_version_records_its_parent_and_inherits_the_rest(
        self, wired: Fixtures
    ) -> None:
        row = await _new_set(wired, dose_g=18.0, target_yield_g=36.0, grind_setting="22")
        first = (await wired.sets.versions(row.id))[0]

        second = await wired.sets.add_version(
            row.id, SetVersionPatch(grind_setting="21", intent="chasing the sourness out")
        )
        assert second is not None
        assert second.version_no == 2
        assert second.parent_version_id == first.id
        assert second.grind_setting == "21"
        # Not sent, so inherited — the whole point of a patch rather than a PUT.
        assert second.dose_g == 18.0
        assert second.target_yield_g == 36.0
        assert second.intent == "chasing the sourness out"

    async def test_sending_null_clears_where_omitting_inherits(self, wired: Fixtures) -> None:
        row = await _new_set(wired, dose_g=18.0, target_yield_g=36.0)
        cleared = await wired.sets.add_version(
            row.id,
            SetVersionPatch.model_validate({"target_yield_g": None, "intent": "back to default"}),
        )
        assert cleared is not None
        assert cleared.target_yield_g is None
        assert cleared.dose_g == 18.0

    async def test_the_diff_is_computed_against_the_parent(self, wired: Fixtures) -> None:
        row = await _new_set(wired, dose_g=18.0, grind_setting="22")
        await wired.sets.add_version(
            row.id,
            SetVersionPatch(dose_g=18.5, grind_setting="21", intent="finer and a touch more"),
        )
        versions = await wired.sets.versions(row.id)
        by_id = {version.id: version for version in versions}
        newest = versions[0]

        changes = version_changes(newest, by_id[newest.parent_version_id or 0])
        assert {change.field for change in changes} == {"dose_g", "grind_setting"}
        dose = next(change for change in changes if change.field == "dose_g")
        assert (dose.before, dose.after) == ("18 g", "18.5 g")

        # Version 1 is a baseline, not a change to anything.
        assert version_changes(versions[-1], None) == []

    async def test_a_versions_temperature_is_read_from_its_profile(self, wired: Fixtures) -> None:
        """The number follows the profile, and there is nowhere to type one.

        Three cases, because "no temperature" has two honest causes: a version
        that names no profile at all, and one whose profile states none (the
        firmware writes 0 for "not set", which is not a 0 °C shot).
        """
        hot = await make_profile_version(wired.db, "9 Bar Espresso", temperature=94)
        silent = await make_profile_version(wired.db, "Says nothing", temperature=0)

        with pytest.raises(ValidationError):
            SetVersionWrite.model_validate({"target_temperature_c": 93})

        row = await _new_set(wired, profile_version_id=hot, dose_g=18.0)
        first = await wired.sets.current_version(row.id)
        assert first is not None and first.profile_temperature_c == 94

        quiet = await wired.sets.add_version(
            row.id, SetVersionPatch(profile_version_id=silent, intent="a profile with no target")
        )
        assert quiet is not None and quiet.profile_temperature_c is None

        none_at_all = await wired.sets.add_version(
            row.id,
            SetVersionPatch.model_validate({"profile_version_id": None, "intent": "any profile"}),
        )
        assert none_at_all is not None and none_at_all.profile_temperature_c is None

    async def test_a_temperature_change_is_reported_with_the_profile_change(
        self, wired: Fixtures
    ) -> None:
        """ "93 → 94 °C" is the profile's doing, and only ever shown as such.

        A version can no longer differ from its parent in temperature alone, so
        the change is attached to the profile change that carried it — and a
        profile switch between two documents that agree on the temperature adds
        no line at all.
        """
        cool = await make_profile_version(wired.db, "9 Bar Espresso", temperature=93)
        hot = await make_profile_version(wired.db, "9 Bar Espresso hotter", temperature=94)
        same_heat = await make_profile_version(wired.db, "A different shape", temperature=94)

        row = await _new_set(wired, profile_version_id=cool, dose_g=18.0)
        await wired.sets.add_version(
            row.id, SetVersionPatch(profile_version_id=hot, intent="a degree hotter")
        )
        versions = await wired.sets.versions(row.id)
        by_id = {version.id: version for version in versions}
        changes = version_changes(versions[0], by_id[versions[0].parent_version_id or 0])
        assert {change.field for change in changes} == {
            "profile_version_id",
            "profile_temperature_c",
        }
        temperature = next(c for c in changes if c.field == "profile_temperature_c")
        assert (temperature.before, temperature.after) == ("93 °C", "94 °C")
        assert temperature.label == "Temperature"
        # Marked, because there is no Temperature field on the form any more and
        # a line nobody can explain reads as a bug.
        assert temperature.from_profile is True

        # A profile change that keeps the temperature says nothing about it.
        await wired.sets.add_version(
            row.id, SetVersionPatch(profile_version_id=same_heat, intent="another shape")
        )
        versions = await wired.sets.versions(row.id)
        by_id = {version.id: version for version in versions}
        assert {
            change.field
            for change in version_changes(versions[0], by_id[versions[0].parent_version_id or 0])
        } == {"profile_version_id"}

        # And neither does a change that leaves the profile alone.
        await wired.sets.add_version(row.id, SetVersionPatch(grind_setting="21", intent="finer"))
        versions = await wired.sets.versions(row.id)
        by_id = {version.id: version for version in versions}
        assert {
            change.field
            for change in version_changes(versions[0], by_id[versions[0].parent_version_id or 0])
        } == {"grind_setting"}

    async def test_version_numbers_are_unique_per_set(self, wired: Fixtures) -> None:
        first = await _new_set(wired, dose_g=18.0)
        second = await _new_set(wired, name="A different bag")
        await wired.sets.add_version(first.id, SetVersionPatch(dose_g=19.0))
        await wired.sets.add_version(second.id, SetVersionPatch(dose_g=20.0))
        assert [v.version_no for v in await wired.sets.versions(first.id)] == [2, 1]
        assert [v.version_no for v in await wired.sets.versions(second.id)] == [2, 1]

    async def test_any_number_of_sets_collect_shots(self, wired: Fixtures) -> None:
        """Several grinders, several bags loaded, and no flag to fight over."""
        first = await _new_set(wired, name="Bag one")
        second = await _new_set(wired, name="Bag two")
        assert (await wired.sets.get(first.id)).automatch is True  # type: ignore[union-attr]
        assert (await wired.sets.get(second.id)).automatch is True  # type: ignore[union-attr]

        out = await wired.sets.set_automatch(first.id, False)
        assert out is not None and out.automatch is False
        # Taking one out of the running leaves the other exactly as it was.
        other = await wired.sets.get(second.id)
        assert other is not None
        assert (other.automatch, other.archived) == (True, False)

        back = await wired.sets.set_automatch(first.id, True)
        assert back is not None and back.automatch is True
        assert (await wired.sets.get(second.id)).automatch is True  # type: ignore[union-attr]

    async def test_archiving_clears_automatch(self, wired: Fixtures) -> None:
        row = await _new_set(wired)
        archived = await wired.sets.archive(row.id)
        assert archived is not None
        assert (archived.archived, archived.automatch) == (True, False)
        assert await wired.sets.list_sets() == []
        assert len(await wired.sets.list_sets(include_archived=True)) == 1

    async def test_shot_counts_roll_up_from_the_versions(self, wired: Fixtures) -> None:
        row = await _new_set(wired)
        version = (await wired.sets.versions(row.id))[0]
        for index in range(3):
            shot_id = await make_shot(wired.db, f"00010{index}")
            assert await wired.sets.assign_shot(shot_id, version.id)
        refreshed = await wired.sets.get(row.id)
        assert refreshed is not None and refreshed.shot_count == 3
        assert (await wired.sets.versions(row.id))[0].shot_count == 3

    async def test_assigning_to_a_version_that_does_not_exist_is_refused(
        self, wired: Fixtures
    ) -> None:
        shot_id = await make_shot(wired.db, "000200")
        assert await wired.sets.assign_shot(shot_id, 4040) is False
        # The column carries no foreign key, so this check is the only thing
        # standing between a typo and a dangling reference.
        shot = await wired.shots.get(shot_id)
        assert shot is not None and shot.set_version_id is None

    async def test_unassigning_is_assigning_to_nothing(self, wired: Fixtures) -> None:
        row = await _new_set(wired)
        version = (await wired.sets.versions(row.id))[0]
        shot_id = await make_shot(wired.db, "000201")
        await wired.sets.assign_shot(shot_id, version.id)
        assert await wired.sets.assign_shot(shot_id, None)
        shot = await wired.shots.get(shot_id)
        assert shot is not None and shot.set_version_id is None

    async def test_the_profile_label_comes_along(self, wired: Fixtures) -> None:
        version_id = await make_profile_version(wired.db, "Adaptive v2")
        row = await _new_set(wired, profile_version_id=version_id)
        assert row.profile_label == "Adaptive v2"
        assert (await wired.sets.versions(row.id))[0].profile_label == "Adaptive v2"


class TestConcurrency:
    async def test_two_versions_added_at_once_both_land(self, wired: Fixtures) -> None:
        """Two writers, one connection, two distinct version numbers.

        There is one SQLite connection, so there is one transaction. Before
        `Database.transaction()` took a lock, two coroutines that both reached
        `BEGIN IMMEDIATE` — two browser tabs, or a tab and the sync engine —
        gave the second "cannot start a transaction within a transaction",
        which surfaced as a 500 on a request that had done nothing wrong.

        Both landing *with distinct numbers* is the other half: the parent is
        read inside the transaction that writes the child, so serialising the
        transactions is also what stops two versions claiming the same
        `version_no` and colliding on its unique index.
        """
        row = await _new_set(wired, dose_g=18.0)

        results = await asyncio.gather(
            wired.sets.add_version(row.id, SetVersionPatch(dose_g=19.0, intent="a")),
            wired.sets.add_version(row.id, SetVersionPatch(dose_g=20.0, intent="b")),
        )

        assert all(version is not None for version in results)
        assert sorted(version.version_no for version in results if version) == [2, 3]
        assert [v.version_no for v in await wired.sets.versions(row.id)] == [3, 2, 1]

    async def test_a_transaction_arriving_mid_flight_waits_instead_of_failing(
        self, wired: Fixtures
    ) -> None:
        """A concurrent caller is not a nested caller.

        The guard against re-entrancy must be task-local: a request that arrives
        while another task's transaction is open has to queue on the lock, not
        be mistaken for nesting and blow up with a 500.
        """
        import asyncio

        opened = asyncio.Event()
        release = asyncio.Event()

        async def holder() -> None:
            async with wired.db.transaction():
                opened.set()
                await release.wait()

        async def latecomer() -> int:
            await opened.wait()
            await asyncio.sleep(0)  # arrive strictly after the holder has BEGUN
            row = await _new_set(wired, name="Arrived mid-flight")
            return row.id

        holder_task = asyncio.create_task(holder())
        late_task = asyncio.create_task(latecomer())
        await opened.wait()
        await asyncio.sleep(0.05)
        assert not late_task.done()  # queued on the lock, not failed
        release.set()
        await holder_task
        assert isinstance(await late_task, int)

    async def test_a_nested_transaction_is_a_loud_failure(self, wired: Fixtures) -> None:
        """Re-entrancy is not supported, and says so rather than deadlocking.

        An inner `COMMIT` would either publish the outer block's half-finished
        work or be a no-op whose rollback silently loses data. The assert turns
        that into a failure at the call site (a RuntimeError, so python -O keeps it).
        """
        async with wired.db.transaction():
            with pytest.raises(RuntimeError, match="nested transaction"):
                async with wired.db.transaction():
                    pass  # pragma: no cover - the guard fires on the way in

    async def test_the_lock_is_released_when_a_transaction_fails(self, wired: Fixtures) -> None:
        with pytest.raises(RuntimeError):
            async with wired.db.transaction():
                raise RuntimeError("something went wrong half-way")

        # A lock left held by a failed write is a hang, not an error, and it
        # would only show up under the load that caused the failure.
        row = await _new_set(wired, name="After the failure")
        assert row.current_version_no == 1


class TestAutomatchRefusals:
    async def test_an_archived_set_cannot_be_offered_to_the_matcher(self, wired: Fixtures) -> None:
        """The flag would promise something the matcher will never do.

        `versions_naming_profile` filters on `archived` as well, so a shot
        would never be filed there whatever the flag said.
        """
        live = await _new_set(wired, name="The live one")
        old = await _new_set(wired, name="Last month's bag")
        await wired.sets.archive(old.id)

        assert await wired.sets.set_automatch(old.id, True) is None

        assert (await wired.sets.get(old.id)).automatch is False  # type: ignore[union-attr]
        assert (await wired.sets.get(live.id)).automatch is True  # type: ignore[union-attr]

    async def test_a_shot_cannot_be_filed_under_an_archived_set(self, wired: Fixtures) -> None:
        row = await _new_set(wired)
        version = (await wired.sets.versions(row.id))[0]
        await wired.sets.archive(row.id)
        shot_id = await make_shot(wired.db, "000500")

        assert await wired.sets.assign_shot(shot_id, version.id) is False

    async def test_a_tombstoned_device_profile_does_not_match(self, wired: Fixtures) -> None:
        """A deleted profile id has been freed and may point at something else."""
        version_id = await make_profile_version(wired.db, "Adaptive v2")
        profiles = ProfilesRepository(wired.db)
        await profiles.upsert_device_profile(device_id="adapt", version_id=version_id)
        await _new_set(wired, profile_version_id=version_id)
        # The user deleted it on the machine, so the next profiles pass listed
        # everything except it. (An *empty* list is a failed read and tombstones
        # nothing — see `mark_missing_deleted`.)
        await profiles.upsert_device_profile(device_id="9bar", version_id=version_id)
        assert await profiles.mark_missing_deleted(["9bar"]) == 1

        shot_id = await make_shot(wired.db, "000501")
        assert (
            await wired.sets.profile_match(
                shot_id,
                profile_version_id=None,
                device_profile_id="adapt",
            )
        ).outcome == "unmatched"
