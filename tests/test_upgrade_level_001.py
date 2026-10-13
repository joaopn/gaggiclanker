"""The first upgrade step: a profile owns its versions; drafts and machine files store names.

Every test builds a database from the frozen level-0 schema, fills it with raw SQL (the step
works on rows exactly as level 0 left them), runs the real upgrade at start, and reads the
result back. The numbered comments are the step's definitions.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import UpgradeRefused, UpgradeReport, upgrade_database_file
from gaggiclanker.db.upgrades import STEPS, frozen_schema_sql, level_001

UNOWNED = (
    "INSERT INTO profile_versions (content_hash, label, type, json) VALUES ('h', 'l', 't', '{}')"
)
NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
LEVEL_ONE = schema.describe_sql(frozen_schema_sql(1))


class Level0:
    """A database as the version before this one left it, filled row by row."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "gaggiclanker.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(frozen_schema_sql(0))
        self.counter = 0

    # ── profile versions and the profile list ────────────────────────

    def version(self, label: str, *, brew: str | None = None, source: str = "device") -> int:
        self.counter += 1
        document = f'{{"label":"{label}","brew":"{brew or label}"}}'
        cursor = self.conn.execute(
            "INSERT INTO profile_versions (content_hash, label, type, json, device_json, source, "
            "created_at) VALUES (?, ?, 'pro', ?, ?, ?, ?)",
            (
                f"hash-{label}-{brew or label}",
                label,
                document,
                document + " ",
                source,
                f"2026-10-0{self.counter % 9 + 1}T10:00:00.000Z",
            ),
        )
        return int(cursor.lastrowid or 0)

    def board(
        self,
        label: str,
        version: int,
        *,
        deleted: bool = False,
        device: str | None = None,
        listed: tuple[int, ...] = (),
        **columns: int | None,
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO profile_board (label, current_version_id, device_profile_id, origin, "
            "deleted_at) VALUES (?, ?, ?, 'adopted', ?)",
            (label, version, device, "2026-10-05T10:00:00.000Z" if deleted else None),
        )
        board_id = int(cursor.lastrowid or 0)
        for name, value in columns.items():
            self.conn.execute(
                f"UPDATE profile_board SET {name} = ? WHERE id = ?",  # noqa: S608 - test column names
                (value, board_id),
            )
        for listed_version in (version, *listed):
            self.add_listing(board_id, listed_version)
        return board_id

    def add_listing(self, board_id: int, version: int, *, source: str = "machine") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO profile_board_versions (board_id, version_id, added_at, source) "
            "VALUES (?, ?, ?, ?)",
            (board_id, version, f"2026-10-0{version % 9 + 1}T09:00:00.000Z", source),
        )

    def device(self, device_id: str, version: int, *, deleted: bool = False) -> None:
        self.conn.execute(
            "INSERT INTO device_profiles (device_id, current_version_id, deleted_at) "
            "VALUES (?, ?, ?)",
            (device_id, version, "2026-10-05T10:00:00.000Z" if deleted else None),
        )

    # ── drafts, Sets, pins, shots, signatures ────────────────────────

    def draft(
        self,
        base: int,
        draft_version: int | None,
        *,
        status: str = "draft",
        is_new: bool = False,
        set_id: int | None = None,
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO profile_drafts "
            "(base_version_id, draft_version_id, status, is_new, set_id) VALUES (?, ?, ?, ?, ?)",
            (base, draft_version, status, int(is_new), set_id),
        )
        return int(cursor.lastrowid or 0)

    def set_brewing(self, version: int, *, pushed: str | None = None) -> tuple[int, int]:
        bean = int(self.conn.execute("INSERT INTO beans (name) VALUES ('b')").lastrowid or 0)
        set_id = int(
            self.conn.execute(
                "INSERT INTO sets (name, bean_id) VALUES (?, ?)", (f"Set {self.counter}", bean)
            ).lastrowid
            or 0
        )
        set_version = int(
            self.conn.execute(
                "INSERT INTO set_versions (set_id, profile_version_id, pushed_device_profile_id) "
                "VALUES (?, ?, ?)",
                (set_id, version, pushed),
            ).lastrowid
            or 0
        )
        self.conn.execute(
            "UPDATE sets SET current_version_id = ? WHERE id = ?", (set_version, set_id)
        )
        return set_id, set_version

    def pin(self, set_version: int, board_id: int, version: int, *, unpinned: bool = False) -> int:
        cursor = self.conn.execute(
            "INSERT INTO set_version_pins "
            "(set_version_id, board_id, profile_version_id, unpinned_at) VALUES (?, ?, ?, ?)",
            (set_version, board_id, version, "2026-10-06T10:00:00.000Z" if unpinned else None),
        )
        return int(cursor.lastrowid or 0)

    def shot(self, version: int) -> int:
        cursor = self.conn.execute(
            "INSERT INTO shots (device_id, raw_slog, profile_version_id) "
            "VALUES ('000001', x'00', ?)",
            (version,),
        )
        return int(cursor.lastrowid or 0)

    def signature(self, version: int, *sentences: tuple[str, str]) -> list[int]:
        signature = int(
            self.conn.execute(
                "INSERT INTO profile_signatures (profile_version_id) VALUES (?)", (version,)
            ).lastrowid
            or 0
        )
        ids = []
        for position, (sentence, status) in enumerate(sentences, start=1):
            ids.append(
                int(
                    self.conn.execute(
                        "INSERT INTO signature_expectations (signature_id, position, tier, kind, "
                        "sentence, status, reason, reject_reason, needs_phase) "
                        "VALUES (?, ?, 'critical', 'free_text', ?, ?, 'why', ?, ?)",
                        (
                            signature,
                            position,
                            sentence,
                            status,
                            "no" if status == "rejected" else "",
                            int(position == 2),
                        ),
                    ).lastrowid
                    or 0
                )
            )
        return ids

    # ── running the upgrade ──────────────────────────────────────────

    def upgrade(self) -> UpgradeReport:
        self.conn.commit()
        self.conn.close()
        # This step alone, onto the schema it produced: the steps after it have their own tests.
        report = upgrade_database_file(
            self.path,
            self.path.parent,
            steps=STEPS[:1],
            expected=LEVEL_ONE,
            now=NOW,
        )
        assert report is not None
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        return report

    def one(self, sql: str, *params: Any) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def rows(self, sql: str, *params: Any) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def owner(self, version: int) -> int | None:
        found: int | None = self.one("SELECT board_id FROM profile_versions WHERE id = ?", version)
        return found


@pytest.fixture
def db(tmp_path: Path) -> Level0:
    return Level0(tmp_path)


# ── the shape ────────────────────────────────────────────────────────────


def test_the_step_is_registered_and_expects_the_frozen_level_zero() -> None:
    assert [s.level for s in STEPS][:1] == [1]
    assert STEPS[0].foreign_keys_off is True
    built = schema.describe_sql(frozen_schema_sql(0))
    assert schema.fingerprint_of(built) == STEPS[0].expects == level_001.EXPECTS


def test_the_result_is_the_schema_file_at_level_one_and_every_row_is_still_there(
    db: Level0,
) -> None:
    first = db.version("A")
    second = db.version("B")
    board = db.board("A", first)
    db.device("dev-1", first)
    db.draft(second, None)
    db.shot(first)
    before = {
        table: db.one(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
        for table in ("profile_versions", "profile_board", "device_profiles", "profile_drafts")
    }
    seq = db.one("SELECT seq FROM sqlite_sequence WHERE name = 'profile_versions'")
    report = db.upgrade()
    assert report.to_level == 1
    conn = sqlite3.connect(db.path)
    assert schema.schema_differences(conn, LEVEL_ONE) == []
    conn.close()
    assert conn is not None and board
    for table, count in before.items():
        assert db.one(f"SELECT COUNT(*) FROM {table}") == count, table  # noqa: S608
    # The counter of the rebuilt table continues where the old one was.
    assert db.one("SELECT seq FROM sqlite_sequence WHERE name = 'profile_versions'") == seq
    assert db.one("PRAGMA user_version") == 1
    assert db.one("SELECT COUNT(*) FROM sqlite_master WHERE type = 'view'") > 3
    assert db.rows("PRAGMA foreign_key_check") == []


def test_a_rebuild_does_not_reuse_an_id_the_old_table_had_handed_out(db: Level0) -> None:
    low = db.version("Low")
    high = db.version("High")
    db.conn.execute("DELETE FROM profile_versions WHERE id = ?", (high,))
    db.board("Low", low)
    db.board("Other", db.version("Other"), listed=(low,))
    db.upgrade()
    assert db.one("SELECT MAX(id) FROM profile_versions") > high


# ── 1: who owns a version ────────────────────────────────────────────────


def test_a_version_is_owned_by_the_live_profile_that_lists_it_else_the_lowest_id(
    db: Level0,
) -> None:
    only_tombstoned = db.version("Tomb")
    both = db.version("Both")
    solo = db.version("Solo")
    first = db.board("Gone", only_tombstoned, deleted=True, listed=(both,))
    second = db.board("Live", solo, listed=(both,))
    db.upgrade()
    assert db.owner(only_tombstoned) == first  # nobody live lists it: the tombstoned one
    assert db.owner(both) == second  # a live profile before a lower tombstoned id
    assert db.owner(solo) == second


def test_a_version_nobody_lists_but_a_row_names_belongs_to_that_row(db: Level0) -> None:
    current = db.version("Current")
    on_file = db.version("On file")
    other = db.version("Other")
    row = db.board("P", current, device_version_id=on_file, failed_version_id=other)
    db.upgrade()
    assert db.owner(on_file) == row
    assert db.owner(other) == row


def test_anything_else_stays_without_an_owner(db: Level0) -> None:
    db.board("P", db.version("P"))
    drafted = db.version("Drafted", source="draft")
    imported = db.version("Imported", source="import")
    db.draft(drafted, imported)
    db.upgrade()
    assert db.owner(drafted) is None
    assert db.owner(imported) is None


# ── 2: versions two profiles listed ──────────────────────────────────────


def _two_profiles_on_one_version(db: Level0) -> dict[str, int]:
    shared = db.version("Shared")
    mine = db.version("Mine")
    first = db.board("First", shared, device="file-first")
    second = db.board(
        "Second",
        mine,
        device="file-second",
        listed=(shared,),
        previous_version_id=shared,
        device_version_id=shared,
        failed_version_id=shared,
        back_from_version_id=shared,
    )
    db.conn.execute(
        "UPDATE profile_board_versions SET source = 'edited_on_machine', "
        "added_at = '2026-01-02T00:00:00.000Z' "
        "WHERE board_id = ? AND version_id = ?",
        (second, shared),
    )
    db.device("file-first", shared)
    db.device("file-second", shared)
    db.device("file-unclaimed", shared)
    return {"shared": shared, "mine": mine, "first": first, "second": second}


def test_a_version_listed_by_two_profiles_gives_the_second_its_own_copy(db: Level0) -> None:
    ids = _two_profiles_on_one_version(db)
    report = db.upgrade()
    assert db.owner(ids["shared"]) == ids["first"]
    copy = db.one(
        "SELECT id FROM profile_versions "
        "WHERE board_id = ? AND content_hash = 'hash-Shared-Shared'",
        ids["second"],
    )
    assert copy is not None and copy != ids["shared"]
    original, duplicate = (
        dict(db.rows("SELECT * FROM profile_versions WHERE id = ?", i)[0])
        for i in (ids["shared"], copy)
    )
    for same in (
        "content_hash",
        "label",
        "type",
        "utility",
        "json",
        "device_json",
        "source",
        "created_at",
    ):
        assert original[same] == duplicate[same], same
    assert report.steps[0].counts["split_listed"] == 1
    assert report.steps[0].counts.get("split_named", 0) == 0


def test_the_profile_that_got_a_copy_has_every_reference_of_its_own_on_the_copy(db: Level0) -> None:
    ids = _two_profiles_on_one_version(db)
    db.upgrade()
    copy = db.one("SELECT id FROM profile_versions WHERE board_id = ?", ids["second"])
    copy = db.one(
        "SELECT id FROM profile_versions WHERE board_id = ? AND id != ?", ids["second"], ids["mine"]
    )
    entry = db.rows(
        "SELECT * FROM profile_board_versions WHERE board_id = ? AND version_id = ?",
        ids["second"],
        copy,
    )
    # The same entry, the same moment and source, now naming the copy.
    assert len(entry) == 1
    assert entry[0]["source"] == "edited_on_machine"
    assert entry[0]["added_at"] == "2026-01-02T00:00:00.000Z"
    assert not db.rows(
        "SELECT 1 FROM profile_board_versions WHERE board_id = ? AND version_id = ?",
        ids["second"],
        ids["shared"],
    )
    row = db.rows("SELECT * FROM profile_board WHERE id = ?", ids["second"])[0]
    for column in (
        "device_version_id",
        "failed_version_id",
        "previous_version_id",
        "back_from_version_id",
    ):
        assert row[column] == copy, column
    assert row["current_version_id"] == ids["mine"]
    # Its own file, and only its own, points at the copy.
    assert (
        db.one("SELECT current_version_id FROM device_profiles WHERE device_id = 'file-second'")
        == copy
    )
    assert (
        db.one("SELECT current_version_id FROM device_profiles WHERE device_id = 'file-first'")
        == ids["shared"]
    )
    assert (
        db.one("SELECT current_version_id FROM device_profiles WHERE device_id = 'file-unclaimed'")
        == ids["shared"]
    )
    first = db.rows("SELECT * FROM profile_board WHERE id = ?", ids["first"])[0]
    assert first["current_version_id"] == ids["shared"]


def test_everything_else_keeps_pointing_at_the_original(db: Level0) -> None:
    ids = _two_profiles_on_one_version(db)
    shot = db.shot(ids["shared"])
    _, set_version = db.set_brewing(ids["shared"], pushed="file-first")
    pin = db.pin(set_version, ids["first"], ids["shared"], unpinned=True)
    draft = db.draft(ids["shared"], ids["shared"], status="pushed")
    db.upgrade()
    assert db.one("SELECT profile_version_id FROM shots WHERE id = ?", shot) == ids["shared"]
    assert (
        db.one("SELECT profile_version_id FROM set_versions WHERE id = ?", set_version)
        == ids["shared"]
    )
    assert (
        db.one("SELECT profile_version_id FROM set_version_pins WHERE id = ?", pin) == ids["shared"]
    )
    row = db.rows(
        "SELECT base_version_id, draft_version_id FROM profile_drafts WHERE id = ?", draft
    )[0]
    assert (row[0], row[1]) == (ids["shared"], ids["shared"])


def test_the_copy_gets_the_originals_signature_and_names_each_expectation_it_came_from(
    db: Level0,
) -> None:
    ids = _two_profiles_on_one_version(db)
    expectations = db.signature(
        ids["shared"], ("Peaks early", "confirmed"), ("Flat tail", "rejected")
    )
    _, set_version = db.set_brewing(ids["shared"])
    db.conn.execute(
        "INSERT INTO set_version_signature_overrides "
        "(set_version_id, expectation_id, compare_json) VALUES (?, ?, '{}')",
        (set_version, expectations[0]),
    )
    report = db.upgrade()
    copy = db.one(
        "SELECT id FROM profile_versions WHERE board_id = ? AND id != ?", ids["second"], ids["mine"]
    )
    original = db.rows(
        "SELECT * FROM signature_expectations WHERE signature_id = "
        "(SELECT id FROM profile_signatures WHERE profile_version_id = ?) ORDER BY position",
        ids["shared"],
    )
    carried = db.rows(
        "SELECT * FROM signature_expectations WHERE signature_id = "
        "(SELECT id FROM profile_signatures WHERE profile_version_id = ?) ORDER BY position",
        copy,
    )
    assert len(original) == len(carried) == 2
    for was, now in zip(original, carried, strict=True):
        for same in (
            "position",
            "tier",
            "kind",
            "sentence",
            "status",
            "reason",
            "reject_reason",
            "needs_phase",
        ):
            assert was[same] == now[same], same
        assert now["carried_from_id"] == was["id"]
        assert was["carried_from_id"] is None
    assert [r["status"] for r in carried] == ["confirmed", "rejected"]
    # The Set version's override stays on the original's expectation.
    assert db.one("SELECT expectation_id FROM set_version_signature_overrides") == expectations[0]
    assert report.steps[0].counts["signatures_copied"] == 1


def test_a_version_without_a_signature_copies_none(db: Level0) -> None:
    _two_profiles_on_one_version(db)
    report = db.upgrade()
    assert report.steps[0].counts.get("signatures_copied", 0) == 0
    assert db.one("SELECT COUNT(*) FROM profile_signatures") == 0


def test_three_profiles_on_one_version_give_two_copies_and_the_counts_say_so(db: Level0) -> None:
    shared = db.version("Shared")
    first = db.board("A", shared)
    second = db.board("B", db.version("B"), listed=(shared,))
    third = db.board("C", db.version("C"), listed=(shared,), deleted=True)
    report = db.upgrade()
    assert db.owner(shared) == first
    owners = [
        r[0]
        for r in db.rows(
            "SELECT board_id FROM profile_versions WHERE content_hash = 'hash-Shared-Shared'"
        )
    ]
    assert sorted(owners) == sorted([first, second, third])
    assert report.steps[0].counts["split_listed"] == 2


def test_a_version_two_rows_name_but_nobody_lists_is_split_as_well(db: Level0) -> None:
    named = db.version("Named")
    first = db.board("A", db.version("A"), device_version_id=named)
    second = db.board("B", db.version("B"), failed_version_id=named)
    report = db.upgrade()
    assert db.owner(named) == first
    copy = db.one("SELECT failed_version_id FROM profile_board WHERE id = ?", second)
    assert copy != named and db.owner(copy) == second
    assert report.steps[0].counts["split_named"] == 1


# ── 3: draft names ───────────────────────────────────────────────────────


def test_a_draft_is_named_by_its_version_else_by_its_base(db: Level0) -> None:
    base = db.version("Base")
    drafted = db.version("Drafted", source="draft")
    db.board("Base", base)
    with_version = db.draft(base, drafted)
    without_version = db.draft(base, None)
    db.upgrade()
    assert db.one("SELECT label FROM profile_drafts WHERE id = ?", with_version) == "Drafted"
    assert db.one("SELECT label FROM profile_drafts WHERE id = ?", without_version) == "Base"


# ── 4: where an open draft goes ──────────────────────────────────────────


def _destination(db: Level0, draft: int) -> int | None:
    found: int | None = db.one(
        "SELECT destination_board_id FROM profile_drafts WHERE id = ?", draft
    )
    return found


def test_a_draft_that_keeps_its_profiles_name_is_a_change_to_that_profile(db: Level0) -> None:
    base = db.version("Espresso")
    row = db.board("Espresso", base)
    # Same name, another brew: a new version of Espresso (the labels agree).
    drafted = db.version("espresso ", brew="tuned", source="draft")
    approved = db.draft(base, drafted)
    db.conn.execute("UPDATE profile_drafts SET status = 'approved' WHERE id = ?", (approved,))
    db.upgrade()
    assert _destination(db, approved) == row


def test_a_draft_under_another_name_is_a_new_profile(db: Level0) -> None:
    base = db.version("Espresso")
    db.board("Espresso", base)
    renamed = db.draft(base, db.version("Lungo", source="draft"))
    fresh = db.draft(base, db.version("Brand new", source="draft"), is_new=True)
    report = db.upgrade()
    assert _destination(db, renamed) is None
    assert _destination(db, fresh) is None
    assert report.steps[0].counts["open_new"] == 2


def test_a_draft_whose_name_is_taken_is_refused_and_stays_without_a_destination(db: Level0) -> None:
    base = db.version("Espresso")
    other = db.version("Lungo")
    db.board("Espresso", base)
    db.board("Lungo", other)
    clash = db.draft(base, db.version("lungo", brew="x", source="draft"))
    report = db.upgrade()
    assert _destination(db, clash) is None
    assert report.steps[0].counts["open_taken"] == 1


def test_a_draft_of_a_pinned_copy_is_refused(db: Level0) -> None:
    original = db.version("Espresso")
    copy = db.version("Espresso v1")
    db.board("Espresso", original)
    copy_row = db.board("Espresso v1", copy)
    _, set_version = db.set_brewing(original)
    db.pin(set_version, copy_row, copy)
    frozen = db.draft(copy, db.version("Espresso v1", brew="edit", source="draft"))
    report = db.upgrade()
    assert _destination(db, frozen) is None
    assert report.steps[0].counts["open_frozen"] == 1


def test_a_base_listed_by_a_dead_profile_continues_the_live_profile_of_that_name(
    db: Level0,
) -> None:
    old = db.version("Espresso")
    live_version = db.version("Espresso", brew="now")
    db.board("Espresso", old, deleted=True)
    live = db.board("Espresso", live_version)
    drafted = db.draft(old, db.version("Espresso", brew="next", source="draft"))
    db.upgrade()
    assert _destination(db, drafted) == live


def test_a_sets_profile_comes_first_when_its_name_is_the_drafts(db: Level0) -> None:
    base = db.version("x v1")
    set_version_profile = db.version("X v1")
    db.board("x v1", base)
    sets_row = db.board("X v1", set_version_profile, device="file-x")
    db.device("file-x", set_version_profile)
    set_id, _ = db.set_brewing(set_version_profile, pushed="file-x")
    drafted = db.draft(base, db.version("X V1", brew="y", source="draft"), set_id=set_id)
    db.upgrade()
    assert _destination(db, drafted) == sets_row


def test_a_closed_draft_goes_to_the_profile_that_lists_its_version_else_nowhere(db: Level0) -> None:
    listed = db.version("Listed")
    row = db.board("Listed", listed)
    unlisted = db.version("Unlisted", source="draft")
    pushed = db.draft(listed, listed, status="pushed")
    discarded = db.draft(listed, unlisted, status="discarded")
    superseded = db.draft(listed, None, status="superseded")
    report = db.upgrade()
    assert _destination(db, pushed) == row
    assert _destination(db, discarded) is None
    assert _destination(db, superseded) is None
    assert report.steps[0].counts["closed_to_a_profile"] == 1


# ── 5: machine file names ────────────────────────────────────────────────


def test_a_machine_file_is_named_by_the_version_it_maps_to(db: Level0) -> None:
    first = db.version("First")
    second = db.version("Second")
    db.device("a", first)
    db.device("b", second, deleted=True)
    report = db.upgrade()
    assert db.one("SELECT label FROM device_profiles WHERE device_id = 'a'") == "First"
    assert db.one("SELECT label FROM device_profiles WHERE device_id = 'b'") == "Second"
    assert report.steps[0].counts["file_labels"] == 2


# ── 6: the end state, checked inside the transaction ─────────────────────


def _upgraded(db: Level0) -> sqlite3.Connection:
    shared = db.version("Shared")
    db.board("A", shared)
    db.board("B", db.version("B"), listed=(shared,))
    db.upgrade()
    return db.conn


def test_the_end_state_check_passes_on_what_the_step_leaves(db: Level0) -> None:
    level_001._check_end_state(_upgraded(db))


def test_the_end_state_check_names_a_listed_version_its_lister_does_not_own(db: Level0) -> None:
    conn = _upgraded(db)
    conn.execute("UPDATE profile_versions SET board_id = NULL WHERE id = 1")
    with pytest.raises(RuntimeError, match="not owned by the profile that lists"):
        level_001._check_end_state(conn)


def test_the_end_state_check_names_a_column_that_names_another_profiles_version(db: Level0) -> None:
    conn = _upgraded(db)
    conn.execute("PRAGMA foreign_keys = OFF")
    other = conn.execute("SELECT current_version_id FROM profile_board WHERE id = 1").fetchone()[0]
    conn.execute("UPDATE profile_board SET failed_version_id = ? WHERE id = 2", (other,))
    with pytest.raises(RuntimeError, match="failed_version_id"):
        level_001._check_end_state(conn)


def test_a_failed_end_state_check_rolls_the_whole_upgrade_back(
    db: Level0, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = db.version("Shared")
    db.board("A", shared)
    db.board("B", db.version("B"), listed=(shared,))
    db.conn.commit()
    before = list(db.conn.iterdump())
    db.conn.close()

    def broken(conn: sqlite3.Connection) -> None:
        raise RuntimeError("1 listed version(s) are not owned by the profile that lists them")

    monkeypatch.setattr(level_001, "_check_end_state", broken)
    with pytest.raises(UpgradeRefused, match="not owned by the profile"):
        upgrade_database_file(db.path, db.path.parent, steps=STEPS[:1], expected=LEVEL_ONE, now=NOW)
    conn = sqlite3.connect(db.path)
    try:
        assert list(conn.iterdump()) == before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        conn.close()
    assert not list(db.path.parent.glob("upgrade-backup-*"))


def test_two_versions_of_one_profile_with_the_same_brew_cannot_exist_afterwards(db: Level0) -> None:
    conn = _upgraded(db)
    owner = conn.execute(
        "SELECT board_id FROM profile_versions WHERE board_id IS NOT NULL"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO profile_versions (content_hash, board_id, label, type, json) "
            "SELECT content_hash, board_id, label, type, json FROM profile_versions "
            "WHERE board_id = ? LIMIT 1",
            (owner,),
        )
    # Two versions with no profile and the same brew cannot either; with different owners they can.
    conn.execute(UNOWNED)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(UNOWNED)


# ── 1 (continued): which of several listing profiles owns the version ────


def _shared_between_two(db: Level0, *, first_dead: bool = False) -> tuple[int, int, int]:
    shared = db.version("Shared")
    first = db.board("First", db.version("First's"), listed=(shared,), deleted=first_dead)
    second = db.board("Second", db.version("Second's"), listed=(shared,))
    return shared, first, second


def test_the_profile_the_version_is_active_for_owns_it_whatever_the_ids(db: Level0) -> None:
    shared = db.version("Shared")
    first = db.board("First", db.version("First's"), listed=(shared,))
    second = db.board("Second", shared)
    db.upgrade()
    assert db.owner(shared) == second
    assert db.one("SELECT COUNT(*) FROM profile_versions WHERE board_id = ?", first) == 2


def test_failing_that_the_profile_whose_file_holds_it_owns_it(db: Level0) -> None:
    shared, first, second = _shared_between_two(db)
    db.board("Third", db.version("Third's"), device="file-third")
    db.device("file-third", db.version("Held elsewhere"))
    db.conn.execute(
        "UPDATE profile_board SET device_profile_id = 'file-second' WHERE id = ?", (second,)
    )
    db.device("file-second", shared)
    db.upgrade()
    assert db.owner(shared) == second != first


def test_failing_that_a_live_profile_comes_before_a_deleted_one_and_the_lowest_id_wins(
    db: Level0,
) -> None:
    shared = db.version("Shared")
    db.board("Dead", db.version("Dead's"), listed=(shared,), deleted=True)
    live_low = db.board("Live low", db.version("Low's"), listed=(shared,))
    db.board("Live high", db.version("High's"), listed=(shared,))
    db.upgrade()
    assert db.owner(shared) == live_low


# ── 4 (continued): what a draft is, and where a change with no profile goes ──


def _intent(db: Level0, draft: int) -> tuple[int, int | None]:
    found = db.rows(
        "SELECT new_profile, destination_board_id FROM profile_drafts WHERE id = ?", draft
    )[0]
    return found[0], found[1]


def test_a_draft_is_a_change_or_a_new_profile_by_what_the_rule_said(db: Level0) -> None:
    base = db.version("Espresso")
    row = db.board("Espresso", base)
    taken_by = db.board("Lungo", db.version("Lungo"))
    change = db.draft(base, db.version("Espresso", brew="n", source="draft"))
    renamed = db.draft(base, db.version("Soft", source="draft"))
    taken = db.draft(base, db.version("lungo", brew="x", source="draft"))
    scratch = db.draft(base, db.version("From zero", source="draft"), is_new=True)
    pushed_version = db.version("Espresso", brew="old", source="draft")
    db.add_listing(row, pushed_version)
    done = db.draft(base, pushed_version, status="pushed")
    done_new = db.draft(base, db.version("Done fork", source="draft"), status="discarded")
    db.upgrade()
    assert _intent(db, change) == (0, row)
    assert _intent(db, renamed) == (1, None)
    assert _intent(db, taken) == (1, None)
    assert _intent(db, scratch) == (1, None)
    assert _intent(db, done) == (0, row)
    assert _intent(db, done_new) == (1, None)
    assert taken_by


def test_a_change_no_live_profile_has_the_base_of_stays_a_change_with_no_destination(
    db: Level0,
) -> None:
    """Drafted before the list adopted the machine: the profile is not a row yet."""
    base = db.version("Espresso", source="device")
    drafted = db.draft(base, db.version("Espresso", brew="n", source="draft"))
    pinned_copy = db.version("Espresso v1")
    copy_row = db.board("Espresso v1", pinned_copy)
    _, set_version = db.set_brewing(db.version("Original"))
    db.pin(set_version, copy_row, pinned_copy)
    frozen = db.draft(pinned_copy, db.version("Espresso v1", brew="e", source="draft"))
    report = db.upgrade()
    assert _intent(db, drafted) == (0, None)
    assert _intent(db, frozen) == (0, None)
    assert report.steps[0].counts["open_orphan"] == 1
    assert report.steps[0].counts["open_frozen"] == 1


# ── 4 (continued): how the frozen placement finds a draft's base ─────────


def test_a_base_two_live_profiles_list_goes_to_the_lowest_id_as_the_placement_rule_did(
    db: Level0,
) -> None:
    base = db.version("Espresso")
    first = db.board("Espresso", db.version("First's own"), listed=(base,))
    db.board("espresso", db.version("Second's own"), listed=(base,))
    drafted = db.draft(base, db.version("Espresso", brew="n", source="draft"))
    db.upgrade()
    assert _intent(db, drafted) == (0, first)


def test_a_base_only_a_live_profiles_file_holds_is_found_through_the_file(db: Level0) -> None:
    """Not listed by anyone: the profile standing on the file that holds it is its profile. Its
    name is not the draft's, so the draft is a new profile; without the file it would have been
    a change nobody has the base of."""
    base = db.version("Espresso")
    db.board("Renamed on the display", db.version("Its own"), device="file-held")
    db.device("file-held", base)
    drafted = db.draft(base, db.version("Espresso", brew="n", source="draft"))
    db.upgrade()
    assert _intent(db, drafted) == (1, None)


def test_a_version_active_for_one_profile_beats_another_profiles_file_holding_it(
    db: Level0,
) -> None:
    shared = db.version("Shared")
    db.board("Holds the file", db.version("Its own"), device="file-held", listed=(shared,))
    db.device("file-held", shared)
    active = db.board("Has it active", shared)
    db.upgrade()
    assert db.owner(shared) == active


def test_a_closed_draft_goes_to_the_owner_of_its_version_not_to_the_last_that_lists_it(
    db: Level0,
) -> None:
    shared = db.version("Shared")
    owner = db.board("Owner", shared)
    db.board("Other", db.version("Other's own"), listed=(shared,))
    done = db.draft(shared, shared, status="pushed")
    db.upgrade()
    assert _intent(db, done)[1] == owner
