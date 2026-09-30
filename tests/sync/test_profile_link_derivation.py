"""The profile a shot is linked to is an input of its derived columns.

A shot derived before its profile was known is derived again the moment it is
linked. What is pinned here: each writer of the link leaves the shot to be
derived again (in the same write), the sync and the importer do it right there,
and a shot that arrives with its profile already mirrored is derived with it
the first time.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.imports.service import ImportFile, ImportService
from gaggiclanker.sync import engine as sync_engine
from gaggiclanker.sync.derive import DERIVATION_VERSION, derive_shot, rederive_shots
from tests.domain.helpers import constructed_profile, load_export
from tests.sync.conftest import Archive, archive_for
from tests.sync.profile_helpers import (
    PROFILE_ID_204,
    SLOG_204,
    compliance,
    derivation_version,
    profile_version,
    unlinked_shot,
)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "gaggiclanker.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


async def test_linking_a_shot_to_a_version_puts_it_back_to_be_derived(db: Database) -> None:
    shots = ShotsRepository(db)
    shot_id = await unlinked_shot(db)
    assert await derivation_version(db, shot_id) == DERIVATION_VERSION

    await shots.link_profile_version(shot_id, await profile_version(db))

    # Reset in the link's own write; nothing has re-derived it yet.
    assert await derivation_version(db, shot_id) == 0
    assert await compliance(db, shot_id) is None
    assert await rederive_shots(shots) == (1, 0)
    assert await compliance(db, shot_id) is not None


async def test_linking_by_device_profile_resets_only_the_shots_it_links(db: Database) -> None:
    shots = ShotsRepository(db)
    version_id = await profile_version(db)
    unlinked = await unlinked_shot(db, "000301")
    other = await unlinked_shot(db, "000302")
    already = await unlinked_shot(db, "000303")
    for shot_id, device_profile in ((unlinked, "p1"), (other, "p2"), (already, "p1")):
        await db.execute(
            "UPDATE shots SET profile_id_on_device = ? WHERE id = ?", (device_profile, shot_id)
        )
    await db.execute("UPDATE shots SET profile_version_id = ? WHERE id = ?", (version_id, already))

    assert await shots.link_unlinked_by_device_profile("p1", version_id) == 1

    assert await derivation_version(db, unlinked) == 0
    assert await derivation_version(db, other) == DERIVATION_VERSION
    assert await derivation_version(db, already) == DERIVATION_VERSION


# ── the sync engine ──────────────────────────────────────────────────


def _machine_with_the_profile_and_a_shot() -> FakeDevice:
    """A machine holding shot 204 and, under the id its `.slog` header names, its profile."""
    device = FakeDevice()
    device.profiles = [{**constructed_profile("shot_204"), "id": PROFILE_ID_204}]
    device.add_shot(200, SLOG_204.read_bytes(), timestamp=1_770_000_000)
    return device


async def _shot_row(archive: Archive) -> dict[str, Any]:
    row = await archive.db.fetch_one("SELECT * FROM shots WHERE device_id = '000200'")
    assert row is not None
    return dict(row)


async def test_a_shot_pulled_before_its_profile_is_derived_again_when_the_mirror_links_it(
    tmp_path: Path,
) -> None:
    device = _machine_with_the_profile_and_a_shot()
    await device.start()
    try:
        async with archive_for(device, tmp_path) as archive:
            await archive.engine.sync_shots(trigger="test")
            first = await _shot_row(archive)
            assert first["profile_version_id"] is None
            assert (
                json.loads(first["diagnostics_json"])["diagnostics"]["profile_compliance"] is None
            )

            await archive.engine.sync_profiles(trigger="test")

            second = await _shot_row(archive)
            assert second["profile_version_id"] is not None
            assert second["derivation_version"] == DERIVATION_VERSION
            block = json.loads(second["diagnostics_json"])["diagnostics"]["profile_compliance"]
            assert block["pressure_grading"] == "graded"
            assert block["flow_grading"] == "not_applicable"
            assert second["execution_score"] != first["execution_score"]
    finally:
        await device.stop()


async def test_a_shot_pulled_after_its_profile_is_derived_with_it_the_first_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = _machine_with_the_profile_and_a_shot()
    await device.start()
    rederived: list[bool] = []

    async def counting(shots: ShotsRepository) -> tuple[int, int]:
        rederived.append(True)
        return await rederive_shots(shots)

    monkeypatch.setattr(sync_engine, "rederive_shots", counting)
    try:
        async with archive_for(device, tmp_path) as archive:
            await archive.engine.sync_profiles(trigger="test")
            await archive.engine.sync_shots(trigger="test")

            row = await _shot_row(archive)
            assert row["profile_version_id"] is not None
            assert row["derivation_version"] == DERIVATION_VERSION
            block = json.loads(row["diagnostics_json"])["diagnostics"]["profile_compliance"]
            assert block["pressure_grading"] == "graded"
            # Derived once with the profile in hand: the mirror had nothing to re-link.
            await archive.engine.sync_profiles(trigger="test")
            assert rederived == []
    finally:
        await device.stop()


# ── the importer ─────────────────────────────────────────────────────


async def test_an_imported_shot_linked_to_a_profile_by_label_is_derived_again(
    db: Database,
) -> None:
    """The batch's label link runs after the shot is derived: the link re-derives it."""
    service = ImportService(db)
    profile_129 = constructed_profile("shot_129")
    summary = await service.import_files(
        [
            ImportFile(
                filename="shot-129.json", data=json.dumps(load_export("shot-129.json")).encode()
            ),
            ImportFile(filename="profile.json", data=json.dumps(profile_129).encode()),
        ]
    )
    shot_item = next(item for item in summary.items if item.kind == "shot")
    assert shot_item.profile_version_id is not None, "the label link must have happened"
    assert shot_item.shot_id is not None

    block = await compliance(db, shot_item.shot_id)
    assert block is not None
    assert block["flow_grading"] == "not_applicable"
    assert await derivation_version(db, shot_item.shot_id) == DERIVATION_VERSION
    score = await db.fetch_value(
        "SELECT execution_score FROM shots WHERE id = ?", (shot_item.shot_id,)
    )
    assert score == pytest.approx(9.3)


async def test_an_imported_shot_whose_profile_is_already_stored_is_derived_with_it(
    db: Database,
) -> None:
    """A profile the archive already holds under the export's own profile id."""
    profile = Profile.model_validate(constructed_profile("shot_129"))
    repo = ProfilesRepository(db)
    version, _ = await repo.ensure_version(profile)
    document = load_export("shot-129.json")
    await repo.upsert_device_profile(device_id=document["profileId"], version_id=version.id)

    summary = await ImportService(db).import_files(
        [ImportFile(filename="shot-129.json", data=json.dumps(document).encode())]
    )

    shot_id = summary.items[0].shot_id
    assert shot_id is not None
    block = await compliance(db, shot_id)
    assert block is not None and block["pressure_grading"] == "graded"
    assert await derivation_version(db, shot_id) == DERIVATION_VERSION


async def test_a_replace_that_finds_no_profile_keeps_the_link_and_derives_with_it(
    db: Database,
) -> None:
    """`replace` keeps a shot's link when the export finds no profile: so must the derive."""
    # Labelled otherwise, so the batch's label guess does not link the first import.
    profile = Profile.model_validate({**constructed_profile("shot_129"), "label": "Not its name"})
    repo = ProfilesRepository(db)
    version, _ = await repo.ensure_version(profile)
    document = load_export("shot-129.json")
    service = ImportService(db)
    first = await service.import_files(
        [ImportFile(filename="shot-129.json", data=json.dumps(document).encode())]
    )
    shot_id = first.items[0].shot_id
    assert shot_id is not None
    assert await compliance(db, shot_id) is None
    await ShotsRepository(db).link_profile_version(shot_id, version.id)
    await rederive_shots(ShotsRepository(db))

    await service.import_files(
        [ImportFile(filename="shot-129.json", data=json.dumps(document).encode())], replace=True
    )

    block = await compliance(db, shot_id)
    assert block is not None and block["pressure_grading"] == "graded"


async def test_the_profile_id_the_sync_resolves_is_the_one_derive_stores() -> None:
    """The engine looks the profile up by the header's id before deriving: derive must agree."""
    from gaggiclanker.domain.models import IndexEntry

    raw = SLOG_204.read_bytes()
    slog = parse_slog(raw)
    entry = IndexEntry(
        id=1,
        timestamp=1_770_000_000,
        duration_ms=28_000,
        volume_g=36.0,
        rating=0,
        flags=0,
        profile_id="from-the-index",
        profile_name="x",
        avg_temp_c=93.0,
        max_pressure_bar=9.0,
        avg_flow_ml_s=1.8,
    )
    for given in (None, entry):
        stored = derive_shot(slog, raw, device_id="000001", entry=given).shot.profile_id_on_device
        assert stored == slog.header.profile_id
