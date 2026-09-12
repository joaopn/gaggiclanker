"""Splitting a markdown document into retrievable, citable pieces.

One function — :func:`chunk_markdown` — and it is pure: text in, chunks out, no
database, no clock, no randomness. That is deliberate and it is the property the
tests pin, because a chunk's `heading_path` is a **citation**. An analysis says
``[ESPRESSO_BREWING_BASICS#adjustment-strategies/variable-hierarchy]`` and that
string has to still name the same passage after a re-seed, a reset and an
upgrade. Ids derived from the headings do; ids derived from a counter, a hash of
the body or the order rows happened to be inserted in do not.

**The shape of the split**:

* sections break at ``##`` and ``###``. ``#`` is the document title and ``####``
  and deeper stay inside their parent — a fourth-level heading in these files is
  a label on a list, not a section;
* the text before the first ``##`` is its own chunk, so a document's opening
  definition is retrievable rather than orphaned;
* a section under :data:`MIN_WORDS` is merged **upward** into the chunk before
  it, as long as the result still fits. A 40-word section is not a retrieval
  unit: it matches on one word and arrives with no context around it;
* a section over :data:`MAX_WORDS` is split at paragraph boundaries into parts
  that each fit, and the parts after the first get ``/part-2``, ``/part-3`` on
  their heading path;
* **a table is never split.** These documents carry their most quotable facts as
  tables — the roast x processing pressure matrix, the taste-to-cause mapping —
  and half a table is worse than no table, because the half that arrives still
  looks complete. A table alone larger than the maximum becomes an oversized
  chunk of its own, which is the one place the bounds are knowingly broken. The
  same holds for a fenced code block, which is a profile JSON here.

Everything else is ordinary: headings are slugified to ``[a-z0-9-]``, a repeated
heading inside one document gets ``-2``, and the trail is joined with ``/``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "MAX_WORDS",
    "MIN_WORDS",
    "ParsedChunk",
    "chunk_markdown",
    "estimate_tokens",
    "slugify",
]

#: Below this a section is merged into the one before it. 200 words is about a
#: screen of prose: enough that an excerpt read on its own makes a point.
MIN_WORDS = 200

#: Above this a section is split at a paragraph boundary. 600 words is roughly
#: 800 tokens — three of them fit inside the analyzer's default budget with room
#: for the rules, and a chunk larger than that is being retrieved for one
#: paragraph and paid for in full.
MAX_WORDS = 600

#: Words to tokens. English prose through a BPE tokeniser runs about 1.3
#: tokens per word; the extra is punctuation and the numbers these documents are
#: full of. An estimate is all the budget needs — see the column comment in
#: migration 0011 for why there is no real tokeniser here.
_TOKENS_PER_WORD = 1.35

_H1 = re.compile(r"^#\s+(.*\S)\s*$")
_H2_OR_H3 = re.compile(r"^(#{2,3})\s+(.*\S)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_ROW = re.compile(r"^\s*\|")
#: The `---` rules these documents use as section separators. Dropped on the way
#: in: they are typography, they carry no words, and a chunk that begins with
#: one reads as though something was cut off above it.
_RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")

#: Any ATX heading, at any depth. Used only to spot one left dangling at the
#: end of a split part.
_ANY_HEADING = re.compile(r"^#{1,6}\s+\S")

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """A heading as a URL-safe, lowercase slug. Empty text gives ``section``.

    Deliberately lossy and deliberately stable: markdown emphasis, inline code
    ticks and the arrows these headings use all collapse to hyphens, so
    ``### Sour → grind finer`` and ``### Sour -> grind finer`` are the same slug
    and an editor's typographic tidy-up does not invalidate a citation.
    """
    slug = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-")
    return slug or "section"


def estimate_tokens(text: str) -> int:
    """Roughly how many tokens this body costs. Never below 1."""
    return max(1, round(len(text.split()) * _TOKENS_PER_WORD))


@dataclass(frozen=True, slots=True)
class ParsedChunk:
    """One retrievable piece of one document."""

    #: ``<doc slug>#<heading>/<subheading>``. Unique within the document, and —
    #: because the document slug leads it — unique across the table.
    heading_path: str
    #: The same trail as it was written, joined with " > ". Indexed separately
    #: by FTS5 so a query naming a heading outranks a passing mention.
    heading: str
    ordinal: int
    body: str
    tokens_estimate: int

    @property
    def words(self) -> int:
        return len(self.body.split())


@dataclass(slots=True)
class _Section:
    """A heading and the lines under it, before any merging or splitting."""

    trail: list[str]
    lines: list[str]

    @property
    def text(self) -> str:
        return _tidy(self.lines)

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(slots=True)
class _Block:
    """One paragraph, list, table or fenced block. The unit a split may cut at."""

    lines: list[str]
    #: Tables and code fences are atomic: a split never happens inside one.
    atomic: bool

    @property
    def words(self) -> int:
        return sum(len(line.split()) for line in self.lines)


def chunk_markdown(slug: str, markdown: str) -> list[ParsedChunk]:
    """Split one document. Deterministic: same text in, same chunks out.

    ``slug`` leads every heading path, so it is the document's identity as far
    as a citation is concerned.
    """
    title, sections = _sections(markdown)
    chunks: list[tuple[list[str], str]] = []

    for section in sections:
        text = section.text
        if not text:
            continue
        trail = section.trail or [title or "Overview"]
        # Small section, and the chunk before it has room: fold it in. The
        # merged chunk keeps the *first* section's heading path, because that is
        # where a reader following the citation lands and the rest is below it
        # on the page.
        if chunks and section.words < MIN_WORDS:
            previous_trail, previous_text = chunks[-1]
            if len((previous_text + " " + text).split()) <= MAX_WORDS:
                chunks[-1] = (previous_trail, f"{previous_text}\n\n{text}")
                continue
        for part in _split(section):
            chunks.append((trail, part))

    return _identified(slug, _absorb_runts(chunks))


# ── parsing ──────────────────────────────────────────────────────────


def _sections(markdown: str) -> tuple[str, list[_Section]]:
    """The document title, and its H2/H3 sections in order.

    Fence-aware, because these documents are full of profile JSON and a ``#``
    comment inside a code block is not a heading. The preamble — everything
    before the first ``##`` — is returned as a section with an empty trail.
    """
    title = ""
    sections: list[_Section] = [_Section(trail=[], lines=[])]
    trail: list[str] = []
    in_fence = False
    fence = ""

    for line in markdown.splitlines():
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if not in_fence:
                in_fence, fence = True, marker
            elif marker == fence:
                in_fence = False
            sections[-1].lines.append(line)
            continue
        if in_fence:
            sections[-1].lines.append(line)
            continue

        h1 = _H1.match(line)
        if h1 is not None:
            # A second H1 in one file is a formatting choice, not a second
            # document; only the first one names the doc.
            title = title or h1.group(1)
            continue

        heading = _H2_OR_H3.match(line)
        if heading is not None:
            depth, text = len(heading.group(1)), heading.group(2)
            trail = [text] if depth == 2 else [*trail[:1], text]
            # The heading line stays at the top of its own section's body.
            #
            # This is what keeps a folded section labelled. A small section is
            # merged into the chunk above it and a runt is absorbed by its
            # neighbour, so on the real corpus most H2/H3 headings are not the
            # heading of the chunk they end up in — and without their own line
            # in the text the passage arrives in a prompt as an unlabelled
            # paragraph, with nothing for the FTS heading column or a reader to
            # match on. Carried as a line rather than re-attached at merge time
            # because the split, the merge and the absorb would each need their
            # own copy of the rule otherwise.
            sections.append(_Section(trail=list(trail), lines=[f"{'#' * depth} {text}", ""]))
            continue

        if _RULE.match(line):
            continue
        sections[-1].lines.append(line)

    # The preamble has no heading of its own, so it takes the document's — a
    # chunk whose first line names the document is a chunk a reader can place.
    preamble = sections[0]
    if title and _tidy(preamble.lines):
        preamble.lines = [f"# {title}", "", *preamble.lines]
    return title, sections


def _blocks(lines: list[str]) -> list[_Block]:
    """Lines grouped into paragraphs, with tables and fences marked atomic."""
    out: list[_Block] = []
    current: list[str] = []
    atomic = False
    in_fence = False
    fence = ""

    def flush() -> None:
        nonlocal current, atomic
        if current:
            out.append(_Block(lines=current, atomic=atomic))
        current, atomic = [], False

    for line in lines:
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if not in_fence:
                flush()
                in_fence, fence, atomic = True, marker, True
                current.append(line)
                continue
            if marker == fence:
                current.append(line)
                in_fence = False
                flush()
                continue
        if in_fence:
            current.append(line)
            continue

        if not line.strip():
            flush()
            continue
        if _TABLE_ROW.match(line):
            # A table row that did not follow another one starts a new block, so
            # the prose above it is free to end up in a different part.
            if not (current and atomic):
                flush()
                atomic = True
            current.append(line)
            continue
        if atomic:
            # Prose immediately after a table, with no blank line between.
            flush()
        current.append(line)

    flush()
    return out


def _split(section: _Section) -> list[str]:
    """One section as one or more bodies, each within the bounds where it can be.

    Greedy over blocks: fill a part until the next block would take it past
    :data:`MAX_WORDS`, then start another. Greedy rather than balanced because a
    balanced split moves every boundary in a document when one paragraph is
    edited, and every boundary is a citation.
    """
    text = section.text
    if len(text.split()) <= MAX_WORDS:
        return [text] if text else []

    parts: list[list[str]] = []
    current: list[str] = []
    words = 0
    for block in _blocks(section.lines):
        if current and words + block.words > MAX_WORDS:
            parts.append(current)
            current, words = [], 0
        current.extend(block.lines)
        current.append("")
        words += block.words
    if current:
        parts.append(current)

    _move_dangling_headings(parts)

    # A trailing part too small to stand alone goes back onto the one before it,
    # even though that takes it over the maximum. Two hundred words of context
    # is worth more than a bound.
    tidied = [_tidy(part) for part in parts]
    if len(tidied) > 1 and len(tidied[-1].split()) < MIN_WORDS:
        tail = tidied.pop()
        tidied[-1] = f"{tidied[-1]}\n\n{tail}"
    return [part for part in tidied if part]


def _move_dangling_headings(parts: list[list[str]]) -> None:
    """Push a heading that ended up as a part's last line onto the next part.

    These documents label their lists with ``####``, so a greedy split lands one
    at the end of a part often enough to matter. A part that ends on a heading
    promises something it does not contain, and the part after it starts with
    the list the heading was introducing and no label at all — both halves read
    as truncated.
    """
    for index in range(len(parts) - 1):
        part = parts[index]
        while part and not part[-1].strip():
            part.pop()
        if part and _ANY_HEADING.match(part[-1]):
            parts[index + 1] = [part.pop(), "", *parts[index + 1]]


def _absorb_runts(chunks: list[tuple[list[str], str]]) -> list[tuple[list[str], str]]:
    """Fold away any chunk still under :data:`MIN_WORDS`, ignoring the maximum.

    The merge inside :func:`chunk_markdown` only folds a small section upward
    when the result still fits, which leaves two kinds of runt behind: a short
    closing section after a full chunk, and the document preamble when it is a
    single line. Both are real in these files — "Resources", a one-sentence
    intro — and both are useless on their own: they match on one word and arrive
    with nothing around them.

    So this pass merges them anyway, and accepts the overshoot. Two hundred
    words of surrounding context is worth more to the reader of an excerpt than
    a bound is; a chunk of 640 words costs about 60 tokens more than one of 600.

    A runt merges **backward** where it can, keeping the earlier heading path,
    and forward only when it is the first chunk — a citation should land on a
    heading with the extra text below it, not above it. A document that is
    entirely shorter than the minimum stays as its one chunk.
    """
    out = list(chunks)
    index = 0
    while len(out) > 1 and index < len(out):
        _trail, text = out[index]
        if len(text.split()) >= MIN_WORDS:
            index += 1
            continue
        if index > 0:
            previous_trail, previous_text = out[index - 1]
            out[index - 1] = (previous_trail, f"{previous_text}\n\n{text}")
            del out[index]
            # Step back: the chunk that just grew is the one to re-examine, and
            # the one now at `index` has not been looked at yet either.
            index = max(0, index - 1)
            continue
        next_trail, next_text = out[1]
        out[1] = (next_trail, f"{text}\n\n{next_text}")
        del out[0]
    return out


def _identified(slug: str, chunks: list[tuple[list[str], str]]) -> list[ParsedChunk]:
    """Heading trails to unique heading paths, in document order."""
    seen: dict[str, int] = {}
    out: list[ParsedChunk] = []
    for ordinal, (trail, body) in enumerate(chunks):
        path = "/".join(slugify(part) for part in trail) or "intro"
        count = seen.get(path, 0)
        seen[path] = count + 1
        # The second chunk of a repeated heading — a real repeat, or a section
        # that was split into parts — gets a suffix. `part-2` rather than `-2`
        # for a split, because the two cases read differently to somebody
        # following a citation.
        if count:
            path = f"{path}/part-{count + 1}"
        out.append(
            ParsedChunk(
                heading_path=f"{slug}#{path}",
                heading=" > ".join(trail),
                ordinal=ordinal,
                body=body,
                tokens_estimate=estimate_tokens(body),
            )
        )
    return out


def _tidy(lines: list[str]) -> str:
    """Trailing whitespace off every line, blank runs collapsed, ends trimmed."""
    out: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if not stripped and (not out or not out[-1]):
            continue
        out.append(stripped)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)
