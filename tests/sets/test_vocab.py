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
from typing import get_args

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.domain.models import BalanceTaste
from gaggiclanker.domain.spread import MEASURE_FLOORS
from gaggiclanker.domain.vocab import (
    ACTIONABLE_VARIABLES,
    BALANCES,
    BURR_TYPES,
    DECISIONS,
    FLAVOR_LABELS,
    FLAVOR_NOTES,
    FLAVOR_PICK_KINDS,
    FLAVOR_WHEEL,
    MEASURE_DECIMALS,
    MEASURE_DIFFERENCE_DECIMALS,
    OUTCOME_STATES,
    PROCESSES,
    ROAST_LEVELS,
    SET_VERSION_ORIGINS,
    SPREAD_MEASURES,
    STEP_UNITS,
    VERSION_OUTCOMES,
    SuggestionVariable,
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


async def test_a_set_version_has_no_temperature_and_the_vocabulary_agrees(
    db: Database,
) -> None:
    """The three actionable variables are the three columns there are.

    Two halves of one fact, and they are checked together because drifting apart
    is what would hurt: `ACTIONABLE_VARIABLES` is what `accept` consults before
    writing, so a variable listed here with no column behind it would be an
    accept that raises instead of refusing politely. The temperature has no
    column because the machine brews at the profile's.
    """
    schema = await _schema(db, "set_versions")
    assert "target_temperature_c" not in schema

    columns = {str(row["name"]) for row in await db.fetch_all("PRAGMA table_info(set_versions)")}
    assert {"grind_value", "dose_g", "target_yield_g"} <= columns
    assert ACTIONABLE_VARIABLES == ("grind", "dose", "yield")
    assert set(ACTIONABLE_VARIABLES) < set(get_args(SuggestionVariable.__value__))


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


def test_the_served_vocabulary_says_which_variables_can_be_accepted() -> None:
    """The suggestion card offers Accept for exactly these, and nothing else.

    Served rather than typed in the front end, because the server refuses an
    accept for anything outside the list and a card working from its own copy
    turns good advice into a 409 — which is what happened when the temperature
    became a profile change.
    """
    assert vocabulary().actionable_variables == list(ACTIONABLE_VARIABLES)
    assert "temperature" not in vocabulary().actionable_variables
    # Every one of them is a variable a suggestion can be about.
    variables = {term.value for term in vocabulary().suggestion_variables}
    assert set(vocabulary().actionable_variables) <= variables


def test_every_spread_measure_is_served_with_its_words_and_its_unit() -> None:
    """The Set page writes "Shot time ±1.8 s" out of these three fields.

    Served rather than typed in the front end for the same reason as every
    other vocabulary here, and with the unit as its own field: a label of
    "Shot time (s)" would put the unit in the wrong half of that sentence and
    of "held against 2.4 s".
    """
    served = vocabulary().spread_measures

    assert [term.value for term in served] == list(SPREAD_MEASURES)
    assert [term.label for term in served][:2] == ["Shot time", "Time to first drip"]
    assert {term.value: term.unit for term in served}["peak_pressure_bar"] == "bar"
    # The rating is a number of stars, not a quantity.
    assert {term.value: term.unit for term in served}["rating"] == ""
    # Every measure the arithmetic knows a floor for is served, and no other.
    assert {term.value for term in served} == set(MEASURE_FLOORS)


def test_a_measure_says_how_a_mean_and_a_difference_are_written() -> None:
    """One decimal for seconds and grams, two for bar and ml/s — and one more
    for a difference and a yardstick.

    Served rather than decided in the front end: the server rounds what it
    serves, and a page formatting to its own precision would either invent
    digits or hide the one that decided a verdict. The extra decimal is what
    keeps "+2.04 s, beyond 2.00 s" from reading as "+2.0 s, beyond 2.0 s".
    """
    served = {term.value: term for term in vocabulary().spread_measures}

    assert (served["shot_time_s"].decimals, served["shot_time_s"].difference_decimals) == (1, 2)
    assert (
        served["peak_pressure_bar"].decimals,
        served["peak_pressure_bar"].difference_decimals,
    ) == (
        2,
        3,
    )
    assert all(
        term.difference_decimals == term.decimals + 1 for term in vocabulary().spread_measures
    )
    assert {value: term.decimals for value, term in served.items()} == MEASURE_DECIMALS
    assert MEASURE_DIFFERENCE_DECIMALS == {
        measure: decimals + 1 for measure, decimals in MEASURE_DECIMALS.items()
    }


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
