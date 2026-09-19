"""The knowledge base as one object: seeding, search, retrieval and selection.

:class:`KnowledgeService` is a thin façade over three repositories, and it is
here for a reason that has not arrived yet: the chat calls
:meth:`~KnowledgeService.search_chunks` and
:meth:`~KnowledgeService.select_insights` as tools, while the analyzer calls
:meth:`~KnowledgeService.select_chunks` and the same ``select_insights`` in one
deterministic pass. Two callers, one behaviour — so the behaviour is written
once, as plain async methods taking plain values, with nothing on them that
assumes a request, a shot or a conversation.

**Retrieval is deterministic.** That is the same promise tier 1 makes and it is
made for the same reason: the model is asked to cite what it used, and a
citation is only worth reading if the same shot would be given the same
excerpts twice running. So the queries are built from the context in a fixed
order, each one's results are ordered by (score, heading path) in SQL, the merge
is a total sort, and the budget is spent greedily over that. Nothing reads the
clock and nothing iterates a set.

**Excerpts are supporting context; rules are authoritative.** The prompt says so
and this module's shape agrees with it: at most one chunk per document, a token
budget that is a couple of thousand rather than a couple of tens of thousands,
and the heading path printed beside every excerpt so a reader can go and check
it. Tier 2 is thirty thousand words of somebody else's prose — useful, and not
the thing to resolve a disagreement with.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.knowledge_docs import (
    ChunkHit,
    ChunkRow,
    DocRow,
    DocWrite,
    KnowledgeDocsRepository,
    content_hash,
)
from gaggiclanker.db.repos.knowledge_insights import InsightRow, InsightsRepository
from gaggiclanker.domain.vocab import FLAVOR_LABELS
from gaggiclanker.knowledge.chunker import chunk_markdown

__all__ = [
    "DEFAULT_CHUNK_TOKEN_BUDGET",
    "DEFAULT_DOCS_DIR",
    "MAX_EXCERPTS",
    "SEED_ATTRIBUTION",
    "SEED_LICENCE",
    "SEED_SOURCE",
    "Excerpt",
    "KnowledgeService",
    "RetrievalContext",
    "render_excerpts",
    "render_insights",
]

log = structlog.get_logger(__name__)

#: Shipped inside the package, next to the prompts, the rule seed and the
#: migrations, for the same reason: the wheel and the container image carry
#: ``gaggiclanker/`` and nothing else.
DEFAULT_DOCS_DIR = Path(__file__).resolve().parent / "seed" / "docs"

#: Not a document. It explains where the others came from and what was left out,
#: and chunking it would put a licence notice into retrieval results.
_ATTRIBUTION_FILE = "ATTRIBUTION.md"

SEED_SOURCE = "gaggimate-mcp"
SEED_LICENCE = "MIT"
SEED_ATTRIBUTION = (
    "gaggimate-mcp (julianleopold, MIT), adapting gaggimate-barista (Charlie Hall); "
    "each document names its own sources. See knowledge/seed/docs/ATTRIBUTION.md."
)

#: The analyzer's default excerpt budget, in estimated tokens. Overridden by the
#: `analysisChunkTokenBudget` setting. Fifteen hundred is two or three chunks —
#: enough that a diagnosis can quote the passage it is leaning on, small enough
#: that the rules, the trajectory and the shot itself still dominate the prompt.
DEFAULT_CHUNK_TOKEN_BUDGET = 1500

#: A hard ceiling on how many excerpts an analysis gets, whatever the budget
#: says. A budget raised to 20 000 should buy longer excerpts, not a reading
#: list: past about half a dozen citations nobody checks any of them.
MAX_EXCERPTS = 6

#: How many hits each query contributes to the merge.
_PER_QUERY = 3

#: Band labels that mean "this was fine". A query built from one of these
#: retrieves prose about the normal case, which is the one thing an analysis
#: does not need explaining.
_UNREMARKABLE_BANDS = frozenset(
    {
        "NORMAL",
        "GOOD",
        "EXCELLENT",
        "NONE",
        "OK",
        "STABLE",
        "WITHIN_TOLERANCE",
        "MINOR_DEVIATION",
        "UNKNOWN",
        "NOT_MEASURED",
    }
)

#: Signal tokens whose shape is not a diagnostic band and which are handled
#: elsewhere in :meth:`~KnowledgeService.queries_for`, or not at all.
_NON_BAND_PREFIXES = ("style", "taste", "balance", "primary", "scale")


@dataclass(frozen=True, slots=True)
class RetrievalContext:
    """What an analysis knows, in the shape retrieval needs it.

    A flat record rather than the analyzer's :class:`AnalysisContext`, and
    deliberately so: ``gaggiclanker.analyzer`` imports
    ``gaggiclanker.knowledge``, never the other way round, and the chat
    will build one of these from a conversation that has no shot in it at all.
    """

    #: The detected shot style (`bloom`, `turbo`, `traditional`, …).
    style: str = "unknown"
    #: The signal tokens rule selection was made against, exactly as
    #: :func:`gaggiclanker.analyzer.context.signal_tokens` produced them.
    signals: tuple[str, ...] = ()
    #: The user's taste notes (flavour-wheel slugs) and their balance verdict.
    #: Ground truth for taste, so they lead the query list.
    taste_notes: tuple[str, ...] = ()
    balance: str | None = None
    #: The bean, for the two queries that are about the coffee rather than the
    #: shot. ``None`` means "not stated" and produces no query.
    roast_level: str | None = None
    process: str | None = None
    #: Free-text terms a caller wants folded in. The chat's own question goes
    #: here; the analyzer leaves it empty.
    extra_queries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Excerpt:
    """One retrieved chunk, as the prompt and the analysis snapshot carry it."""

    heading_path: str
    doc_slug: str
    doc_title: str
    heading: str
    body: str
    tokens_estimate: int
    #: The query that found it. Stored on the snapshot so "why was I shown
    #: this" has an answer months later.
    query: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "heading_path": self.heading_path,
            "doc_slug": self.doc_slug,
            "doc_title": self.doc_title,
            "heading": self.heading,
            "body": self.body,
            "tokens_estimate": self.tokens_estimate,
            "query": self.query,
        }


def render_excerpts(excerpts: list[dict[str, Any]]) -> str:
    """Retrieved chunks as prompt text, each under its citable heading path.

    Takes dicts rather than models for the reason :func:`render_rules` does: the
    analysis snapshot stores its excerpts as plain JSON and has to render the
    same way months later, when the documents have been edited. One renderer, so
    the live prompt and the stored one cannot disagree about what was said.
    """
    if not excerpts:
        return "(no reference excerpts were retrieved for this shot)"
    blocks: list[str] = []
    for excerpt in excerpts:
        heading = str(excerpt.get("heading") or excerpt.get("doc_title") or "")
        blocks.append(f"[{excerpt['heading_path']}] {heading}\n{excerpt['body']}".strip())
    return "\n\n".join(blocks)


def render_insights(insights: list[dict[str, Any]]) -> str:
    """Confirmed insights as prompt text, one scoped line each."""
    if not insights:
        return "(nothing has been learned and confirmed for this setup yet)"
    return "\n".join(f"  [{item['scope']}] {item['text']}" for item in insights)


class KnowledgeService:
    """Seeding, search, retrieval and selection over the whole knowledge base."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.docs = KnowledgeDocsRepository(db)
        self.insights = InsightsRepository(db)

    # ── seeding ──────────────────────────────────────────────────────

    async def seed_docs(self, directory: Path | None = None) -> int:
        """Upsert every shipped document. Returns how many rows changed.

        The three-way upsert the prompts and the rule tier use
        (:func:`gaggiclanker.knowledge.rules.seed_rules`), at **document**
        level: insert what is new, take the new text where nobody has edited the
        row, and record only the new default where somebody has. Document level
        rather than chunk level because a chunk is derived — there is no such
        thing as an edited chunk, only an edited document that is re-chunked.

        A document whose file has vanished is left alone, for the reason a
        dropped rule is: an image that lost a file should not delete text the
        user may have edited.

        A file that cannot be read is logged and skipped rather than raised. The
        archive must still boot, and an analysis with no excerpts says so in its
        context instead of taking the app down.
        """
        root = directory or DEFAULT_DOCS_DIR
        changed = 0
        total = 0
        for path in sorted(root.rglob("*.md")):
            if path.name == _ATTRIBUTION_FILE:
                continue
            try:
                markdown = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.error("knowledge_doc_unreadable", path=str(path), error=str(exc))
                continue
            total += 1
            doc = DocWrite(
                slug=path.stem,
                title=_title(markdown, path.stem),
                source=SEED_SOURCE,
                licence=SEED_LICENCE,
                attribution=SEED_ATTRIBUTION,
                body=markdown,
            )
            if await self._seed_one(doc):
                changed += 1
        if changed:
            log.info("knowledge_docs_seeded", changed=changed, total=total)
        return changed

    async def _seed_one(self, doc: DocWrite) -> bool:
        """One document through the three-way rule. True when the live text moved."""
        existing = await self.docs.get_doc(doc.slug)
        if existing is None:
            doc_id = await self.docs.insert_doc(doc)
            await self.docs.replace_chunks(doc_id, chunk_markdown(doc.slug, doc.body))
            return True
        if existing.edited:
            await self.docs.refresh_default(doc)
            return False
        if (await self.docs.default_hash(doc.slug)) == content_hash(doc.body):
            # Unedited and unchanged: the common case on every boot. Compared as
            # hashes rather than as text, which is what the stored column is for
            # — reading 200 KB of markdown back out of the database to discover
            # that nothing changed is the work this column exists to avoid.
            return False
        await self.docs.refresh_both(doc)
        await self._rechunk(doc.slug)
        return True

    async def set_doc_markdown(self, slug: str, markdown: str) -> DocRow | None:
        """Store an edit and re-chunk. ``None`` when there is no such document."""
        if not await self.docs.set_body(slug, markdown):
            return None
        await self._rechunk(slug)
        return await self.docs.get_doc(slug)

    async def reset_doc(self, slug: str) -> DocRow | None:
        """Put the shipped text back. ``None`` when there is no such document."""
        default = await self.docs.default_body(slug)
        if default is None:
            return None
        return await self.set_doc_markdown(slug, default)

    async def _rechunk(self, slug: str) -> int:
        doc = await self.docs.get_doc(slug)
        if doc is None:  # pragma: no cover - callers check first
            return 0
        return await self.docs.replace_chunks(doc.id, chunk_markdown(doc.slug, doc.body))

    # ── search (shared with the chat) ────────────────────────────────

    async def search_chunks(
        self,
        query: str,
        *,
        k: int = 5,
        slugs: list[str] | None = None,
    ) -> list[ChunkHit]:
        """Free-text search over the chunk index. Best first, ties broken stably.

        The chat's search tool and the Knowledge page's search box are the same
        call. ``slugs`` narrows to named documents, which is how "search the
        profile documentation only" is expressed.
        """
        return await self.docs.search(query, limit=k, slugs=slugs)

    async def get_chunk(self, heading_path: str) -> ChunkRow | None:
        """One chunk by its citation. ``None`` when nothing has that path.

        Expanding a citation is its own operation: an analysis stores
        ``excerpts_used`` as heading paths, the chat will be handed one in a
        question ("what does X say?"), and both want the passage back without
        guessing at a search query that would find it again.
        """
        found = await self.docs.chunks_by_paths([heading_path])
        return found[0] if found else None

    async def get_chunks(self, heading_paths: list[str]) -> list[ChunkRow]:
        """Several chunks by citation, in the order asked for.

        A path that no longer resolves — a document edited so that the heading
        moved — is dropped rather than returned as a hole. A citation into text
        that has changed is not a failure worth raising; it is an old analysis
        pointing at prose that has moved on, which is exactly why the snapshot
        on the analysis row carries the body it was given.
        """
        return await self.docs.chunks_by_paths(heading_paths)

    # ── retrieval for an analysis ────────────────────────────────────

    def queries_for(self, context: RetrievalContext) -> list[str]:
        """The search queries one analysis makes, in a fixed order.

        Order is priority: what the person tasted first, then the channeling
        indicators that actually fired, then the diagnostic bands that were not
        normal, then the bean and the style. The merge below takes each query's
        best hit before any query's second, so this order is what decides which
        excerpt an analysis gets when the budget only buys two.
        """
        queries: list[str] = []

        def add(text: str) -> None:
            cleaned = " ".join(text.replace("_", " ").split())
            if cleaned and cleaned not in queries:
                queries.append(cleaned)

        # 0. What the caller actually asked, when there is one. The merge below
        #    takes each query's best hit before any query's second and the
        #    budget is spent greedily over that, so a question appended at the
        #    end is a question whose answer the background queries have already
        #    paid for. The chat asks about milk steaming in the middle of a
        #    dial-in conversation; it must not get the roast temperature table.
        for query in context.extra_queries:
            add(query)

        # 1. Taste, because taste is ground truth. Both sides in one cup is
        #    channeling rather than an extraction level, and the seed's own
        #    `taste:sour_and_bitter` token says so — asked for by name so the
        #    retrieval agrees with the rule the analysis is also given.
        if "taste:sour_and_bitter" in context.signals:
            add("channeling sour and bitter puck preparation distribution")
        if context.balance and context.balance != "balanced":
            add(f"{context.balance} taste cause extraction adjustment")
        for note in context.taste_notes:
            # No "espresso" in the query. It is the most common word in a corpus
            # that is entirely about espresso, so it contributes nothing to the
            # ranking except term frequency — and a chunk that happens to repeat
            # it, like the drinks table, outranks the one that explains the note.
            # The note's own label, not its slug: the prose says "bitter", not
            # "other chemical bitter".
            add(f"{FLAVOR_LABELS.get(note, note).lower()} taste cause fix")

        # 2. The channeling indicators that fired, by name.
        for token in context.signals:
            if token.startswith("primary:"):
                add(f"channeling {token.removeprefix('primary:')}")

        # 3. The diagnostic bands that were not normal.
        for token in context.signals:
            metric, _, label = token.partition(":")
            if not label or metric in _NON_BAND_PREFIXES or not label.isupper():
                continue
            if label in _UNREMARKABLE_BANDS:
                continue
            add(f"{metric} {label}")

        # 4. The bean and the shot style — always present, always last. They are
        #    background rather than evidence, and they are what an analysis with
        #    nothing wrong in it retrieves.
        if context.process:
            add(f"{context.process} processing extraction pressure temperature")
        if context.roast_level:
            add(f"{context.roast_level} roast temperature ratio")
        if context.style and context.style != "unknown":
            add(f"{context.style} shot profile")

        return queries

    async def select_chunks(
        self,
        context: RetrievalContext,
        *,
        token_budget: int = DEFAULT_CHUNK_TOKEN_BUDGET,
        max_excerpts: int = MAX_EXCERPTS,
    ) -> list[Excerpt]:
        """The excerpts one analysis is given. Deterministic, budgeted, deduped.

        Three properties, each of which a test pins:

        * **deterministic** — the queries come from :meth:`queries_for` in a
          fixed order, each query's hits are ordered by (score, heading path) in
          SQL, and the merge is a total sort over (rank, query index, score,
          heading path). No ties are left for the database to break;
        * **budgeted** — chunks are taken in merged order while the running
          total of their own token estimates fits, and a chunk that does not fit
          is skipped rather than ending the loop, so a 600-word chunk at the
          front does not exclude a 200-word one behind it;
        * **deduped by document** — at most one chunk per document. Two sections
          of the same guide say much the same thing in much the same words, so
          without this the second-best hit is almost always the neighbour of the
          best one, and the analysis gets one document's opinion twice.
        """
        queries = self.queries_for(context)
        if not queries or token_budget <= 0:
            return []

        candidates: list[tuple[int, int, float, str, ChunkRow, str]] = []
        for index, query in enumerate(queries):
            for rank, hit in enumerate(await self.docs.search(query, limit=_PER_QUERY)):
                candidates.append(
                    (rank, index, -hit.score, hit.chunk.heading_path, hit.chunk, query)
                )
        candidates.sort(key=lambda item: item[:4])

        chosen: list[Excerpt] = []
        seen_docs: set[str] = set()
        spent = 0
        for _rank, _index, _score, _path, chunk, query in candidates:
            if len(chosen) >= max_excerpts:
                break
            if chunk.doc_slug in seen_docs:
                continue
            if spent + chunk.tokens_estimate > token_budget:
                continue
            seen_docs.add(chunk.doc_slug)
            spent += chunk.tokens_estimate
            chosen.append(
                Excerpt(
                    heading_path=chunk.heading_path,
                    doc_slug=chunk.doc_slug,
                    doc_title=chunk.doc_title,
                    heading=chunk.heading,
                    body=chunk.body,
                    tokens_estimate=chunk.tokens_estimate,
                    query=query,
                )
            )
        return chosen

    # ── the learned tier ─────────────────────────────────────────────

    async def select_insights(self, attributes: dict[str, Any]) -> list[InsightRow]:
        """The confirmed insights that apply to a Set. Oldest first.

        ``attributes`` is the flat record :data:`SCOPE_KEYS` names — bean id,
        roast level, process, origin, grinder id, profile style, machine id —
        with ``None`` for anything the Set does not state. Shared with the chat,
        which builds it from whichever Set the conversation is about.
        """
        return await self.insights.select(attributes)


def _title(markdown: str, fallback: str) -> str:
    """The document's first H1, or its slug spelled as words."""
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
        if line.strip().startswith("```"):
            break
    return fallback.replace("_", " ").title()
