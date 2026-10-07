"""What the shipped review prompt says, for the sentences a review's trust rests on."""

from __future__ import annotations

from pathlib import Path

import yaml

PROMPTS = Path(__file__).resolve().parents[2] / "gaggiclanker" / "prompts"


def _system() -> str:
    text = yaml.safe_load((PROMPTS / "review.yaml").read_text(encoding="utf-8"))["system"]
    return " ".join(str(text).split())


def test_the_summary_carries_no_figures_either() -> None:
    """Figures come only from evidence: a model-typed number in the summary is unchecked."""
    system = _system()
    assert "The summary is no exception: it carries no figures either." in system
    assert "`summary`: one sentence of at most 200 characters with no figures in it" in system


def test_it_says_exactly_what_the_review_is_and_is_not_given() -> None:
    system = _system()
    # What it is given, including the prediction and what that does and does not reveal.
    assert "the recipe of the Set version the shot is filed under" in system
    assert "the prediction that version was filed with" in system
    assert "told nothing about that version beyond its name" in system
    assert "the signature's free-text expectations" in system
    # What it is not.
    assert (
        "You are not told what the person thought of the cup, their notes or their label, or "
        "anything about any other shot or any earlier review"
    ) in system
    assert "what they were trying" not in system
