"""The vocabularies, and the three places each of them has to agree.

`gaggiclanker/domain/vocab.py` claims to be the single source for a set of words
that also appears as a SQL CHECK constraint and as an OpenAPI enum. This file is
what makes that claim checkable: it reads the database's own schema back out of
`sqlite_master` and compares it with the module.

Without it, adding a value to the module and forgetting the migration produces a
422 the API accepts and an IntegrityError the repository does not, which is a
confusing afternoon.
"""

from __future__ import annotations

import re

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.domain.models import BalanceTaste
from gaggiclanker.domain.vocab import (
    BALANCES,
    BURR_TYPES,
    DECISIONS,
    PROCESSES,
    ROAST_LEVELS,
    SET_VERSION_ORIGINS,
    STEP_UNITS,
    TASTE_GROUPS,
    TASTE_TAGS,
    vocabulary,
)

#: (table, column, the tuple it must match).
CHECKED: list[tuple[str, str, tuple[str, ...]]] = [
    ("beans", "process", PROCESSES),
    ("beans", "roast_level", ROAST_LEVELS),
    ("grinders", "burr_type", BURR_TYPES),
    ("grinders", "step_unit", STEP_UNITS),
    ("set_versions", "origin", SET_VERSION_ORIGINS),
    ("shot_judgements", "balance", BALANCES),
    ("shot_judgements", "decision", DECISIONS),
]


async def _schema(db: Database, table: str) -> str:
    sql = await db.fetch_value(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    )
    assert sql is not None, f"no table {table}"
    return str(sql)


@pytest.mark.parametrize(("table", "column", "expected"), CHECKED, ids=lambda value: str(value))
async def test_the_check_constraint_matches_the_module(
    db: Database, table: str, column: str, expected: tuple[str, ...]
) -> None:
    schema = await _schema(db, table)
    # The CHECK for this column, wherever it sits in the CREATE TABLE. Matched
    # on the column name so a second constraint on the same table cannot pass
    # for this one.
    match = re.search(rf"{column}\s+TEXT[^,]*?CHECK\s*\((.*?)\)\s*\)", schema, re.DOTALL)
    assert match, f"no CHECK on {table}.{column} in:\n{schema}"
    in_schema = set(re.findall(r"'([^']+)'", match.group(1)))
    assert in_schema == set(expected), f"{table}.{column} disagrees with domain/vocab.py"


async def test_balance_is_the_same_three_words_the_firmware_uses() -> None:
    """Seeding a judgement from a device note has to be a copy, not a mapping.

    `ShotNotes.balance_taste` is the machine's own field; if these two ever
    stopped agreeing, the seeding path would need a translation table, and a
    translation table is where a value quietly becomes 'balanced'.
    """
    from typing import get_args

    assert set(get_args(BalanceTaste)) == set(BALANCES)


def test_every_taste_tag_is_unique_and_grouped() -> None:
    assert len(TASTE_TAGS) == len(set(TASTE_TAGS))
    flattened = [tag.value for group in TASTE_GROUPS for tag in group.tags]
    assert flattened == list(TASTE_TAGS)
    # crema's four groups, with the counts from its own vocabulary.
    assert [len(group.tags) for group in TASTE_GROUPS] == [5, 4, 5, 2]


def test_the_served_vocabulary_carries_every_term() -> None:
    served = vocabulary()
    assert [term.value for term in served.roast_levels] == list(ROAST_LEVELS)
    assert [term.value for term in served.decisions] == list(DECISIONS)
    assert all(term.label for term in served.balances)
    # A fresh object each call: a shared pydantic instance is a mutable thing
    # handed to every request at once.
    assert vocabulary() is not served
