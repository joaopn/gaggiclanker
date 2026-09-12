"""`knowledge_docs` + `knowledge_chunks` — tier 2, as rows and as an FTS5 index.

The rule tier next door (`knowledge.py`) selects in Python because its
conditions are a JSON document and the table is small. This one is the opposite
case: the question is "which forty words of thirty thousand answer this", and
SQLite's FTS5 with BM25 answers it in a millisecond over an index it maintains
itself. So the search *is* the SQL, and there is no Python ranking pass to drift
away from it.

Two invariants hold this together and are worth stating before the code.

**Chunks are derived; heading paths are not.** A document's chunks are deleted
and rebuilt whenever its markdown changes, so a chunk *id* is meaningless to
anything outside this module. What is stored elsewhere — in an analysis's
`excerpts_used`, in a citation inside a diagnosis — is the `heading_path`, which
the chunker derives from the headings themselves and which therefore survives a
re-chunk of unchanged text.

**The FTS index is maintained by triggers, not here.** `content=` external
content means the index holds tokens and this table holds text; the three
triggers in migration 0011 keep them level. That is why `replace_chunks` can
delete and insert freely and why nothing in this file mentions
`knowledge_chunks_fts` except to read it.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.knowledge.chunker import ParsedChunk

__all__ = [
    "ChunkHit",
    "ChunkRow",
    "DocRow",
    "DocWrite",
    "KnowledgeDocsRepository",
    "content_hash",
]

#: How many characters of a chunk the search result quotes. Two or three
#: sentences: enough to tell whether the hit is the one you wanted, short enough
#: that twenty of them fit on a page.
SNIPPET_CHARS = 240

#: FTS5's own query syntax is a language — `AND`, `NEAR`, `*`, quoting, column
#: filters — and a user typing `sour AND bitter?` into a search box is writing
#: prose, not a query. Everything that is not a word or a digit is dropped and
#: the remaining terms are quoted, so a search can never be a syntax error and
#: never an injection into the match expression.
_FTS_WORD = re.compile(r"\w+", re.UNICODE)

#: How many of a query's words the heading tiebreak looks at. The SQL is one
#: bound `instr` per word, and a query long enough to need more than a dozen is
#: not one a tiebreak decides.
_MAX_HEADING_TERMS = 12

#: Decimal places the BM25 score is rounded to *for ordering only*. Three,
#: because a difference below a thousandth of a point over sixty chunks is
#: rounding error rather than relevance — see :meth:`KnowledgeDocsRepository.search`.
_RANK_PRECISION = 3


def content_hash(text: str) -> str:
    """sha256 of a document body. The seeder's "has this file changed" test."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def match_expression(query: str) -> str:
    """A user's words as a safe FTS5 MATCH expression, or "" for no words.

    Terms are OR-ed rather than AND-ed. The queries the analyzer builds are
    descriptions of a situation ("channeling pressure cliff sour"), not
    conjunctions somebody wants all of; requiring every term would return
    nothing for most of them, and BM25 already ranks a chunk matching four terms
    above one matching two.
    """
    terms = _FTS_WORD.findall(query)
    return " OR ".join(f'"{term}"' for term in terms)


class DocWrite(BaseModel):
    """One document on its way into the table."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=200)
    title: str = ""
    source: str = ""
    licence: str = ""
    attribution: str = ""
    body: str


class DocRow(BaseModel):
    """One row of `knowledge_docs`, as read back.

    ``body`` is the live markdown and is what the editor loads; ``default_body``
    is not on this model at all, for the reason `RuleRow` leaves `default_json`
    off — it is a second copy of the text that no reader wants and every
    serialiser would send. "Is it edited" is the part a reader needs, and that
    is a column.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    slug: str
    title: str = ""
    source: str = ""
    licence: str = ""
    attribution: str = ""
    body: str = ""
    content_hash: str = ""
    edited: bool = False
    created_at: str = ""
    updated_at: str = ""
    #: Filled in by the listing query. Not columns: chunks are derived, and a
    #: stored count is one more thing that can disagree with the rows.
    chunk_count: int = 0
    #: The sum of the chunks' own estimates — what this document would cost a
    #: prompt if all of it were retrieved, which is the number that says why
    #: retrieval exists.
    tokens_estimate: int = 0


class ChunkRow(BaseModel):
    """One row of `knowledge_chunks`."""

    model_config = ConfigDict(extra="forbid")

    id: int
    doc_id: int
    doc_slug: str = ""
    doc_title: str = ""
    heading_path: str
    heading: str = ""
    ordinal: int = 0
    body: str = ""
    tokens_estimate: int = 0


class ChunkHit(BaseModel):
    """One search result: the chunk, its BM25 score and a quotable snippet."""

    model_config = ConfigDict(extra="forbid")

    chunk: ChunkRow
    #: Higher is better. BM25 itself is negative-is-better, which reads backwards
    #: everywhere a person looks at it, so it is negated exactly here.
    score: float
    snippet: str


class KnowledgeDocsRepository(Repository):
    """Reads and writes the documents, their chunks and the search over them."""

    # ── documents ────────────────────────────────────────────────────

    async def list_docs(self) -> list[DocRow]:
        """Every document, slug order, with its chunk and word counts."""
        rows = await self.db.fetch_all(
            """
            SELECT d.id, d.slug, d.title, d.source, d.licence, d.attribution,
                   '' AS body, d.content_hash, d.edited, d.created_at, d.updated_at,
                   (SELECT COUNT(*) FROM knowledge_chunks c WHERE c.doc_id = d.id)
                       AS chunk_count,
                   (SELECT COALESCE(SUM(c.tokens_estimate), 0) FROM knowledge_chunks c
                     WHERE c.doc_id = d.id) AS tokens_estimate
              FROM knowledge_docs d
             ORDER BY d.slug
            """
        )
        return self.to_models(DocRow, rows)

    async def get_doc(self, slug: str) -> DocRow | None:
        row = await self.db.fetch_one(
            """
            SELECT d.id, d.slug, d.title, d.source, d.licence, d.attribution,
                   d.body, d.content_hash, d.edited, d.created_at, d.updated_at,
                   (SELECT COUNT(*) FROM knowledge_chunks c WHERE c.doc_id = d.id)
                       AS chunk_count,
                   (SELECT COALESCE(SUM(c.tokens_estimate), 0) FROM knowledge_chunks c
                     WHERE c.doc_id = d.id) AS tokens_estimate
              FROM knowledge_docs d
             WHERE d.slug = ?
            """,
            (slug,),
        )
        return self.to_model(DocRow, row)

    async def default_body(self, slug: str) -> str | None:
        """The markdown the package shipped, for "reset to default"."""
        value = await self.db.fetch_value(
            "SELECT default_body FROM knowledge_docs WHERE slug = ?", (slug,)
        )
        return None if value is None else str(value)

    async def default_hash(self, slug: str) -> str | None:
        """sha256 of the shipped markdown — seeding's "has this file changed" test.

        A column rather than a hash of `default_body` computed on read, which is
        the whole point of storing it: this runs once per document on every boot
        and must not pull 200 KB of text out of the database to answer "no".
        """
        value = await self.db.fetch_value(
            "SELECT default_hash FROM knowledge_docs WHERE slug = ?", (slug,)
        )
        return None if value is None else str(value)

    async def insert_doc(self, doc: DocWrite) -> int:
        """A document the seed has never shipped before. Live and default identical."""
        digest = content_hash(doc.body)
        cursor = await self.db.execute(
            """
            INSERT INTO knowledge_docs
                (slug, title, source, licence, attribution, body, default_body,
                 content_hash, default_hash, edited, created_at, updated_at)
            VALUES (:slug, :title, :source, :licence, :attribution, :body, :body,
                    :hash, :hash, 0, :now, :now)
            """,
            {
                "slug": doc.slug,
                "title": doc.title,
                "source": doc.source,
                "licence": doc.licence,
                "attribution": doc.attribution,
                "body": doc.body,
                "hash": digest,
                "now": utc_now(),
            },
        )
        return int(cursor.lastrowid or 0)

    async def refresh_both(self, doc: DocWrite) -> None:
        """The file changed and nobody had edited the row: take the new text."""
        digest = content_hash(doc.body)
        await self.db.execute(
            """
            UPDATE knowledge_docs
               SET title = :title, source = :source, licence = :licence,
                   attribution = :attribution, body = :body, default_body = :body,
                   content_hash = :hash, default_hash = :hash, edited = 0,
                   updated_at = :now
             WHERE slug = :slug
            """,
            {
                "slug": doc.slug,
                "title": doc.title,
                "source": doc.source,
                "licence": doc.licence,
                "attribution": doc.attribution,
                "body": doc.body,
                "hash": digest,
                "now": utc_now(),
            },
        )

    async def refresh_default(self, doc: DocWrite) -> None:
        """The file changed but the row is edited: record the new default only.

        The user's text stands. Provenance moves anyway — the same split
        `RulesRepository.refresh_default` makes, and for the same reason: source
        and licence describe where the document came from rather than what it
        now says, and leaving them stale would attribute an edited document to a
        citation that no longer matches it.
        """
        await self.db.execute(
            """
            UPDATE knowledge_docs
               SET default_body = :body, default_hash = :hash,
                   source = :source, licence = :licence, attribution = :attribution
             WHERE slug = :slug
            """,
            {
                "slug": doc.slug,
                "body": doc.body,
                "hash": content_hash(doc.body),
                "source": doc.source,
                "licence": doc.licence,
                "attribution": doc.attribution,
            },
        )

    async def set_body(self, slug: str, body: str) -> bool:
        """Store an edit (or a reset). ``edited`` follows from the two hashes."""
        digest = content_hash(body)
        cursor = await self.db.execute(
            """
            UPDATE knowledge_docs
               SET body = :body,
                   content_hash = :hash,
                   edited = CASE WHEN :hash = default_hash THEN 0 ELSE 1 END,
                   updated_at = :now
             WHERE slug = :slug
            """,
            {"slug": slug, "body": body, "hash": digest, "now": utc_now()},
        )
        return cursor.rowcount > 0

    async def count_docs(self) -> int:
        return int(await self.db.fetch_value("SELECT COUNT(*) FROM knowledge_docs") or 0)

    # ── chunks ───────────────────────────────────────────────────────

    async def replace_chunks(self, doc_id: int, chunks: list[ParsedChunk]) -> int:
        """Rebuild one document's chunks. Returns how many there now are.

        Delete-then-insert rather than a diff. A diff would have to decide what
        "the same chunk, edited" means when a heading moved, and the answer it
        would get wrong is exactly the one that matters — the FTS row. The
        triggers make both halves free.
        """
        await self.db.execute("DELETE FROM knowledge_chunks WHERE doc_id = ?", (doc_id,))
        for chunk in chunks:
            await self.db.execute(
                """
                INSERT INTO knowledge_chunks
                    (doc_id, heading_path, heading, ordinal, body, tokens_estimate)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    chunk.heading_path,
                    chunk.heading,
                    chunk.ordinal,
                    chunk.body,
                    chunk.tokens_estimate,
                ),
            )
        return len(chunks)

    async def chunks_for_doc(self, doc_id: int) -> list[ChunkRow]:
        rows = await self.db.fetch_all(
            f"SELECT {_CHUNK_COLUMNS} FROM knowledge_chunks c "  # noqa: S608 - literal column list
            "JOIN knowledge_docs d ON d.id = c.doc_id "
            "WHERE c.doc_id = ? ORDER BY c.ordinal",
            (doc_id,),
        )
        return self.to_models(ChunkRow, rows)

    async def chunks_by_paths(self, paths: list[str]) -> list[ChunkRow]:
        """The named chunks, in the order the caller named them.

        Order preserved in Python rather than in SQL: an `IN` clause has no
        order of its own, and the caller's order is a ranking it worked for.
        """
        if not paths:
            return []
        placeholders = ", ".join("?" for _ in paths)
        rows = await self.db.fetch_all(
            f"SELECT {_CHUNK_COLUMNS} FROM knowledge_chunks c "  # noqa: S608 - literal column list, bound values
            f"JOIN knowledge_docs d ON d.id = c.doc_id WHERE c.heading_path IN ({placeholders})",
            paths,
        )
        found = {chunk.heading_path: chunk for chunk in self.to_models(ChunkRow, rows)}
        return [found[path] for path in paths if path in found]

    async def count_chunks(self) -> int:
        return int(await self.db.fetch_value("SELECT COUNT(*) FROM knowledge_chunks") or 0)

    # ── search ───────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        slugs: list[str] | None = None,
    ) -> list[ChunkHit]:
        """BM25 over the chunk index, best first. Deterministic on ties.

        Three ordering terms, and the middle one is not decoration.

        **BM25 first.** The heading column carries ten times the body's weight,
        because a chunk headed "Channeling" is *about* channeling and one that
        says the word once in a sentence about something else is not.

        **Then how many of the query's words are in the heading**, over a score
        rounded to :data:`_RANK_PRECISION` decimals. At this corpus size BM25
        alone cannot express that. Sixty-odd chunks means a term appearing in
        more than half of them — `channeling`, `pressure`, `flow`, `grind`,
        `extraction`, `temperature`, `bloom` — has an inverse document frequency
        that floors at zero, and what is left is arithmetic noise: measured on
        the shipped corpus, every chunk matching `channeling` scores between
        -2.1e-06 and -1.7e-06, which never ties and therefore orders the results
        by rounding error. Rounding collapses that noise into a real tie so the
        heading term can decide it, while leaving a score that actually
        discriminates — `milk steaming` spans -10.4 to -3.4 — untouched.

        Heading hits are counted with `instr` rather than a second FTS table: it
        is a tiebreak, the headings are three words each, and a second index is
        a second thing to keep in step with the triggers.

        **Then the heading path.** Two chunks can still tie on both, SQLite is
        free to break that however the index happens to be laid out, and the
        analyzer's retrieval has to produce the same excerpts for the same shot
        twice running.
        """
        expression = match_expression(query)
        if not expression or limit <= 0:
            return []
        terms = [term.lower() for term in _FTS_WORD.findall(query)][:_MAX_HEADING_TERMS]
        heading_hits = " + ".join("(instr(lower(c.heading), ?) > 0)" for _ in terms) or "0"
        params: list[Any] = [*terms, expression]
        clause = ""
        if slugs:
            clause = f" AND d.slug IN ({', '.join('?' for _ in slugs)})"
            params.extend(slugs)
        params.append(limit)
        rows = await self.db.fetch_all(
            f"""
            SELECT {_CHUNK_COLUMNS},
                   bm25(knowledge_chunks_fts, 10.0, 1.0) AS rank_score,
                   ({heading_hits}) AS heading_hits
              FROM knowledge_chunks_fts
              JOIN knowledge_chunks c ON c.id = knowledge_chunks_fts.rowid
              JOIN knowledge_docs d ON d.id = c.doc_id
             WHERE knowledge_chunks_fts MATCH ?{clause}
             ORDER BY ROUND(rank_score, {_RANK_PRECISION}), heading_hits DESC, c.heading_path
             LIMIT ?
            """,  # noqa: S608 - literal column list; every value is bound
            params,
        )
        hits: list[ChunkHit] = []
        for row in rows:
            payload = dict(zip(row.keys(), tuple(row), strict=True))
            score = float(payload.pop("rank_score", 0.0) or 0.0)
            payload.pop("heading_hits", None)
            chunk = ChunkRow.model_validate(payload)
            hits.append(
                ChunkHit(chunk=chunk, score=round(-score, 4), snippet=_snippet(chunk.body, query))
            )
        return hits


#: Every chunk read joins its document, because a chunk with no document title
#: beside it cannot be rendered or cited. One list, so the three readers cannot
#: disagree about which columns a `ChunkRow` needs.
_CHUNK_COLUMNS = (
    "c.id, c.doc_id, d.slug AS doc_slug, d.title AS doc_title, "
    "c.heading_path, c.heading, c.ordinal, c.body, c.tokens_estimate"
)


def _snippet(body: str, query: str) -> str:
    """A few sentences of the chunk, centred on the first matching word.

    Built here rather than with FTS5's own `snippet()` because that function
    marks up the *indexed* text, which is stemmed and stripped of the markdown
    the reader is looking at. This one quotes the body as written.
    """
    flat = " ".join(body.split())
    terms = [term.lower() for term in _FTS_WORD.findall(query)]
    lowered = flat.lower()
    position = -1
    for term in terms:
        found = lowered.find(term)
        if found >= 0 and (position < 0 or found < position):
            position = found
    if position < 0 or position < SNIPPET_CHARS // 2:
        return flat[:SNIPPET_CHARS] + ("…" if len(flat) > SNIPPET_CHARS else "")
    start = position - SNIPPET_CHARS // 3
    end = start + SNIPPET_CHARS
    return "…" + flat[start:end] + ("…" if end < len(flat) else "")
