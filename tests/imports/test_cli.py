"""`gaggiclanker import` — the same importer without a browser.

What matters here is the wiring, not the importing: the command opens the
configured database, runs migrations itself, walks directories, and comes back
with an exit status a seeding script can branch on. The import behaviour itself
is `test_service.py`'s subject and is not repeated.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gaggiclanker.__main__ import main
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.imports.cli import collect_files, run_import
from gaggiclanker.settings import EnvSettings
from tests.imports.helpers import EXPORT_FIXTURES

SHOT_129_SAMPLES = 213


async def open_db(env: EnvSettings) -> Database:
    db = Database(env.database_path)
    await db.connect()
    return db


async def test_importing_the_fixture_directory_seeds_a_database(env: EnvSettings) -> None:
    """The one command the front end needs to fill a development database."""
    summary = await run_import([EXPORT_FIXTURES], env=env)

    assert summary.failed == 0
    assert summary.created >= 2

    db = await open_db(env)
    try:
        shots = ShotsRepository(db)
        counts = await shots.counts()
        assert counts.total == 2  # shot-129 and the synthetic v7 one
        assert counts.quarantined == 0
        assert counts.samples > SHOT_129_SAMPLES
    finally:
        await db.close()


async def test_a_second_run_over_the_same_directory_changes_nothing(env: EnvSettings) -> None:
    first = await run_import([EXPORT_FIXTURES], env=env)
    second = await run_import([EXPORT_FIXTURES], env=env)

    assert second.created == 0
    assert second.skipped == len(first.items)
    assert second.failed == 0


async def test_replace_re_derives_without_duplicating(env: EnvSettings) -> None:
    await run_import([EXPORT_FIXTURES], env=env)
    summary = await run_import([EXPORT_FIXTURES], replace=True, env=env)

    assert summary.updated == 2  # the two shots; the profiles are content-hashed
    db = await open_db(env)
    try:
        assert (await ShotsRepository(db).counts()).total == 2
    finally:
        await db.close()


async def test_a_single_file_and_a_missing_path_are_both_reported(
    env: EnvSettings, tmp_path: Path
) -> None:
    summary = await run_import(
        [EXPORT_FIXTURES / "shot-129.json", tmp_path / "nowhere.json"], env=env
    )

    assert summary.created == 1
    assert summary.failed == 1
    assert summary.items[0].message == "no such file"


def test_the_directory_walk_takes_json_and_zips_and_leaves_the_rest(tmp_path: Path) -> None:
    shutil.copy(EXPORT_FIXTURES / "shot-129.json", tmp_path / "shot-129.json")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "bundle.zip").write_bytes(b"PK\x03\x04")
    (tmp_path / "README.md").write_text("exports from the machine")
    # A `.slog` belongs to the sync engine's path, not this one.
    (tmp_path / "000129.slog").write_bytes(b"SHOT")

    found, problems = collect_files([tmp_path])

    # Sorted, so a batch imports in a predictable order and a rerun reports the
    # same files in the same places.
    assert [path.name for path in found] == ["bundle.zip", "shot-129.json"]
    assert problems == []


def test_the_command_prints_a_line_per_file_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    status = main(["import", str(EXPORT_FIXTURES / "shot-129.json")])
    output = capsys.readouterr().out

    assert status == 0
    assert "created  000129" in output
    assert "213 samples" in output
    assert "1 created" in output


def test_the_command_exits_non_zero_when_a_file_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """So a seeding script in CI notices rather than carrying on with half an archive."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")

    status = main(["import", str(broken)])

    assert status == 1
    assert "failed" in capsys.readouterr().out


def test_a_bare_invocation_still_means_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container's CMD is `gaggiclanker`; adding a subcommand must not move it."""
    served: list[bool] = []
    monkeypatch.setattr("gaggiclanker.__main__.serve", lambda: served.append(True))

    assert main([]) == 0
    assert served == [True]
