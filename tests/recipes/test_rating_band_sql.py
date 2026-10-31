"""``RATING_BAND_SQL`` is the Python rule, over every combination of its three inputs.

The rule lives twice, in ``domain/rating_band.py`` and as a SQL expression the readers embed, so
a test runs the SQL over every combination of a rating in each of the three places the Rating
column reads and compares it with the Python. The schema refuses a judgement rating of 0, which
a hand-edited file could still hold, so the rows are written with the check constraints off: the
point is the expression, not what the schema lets in.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import (
    RATING_BAND_SQL,
    not_discarded_sql,
    rating_band_sql,
    shot_label_sql,
)
from gaggiclanker.db.schema import create_schema
from gaggiclanker.domain.rating_band import rating_band, rating_of

RATINGS: tuple[int | None, ...] = (None, 0, 1, 2, 3, 4, 5)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "band.db")
    await database.connect()
    await create_schema(database)
    try:
        yield database
    finally:
        await database.close()


async def test_the_sql_agrees_with_the_python_over_every_combination(db: Database) -> None:
    await db.execute("PRAGMA ignore_check_constraints = ON")
    combos = list(itertools.product(RATINGS, RATINGS, RATINGS))
    for index, (judged, noted, indexed) in enumerate(combos, start=1):
        await db.execute(
            "INSERT INTO shots (id, device_id, raw_slog, index_rating) VALUES (?, ?, ?, ?)",
            (index, f"{index:06d}", b"x", indexed),
        )
        await db.execute(
            "INSERT INTO shot_judgements (shot_id, rating) VALUES (?, ?)", (index, judged)
        )
        await db.execute(
            "INSERT INTO device_shot_notes (shot_id, raw_json, rating) VALUES (?, '{}', ?)",
            (index, noted),
        )
    rows = await db.fetch_all(
        f"""
        SELECT s.id AS id, ({RATING_BAND_SQL}) AS band
          FROM shots s
          LEFT JOIN shot_judgements j ON j.shot_id = s.id
          LEFT JOIN device_shot_notes n ON n.shot_id = s.id
        """  # noqa: S608 - the only interpolation is the band expression, a constant
    )

    assert len(rows) == len(combos) == 7**3
    # The precondition: every band, and no band, is among the answers the SQL gives.
    assert {row["band"] for row in rows} == {"good", "neutral", "bad", None}
    for row in rows:
        judged, noted, indexed = combos[int(row["id"]) - 1]
        assert row["band"] == rating_band(rating_of(judged, noted, indexed)), (
            judged,
            noted,
            indexed,
        )


async def test_a_shot_with_no_judgement_row_and_no_notes_row_reads_from_its_index(
    db: Database,
) -> None:
    for index, indexed in enumerate((None, 0, 2, 3, 4), start=1):
        await db.execute(
            "INSERT INTO shots (id, device_id, raw_slog, index_rating) VALUES (?, ?, ?, ?)",
            (index, f"{index:06d}", b"x", indexed),
        )
    rows = await db.fetch_all(
        f"""
        SELECT ({rating_band_sql()}) AS band FROM shots s
          LEFT JOIN shot_judgements j ON j.shot_id = s.id
          LEFT JOIN device_shot_notes n ON n.shot_id = s.id
         ORDER BY s.id
        """  # noqa: S608 - the only interpolation is the band expression, a constant
    )

    assert [row["band"] for row in rows] == [None, None, "bad", "neutral", "good"]


async def test_the_aliases_are_a_parameter(db: Database) -> None:
    await db.execute(
        "INSERT INTO shots (id, device_id, raw_slog, index_rating) VALUES (1, '000001', x'00', 5)"
    )
    row = await db.fetch_one(
        f"""
        SELECT ({rating_band_sql(j="jj", n="nn", s="sh")}) AS band FROM shots sh
          LEFT JOIN shot_judgements jj ON jj.shot_id = sh.id
          LEFT JOIN device_shot_notes nn ON nn.shot_id = sh.id
        """  # noqa: S608 - the only interpolation is the band expression, a constant
    )

    assert row is not None and row["band"] == "good"


async def test_a_label_is_a_band_of_a_shot_that_was_not_discarded_or_the_discarded(
    db: Database,
) -> None:
    """Four shots: rated 5 and discarded, rated 5, rated 3, rated 1 and discarded."""
    for index, (rating, discarded) in enumerate(((5, 1), (5, 0), (3, 0), (1, 1)), start=1):
        await db.execute(
            "INSERT INTO shots (id, device_id, raw_slog) VALUES (?, ?, x'00')",
            (index, f"{index:06d}"),
        )
        await db.execute(
            "INSERT INTO shot_judgements (shot_id, rating, discarded) VALUES (?, ?, ?)",
            (index, rating, discarded),
        )

    async def found(label: str) -> list[int]:
        rows = await db.fetch_all(
            f"""
            SELECT s.id FROM shots s
              LEFT JOIN shot_judgements j ON j.shot_id = s.id
              LEFT JOIN device_shot_notes n ON n.shot_id = s.id
             WHERE {shot_label_sql(label)} ORDER BY s.id
            """  # noqa: S608 - the label is one of four literals
        )
        return [int(row["id"]) for row in rows]

    # The precondition: a discarded shot has a band of its own, so the exclusion is what bites.
    bands = await db.fetch_all(
        f"SELECT ({RATING_BAND_SQL}) AS band FROM shots s "  # noqa: S608
        "LEFT JOIN shot_judgements j ON j.shot_id = s.id "
        "LEFT JOIN device_shot_notes n ON n.shot_id = s.id ORDER BY s.id"
    )
    assert [row["band"] for row in bands] == ["good", "good", "neutral", "bad"]
    assert await found("good") == [2]
    assert await found("neutral") == [3]
    assert await found("bad") == []
    assert await found("discard") == [1, 4]
    discarded = await db.fetch_value(
        f"SELECT COUNT(*) FROM shot_judgements j WHERE {not_discarded_sql()}"  # noqa: S608
    )
    assert discarded == 2
