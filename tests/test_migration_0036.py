"""0036 gives every profile its own on-the-machine switch and version list, and the list is
filled from everything the archive stores: each rule of how versions are grouped is a test here."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations
from gaggiclanker.db.repos.profile_list import (
    ProfileListBuilder,
    stripped_label,
    version_source_for_draft,
)


@pytest.fixture
async def db(data_dir: Path) -> AsyncIterator[Database]:
    database = Database(data_dir / "test.db")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


async def _below(db: Database, tmp_path: Path) -> None:
    directory = tmp_path / "below-0036"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "0036":
            shutil.copy(path, directory / path.name)
    await run_migrations(db, directory)


async def _version(
    db: Database, vid: int, label: str, *, source: str = "device", utility: int = 0
) -> None:
    await db.execute(
        "INSERT INTO profile_versions (id, content_hash, label, type, utility, json, source, "
        "created_at) VALUES (?, ?, ?, 'pro', ?, '{}', ?, ?)",
        (vid, f"h{vid}", label, utility, source, f"2026-01-{vid:02d}T00:00:00Z"),
    )


async def _draft(
    db: Database,
    did: int,
    *,
    base: int,
    version: int,
    status: str,
    pushed: str | None = None,
    replaced: int | None = None,
    set_id: int | None = None,
) -> None:
    await db.execute(
        "INSERT INTO profile_drafts (id, base_version_id, draft_version_id, status, "
        "pushed_device_profile_id, replaced_version_id, set_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (did, base, version, status, pushed, replaced, set_id),
    )


async def _row(
    db: Database,
    rid: int,
    label: str,
    current: int,
    *,
    previous: int | None = None,
    device: str | None = None,
    device_version: int | None = None,
    deleted: bool = False,
    origin: str = "draft",
    star: int = 1,
) -> None:
    await db.execute(
        "INSERT INTO profile_board (id, label, current_version_id, previous_version_id, "
        "device_profile_id, device_version_id, origin, on_home_screen, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            rid,
            label,
            current,
            previous,
            device,
            device_version,
            origin,
            star,
            "2026-02-01T00:00:00Z" if deleted else None,
        ),
    )


async def _seed(db: Database) -> None:
    await _version(db, 1, "Alpha", source="import")
    await _version(db, 2, "Alpha [AI]", source="draft")
    await _version(db, 3, "Alpha [AI]", source="draft")
    await _version(db, 4, "Beta")
    await _version(db, 5, "Gamma [AI]", source="draft")
    await _version(db, 6, "Gamma", source="import")
    await _version(db, 7, "Empty baseline", source="draft")
    await _version(db, 8, "Flush", utility=1)
    await _version(db, 9, "Delta")
    await _version(db, 10, "Delta [AI]", source="draft")
    await _version(db, 11, "Beta")
    await _version(db, 12, "Open proposal", source="draft")
    await _version(db, 13, "Epsilon [AI]", source="draft")
    await _draft(db, 1, base=1, version=2, status="superseded")
    await _draft(db, 2, base=2, version=3, status="pushed", pushed="fa", replaced=2)
    await _draft(db, 3, base=1, version=5, status="discarded")
    await _draft(db, 4, base=4, version=12, status="draft")
    await _draft(db, 5, base=9, version=10, status="pushed", pushed="fd")
    # A draft nobody closed and nothing else names: an open proposal, not a version yet.
    await _draft(db, 6, base=4, version=13, status="approved")
    await _row(db, 1, "Alpha [AI]", 3, previous=2, device="fa", device_version=3)
    await _row(db, 2, "Beta", 4, device="fb", device_version=4, origin="adopted", star=0)
    # Tombstones: one for a label a live row has, one for a label no live row has.
    await _row(db, 3, "Beta", 11, deleted=True)
    await _row(db, 4, "Delta [AI]", 10, device="fd", device_version=10, deleted=True)


async def _profiles(db: Database) -> dict[str, dict[str, object]]:
    found: dict[str, dict[str, object]] = {}
    for row in await db.fetch_all("SELECT * FROM profile_board ORDER BY id"):
        versions = await db.fetch_all(
            "SELECT version_id, source FROM profile_board_versions WHERE board_id = ? "
            "ORDER BY version_id",
            (row["id"],),
        )
        found[f"{row['id']}"] = {
            "label": row["label"],
            "current": row["current_version_id"],
            "on": row["on_machine"],
            "star": row["on_home_screen"],
            "deleted": row["deleted_at"] is not None,
            "versions": [(v["version_id"], v["source"]) for v in versions],
        }
    return found


async def test_the_list_is_built_from_everything_stored(db: Database, tmp_path: Path) -> None:
    await _below(db, tmp_path)
    await _seed(db)
    assert "0036" in await run_migrations(db)

    counts = await ProfileListBuilder(db).build()
    assert counts is not None and counts["profiles_made"] == 1

    got = await _profiles(db)
    # A live row keeps its switch on and its star; its versions are the ones it names, the
    # drafts that were put on its file or replaced one of its versions, and the stored
    # version that carries its name without the app's suffix.
    assert got["1"] == {
        "label": "Alpha [AI]",
        "current": 3,
        "on": 1,
        "star": 1,
        "deleted": False,
        "versions": [(1, "import"), (2, "edit"), (3, "edit")],
    }
    # A tombstone with the label of a live row is merged into it and stays a tombstone.
    assert got["2"]["versions"] == [(4, "machine"), (11, "machine")]
    assert got["2"]["on"] == 1 and got["2"]["star"] == 0
    assert got["3"]["deleted"] is True and got["3"]["versions"] == []
    # A tombstone with no live twin comes back off, with its file still named for the sync.
    assert got["4"]["deleted"] is False and got["4"]["on"] == 0
    assert got["4"]["versions"] == [(9, "machine"), (10, "edit")]
    file = await db.fetch_value("SELECT device_profile_id FROM profile_board WHERE id = 4")
    assert file == "fd"
    # What no row names is grouped by label without the suffix, into a new profile that is off,
    # starred, and has its newest version active.
    new = [p for k, p in got.items() if k not in {"1", "2", "3", "4"}]
    gamma = next(p for p in new if str(p["label"]).startswith("Gamma"))
    assert gamma == {
        "label": "Gamma",
        "current": 6,
        "on": 0,
        "star": 1,
        "deleted": False,
        "versions": [(5, "edit"), (6, "import")],
    }
    # Never a profile or a version of one: the synthetic base, a utility profile, an open
    # proposal's version.
    listed = {v for p in got.values() for v, _ in p["versions"]}  # type: ignore[attr-defined]
    assert listed.isdisjoint({7, 8, 12, 13})
    assert await db.fetch_all("PRAGMA foreign_key_check") == []


async def test_building_twice_changes_nothing(db: Database, tmp_path: Path) -> None:
    await _below(db, tmp_path)
    await _seed(db)
    await run_migrations(db)
    builder = ProfileListBuilder(db)
    assert await builder.build() is not None
    first = await _profiles(db)
    # A person's later choice survives a second boot.
    await db.execute("UPDATE profile_board SET on_machine = 1 WHERE id = 4")
    assert await builder.build() is None
    second = await _profiles(db)
    assert {k: v for k, v in second.items() if k != "4"} == {
        k: v for k, v in first.items() if k != "4"
    }
    assert second["4"]["on"] == 1


async def test_a_tombstone_whose_file_a_live_row_stands_on_lets_go_of_it(
    db: Database, tmp_path: Path
) -> None:
    await _below(db, tmp_path)
    await _version(db, 1, "One [AI]", source="draft")
    await _version(db, 2, "Two [AI]", source="draft")
    await _row(db, 1, "One [AI]", 1, device="same")
    await _row(db, 2, "Two [AI]", 2, device="same", deleted=True)
    await run_migrations(db)
    await ProfileListBuilder(db).build()
    row = await db.fetch_one("SELECT * FROM profile_board WHERE id = 2")
    assert row is not None
    assert row["deleted_at"] is None and row["on_machine"] == 0
    assert row["device_profile_id"] is None and row["device_version_id"] is None


async def test_an_empty_archive_is_built_and_marked(db: Database) -> None:
    await run_migrations(db)
    counts = await ProfileListBuilder(db).build()
    assert counts is not None and counts["profiles_made"] == 0
    assert await ProfileListBuilder(db).built()


async def test_existing_rows_start_on_and_lose_nothing(db: Database, tmp_path: Path) -> None:
    await _below(db, tmp_path)
    await _version(db, 1, "P")
    await _row(db, 1, "P", 1, device="x", device_version=1, star=0)
    assert "0036" in await run_migrations(db)
    row = await db.fetch_one("SELECT * FROM profile_board")
    assert row is not None and row["on_machine"] == 1 and row["on_home_screen"] == 0
    assert row["device_profile_id"] == "x"


def test_the_suffix_is_not_part_of_a_profiles_name() -> None:
    assert stripped_label("Cremina Lever [AI]") == "Cremina Lever"
    assert stripped_label("Cremina Lever") == "Cremina Lever"
    assert stripped_label("[AI] Lever") == "[AI] Lever"


def test_a_draft_with_nothing_an_agent_adds_is_a_persons_edit() -> None:
    assert version_source_for_draft({"change_summary": "manual"}) == "edit"
    assert version_source_for_draft({"set_id": 3}) == "agent"
    assert version_source_for_draft({"prediction": "more body"}) == "agent"
    assert version_source_for_draft({"change_summary": "Raised the pressure."}) == "agent"
