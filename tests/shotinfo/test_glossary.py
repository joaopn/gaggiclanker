"""The field glossary: every item a chat can see, explained, with every label.

A band label the model has never been told about is a label it reads from its
training, which knows nothing of this engine's thresholds. So the tests here
walk the vendored engine itself — every threshold table it has, and every
label its channeling code can assign — and require each one to be in the
meaning of the item that shows it.

The golden is `golden/glossary.txt`, rendered with the default tiers.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from gaggiclanker.domain import diagnostics as engine
from gaggiclanker.shotinfo.catalogue import (
    CATALOGUE,
    GROUP_NOTES,
    ITEMS,
    SHARED_BANDS,
    BandTable,
    Tier,
    band_text,
    default_tiers,
    keys_in,
)
from gaggiclanker.shotinfo.glossary import EXTENDED_TOOLS, Part, render_glossary, shared_bands_of
from gaggiclanker.shotinfo.render import load_shots, shot_lines
from tests.shotinfo.conftest import Archive

GOLDEN = Path(__file__).resolve().parent / "golden" / "glossary.txt"

#: Threshold tables the engine defines and never applies: they belong to
#: `_pressure_volatility_label`, which nothing in the engine calls. Listed so a
#: table that *is* applied cannot slip out of the glossary unnoticed.
NOT_EMITTED = {"_PRESSURE_CV_BANDS", "_PRESSURE_VOLATILITY_BANDS"}


def whole(tiers: Mapping[str, Tier]) -> str:
    """Both halves, as a reader holding the prompt and the first extended read has them."""
    return render_glossary(tiers, "base") + "\n\n" + render_glossary(tiers, "extended")


def _tables() -> dict[str, BandTable]:
    return {
        name: value
        for name, value in vars(engine).items()
        if re.fullmatch(r"_[A-Z_]+_BANDS", name) and isinstance(value, list)
    }


def _labels(table: BandTable) -> list[str]:
    return [label for _, label in table]


def _says(meaning: str, label: str) -> bool:
    return re.search(rf"(?<![A-Z_]){re.escape(label)}(?![A-Z_])", meaning) is not None


def test_the_engine_s_unused_tables_are_the_ones_named_here() -> None:
    assert "_pressure_volatility_label" in vars(engine)
    source = Path(engine.__file__).read_text(encoding="utf-8")
    assert source.count("_pressure_volatility_label(") == 1, "it is called now: map its tables"


def test_every_band_table_the_engine_applies_is_shown_by_an_item() -> None:
    shown = {
        name
        for name, table in _tables().items()
        if any(any(band is table for band in item.bands) for item in CATALOGUE)
    }

    assert set(_tables()) - shown == NOT_EMITTED


def _shared_lines(glossary: str) -> dict[str, str]:
    """The `[Shared bands]` section, as name → the line that lists the table."""
    lines = re.findall(r"^- (.+?) bands: (.+)$", glossary, flags=re.MULTILINE)
    return dict(lines)


def test_every_label_of_every_table_is_in_the_entry_or_the_table_it_names() -> None:
    """Each table an item is banded by is spelled out in its entry, or named there
    and listed once under [Shared bands] — never anywhere else."""
    glossary = whole(default_tiers())
    listed = _shared_lines(glossary)
    for item in CATALOGUE:
        if default_tiers()[item.key] == "excluded":
            continue
        entry = f"- {item.label} [{default_tiers()[item.key]}]: {item.meaning}"
        assert entry in glossary, item.key
        names = shared_bands_of(item)
        for table in item.bands:
            name = next((n for n, (shared, _) in SHARED_BANDS.items() if shared is table), None)
            if name is None:
                for label in _labels(table):
                    assert _says(entry, label), (item.key, label)
                continue
            assert name in names
            assert f"the {name} bands" in entry, (item.key, name)
            for label in _labels(table):
                assert _says(listed[name], label), (item.key, name, label)


def test_a_shared_table_is_listed_once_and_only_when_an_item_shown_names_it() -> None:
    glossary = whole(default_tiers())

    for name in SHARED_BANDS:
        assert glossary.count(f"- {name} bands: ") == 1, name
    hidden = {
        **default_tiers(),
        "pressure_adherence": "excluded",
        "flow_adherence": "excluded",
        "phase_pressure_adherence": "excluded",
    }
    assert "- adherence bands: " not in whole(hidden)
    assert "- slope bands: " in whole(hidden)


def test_each_group_note_is_written_under_its_heading() -> None:
    glossary = whole(default_tiers())

    for group, note in GROUP_NOTES.items():
        assert f"[{group}]\n{note}\n" in glossary, group


def test_band_text_names_every_label_of_ascending_and_descending_tables() -> None:
    for name, table in _tables().items():
        descending = table[-1][0] == float("-inf")
        text = band_text(table, descending=descending)
        for label in _labels(table):
            assert _says(text, label), (name, label)


def test_band_text_reads_the_thresholds_from_the_table() -> None:
    assert band_text([(0.5, "LOW"), (float("inf"), "HIGH")], "bar") == (
        "LOW under 0.5 bar; HIGH from 0.5 bar up"
    )
    assert band_text([(1.0, "UP"), (-1.0, "FLAT"), (float("-inf"), "DOWN")], descending=True) == (
        "UP from 1 up; FLAT -1 to 1; DOWN under -1"
    )
    assert "15 to 35 %" in band_text(engine._RESISTANCE_PEAK_TIMING_BANDS, "%", scale=100)


async def test_a_rendered_band_comes_from_the_tables_its_item_names(archive: Archive) -> None:
    """The mapping is right, not merely complete: the real shot's labels are in it."""
    shots = await load_shots(archive.db, [archive.shot, archive.no_scale])
    for facts in shots:
        for line in shot_lines(facts, keys_in("full", default_tiers())):
            item = ITEMS[line.key]
            if not item.bands:
                continue
            labels = {label for table in item.bands for label in _labels(table)}
            shown = set(re.findall(r"\b[A-Z][A-Z_]{2,}\b", line.value))
            assert shown, (line.key, line.value)
            assert shown <= labels, (line.key, line.value)


def test_every_channeling_risk_the_scoring_can_give_is_explained() -> None:
    grid = (0.0, 0.06, 0.2)
    risks = {
        engine._assess_channeling_risk(jitter, residual, -drop * 20, accel, pressure)
        for jitter, residual, drop, accel, pressure in itertools.product(
            grid, (None, *grid, 1.0), grid, grid, grid
        )
    }
    risks.add("INSUFFICIENT_DATA")  # the short-window branch of `_build_channeling`

    assert risks == {"LOW", "MODERATE", "HIGH", "VERY_HIGH", "INSUFFICIENT_DATA"}
    for risk in risks:
        assert _says(ITEMS["channeling_risk"].meaning, risk), risk


def test_every_primary_signal_name_is_explained() -> None:
    names: set[str] = set()
    for jitter, residual, drop, accel, pressure in itertools.product(
        (0.0, 0.2), (None, 0.0, 1.0), (0.0, -4.0), (0.0, 0.2), (0.0, 0.3)
    ):
        names |= set(
            engine._channeling_primary_signal(jitter, residual, drop, accel, pressure).split(",")
        )

    assert "none" in names and len(names) == 6
    for name in names:
        assert re.search(rf"\b{name}\b", ITEMS["primary_signal"].meaning), name


def test_every_flow_shape_and_window_confidence_is_explained() -> None:
    shapes = {
        engine._flow_shape_label([1.0 + slope * i for i in range(10)], 1.0)
        for slope in (-0.1, 0.0, 0.1)
    }
    confidences = {engine._window_confidence(n) for n in range(0, 40)}

    assert shapes == {"FLAT", "RAMPING_UP", "RAMPING_DOWN"}
    assert confidences == {"INSUFFICIENT", "LOW", "MEDIUM", "HIGH"}
    for shape in shapes:
        assert _says(ITEMS["flow_shape"].meaning, shape), shape
    for confidence in confidences:
        assert _says(ITEMS["window_confidence"].meaning, confidence), confidence


def test_the_labels_assigned_without_a_table_are_explained() -> None:
    """Assigned inline in the engine, so listed here from the code that assigns them."""
    # `compute_shot_diagnostics`' flow_trend.
    for label in ("DECLINING", "STABLE", "INCREASING"):
        assert _says(ITEMS["flow_slope"].meaning, label), label
    # `_classify_phase`.
    for phase_type in ("preinfusion", "brew", "decline"):
        assert re.search(rf"\b{phase_type}\b", ITEMS["phase_type"].meaning), phase_type


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


def test_every_shared_band_is_written_in_exactly_one_half_the_first_that_needs_it() -> None:
    tiers = default_tiers()
    base = render_glossary(tiers, "base")
    extended = render_glossary(tiers, "extended")

    for name in SHARED_BANDS:
        in_base = base.count(f"- {name} bands: ")
        in_extended = extended.count(f"- {name} bands: ")
        base_names = any(name in shared_bands_of(i) for i in CATALOGUE if tiers[i.key] == "base")
        extended_names = any(
            name in shared_bands_of(i) for i in CATALOGUE if tiers[i.key] == "extended"
        )
        assert (in_base, in_extended) == (
            (1, 0) if base_names else (0, 1 if extended_names else 0)
        ), name
    # And the case that separates the two rules: with every item naming `adherence` moved to
    # extended, the table moves with them instead of being lost.
    moved: dict[str, Tier] = {
        **tiers,
        **{i.key: "extended" for i in CATALOGUE if "adherence" in shared_bands_of(i)},
    }
    assert "- adherence bands: " not in render_glossary(moved, "base")
    assert render_glossary(moved, "extended").count("- adherence bands: ") == 1


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
        assert name in preamble.split("they arrive")[1], name


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
    moved = {**default_tiers(), "processing_note": "extended", "rating": "excluded"}

    rendered = whole(moved)

    assert f"- Processing note [extended]: {ITEMS['processing_note'].meaning}" in rendered
    assert f"- Processing note [extended]: {ITEMS['processing_note'].meaning}" in render_glossary(
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
