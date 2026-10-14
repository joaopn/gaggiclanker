"""Upgrade step 3 (level 2 to 3): a version is what its profile brews, whatever it is called.

The databases here are built from the frozen schema of level 2 and filled row by row, with
documents that are real profiles (so the step's frozen brew hash can be held against the
application's own over every shipped fixture).
"""

from __future__ import annotations

import copy
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import UpgradeReport, upgrade_database_file
from gaggiclanker.db.upgrades import STEPS, frozen_schema_sql, level_003
from gaggiclanker.domain.models import (
    Profile,
    brew_hash,
    canonical_document_json,
    document_hash,
)

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "profiles"

BASE_DOCUMENT: dict[str, Any] = json.loads((FIXTURES / "docs-medium-18g.json").read_text())


def document(label: str, temperature: float | None = None) -> dict[str, Any]:
    """A real profile document under ``label``; another temperature is another brew."""
    body = copy.deepcopy(BASE_DOCUMENT)
    body["label"] = label
    if temperature is not None:
        body["temperature"] = temperature
    return json.loads(canonical_document_json(Profile.model_validate(body)))  # type: ignore[no-any-return]


class Level2:
    """A database as the previous version left it, filled row by row."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "gaggiclanker.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(frozen_schema_sql(2))
        self.counter = 0
        self.bean = int(self.conn.execute("INSERT INTO beans (name) VALUES ('b')").lastrowid or 0)

    # ── profiles and their versions ──────────────────────────────────

    def board(
        self,
        label: str,
        *,
        temperature: float | None = None,
        device: str | None = None,
        on: bool = True,
        deleted: bool = False,
    ) -> int:
        """A profile with a first version of its own, active and listed."""
        placeholder = self.version(label, None, temperature=temperature)
        cursor = self.conn.execute(
            "INSERT INTO profile_board (label, current_version_id, device_profile_id, origin, "
            "on_machine, deleted_at) VALUES (?, ?, ?, 'adopted', ?, ?)",
            (label, placeholder, device, int(on), "2026-10-05T10:00:00.000Z" if deleted else None),
        )
        board_id = int(cursor.lastrowid or 0)
        self.conn.execute(
            "UPDATE profile_versions SET board_id = ? WHERE id = ?", (board_id, placeholder)
        )
        self.listing(board_id, placeholder)
        return board_id

    def version(
        self,
        label: str,
        owner: int | None,
        *,
        temperature: float | None = None,
        source: str = "device",
    ) -> int:
        """A version of ``owner`` (listed by it) or, with ``owner`` None, of nobody."""
        self.counter += 1
        body = document(label, temperature)
        cursor = self.conn.execute(
            "INSERT INTO profile_versions (content_hash, board_id, label, type, json, device_json, "
            "source, created_at) VALUES (?, ?, ?, 'pro', ?, ?, ?, ?)",
            (
                document_hash(Profile.model_validate(body)),
                owner,
                label,
                json.dumps(body, sort_keys=True, separators=(",", ":")),
                json.dumps(body) + " ",
                source,
                f"2026-10-0{self.counter % 9 + 1}T10:00:00.000Z",
            ),
        )
        version_id = int(cursor.lastrowid or 0)
        if owner is not None:
            self.listing(owner, version_id)
        return version_id

    def listing(
        self,
        board_id: int,
        version: int,
        *,
        added: str = "2026-10-01T09:00:00.000Z",
        source: str = "machine",
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO profile_board_versions (board_id, version_id, added_at, source) "
            "VALUES (?, ?, ?, ?)",
            (board_id, version, added, source),
        )

    def make_active(self, board_id: int, version: int) -> None:
        self.conn.execute(
            "UPDATE profile_board SET current_version_id = ? WHERE id = ?", (version, board_id)
        )

    def record_file(self, board_id: int, version: int, device_id: str = "f1") -> None:
        """The profile's file holds ``version`` and the app recorded it so."""
        self.conn.execute(
            "UPDATE profile_board SET device_profile_id = ?, device_version_id = ? WHERE id = ?",
            (device_id, version, board_id),
        )

    # ── Sets, shots, signatures ──────────────────────────────────────

    def set_on(
        self,
        name: str,
        *versions: int | None,
        board_id: int | None = None,
        archived: bool = False,
        created: str | None = None,
    ) -> int:
        """A Set whose versions name these profile versions in order; the last is current."""
        self.counter += 1
        cursor = self.conn.execute(
            "INSERT INTO sets (name, bean_id, created_at, archived, board_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                name,
                self.bean,
                created or f"2026-10-0{self.counter % 9 + 1}T08:00:00.000Z",
                int(archived),
                board_id,
            ),
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
        self.counter += 1
        cursor = self.conn.execute(
            "INSERT INTO shots (device_id, start_epoch, raw_slog, profile_version_id, "
            "set_version_id) VALUES ('000001', ?, x'00', ?, ?)",
            (self.counter, version, set_version),
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

    def carried(self, version: int, source_ids: list[int]) -> list[int]:
        """A signature on ``version`` whose expectations are carried copies of ``source_ids``."""
        signature = int(
            self.conn.execute(
                "INSERT INTO profile_signatures (profile_version_id) VALUES (?)", (version,)
            ).lastrowid
            or 0
        )
        ids = []
        for position, source in enumerate(source_ids, start=1):
            ids.append(
                int(
                    self.conn.execute(
                        "INSERT INTO signature_expectations (signature_id, position, tier, kind, "
                        "sentence, status, carried_from_id) "
                        "SELECT ?, ?, tier, kind, sentence, status, id "
                        "FROM signature_expectations WHERE id = ?",
                        (signature, position, source),
                    ).lastrowid
                    or 0
                )
            )
        return ids

    def pin(self, set_version: int, board_id: int, version: int) -> int:
        return int(
            self.conn.execute(
                "INSERT INTO set_version_pins (set_version_id, board_id, profile_version_id) "
                "VALUES (?, ?, ?)",
                (set_version, board_id, version),
            ).lastrowid
            or 0
        )

    def draft(self, base: int, version: int, **columns: Any) -> int:
        names = ["base_version_id", "draft_version_id", *columns]
        cursor = self.conn.execute(
            f"INSERT INTO profile_drafts ({', '.join(names)}) "  # noqa: S608 - test names
            f"VALUES ({', '.join('?' * len(names))})",
            (base, version, *columns.values()),
        )
        return int(cursor.lastrowid or 0)

    def device_file(self, device_id: str, version: int, label: str) -> None:
        self.conn.execute(
            "INSERT INTO device_profiles (device_id, current_version_id, label) VALUES (?, ?, ?)",
            (device_id, version, label),
        )

    # ── running the upgrade ──────────────────────────────────────────

    def upgrade(self) -> UpgradeReport:
        self.conn.commit()
        self.conn.close()
        # This step alone, onto the schema it produced.
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

    def versions_of(self, board_id: int | None) -> list[int]:
        return [
            int(r[0])
            for r in self.rows(
                "SELECT id FROM profile_versions WHERE board_id IS ? ORDER BY id", board_id
            )
        ]

    def counts(self, report: UpgradeReport) -> dict[str, int]:
        return dict(report.steps[-1].counts)


@pytest.fixture
def db(tmp_path: Path) -> Level2:
    return Level2(tmp_path)


def renamed_pair(
    db: Level2, *, name: str = "Nine bar", old: str = "Nine bar [old]"
) -> tuple[int, int, int]:
    """A profile that was renamed on the display and kept: the same brew listed under two names."""
    board = db.board(old)
    first = int(db.one("SELECT current_version_id FROM profile_board WHERE id = ?", board))
    second = db.version(name, board)
    db.make_active(board, second)
    db.conn.execute("UPDATE profile_board SET label = ? WHERE id = ?", (name, board))
    return board, first, second


# ── the shape ────────────────────────────────────────────────────────────


def test_the_step_is_registered_and_expects_the_frozen_level_two() -> None:
    assert [s.level for s in STEPS][:3] == [1, 2, 3]
    assert STEPS[2].foreign_keys_off is False
    built = schema.describe_sql(frozen_schema_sql(2))
    assert schema.fingerprint_of(built) == STEPS[2].expects == level_003.EXPECTS


def test_the_result_is_the_schema_file_and_every_row_not_merged_is_still_there(
    db: Level2,
) -> None:
    board, first, second = renamed_pair(db)
    other = db.board("Adaptive", temperature=90)
    db.set_on("Guji", db.one("SELECT current_version_id FROM profile_board WHERE id = ?", other))
    db.shot(first)
    before = {
        t: db.one(f"SELECT COUNT(*) FROM {t}")  # noqa: S608
        for t in ("profile_board", "sets", "set_versions", "shots", "profile_board_versions")
    }
    seq = db.one("SELECT seq FROM sqlite_sequence WHERE name = 'profile_board'")

    report = db.upgrade()

    assert report.to_level == 3
    conn = sqlite3.connect(db.path)
    assert schema.schema_differences(conn) == []
    conn.close()
    assert db.one("PRAGMA user_version") == 3
    assert db.rows("PRAGMA foreign_key_check") == []
    assert db.one("SELECT seq FROM sqlite_sequence WHERE name = 'profile_board'") == seq
    for table, count in before.items():
        expect = count - 1 if table == "profile_board_versions" else count
        assert db.one(f"SELECT COUNT(*) FROM {table}") == expect, table  # noqa: S608
    assert board == 1 and second > first


# ── 5: the hashes ────────────────────────────────────────────────────────


def test_every_stored_hash_is_the_live_brew_hash_of_its_document(db: Level2) -> None:
    for index, path in enumerate(sorted(FIXTURES.glob("*.json"))):
        body = json.loads(path.read_text())
        profile = Profile.model_validate(body)
        db.conn.execute(
            "INSERT INTO profile_versions (content_hash, label, type, json, source) "
            "VALUES (?, ?, 'pro', ?, 'import')",
            (
                f"old-{index}",
                profile.label,
                canonical_document_json(profile),
            ),
        )
    db.upgrade()

    rows = db.rows("SELECT label, json, content_hash FROM profile_versions")
    assert len(rows) == len(list(FIXTURES.glob("*.json")))
    for row in rows:
        live = brew_hash(Profile.model_validate(json.loads(row["json"])))
        assert (
            row["content_hash"] == live == level_003.brew_hash_of_document(json.loads(row["json"]))
        )


def test_the_brew_hash_leaves_the_name_out_and_nothing_else(db: Level2) -> None:
    same = level_003.brew_hash_of_document(document("A"))
    assert level_003.brew_hash_of_document(document("B")) == same
    assert level_003.brew_hash_of_document(document("A", 91.0)) != same


# ── 6: merges ────────────────────────────────────────────────────────────


def test_versions_of_one_profile_that_differ_only_by_name_become_the_lowest_id(db: Level2) -> None:
    board, first, _second = renamed_pair(db)
    other = db.board("Adaptive", temperature=90)

    report = db.upgrade()

    assert db.versions_of(board) == [first]
    assert db.versions_of(other) == [
        int(db.one("SELECT current_version_id FROM profile_board WHERE id = ?", other))
    ]
    assert db.one("SELECT current_version_id FROM profile_board WHERE id = ?", board) == first
    assert db.one("SELECT label FROM profile_board WHERE id = ?", board) == "Nine bar"
    counts = db.counts(report)
    assert counts["merged_owned"] == 1 and counts.get("merged_ownerless", 0) == 0


def test_the_same_brew_under_two_profiles_is_two_versions(db: Level2) -> None:
    one = db.board("A")
    two = db.board("B")

    db.upgrade()

    assert len(db.versions_of(one)) == 1 and len(db.versions_of(two)) == 1
    assert db.one("SELECT COUNT(DISTINCT content_hash) FROM profile_versions") == 1


def test_loose_versions_are_the_document_they_came_as_and_stay_apart(db: Level2) -> None:
    """A version on no profile has no profile name, so two drafts of one brew under two names
    are two versions, as they were."""
    a = db.version("Idea one", None)
    b = db.version("Idea two", None)
    other = db.version("Idea three", None, temperature=95.0)
    one = db.draft(a, a, label="Idea one", new_profile=1)
    two = db.draft(b, b, label="Idea two", new_profile=1)

    report = db.upgrade()

    assert db.versions_of(None) == [a, b, other]
    assert db.one("SELECT draft_version_id FROM profile_drafts WHERE id = ?", one) == a
    assert db.one("SELECT draft_version_id FROM profile_drafts WHERE id = ?", two) == b
    assert "merged_ownerless" not in db.counts(report)
    # unique by brew and label from here on
    same = db.conn.execute(
        "SELECT content_hash, label FROM profile_versions WHERE id = ?", (a,)
    ).fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO profile_versions (content_hash, label, type, json, source) "
            "VALUES (?, ?, 'pro', '{}', 'draft')",
            (same["content_hash"], same["label"]),
        )
    db.conn.execute(
        "INSERT INTO profile_versions (content_hash, label, type, json, source) "
        "VALUES (?, 'Another name', 'pro', '{}', 'draft')",
        (same["content_hash"],),
    )


def test_every_reference_to_a_merged_version_is_repointed(db: Level2) -> None:
    board, first, second = renamed_pair(db)
    third = db.version("Nine bar [older]", board)
    # the five columns of the profile, besides the active version
    db.conn.execute(
        "UPDATE profile_board SET device_profile_id = 'f1', device_version_id = ?, "
        "failed_version_id = ?, previous_version_id = ?, back_from_version_id = ? WHERE id = ?",
        (second, third, second, second, board),
    )
    db.device_file("f1", second, "Nine bar")
    set_id = db.set_on("Guji", first, second, board_id=board)
    versions = db.set_versions(set_id)
    shot = db.shot(second, int(versions[-1]["id"]))
    draft = db.draft(second, second, replaced_version_id=third, label="Nine bar")
    db.conn.execute(
        "INSERT INTO sync_events (at, kind, message, data_json) VALUES "
        "('2026-10-05T10:00:00Z', 'profile_changed', 'x', ?), "
        "('2026-10-05T10:00:00Z', 'board_recorded', 'y', ?), "
        "('2026-10-05T10:00:00Z', 'something_else', 'z', ?)",
        (
            json.dumps({"version_id": second, "new_version": True}),
            json.dumps({"row_id": board, "version_id": third}),
            json.dumps({"version_id": second}),
        ),
    )
    db.conn.execute(
        "INSERT INTO set_version_proposals (set_id, base_version_id, patch_json, reason, status, "
        "created_at, kind) VALUES (?, ?, ?, 'r', 'proposed', '2026-10-05T10:00:00Z', 'change')",
        (set_id, versions[-1]["id"], json.dumps({"profile_version_id": second, "dose_g": 19})),
    )
    db.conn.execute(
        "INSERT INTO starting_point_runs (bean_id, provider, model, prompt_name, prompt_version, "
        "input_json, output_json, status, created_at) VALUES (?, 'p', 'm', 'n', '1', ?, ?, "
        "'ok', '2026-10-05T10:00:00Z')",
        (
            db.bean,
            json.dumps({"profile_version_id": third}),
            json.dumps({"options": [{"profile_version_id": second}, {"profile_version_id": None}]}),
        ),
    )
    lowest = min(first, second, third)
    db.conn.commit()
    ids = {first, second, third}
    assert len(ids) == 3

    db.upgrade()

    assert db.versions_of(board) == [first]
    assert lowest == first
    row = db.rows("SELECT * FROM profile_board WHERE id = ?", board)[0]
    for column in (
        "current_version_id",
        "device_version_id",
        "failed_version_id",
        "previous_version_id",
        "back_from_version_id",
    ):
        assert row[column] == first, column
    assert db.one("SELECT current_version_id FROM device_profiles WHERE device_id = 'f1'") == first
    assert {r["profile_version_id"] for r in db.set_versions(set_id)} == {first}
    assert db.one("SELECT profile_version_id FROM shots WHERE id = ?", shot) == first
    assert db.rows(
        "SELECT base_version_id, draft_version_id, replaced_version_id FROM profile_drafts "
        "WHERE id = ?",
        draft,
    )[0][:] == (first, first, first)
    assert db.one("SELECT COUNT(*) FROM profile_board_versions WHERE board_id = ?", board) == 1
    events = [json.loads(r[0]) for r in db.rows("SELECT data_json FROM sync_events ORDER BY id")]
    assert [e["version_id"] for e in events] == [first, first, second]  # the other kind is not ours
    assert (
        json.loads(db.one("SELECT patch_json FROM set_version_proposals"))["profile_version_id"]
        == first
    )
    run = db.rows("SELECT input_json, output_json FROM starting_point_runs")[0]
    assert json.loads(run["input_json"]) == {"profile_version_id": first}
    assert json.loads(run["output_json"]) == {
        "options": [{"profile_version_id": first}, {"profile_version_id": None}]
    }


def test_no_text_column_names_a_merged_away_version_afterwards(db: Level2) -> None:
    _board, _first, second = renamed_pair(db)
    db.conn.execute(
        "INSERT INTO sync_events (at, kind, message, data_json) VALUES "
        "('2026-10-05T10:00:00Z', 'profile_changed', 'x', ?)",
        (json.dumps({"version_id": second, "new_version": False}),),
    )
    db.upgrade()

    gone = f'"version_id":{second}'
    for (table,) in db.rows("SELECT name FROM sqlite_master WHERE type = 'table'"):
        for column in db.rows(f"PRAGMA table_info({table})"):
            if column["type"].upper() == "TEXT":
                hit = db.one(
                    f"SELECT COUNT(*) FROM {table} WHERE REPLACE({column['name']}, ' ', '') "  # noqa: S608
                    "LIKE ?",
                    f"%{gone}%",
                )
                assert not hit, (table, column["name"])


def test_a_merged_list_entry_keeps_the_earliest_added_at_and_its_source(db: Level2) -> None:
    board, first, second = renamed_pair(db)
    db.conn.execute(
        "UPDATE profile_board_versions SET added_at = '2026-10-03T00:00:00.000Z', source = 'edit' "
        "WHERE board_id = ? AND version_id = ?",
        (board, first),
    )
    db.conn.execute(
        "UPDATE profile_board_versions SET added_at = '2026-10-02T00:00:00.000Z', "
        "source = 'edited_on_machine' WHERE board_id = ? AND version_id = ?",
        (board, second),
    )

    report = db.upgrade()

    entries = db.rows("SELECT * FROM profile_board_versions WHERE board_id = ?", board)
    assert [(e["version_id"], e["added_at"], e["source"]) for e in entries] == [
        (first, "2026-10-02T00:00:00.000Z", "edited_on_machine")
    ]
    assert db.counts(report)["list_entries_merged"] == 1


def test_a_survivor_without_a_signature_takes_the_others(db: Level2) -> None:
    _board, first, second = renamed_pair(db)
    db.signature(second, ("holds", "confirmed"))

    report = db.upgrade()

    assert db.one("SELECT COUNT(*) FROM profile_signatures") == 1
    assert db.one("SELECT profile_version_id FROM profile_signatures") == first
    assert db.counts(report)["signatures_taken"] == 1


def test_when_both_have_a_signature_the_others_expectations_follow_the_survivors(
    db: Level2,
) -> None:
    board, first, second = renamed_pair(db)
    ours = db.signature(first, ("a", "confirmed"), ("b", "proposed"))
    theirs = db.signature(second, ("c", "confirmed"), ("d", "rejected"))
    set_id = db.set_on("S", second, board_id=board)
    version = int(db.set_versions(set_id)[0]["id"])
    override = int(
        db.conn.execute(
            "INSERT INTO set_version_signature_overrides (set_version_id, expectation_id, "
            "compare_json, reason, status, proposed_at) VALUES (?, ?, '{}', 'why', 'confirmed', "
            "'2026-10-05T10:00:00Z')",
            (version, theirs[1]),
        ).lastrowid
        or 0
    )

    report = db.upgrade()

    rows = db.rows("SELECT * FROM signature_expectations ORDER BY position")
    assert [(r["id"], r["position"], r["sentence"], r["status"]) for r in rows] == [
        (ours[0], 1, "a", "confirmed"),
        (ours[1], 2, "b", "proposed"),
        (theirs[0], 3, "c", "confirmed"),
        (theirs[1], 4, "d", "rejected"),
    ]
    assert db.one("SELECT COUNT(*) FROM profile_signatures") == 1
    assert (
        db.one("SELECT expectation_id FROM set_version_signature_overrides WHERE id = ?", override)
        == theirs[1]
    )
    counts = db.counts(report)
    assert counts["signatures_unioned"] == 1 and counts["expectations_moved"] == 2


def test_an_expectation_carried_from_one_the_survivor_has_is_not_added_twice(db: Level2) -> None:
    board, first, second = renamed_pair(db)
    ours = db.signature(first, ("a", "confirmed"), ("b", "confirmed"))
    carried = db.carried(second, ours[:1])
    extra = int(
        db.conn.execute(
            "INSERT INTO signature_expectations (signature_id, position, tier, kind, sentence, "
            "status) SELECT signature_id, 2, 'critical', 'free_text', 'only theirs', 'confirmed' "
            "FROM signature_expectations WHERE id = ?",
            (carried[0],),
        ).lastrowid
        or 0
    )
    set_id = db.set_on("S", second, board_id=board)
    version = int(db.set_versions(set_id)[0]["id"])
    db.conn.execute(
        "INSERT INTO set_version_signature_overrides (set_version_id, expectation_id, "
        "compare_json, reason, status, proposed_at) VALUES (?, ?, '{}', 'why', 'confirmed', "
        "'2026-10-05T10:00:00Z')",
        (version, carried[0]),
    )

    report = db.upgrade()

    sentences = [
        r[0] for r in db.rows("SELECT sentence FROM signature_expectations ORDER BY position")
    ]
    assert sentences == ["a", "b", "only theirs"]
    assert db.one("SELECT expectation_id FROM set_version_signature_overrides") == ours[0]
    assert db.one("SELECT position FROM signature_expectations WHERE id = ?", extra) == 3
    assert db.counts(report)["expectations_dropped_as_carried_copies"] == 1


def test_two_pins_whose_copies_would_merge_refuse_the_upgrade(db: Level2) -> None:
    board, first, second = renamed_pair(db)
    set_id = db.set_on("S", first, second, board_id=board)
    versions = db.set_versions(set_id)
    copy_board = db.board("Copy", temperature=93.0)
    one = db.version("Copy one", copy_board)
    two = db.version("Copy two", copy_board)
    db.pin(int(versions[0]["id"]), copy_board, one)
    db.pin(int(versions[1]["id"]), copy_board, two)
    db.conn.commit()
    db.conn.close()

    from gaggiclanker.db.upgrade import UpgradeRefused

    with pytest.raises(UpgradeRefused, match="copies of two different pins"):
        upgrade_database_file(db.path, db.path.parent, steps=STEPS, now=NOW)
    conn = sqlite3.connect(db.path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.close()


def test_a_pin_and_its_original_at_one_brew_stay_two_versions(db: Level2) -> None:
    original = db.board("Nine bar")
    version = int(db.one("SELECT current_version_id FROM profile_board WHERE id = ?", original))
    set_id = db.set_on("Guji", version, board_id=original)
    copy_board = db.board("Guji copy")
    copy_version = int(
        db.one("SELECT current_version_id FROM profile_board WHERE id = ?", copy_board)
    )
    pin = db.pin(int(db.set_versions(set_id)[0]["id"]), copy_board, copy_version)

    db.upgrade()

    assert (
        db.one("SELECT profile_version_id FROM set_version_pins WHERE id = ?", pin) == copy_version
    )
    assert db.versions_of(original) == [version]
    assert db.versions_of(copy_board) == [copy_version]


def test_a_pin_copy_that_merges_with_a_version_found_on_its_file_follows_it(db: Level2) -> None:
    copy_board = db.board("Guji copy")
    copy_version = int(
        db.one("SELECT current_version_id FROM profile_board WHERE id = ?", copy_board)
    )
    edited = db.version("Guji copy [display]", copy_board)
    original = db.board("Nine bar", temperature=90)
    set_id = db.set_on(
        "Guji",
        int(db.one("SELECT current_version_id FROM profile_board WHERE id = ?", original)),
        board_id=original,
    )
    pin = db.pin(int(db.set_versions(set_id)[0]["id"]), copy_board, edited)

    db.upgrade()

    assert (
        db.one("SELECT profile_version_id FROM set_version_pins WHERE id = ?", pin) == copy_version
    )


def test_a_version_found_for_one_profile_and_for_nobody_are_not_merged(db: Level2) -> None:
    board = db.board("Nine bar")
    loose = db.version("Nine bar", None)

    db.upgrade()

    assert db.versions_of(None) == [loose] and len(db.versions_of(board)) == 1


def test_the_recorded_name_of_a_file_is_the_stored_label_of_the_recorded_version(
    db: Level2,
) -> None:
    board, first, _second = renamed_pair(db, name="Nine bar", old="Nine bar [old]")
    db.record_file(board, first)
    unrecorded = db.board("Adaptive", temperature=90)

    db.upgrade()

    assert db.one("SELECT device_label FROM profile_board WHERE id = ?", board) == "Nine bar [old]"
    assert db.one("SELECT device_version_id FROM profile_board WHERE id = ?", board) == first
    assert db.one("SELECT device_label FROM profile_board WHERE id = ?", unrecorded) is None


# ── the Sets that the name kept apart ────────────────────────────────────


def test_the_loose_copies_the_previous_step_made_for_sets_stay_apart(db: Level2) -> None:
    """Two Sets that shared one loose version were given a copy under "<name> — <Set>" by the
    previous step. Loose versions are the document they came as, so nothing merges back and no
    Set moves."""
    shared = db.version("Nine bar", None)
    copied = db.version("Nine bar — Kenya", None)
    guji = db.set_on("Guji", shared)
    kenya = db.set_on("Kenya", copied)

    report = db.upgrade()

    assert db.versions_of(None) == [shared, copied]
    assert db.one("SELECT board_id FROM sets WHERE id = ?", guji) is None
    assert db.one("SELECT board_id FROM sets WHERE id = ?", kenya) is None
    assert {r["profile_version_id"] for r in db.set_versions(kenya)} == {copied}
    counts = db.counts(report)
    assert "sets_given_a_profile" not in counts and "profiles_created" not in counts
    assert db.one("SELECT COUNT(*) FROM profile_board") == 0


# ── 7: the views ─────────────────────────────────────────────────────────


def test_the_views_show_the_profiles_name_and_the_stored_label_only_for_a_loose_version(
    db: Level2,
) -> None:
    board, first, _second = renamed_pair(db, name="Nine bar", old="Nine bar [old]")
    loose = db.version("Loose idea", None, temperature=95.0)
    set_id = db.set_on("Guji", first, board_id=board)
    db.shot(first, int(db.set_versions(set_id)[0]["id"]))
    db.shot(loose)

    db.upgrade()

    labels = {r[0]: r[1] for r in db.rows("SELECT profile_version_id, label FROM v_profiles")}
    assert labels[first] == "Nine bar" and labels[loose] == "Loose idea"
    assert db.one("SELECT profile_label FROM v_set_versions") == "Nine bar"
    assert sorted(r[0] for r in db.rows("SELECT profile_label FROM v_shots")) == [
        "Loose idea",
        "Nine bar",
    ]


def test_v_profiles_still_finds_the_empty_baseline_by_its_stored_label(db: Level2) -> None:
    db.conn.execute(
        "INSERT INTO profile_versions (content_hash, label, type, json, source) "
        "VALUES ('x', 'Empty baseline', 'pro', ?, 'draft')",
        (
            json.dumps(
                {
                    **document("Empty baseline"),
                    "description": "An empty baseline, created because the archive held no "
                    "profile to diff a new draft against.",
                }
            ),
        ),
    )
    db.version("Real", None)

    db.upgrade()

    assert [r[0] for r in db.rows("SELECT label FROM v_profiles")] == ["Real"]


# ── 8: the end state ─────────────────────────────────────────────────────


def test_a_failed_check_rolls_the_whole_step_back(
    db: Level2, monkeypatch: pytest.MonkeyPatch
) -> None:
    renamed_pair(db)
    db.conn.commit()

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("a lost row")

    monkeypatch.setattr(level_003, "_check_end_state", broken)
    db.conn.close()

    from gaggiclanker.db.upgrade import UpgradeRefused

    with pytest.raises(UpgradeRefused):
        upgrade_database_file(db.path, db.path.parent, steps=STEPS, now=NOW)
    conn = sqlite3.connect(db.path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM profile_versions").fetchone()[0] == 2
    conn.close()


def test_the_end_state_names_what_it_found_wrong(db: Level2) -> None:
    board, first, _second = renamed_pair(db)
    db.upgrade()
    conn = db.conn
    conn.row_factory = sqlite3.Row

    conn.execute("UPDATE profile_board SET device_label = 'x' WHERE id = ?", (board,))
    with pytest.raises(RuntimeError, match="without its name"):
        level_003._check_end_state(conn)
    conn.execute("UPDATE profile_board SET device_label = NULL WHERE id = ?", (board,))

    other = db.board("Elsewhere", temperature=90)
    conn.execute("UPDATE profile_board SET current_version_id = ? WHERE id = ?", (first, other))
    with pytest.raises(RuntimeError, match="does not own"):
        level_003._check_end_state(conn)


# ── the boot log ─────────────────────────────────────────────────────────


def test_each_merge_is_in_the_boot_log(db: Level2, monkeypatch: pytest.MonkeyPatch) -> None:
    board, first, second = renamed_pair(db)
    db.signature(second, ("c", "confirmed"))
    db.signature(first, ("a", "confirmed"))

    logs: list[dict[str, Any]] = []

    class Recorder:
        """The step's logger: other tests configure structlog to drop info lines."""

        def info(self, event: str, **fields: object) -> None:
            logs.append({"event": event, **fields})

    monkeypatch.setattr(level_003, "log", Recorder())
    db.upgrade()

    merged = [e for e in logs if e["event"] == "profile_versions_merged"]
    assert [
        (e["survivor"], e["merged"], e["owner"], e["signature_rows_moved"]) for e in merged
    ] == [(first, [second], board, 1)]


# ── D7: a rejection is never lost to a merge ─────────────────────────────


def rejected(db: Level2, expectation: int, reason: str, at: str) -> None:
    db.conn.execute(
        "UPDATE signature_expectations SET status = 'rejected', reject_reason = ?, "
        "answered_at = ? WHERE id = ?",
        (reason, at, expectation),
    )


def claim(db: Level2, expectation: int) -> int:
    shot = db.shot(int(db.one("SELECT id FROM profile_versions ORDER BY id LIMIT 1")))
    review = int(
        db.conn.execute("INSERT INTO shot_reviews (shot_id) VALUES (?)", (shot,)).lastrowid or 0
    )
    return int(
        db.conn.execute(
            "INSERT INTO review_claims (review_id, position, kind, text, expectation_id) "
            "VALUES (?, 1, 'free_text', 't', ?)",
            (review, expectation),
        ).lastrowid
        or 0
    )


def test_a_dropped_copy_that_was_rejected_rejects_the_expectation_kept(db: Level2) -> None:
    _board, first, second = renamed_pair(db)
    ours = db.signature(first, ("a", "confirmed"), ("b", "confirmed"))
    copies = db.carried(second, ours)
    rejected(db, copies[0], "not this one", "2026-10-06T07:00:00.000Z")
    claim_id = claim(db, copies[0])

    db.upgrade()

    kept = db.rows(
        "SELECT status, reject_reason, answered_at FROM signature_expectations WHERE id = ?",
        ours[0],
    )[0]
    assert (kept[0], kept[1], kept[2]) == ("rejected", "not this one", "2026-10-06T07:00:00.000Z")
    assert db.one("SELECT status FROM signature_expectations WHERE id = ?", ours[1]) == "confirmed"
    assert db.one("SELECT expectation_id FROM review_claims WHERE id = ?", claim_id) == ours[0]
    assert db.one("SELECT COUNT(*) FROM signature_expectations") == 2


def test_a_source_that_was_rejected_stays_rejected_with_its_own_reason(db: Level2) -> None:
    _board, first, second = renamed_pair(db)
    ours = db.signature(first, ("a", "confirmed"))
    copies = db.carried(second, ours)
    rejected(db, ours[0], "mine", "2026-10-06T07:00:00.000Z")
    rejected(db, copies[0], "theirs", "2026-10-07T07:00:00.000Z")
    # a copy whose status was reset by the carry still leaves the source rejected
    db.upgrade()

    kept = db.rows(
        "SELECT status, reject_reason FROM signature_expectations WHERE id = ?", ours[0]
    )[0]
    assert (kept[0], kept[1]) == ("rejected", "mine")


def test_a_copy_of_a_copy_that_was_rejected_is_rejected_on_the_original(db: Level2) -> None:
    """Three names of one brew: b carries a, c carries b, and c's is rejected."""
    board = db.board("Nine")
    a = int(db.one("SELECT current_version_id FROM profile_board WHERE id = ?", board))
    b = db.version("Nine two", board)
    c = db.version("Nine three", board)
    sig_a = db.signature(a, ("one", "confirmed"))
    sig_b = db.carried(b, sig_a)
    sig_c = db.carried(c, sig_b)
    rejected(db, sig_c[0], "no", "2026-10-08T07:00:00.000Z")

    db.upgrade()

    kept = db.rows("SELECT status, reject_reason, answered_at FROM signature_expectations")
    assert [(r[0], r[1], r[2]) for r in kept] == [("rejected", "no", "2026-10-08T07:00:00.000Z")]
