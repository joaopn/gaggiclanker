"""`select_chunks`: which excerpts an analysis gets, and why those.

Three properties, and each of them is load-bearing somewhere else:

* **deterministic**, because the model is asked to cite what it used and a
  citation is only worth reading if the same shot gets the same excerpts twice;
* **budgeted**, because excerpts are supporting context and a retrieval that
  quietly spends ten thousand tokens has made them the prompt;
* **deduped by document**, because two sections of the same guide say much the
  same thing and an analysis given both has heard one opinion twice.
"""

from __future__ import annotations

from dataclasses import replace

from gaggiclanker.knowledge.service import MAX_EXCERPTS, KnowledgeService, RetrievalContext

CHANNELED = RetrievalContext(
    style="bloom",
    signals=(
        "channeling_risk:HIGH",
        "flow_adherence:GOOD",
        "primary:pressure_cliff",
        "taste:sour",
        "taste:bitter",
        "taste:sour_and_bitter",
        "style:bloom",
    ),
    taste_tags=("sour", "bitter"),
    balance="sour",
    roast_level="light",
    process="natural",
)


async def test_queries_are_built_in_a_fixed_priority_order(
    seeded_docs: KnowledgeService,
) -> None:
    queries = seeded_docs.queries_for(CHANNELED)
    assert queries[0].startswith("channeling sour and bitter")
    assert "sour taste cause extraction adjustment" in queries
    assert "channeling pressure cliff" in queries
    assert "channeling risk HIGH" in queries
    # The bean and the style are background, and come last.
    assert queries.index("natural processing extraction pressure temperature") > queries.index(
        "channeling pressure cliff"
    )
    assert queries[-1] == "bloom shot profile"


async def test_a_normal_band_produces_no_query(seeded_docs: KnowledgeService) -> None:
    """Prose about the case where nothing went wrong is the one thing not needed."""
    queries = seeded_docs.queries_for(
        RetrievalContext(signals=("flow_adherence:GOOD", "temperature_stability:STABLE"))
    )
    assert queries == []


async def test_queries_are_deduplicated(seeded_docs: KnowledgeService) -> None:
    context = RetrievalContext(taste_tags=("sour", "sour"), balance=None)
    assert seeded_docs.queries_for(context) == ["sour taste cause fix"]


async def test_the_same_context_selects_the_same_excerpts_twice(
    seeded_docs: KnowledgeService,
) -> None:
    first = await seeded_docs.select_chunks(CHANNELED)
    second = await seeded_docs.select_chunks(CHANNELED)
    assert [excerpt.heading_path for excerpt in first] == [
        excerpt.heading_path for excerpt in second
    ]
    assert first


async def test_the_budget_is_respected(seeded_docs: KnowledgeService) -> None:
    for budget in (300, 800, 1500, 4000):
        chosen = await seeded_docs.select_chunks(CHANNELED, token_budget=budget)
        assert sum(excerpt.tokens_estimate for excerpt in chosen) <= budget


async def test_a_bigger_budget_never_loses_the_first_excerpt(
    seeded_docs: KnowledgeService,
) -> None:
    """Greedy over a stable order, so raising the budget only adds."""
    small = await seeded_docs.select_chunks(CHANNELED, token_budget=600)
    large = await seeded_docs.select_chunks(CHANNELED, token_budget=6000)
    assert small
    assert {excerpt.heading_path for excerpt in small} <= {
        excerpt.heading_path for excerpt in large
    }


async def test_a_zero_budget_turns_retrieval_off(seeded_docs: KnowledgeService) -> None:
    assert await seeded_docs.select_chunks(CHANNELED, token_budget=0) == []


async def test_at_most_one_excerpt_per_document(seeded_docs: KnowledgeService) -> None:
    chosen = await seeded_docs.select_chunks(CHANNELED, token_budget=20_000)
    slugs = [excerpt.doc_slug for excerpt in chosen]
    assert len(slugs) == len(set(slugs))


async def test_the_excerpt_ceiling_holds_whatever_the_budget(
    seeded_docs: KnowledgeService,
) -> None:
    chosen = await seeded_docs.select_chunks(CHANNELED, token_budget=1_000_000)
    assert len(chosen) <= MAX_EXCERPTS


async def test_a_context_with_nothing_in_it_retrieves_nothing(
    seeded_docs: KnowledgeService,
) -> None:
    assert await seeded_docs.select_chunks(RetrievalContext()) == []


async def test_a_taste_context_reaches_the_tasting_guide(
    seeded_docs: KnowledgeService,
) -> None:
    """The one end-to-end assertion: the right document for the right shot."""
    chosen = await seeded_docs.select_chunks(CHANNELED)
    assert "ESPRESSO_TASTING_GUIDE" in {excerpt.doc_slug for excerpt in chosen}
    assert all(excerpt.query for excerpt in chosen)
    assert all(excerpt.heading_path.startswith(f"{excerpt.doc_slug}#") for excerpt in chosen)


async def test_extra_queries_are_appended_for_the_chat(
    seeded_docs: KnowledgeService,
) -> None:
    """The chat builds the same record with its own question in `extra_queries`."""
    queries = seeded_docs.queries_for(RetrievalContext(extra_queries=("how do I steam milk",)))
    assert queries == ["how do I steam milk"]
    chosen = await seeded_docs.select_chunks(
        RetrievalContext(extra_queries=("how do I steam milk",))
    )
    assert "MILK_AND_DRINKS" in {excerpt.doc_slug for excerpt in chosen}


#: A dial-in context that has nothing to do with milk: a dark washed bean, a
#: turbo shot, a bitter cup. Every background query pulls towards pressure,
#: grind and roast temperature.
DIALLING_IN = RetrievalContext(
    style="turbo",
    signals=("resistance_level:LOW", "taste:bitter", "style:turbo"),
    taste_tags=("bitter",),
    balance="bitter",
    roast_level="dark",
    process="washed",
)


async def test_a_caller_s_own_question_leads_the_query_list(
    seeded_docs: KnowledgeService,
) -> None:
    """The chat asks about milk in the middle of a dial-in conversation.

    The merge takes each query's best hit before any query's second and the
    budget is spent greedily over that, so a question appended after the
    background queries is a question whose answer they have already paid for.
    """
    asked = replace(DIALLING_IN, extra_queries=("how do I steam milk for a flat white",))
    assert seeded_docs.queries_for(asked)[0].startswith("how do I steam milk")

    chosen = await seeded_docs.select_chunks(asked)
    assert chosen
    assert chosen[0].doc_slug == "MILK_AND_DRINKS"


async def test_without_the_question_the_same_context_finds_no_milk(
    seeded_docs: KnowledgeService,
) -> None:
    """The control for the test above: the lead position is what did the work."""
    chosen = await seeded_docs.select_chunks(DIALLING_IN)
    assert "MILK_AND_DRINKS" not in {excerpt.doc_slug for excerpt in chosen}


async def test_a_citation_can_be_expanded_back_into_its_passage(
    seeded_docs: KnowledgeService,
) -> None:
    """What the chat calls to answer "what does that excerpt actually say"."""
    chosen = await seeded_docs.select_chunks(CHANNELED)
    paths = [excerpt.heading_path for excerpt in chosen]

    one = await seeded_docs.get_chunk(paths[0])
    assert one is not None
    assert one.heading_path == paths[0]
    assert one.body

    # Order preserved, and a path that no longer resolves is dropped rather
    # than returned as a hole.
    many = await seeded_docs.get_chunks([*reversed(paths), "GONE#nowhere"])
    assert [chunk.heading_path for chunk in many] == list(reversed(paths))
    assert await seeded_docs.get_chunk("GONE#nowhere") is None
    assert await seeded_docs.get_chunks([]) == []


async def test_a_taste_query_does_not_carry_the_word_espresso(
    seeded_docs: KnowledgeService,
) -> None:
    """The most common word in the corpus discriminates nothing.

    With it in the query, a chunk that merely repeats it — the drinks
    specification table — outranked the passage that explains what a thin shot
    means, and an analysis of a sour bloom shot was handed milk drink formats.
    """
    queries = seeded_docs.queries_for(RetrievalContext(taste_tags=("thin",), balance=None))
    assert queries == ["thin taste cause fix"]

    chosen = await seeded_docs.select_chunks(
        RetrievalContext(taste_tags=("sour", "thin"), balance="sour", process="natural")
    )
    assert "MILK_AND_DRINKS" not in {excerpt.doc_slug for excerpt in chosen}
    assert "ESPRESSO_TASTING_GUIDE" in {excerpt.doc_slug for excerpt in chosen}
