"""The chunker: stable ids, sizes within bounds, and tables kept whole.

The properties here are the ones a *citation* depends on. A heading path is
printed by an analysis and followed by a reader months later, so the tests that
matter are "the same text gives the same ids" and "the ids come from the
headings rather than from the order or the length".
"""

from __future__ import annotations

import re
from pathlib import Path

from gaggiclanker.knowledge.chunker import (
    MAX_WORDS,
    MIN_WORDS,
    chunk_markdown,
    estimate_tokens,
    slugify,
)
from gaggiclanker.knowledge.service import DEFAULT_DOCS_DIR

#: Every shipped document, so the bound tests run against the real corpus rather
#: than against markdown written to pass them.
SHIPPED = sorted(path for path in DEFAULT_DOCS_DIR.rglob("*.md") if path.name != "ATTRIBUTION.md")


def _prose(word: str, count: int = 250) -> str:
    """`count` words of one repeated token. Long enough not to be merged away."""
    return " ".join([word] * count)


#: Four sections, each comfortably over `MIN_WORDS` so that nothing is merged
#: and the ids under test are the ids the headings produce. The repeated heading
#: is the point of the last one.
SAMPLE = f"""# A Guide

{_prose("opening")}

## First Section

{_prose("first")}

### A Subsection

{_prose("subsection")}

## First Section

{_prose("repeated")}
"""


def test_heading_paths_come_from_the_headings() -> None:
    chunks = chunk_markdown("A_GUIDE", SAMPLE)
    paths = [chunk.heading_path for chunk in chunks]
    assert paths[0] == "A_GUIDE#a-guide"
    assert "A_GUIDE#first-section" in paths
    # The repeat is suffixed rather than dropped or silently overwritten.
    assert "A_GUIDE#first-section/part-2" in paths
    assert len(paths) == len(set(paths))


def test_the_same_text_gives_the_same_ids_every_time() -> None:
    """Determinism, asserted directly. A citation is only worth printing if this holds."""
    first = chunk_markdown("A_GUIDE", SAMPLE)
    second = chunk_markdown("A_GUIDE", SAMPLE)
    assert [chunk.heading_path for chunk in first] == [chunk.heading_path for chunk in second]
    assert [chunk.body for chunk in first] == [chunk.body for chunk in second]
    assert [chunk.ordinal for chunk in first] == list(range(len(first)))


def test_editing_one_section_does_not_move_the_others_ids() -> None:
    """The reason the split is greedy rather than balanced."""
    before = {chunk.heading_path for chunk in chunk_markdown("A_GUIDE", SAMPLE)}
    edited = SAMPLE.replace(_prose("first"), _prose("rewritten"))
    after = {chunk.heading_path for chunk in chunk_markdown("A_GUIDE", edited)}
    assert before == after


def test_slugify_is_stable_across_typography() -> None:
    assert slugify("Sour → grind finer") == slugify("Sour -> grind finer")
    assert slugify("**Bold** heading") == "bold-heading"
    assert slugify("###") == "section"


def test_estimate_tokens_is_never_zero() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("one two three four") > 4


def test_every_shipped_chunk_is_at_least_the_minimum() -> None:
    """No runts. A forty-word chunk matches on one word and explains nothing."""
    for path in SHIPPED:
        chunks = chunk_markdown(path.stem, path.read_text(encoding="utf-8"))
        assert chunks, f"{path.name} produced no chunks"
        short = [chunk.heading_path for chunk in chunks if chunk.words < MIN_WORDS]
        assert not short, f"{path.name}: {short}"


def test_oversized_shipped_chunks_are_only_the_documented_exceptions() -> None:
    """The bound may be broken, but only to keep a table or a fence whole.

    A chunk over :data:`MAX_WORDS` has to contain an atomic block — a table or a
    fenced code block — that could not be split, or be the result of absorbing a
    runt. Anything else is the splitter failing to split.
    """
    for path in SHIPPED:
        for chunk in chunk_markdown(path.stem, path.read_text(encoding="utf-8")):
            if chunk.words <= MAX_WORDS:
                continue
            has_atomic = "|" in chunk.body or "```" in chunk.body
            assert has_atomic, f"{path.name} {chunk.heading_path}: {chunk.words} words, no table"


def test_a_table_is_never_split_across_chunks() -> None:
    """Half a table is worse than none, because it still looks complete."""
    table = "\n".join(f"| row {index} | value {index} |" for index in range(60))
    filler = ("word " * 300).strip()
    markdown = f"# Doc\n\n## Section\n\n{filler}\n\n{table}\n\n{filler}\n"
    chunks = chunk_markdown("DOC", markdown)
    with_rows = [chunk for chunk in chunks if "| row 0 |" in chunk.body]
    assert len(with_rows) == 1
    holder = with_rows[0]
    assert "| row 59 |" in holder.body


def test_a_fenced_block_is_never_split_and_its_hashes_are_not_headings() -> None:
    fence = "```json\n" + "\n".join(f'  "key{index}": {index},' for index in range(200)) + "\n```"
    markdown = f"# Doc\n\n## Section\n\n{fence}\n\n## After\n\n{('word ' * 300).strip()}\n"
    chunks = chunk_markdown("DOC", markdown)
    holders = [chunk for chunk in chunks if '"key0"' in chunk.body]
    assert len(holders) == 1
    assert '"key199"' in holders[0].body


def test_a_hash_inside_a_fence_is_not_a_heading() -> None:
    markdown = "# Doc\n\n## Real\n\n```\n## Not a heading\n```\n\n" + ("word " * 250).strip()
    paths = [chunk.heading_path for chunk in chunk_markdown("DOC", markdown)]
    assert paths == ["DOC#real"]


def test_a_tiny_document_stays_one_chunk() -> None:
    chunks = chunk_markdown("TINY", "# Tiny\n\nOne sentence.\n")
    assert len(chunks) == 1
    assert chunks[0].heading_path == "TINY#tiny"


def test_heading_carries_the_trail_as_written() -> None:
    chunks = chunk_markdown("A_GUIDE", SAMPLE)
    subsection = next(chunk for chunk in chunks if chunk.heading_path.endswith("a-subsection"))
    assert subsection.heading == "First Section > A Subsection"


def test_the_shipped_corpus_has_globally_unique_heading_paths() -> None:
    """The heading path is the citation, and it carries no document qualifier."""
    seen: dict[str, Path] = {}
    for path in SHIPPED:
        for chunk in chunk_markdown(path.stem, path.read_text(encoding="utf-8")):
            assert chunk.heading_path not in seen, (
                f"{chunk.heading_path} in both {seen.get(chunk.heading_path)} and {path}"
            )
            seen[chunk.heading_path] = path
    assert len(seen) > 40


def test_every_shipped_heading_survives_into_some_chunk() -> None:
    """The property that makes an excerpt readable and the heading boost fire.

    Most H2/H3 sections in this corpus do not end up as the *heading* of the
    chunk they land in — they are merged upward or absorbed as runts — so
    without their own line in the body the majority of the document's headings
    would exist nowhere in the index. An excerpt would then arrive in a prompt
    as an unlabelled paragraph, and a search for the words in a heading would
    have nothing to match.
    """
    fence = re.compile(r"^\s*(```|~~~)")
    heading = re.compile(r"^#{2,3}\s+(.*\S)\s*$")
    checked = 0
    for path in SHIPPED:
        text = path.read_text(encoding="utf-8")
        rendered = "\n".join(
            chunk.heading + "\n" + chunk.body for chunk in chunk_markdown(path.stem, text)
        )
        inside = False
        marker = ""
        for line in text.splitlines():
            opened = fence.match(line)
            if opened:
                if not inside:
                    inside, marker = True, opened.group(1)
                elif opened.group(1) == marker:
                    inside = False
                continue
            if inside:
                continue
            found = heading.match(line)
            if found is None:
                continue
            checked += 1
            assert found.group(1) in rendered, f"{path.name}: {found.group(1)!r} reached no chunk"
    assert checked > 300, "the corpus should have hundreds of headings to check"


def test_a_merged_section_carries_its_own_heading_line() -> None:
    """The mechanism, asserted on text small enough to read."""
    markdown = (
        "# Doc\n\n## Big Section\n\n"
        + _prose("big", 260)
        + "\n\n## Tiny Section\n\nOne short sentence that cannot stand alone.\n"
    )
    chunks = chunk_markdown("DOC", markdown)
    assert [chunk.heading_path for chunk in chunks] == ["DOC#big-section"]
    # The citation is the first section's, and the folded one is still labelled.
    assert "## Tiny Section" in chunks[0].body
    assert chunks[0].body.startswith("## Big Section")


def test_the_preamble_is_labelled_with_the_document_title() -> None:
    chunks = chunk_markdown("DOC", f"# The Guide\n\n{_prose('opening', 260)}\n\n## After\n\nx\n")
    assert chunks[0].body.startswith("# The Guide")


def test_a_heading_is_never_left_dangling_at_the_end_of_a_part() -> None:
    """A part that ends on a heading promises something it does not contain."""
    markdown = (
        "# Doc\n\n## Section\n\n"
        + _prose("alpha", 560)
        + "\n\n#### A Label\n\n"
        + _prose("beta", 300)
        + "\n"
    )
    chunks = chunk_markdown("DOC", markdown)
    assert len(chunks) > 1
    for chunk in chunks:
        assert not chunk.body.rstrip().split("\n")[-1].startswith("#")
    holder = next(chunk for chunk in chunks if "#### A Label" in chunk.body)
    assert "beta" in holder.body
