"""The field glossary: every item a chat can see, explained, with no grade in it.

Every item that is not excluded is explained once, in the half of the glossary its
tier puts it in, and a group's note is written once under its heading. There is no
band table to walk any more: a number is explained by what it measures, and the
glossary carries no label that grades one (the words that went with the execution
score and the threshold bands are not in it, in either half).

The golden is `golden/glossary.txt`, rendered with the default tiers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from gaggiclanker.shotinfo.catalogue import (
    CATALOGUE,
    GROUP_NOTES,
    ITEMS,
    Tier,
    default_tiers,
)
from gaggiclanker.shotinfo.glossary import EXTENDED_TOOLS, Part, render_glossary

GOLDEN = Path(__file__).resolve().parent / "golden" / "glossary.txt"


def whole(tiers: Mapping[str, Tier]) -> str:
    """Both halves, as a reader holding the prompt and an extended read has them."""
    return render_glossary(tiers, "base") + "\n\n" + render_glossary(tiers, "extended")


#: Words that belonged to the retired execution score, the threshold bands and the
#: channeling block. A reader must never meet one in what a chat is told about a shot.
REMOVED_WORDS = (
    "execution score",
    "VERY_LOW",
    "VERY_HIGH",
    "MODERATE",
    "EXCELLENT",
    "GRADUAL_DECLINE",
    "WITHIN_TOLERANCE",
    "INSUFFICIENT_DATA",
    "VERY_STABLE",
    "channeling risk",
)


def test_each_group_note_is_written_under_its_heading() -> None:
    glossary = whole(default_tiers())

    for group, note in GROUP_NOTES.items():
        assert f"[{group}]\n{note}\n" in glossary, group


def test_no_word_of_the_retired_score_or_bands_is_in_the_glossary() -> None:
    glossary = whole(default_tiers())
    for word in REMOVED_WORDS:
        assert word.lower() not in glossary.lower(), word
    assert "[Shared bands]" not in glossary
    assert not re.findall(r"\b[A-Z]{3,}_[A-Z_]{3,}\b", glossary), "a grade label is back"


def test_the_labels_assigned_without_a_table_are_explained() -> None:
    """Assigned inline in the engine, so listed here from the code that assigns them."""
    # `_classify_phase`.
    for phase_type in ("preinfusion", "brew", "decline"):
        assert re.search(rf"\b{phase_type}\b", ITEMS["phase_type"].meaning), phase_type
    assert "every phase gets the same metrics" in ITEMS["phase_type"].meaning


@pytest.mark.parametrize("part", ["base", "extended"])
def test_each_half_matches_its_golden_file(update_golden: bool, part: Part) -> None:
    rendered = render_glossary(default_tiers(), part)
    golden = GOLDEN if part == "base" else GOLDEN.with_name("glossary-extended.txt")

    if update_golden:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(rendered, encoding="utf-8")
        pytest.skip("golden file rewritten")
    assert golden.exists(), "run with --update-golden to create it"
    assert rendered == golden.read_text(encoding="utf-8")


def _entries(rendered: str) -> list[str]:
    return re.findall(r"^- (.+? \[(?:base|extended)\]): ", rendered, flags=re.MULTILINE)


def test_the_halves_are_disjoint_and_together_are_every_shown_item_once() -> None:
    """The definition of the split: base holds the base items, extended the extended
    ones, and the union is the set of items not excluded, none twice."""
    tiers = default_tiers()
    base = render_glossary(tiers, "base")
    extended = render_glossary(tiers, "extended")

    assert set(tiers.values()) == {"base", "extended", "excluded"}, (
        "the default tiers use all three"
    )
    expect = {part: [i for i in CATALOGUE if tiers[i.key] == part] for part in ("base", "extended")}
    for part, rendered in (("base", base), ("extended", extended)):
        assert _entries(rendered) == [f"{i.label} [{part}]" for i in expect[part]], part
        other = "extended" if part == "base" else "base"
        assert f"[{other}]: " not in rendered, part
    names = _entries(base) + _entries(extended)
    assert len(names) == len(set(names))
    assert len(names) == sum(t != "excluded" for t in tiers.values())
    for item in CATALOGUE:
        if tiers[item.key] == "excluded":
            assert item.meaning not in base + extended, item.key


def test_a_group_note_is_written_once_under_its_first_heading() -> None:
    tiers = default_tiers()
    both = render_glossary(tiers, "base") + "\n" + render_glossary(tiers, "extended")

    for group, note in GROUP_NOTES.items():
        assert both.count(note) <= 1, group


def test_each_half_is_deterministic() -> None:
    for part in ("base", "extended"):
        assert render_glossary(default_tiers(), part) == render_glossary(default_tiers(), part)


def test_the_base_preamble_names_the_three_tools_that_bring_the_extended_half() -> None:
    preamble = "\n".join(render_glossary(default_tiers(), "base").splitlines()[:5])

    assert EXTENDED_TOOLS == ("get_shot_extended", "get_shot_full", "compare_shots")
    for name in EXTENDED_TOOLS:
        assert name in preamble.split("they come once")[1], name


def test_the_glossary_covers_what_can_be_shown_and_nothing_else() -> None:
    tiers = default_tiers()
    rendered = whole(tiers)

    for item in CATALOGUE:
        entry = f"- {item.label} [{tiers[item.key]}]: {item.meaning}"
        if tiers[item.key] == "excluded":
            assert item.meaning not in rendered, item.key
        else:
            assert entry in rendered, item.key


def test_the_glossary_follows_the_tiers() -> None:
    moved = {**default_tiers(), "phase_samples": "extended", "rating": "excluded"}

    rendered = whole(moved)

    assert f"- samples [extended]: {ITEMS['phase_samples'].meaning}" in rendered
    assert f"- samples [extended]: {ITEMS['phase_samples'].meaning}" in render_glossary(
        moved, "extended"
    )
    assert "- Rating [" not in rendered


def test_the_base_glossary_starts_with_its_preamble() -> None:
    lines = render_glossary(default_tiers(), "base").splitlines()

    assert lines[0] == "SHOT FIELDS"
    assert "get_shot_extended adds" in lines[1]
    assert "never that it was zero" in lines[2]
    assert "extended fields are not in this prompt" in lines[3]
    assert lines[4] == ""


def test_the_extended_glossary_starts_with_its_own_heading() -> None:
    lines = render_glossary(default_tiers(), "extended").splitlines()

    assert lines[0] == "SHOT FIELDS, EXTENDED"
    assert lines[2] == ""
