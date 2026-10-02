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
    is_new: bool = False,
) -> None:
    await db.execute(
        "INSERT INTO profile_drafts (id, base_version_id, draft_version_id, status, "
        "pushed_device_profile_id, replaced_version_id, set_id, is_new) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (did, base, version, status, pushed, replaced, set_id, int(is_new)),
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


async def _assert_no_shared_labels(db: Database) -> None:
    """Two live profiles never share a label, whatever the fill did."""
    labels = [
        r["label"]
        for r in await db.fetch_all("SELECT label FROM profile_board WHERE deleted_at IS NULL")
    ]
    assert len(labels) == len(set(labels)), labels


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
    assert counts is not None and counts["profiles_made"] == 4

    got = await _profiles(db)
    # A live row keeps its switch on and its star; its versions are the ones it names and the
    # drafts that continue it (the rule a put uses): "Alpha [AI]" (v2) is a fork of the import
    # "Alpha" (v1), whose name differs by the suffix, so v1 is NOT absorbed into it.
    assert got["1"] == {
        "label": "Alpha [AI]",
        "current": 3,
        "on": 1,
        "star": 1,
        "deleted": False,
        "versions": [(2, "edit"), (3, "edit")],
    }
    # A tombstone with the label of a live row is merged into it and stays a tombstone.
    assert got["2"]["versions"] == [(4, "machine"), (11, "machine")]
    assert got["2"]["on"] == 1 and got["2"]["star"] == 0
    assert got["3"]["deleted"] is True and got["3"]["versions"] == []
    # A tombstone with no live twin comes back off, with its file still named for the sync.
    assert got["4"]["deleted"] is False and got["4"]["on"] == 0
    assert got["4"]["versions"] == [(10, "edit")]
    file = await db.fetch_value("SELECT device_profile_id FROM profile_board WHERE id = 4")
    assert file == "fd"
    # What no row or draft chain names is a profile of its own by exact label, off, starred,
    # with its newest version active; the suffix is never stripped.
    new = {str(p["label"]): p for p in got.values() if int(p["current"]) in (1, 5, 6, 9)}  # type: ignore[call-overload]
    assert set(new) == {"Alpha", "Gamma [AI]", "Gamma", "Delta"}
    assert new["Gamma [AI]"] == {
        "label": "Gamma [AI]",
        "current": 5,
        "on": 0,
        "star": 1,
        "deleted": False,
        "versions": [(5, "edit")],
    }
    assert new["Gamma"]["versions"] == [(6, "import")] and new["Alpha"]["versions"] == [
        (1, "import")
    ]
    # Never a profile or a version of one: the synthetic base, a utility profile, an open
    # proposal's version.
    listed = {v for p in got.values() for v, _ in p["versions"]}  # type: ignore[attr-defined]
    assert listed.isdisjoint({7, 8, 12, 13})
    assert await db.fetch_all("PRAGMA foreign_key_check") == []
    await _assert_no_shared_labels(db)


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


async def test_agent_work_stays_with_the_profile_a_put_would_have_put_it_in(
    db: Database, tmp_path: Path
) -> None:
    """The fill replays old drafts through the put's own rule, never by label without the suffix.

    A firmware "Turbo Shot" keeps its own versions; "Turbo Shot [AI]" versions made for a Set and
    the version that renamed it for that Set are one profile; a chain of "Blooming [AI]" drafts
    joins the board row it ended up on; a fork of an imported profile is not merged into it.
    """
    await _below(db, tmp_path)
    for vid, label, source in (
        (1, "Turbo Shot", "device"),
        (2, "Turbo Shot [AI]", "draft"),
        (3, "Turbo Shot [AI]", "draft"),
        (4, "Ethiopia Turbo [AI]", "draft"),
        (5, "Blooming", "device"),
        (6, "Blooming [AI]", "draft"),
        (7, "Blooming [AI]", "draft"),
        (8, "Blooming [AI]", "draft"),
        (9, "Lever", "import"),
        (10, "Lever [AI]", "draft"),
        (11, "Mine [AI]", "device"),
        (12, "Mine [AI]", "draft"),
    ):
        await _version(db, vid, label, source=source)
    await _draft(db, 1, base=1, version=2, status="superseded", set_id=1)
    await _draft(db, 2, base=2, version=3, status="failed", set_id=1)
    await _draft(db, 3, base=2, version=4, status="pushed", pushed="pt", replaced=2, set_id=1)
    await _draft(db, 4, base=5, version=6, status="superseded")
    await _draft(db, 5, base=6, version=7, status="discarded")
    await _draft(db, 6, base=7, version=8, status="pushed", pushed="pb", replaced=7)
    await _draft(db, 7, base=9, version=10, status="discarded")
    await _draft(db, 8, base=11, version=12, status="discarded")
    await _row(db, 1, "Turbo Shot", 1, device="ft", device_version=1, origin="adopted")
    await _row(db, 2, "Blooming", 5, device="fb", device_version=5, origin="adopted")
    await _row(db, 3, "Blooming [AI]", 8, device="pb", device_version=8)
    await _row(db, 4, "Mine [AI]", 11, device="fm", device_version=11, origin="adopted")
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    assert got["1"]["versions"] == [(1, "machine")], "the firmware profile keeps only its own"
    assert got["2"]["versions"] == [(5, "machine")]
    assert got["3"]["versions"] == [(6, "edit"), (7, "edit"), (8, "edit")]
    turbo = next(p for p in got.values() if p["label"] == "Ethiopia Turbo [AI]")
    assert turbo["versions"] == [(2, "agent"), (3, "agent"), (4, "agent")]
    assert turbo["on"] == 0 and turbo["current"] == 4
    lever = {str(p["label"]): p["versions"] for p in got.values() if "Lever" in str(p["label"])}
    assert lever == {"Lever": [(9, "import")], "Lever [AI]": [(10, "edit")]}
    # A version that would start a profile of an exact label somebody already has joins it.
    assert got["4"]["versions"] == [(11, "machine"), (12, "edit")]
    await _assert_no_shared_labels(db)


async def test_separate_drafts_of_one_default_make_one_profile_not_several(
    db: Database, tmp_path: Path
) -> None:
    """Three attempts from the firmware "Classic" and two from "Lever" each end as one profile."""
    await _below(db, tmp_path)
    for vid, label, source in (
        (1, "Classic", "device"),
        (2, "Classic [AI]", "draft"),
        (3, "Classic [AI]", "draft"),
        (4, "Classic [AI]", "draft"),
        (5, "Classic [AI]", "draft"),
        (6, "Lever", "device"),
        (7, "Lever [AI]", "draft"),
        (8, "Lever [AI]", "draft"),
    ):
        await _version(db, vid, label, source=source)
    await _draft(db, 1, base=1, version=2, status="superseded", pushed="a1")
    await _draft(db, 2, base=2, version=3, status="pushed", pushed="a2", replaced=2)
    await _draft(db, 3, base=1, version=4, status="discarded")
    await _draft(db, 4, base=1, version=5, status="pushed", pushed="a3")
    await _draft(db, 5, base=6, version=7, status="pushed", pushed="l1", set_id=9)
    await _draft(db, 6, base=6, version=8, status="pushed", pushed="l2")
    await _row(db, 1, "Classic", 1, device="d1", device_version=1, origin="adopted")
    await _row(db, 2, "Lever", 6, device="d2", device_version=6, origin="adopted")
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    by_label = {str(p["label"]): p for p in got.values()}
    assert by_label["Classic"]["versions"] == [(1, "machine")]
    classic = by_label["Classic [AI]"]
    assert [v for v, _ in classic["versions"]] == [2, 3, 4, 5]  # type: ignore[attr-defined]
    assert classic["on"] == 0 and by_label["Lever [AI]"]["on"] == 0
    assert [v for v, _ in by_label["Lever [AI]"]["versions"]] == [7, 8]  # type: ignore[attr-defined]
    assert len(got) == 4
    await _assert_no_shared_labels(db)


async def test_what_a_draft_replaced_is_the_same_profile(db: Database, tmp_path: Path) -> None:
    await _below(db, tmp_path)
    for vid, label, source in (
        (1, "Foo", "device"),
        (2, "Foo [AI]", "draft"),
        (3, "Renamed [AI]", "draft"),
    ):
        await _version(db, vid, label, source=source)
    await _draft(db, 1, base=1, version=2, status="superseded")
    # A different name, no Set: by the put's rule a new profile, but it replaced version 2.
    await _draft(db, 2, base=2, version=3, status="pushed", pushed="p", replaced=2)
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    joined = [p for p in got.values() if (2, "edit") in p["versions"]]  # type: ignore[operator]
    assert len(joined) == 1 and [v for v, _ in joined[0]["versions"]] == [2, 3]  # type: ignore[attr-defined]


async def test_two_profiles_that_exist_are_never_merged_by_a_draft(
    db: Database, tmp_path: Path
) -> None:
    """An old database can hold two live rows with one label; a draft linking them leaves both."""
    await _below(db, tmp_path)
    await _version(db, 1, "X [AI]", source="draft")
    await _version(db, 2, "X [AI]", source="draft")
    await _draft(db, 1, base=1, version=2, status="pushed", pushed="b", replaced=1)
    await _row(db, 1, "X [AI]", 1, device="a", device_version=1)
    await _row(db, 2, "X [AI]", 2, device="b", device_version=2)
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    assert [v for v, _ in got["1"]["versions"]] == [1]  # type: ignore[attr-defined]
    assert [v for v, _ in got["2"]["versions"]] == [2]  # type: ignore[attr-defined]


async def test_a_set_chain_renamed_onto_a_held_name_joins_the_profile_that_has_it(
    db: Database, tmp_path: Path
) -> None:
    """Whatever path placed a version, no two profiles end with one name: a Set's chain that
    was renamed to a name a live row holds joins that row. Only current names count, so a
    later fresh draft with the chain's former name is a profile of its own."""
    await _below(db, tmp_path)
    for vid, label, source in (
        (1, "Classic", "device"),
        (2, "Chosen [AI]", "draft"),
        (3, "Start [AI]", "draft"),
        (4, "Chosen [AI]", "draft"),
        (7, "Old name [AI]", "draft"),
        (8, "New name [AI]", "draft"),
        (9, "Old name [AI]", "draft"),
    ):
        await _version(db, vid, label, source=source)
    await _draft(db, 1, base=1, version=3, status="superseded", set_id=7)
    await _draft(db, 2, base=3, version=4, status="pushed", pushed="s2", replaced=3, set_id=7)
    await _draft(db, 4, base=1, version=7, status="superseded")
    await _draft(db, 5, base=7, version=8, status="pushed", pushed="o2", replaced=7, set_id=8)
    await _draft(db, 6, base=1, version=9, status="discarded")
    await _row(db, 1, "Classic", 1, device="d1", device_version=1, origin="adopted")
    await _row(db, 2, "Chosen [AI]", 2, device="c1", device_version=2)
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    assert [v for v, _ in got["2"]["versions"]] == [2, 3, 4]  # type: ignore[attr-defined]
    by_label = {str(p["label"]): p for k, p in got.items() if int(k) > 2}
    assert [v for v, _ in by_label["New name [AI]"]["versions"]] == [7, 8]  # type: ignore[attr-defined]
    assert [v for v, _ in by_label["Old name [AI]"]["versions"]] == [9]  # type: ignore[attr-defined]
    await _assert_no_shared_labels(db)


async def test_a_made_profile_is_active_on_its_newest_pushed_version(
    db: Database, tmp_path: Path
) -> None:
    await _below(db, tmp_path)
    await _version(db, 1, "Base", source="device")
    for vid in (7, 8, 9):
        await _version(db, vid, "Alt [AI]" if vid != 8 else "Pushed name [AI]", source="draft")
    await _draft(db, 1, base=1, version=7, status="superseded")
    await _draft(db, 2, base=7, version=8, status="pushed", pushed="p", replaced=7)
    await _draft(db, 3, base=8, version=9, status="discarded", replaced=8)
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    made = next(p for p in got.values() if (8, "edit") in p["versions"])  # type: ignore[operator]
    assert [v for v, _ in made["versions"]] == [7, 8, 9]  # type: ignore[attr-defined]
    assert made["current"] == 8 and made["label"] == "Pushed name [AI]"
    # With nothing pushed it is the newest by id.
    await db.execute("DELETE FROM profile_board")
    await db.execute("DELETE FROM profile_board_versions")
    await db.execute("DELETE FROM profile_list_build")
    await db.execute("UPDATE profile_drafts SET status = 'superseded'")
    await ProfileListBuilder(db).build()
    again = next(p for p in (await _profiles(db)).values() if (8, "edit") in p["versions"])  # type: ignore[operator]
    assert again["current"] == 9


async def test_an_import_or_pre_board_version_joins_the_profile_with_exactly_its_name(
    db: Database, tmp_path: Path
) -> None:
    await _below(db, tmp_path)
    await _version(db, 1, "Lever", source="device")
    await _version(db, 2, "Lever", source="import")
    await _version(db, 3, "Lever [AI]", source="import")
    await _row(db, 1, "Lever", 1, device="d", device_version=1, origin="adopted")
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    assert got["1"]["versions"] == [(1, "machine"), (2, "import")]
    other = next(p for k, p in got.items() if k != "1")
    assert other["label"] == "Lever [AI]" and other["versions"] == [(3, "import")]


async def test_a_sets_chain_alone_links_a_renamed_version_to_its_profile(
    db: Database, tmp_path: Path
) -> None:
    """No file, no replaced version: the Set's profile is the one its draft is named for, and a
    renamed draft is a profile of its own (the put's rule: a name never changes through a
    version)."""
    await _below(db, tmp_path)
    await _version(db, 1, "Base", source="device")
    await _version(db, 2, "First [AI]", source="draft")
    await _version(db, 3, "Second [AI]", source="draft")
    await _draft(db, 1, base=1, version=2, status="superseded", set_id=5)
    await _draft(db, 2, base=2, version=3, status="discarded", set_id=5)
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    first = next(p for p in got.values() if (2, "agent") in p["versions"])  # type: ignore[operator]
    second = next(p for p in got.values() if (3, "agent") in p["versions"])  # type: ignore[operator]
    assert [v for v, _ in first["versions"]] == [2] and first["label"] == "First [AI]"  # type: ignore[attr-defined]
    assert [v for v, _ in second["versions"]] == [3] and second["label"] == "Second [AI]"  # type: ignore[attr-defined]


async def test_a_new_draft_never_continues_the_profile_of_its_stored_base(
    db: Database, tmp_path: Path
) -> None:
    """A draft designed from scratch is stored against some base only to have a diff anchor. In
    the fill, as in the put, it does not join that base's profile even when the labels agree,
    and the synthetic baseline is never a profile."""
    await _below(db, tmp_path)
    await _version(db, 1, "Mine [AI]", source="draft")
    await _version(db, 2, "Empty baseline", source="draft")
    await _version(db, 3, "Mine [AI]", source="draft")
    await _version(db, 4, "From zero [AI]", source="draft")
    await _draft(db, 1, base=1, version=3, status="pushed", pushed="p3", is_new=True)
    await _draft(db, 2, base=2, version=4, status="discarded", is_new=True)
    await _row(db, 1, "Mine [AI]", 1, device="p1", device_version=1)
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    # Pushed as the file the row stands on is a hard fact, so version 3 is the row's; version 4
    # is its own profile and nothing is listed for the synthetic base.
    listed = {v for p in got.values() for v, _ in p["versions"]}  # type: ignore[attr-defined]
    assert 2 not in listed and 4 in listed
    own = next(p for p in got.values() if (4, "edit") in p["versions"])  # type: ignore[operator]
    assert [v for v, _ in own["versions"]] == [4]  # type: ignore[attr-defined]
    await _assert_no_shared_labels(db)


async def test_a_made_profiles_name_comes_from_its_newest_pushed_version(
    db: Database, tmp_path: Path
) -> None:
    """Pushed ``A [AI]``, then a discarded rename ``B [AI]``: the profile is ``A [AI]`` for the
    fill's own questions as well as in the row it makes, so a lone ``B [AI]`` version does not
    join it (a profile is only ever known by its current name, never by an unpushed one)."""
    await _below(db, tmp_path)
    await _version(db, 1, "Base", source="device")
    await _version(db, 7, "A [AI]", source="draft")
    await _version(db, 8, "A [AI]", source="draft")
    await _version(db, 9, "B [AI]", source="draft")
    await _version(db, 10, "B [AI]", source="draft")
    await _draft(db, 1, base=1, version=7, status="superseded")
    await _draft(db, 2, base=7, version=8, status="pushed", pushed="p", replaced=7)
    await _draft(db, 3, base=8, version=9, status="discarded", replaced=8)
    # Another, unrelated draft that happens to carry the rename's name.
    await _draft(db, 4, base=1, version=10, status="discarded")
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    made = next(p for p in got.values() if (8, "edit") in p["versions"])  # type: ignore[operator]
    assert made["label"] == "A [AI]" and made["current"] == 8
    assert [v for v, _ in made["versions"]] == [7, 8, 9]  # type: ignore[attr-defined]
    lone = next(p for p in got.values() if (10, "edit") in p["versions"])  # type: ignore[operator]
    assert [v for v, _ in lone["versions"]] == [10]  # type: ignore[attr-defined]
    await _assert_no_shared_labels(db)


async def test_what_a_draft_replaced_stays_with_the_profile_that_already_owns_it(
    db: Database, tmp_path: Path
) -> None:
    """A draft's version lands in a made group, and the version it replaced belongs to a profile
    that exists: the made group joins that profile. The profile keeps every version it had; they
    are not moved out into a new profile."""
    await _below(db, tmp_path)
    await _version(db, 2, "Foo [AI]", source="draft")
    await _version(db, 3, "Renamed [AI]", source="draft")
    await _draft(db, 2, base=2, version=3, status="pushed", pushed="p", replaced=2)
    await _row(db, 1, "Foo [AI]", 2, device="p0", device_version=2)
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    assert list(got) == ["1"], "no second profile is made for what the row already owns"
    assert [v for v, _ in got["1"]["versions"]] == [2, 3]  # type: ignore[attr-defined]
    assert got["1"]["label"] == "Foo [AI]" and got["1"]["current"] == 2


async def test_a_second_change_to_a_profile_of_the_persons_joins_the_first_copy(
    db: Database, tmp_path: Path
) -> None:
    """The fill asks the same lineage question a put does: a draft based on a profile the app did
    not make continues the app's copy of it (named with the suffix) when one exists."""
    await _below(db, tmp_path)
    await _version(db, 1, "Foo", source="device")
    await _version(db, 2, "Foo [AI]", source="draft")
    await _version(db, 3, "Foo [AI]", source="draft")
    await _draft(db, 1, base=1, version=2, status="pushed", pushed="p")
    await _draft(db, 2, base=1, version=3, status="discarded")
    await _row(db, 1, "Foo", 1, device="d1", device_version=1, origin="adopted")
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    assert [v for v, _ in got["1"]["versions"]] == [1]  # type: ignore[attr-defined]
    copies = [p for k, p in got.items() if k != "1"]
    assert len(copies) == 1 and [v for v, _ in copies[0]["versions"]] == [2, 3]  # type: ignore[attr-defined]
    assert copies[0]["label"] == "Foo [AI]"


async def test_a_version_a_pushed_file_placed_is_not_pulled_into_another_group_by_its_name(
    db: Database, tmp_path: Path
) -> None:
    """A draft pushed as the file a profile stands on belongs to that profile. Its label happens
    to be the name another (made) group has: the fill does not merge the two."""
    await _below(db, tmp_path)
    await _version(db, 1, "Bar", source="device")
    await _version(db, 2, "Base", source="device")
    await _version(db, 3, "Foo [AI]", source="draft")
    await _version(db, 4, "Foo [AI]", source="draft")
    await _draft(db, 1, base=2, version=3, status="pushed", pushed="p2")
    await _draft(db, 2, base=2, version=4, status="discarded")
    await _row(db, 1, "Bar", 1, device="p2", device_version=1, origin="adopted")
    await run_migrations(db)

    await ProfileListBuilder(db).build()

    got = await _profiles(db)
    row = got["1"]
    assert [v for v, _ in row["versions"]] == [1, 3]  # type: ignore[attr-defined]
    other = next(p for k, p in got.items() if k != "1" and (4, "edit") in p["versions"])  # type: ignore[operator]
    assert [v for v, _ in other["versions"]] == [4]  # type: ignore[attr-defined]
