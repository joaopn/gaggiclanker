"""Upgrade step 7 (level 6 to 7): the stars are the judgement, and Discard is its own column.

The databases here are built from the frozen schema of level 6, which is what the previous
version wrote. Every stored label (keep, improve, discard, none) is crossed with where a rating
can sit (the verdict's own, the machine's notes card, the machine's index, nowhere), and each
row is checked against what the step promises.
"""

from __future__ import annotations

import itertools
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import UpgradeReport, upgrade_database_file
from gaggiclanker.db.upgrades import STEPS, frozen_schema_sql, level_007

LEVEL_SIX = schema.describe_sql(frozen_schema_sql(6))
NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)

LABELS = (None, "keep", "improve", "discard")
#: Where the one rating sits (always 5 stars, so a label that contradicts it shows).
PLACES = ("own", "machine_notes", "machine_index", "none")

EDITED = "2026-10-22T09:30:00.000Z"

#: (label, place) -> (rating after, discarded after).


def _rating_after(label: str | None, place: str) -> int | None:
    if place == "own":
        return 5
    if place == "none" and label == "keep":
        return 4
    if place == "none" and label == "improve":
        return 2
    return None


EXPECTED = {
    (label, place): (_rating_after(label, place), 1 if label == "discard" else 0)
    for label, place in itertools.product(LABELS, PLACES)
}


class Level6:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "gaggiclanker.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(frozen_schema_sql(6))
        self.shots: dict[tuple[str | None, str], int] = {}
        for index, (label, place) in enumerate(itertools.product(LABELS, PLACES), start=1):
            shot = self.conn.execute(
                "INSERT INTO shots (device_id, raw_slog, index_rating) VALUES (?, x'00', ?)",
                (f"{index:06d}", 5 if place == "machine_index" else None),
            ).lastrowid
            assert shot is not None
            self.shots[(label, place)] = shot
            self.conn.execute(
                "INSERT INTO shot_judgements (shot_id, rating, balance, notes, dose_in_g, "
                "decision, seeded_from_device_note, updated_at) "
                "VALUES (?, ?, 'sour', ?, 18.5, ?, 1, ?)",
                (shot, 5 if place == "own" else None, f"{label}/{place}", label, EDITED),
            )
            if place == "machine_notes":
                self.conn.execute(
                    "INSERT INTO device_shot_notes (shot_id, raw_json, rating) VALUES (?, '{}', 5)",
                    (shot,),
                )
        # A card whose rating is 0 ("not rated" on the machine) hides the index's figure, as the
        # Rating column reads it, so a keep on it has no rating anywhere.
        shot = self.conn.execute(
            "INSERT INTO shots (device_id, raw_slog, index_rating) VALUES ('000900', x'00', 5)"
        ).lastrowid
        assert shot is not None
        self.shots[("keep", "zero_card")] = shot
        self.conn.execute(
            "INSERT INTO shot_judgements (shot_id, decision) VALUES (?, 'keep')", (shot,)
        )
        self.conn.execute(
            "INSERT INTO device_shot_notes (shot_id, raw_json, rating) VALUES (?, '{}', 0)", (shot,)
        )
        # A shot with no judgement row at all stays without one.
        self.conn.execute("INSERT INTO shots (device_id, raw_slog) VALUES ('000901', x'00')")
        self.conn.commit()

    def one(self, sql: str) -> object:
        return self.conn.execute(sql).fetchone()[0]

    def row(self, key: tuple[str | None, str]) -> sqlite3.Row:
        found = self.conn.execute(
            "SELECT * FROM shot_judgements WHERE shot_id = ?", (self.shots[key],)
        ).fetchone()
        assert isinstance(found, sqlite3.Row)
        return found

    def upgrade(self) -> UpgradeReport:
        self.conn.commit()
        self.conn.close()
        report = upgrade_database_file(self.path, self.path.parent, steps=STEPS[:7], now=NOW)
        assert report is not None
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        return report


@pytest.fixture
def level6(tmp_path: Path) -> Level6:
    return Level6(tmp_path)


def test_the_step_is_the_seventh_and_expects_the_frozen_level_6_schema() -> None:
    assert [s.level for s in STEPS][:7] == [1, 2, 3, 4, 5, 6, 7]
    assert STEPS[6].foreign_keys_off is False
    built = schema.describe_sql(frozen_schema_sql(6))
    assert schema.fingerprint_of(built) == STEPS[6].expects == level_007.EXPECTS


@pytest.mark.parametrize(("label", "place"), list(itertools.product(LABELS, PLACES)))
def test_each_label_and_rating_place_comes_through_as_the_step_promises(
    level6: Level6, label: str | None, place: str
) -> None:
    key = (label, place)
    # The precondition: the label is stored, and the rating sits only where it was put.
    before = level6.row(key)
    assert before["decision"] == label
    assert before["rating"] == (5 if place == "own" else None)

    level6.upgrade()

    after = level6.row(key)
    rating, discarded = EXPECTED[key]
    assert (after["rating"], after["discarded"]) == (rating, discarded)
    # Every other field is the row's own.
    assert (after["balance"], after["notes"], after["dose_in_g"]) == (
        "sour",
        f"{label}/{place}",
        18.5,
    )
    assert (after["seeded_from_device_note"], after["updated_at"]) == (1, EDITED)


def test_a_label_on_a_shot_whose_card_says_zero_has_no_rating_anywhere(level6: Level6) -> None:
    """The card's zero hides the index's 5, as the Rating column reads it: so keep writes 4."""
    key = ("keep", "zero_card")
    assert level6.row(key)["rating"] is None

    level6.upgrade()

    after = level6.row(key)
    assert (after["rating"], after["discarded"]) == (4, 0)


def test_the_result_is_the_schema_file_and_no_row_is_lost(level6: Level6) -> None:
    rows = level6.one("SELECT COUNT(*) FROM shot_judgements")
    shots = level6.one("SELECT COUNT(*) FROM shots")
    level6.conn.commit()
    level6.conn.close()

    report = upgrade_database_file(level6.path, level6.path.parent, steps=STEPS, now=NOW)

    assert report is not None and (report.from_level, report.to_level) == (6, 7)
    conn = sqlite3.connect(level6.path)
    try:
        assert schema.schema_differences(conn) == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert list(conn.execute("PRAGMA foreign_key_check")) == []
        assert conn.execute("SELECT COUNT(*) FROM shot_judgements").fetchone()[0] == rows == 17
        assert conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0] == shots
        # The views serve the new column, and not the old.
        for view in ("v_judgements", "v_shots"):
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({view})")]
            assert "discarded" in columns and "decision" not in columns, view
    finally:
        conn.close()


def test_the_log_line_counts_what_it_did(level6: Level6) -> None:
    report = level6.upgrade()

    assert [step.level for step in report.steps] == [7]
    # Four discards; keep and improve over four places each plus the zero card's keep; a rating
    # written for each of those with none anywhere.
    counts = report.steps[0].counts
    assert counts == {"discarded": 4, "labels_dropped": 9, "ratings_written": 3}


def test_a_database_with_no_judgements_comes_through_empty(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "gaggiclanker.db")
    conn.executescript(frozen_schema_sql(6))
    conn.close()

    report = upgrade_database_file(tmp_path / "gaggiclanker.db", tmp_path, steps=STEPS, now=NOW)

    assert report is not None
    assert report.steps[0].counts == {"discarded": 0, "labels_dropped": 0, "ratings_written": 0}
