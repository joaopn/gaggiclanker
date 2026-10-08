"""No prompt, glossary, rule or golden says a word of the retired score, bands or channeling block.

The execution score, the threshold bands and their labels, and the channeling
block's risk and indicators were retired: a model told about one is a model told
about something the app no longer measures. The knowledge documents under
`knowledge/seed/docs` are reference prose about coffee, with their own attribution,
and are not scanned; everything a chat or a review is *handed* about a shot is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gaggiclanker.shotinfo.catalogue import CATALOGUE, GROUP_NOTES, default_tiers
from gaggiclanker.shotinfo.glossary import render_glossary

#: The threshold bands' own labels, upper case as a grade was written. Case-sensitive on
#: purpose: the plain words ("high", "good", "smooth") are ordinary English.
BAND_LABELS = re.compile(
    r"(?<!WHAT )\b(VERY_LOW|LOW|MODERATE|HIGH|VERY_HIGH|EXCELLENT|GOOD|FAIR|POOR|GENTLE|BRISK"
    r"|AGGRESSIVE"
    r"|VERY_AGGRESSIVE|VERY_STABLE|STABLE|VOLATILE|JITTERY|EARLY|LATE|SMOOTH|ROUGH|VERY_SMOOTH"
    r"|WITHIN_TOLERANCE|INSUFFICIENT_DATA|GRADUAL_DECLINE|STEEP_DECLINE|MODERATE_DECLINE|CLIFF"
    r"|MINIMAL|SLIGHT|SIGNIFICANT|UNSTABLE)\b"
)

ROOT = Path(__file__).resolve().parents[1]

RETIRED = re.compile(
    r"execution[ _]score|\bbands?\b|channeling (risk|indicator)|primary[ _]signal"
    r"|\b(VERY_LOW|VERY_HIGH|WITHIN_TOLERANCE|INSUFFICIENT_DATA|EXCELLENT|GRADUAL_DECLINE)\b"
    r"|erosion"
    # The app no longer adds "[AI]" to a name; nothing may tell a model or a person it does.
    r"|\[AI\][ ]suffix|suffix[^\n]{0,24}\[AI\]",
    re.IGNORECASE,
)


def _scanned() -> list[Path]:
    return sorted(
        [
            *(ROOT / "gaggiclanker" / "prompts").rglob("*.yaml"),
            ROOT / "gaggiclanker" / "knowledge" / "seed" / "rules.yaml",
            *(ROOT / "tests").glob("*/golden/*.txt"),
        ]
    )


@pytest.mark.parametrize("path", _scanned(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_retired_word_in_what_a_model_is_handed(path: Path) -> None:
    found = [
        (number, line.strip())
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        # A rule's `source_ref` cites the upstream document's own section title.
        if (RETIRED.search(line) or BAND_LABELS.search(line))
        and not line.lstrip().startswith("source_ref:")
    ]
    assert not found, found


def test_no_retired_word_in_the_glossary_or_a_group_note() -> None:
    texts = [render_glossary(default_tiers(), "base"), render_glossary(default_tiers(), "extended")]
    texts += list(GROUP_NOTES.values())
    texts += [item.name for item in CATALOGUE] + [item.label for item in CATALOGUE]
    for text in texts:
        assert not RETIRED.search(text), RETIRED.search(text)
        assert not BAND_LABELS.search(text), BAND_LABELS.search(text)


def test_no_retired_word_in_a_tool_description_builtin_or_over_mcp() -> None:
    """What a chat reads as a tool's description and what an MCP client lists are the same text."""
    from gaggiclanker.tools import builtin  # noqa: F401  (registers the tools)
    from gaggiclanker.tools.registry import registry

    checked = 0
    for spec in registry.specs():
        texts = [spec.description]
        schema = spec.input_schema()
        texts += [
            str(prop["description"])
            for prop in schema.get("properties", {}).values()
            if isinstance(prop, dict) and "description" in prop
        ]
        for text in texts:
            assert not RETIRED.search(text), (spec.name, RETIRED.search(text))
            assert not BAND_LABELS.search(text), (spec.name, BAND_LABELS.search(text))
            checked += 1
    assert checked > 20


#: A label as the retired bands wrote it (two or more upper-case words joined by underscores:
#: `VERY_LOW`, `MODERATE_DROP`, `WITHIN_TOLERANCE`), the single-word ones, and the retired
#: fields. "Channeling" as a physical effect of the puck stays in the documents, so the word
#: alone is not matched: the label (`channeling_risk`) and its readings are.
_DOC_LABELS = re.compile(
    r"\b[A-Z]{2,}(?:_[A-Z]+)+\b"
    r"|\b(?:LOW|MODERATE|HIGH|EXCELLENT|GOOD|FAIR|POOR|GENTLE|BRISK|AGGRESSIVE|STABLE|VOLATILE"
    r"|JITTERY|UNSTABLE|INCREASING|ROUGH|SMOOTH|CLIFF|MINIMAL|SLIGHT|SIGNIFICANT)\b"
    r"|channeling_risk|primary_signal|execution score"
)

KNOWLEDGE_DOCS = ROOT / "gaggiclanker" / "knowledge" / "seed" / "docs"


@pytest.mark.parametrize(
    "path",
    sorted(KNOWLEDGE_DOCS.rglob("*.md")),
    ids=lambda p: str(p.relative_to(KNOWLEDGE_DOCS)),
)
def test_a_shipped_knowledge_document_has_no_retired_label(path: Path) -> None:
    found = [
        (number, match.group(0))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        # A file name such as TELEMETRY_PATTERNS.md is a name, not a label.
        for match in _DOC_LABELS.finditer(re.sub(r"[A-Z]+(?:_[A-Z]+)+\.md", "", line))
    ]
    assert not found, found


def test_the_trimmed_reference_documents_keep_the_channeling_effect_elsewhere() -> None:
    """The cut removes the label, not the physics: the puck's channeling is still described."""
    text = (KNOWLEDGE_DOCS / "COFFEE_PROCESSING.md").read_text(encoding="utf-8")
    assert "channeling" in text.lower()
