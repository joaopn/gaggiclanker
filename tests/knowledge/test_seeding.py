"""Seeding tier 2: the three-way rule, at document level.

The same contract the prompts and the rule tier have — insert what is new, take
the new text where nobody has edited the row, record only the new *default*
where somebody has — and the reason it matters here is the same: an upgrade must
reach every untouched document without clobbering the ones the user has changed,
and "reset to default" must converge on the new wording rather than on the
version they forked from.
"""

from __future__ import annotations

from pathlib import Path

from gaggiclanker.db.repos.knowledge_docs import KnowledgeDocsRepository
from gaggiclanker.knowledge.service import (
    SEED_ATTRIBUTION,
    SEED_LICENCE,
    SEED_SOURCE,
    KnowledgeService,
)


async def test_seeding_inserts_every_document_and_chunks_it(small: KnowledgeService) -> None:
    docs = await small.docs.list_docs()
    assert [doc.slug for doc in docs] == ["BITTER_SHOTS", "CHANNELING", "SOUR_SHOTS"]
    assert all(doc.chunk_count > 0 for doc in docs)
    assert all(doc.tokens_estimate > 0 for doc in docs)
    assert {doc.source for doc in docs} == {SEED_SOURCE}
    assert {doc.licence for doc in docs} == {SEED_LICENCE}
    assert docs[0].attribution == SEED_ATTRIBUTION


async def test_the_title_comes_from_the_first_heading(small: KnowledgeService) -> None:
    doc = await small.docs.get_doc("SOUR_SHOTS")
    assert doc is not None
    assert doc.title == "Sour Shots"


async def test_the_attribution_file_is_not_a_document(seeded_docs: KnowledgeService) -> None:
    """It explains provenance; chunking it would put a licence in search results."""
    slugs = {doc.slug for doc in await seeded_docs.docs.list_docs()}
    assert "ATTRIBUTION" not in slugs
    assert len(slugs) == 25


async def test_a_second_seed_changes_nothing(small: KnowledgeService, small_dir: Path) -> None:
    """The common case on every boot: unedited and unchanged does no work."""
    before = await small.docs.get_doc("SOUR_SHOTS")
    assert await small.seed_docs(small_dir) == 0
    after = await small.docs.get_doc("SOUR_SHOTS")
    assert before == after


async def test_an_unedited_document_takes_the_new_text(
    small: KnowledgeService, small_dir: Path
) -> None:
    (small_dir / "SOUR_SHOTS.md").write_text(
        "# Sour Shots\n\n## Causes\n\n" + ("rewritten " * 220).strip() + "\n",
        encoding="utf-8",
    )
    assert await small.seed_docs(small_dir) == 1
    doc = await small.docs.get_doc("SOUR_SHOTS")
    assert doc is not None
    assert "rewritten" in doc.body
    assert doc.edited is False
    chunks = await small.docs.chunks_for_doc(doc.id)
    assert [chunk.heading_path for chunk in chunks] == ["SOUR_SHOTS#causes"]


async def test_an_edited_document_keeps_its_text_and_moves_only_its_default(
    small: KnowledgeService, small_dir: Path
) -> None:
    mine = "# Sour Shots\n\n## My Section\n\n" + ("mine " * 220).strip() + "\n"
    assert await small.set_doc_markdown("SOUR_SHOTS", mine) is not None

    (small_dir / "SOUR_SHOTS.md").write_text(
        "# Sour Shots\n\n## Upstream\n\n" + ("theirs " * 220).strip() + "\n", encoding="utf-8"
    )
    assert await small.seed_docs(small_dir) == 0

    doc = await small.docs.get_doc("SOUR_SHOTS")
    assert doc is not None
    assert doc.edited is True
    assert "mine" in doc.body
    assert "theirs" not in doc.body

    # The reset converges on the *upgrade's* wording, not on what was forked.
    reset = await small.reset_doc("SOUR_SHOTS")
    assert reset is not None
    assert "theirs" in reset.body
    assert reset.edited is False


async def test_an_edit_re_chunks_and_a_reset_puts_the_chunks_back(
    small: KnowledgeService,
) -> None:
    original = await small.docs.get_doc("CHANNELING")
    assert original is not None
    before = [chunk.heading_path for chunk in await small.docs.chunks_for_doc(original.id)]

    edited = await small.set_doc_markdown(
        "CHANNELING", "# Channeling\n\n## Only Section\n\n" + ("word " * 300).strip() + "\n"
    )
    assert edited is not None
    assert [chunk.heading_path for chunk in await small.docs.chunks_for_doc(edited.id)] == [
        "CHANNELING#only-section"
    ]

    restored = await small.reset_doc("CHANNELING")
    assert restored is not None
    assert [chunk.heading_path for chunk in await small.docs.chunks_for_doc(restored.id)] == before


async def test_a_document_whose_file_vanished_is_left_alone(
    small: KnowledgeService, small_dir: Path
) -> None:
    """An image that dropped a file must not delete text the user may have edited."""
    (small_dir / "SOUR_SHOTS.md").unlink()
    await small.seed_docs(small_dir)
    assert await small.docs.get_doc("SOUR_SHOTS") is not None


async def test_setting_and_resetting_an_unknown_slug_is_none_not_a_crash(
    small: KnowledgeService,
) -> None:
    assert await small.set_doc_markdown("NOPE", "# Nope") is None
    assert await small.reset_doc("NOPE") is None


async def test_an_unreadable_directory_is_skipped_rather_than_raised(
    knowledge: KnowledgeService, tmp_path: Path
) -> None:
    """The archive must still boot. An analysis with no excerpts says so."""
    assert await knowledge.seed_docs(tmp_path / "does-not-exist") == 0
    assert await knowledge.docs.count_docs() == 0


async def test_the_fts_index_follows_a_rechunk(small: KnowledgeService) -> None:
    """The triggers, asserted through the search rather than by reading the index."""
    assert await small.search_chunks("channeling", k=5)
    await small.set_doc_markdown(
        "CHANNELING", "# Channeling\n\n## Gone\n\n" + ("unrelated " * 300).strip() + "\n"
    )
    hits = await small.search_chunks("resistance", k=5)
    assert all(
        "unrelated" not in hit.chunk.body or hit.chunk.doc_slug != "SOUR_SHOTS" for hit in hits
    )
    assert not [
        hit for hit in await small.search_chunks("puck", k=5) if hit.chunk.doc_slug == "CHANNELING"
    ]


async def test_deleting_a_document_takes_its_chunks_and_its_index_entries(
    small: KnowledgeService,
) -> None:
    """ON DELETE CASCADE plus the delete trigger, which is easy to get wrong."""
    doc = await small.docs.get_doc("CHANNELING")
    assert doc is not None
    repo = KnowledgeDocsRepository(small.db)
    await repo.db.execute("DELETE FROM knowledge_docs WHERE id = ?", (doc.id,))
    assert await repo.chunks_for_doc(doc.id) == []
    assert not [
        hit for hit in await small.search_chunks("puck", k=10) if hit.chunk.doc_id == doc.id
    ]
