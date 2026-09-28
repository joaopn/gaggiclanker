"""One shot, rendered at each tier, and the rules every rendering keeps.

The goldens are `golden/shot-{base,extended,full}.txt`: a real shot (see the
conftest) as the model reads it. Regenerate with
`uv run pytest tests/shotinfo --update-golden` and read the diff — it is what
every chat would be told differently about every shot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.shotinfo import catalogue
from gaggiclanker.shotinfo.catalogue import ITEMS, default_tiers, keys_in
from gaggiclanker.shotinfo.facts import ShotFacts
from gaggiclanker.shotinfo.render import (
    Line,
    item_example,
    load_shots,
    needs_samples,
    render_shot,
    shot_lines,
)
from tests.shotinfo.conftest import Archive

GOLDEN = Path(__file__).resolve().parent / "golden"


async def _one(db: Database, shot_id: int, *, samples: bool = True) -> ShotFacts:
    [facts] = await load_shots(db, [shot_id], samples=samples)
    return facts


@pytest.mark.parametrize("tier", ["base", "extended", "full"])
async def test_a_shot_renders_as_its_golden_file(
    archive: Archive, update_golden: bool, tier: str
) -> None:
    facts = await _one(archive.db, archive.shot)
    rendered = render_shot(facts, tier, default_tiers())  # type: ignore[arg-type]

    path = GOLDEN / f"shot-{tier}.txt"
    if update_golden:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        pytest.skip("golden file rewritten")
    assert path.exists(), "run with --update-golden to create it"
    assert rendered == path.read_text(encoding="utf-8")


async def test_the_same_shot_renders_the_same_bytes(archive: Archive) -> None:
    first = render_shot(await _one(archive.db, archive.shot), "full", default_tiers())
    second = render_shot(await _one(archive.db, archive.shot), "full", default_tiers())

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

    rendered = render_shot(facts, "extended", tiers)
    for line in base:
        if line.key != "shot_id":
            assert f"{ITEMS[line.key].label}: {line.value}" not in rendered, line.key


async def test_the_curve_is_one_table_at_full_resolution(archive: Archive) -> None:
    facts = await _one(archive.db, archive.shot)
    assert facts.samples

    rendered = render_shot(facts, "extended", default_tiers())
    table = rendered.split("[Curve]\n", 1)[1].splitlines()

    assert table[0] == f"{len(facts.samples)} samples"
    assert table[1] == (
        "t (s),pressure (bar),target pressure (bar),puck flow (ml/s),target flow (ml/s),"
        "weight (g),temperature (°C),phase marker"
    )
    assert len(table) == 2 + len(facts.samples)
    assert all(row.count(",") == 7 for row in table[2:])


async def test_a_tier_with_no_curve_channel_has_no_table_and_needs_no_samples(
    archive: Archive,
) -> None:
    facts = await _one(archive.db, archive.shot)

    assert not needs_samples("base", default_tiers())
    assert needs_samples("extended", default_tiers())
    assert "[Curve]" not in render_shot(facts, "base", default_tiers())


async def test_no_curve_is_written_when_the_samples_were_not_loaded(archive: Archive) -> None:
    facts = await _one(archive.db, archive.shot, samples=False)

    assert facts.samples is None
    assert "[Curve]" not in render_shot(facts, "extended", default_tiers())


async def test_a_shot_with_no_scale_has_no_weight_derived_values(archive: Archive) -> None:
    facts = await _one(archive.db, archive.no_scale)
    tiers = default_tiers()
    keys = {line.key for line in shot_lines(facts, keys_in("full", tiers))}
    rendered = render_shot(facts, "full", tiers)

    assert "yield" not in keys
    assert "weight_rate" not in keys
    assert "weight_rate_variability" not in keys
    assert "weight (g)" not in rendered, "a scale that was not there reads as absent, not zero"
    # Everything the scale does not feed is still there.
    assert {"shot_time", "peak_pressure", "brew_flow", "channeling_risk"} <= keys
    assert "puck flow (ml/s)" in rendered


async def test_a_shot_with_no_pressure_sensor_has_no_pressure_derived_values(
    archive: Archive,
) -> None:
    facts = await _one(archive.db, archive.no_pressure)
    tiers = default_tiers()
    lines = shot_lines(facts, keys_in("full", tiers))
    keys = {line.key for line in lines}
    rendered = render_shot(facts, "full", tiers)

    for absent in (
        "peak_pressure",
        "average_pressure",
        "minimum_pressure",
        "peak_pressure_time",
        "pressure_auc",
        "pressure_slope",
        "preinfusion_time",
        "main_extraction_time",
        "resistance_level",
        "resistance_erosion",
        "channeling_risk",
        "flow_jitter",
        "pressure_adherence",
        "pressure_overshoot_max",
        "phase_pressure",
        "phase_pressure_adherence",
    ):
        assert absent not in keys, absent
    assert "pressure (bar)," not in rendered.replace("target pressure (bar),", "")
    # What does not need the sensor stays.
    assert {"shot_time", "yield", "brew_flow", "average_temperature", "phase_start"} <= keys
    assert "target pressure (bar)" in rendered


async def test_the_judgement_reads_with_its_flavour_wheel_paths(archive: Archive) -> None:
    rendered = render_shot(await _one(archive.db, archive.shot), "base", default_tiers())

    assert "Taste notes: Sweet › Brown sugar › Caramelized; Fruity › Citrus fruit › Lemon" in (
        rendered
    )
    assert "Aroma notes: Floral › Floral › Jasmine" in rendered
    assert "Ratio: 1:2.03" in rendered
    assert "Label: Keep" in rendered
    assert "Counted: counted" in rendered


async def test_the_header_is_the_shot_id_and_is_not_repeated(archive: Archive) -> None:
    rendered = render_shot(await _one(archive.db, archive.shot), "base", default_tiers())

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
            "channeling_risk": "LOW",
            "temperature_stability_c": 0.42,
            "pressure_rmse_bar": 0.31,
            "max_overshoot_bar": 0.22,
            "flow_rmse_ml_s": 0.41,
            "max_flow_overshoot_ml_s": 0.55,
            "annotations": {
                "resistance_level": "MODERATE",
                "resistance_erosion": "GRADUAL_DECLINE",
                "channeling_risk": "LOW",
                "pressure_adherence": "EXCELLENT",
                "pressure_overshoot": "WITHIN_TOLERANCE",
                "temperature_stability": "STABLE",
                "flow_adherence": "GOOD",
                "flow_overshoot": "MINOR_DEVIATION",
            },
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

    rendered = render_shot(await _one(archive.db, shot_id), "full", default_tiers())

    assert "Resistance level: 2.40 MODERATE" in rendered
    assert "Resistance erosion: -0.04 /s GRADUAL_DECLINE" in rendered
    assert "Channeling risk: LOW" in rendered
    assert "Temperature stability: 0.42 °C STABLE" in rendered
    assert "Pressure adherence: 0.31 bar EXCELLENT" in rendered
    assert "Flow adherence: 0.41 ml/s GOOD" in rendered
    assert "Largest pressure overshoot: 0.22 bar WITHIN_TOLERANCE" in rendered
    assert "Largest flow overshoot: 0.55 ml/s MINOR_DEVIATION" in rendered
    assert "Peak pressure: 9.20 bar" in rendered
    assert "Set version: not filed in a Set" in rendered


async def test_an_indicator_the_engine_did_not_assess_is_absent_not_zero(
    archive: Archive,
) -> None:
    """A window too short to judge carries placeholder zeros beside `N/A`."""
    facts = await _one(archive.db, archive.shot, samples=False)
    blob = json.loads(json.dumps(facts.blob))
    channeling = blob["diagnostics"]["channeling"]
    channeling.update(
        {
            "channeling_risk": "INSUFFICIENT_DATA",
            "flow_jitter_ml_s": 0.0,
            "flow_vs_target_residual_ml_s": None,
            "pressure_max_drop_rate_bar_s": 0.0,
            "flow_acceleration_late_ml_s2": 0.0,
            "pressure_jitter_bar": 0.0,
            "flow_spread_ml_s": 0.0,
        }
    )
    for key in ("flow_jitter", "flow_vs_target", "pressure_drop", "late_flow_trend"):
        channeling["annotations"][key] = "N/A"
    channeling["annotations"]["pressure_jitter"] = "N/A"
    starved = ShotFacts(shot=facts.shot.model_copy(update={"diagnostics": blob}))

    keys = {line.key for line in shot_lines(starved, keys_in("full", default_tiers()))}

    assert "channeling_risk" in keys
    for absent in (
        "flow_jitter",
        "flow_vs_target",
        "pressure_drop_rate",
        "late_flow_acceleration",
        "pressure_jitter",
        "flow_spread",
    ):
        assert absent not in keys, absent


def _with(facts: ShotFacts, **update: Any) -> ShotFacts:
    return ShotFacts(shot=facts.shot.model_copy(update=update))


async def test_a_choked_puck_shows_its_zero_brew_flow(archive: Archive) -> None:
    """Puck flow was recorded and nothing got through: 0.00 is a reading."""
    facts = await _one(archive.db, archive.shot, samples=False)
    blob = json.loads(json.dumps(facts.blob))
    blob["diagnostics"]["extraction"]["flow_avg_brew_ml_s"] = 0.0
    assert facts.shot.fields_mask is not None
    choked = _with(facts, diagnostics=blob)

    rendered = render_shot(choked, "full", default_tiers())

    assert choked.puck_flow_recorded
    assert "Average brew flow: 0.00 ml/s" in rendered


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
    assert "Yield:" not in render_shot(zero, "base", default_tiers())
    assert "Yield: 31.6 g" in render_shot(facts, "base", default_tiers())


async def test_an_example_is_absent_wherever_the_rendering_leaves_the_line_out(
    archive: Archive,
) -> None:
    """The settings page's "not on this shot" is the rendering's own absence."""
    full = await _one(archive.db, archive.shot)
    no_scale = await _one(archive.db, archive.no_scale)
    no_pressure = await _one(archive.db, archive.no_pressure)
    unsampled = await _one(archive.db, archive.shot, samples=False)

    assert item_example(full, "yield") == "31.6 g"
    assert item_example(no_scale, "yield") is None
    assert item_example(full, "curve_weight") is not None
    assert item_example(no_scale, "curve_weight") is None
    assert item_example(full, "curve_pressure") is not None
    assert item_example(no_pressure, "curve_pressure") is None
    assert item_example(unsampled, "curve_pressure") is None
    # A phase item is one line per phase that has it, and no line for one that
    # does not: the ramp rate belongs to pre-infusion phases only.
    ramps = item_example(full, "phase_ramp")
    assert ramps is not None
    assert all(": ramp " in line for line in ramps.splitlines())
    assert len(ramps.splitlines()) < len(full.phases)
    assert len((item_example(full, "phase_name") or "").splitlines()) == len(full.phases)
