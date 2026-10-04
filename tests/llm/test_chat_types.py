"""What is taken out of a tool result before it is previewed or replayed."""

from __future__ import annotations

import json

from gaggiclanker.llm.chat_types import event_preview, without_field_meanings

BODY = {"field_meanings": 'a "quoted" \\ meaning\nover lines', "shot_id": 7, "text": "x"}


def test_the_glossary_that_heads_a_result_is_removed_and_the_rest_is_kept() -> None:
    assert json.loads(without_field_meanings(json.dumps(BODY))) == {"shot_id": 7, "text": "x"}
    # The MCP server's own serialisation may use other spacing.
    compact = json.dumps(BODY, separators=(",", ":"))
    assert json.loads(without_field_meanings(compact)) == {"shot_id": 7, "text": "x"}


def test_a_result_cut_at_the_cap_still_loses_it() -> None:
    cut = json.dumps(BODY)[:-9] + "\n... truncated at 24000 characters."

    assert without_field_meanings(cut).startswith('{"shot_id": 7')
    assert "meaning" not in without_field_meanings(cut)


def test_a_result_without_it_is_unchanged() -> None:
    plain = json.dumps({"shot_id": 7, "text": "field_meanings is a word here"})
    nested = json.dumps({"rows": [{"field_meanings": "a", "b": 1}]})
    assert without_field_meanings(nested) == nested

    assert without_field_meanings(plain) == plain
    assert without_field_meanings("not json") == "not json"


def test_the_preview_is_the_first_4000_characters_after_the_glossary() -> None:
    body = json.dumps({"field_meanings": "M" * 9000, "text": "T" * 9000})

    preview = event_preview(body)

    assert len(preview) == 4000 and "M" not in preview and preview.startswith('{"text": "TTT')
    assert event_preview("y" * 9000) == "y" * 4000
