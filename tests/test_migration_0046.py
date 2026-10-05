"""0046 adds the signature tables to a populated archive and keeps everything in it.

A database as the version before this one left it (a Set, a lever shot filed under it) is
migrated, and then booted: the new tables exist and are empty, and every view and the shot's own
routes answer.
"""

from __future__ import annotations

from pathlib import Path

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.models import Profile
from gaggiclanker.settings import EnvSettings
from gaggiclanker.sync.derive import derive_shot
from tests.conftest import running_app
from tests.lever_shot import LEVER_PROFILE, TARGET_YIELD_G, lever_shot
from tests.test_migrations import _migrate_below, _migrate_through


async def _old_archive(db: Database, tmp_path: Path) -> int:
    assert (await _migrate_below(db, tmp_path, "0046"))[-1] == "0045"
    bean = await BeansRepository(db).create(BeanWrite(name="Guji", roast_level="light"))
    grinder = await GrindersRepository(db).create(GrinderWrite(name="Niche", step_unit="numbers"))
    profile, _ = await ProfilesRepository(db).ensure_version(Profile.model_validate(LEVER_PROFILE))
    row = await SetsRepository(db).create(
        SetWrite(name="Guji", bean_id=bean.id, grinder_id=grinder.id),
        SetVersionWrite(
            profile_version_id=profile.id,
            dose_g=18.0,
            target_yield_g=TARGET_YIELD_G,
            grind_setting="14",
        ),
    )
    slog = lever_shot()
    derived = derive_shot(
        slog, slog_to_raw(slog), device_id="000900", source="import", profile=LEVER_PROFILE
    )
    shot = await ShotsRepository(db).insert(derived.shot, derived.samples)
    assert row.current_version_id is not None
    await SetsRepository(db).assign_shot(shot, row.current_version_id)
    await db.execute("UPDATE shots SET profile_version_id = ? WHERE id = ?", (profile.id, shot))
    return shot


async def test_the_migration_adds_empty_tables_and_keeps_the_archive_as_it_was(
    data_dir: Path, tmp_path: Path
) -> None:
    db = Database(data_dir / "old.db")
    await db.connect()
    try:
        shot = await _old_archive(db, tmp_path)
        before = await db.fetch_one("SELECT raw_slog, duration_ms FROM shots WHERE id = ?", (shot,))

        assert await _migrate_through(db, tmp_path, "0046") == ["0046"]

        for table in (
            "profile_signatures",
            "signature_expectations",
            "set_version_signature_overrides",
        ):
            assert await db.fetch_value(f"SELECT COUNT(*) FROM {table}") == 0  # noqa: S608
        after = await db.fetch_one("SELECT raw_slog, duration_ms FROM shots WHERE id = ?", (shot,))
        assert tuple(before or ()) == tuple(after or ())
        assert await db.fetch_value("PRAGMA integrity_check") == "ok"
        assert await db.fetch_all("PRAGMA foreign_key_check") == []
    finally:
        await db.close()


async def test_the_next_boot_answers_every_view_and_the_shot_routes(
    env: EnvSettings, tmp_path: Path
) -> None:
    db = Database(env.database_path)
    await db.connect()
    try:
        shot = await _old_archive(db, tmp_path)
    finally:
        await db.close()

    async with running_app(env) as (app, client):
        for view in ("v_shots", "v_sets", "v_beans", "v_profiles"):
            assert await app.state.db.fetch_all(f"SELECT * FROM {view}") is not None  # noqa: S608
        assert (await client.get("/api/shots")).status_code == 200
        assert (await client.get("/api/shots?sort=review")).status_code == 200
        body = (await client.get(f"/api/shots/{shot}/fields")).json()["data"]
        assert body["shot_id"] == shot
