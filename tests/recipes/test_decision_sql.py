"""``DECISION_SQL`` is the Python rule, over every combination of its four inputs.

The rule lives twice, in ``domain/decision.py`` and as a SQL expression the readers
embed, so a test runs the SQL over every combination of a stored decision and a
rating in each of the three places the Rating column reads, and compares it with the
Python. The schema refuses a judgement rating of 0, which a hand-edited file could
still hold, so the rows are written with the check constraints off: the point is
the expression, not what the schema lets in.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import (
    DECISION_RATING_SOURCE_SQL,
    DECISION_RATING_SQL,
    DECISION_SQL,
    decision_sql,
)
from gaggiclanker.db.schema import create_schema
from gaggiclanker.domain.decision import (
    derived_decision,
    effective_decision,
    rating_of,
    rating_source,
)
from gaggiclanker.domain.vocab import Decision

DECISIONS: tuple[Decision | None, ...] = (None, "keep", "improve", "discard")
RATINGS: tuple[int | None, ...] = (None, 0, 1, 2, 3, 5)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "decision.db")
    await database.connect()
    await create_schema(database)
    try:
        yield database
    finally:
        await database.close()


async def test_the_sql_agrees_with_the_python_over_every_combination(db: Database) -> None:
    await db.execute("PRAGMA ignore_check_constraints = ON")
    combos = list(itertools.product(DECISIONS, RATINGS, RATINGS, RATINGS))
    for index, (decision, judged, noted, indexed) in enumerate(combos, start=1):
        await db.execute(
            "INSERT INTO shots (id, device_id, raw_slog, index_rating) VALUES (?, ?, ?, ?)",
            (index, f"{index:06d}", b"x", indexed),
        )
        await db.execute(
            "INSERT INTO shot_judgements (shot_id, decision, rating) VALUES (?, ?, ?)",
            (index, decision, judged),
        )
        await db.execute(
            "INSERT INTO device_shot_notes (shot_id, raw_json, rating) VALUES (?, '{}', ?)",
            (index, noted),
        )
    rows = await db.fetch_all(
        f"""
        SELECT s.id AS id, ({DECISION_SQL}) AS decision,
               ({DECISION_RATING_SQL}) AS rating, ({DECISION_RATING_SOURCE_SQL}) AS source
          FROM shots s
          LEFT JOIN shot_judgements j ON j.shot_id = s.id
          LEFT JOIN device_shot_notes n ON n.shot_id = s.id
        """  # noqa: S608 - the only interpolation is the decision expression, a constant
    )

    assert len(rows) == len(combos) == 4 * 6**3
    for row in rows:
        decision, judged, noted, indexed = combos[int(row["id"]) - 1]
        expected = effective_decision(decision, rating_of(judged, noted, indexed))
        assert row["decision"] == expected, (decision, judged, noted, indexed)
        # The rating and where it sits are the rule's own pieces: set exactly when the rating
        # gives a decision, and then the first rating present.
        rating = rating_of(judged, noted, indexed)
        gives = derived_decision(rating) is not None
        assert row["rating"] == (rating if gives else None), (judged, noted, indexed)
        assert row["source"] == (rating_source(judged, noted, indexed) if gives else None), (
            judged,
            noted,
            indexed,
        )


async def test_a_shot_with_no_judgement_row_and_no_notes_row_reads_from_its_index(
    db: Database,
) -> None:
    for index, indexed in enumerate((None, 0, 2, 4), start=1):
        await db.execute(
            "INSERT INTO shots (id, device_id, raw_slog, index_rating) VALUES (?, ?, ?, ?)",
            (index, f"{index:06d}", b"x", indexed),
        )
    rows = await db.fetch_all(
        f"""
        SELECT ({decision_sql()}) AS decision FROM shots s
          LEFT JOIN shot_judgements j ON j.shot_id = s.id
          LEFT JOIN device_shot_notes n ON n.shot_id = s.id
         ORDER BY s.id
        """  # noqa: S608 - the only interpolation is the decision expression, a constant
    )

    assert [row["decision"] for row in rows] == [None, None, "improve", "keep"]


async def test_the_aliases_are_a_parameter(db: Database) -> None:
    await db.execute(
        "INSERT INTO shots (id, device_id, raw_slog, index_rating) VALUES (1, '000001', x'00', 5)"
    )
    row = await db.fetch_one(
        f"""
        SELECT ({decision_sql(j="jj", n="nn", s="sh")}) AS decision FROM shots sh
          LEFT JOIN shot_judgements jj ON jj.shot_id = sh.id
          LEFT JOIN device_shot_notes nn ON nn.shot_id = sh.id
        """  # noqa: S608 - the only interpolation is the decision expression, a constant
    )

    assert row is not None and row["decision"] == "keep"
