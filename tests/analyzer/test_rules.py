"""The rule tier: what the seed produces, and what selection picks out of it.

The first acceptance criterion for the analyzer is that two analyses of the same shot
select the same rules. That is a property of two things — a total sort key and a
filter with no iteration order in it — and both are asserted here rather than
inferred from the analyzer producing the same text twice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.domain.vocab import RULE_CATEGORIES
from gaggiclanker.knowledge.rules import (
    RuleSeedError,
    SetContext,
    load_seed_rules,
    seed_rules,
    select_rules,
)

LIGHT_NATURAL = SetContext(roast_level="light", process="natural", burr_type="conical")


def test_the_shipped_seed_parses() -> None:
    rules = load_seed_rules()
    assert len(rules) > 100, "the condensed heuristics are the whole point of the tier"
    assert {rule.category for rule in rules} <= set(RULE_CATEGORIES)
    # Every shipped rule carries prose. The rendered fallback exists for a rule
    # somebody wrote through the API, not for one we ship.
    assert all(rule.value.get("text") for rule in rules)
    assert all(rule.source for rule in rules), "provenance is shown in the UI"


def test_every_category_is_covered() -> None:
    """A category with no rules is a category nothing can ever select."""
    covered = {rule.category for rule in load_seed_rules()}
    assert covered == set(RULE_CATEGORIES)


def test_a_typo_in_applies_is_rejected(tmp_path: Path) -> None:
    """A rule that filters on `roast` instead of `roast_level` matches everything.

    The output would still look plausible, which is why this is an error at
    load time rather than something to notice in an analysis six weeks later.
    """
    bad = tmp_path / "rules.yaml"
    bad.write_text(
        "rules:\n"
        "  - category: temperature_by_roast\n"
        "    key: oops\n"
        "    applies: {roast: [light]}\n"
        "    value: {text: nope}\n"
    )
    with pytest.raises(RuleSeedError, match="roast"):
        load_seed_rules(bad)


async def test_seeding_is_idempotent(db: Database) -> None:
    repo = RulesRepository(db)
    first = await seed_rules(repo)
    assert first == len(load_seed_rules())
    assert await seed_rules(repo) == 0, "a steady-state boot touches nothing"
    assert await repo.count() == first


async def test_an_edited_rule_keeps_its_text_through_a_reseed(db: Database, tmp_path: Path) -> None:
    """The property that makes editing a rule safe to do."""
    repo = RulesRepository(db)
    await seed_rules(repo)
    rule = await repo.get_by_key("temperature_by_roast", "light")
    assert rule is not None
    await repo.set_value(rule.id, {"text": "mine", "min_c": 95, "max_c": 97})

    # A later image ships a different default for the same rule.
    newer = tmp_path / "rules.yaml"
    newer.write_text(
        "rules:\n"
        "  - category: temperature_by_roast\n"
        "    key: light\n"
        "    value: {text: theirs, min_c: 93, max_c: 96}\n"
        "    unit: c\n"
        "    source: upstream\n"
    )
    await seed_rules(repo, newer)

    stored = await repo.get_by_key("temperature_by_roast", "light")
    assert stored is not None
    assert stored.text == "mine", "an upgrade must not take an edit back"
    assert stored.edited is True
    assert stored.source == "upstream", (
        "provenance still moves: it describes the rule, not the text"
    )


async def test_selection_is_deterministic(db: Database) -> None:
    """Same inputs, same ids, same order — twice."""
    repo = RulesRepository(db)
    await seed_rules(repo)
    signals = {"channeling_risk:HIGH", "taste:sour", "first_drip:fast"}

    first = await select_rules(repo, LIGHT_NATURAL, "bloom", signals)
    second = await select_rules(repo, LIGHT_NATURAL, "bloom", set(signals))

    assert [rule.id for rule in first.rules] == [rule.id for rule in second.rules]
    assert [rule.key for rule in first.rules] == [rule.key for rule in second.rules]
    assert first.render() == second.render()
    assert first.signals == second.signals


async def test_selection_is_ordered_by_category_then_key(db: Database) -> None:
    repo = RulesRepository(db)
    await seed_rules(repo)
    selection = await select_rules(repo, LIGHT_NATURAL, "classic", set())

    ranks = [RULE_CATEGORIES.index(rule.category) for rule in selection.rules]
    assert ranks == sorted(ranks), "categories come out in the order the prompt lists them"
    for category in {rule.category for rule in selection.rules}:
        keys = [rule.key for rule in selection.rules if rule.category == category]
        assert keys == sorted(keys)


async def test_a_rule_filters_on_every_stated_dimension(db: Database) -> None:
    repo = RulesRepository(db)
    await seed_rules(repo)

    natural = await select_rules(repo, LIGHT_NATURAL, "classic", set())
    washed = await select_rules(
        repo, SetContext(roast_level="light", process="washed"), "classic", set()
    )
    assert "natural.light" in natural.keys
    assert "natural.light" not in washed.keys
    assert "washed.light" in washed.keys

    # Style is a dimension too: a turbo rule must not reach a classic shot.
    turbo = await select_rules(repo, LIGHT_NATURAL, "turbo", set())
    assert "turbo" in {rule.key for rule in turbo.rules if rule.category == "ratio_by_style"}
    assert "turbo" not in {rule.key for rule in natural.rules if rule.category == "ratio_by_style"}


async def test_an_unstated_dimension_matches_nothing_that_filters_on_it(db: Database) -> None:
    """A bag whose roaster printed no process gets no pressure-matrix cell.

    Saying nothing is better than reasoning from a guess, and a fallback cell
    would be a claim about a bean nobody made.
    """
    repo = RulesRepository(db)
    await seed_rules(repo)
    selection = await select_rules(repo, SetContext(), "classic", set())
    assert not [
        rule for rule in selection.rules if rule.category == "pressure_matrix" and rule.applies
    ]


async def test_signal_rules_need_their_signal(db: Database) -> None:
    repo = RulesRepository(db)
    await seed_rules(repo)

    without = await select_rules(repo, LIGHT_NATURAL, "classic", set())
    with_signal = await select_rules(repo, LIGHT_NATURAL, "classic", {"channeling_risk:HIGH"})

    assert "channeling_risk:HIGH" not in without.keys
    assert "channeling_risk:HIGH" in with_signal.keys


async def test_the_channeling_rule_needs_both_sides_of_the_cup(db: Database) -> None:
    """Sour AND bitter is channeling; sour alone is not, and the advice differs.

    The channeling rule says "do NOT grind finer"; the sour rule one line above
    says grind finer first. Firing both on a plainly sour cup would hand the
    model a contradiction and let it pick.
    """
    repo = RulesRepository(db)
    await seed_rules(repo)

    sour_only = await select_rules(repo, LIGHT_NATURAL, "classic", {"taste:sour", "balance:sour"})
    assert "sour" in sour_only.keys
    assert "sour_and_bitter_is_channeling" not in sour_only.keys

    both = await select_rules(
        repo, LIGHT_NATURAL, "classic", {"taste:sour", "taste:bitter", "taste:sour_and_bitter"}
    )
    assert "sour_and_bitter_is_channeling" in both.keys


async def test_a_disabled_rule_leaves_the_next_analysis(db: Database) -> None:
    repo = RulesRepository(db)
    await seed_rules(repo)
    rule = await repo.get_by_key("dial_in_order", "hierarchy")
    assert rule is not None

    assert "hierarchy" in (await select_rules(repo, LIGHT_NATURAL, "classic", set())).keys
    await repo.set_enabled(rule.id, False)
    assert "hierarchy" not in (await select_rules(repo, LIGHT_NATURAL, "classic", set())).keys


async def test_procedure_rules_always_apply(db: Database) -> None:
    """Dial-in order, increments and safety bounds are not facts about a bean."""
    repo = RulesRepository(db)
    await seed_rules(repo)
    selection = await select_rules(repo, SetContext(), "unknown", set())
    categories = {rule.category for rule in selection.rules}
    assert {"dial_in_order", "increments", "safety_bounds"} <= categories
