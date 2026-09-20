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
    FLAVOR_LABELS,
    FLAVOR_NOTES,
    FLAVOR_PICK_KINDS,
    FLAVOR_WHEEL,
    OUTCOME_STATES,
    PROCESSES,
    ROAST_LEVELS,
    SET_VERSION_ORIGINS,
    STEP_UNITS,
    VERSION_OUTCOMES,
    flavor_ancestors,
    flavor_path,
    in_wheel_order,
    vocabulary,
)

#: (table, column, the tuple it must match).
CHECKED: list[tuple[str, str, tuple[str, ...]]] = [
    ("beans", "process", PROCESSES),
    ("beans", "roast_level", ROAST_LEVELS),
    ("grinders", "burr_type", BURR_TYPES),
    ("grinders", "step_unit", STEP_UNITS),
    ("set_versions", "origin", SET_VERSION_ORIGINS),
    ("set_versions", "outcome", VERSION_OUTCOMES),
    ("shot_judgements", "balance", BALANCES),
    ("shot_judgements", "decision", DECISIONS),
    ("flavor_picks", "kind", FLAVOR_PICK_KINDS),
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


def test_the_wheel_is_the_whole_sca_wheel_with_unique_slugs() -> None:
    """Nine categories, 28 groups, 73 notes: the 2016 wheel, all three tiers."""
    assert len(FLAVOR_WHEEL) == 9
    assert sum(len(category.children) for category in FLAVOR_WHEEL) == 28
    leaves = [
        leaf.value
        for category in FLAVOR_WHEEL
        for group in category.children
        for leaf in group.children
    ]
    assert len(leaves) == 73
    assert len(FLAVOR_NOTES) == 110
    assert len(set(FLAVOR_NOTES)) == len(FLAVOR_NOTES)
    # Nothing deeper than three tiers, and every slug is its parent's plus one step.
    for category in FLAVOR_WHEEL:
        for group in category.children:
            assert group.value.startswith(f"{category.value}.")
            for leaf in group.children:
                assert leaf.value.startswith(f"{group.value}.")
                assert leaf.children == []


def test_a_slug_is_the_path_of_its_labels() -> None:
    # The category and the group of the same name are two notes, not one.
    assert FLAVOR_LABELS["floral"] == FLAVOR_LABELS["floral.floral"] == "Floral"
    assert FLAVOR_LABELS["other.papery_musty.moldy_damp"] == "Moldy/Damp"
    assert flavor_ancestors("sour_fermented.sour.acetic_acid") == (
        "sour_fermented",
        "sour_fermented.sour",
    )
    assert flavor_ancestors("sweet") == ()
    assert flavor_path("fruity.berry.blackberry") == "Fruity › Berry › Blackberry"


def test_wheel_order_is_centre_first_and_clockwise() -> None:
    assert FLAVOR_NOTES[:3] == ("floral", "floral.black_tea", "floral.floral")
    assert FLAVOR_NOTES[-1] == "sweet.sweet_aromatics"
    assert in_wheel_order(["sweet", "floral.floral.rose", "sweet", "floral"]) == [
        "floral",
        "floral.floral.rose",
        "sweet",
    ]


def test_the_served_vocabulary_carries_every_term() -> None:
    served = vocabulary()
    assert [term.value for term in served.roast_levels] == list(ROAST_LEVELS)
    assert [term.value for term in served.decisions] == list(DECISIONS)
    # Six states are rendered and only four can be recorded: `open` and
    # `no_prediction` are what a version looks like, not grades anybody gave.
    assert [term.value for term in served.version_outcomes] == list(VERSION_OUTCOMES)
    assert [term.value for term in served.outcome_states] == list(OUTCOME_STATES)
    assert [term.label for term in served.outcome_states][:2] == ["No prediction", "Open"]
    assert all(term.label for term in served.balances)
    # A fresh object each call: a shared pydantic instance is a mutable thing
    # handed to every request at once.
    assert vocabulary() is not served
