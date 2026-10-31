"""Upgrade step 6 (level 5 to 6): a 'balanced' that may be the machine card's default is cleared.

The machine's notes card starts its balance on "balanced" whether or not anybody touched it, so
a "balanced" on a verdict that was copied from a card (``seeded_from_device_note = 1``) is the
card's default and not the person's choice. Saving such a verdict from a cell or the form carries
the copy into a verdict the person has saved (flag 0), where it cannot be told from a choice when
the card says balanced too. The step clears a 'balanced' where the flag is still set, or where the
shot's card holds 'balanced'; a saved 'balanced' on a shot with no card, or with a Sour or Bitter
card, stays. The databases
here are built from the frozen schema of level 5, which is what the previous version wrote.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db import schema
from gaggiclanker.db.upgrade import UpgradeReport, upgrade_database_file
from gaggiclanker.db.upgrades import STEPS, frozen_schema_sql, level_006

LEVEL_SIX = schema.describe_sql(frozen_schema_sql(6))

NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)

#: The columns that must come through untouched on every row but the one the step clears.
KEPT = (
    "shot_id, rating, taste_notes_json, aroma_notes_json, dose_in_g, dose_out_g, notes, "
    "decision, seeded_from_device_note, device_synced_at, updated_at"
)

COPIED = "2026-10-20T08:00:00.000Z"
EDITED = "2026-10-22T09:30:00.000Z"


class Level5:
    """A database as the previous version left it, with one verdict of each kind."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "gaggiclanker.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(frozen_schema_sql(5))
        self.shots: dict[str, int] = {}
        # Copied from a card that was left on its default: cleared (still flagged).
        self._verdict("card_default", balance="balanced", seeded=1, rating=4, notes="from card")
        self._card("card_default", "balanced")
        # Still flagged, but the card has since been moved to Sour on the machine: cleared, since
        # the flag says nobody has saved it and a flagged Balanced is never the person's.
        self._verdict("flagged_card_now_sour", balance="balanced", seeded=1, rating=3)
        self._card("flagged_card_now_sour", "sour")
        # Copied from a card where somebody chose Sour or Bitter on the machine.
        self._verdict("card_sour", balance="sour", seeded=1, rating=3)
        self._card("card_sour", "sour")
        self._verdict("card_bitter", balance="bitter", seeded=1)
        self._card("card_bitter", "bitter")
        # Copied from a card with no balance at all.
        self._verdict("card_none", balance=None, seeded=1, rating=2)
        # A saved verdict (flag 0) holding 'balanced': it may be a carried copy of the card's
        # default, so it goes when the card says balanced too, and stays otherwise.
        self._verdict("saved_card_balanced", balance="balanced", seeded=0, rating=5, notes="mine")
        self._card("saved_card_balanced", "balanced")
        self._verdict("saved_card_sour", balance="balanced", seeded=0, rating=4)
        self._card("saved_card_sour", "sour")
        self._verdict("saved_no_card", balance="balanced", seeded=0, rating=4, notes="mine")
        self._verdict("saved_sour", balance="sour", seeded=0)
        self._card("saved_sour", "balanced")
        self.conn.commit()

    def _verdict(
        self,
        name: str,
        *,
        balance: str | None,
        seeded: int,
        rating: int | None = None,
        notes: str = "",
    ) -> None:
        shot = self.conn.execute(
            "INSERT INTO shots (device_id, raw_slog) VALUES (?, x'00')", (name,)
        ).lastrowid
        assert shot is not None
        self.shots[name] = shot
        self.conn.execute(
            "INSERT INTO shot_judgements (shot_id, rating, balance, dose_in_g, dose_out_g, notes, "
            "decision, seeded_from_device_note, device_synced_at, updated_at) "
            "VALUES (?, ?, ?, 18.5, 37, ?, 'keep', ?, ?, ?)",
            (
                shot,
                rating,
                balance,
                notes,
                seeded,
                COPIED if seeded else None,
                COPIED if seeded else EDITED,
            ),
        )

    def _card(self, name: str, balance_taste: str) -> None:
        self.conn.execute(
            "INSERT INTO device_shot_notes (shot_id, raw_json, balance_taste) VALUES (?, '{}', ?)",
            (self.shots[name], balance_taste),
        )

    def balance(self, name: str) -> str | None:
        row = self.conn.execute(
            "SELECT balance FROM shot_judgements WHERE shot_id = ?", (self.shots[name],)
        ).fetchone()
        return None if row is None else row[0]

    def kept(self) -> list[tuple[Any, ...]]:
        return [
            tuple(row)
            for row in self.conn.execute(f"SELECT {KEPT} FROM shot_judgements ORDER BY shot_id")  # noqa: S608
        ]

    def one(self, sql: str) -> Any:
        return self.conn.execute(sql).fetchone()[0]

    def upgrade(self) -> UpgradeReport:
        self.conn.commit()
        self.conn.close()
        report = upgrade_database_file(
            self.path, self.path.parent, steps=STEPS[:6], expected=LEVEL_SIX, now=NOW
        )
        assert report is not None
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        return report


@pytest.fixture
def level5(tmp_path: Path) -> Level5:
    return Level5(tmp_path)


def test_the_step_is_the_sixth_and_expects_the_frozen_level_5_schema() -> None:
    assert [s.level for s in STEPS][:6] == [1, 2, 3, 4, 5, 6]
    assert STEPS[5].foreign_keys_off is False
    built = schema.describe_sql(frozen_schema_sql(5))
    assert schema.fingerprint_of(built) == STEPS[5].expects == level_006.EXPECTS


CLEARED = ("card_default", "flagged_card_now_sour", "saved_card_balanced")


def test_a_balanced_that_may_be_the_cards_default_is_cleared_and_nothing_else_of_it_moves(
    level5: Level5,
) -> None:
    # The precondition: each of these holds 'balanced' now, so the NULL below is the step.
    for name in CLEARED:
        assert level5.balance(name) == "balanced", name
    before = level5.kept()

    report = level5.upgrade()

    assert (report.from_level, report.to_level) == (5, 6)
    for name in CLEARED:
        assert level5.balance(name) is None, name
    # Rating, doses, notes, decision, the flag itself and updated_at are the row's own.
    assert level5.kept() == before


def test_a_saved_balanced_is_cleared_only_when_the_card_says_balanced_too(level5: Level5) -> None:
    assert level5.balance("saved_card_balanced") == "balanced"
    assert level5.balance("saved_card_sour") == "balanced"
    assert level5.balance("saved_no_card") == "balanced"

    level5.upgrade()

    assert level5.balance("saved_card_balanced") is None
    assert level5.balance("saved_card_sour") == "balanced"
    assert level5.balance("saved_no_card") == "balanced"


def test_a_flagged_balanced_is_cleared_whatever_the_card_says_now(level5: Level5) -> None:
    assert level5.balance("flagged_card_now_sour") == "balanced"

    level5.upgrade()

    assert level5.balance("flagged_card_now_sour") is None


def test_a_copied_sour_or_bitter_and_a_persons_own_sour_are_kept(level5: Level5) -> None:
    level5.upgrade()

    assert level5.balance("card_sour") == "sour"
    assert level5.balance("card_bitter") == "bitter"
    assert level5.balance("card_none") is None
    assert level5.balance("saved_sour") == "sour"


def test_the_result_is_the_schema_file_and_no_row_is_lost(level5: Level5) -> None:
    rows = level5.one("SELECT COUNT(*) FROM shot_judgements")

    level5.upgrade()

    assert schema.schema_differences(level5.conn, LEVEL_SIX) == []
    assert level5.one("PRAGMA user_version") == 6
    assert level5.one("PRAGMA integrity_check") == "ok"
    assert list(level5.conn.execute("PRAGMA foreign_key_check")) == []
    assert level5.one("SELECT COUNT(*) FROM shot_judgements") == rows == 9
    assert level5.one("SELECT COUNT(*) FROM shots") == 9


def test_the_log_line_counts_what_it_cleared(level5: Level5) -> None:
    report = level5.upgrade()

    assert [step.level for step in report.steps] == [6]
    assert report.steps[0].counts == {"balances_cleared": 3}


def test_a_database_with_nothing_to_clear_comes_through_as_it_was(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "gaggiclanker.db")
    conn.executescript(frozen_schema_sql(5))
    conn.close()

    report = upgrade_database_file(
        tmp_path / "gaggiclanker.db", tmp_path, steps=STEPS[:6], expected=LEVEL_SIX, now=NOW
    )

    assert report is not None
    assert report.steps[0].counts == {"balances_cleared": 0}


def test_the_frozen_level_5_file_is_the_schema_the_previous_version_wrote(level5: Level5) -> None:
    assert level5.one("PRAGMA user_version") == 5
