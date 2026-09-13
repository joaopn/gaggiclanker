"""Auto-assignment, against the fake machine and against the importer.

The chunk's first acceptance criterion: *creating a Set and pulling a shot with
the matching profile assigns it automatically; a shot with another profile lands
in `needs_set`.* Both halves are asserted here through the real sync engine, on
a real database, against the fake device serving the repository's own `.slog`
fixtures — because the interesting part is the join between three tables that
are filled by three different passes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.judgements import JudgementWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetVersionWrite, SetWrite
from gaggiclanker.device.fake import FakeDevice, default_notes
from gaggiclanker.domain.models import SHOT_FLAG_HAS_NOTES, Profile
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.imports.service import ImportService
from tests.sets.conftest import Fixtures, make_shot
from tests.sync.conftest import Archive, archive_for, fixture_slogs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: The two shots the device serves. Ids well above anything the shared archive
#: builder uses, so "the shot I added" is never "the shot it cloned".
MATCHING_ID = 300
OTHER_ID = 301


def _device_profile_ids() -> tuple[str, str]:
    """The profile ids two of the shipped `.slog` fixtures were brewed with.

    Read from the files rather than written down: the headers are real and a
    fixture swapped for another one should change this test's inputs, not break
    it in a way that looks like a bug in the assignment.
    """
    ids = [parse_slog(raw).header.profile_id for raw in fixture_slogs()]
    distinct = list(dict.fromkeys(ids))
    if len(distinct) < 2:  # pragma: no cover - the shipped fixtures differ
        pytest.skip("the .slog fixtures no longer carry two distinct profile ids")
    return distinct[0], distinct[1]


def _device_with_two_shots(*, with_notes: bool = False) -> FakeDevice:
    """A machine holding one shot per profile, and nothing awkward.

    Deliberately not the shared fifty-shot archive: what is under test is the
    join between a Set, the profile mirror and one ingested shot, and a corrupt
    file and a header-only file would only make the failure harder to read.
    """
    raws = fixture_slogs()
    ids = [parse_slog(raw).header.profile_id for raw in raws]
    matching, other = _device_profile_ids()

    device = FakeDevice()
    device.add_shot(
        MATCHING_ID,
        raws[ids.index(matching)],
        timestamp=1_770_000_000,
        notes=default_notes(MATCHING_ID) if with_notes else None,
    )
    device.add_shot(OTHER_ID, raws[ids.index(other)], timestamp=1_770_003_600)
    return device


async def _profile_version(db: Database, label: str) -> int:
    document = json.loads((FIXTURES / "profiles" / "docs-medium-18g.json").read_text())
    document["label"] = label
    version, _ = await ProfilesRepository(db).ensure_version(Profile.model_validate(document))
    return version.id


async def _mirror(archive: Archive, device_profile_id: str, label: str) -> int:
    """Point a device profile id at a stored version, as a profiles pass would."""
    version_id = await _profile_version(archive.db, label)
    await ProfilesRepository(archive.db).upsert_device_profile(
        device_id=device_profile_id, version_id=version_id
    )
    return version_id


async def _set_naming(archive: Archive, version_id: int) -> int:
    """A Set whose current version names this profile."""
    bean = await BeansRepository(archive.db).create(BeanWrite(name="Ethiopia Guji"))
    row = await archive.engine.sets.create(
        SetWrite(name="Guji on the Niche", bean_id=bean.id),
        SetVersionWrite(profile_version_id=version_id, dose_g=18.0, target_yield_g=36.0),
    )
    return row.id


class TestSyncAutoAssignment:
    async def test_a_matching_profile_is_assigned_and_another_is_not(self, tmp_path: Path) -> None:
        matching, other = _device_profile_ids()
        device = _device_with_two_shots()
        await device.start()
        try:
            async with archive_for(device, tmp_path) as archive:
                # Identity first, so the machine row carries the host it was
                # pulled from. The Set does not name it — there is only one —
                # but the profile mirror the Set matches on is written by a pass.
                await archive.engine.sync_identity()
                wanted = await _mirror(archive, matching, "Adaptive v2")
                await _mirror(archive, other, "9 Bar Espresso")
                set_id = await _set_naming(archive, wanted)

                await archive.engine.sync_shots()

                assigned = await archive.engine.shots.get_by_device_id("000300")
                unassigned = await archive.engine.shots.get_by_device_id("000301")
                assert assigned is not None and unassigned is not None

                version = await archive.engine.sets.current_version(set_id)
                assert version is not None
                assert assigned.set_version_id == version.id
                assert assigned.set_badge is not None
                assert assigned.set_badge.version_no == 1
                # The other profile is not this Set's, so the archive says so
                # rather than guessing.
                assert unassigned.set_version_id is None
        finally:
            await device.stop()

    async def test_with_no_active_set_every_shot_needs_one(self, tmp_path: Path) -> None:
        device = _device_with_two_shots()
        await device.start()
        try:
            async with archive_for(device, tmp_path) as archive:
                await archive.engine.sync_shots()
                page = await archive.engine.shots.list_shots(needs_set=True)
                assert page.total == 2
        finally:
            await device.stop()

    async def test_a_device_note_becomes_a_judgement_and_survives_an_edit(
        self, tmp_path: Path
    ) -> None:
        """The second acceptance criterion, through the engine that does it.

        The notes pass runs on every shot sync, so the second `sync_shots()` is
        the real "sync comes round again" — not a hand-rolled second call to the
        repository.
        """
        device = _device_with_two_shots(with_notes=True)
        await device.start()
        try:
            async with archive_for(device, tmp_path) as archive:
                await archive.engine.sync_shots()
                shot = await archive.engine.shots.get_by_device_id("000300")
                assert shot is not None

                seeded = await archive.engine.judgements.get(shot.id)
                assert seeded is not None
                assert seeded.seeded_from_device_note is True

                await archive.engine.judgements.upsert(
                    shot.id, JudgementWrite(rating=5, notes="mine, not the machine's")
                )

                # The device's index entry changed under us, which is what makes
                # the engine re-pull the notes.
                device.shots[MATCHING_ID].entry.rating = 2
                await archive.engine.sync_shots()

                after = await archive.engine.judgements.get(shot.id)
                assert after is not None
                assert (after.rating, after.notes) == (5, "mine, not the machine's")
                assert after.seeded_from_device_note is False
        finally:
            await device.stop()


class TestImporterAutoAssignment:
    async def test_an_imported_shot_joins_the_active_set(self, wired: Fixtures) -> None:
        version_id = await _profile_version(wired.db, "Gratus 16:32 trad")
        # shot-129's header names this profile id; the mirror is what maps it.
        await ProfilesRepository(wired.db).upsert_device_profile(
            device_id="rV4GhUcSZc", version_id=version_id
        )
        created = await wired.sets.create(
            SetWrite(name="Imported archive", bean_id=wired.bean_id),
            SetVersionWrite(profile_version_id=version_id),
        )

        service = ImportService(wired.db)
        result = await service.import_shot(
            (FIXTURES / "exports" / "shot-129.json").read_bytes(),
            filename="shot-129.json",
        )
        assert result.status == "created"
        assert result.shot_id is not None

        shot = await wired.shots.get(result.shot_id)
        current = await wired.sets.current_version(created.id)
        assert shot is not None and current is not None
        assert shot.set_version_id == current.id
        # The export carries a notes card with a yield in it, so the judgement
        # comes across too.
        judgement = await wired.judgements.get(result.shot_id)
        assert judgement is not None
        assert judgement.dose_out_g == 32.1
        assert judgement.seeded_from_device_note is True

    async def test_an_import_with_no_matching_set_needs_one(self, wired: Fixtures) -> None:
        service = ImportService(wired.db)
        result = await service.import_shot(
            (FIXTURES / "exports" / "shot-129.json").read_bytes(),
            filename="shot-129.json",
        )
        assert result.shot_id is not None
        shot = await wired.shots.get(result.shot_id)
        assert shot is not None and shot.set_version_id is None


class TestAutoAssignmentRules:
    async def test_a_shot_that_already_has_a_set_is_never_moved(self, wired: Fixtures) -> None:
        """The rule that makes a hand correction stick.

        Auto-assignment runs on ingest, but a re-derive or a second pass must
        not undo a decision somebody made in the UI, so the UPDATE only ever
        fills a NULL.
        """
        first = await wired.sets.create(
            SetWrite(name="One", bean_id=wired.bean_id),
            SetVersionWrite(),
        )
        version = await wired.sets.current_version(first.id)
        assert version is not None
        shot_id = await make_shot(wired.db, "000400")
        await wired.sets.assign_shot(shot_id, version.id)

        second = await wired.sets.create(
            SetWrite(name="Two", bean_id=wired.bean_id),
            SetVersionWrite(),
        )
        assert second.active is True
        assert (
            await wired.sets.auto_assign(
                shot_id,
                profile_version_id=None,
                device_profile_id="",
            )
            is None
        )
        shot = await wired.shots.get(shot_id)
        assert shot is not None and shot.set_version_id == version.id

    async def test_a_set_that_names_no_profile_takes_everything(self, wired: Fixtures) -> None:
        """A Set with no profile is not filtering on one.

        Leaving every shot unassigned under such a Set would make it look broken
        rather than permissive, and the wizard makes naming a profile the easy
        path anyway.
        """
        await wired.sets.create(
            SetWrite(name="Whatever is loaded", bean_id=wired.bean_id),
            SetVersionWrite(),
        )
        shot_id = await make_shot(wired.db, "000401")
        assigned = await wired.sets.auto_assign(
            shot_id,
            profile_version_id=None,
            device_profile_id="anything",
        )
        assert assigned is not None

    async def test_an_archived_set_collects_nothing(self, wired: Fixtures) -> None:
        created = await wired.sets.create(
            SetWrite(name="Finished bag", bean_id=wired.bean_id),
            SetVersionWrite(),
        )
        await wired.sets.archive(created.id)
        shot_id = await make_shot(wired.db, "000402")
        assert (
            await wired.sets.auto_assign(
                shot_id,
                profile_version_id=None,
                device_profile_id="",
            )
            is None
        )


#: The four shapes a real machine will serve that our own bounds refuse.
UNBELIEVABLE = [
    pytest.param({"doseIn": "0"}, id="zero dose"),
    pytest.param({"doseIn": "150"}, id="implausible dose in"),
    pytest.param({"doseOut": "600"}, id="implausible dose out"),
    pytest.param({"grindSetting": "x" * 250}, id="very long grind"),
]


class TestNotesTheFirmwareAcceptsSurviveBothPaths:
    """A note we cannot seed from must not cost the pass it is riding on.

    Both ingest paths are asserted, because the failure looked different in
    each: in the sync engine the ValidationError aborted the rest of the notes
    pass and was swallowed by the shots loop, and in the importer it escaped
    `import_shot` *after* the shot had already been stored — so the caller saw a
    crash for a shot that was, in fact, safely in the archive.
    """

    @pytest.mark.parametrize("bad", UNBELIEVABLE)
    async def test_the_sync_notes_pass_finishes(self, tmp_path: Path, bad: dict[str, str]) -> None:
        device = _device_with_two_shots(with_notes=True)
        # The *first* shot's card is the unreadable one, so a pass that gave up
        # on it would leave the second shot's notes unsynced — which is exactly
        # the symptom this is here to catch.
        device.shots[MATCHING_ID].notes = {"id": f"{MATCHING_ID:06d}", "rating": 0, **bad}
        device.shots[OTHER_ID].notes = default_notes(OTHER_ID)
        device.shots[OTHER_ID].entry.flags |= SHOT_FLAG_HAS_NOTES
        await device.start()
        try:
            async with archive_for(device, tmp_path) as archive:
                run = await archive.engine.sync_shots()
                assert run.status == "ok"

                first = await archive.engine.shots.get_by_device_id("000300")
                second = await archive.engine.shots.get_by_device_id("000301")
                assert first is not None and second is not None

                # The notes themselves are stored verbatim either way: the
                # mirror has no opinion about plausibility, only the judgement
                # does.
                assert await archive.engine.notes.get(first.id) is not None
                assert await archive.engine.notes.get(second.id) is not None
                # And the shot behind the bad card got its judgement seeded from
                # the fields that were believable.
                assert await archive.engine.judgements.get(second.id) is not None
        finally:
            await device.stop()

    @pytest.mark.parametrize("bad", UNBELIEVABLE)
    async def test_the_importer_still_reports_the_shot_it_stored(
        self, wired: Fixtures, bad: dict[str, str]
    ) -> None:
        document = json.loads((FIXTURES / "exports" / "shot-129.json").read_text())
        document["notes"] = {"id": "129", "rating": 4, **bad}

        result = await ImportService(wired.db).import_shot(document, filename="shot-129.json")

        assert result.status == "created"
        assert result.shot_id is not None
        # The rating was believable, so there is still a judgement — just
        # without the field we could not accept.
        judgement = await wired.judgements.get(result.shot_id)
        assert judgement is not None and judgement.rating == 4
