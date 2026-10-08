"""One shot, rendered at each tier, and the rules every rendering keeps.

The goldens are `golden/shot-{base,extended,full}.txt`: a real shot (see the
conftest) as the model reads it. Regenerate with
`uv run pytest tests/shotinfo --update-golden` and read the diff — it is what
every chat would be told differently about every shot.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.shotinfo import catalogue
from gaggiclanker.shotinfo.catalogue import ITEMS, ShotTier, default_tiers, keys_in
from gaggiclanker.shotinfo.downsample import CURVE_POINTS
from gaggiclanker.shotinfo.facts import ShotFacts
from gaggiclanker.shotinfo.render import (
    Line,
    _selection,
    item_example,
    load_shots,
    needs_samples,
    render_shot,
    shot_lines,
)
from gaggiclanker.shotinfo.service import approximate_tokens
from gaggiclanker.sync.derive import derive_shot
from tests.domain.helpers import slog_from_export, standard_board
from tests.shotinfo.conftest import Archive, insert_shot_129

GOLDEN = Path(__file__).resolve().parent / "golden"


async def _one(db: Database, shot_id: int, *, samples: bool = True) -> ShotFacts:
    [facts] = await load_shots(db, [shot_id], samples=samples)
    return facts


def _headings(text: str) -> list[str]:
    return [line[1:-1] for line in text.split("\n") if line.startswith("[") and line.endswith("]")]


@pytest.mark.parametrize("tier", ["base", "extended", "full"])
async def test_the_checks_come_first_then_phases_then_the_rest_and_the_curve_last(
    archive: Archive, tier: str
) -> None:
    facts = await _one(archive.db, archive.shot)
    # The checks and the phase lines belong to different default tiers, so put
    # every item the tier would carry in this one to see all the groups at once.
    tiers = dict.fromkeys(default_tiers(), "extended" if tier == "full" else tier)
    found = _headings(render_shot(facts, tier, tiers, curve_points=CURVE_POINTS))  # type: ignore[arg-type]
    assert found[0] == "Checks"
    assert found[1] == "Phases" or tier == "base"
    if tier != "base":
        assert found[:2] == ["Checks", "Phases"]
        assert found[-1] == "Curve"
    rest = [g for g in found if g not in ("Checks", "Phases", "Curve")]
    catalogued = [g for g in catalogue.GROUPS if g in rest]
    assert rest == catalogued


async def test_phases_follow_the_checks_directly_even_when_the_tier_has_no_other_group_first(
    archive: Archive,
) -> None:
    facts = await _one(archive.db, archive.shot)
    tiers = dict.fromkeys(default_tiers(), "excluded")
    for key, item in ITEMS.items():
        if item.group in ("Checks", "Phases", "Outcome") and not item.locked:
            tiers[key] = "extended"
    found = _headings(render_shot(facts, "extended", tiers, curve_points=CURVE_POINTS))  # type: ignore[arg-type]
    assert found[:2] == ["Checks", "Phases"]


@pytest.mark.parametrize("tier", ["base", "extended", "full"])
async def test_a_shot_renders_as_its_golden_file(
    archive: Archive, update_golden: bool, tier: str
) -> None:
    facts = await _one(archive.db, archive.shot)
    rendered = render_shot(facts, tier, default_tiers(), curve_points=CURVE_POINTS)  # type: ignore[arg-type]

    path = GOLDEN / f"shot-{tier}.txt"
    if update_golden:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        pytest.skip("golden file rewritten")
    assert path.exists(), "run with --update-golden to create it"
    assert rendered == path.read_text(encoding="utf-8")


async def test_the_same_shot_renders_the_same_bytes(archive: Archive) -> None:
    first = render_shot(
        await _one(archive.db, archive.shot), "full", default_tiers(), curve_points=CURVE_POINTS
    )
    second = render_shot(
        await _one(archive.db, archive.shot), "full", default_tiers(), curve_points=CURVE_POINTS
    )

    assert first == second


def _pairs(lines: list[Line]) -> set[tuple[str, int | None, str]]:
    return {(line.key, line.phase, line.value) for line in lines}


async def test_extended_carries_no_base_item_and_full_is_both_item_by_item(
    archive: Archive,
) -> None:
    facts = await _one(archive.db, archive.shot)
    tiers = default_tiers()
    base = shot_lines(facts, keys_in("base", tiers))
    extended = shot_lines(facts, keys_in("extended", tiers))
    full = shot_lines(facts, keys_in("full", tiers))

    assert base and extended
    assert {line.key for line in extended}.isdisjoint(keys_in("base", tiers))
    assert {line.key for line in base} <= keys_in("base", tiers)
    assert _pairs(full) == _pairs(base) | _pairs(extended)

    rendered = render_shot(facts, "extended", tiers, curve_points=CURVE_POINTS)
    for line in base:
        if line.key != "shot_id":
            assert f"{ITEMS[line.key].label}: {line.value}" not in rendered, line.key


def _curve(rendered: str) -> list[str]:
    """The curve group's lines, up to the next group."""
    lines = rendered.split("[Curve]\n", 1)[1].splitlines()
    end = next((i for i, line in enumerate(lines) if line.startswith("[")), len(lines))
    return lines[:end]


def _times(table: list[str]) -> list[str]:
    return [row.split(",", 1)[0] for row in table[2:]]


async def test_the_curve_is_one_table_of_the_rows_the_selection_keeps(archive: Archive) -> None:
    facts = await _one(archive.db, archive.shot)
    assert facts.samples

    table = _curve(render_shot(facts, "extended", default_tiers(), curve_points=CURVE_POINTS))
    rows, _ = _selection(facts, CURVE_POINTS)

    assert 10 < len(rows) <= CURVE_POINTS < len(facts.samples)
    assert table[0] == (
        f"{len(rows)} of {len(facts.samples)} samples, shape-preserving; always kept: the first "
        "and last, each phase's first and last, peak pressure, first drip, both ends of the "
        "largest pressure drop"
    )
    assert table[1] == (
        "t (s),pressure (bar),target pressure (bar),puck flow (ml/s),target flow (ml/s),"
        "weight (g),temperature (°C),phase marker"
    )
    assert _times(table) == [f"{facts.samples[i].t_ms / 1000:.2f}" for i in rows]
    assert all(row.count(",") == 7 for row in table[2:])


async def test_a_budget_at_or_above_the_samples_writes_every_sample(archive: Archive) -> None:
    facts = await _one(archive.db, archive.shot)
    assert facts.samples
    count = len(facts.samples)

    for budget in (count, count + 100):
        table = _curve(render_shot(facts, "extended", default_tiers(), curve_points=budget))
        assert table[0] == f"all {count} samples"
        assert len(table) == 2 + count


async def test_the_budget_moves_the_row_count(archive: Archive) -> None:
    facts = await _one(archive.db, archive.shot)

    def count(budget: int) -> int:
        table = _curve(render_shot(facts, "extended", default_tiers(), curve_points=budget))
        return len(table) - 2

    assert count(10) < count(30) < count(CURVE_POINTS) < count(120)


async def test_moving_a_channel_between_tiers_never_moves_a_timestamp(archive: Archive) -> None:
    facts = await _one(archive.db, archive.shot)
    tiers = default_tiers()
    thinner = {
        **tiers,
        "curve_pressure": "excluded",
        "curve_puck_flow": "excluded",
        "curve_weight": "base",
    }

    everything = _curve(render_shot(facts, "extended", tiers, curve_points=CURVE_POINTS))
    extended = _curve(render_shot(facts, "extended", thinner, curve_points=CURVE_POINTS))
    base = _curve(render_shot(facts, "base", thinner, curve_points=CURVE_POINTS))

    assert (
        extended[1]
        == "t (s),target pressure (bar),target flow (ml/s),temperature (°C),phase marker"
    )
    assert base[1] == "t (s),weight (g)"
    assert _times(extended) == _times(everything) == _times(base)
    assert extended[0] == everything[0] == base[0]


async def test_a_machine_without_a_pressure_sensor_keeps_no_pressure_moment(
    archive: Archive,
) -> None:
    facts = await _one(archive.db, archive.no_pressure)

    table = _curve(render_shot(facts, "extended", default_tiers(), curve_points=CURVE_POINTS))

    # No pressure moment, and no first drip either: there is no puck flow to see it in.
    assert table[0].endswith("always kept: the first and last, each phase's first and last")
    assert "pressure (bar)" not in table[1].split(",")
    assert "puck flow (ml/s)" not in table[1].split(",")


async def test_shot_129_keeps_these_rows(archive: Archive, update_golden: bool) -> None:
    """The real shot's rows at the default budget, each with the moments it is kept for."""
    facts = await _one(archive.db, await insert_shot_129(archive.db))
    assert facts.samples is not None and len(facts.samples) == 213
    rows, events = _selection(facts, CURVE_POINTS)
    reasons: dict[int, list[str]] = {}
    for index in (0, len(facts.samples) - 1):
        reasons.setdefault(index, []).append("end")
    for index in events.phase_edges:
        reasons.setdefault(index, []).append("phase edge")
    for name, moment in (
        ("peak pressure", events.peak_pressure),
        ("first drip", events.first_drip),
    ):
        if moment is not None:
            reasons.setdefault(moment, []).append(name)
    for index in events.pressure_drop or ():
        reasons.setdefault(index, []).append("largest pressure drop")
    lines = []
    for index in rows:
        why = ", ".join(reasons.get(index, []))
        lines.append(
            f"{index}\t{facts.samples[index].t_ms / 1000:.2f}" + (f"\t{why}" if why else "")
        )
    text = "\n".join(lines) + "\n"

    path = GOLDEN / "shot-129-rows.txt"
    if update_golden:
        path.write_text(text, encoding="utf-8")
        pytest.skip("golden file rewritten")
    assert text == path.read_text(encoding="utf-8")


async def test_shot_129_s_curve_costs_a_few_hundred_tokens(archive: Archive) -> None:
    facts = await _one(archive.db, await insert_shot_129(archive.db))
    curve = "[Curve]\n" + "\n".join(
        _curve(render_shot(facts, "extended", default_tiers(), curve_points=CURVE_POINTS))
    )

    assert approximate_tokens(curve) < 700


async def test_a_tier_with_no_curve_channel_has_no_table_and_needs_no_samples(
    archive: Archive,
) -> None:
    facts = await _one(archive.db, archive.shot)

    assert not needs_samples("base", default_tiers())
    assert needs_samples("extended", default_tiers())
    assert "[Curve]" not in render_shot(facts, "base", default_tiers(), curve_points=CURVE_POINTS)


async def test_no_curve_is_written_when_the_samples_were_not_loaded(archive: Archive) -> None:
    facts = await _one(archive.db, archive.shot, samples=False)

    assert facts.samples is None
    assert "[Curve]" not in render_shot(
        facts, "extended", default_tiers(), curve_points=CURVE_POINTS
    )


async def test_a_shot_with_no_scale_has_no_weight_derived_values(archive: Archive) -> None:
    facts = await _one(archive.db, archive.no_scale)
    tiers = default_tiers()
    keys = {line.key for line in shot_lines(facts, keys_in("full", tiers))}
    rendered = render_shot(facts, "full", tiers, curve_points=CURVE_POINTS)

    assert "yield" not in keys
    assert "weight_rate" not in keys
    assert "phase_cup_end" not in keys
    assert "phase_scale_flow" not in keys
    assert "yield_share" not in keys
    assert "weight (g)" not in rendered, "a scale that was not there reads as absent, not zero"
    # Everything the scale does not feed is still there.
    assert {"shot_time", "peak_pressure", "brew_flow", "resistance_level"} <= keys
    assert "puck flow (ml/s)" in rendered


async def test_a_shot_with_no_pressure_sensor_has_no_pressure_derived_values(
    archive: Archive,
) -> None:
    facts = await _one(archive.db, archive.no_pressure)
    tiers = default_tiers()
    lines = shot_lines(facts, keys_in("full", tiers))
    keys = {line.key for line in lines}
    rendered = render_shot(facts, "full", tiers, curve_points=CURVE_POINTS)

    for absent in (
        "peak_pressure",
        "average_pressure",
        "minimum_pressure",
        "peak_pressure_time",
        "preinfusion_time",
        "main_extraction_time",
        "resistance_level",
        "resistance_slope",
        "pressure_adherence",
        "pressure_undershoot_max",
        "phase_pressure",
        "phase_pressure_peak",
        "phase_pressure_end",
        "phase_pressure_adherence",
        "phase_resistance",
        "brew_flow",
        "average_flow",
        "total_volume",
        "phase_flow",
        "phase_volume",
    ):
        assert absent not in keys, absent
    assert "pressure (bar)," not in rendered.replace("target pressure (bar),", "")
    # What does not need the sensor stays.
    assert {"shot_time", "yield", "average_temperature", "phase_start"} <= keys
    assert "target pressure (bar)" in rendered


async def test_the_judgement_reads_with_its_flavour_wheel_paths(archive: Archive) -> None:
    rendered = render_shot(
        await _one(archive.db, archive.shot), "base", default_tiers(), curve_points=CURVE_POINTS
    )

    assert "Taste notes: Sweet › Brown sugar › Caramelized; Fruity › Citrus fruit › Lemon" in (
        rendered
    )
    assert "Aroma notes: Floral › Floral › Jasmine" in rendered
    assert "Ratio: 1:2.03" in rendered
    assert "Label: Keep" in rendered
    assert "Counted: counted" in rendered


async def test_the_header_is_the_shot_id_and_is_not_repeated(archive: Archive) -> None:
    rendered = render_shot(
        await _one(archive.db, archive.shot), "base", default_tiers(), curve_points=CURVE_POINTS
    )

    assert rendered.startswith(f"shot {archive.shot}\n")
    assert "Shot id:" not in rendered


async def test_load_shots_keeps_the_order_asked_for_and_skips_what_is_not_there(
    archive: Archive,
) -> None:
    wanted = [archive.no_pressure, 999_999, archive.shot, archive.no_scale]

    loaded = await load_shots(archive.db, wanted)

    assert [facts.shot_id for facts in loaded] == [
        archive.no_pressure,
        archive.shot,
        archive.no_scale,
    ]
    assert all(facts.samples is None for facts in loaded)
    assert loaded[1].judgement is not None
    assert loaded[1].note is not None
    assert loaded[1].version is not None
    assert loaded[1].version.id == archive.version_id


async def test_load_shots_reads_a_fixed_number_of_queries_however_many_shots(
    archive: Archive, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No query per shot, and above all no query per shot's samples."""
    calls: list[str] = []
    fetch_all = archive.db.fetch_all
    fetch_one = archive.db.fetch_one

    async def counting_all(sql: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(sql)
        return await fetch_all(sql, *args, **kwargs)

    async def counting_one(sql: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(sql)
        return await fetch_one(sql, *args, **kwargs)

    monkeypatch.setattr(archive.db, "fetch_all", counting_all)
    monkeypatch.setattr(archive.db, "fetch_one", counting_one)

    await load_shots(archive.db, [archive.shot], samples=True)
    one = len(calls)
    calls.clear()
    await load_shots(
        archive.db, [archive.shot, archive.no_scale, archive.no_pressure], samples=True
    )

    assert len(calls) == one


async def test_summary_level_diagnostics_are_read_as_well(archive: Archive) -> None:
    """An older row holds the flat summary block; its items still render."""
    blob = {
        "summary": {"pressure": {"max_bar": 9.2}, "flow": {"time_to_first_drip_s": 8.2}},
        "diagnostics": {
            "has_pressure": True,
            "resistance_avg": 2.4,
            "resistance_slope": -0.04,
            "pressure_rmse_bar": 0.31,
            "max_overshoot_bar": 0.22,
            "flow_rmse_ml_s": 0.41,
            "max_flow_overshoot_ml_s": 0.55,
        },
        "has_pressure": True,
    }
    shot_id = await ShotsRepository(archive.db).insert(
        ShotInsert(
            device_id="000999",
            raw_slog=b"summary",
            duration_ms=28_000,
            diagnostics_json=json.dumps(blob),
        )
    )

    rendered = render_shot(
        await _one(archive.db, shot_id), "full", default_tiers(), curve_points=CURVE_POINTS
    )

    assert "Resistance level: 2.40" in rendered.splitlines()
    assert "Resistance slope: -0.04 /s" in rendered
    assert "Pressure adherence: 0.31 bar" in rendered.splitlines()
    assert "Flow adherence: 0.41 ml/s" in rendered.splitlines()
    assert "Peak pressure: 9.20 bar" in rendered
    assert "Set version: not filed in a Set" in rendered
    # No grade beside any number.
    assert not re.findall(r"\b[A-Z]{3,}_[A-Z_]{3,}\b", rendered)


def _with(facts: ShotFacts, **update: Any) -> ShotFacts:
    return ShotFacts(shot=facts.shot.model_copy(update=update))


@pytest.mark.parametrize(
    ("source", "said"),
    [
        ("machine", "Resistance level: 2.10, from the machine"),
        ("computed", "Resistance level: 2.10, computed from pressure and flow"),
        (None, "Resistance level: 2.10"),
    ],
)
async def test_a_summary_level_shot_says_where_its_resistance_came_from(
    archive: Archive, source: str | None, said: str
) -> None:
    """The flat summary blob carries `resistance_source`; a shot stored before it has none."""
    facts = await _one(archive.db, archive.shot, samples=False)
    diagnostics: dict[str, Any] = {
        "has_pressure": True,
        "resistance_avg": 2.1,
        "resistance_slope": -0.03,
    }
    if source is not None:
        diagnostics["resistance_source"] = source
    summary = _with(facts, diagnostics={**facts.blob, "diagnostics": diagnostics})
    assert not summary.full

    rendered = render_shot(summary, "base", default_tiers(), curve_points=CURVE_POINTS)

    assert said in rendered.splitlines()


async def test_a_choked_puck_shows_its_zero_brew_flow(archive: Archive) -> None:
    """Puck flow was recorded and nothing got through: 0.00 is a reading."""
    facts = await _one(archive.db, archive.shot, samples=False)
    blob = json.loads(json.dumps(facts.blob))
    blob["diagnostics"]["extraction"]["flow_avg_brew_ml_s"] = 0.0
    assert facts.shot.fields_mask is not None
    choked = _with(facts, diagnostics=blob)

    rendered = render_shot(choked, "full", default_tiers(), curve_points=CURVE_POINTS)

    assert choked.puck_flow_recorded
    assert "Average brew puck flow: 0.00 ml/s" in rendered


async def test_no_flow_item_is_shown_when_puck_flow_was_not_recorded(archive: Archive) -> None:
    """The same zeros with the field's bit clear are averages over nothing."""
    facts = await _one(archive.db, archive.shot, samples=False)
    blob = json.loads(json.dumps(facts.blob))
    blob["diagnostics"]["extraction"]["flow_avg_brew_ml_s"] = 0.0
    mask = facts.shot.fields_mask
    assert mask is not None
    unrecorded = _with(facts, diagnostics=blob, fields_mask=mask & ~(1 << 7))

    keys = {line.key for line in shot_lines(unrecorded, keys_in("full", default_tiers()))}

    assert not unrecorded.puck_flow_recorded
    for absent in (
        "brew_flow",
        "average_flow",
        "peak_flow",
        "total_volume",
        "flow_slope",
        "phase_flow",
        "phase_volume",
    ):
        assert absent not in keys, absent
    assert {"shot_time", "peak_pressure", "phase_start"} <= keys


async def test_a_row_with_no_field_mask_is_taken_at_its_word(archive: Archive) -> None:
    facts = await _one(archive.db, archive.shot, samples=False)

    assert _with(facts, fields_mask=None).puck_flow_recorded


async def test_a_stored_final_weight_of_zero_is_no_yield(archive: Archive) -> None:
    """The header writes 0 for a reading at or below zero: that is no weight, not 0 g."""
    facts = await _one(archive.db, archive.shot, samples=False)
    zero = _with(facts, final_weight_g=0.0)

    assert catalogue.yield_g(zero) is None
    assert "Yield:" not in render_shot(zero, "base", default_tiers(), curve_points=CURVE_POINTS)
    assert "Yield: 31.6 g" in render_shot(facts, "base", default_tiers(), curve_points=CURVE_POINTS)


async def test_an_example_is_absent_wherever_the_rendering_leaves_the_line_out(
    archive: Archive,
) -> None:
    """The settings page's "not on this shot" is the rendering's own absence."""
    full = await _one(archive.db, archive.shot)
    no_scale = await _one(archive.db, archive.no_scale)
    no_pressure = await _one(archive.db, archive.no_pressure)
    unsampled = await _one(archive.db, archive.shot, samples=False)

    assert item_example(full, "yield", curve_points=CURVE_POINTS) == "31.6 g"
    assert item_example(no_scale, "yield", curve_points=CURVE_POINTS) is None
    assert item_example(full, "curve_weight", curve_points=CURVE_POINTS) is not None
    assert item_example(no_scale, "curve_weight", curve_points=CURVE_POINTS) is None
    assert item_example(full, "curve_pressure", curve_points=CURVE_POINTS) is not None
    assert item_example(no_pressure, "curve_pressure", curve_points=CURVE_POINTS) is None
    assert item_example(unsampled, "curve_pressure", curve_points=CURVE_POINTS) is None
    # A phase item is one line per phase that has it, and no line for one that
    # does not: only the phase holding the first drip has its time.
    drips = item_example(full, "phase_cup_first_drip", curve_points=CURVE_POINTS)
    assert drips is not None
    assert all(": first drip " in line for line in drips.splitlines())
    assert len(drips.splitlines()) == 1 < len(full.phases)
    # The estimate is the figure of a shot with no scale: this one has a scale.
    assert item_example(full, "phase_first_drip", curve_points=CURVE_POINTS) is None
    assert item_example(full, "first_drip", curve_points=CURVE_POINTS) is None
    assert item_example(full, "cup_first_drip", curve_points=CURVE_POINTS) is not None
    bare_drips = item_example(no_scale, "phase_first_drip", curve_points=CURVE_POINTS)
    assert bare_drips is not None and "first drip (estimated)" in bare_drips
    assert item_example(no_scale, "cup_first_drip", curve_points=CURVE_POINTS) is None
    assert item_example(no_scale, "first_drip", curve_points=CURVE_POINTS) is not None
    assert len(
        (item_example(full, "phase_name", curve_points=CURVE_POINTS) or "").splitlines()
    ) == len(full.phases)


_FIRMWARE_KEYS = {
    "machine_puck_resistance",
    "liquid_resistance",
    "water_pumped",
    "water_minus_weight",
    "phase_machine_resistance",
    "phase_liquid_resistance",
}


def _firmware_keys(facts: ShotFacts, tier: str) -> set[str]:
    return {line.key for line in shot_lines(facts, keys_in(tier, default_tiers()))} & _FIRMWARE_KEYS  # type: ignore[arg-type]


async def test_the_firmware_values_render_in_extended_and_never_in_base(archive: Archive) -> None:
    facts = await _one(archive.db, archive.shot)
    base = render_shot(facts, "base", default_tiers(), curve_points=CURVE_POINTS)
    extended = render_shot(facts, "extended", default_tiers(), curve_points=CURVE_POINTS)

    assert "Machine puck resistance" not in base and "Liquid resistance" not in base
    assert _firmware_keys(facts, "base") == set()
    # A v5 shot: resistance yes, water no (there is no `wp` to count).
    assert _firmware_keys(facts, "extended") == {
        "machine_puck_resistance",
        "liquid_resistance",
        "phase_machine_resistance",
        "phase_liquid_resistance",
    }
    lines = extended.splitlines()
    assert (
        "Machine puck resistance (s·√bar/mL): avg 0.98 (start 0.39, end 0.88, min 0.16, max 1.84)"
        in lines
    )
    assert any(line.startswith("Liquid resistance (bar·s/mL): avg ") for line in lines)
    assert "Water pumped" not in extended
    # Every phase that has a valid reading says it, not only the brew phases.
    phases = [line for line in lines if line.startswith("phase ")]
    assert sum("machine puck resistance (s·√bar/mL) avg" in line for line in phases) == 2


async def test_a_shot_with_the_pump_count_shows_its_water_and_its_difference_to_the_yield(
    archive: Archive,
) -> None:
    slog = slog_from_export("shot-v7-synthetic.json")
    derived = derive_shot(slog, slog_to_raw(slog), device_id="000900", source="import")
    shot_id = await ShotsRepository(archive.db).insert(derived.shot, derived.samples)

    facts = await _one(archive.db, shot_id)
    lines = render_shot(facts, "extended", default_tiers(), curve_points=CURVE_POINTS).splitlines()

    assert "Water pumped: 9.4 ml" in lines
    assert slog.volume_g is not None
    assert f"Water pumped minus beverage weight: {9.4 - slog.volume_g:.1f} g" in lines


async def test_a_machine_with_no_pressure_sensor_has_no_firmware_resistance(
    archive: Archive,
) -> None:
    facts = await _one(archive.db, archive.no_pressure)

    assert _firmware_keys(facts, "extended") == set()


async def test_a_shot_derived_before_the_firmware_values_existed_renders_without_them(
    archive: Archive,
) -> None:
    facts = await _one(archive.db, archive.shot)
    old = ShotFacts(
        shot=facts.shot.model_copy(
            update={
                "diagnostics": {k: v for k, v in facts.blob.items() if k != "firmware"},
            }
        ),
        samples=facts.samples,
    )

    assert _firmware_keys(old, "extended") == set()


@pytest.mark.parametrize("tier", ["base", "extended", "full"])
async def test_a_standard_board_shot_shows_no_compliance_at_any_tier(
    archive: Archive, tier: ShotTier
) -> None:
    """No pressure and no flow was measured, so nothing is graded against the profile."""
    path = sorted((Path(__file__).resolve().parents[1] / "fixtures" / "slog").glob("*.slog"))[0]
    raw = path.read_bytes()
    slog = standard_board(parse_slog(raw))
    derived = derive_shot(slog, raw, device_id="000777")
    shot_id = await ShotsRepository(archive.db).insert(derived.shot, derived.samples)
    facts = await _one(archive.db, shot_id)

    text = render_shot(facts, tier, default_tiers(), curve_points=CURVE_POINTS)
    keys = {line.key for line in shot_lines(facts, frozenset(ITEMS))}
    for key in (
        "pressure_adherence",
        "flow_adherence",
        "pressure_overshoot_max",
        "pressure_undershoot_max",
        "flow_overshoot_max",
        "flow_undershoot_max",
    ):
        assert key not in keys, key
    assert "Flow adherence" not in text
