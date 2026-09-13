"""Tier 3: scope matching, confirmation, and what never reaches a prompt.

The rule under test is one sentence — an insight applies when **every** key its
scope states matches the Set — and the tests are mostly the ways that sentence
can be got wrong: a partial scope that should still match, an attribute the Set
does not state that must *not* match, and a scope key nobody recognises.
"""

from __future__ import annotations

from gaggiclanker.db.repos.knowledge_insights import (
    InsightScope,
    InsightsRepository,
    InsightWrite,
    scope_matches,
)
from gaggiclanker.knowledge.service import KnowledgeService

GUJI = {
    "bean_id": 7,
    "roast_level": "light",
    "process": "natural",
    "origin": "Ethiopia",
    "grinder_id": 2,
    "profile_style": "bloom",
}


def test_an_empty_scope_matches_everything() -> None:
    assert scope_matches({}, GUJI)
    assert scope_matches(None, GUJI)
    assert scope_matches({}, {})


def test_a_partial_scope_matches_on_the_keys_it_states() -> None:
    assert scope_matches({"process": "natural"}, GUJI)
    assert scope_matches({"grinder_id": 2, "process": "natural"}, GUJI)


def test_every_stated_key_must_match() -> None:
    """No partial credit: a rule that half-applies is one nobody can predict."""
    assert not scope_matches({"grinder_id": 2, "process": "washed"}, GUJI)
    assert not scope_matches({"grinder_id": 99}, GUJI)


def test_an_attribute_the_set_does_not_state_never_matches() -> None:
    """Saying nothing beats reasoning from a guess — the tier 1 rule, again."""
    unknown = {**GUJI, "process": None}
    assert not scope_matches({"process": "natural"}, unknown)
    assert scope_matches({"roast_level": "light"}, unknown)


def test_strings_compare_trimmed_and_case_insensitively() -> None:
    """`origin` is typed by hand into a bean form."""
    assert scope_matches({"origin": "ethiopia"}, GUJI)
    assert scope_matches({"origin": " Ethiopia "}, GUJI)
    assert not scope_matches({"origin": "Kenya"}, GUJI)


def test_an_unknown_scope_key_never_fires() -> None:
    """The safe direction: ignoring it would widen the scope the user confirmed."""
    assert not scope_matches({"bean_variety": "heirloom"}, GUJI)


def test_the_scope_label_is_stable_and_readable() -> None:
    scope = InsightScope(grinder_id=2, process="natural")
    assert scope.label() == "process=natural · grinder_id=2"
    assert InsightScope().label() == "any shot"


async def test_crud_round_trip(db) -> None:  # type: ignore[no-untyped-def]
    repo = InsightsRepository(db)
    insight_id = await repo.insert(
        InsightWrite(
            scope=InsightScope(grinder_id=2),
            text="  Two   clicks finer   for naturals.  ",
            evidence_shot_ids=[9, 3, 3],
            source="analysis",
            analysis_id=None,
        )
    )
    stored = await repo.get(insight_id)
    assert stored is not None
    # Whitespace collapsed, evidence sorted and deduplicated.
    assert stored.text == "Two clicks finer for naturals."
    assert stored.evidence_shot_ids == [3, 9]
    assert stored.confirmed is False
    assert stored.confirmed_at is None

    await repo.update(insight_id, text="Three clicks.", scope=InsightScope(process="natural"))
    edited = await repo.get(insight_id)
    assert edited is not None
    assert edited.text == "Three clicks."
    assert edited.scope.stated() == {"process": "natural"}

    assert await repo.set_confirmed(insight_id, True)
    confirmed = await repo.get(insight_id)
    assert confirmed is not None
    assert confirmed.confirmed is True
    assert confirmed.confirmed_at

    assert await repo.set_confirmed(insight_id, False)
    unconfirmed = await repo.get(insight_id)
    assert unconfirmed is not None
    # Cleared, because a date under an unconfirmed row reads as a contradiction.
    assert unconfirmed.confirmed_at is None

    assert await repo.delete(insight_id)
    assert await repo.get(insight_id) is None
    assert not await repo.delete(insight_id)


async def test_selection_takes_only_confirmed_insights(knowledge: KnowledgeService) -> None:
    """The safety property: nothing unconfirmed ever reaches a prompt."""
    repo = knowledge.insights
    await repo.insert(
        InsightWrite(scope=InsightScope(process="natural"), text="Confirmed.", confirmed=True)
    )
    await repo.insert(
        InsightWrite(scope=InsightScope(process="natural"), text="Proposed.", confirmed=False)
    )
    selected = await knowledge.select_insights(GUJI)
    assert [insight.text for insight in selected] == ["Confirmed."]


async def test_selection_is_oldest_first_and_deterministic(
    knowledge: KnowledgeService,
) -> None:
    for index in range(4):
        await knowledge.insights.insert(InsightWrite(text=f"Insight {index}.", confirmed=True))
    first = await knowledge.select_insights(GUJI)
    second = await knowledge.select_insights(GUJI)
    assert [item.id for item in first] == [item.id for item in second]
    assert [item.text for item in first] == [f"Insight {index}." for index in range(4)]


async def test_selection_filters_by_scope(knowledge: KnowledgeService) -> None:
    repo = knowledge.insights
    await repo.insert(
        InsightWrite(scope=InsightScope(grinder_id=2), text="This grinder.", confirmed=True)
    )
    await repo.insert(
        InsightWrite(scope=InsightScope(grinder_id=3), text="A different one.", confirmed=True)
    )
    await repo.insert(InsightWrite(text="Everything.", confirmed=True))
    texts = [insight.text for insight in await knowledge.select_insights(GUJI)]
    assert texts == ["This grinder.", "Everything."]


async def test_listing_filters_by_confirmation_and_analysis(
    knowledge: KnowledgeService,
) -> None:
    repo = knowledge.insights
    await repo.insert(InsightWrite(text="A.", confirmed=True))
    await repo.insert(InsightWrite(text="B.", confirmed=False))
    assert [item.text for item in await repo.list_insights(confirmed=True)] == ["A."]
    assert [item.text for item in await repo.list_insights(confirmed=False)] == ["B."]
    assert len(await repo.list_insights()) == 2
    assert await repo.count(confirmed=False) == 1


async def test_a_stored_scope_with_an_unknown_key_does_not_take_the_row_down(
    knowledge: KnowledgeService,
) -> None:
    """A downgrade shows a narrower insight rather than a 500."""
    await knowledge.db.execute(
        "INSERT INTO knowledge_insights (scope_json, text, source, confirmed) "
        "VALUES (?, ?, 'user', 1)",
        ('{"process": "natural", "bean_variety": "heirloom"}', "From the future."),
    )
    rows = await knowledge.insights.list_insights()
    assert [row.scope.stated() for row in rows] == [{"process": "natural"}]
