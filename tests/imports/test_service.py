"""Importing export files into the archive.

The chunk's acceptance, in order: the two real files land as one shot with 213
samples, four phases, diagnostics and a score, plus one profile version labelled
"Cremina v2"; a second import of the same shot is a no-op; `replace` updates it;
a malformed file is reported on its own without costing the batch; and a shot we
cannot read is kept anyway, because the machine's copy is gone.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.machines import MachinesRepository, MachineUpsert
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.imports import service as import_service
from gaggiclanker.imports.service import IMPORT_MACHINE_HOST, ImportFile, ImportService
from gaggiclanker.infra.errors import NotFound
from gaggiclanker.settings_service import SettingsService
from tests.domain.helpers import load_export
from tests.imports.helpers import PROFILE_ARRAY_EXPORT, V7_EXPORT, fixture_bytes

SHOT_FIXTURE = "shot-129.json"
PROFILE_FIXTURE = "profile-dCs4AOOcBn.json"
SHOT_129_SAMPLES = 213
SHOT_129_PHASES = 4


def files(*names: str) -> list[ImportFile]:
    return [ImportFile(filename=name, data=fixture_bytes(name)) for name in names]


def document(name: str) -> Any:
    return load_export(name)


def filler(size: int = 3000) -> bytes:
    """Valid JSON that is not an export, and compresses to almost nothing.

    The shape a zip bomb has: a few bytes on disk, a lot of bytes once expanded.
    """
    return json.dumps({"padding": "x" * size}).encode()


def zipped(entries: dict[str, bytes], name: str = "exports.zip") -> ImportFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for entry, data in entries.items():
            archive.writestr(entry, data)
    return ImportFile(filename=name, data=buffer.getvalue())


# ── the acceptance ───────────────────────────────────────────────────


async def test_the_two_real_exports_land_as_a_shot_and_a_profile(
    service: ImportService, db: Database
) -> None:
    summary = await service.import_files(files(SHOT_FIXTURE, PROFILE_FIXTURE))

    assert summary.created == 2
    assert summary.failed == 0

    shot_result = next(item for item in summary.items if item.kind == "shot")
    profile_result = next(item for item in summary.items if item.kind == "profile")
    assert shot_result.device_id == "000129"
    assert profile_result.label == "Cremina v2"

    shots = ShotsRepository(db)
    assert shot_result.shot_id is not None
    shot = await shots.get(shot_result.shot_id)
    assert shot is not None
    assert shot.source == "import"
    assert shot.sample_count == SHOT_129_SAMPLES
    assert len(await shots.samples(shot.id)) == SHOT_129_SAMPLES
    assert shot.phases is not None
    assert len(shot.phases) == SHOT_129_PHASES
    assert shot.diagnostics is not None
    assert shot.execution_score == pytest.approx(7.7)
    assert shot.execution_reason
    assert shot.quarantined is False
    assert shot.incomplete is False
    # The bytes are the product: an imported shot carries a real `.slog` too.
    raw = await shots.raw_slog(shot.id)
    assert raw is not None and raw[:4] == b"SHOT"


async def test_the_exports_notes_become_the_device_notes(
    service: ImportService, db: Database
) -> None:
    summary = await service.import_files(files(SHOT_FIXTURE))
    shot_id = summary.items[0].shot_id
    assert shot_id is not None

    notes = await NotesRepository(db).get(shot_id)
    assert notes is not None
    assert notes.dose_out_g == pytest.approx(32.1)
    assert notes.balance_taste == "balanced"
    assert notes.document is not None
    assert notes.document["id"] == "129"


async def test_importing_the_same_shot_again_is_skipped(service: ImportService) -> None:
    first = await service.import_files(files(SHOT_FIXTURE))
    second = await service.import_files(files(SHOT_FIXTURE))

    assert first.created == 1
    assert second.skipped == 1
    assert second.created == 0
    assert second.items[0].shot_id == first.items[0].shot_id
    assert "replace" in second.items[0].message


async def test_replace_updates_the_shot_in_place(service: ImportService, db: Database) -> None:
    first = await service.import_files(files(SHOT_FIXTURE))
    shot_id = first.items[0].shot_id
    assert shot_id is not None

    second = await service.import_files(files(SHOT_FIXTURE), replace=True)

    assert second.updated == 1
    assert second.items[0].shot_id == shot_id
    shots = ShotsRepository(db)
    # One row, one curve: a replace must not double the samples.
    assert len(await shots.samples(shot_id)) == SHOT_129_SAMPLES
    page = await shots.list_shots()
    assert page.total == 1


async def test_replace_keeps_what_the_machine_and_the_user_said(
    service: ImportService, db: Database
) -> None:
    """The bytes are overwritten; the device's own view of the shot is not.

    A shot pulled from the machine carries index figures no export has — the
    rating typed on the display, the dose entered in its notes card, the deleted
    flag. Re-importing a saved copy of the same shot must not erase them.
    """
    machine = await MachinesRepository(db).upsert(MachineUpsert(host="gaggimate.local"))
    shots = ShotsRepository(db)
    existing = await shots.insert(
        ShotInsert(
            device_id="000129",
            machine_id=machine.id,
            raw_slog=b"not a slog",
            quarantined=True,
            quarantine_reason="the device served HTML",
            index_rating=4,
            index_avg_temp_c=93.2,
            deleted_on_device=True,
        )
    )

    summary = await service.import_files(files(SHOT_FIXTURE), machine_id=machine.id, replace=True)

    assert summary.updated == 1
    shot = await shots.get(existing)
    assert shot is not None
    assert shot.id == existing
    assert shot.sample_count == SHOT_129_SAMPLES
    assert shot.quarantined is False
    assert shot.quarantine_reason is None
    assert shot.source == "import"
    # Untouched by the re-derive:
    assert shot.index_rating == 4
    assert shot.index_avg_temp_c == pytest.approx(93.2)
    assert shot.deleted_on_device is True


async def test_the_firmware_shaped_profile_imports_as_the_same_version(
    service: ImportService,
) -> None:
    """`id` + `transition.target` from the firmware, and it is not a new version."""
    first = await service.import_files(files(PROFILE_FIXTURE))
    second = await service.import_files(files(PROFILE_ARRAY_EXPORT))

    assert first.created == 1
    # Two profiles in the array: the Cremina is the one we already have.
    assert [item.status for item in second.items] == ["skipped", "created"]
    assert second.items[0].profile_version_id == first.items[0].profile_version_id
    assert second.items[1].label == "9 Bar Espresso"


async def test_a_v7_export_imports_with_its_extra_fields(
    service: ImportService, db: Database
) -> None:
    summary = await service.import_files(files(V7_EXPORT))
    shot_id = summary.items[0].shot_id
    assert shot_id is not None

    shot = await ShotsRepository(db).get(shot_id)
    assert shot is not None
    assert shot.slog_version == 7
    assert shot.final_exit_reason == 1
    assert shot.brew_delay_ms == 900
    samples = await ShotsRepository(db).samples(shot_id)
    assert all(sample.wp is not None for sample in samples)
    notes = await NotesRepository(db).get(shot_id)
    assert notes is not None
    assert notes.rating == 4


# ── files that are wrong ─────────────────────────────────────────────


async def test_a_malformed_file_is_reported_without_aborting_the_batch(
    service: ImportService, db: Database
) -> None:
    batch = [
        ImportFile(filename="broken.json", data=b"{not json"),
        *files(SHOT_FIXTURE),
        ImportFile(filename="notes.txt", data=b"just a note to myself"),
        *files(PROFILE_FIXTURE),
    ]

    summary = await service.import_files(batch)

    assert summary.created == 2
    assert summary.failed == 2
    assert [item.status for item in summary.items] == [
        "failed",
        "created",
        "failed",
        "created",
    ]
    assert "not JSON" in summary.items[0].message
    assert await ShotsRepository(db).counts()


async def test_an_unreadable_shot_is_quarantined_with_its_bytes(
    service: ImportService, db: Database
) -> None:
    """The archive's central rule through the import door: never lose a shot.

    The machine deleted its copy long ago. A file that does not validate today
    may validate after the next parser fix, and the bytes are the only thing
    that makes that possible.
    """
    broken = document(SHOT_FIXTURE)
    broken["samples"][17]["cp"] = "wildly not a number"
    raw = json.dumps(broken).encode()

    summary = await service.import_files([ImportFile(filename="shot-129.json", data=raw)])

    [result] = summary.items
    assert result.status == "created"
    assert result.quarantined is True
    assert result.device_id == "000129"
    assert "validation error" in result.message

    assert result.shot_id is not None
    shots = ShotsRepository(db)
    shot = await shots.get(result.shot_id)
    assert shot is not None
    assert shot.quarantined is True
    assert shot.quarantine_reason is not None
    assert shot.sample_count == 0
    assert await shots.samples(result.shot_id) == []
    assert await shots.raw_slog(result.shot_id) == raw


async def test_a_broken_shot_with_no_id_cannot_be_quarantined(service: ImportService) -> None:
    """Nothing to key the row on, and nothing to stop it landing a hundred times."""
    broken = document(SHOT_FIXTURE)
    broken["id"] = ""
    broken["samples"][0]["cp"] = "nope"

    summary = await service.import_files(
        [ImportFile(filename="x.json", data=json.dumps(broken).encode())]
    )

    assert summary.failed == 1
    assert summary.items[0].quarantined is False


async def test_a_quarantined_shot_does_not_import_twice(service: ImportService) -> None:
    broken = document(SHOT_FIXTURE)
    broken["samples"][0]["cp"] = "nope"
    payload = [ImportFile(filename="x.json", data=json.dumps(broken).encode())]

    assert (await service.import_files(payload)).created == 1
    second = await service.import_files(payload)
    assert second.skipped == 1


async def test_a_file_that_is_neither_is_reported_as_neither(service: ImportService) -> None:
    summary = await service.import_files(
        [ImportFile(filename="settings.json", data=b'{"gaggimateHost": "kitchen.local"}')]
    )

    assert summary.failed == 1
    assert summary.items[0].kind == "unknown"
    assert "not a GaggiMate export" in summary.items[0].message


async def test_a_profile_the_safety_model_refuses_fails_alone(service: ImportService) -> None:
    """One bad profile in an array does not take the good one with it."""
    array = json.loads(fixture_bytes(PROFILE_ARRAY_EXPORT))
    array[0]["phases"][0]["pump"] = 100.0

    summary = await service.import_files(
        [ImportFile(filename="profiles.json", data=json.dumps(array).encode())]
    )

    # The array validates as a whole (it is one document), so the whole document
    # fails — but it fails as one reported file, not as an exception.
    assert summary.failed == 1
    assert summary.items[0].kind == "profile"
    assert "pump" in summary.items[0].message


# ── zips ─────────────────────────────────────────────────────────────


async def test_a_zip_is_expanded_and_each_entry_reported(service: ImportService) -> None:
    archive = zipped(
        {
            "exports/shot-129.json": fixture_bytes(SHOT_FIXTURE),
            "exports/profile.json": fixture_bytes(PROFILE_FIXTURE),
            "exports/broken.json": b"{{{",
            # Archiver noise, silently skipped.
            "__MACOSX/._shot-129.json": b"\x00\x05\x16\x07",
            "exports/": b"",
        }
    )

    summary = await service.import_files([archive])

    assert summary.created == 2
    assert summary.failed == 1
    assert all(item.filename.startswith("exports.zip:") for item in summary.items)


async def test_an_empty_zip_says_so(service: ImportService) -> None:
    summary = await service.import_files([zipped({})])

    assert summary.skipped == 1
    assert "no files" in summary.items[0].message


async def test_archiver_noise_is_skipped_at_any_depth(service: ImportService) -> None:
    """A dotfile in a subdirectory is as much noise as one at the root.

    Reporting `exports/.DS_Store` as a failed file would bury the real results
    under the bookkeeping of whichever tool made the zip.
    """
    archive = zipped(
        {
            "exports/shot-129.json": fixture_bytes(SHOT_FIXTURE),
            "exports/.DS_Store": b"\x00\x05\x16\x07",
            "exports/__MACOSX/._shot-129.json": b"\x00\x05\x16\x07",
            ".hidden/shot.json": b"{}",
        }
    )

    summary = await service.import_files([archive])

    assert [item.filename for item in summary.items] == ["exports.zip:exports/shot-129.json"]


async def test_a_zip_bomb_is_refused_before_it_is_expanded(
    service: ImportService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A few kilobytes of zeros expand to gigabytes, on the event loop.

    The budget is for the whole batch — every file, every level of nesting —
    because a bomb spread over a hundred entries works just as well as one
    concentrated in a single entry, which a per-entry cap cannot see.
    """
    monkeypatch.setattr(import_service, "MAX_EXPANDED_BYTES", 4096)
    archive = zipped({f"filler-{i}.json": filler() for i in range(4)})

    summary = await service.import_files([archive])

    # The first entry fits in the lowered budget, the rest do not — and not one
    # of them was decompressed past the point where it stopped fitting.
    assert [item.status for item in summary.items] == ["failed", "failed", "failed", "failed"]
    assert "decompression limit" in summary.items[1].message
    assert sum(1 for item in summary.items if "not a GaggiMate export" in item.message) == 1


async def test_the_budget_is_spent_across_files_not_per_file(
    service: ImportService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(import_service, "MAX_EXPANDED_BYTES", 5000)
    first = zipped({"filler.json": filler()}, name="first.zip")
    second = zipped({"filler.json": filler()}, name="second.zip")

    summary = await service.import_files([first, second])

    # The first zip's entry was expanded (and rejected as not an export); the
    # second one never got the chance.
    assert "not a GaggiMate export" in summary.items[0].message
    assert "decompression limit" in summary.items[1].message


async def test_a_zip_nested_too_deep_is_refused_not_followed(service: ImportService) -> None:
    inner = zipped({"shot-129.json": fixture_bytes(SHOT_FIXTURE)}, name="inner.zip")
    middle = zipped({"inner.zip": inner.data}, name="middle.zip")
    outer = zipped({"middle.zip": middle.data})

    summary = await service.import_files([outer])

    assert summary.failed == 1
    assert "nested" in summary.items[0].message


# ── machines ─────────────────────────────────────────────────────────


async def test_with_no_machine_configured_imports_get_a_synthetic_one(
    service: ImportService, db: Database
) -> None:
    summary = await service.import_files(files(SHOT_FIXTURE))

    machine = await MachinesRepository(db).get(summary.machine_id)
    assert machine is not None
    assert machine.host == IMPORT_MACHINE_HOST


async def test_the_configured_machine_wins_when_we_have_seen_it(db: Database) -> None:
    """An export of a shot from *this* machine belongs beside its other shots."""
    machine = await MachinesRepository(db).upsert(MachineUpsert(host="gaggimate.local"))
    settings = SettingsService(SettingsRepository(db))
    await settings.apply({"gaggimateHost": "gaggimate.local"})

    summary = await ImportService(db, settings).import_files(files(SHOT_FIXTURE))

    assert summary.machine_id == machine.id


async def test_an_explicit_machine_id_overrides_everything(db: Database) -> None:
    machines = MachinesRepository(db)
    await machines.upsert(MachineUpsert(host="gaggimate.local"))
    other = await machines.upsert(MachineUpsert(host="import:2024-archive"))
    settings = SettingsService(SettingsRepository(db))
    await settings.apply({"gaggimateHost": "gaggimate.local"})

    summary = await ImportService(db, settings).import_files(
        files(SHOT_FIXTURE), machine_id=other.id
    )

    assert summary.machine_id == other.id


async def test_importing_onto_a_machine_that_does_not_exist_is_an_error(
    service: ImportService,
) -> None:
    """The one failure worth losing the batch over: the caller's own mistake."""
    with pytest.raises(NotFound):
        await service.import_files(files(SHOT_FIXTURE), machine_id=404)


async def test_the_same_shot_on_two_machines_is_two_shots(db: Database) -> None:
    """De-duplication is per machine, because shot ids are per machine."""
    machines = MachinesRepository(db)
    first = await machines.upsert(MachineUpsert(host="kitchen.local"))
    second = await machines.upsert(MachineUpsert(host="import:old-box"))
    service = ImportService(db)

    await service.import_files(files(SHOT_FIXTURE), machine_id=first.id)
    await service.import_files(files(SHOT_FIXTURE), machine_id=second.id)

    assert (await ShotsRepository(db).counts()).total == 2


async def test_a_shot_links_to_the_profile_version_when_the_mirror_has_it(
    db: Database, tmp_path: Path
) -> None:
    """The `.slog` header carries a profile *id*; the mirror maps it to content."""
    from gaggiclanker.db.repos.profiles import ProfilesRepository
    from gaggiclanker.domain.exports import profile_export_to_profiles

    machine = await MachinesRepository(db).upsert(MachineUpsert(host="kitchen.local"))
    profiles = ProfilesRepository(db)
    [profile] = profile_export_to_profiles(document(PROFILE_FIXTURE))
    version, _ = await profiles.ensure_version(profile)
    await profiles.upsert_device_profile(
        machine_id=machine.id, device_id="rV4GhUcSZc", version_id=version.id
    )

    summary = await ImportService(db).import_files(files(SHOT_FIXTURE), machine_id=machine.id)

    shot = await ShotsRepository(db).get(summary.items[0].shot_id or 0)
    assert shot is not None
    assert shot.profile_version_id == version.id
    assert shot.profile_label == "Cremina v2"
