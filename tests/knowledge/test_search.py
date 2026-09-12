"""FTS5 search: stemming, the heading weight, filters and a stable order.

The search is the SQL — there is no Python ranking pass — so these tests are
about the index and the query expression rather than about a scoring function.
The two that would be easy to lose in a refactor are the porter stemming (which
is most of the value at this corpus size) and the deterministic tie-break
(without which the analyzer's retrieval stops being reproducible).
"""

from __future__ import annotations

from gaggiclanker.db.repos.knowledge_docs import match_expression
from gaggiclanker.knowledge.service import KnowledgeService


async def test_porter_stemming_finds_the_other_form_of_the_word(
    small: KnowledgeService,
) -> None:
    """ "channeling" finds "channel", which unstemmed matching would not."""
    hits = await small.search_chunks("channel", k=5)
    assert any(hit.chunk.doc_slug == "CHANNELING" for hit in hits)
    assert any("channeling" in hit.chunk.body.lower() for hit in hits)

    extracted = await small.search_chunks("extracting", k=5)
    assert any("extract" in hit.chunk.body.lower() for hit in extracted)


async def test_a_heading_match_outranks_a_passing_mention(small: KnowledgeService) -> None:
    """The ten-to-one column weight, seen from the outside.

    "Channeling" appears in the body of the sour and bitter documents too. The
    chunk whose *heading* is Channeling is the one that is about it.
    """
    hits = await small.search_chunks("channeling", k=5)
    assert hits[0].chunk.doc_slug == "CHANNELING"


async def test_the_snippet_quotes_the_body_around_the_match(small: KnowledgeService) -> None:
    hits = await small.search_chunks("temperature", k=3)
    assert hits
    assert hits[0].snippet
    assert "\n" not in hits[0].snippet
    assert len(hits[0].snippet) < 300


async def test_scores_are_ordered_best_first(small: KnowledgeService) -> None:
    """BM25 is negative-is-better; it is negated in exactly one place.

    Not "every score is positive": a term that appears in *every* chunk has a
    non-positive IDF and contributes nothing or less, which is BM25 working
    rather than the sign being wrong. What has to hold is the order.
    """
    hits = await small.search_chunks("grind finer coarser", k=5)
    assert len(hits) >= 2
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)
    assert hits[0].score > 0


async def test_the_same_query_gives_the_same_order_twice(seeded_docs: KnowledgeService) -> None:
    """The ORDER BY carries `heading_path` precisely so this holds."""
    first = await seeded_docs.search_chunks("pressure profile decline", k=8)
    second = await seeded_docs.search_chunks("pressure profile decline", k=8)
    assert [hit.chunk.heading_path for hit in first] == [hit.chunk.heading_path for hit in second]


async def test_a_slug_filter_narrows_to_the_named_documents(small: KnowledgeService) -> None:
    hits = await small.search_chunks("grind", k=10, slugs=["BITTER_SHOTS"])
    assert hits
    assert {hit.chunk.doc_slug for hit in hits} == {"BITTER_SHOTS"}


async def test_k_bounds_the_result_count(seeded_docs: KnowledgeService) -> None:
    assert len(await seeded_docs.search_chunks("espresso", k=3)) <= 3
    assert await seeded_docs.search_chunks("espresso", k=0) == []


async def test_fts_syntax_in_the_query_is_words_not_operators(small: KnowledgeService) -> None:
    """A search can never be a syntax error, and never an injection."""
    assert match_expression('sour AND bitter" OR body:x') == (
        '"sour" OR "AND" OR "bitter" OR "OR" OR "body" OR "x"'
    )
    assert match_expression("!!! ???") == ""
    # The whole point: these do not raise.
    assert await small.search_chunks('"; DROP TABLE knowledge_chunks; --', k=3) is not None
    assert await small.search_chunks("!!!", k=3) == []


async def test_a_query_with_no_words_returns_nothing(small: KnowledgeService) -> None:
    assert await small.search_chunks("   ", k=5) == []


async def test_every_hit_carries_its_document_identity(seeded_docs: KnowledgeService) -> None:
    """A chunk with no document beside it cannot be rendered or cited."""
    for hit in await seeded_docs.search_chunks("bloom preinfusion", k=5):
        assert hit.chunk.doc_slug
        assert hit.chunk.doc_title
        assert hit.chunk.heading_path.startswith(f"{hit.chunk.doc_slug}#")


async def test_a_heading_match_wins_where_bm25_has_nothing_to_say(
    seeded_docs: KnowledgeService,
) -> None:
    """The real corpus, where the heading weight alone cannot decide.

    `channeling` appears in more than half the chunks, so its inverse document
    frequency floors at zero and every match scores within a millionth of a
    point of every other — an order decided by rounding error. The heading
    tiebreak is what puts the section actually headed "Channeling Indicators"
    first, and this asserts it against the shipped documents rather than against
    a three-file fixture where BM25 still discriminates.
    """
    hits = await seeded_docs.search_chunks("channeling", k=5)
    assert hits
    assert "channeling" in hits[0].chunk.heading.lower()
    # And the noise it is protecting against is real: the top scores tie.
    assert hits[0].score == hits[-1].score


async def test_common_words_still_rank_their_own_heading_first(
    seeded_docs: KnowledgeService,
) -> None:
    for word in ("pressure", "bloom", "temperature"):
        hits = await seeded_docs.search_chunks(word, k=3)
        assert hits, word
        assert word in hits[0].chunk.heading.lower(), (word, hits[0].chunk.heading)


async def test_a_query_bm25_can_separate_is_left_to_bm25(
    seeded_docs: KnowledgeService,
) -> None:
    """Rounding only collapses noise; a real spread still decides the order."""
    hits = await seeded_docs.search_chunks("milk steaming", k=4)
    assert hits[0].chunk.doc_slug == "MILK_AND_DRINKS"
    assert hits[0].score > hits[-1].score + 1
