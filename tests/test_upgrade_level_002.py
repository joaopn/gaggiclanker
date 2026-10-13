"""The second upgrade step: a Set has one profile, and a profile has at most one Set.

Every test builds a database from the frozen level-1 schema, fills it with raw SQL (the step
works on rows exactly as level 1 left them), runs the real upgrade at start, and reads the result
back. The numbered comments are the step's definitions.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import UpgradeReport, upgrade_database_file
from gaggiclanker.db.upgrades import STEPS, frozen_schema_sql, level_002

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


class Level1:
    """A database as the previous version left it, filled row by row."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "gaggiclanker.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(frozen_schema_sql(1))
        self.counter = 0
        self.bean = int(self.conn.execute("INSERT INTO beans (name) VALUES ('b')").lastrowid or 0)

    # ── profiles and their versions ──────────────────────────────────

    def board(self, label: str, *, deleted: bool = False, device: str | None = None) -> int:
        """A profile with a first version of its own, active and listed."""
        placeholder = self.version(label, None)
        cursor = self.conn.execute(
            "INSERT INTO profile_board (label, current_version_id, device_profile_id, origin, "
            "on_machine, deleted_at) VALUES (?, ?, ?, 'adopted', 1, ?)",
            (label, placeholder, device, "2026-10-05T10:00:00.000Z" if deleted else None),
        )
        board_id = int(cursor.lastrowid or 0)
        self.conn.execute(
            "UPDATE profile_versions SET board_id = ? WHERE id = ?", (board_id, placeholder)
        )
        self.listing(board_id, placeholder)
        return board_id

    def version(self, label: str, owner: int | None, *, brew: str | None = None) -> int:
        """A version of ``owner`` (listed by it) or, with ``owner`` None, of nobody."""
        self.counter += 1
        document = f'{{"label":"{label}","brew":"{brew or label}"}}'
        cursor = self.conn.execute(
            "INSERT INTO profile_versions (content_hash, board_id, label, type, json, device_json, "
            "source, created_at) VALUES (?, ?, ?, 'pro', ?, ?, 'device', ?)",
            (
                f"hash-{brew or label}",
                owner,
                label,
                document,
                document + " ",
                f"2026-10-0{self.counter % 9 + 1}T10:00:00.000Z",
            ),
        )
        version_id = int(cursor.lastrowid or 0)
        if owner is not None:
            self.listing(owner, version_id)
        return version_id

    def listing(
        self, board_id: int, version: int, *, added: str = "2026-10-01T09:00:00.000Z"
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO profile_board_versions (board_id, version_id, added_at, source) "
            "VALUES (?, ?, ?, 'machine')",
            (board_id, version, added),
        )

    def make_active(self, board_id: int, version: int) -> None:
        self.conn.execute(
            "UPDATE profile_board SET current_version_id = ? WHERE id = ?", (version, board_id)
        )

    # ── Sets, shots, signatures ──────────────────────────────────────

    def set_on(self, name: str, *versions: int | None, created: str | None = None) -> int:
        """A Set whose versions name these profile versions in order; the last is current."""
        self.counter += 1
        cursor = self.conn.execute(
            "INSERT INTO sets (name, bean_id, created_at) VALUES (?, ?, ?)",
            (name, self.bean, created or f"2026-10-0{self.counter % 9 + 1}T08:00:00.000Z"),
        )
        set_id = int(cursor.lastrowid or 0)
        parent = None
        for position, pv in enumerate(versions):
            made = self.conn.execute(
                "INSERT INTO set_versions (set_id, parent_version_id, profile_version_id, dose_g, "
                "grind_setting, intent, created_at, version_major, version_minor) "
                "VALUES (?, ?, ?, 18, '20', ?, ?, 1, ?)",
                (
                    set_id,
                    parent,
                    pv,
                    f"intent {position}",
                    f"2026-10-02T10:0{position}:00.000Z",
                    position,
                ),
            )
            parent = int(made.lastrowid or 0)
        self.conn.execute("UPDATE sets SET current_version_id = ? WHERE id = ?", (parent, set_id))
        return set_id

    def set_versions(self, set_id: int) -> list[sqlite3.Row]:
        return self.rows(
            "SELECT * FROM set_versions WHERE set_id = ? ORDER BY created_at, id", set_id
        )

    def shot(self, version: int, set_version: int | None = None) -> int:
        cursor = self.conn.execute(
            "INSERT INTO shots (device_id, raw_slog, profile_version_id, set_version_id) "
            "VALUES ('000001', x'00', ?, ?)",
            (version, set_version),
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
                        "sentence, status) VALUES (?, ?, 'critical', 'free_text', ?, ?)",
                        (signature, position, sentence, status),
                    ).lastrowid
                    or 0
                )
            )
        return ids

    # ── running the upgrade ──────────────────────────────────────────

    def upgrade(self) -> UpgradeReport:
        self.conn.commit()
        self.conn.close()
        report = upgrade_database_file(
            self.path,
            self.path.parent,
            steps=STEPS,
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

    def board_of(self, set_id: int) -> int | None:
        found: int | None = self.one("SELECT board_id FROM sets WHERE id = ?", set_id)
        return found

    def counts(self, report: UpgradeReport) -> dict[str, int]:
        return dict(report.steps[-1].counts)


@pytest.fixture
def db(tmp_path: Path) -> Level1:
    return Level1(tmp_path)


# ── the shape ────────────────────────────────────────────────────────────


def test_the_step_is_registered_and_expects_the_frozen_level_one() -> None:
    assert [s.level for s in STEPS][:2] == [1, 2]
    assert STEPS[1].foreign_keys_off is True
    built = schema.describe_sql(frozen_schema_sql(1))
    assert schema.fingerprint_of(built) == STEPS[1].expects == level_002.EXPECTS


def test_the_result_is_the_schema_file_and_every_row_is_still_there(db: Level1) -> None:
    board = db.board("A")
    version = db.one("SELECT current_version_id FROM profile_board WHERE id = ?", board)
    set_id = db.set_on("S", version)
    db.shot(version, db.set_versions(set_id)[0]["id"])
    before = {
        table: db.one(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
        for table in (
            "sets",
            "set_versions",
            "shots",
            "profile_board",
            "profile_versions",
            "profile_board_versions",
        )
    }
    seq = db.one("SELECT seq FROM sqlite_sequence WHERE name = 'profile_board'")

    report = db.upgrade()

    assert report.to_level == 2
    conn = sqlite3.connect(db.path)
    assert schema.schema_differences(conn) == []
    conn.close()
    for table, count in before.items():
        assert db.one(f"SELECT COUNT(*) FROM {table}") == count, table  # noqa: S608
    assert db.one("SELECT seq FROM sqlite_sequence WHERE name = 'profile_board'") == seq
    assert db.rows("PRAGMA foreign_key_check") == []
    assert db.one("PRAGMA user_version") == 2


# ── 7: a Set's profile ───────────────────────────────────────────────────


def test_a_sets_profile_is_the_owner_of_its_current_versions_profile_version(db: Level1) -> None:
    mine = db.board("Mine")
    other = db.board("Other")
    older = db.version("Older", other)
    newer = db.version("Newer", mine)
    set_id = db.set_on("S", older, newer)  # it moved: the current version decides

    db.upgrade()

    assert db.board_of(set_id) == mine


def test_the_current_version_decides_even_when_it_is_not_the_newest_made(db: Level1) -> None:
    """A Set that went back: its current version is an older one, and its profile is that one's."""
    mine = db.board("Mine")
    other = db.board("Other")
    older = db.version("Older", mine)
    newer = db.version("Newer", other)
    set_id = db.set_on("S", older, newer)
    first = db.set_versions(set_id)[0]["id"]
    db.conn.execute("UPDATE sets SET current_version_id = ? WHERE id = ?", (first, set_id))

    db.upgrade()

    assert db.board_of(set_id) == mine


def test_when_the_current_version_has_no_owner_the_newest_owned_one_decides(db: Level1) -> None:
    mine = db.board("Mine")
    other = db.board("Other")
    first = db.version("First", other)
    second = db.version("Second", mine)
    loose = db.version("Loose", None)
    set_id = db.set_on("S", first, second, loose)  # the newest names a version nobody owns

    db.upgrade()

    assert db.board_of(set_id) == mine
    assert db.one("SELECT board_id FROM profile_versions WHERE id = ?", loose) is None


def test_a_set_that_never_named_a_version_a_profile_owns_has_no_profile(db: Level1) -> None:
    loose = db.version("Loose", None)
    nothing = db.set_on("Nothing", None)
    only_loose = db.set_on("Only loose", loose)

    report = db.upgrade()

    assert db.board_of(nothing) is None and db.board_of(only_loose) is None
    assert db.counts(report)["sets_without_a_profile"] == 2
    # Their history is untouched.
    assert [r["profile_version_id"] for r in db.set_versions(only_loose)] == [loose]


# ── 8: two Sets on one profile ───────────────────────────────────────────


def _shared(db: Level1) -> dict[str, int]:
    board = db.board("9 Bar", device="file-1")
    active = db.one("SELECT current_version_id FROM profile_board WHERE id = ?", board)
    older = db.version("9 Bar", board, brew="older")
    guji = db.set_on("Guji", older, active, created="2026-10-01T08:00:00.000Z")
    kenya = db.set_on("Kenya", older, created="2026-10-03T08:00:00.000Z")
    return {"board": board, "active": active, "older": older, "guji": guji, "kenya": kenya}


def test_the_set_on_the_profiles_active_version_keeps_the_profile(db: Level1) -> None:
    w = _shared(db)

    report = db.upgrade()

    assert db.board_of(w["guji"]) == w["board"], "Guji brews the active version, Kenya is newer"
    assert db.board_of(w["kenya"]) not in (None, w["board"])
    assert db.counts(report)["keepers"] == 1 and db.counts(report)["profiles_copied"] == 1


def test_with_no_set_on_the_active_version_the_most_recently_created_keeps_it(db: Level1) -> None:
    board = db.board("9 Bar")
    other = db.version("9 Bar", board, brew="other")
    older = db.set_on("Older", other, created="2026-10-01T08:00:00.000Z")
    newer = db.set_on("Newer", other, created="2026-10-03T08:00:00.000Z")

    db.upgrade()

    assert db.board_of(newer) == board and db.board_of(older) != board


def test_two_sets_on_the_active_version_the_most_recent_keeps_it(db: Level1) -> None:
    board = db.board("9 Bar")
    active = db.one("SELECT current_version_id FROM profile_board WHERE id = ?", board)
    first = db.set_on("First", active, created="2026-10-01T08:00:00.000Z")
    second = db.set_on("Second", active, created="2026-10-03T08:00:00.000Z")

    db.upgrade()

    assert db.board_of(second) == board and db.board_of(first) != board


def test_the_other_set_gets_a_profile_of_its_own_switched_off_and_not_starred(db: Level1) -> None:
    w = _shared(db)

    db.upgrade()

    copy = db.board_of(w["kenya"])
    row = db.rows("SELECT * FROM profile_board WHERE id = ?", copy)[0]
    assert row["label"] == "9 Bar — Kenya"
    assert (row["on_machine"], row["on_home_screen"], row["origin"]) == (0, 0, "draft")
    assert row["device_profile_id"] is None and row["device_version_id"] is None
    assert row["deleted_at"] is None and row["pending_draft_id"] is None
    # The original is as it was: Guji still has its file.
    keeper = db.rows("SELECT * FROM profile_board WHERE id = ?", w["board"])[0]
    assert keeper["device_profile_id"] == "file-1" and keeper["on_machine"] == 1


def test_the_copy_is_made_unique_by_the_name_rule(db: Level1) -> None:
    w = _shared(db)
    db.board("9 bar — kenya")  # a profile with the name the copy would take, case aside

    db.upgrade()

    assert db.one("SELECT label FROM profile_board WHERE id = ?", db.board_of(w["kenya"])) == (
        "9 Bar — Kenya (2)"
    )


def test_the_copy_holds_an_owned_copy_of_every_version_the_set_names_and_lists_them(
    db: Level1,
) -> None:
    w = _shared(db)
    added = "2026-09-30T07:00:00.000Z"
    db.conn.execute(
        "UPDATE profile_board_versions SET added_at = ?, source = 'edited_on_machine' "
        "WHERE board_id = ? AND version_id = ?",
        (added, w["board"], w["older"]),
    )

    report = db.upgrade()

    copy = db.board_of(w["kenya"])
    (kenya_version,) = db.set_versions(w["kenya"])
    new = kenya_version["profile_version_id"]
    assert (
        new != w["older"]
        and db.one("SELECT board_id FROM profile_versions WHERE id = ?", new) == copy
    )
    original = db.rows("SELECT * FROM profile_versions WHERE id = ?", w["older"])[0]
    made = db.rows("SELECT * FROM profile_versions WHERE id = ?", new)[0]
    for column in ("type", "source", "created_at"):
        assert made[column] == original[column], column
    # It is a version of the copy's profile, under that profile's name; the machine's own
    # document is not the copy's.
    copy_label = db.one("SELECT label FROM profile_board WHERE id = ?", copy)
    assert copy_label == "9 Bar — Kenya"
    assert made["label"] == copy_label and json.loads(made["json"])["label"] == copy_label
    assert made["content_hash"] != original["content_hash"] and made["device_json"] is None
    listed = db.rows(
        "SELECT * FROM profile_board_versions WHERE board_id = ? AND version_id = ?", copy, new
    )
    assert [(r["added_at"], r["source"]) for r in listed] == [(added, "edited_on_machine")]
    assert db.one("SELECT current_version_id FROM profile_board WHERE id = ?", copy) == new
    assert db.counts(report)["versions_copied"] == 1


def test_the_copy_carries_the_signature_the_way_a_copied_version_does(db: Level1) -> None:
    w = _shared(db)
    kept, _rejected, _proposed = db.signature(
        w["older"], ("kept", "confirmed"), ("turned down", "rejected"), ("asked", "proposed")
    )

    report = db.upgrade()

    (kenya_version,) = db.set_versions(w["kenya"])
    new = kenya_version["profile_version_id"]
    carried = db.rows(
        "SELECT e.* FROM signature_expectations e "
        "JOIN profile_signatures s ON s.id = e.signature_id WHERE s.profile_version_id = ?",
        new,
    )
    assert [(r["sentence"], r["status"], r["carried_from_id"]) for r in carried] == [
        ("kept", "confirmed", kept)
    ]
    assert db.counts(report)["signatures_copied"] == 1
    # The original keeps what it had.
    assert (
        db.one(
            "SELECT COUNT(*) FROM signature_expectations e JOIN profile_signatures s "
            "ON s.id = e.signature_id WHERE s.profile_version_id = ?",
            w["older"],
        )
        == 3
    )


def test_the_sets_versions_name_the_copies_and_shots_keep_where_they_were_brewed(
    db: Level1,
) -> None:
    w = _shared(db)
    kenya_version = db.set_versions(w["kenya"])[0]["id"]
    shot = db.shot(w["older"], kenya_version)

    db.upgrade()

    assert db.one("SELECT profile_version_id FROM shots WHERE id = ?", shot) == w["older"]
    assert db.one("SELECT set_version_id FROM shots WHERE id = ?", shot) == kenya_version
    # The keeper's versions are untouched.
    assert [r["profile_version_id"] for r in db.set_versions(w["guji"])] == [
        w["older"],
        w["active"],
    ]


def test_nothing_the_machine_holds_changes(db: Level1) -> None:
    w = _shared(db)
    device_rows = db.rows("SELECT device_id, label FROM device_profiles")
    live = db.rows(
        "SELECT id, device_profile_id, on_machine FROM profile_board WHERE id = ?", w["board"]
    )

    db.upgrade()

    assert db.rows("SELECT device_id, label FROM device_profiles") == device_rows
    after = db.rows(
        "SELECT id, device_profile_id, on_machine FROM profile_board WHERE id = ?", w["board"]
    )
    assert [tuple(r) for r in after] == [tuple(r) for r in live]
    assert db.one("SELECT COUNT(*) FROM profile_board WHERE on_machine = 1") == 1


def test_each_split_is_in_the_boot_log(db: Level1, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _shared(db)
    logs: list[dict[str, Any]] = []

    class Recorder:
        """The step's logger: other tests configure structlog to drop info lines."""

        def info(self, event: str, **fields: object) -> None:
            logs.append({"event": event, **fields})

    monkeypatch.setattr(level_002, "log", Recorder())

    db.upgrade()

    [split] = [e for e in logs if e["event"] == "set_profile_copied"]
    assert (split["set_id"], split["from_board_id"], split["kept_by_set_id"]) == (
        w["kenya"],
        w["board"],
        w["guji"],
    )
    assert split["board_id"] == db.board_of(w["kenya"])


def test_a_set_override_stays_on_the_expectation_it_limited(db: Level1) -> None:
    """Old shots read exactly as before: the override is not moved to the copy's expectation."""
    w = _shared(db)
    (expectation,) = db.signature(w["older"], ("kept", "confirmed"))
    kenya_version = db.set_versions(w["kenya"])[0]["id"]
    db.conn.execute(
        "INSERT INTO set_version_signature_overrides "
        "(set_version_id, expectation_id, compare_json, status) VALUES (?, ?, '{}', 'confirmed')",
        (kenya_version, expectation),
    )

    report = db.upgrade()

    assert (
        db.one(
            "SELECT expectation_id FROM set_version_signature_overrides WHERE set_version_id = ?",
            kenya_version,
        )
        == expectation
    )
    assert "overrides_moved" not in db.counts(report)


# ── 9: a Set across several profiles ─────────────────────────────────────


def test_a_version_naming_another_profiles_brew_names_its_own_profiles_version_of_it(
    db: Level1,
) -> None:
    mine = db.board("Mine")
    mine_current = db.one("SELECT current_version_id FROM profile_board WHERE id = ?", mine)
    other = db.board("Other")
    theirs = db.version("Other", other, brew="shared brew")
    of_the_same_brew = db.version("Mine", mine, brew="shared brew")
    set_id = db.set_on("S", theirs, mine_current)

    report = db.upgrade()

    assert db.board_of(set_id) == mine
    assert db.set_versions(set_id)[0]["profile_version_id"] == of_the_same_brew
    assert db.counts(report).get("versions_copied", 0) == 0


def test_a_brew_the_profile_has_no_version_of_is_copied_listed_not_active_with_its_signature(
    db: Level1,
) -> None:
    mine = db.board("Mine")
    active = db.one("SELECT current_version_id FROM profile_board WHERE id = ?", mine)
    other = db.board("Other")
    theirs = db.version("Other", other, brew="theirs only")
    db.signature(theirs, ("kept", "confirmed"))
    set_id = db.set_on("S", theirs, active)

    report = db.upgrade()

    first = db.set_versions(set_id)[0]["profile_version_id"]
    assert (
        first != theirs
        and db.one("SELECT board_id FROM profile_versions WHERE id = ?", first) == mine
    )
    assert (
        db.one(
            "SELECT COUNT(*) FROM profile_board_versions WHERE board_id = ? AND version_id = ?",
            mine,
            first,
        )
        == 1
    )
    assert db.one("SELECT current_version_id FROM profile_board WHERE id = ?", mine) == active
    assert db.counts(report)["versions_copied"] == 1 and db.counts(report)["signatures_copied"] == 1
    # The other profile's version is as it was.
    assert db.one("SELECT board_id FROM profile_versions WHERE id = ?", theirs) == other


def test_a_version_nobody_owns_is_repointed_to_the_sets_profiles_version_of_that_brew(
    db: Level1,
) -> None:
    mine = db.board("Mine")
    active = db.one("SELECT current_version_id FROM profile_board WHERE id = ?", mine)
    loose = db.version("Mine", None)  # same hash as the profile's own first version
    set_id = db.set_on("S", loose, active)

    db.upgrade()

    assert db.set_versions(set_id)[0]["profile_version_id"] == active
    assert db.one("SELECT COUNT(*) FROM profile_versions") == 2, "no copy: the profile had it"


# ── 10: what was waiting for the sync ────────────────────────────────────


def _pending(
    db: Level1, *, status: str = "approved", own: bool = True, major: int | None = None
) -> dict[str, int]:
    board = db.board("P")
    base = db.one("SELECT current_version_id FROM profile_board WHERE id = ?", board)
    drafted = db.version("P drafted", board, brew="drafted")
    set_id = db.set_on("S", base)
    other_set = db.set_on("Other", None)
    draft = int(
        db.conn.execute(
            "INSERT INTO profile_drafts (base_version_id, draft_version_id, status, set_id, "
            "prediction, change_summary) VALUES (?, ?, ?, ?, 'a guess', 'down a bar')",
            (base, drafted, status, set_id),
        ).lastrowid
        or 0
    )
    db.make_active(board, drafted)
    db.conn.execute(
        "UPDATE profile_board SET pending_draft_id = ?, pending_set_id = ?, pending_major = ? "
        "WHERE id = ?",
        (draft, set_id if own else other_set, major, board),
    )
    return {"board": board, "set": set_id, "other": other_set, "draft": draft, "drafted": drafted}


def test_a_pending_recording_for_the_rows_own_set_is_recorded_now(db: Level1) -> None:
    w = _pending(db)

    report = db.upgrade()

    versions = db.set_versions(w["set"])
    assert [r["version_minor"] for r in versions] == [0, 1]
    recorded = versions[-1]
    assert (
        recorded["profile_version_id"] == w["drafted"]
        and recorded["parent_version_id"] == versions[0]["id"]
    )
    assert (recorded["dose_g"], recorded["grind_setting"]) == (18, "20"), "carried forward"
    assert (recorded["intent"], recorded["origin"], recorded["prediction"]) == (
        "down a bar",
        "chat",
        "a guess",
    )
    assert recorded["compares_to_version_id"] == versions[0]["id"]
    assert db.one("SELECT current_version_id FROM sets WHERE id = ?", w["set"]) == recorded["id"]
    assert (
        db.one("SELECT recorded_version_id FROM profile_drafts WHERE id = ?", w["draft"])
        == recorded["id"]
    )
    assert db.counts(report)["pending_recorded"] == 1


def test_a_pending_major_is_recorded_as_a_major(db: Level1) -> None:
    w = _pending(db, major=1)

    db.upgrade()

    assert [(r["version_major"], r["version_minor"]) for r in db.set_versions(w["set"])] == [
        (1, 0),
        (2, 0),
    ]


def test_a_pending_recording_waiting_proposal_goes_stale(db: Level1) -> None:
    w = _pending(db)
    base = db.set_versions(w["set"])[0]["id"]
    db.conn.execute(
        "INSERT INTO set_version_proposals (set_id, base_version_id, patch_json, status) "
        "VALUES (?, ?, '{}', 'proposed')",
        (w["set"], base),
    )

    db.upgrade()

    assert db.one("SELECT status FROM set_version_proposals WHERE set_id = ?", w["set"]) == "stale"


@pytest.mark.parametrize(
    "scenario",
    ["another_set", "draft_pushed", "draft_discarded", "not_the_active_version"],
)
def test_any_other_pending_recording_is_dropped_and_counted(db: Level1, scenario: str) -> None:
    w = _pending(
        db,
        status={"draft_pushed": "pushed", "draft_discarded": "discarded"}.get(scenario, "approved"),
        own=scenario != "another_set",
    )
    if scenario == "not_the_active_version":
        older = db.version("P older", w["board"], brew="older")
        db.make_active(w["board"], older)

    report = db.upgrade()

    # Dropped is not recorded as a pending one; a Set behind its profile still catches up below.
    assert len(db.set_versions(w["set"])) == 1 + db.counts(report).get("sets_followed", 0)
    assert db.counts(report)["pending_dropped"] == 1
    assert db.counts(report).get("pending_recorded", 0) == 0


def test_a_pending_recording_for_a_set_that_was_split_off_is_dropped(db: Level1) -> None:
    w = _shared(db)
    db.conn.execute(
        "UPDATE profile_board SET pending_set_id = ? WHERE id = ?", (w["kenya"], w["board"])
    )

    report = db.upgrade()

    assert db.counts(report)["pending_dropped"] == 1


def test_a_set_behind_its_profile_catches_up_with_one_minor_version(
    db: Level1, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _pending(db, status="approved", own=True)
    db.conn.execute(
        "UPDATE profile_board SET pending_set_id = NULL, pending_draft_id = NULL WHERE id = ?",
        (w["board"],),
    )
    before = db.set_versions(w["set"])[-1]
    logged: list[dict[str, object]] = []
    monkeypatch.setattr(
        level_002.log, "info", lambda event, **kw: logged.append({"event": event, **kw})
    )

    report = db.upgrade()

    versions = db.set_versions(w["set"])
    assert len(versions) == 2
    new = versions[-1]
    active = db.one("SELECT current_version_id FROM profile_board WHERE id = ?", w["board"])
    assert new["profile_version_id"] == active
    assert (new["version_major"], new["version_minor"]) == (
        before["version_major"],
        before["version_minor"] + 1,
    )
    assert new["parent_version_id"] == before["id"]
    assert new["dose_g"] == before["dose_g"]
    assert new["intent"] == before["intent"]
    assert db.one("SELECT current_version_id FROM sets WHERE id = ?", w["set"]) == new["id"]
    assert db.counts(report)["sets_followed"] == 1
    assert [e["event"] for e in logged if e["event"] == "set_followed_profile"] == [
        "set_followed_profile"
    ]


def test_a_set_already_on_its_profiles_active_version_is_left_alone(db: Level1) -> None:
    w = _pending(db, status="approved", own=True)

    report = db.upgrade()

    # the pending recording made the one new version; nothing more is added
    assert len(db.set_versions(w["set"])) == 2
    assert db.counts(report).get("sets_followed", 0) == 0
    assert db.counts(report)["pending_recorded"] == 1


def test_the_dead_columns_are_gone(db: Level1) -> None:
    db.board("P")

    db.upgrade()

    board = {r["name"] for r in db.rows("PRAGMA table_info(profile_board)")}
    assert not board & {"pending_set_id", "pending_major", "back_from_set_version_id"}
    assert "pushed_device_profile_id" not in {
        r["name"] for r in db.rows("PRAGMA table_info(set_versions)")
    }
    assert "cleared_set_version_ids_json" not in {
        r["name"] for r in db.rows("PRAGMA table_info(profile_drafts)")
    }
    assert "board_id" in {r["name"] for r in db.rows("PRAGMA table_info(sets)")}


# ── 11: the end state is checked ─────────────────────────────────────────


def test_a_set_version_naming_another_profiles_version_fails_the_check(db: Level1) -> None:
    mine = db.board("Mine")
    other = db.board("Other")
    foreign = db.version("Foreign", other)
    set_id = db.set_on(
        "S", db.one("SELECT current_version_id FROM profile_board WHERE id = ?", mine)
    )
    db.conn.execute("ALTER TABLE sets ADD COLUMN board_id INTEGER REFERENCES profile_board(id)")
    db.conn.execute("UPDATE sets SET board_id = ? WHERE id = ?", (mine, set_id))
    db.conn.execute(
        "UPDATE set_versions SET profile_version_id = ? WHERE set_id = ?", (foreign, set_id)
    )

    with pytest.raises(RuntimeError, match="name a version their Set's profile lacks"):
        level_002._check_end_state(db.conn, {}, level_002.Counter())  # type: ignore[attr-defined]


def test_a_row_the_step_lost_fails_the_check(db: Level1) -> None:
    db.board("Mine")
    db.conn.execute("ALTER TABLE sets ADD COLUMN board_id INTEGER REFERENCES profile_board(id)")
    before = level_002._totals(db.conn)
    db.conn.execute("DELETE FROM profile_board_versions")

    with pytest.raises(RuntimeError, match="not accounted for"):
        level_002._check_end_state(db.conn, before, Counter())


def test_a_failing_check_leaves_the_database_untouched(
    db: Level1, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shared(db)
    db.conn.commit()

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("a lost row")

    monkeypatch.setattr(level_002, "_check_end_state", broken)
    db.conn.close()

    from gaggiclanker.db.upgrade import UpgradeRefused

    with pytest.raises(UpgradeRefused):
        upgrade_database_file(db.path, db.path.parent, steps=STEPS, now=NOW)
    conn = sqlite3.connect(db.path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM profile_board").fetchone()[0] == 1
    conn.close()


# ── the name a copy carries, keeping a live Set, and Sets sharing a loose version ────────────


def test_a_copy_hashes_as_the_application_hashes_it_under_its_new_name() -> None:
    """The step's frozen canonical form agrees with the live one on every shipped profile."""
    import hashlib
    from pathlib import Path

    from gaggiclanker.domain.models import Profile, profile_content_hash

    for path in sorted(Path("tests/fixtures/profiles").glob("*.json")):
        document = json.loads(path.read_text())
        document["label"] = "Renamed — Set"
        profile = Profile.model_validate(document)
        stored = profile.model_dump(exclude={"annotations"}, exclude_none=True, mode="json")
        mine = hashlib.sha256(level_002._canonical(stored).encode()).hexdigest()
        assert mine == profile_content_hash(profile), path.name


def test_a_set_across_profiles_has_its_versions_under_each_profiles_name(db: Level1) -> None:
    other = db.board("Other")
    elsewhere = db.version("9 Bar", db.board("9 Bar"), brew="x")
    set_id = db.set_on(
        "Across",
        elsewhere,
        db.one("SELECT current_version_id FROM profile_board WHERE id = ?", other),
    )

    db.upgrade()

    board = db.board_of(set_id)
    label = db.one("SELECT label FROM profile_board WHERE id = ?", board)
    for v in db.set_versions(set_id):
        row = db.rows("SELECT * FROM profile_versions WHERE id = ?", v["profile_version_id"])[0]
        assert row["board_id"] == board and row["label"] == label
        assert json.loads(row["json"])["label"] == label


def test_a_set_that_is_not_archived_keeps_the_profile_before_one_that_is(db: Level1) -> None:
    w = _shared(db)
    db.conn.execute("UPDATE sets SET archived = 1 WHERE id = ?", (w["guji"],))

    db.upgrade()

    assert db.board_of(w["kenya"]) == w["board"] and db.board_of(w["guji"]) != w["board"]


def test_two_sets_with_no_profile_on_one_loose_version_share_it_no_longer(db: Level1) -> None:
    loose = db.version("Loose", None, brew="loose")
    a = db.set_on("A", loose, created="2026-10-01T08:00:00.000Z")
    b = db.set_on("B", loose, created="2026-10-03T08:00:00.000Z")

    report = db.upgrade()

    named = {
        s: db.rows("SELECT profile_version_id FROM set_versions WHERE set_id = ?", s)[0][0]
        for s in (a, b)
    }
    assert named[b] == loose, "the most recently created keeps it"
    copy = named[a]
    row = db.rows("SELECT * FROM profile_versions WHERE id = ?", copy)[0]
    assert row["board_id"] is None and row["label"] == "Loose — A" and copy != loose
    assert json.loads(row["json"])["label"] == "Loose — A"
    assert db.counts(report)["loose_copied"] == 1
    assert db.counts(report)["versions_repointed"] == 1
    assert db.board_of(a) is None and db.board_of(b) is None
