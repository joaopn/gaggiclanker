"""Which expectations a patch to a profile touches: every row of the table, in two halves.

Each row is shown flagged when the value it depends on changes and not flagged when it does
not (the second half is only worth reading because the first half shows the same row, the same
expectation and the same window would have been flagged). The profile is the constructed lever
profile with a pressure phase and a flow phase written into it; the expectations are the
measures an agent writes.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from gaggiclanker.domain.metric_language import Expression
from gaggiclanker.signatures.depends import affected_by
from tests.lever_shot import LEVER_PROFILE


class Row:
    def __init__(self, number: int, expression: dict[str, Any]) -> None:
        self.id = number
        self.kind = "measure"
        self.expression = Expression.model_validate(expression)
        self.sentence = f"row {number}"


def measure(channel: str, op: str, window: dict[str, Any] | None = None, **extra: Any) -> Any:
    body: dict[str, Any] = {"channel": channel, "op": op, "window": window or {}}
    body["compare"] = {"op": ">=", "value": 1}
    body.update(extra)
    return body


def base_document() -> dict[str, Any]:
    document = copy.deepcopy(LEVER_PROFILE)
    document["temperature"] = 95.0
    document["phases"][2]["pump"] = {"target": "pressure", "pressure": 9.0, "flow": 0.0}
    document["phases"][3]["pump"] = {"target": "flow", "pressure": 0.0, "flow": 2.0}
    return document


def flagged(patched: dict[str, Any], *rows: Row) -> list[int]:
    return [f.expectation_id for f in affected_by(base_document(), patched, rows)]


def patch(edit: Any) -> dict[str, Any]:
    document = base_document()
    edit(document)
    return document


# ── the profile's temperature ────────────────────────────────────────


def test_a_temperature_change_flags_the_absolute_temperature_measures() -> None:
    rows = (
        Row(1, measure("temperature", "min", {"phase": "soak"})),
        Row(2, measure("target_temperature", "mean")),
        Row(3, measure("temperature", "time_above", {"phase": "ramp"}, threshold=92)),
    )
    cooler = patch(lambda d: d.update(temperature=93.0))
    flags = affected_by(base_document(), cooler, rows)
    assert [f.expectation_id for f in flags] == [1, 2, 3]
    assert flags[0].cause == "the temperature changed from 95 to 93 °C"
    assert flags[0].sentence == "row 1"
    # The other half: the same expectations, the same patch with the temperature untouched.
    assert flagged(base_document(), *rows) == []
    assert flagged(patch(lambda d: d.update(description="other words")), *rows) == []


def test_an_offset_temperature_measure_is_never_flagged() -> None:
    plain = Row(1, measure("temperature", "min", {"phase": "soak"}))
    offset = Row(
        2, measure("temperature", "min", {"phase": "soak"}, offset_from="profile_temperature")
    )
    cooler = patch(lambda d: d.update(temperature=93.0))
    assert flagged(cooler, plain, offset) == [1]
    assert flagged(cooler, offset) == []


def test_other_channels_and_non_level_ops_do_not_follow_the_temperature() -> None:
    plain = Row(1, measure("temperature", "min"))
    rows = (
        Row(2, measure("pressure", "max")),
        Row(3, measure("temperature", "change", {"phase": "soak"})),
        Row(4, measure("temperature", "slope", {"phase": "soak"})),
    )
    cooler = patch(lambda d: d.update(temperature=93.0))
    assert flagged(cooler, plain, *rows) == [1]


def test_a_temperature_that_becomes_unset_or_set_is_a_change() -> None:
    row = Row(1, measure("temperature", "min"))
    assert flagged(patch(lambda d: d.update(temperature=0.0)), row) == [1]
    unset = base_document()
    unset["temperature"] = 0.0
    now_set = affected_by(unset, base_document(), [row])
    assert [f.cause for f in now_set] == ["the temperature was not set and is now 95 °C"]


# ── a phase's pressure ───────────────────────────────────────────────


def test_a_pressure_change_in_one_phase_flags_that_phases_and_the_shot_wide_measures() -> None:
    in_ramp = Row(1, measure("pressure", "mean", {"phase": "ramp"}))
    in_soak = Row(2, measure("pressure", "mean", {"phase": "soak"}))
    shot_wide = Row(3, measure("target_pressure", "max"))
    resistance = Row(4, measure("resistance", "mean", {"phase": "ramp"}))
    flow = Row(5, measure("puck_flow", "mean", {"phase": "ramp"}))
    lower = patch(lambda d: d["phases"][2]["pump"].update(pressure=8.0))
    flags = affected_by(base_document(), lower, [in_ramp, in_soak, shot_wide, resistance, flow])
    assert [f.expectation_id for f in flags] == [1, 3, 4]
    assert flags[0].cause == "the ramp pressure target changed from 9 bar to 8 bar"
    # The other half: the same rows with the ramp's pressure as it was.
    same = patch(lambda d: d["phases"][2]["pump"].update(pressure=9.0))
    assert flagged(same, in_ramp, in_soak, shot_wide, resistance, flow) == []


# ── a phase's flow ───────────────────────────────────────────────────


def test_a_flow_change_flags_the_flow_channels_of_that_phase() -> None:
    rows = [
        Row(1, measure("target_flow", "mean", {"phase": "decline"})),
        Row(2, measure("pump_flow", "max", {"phase": "decline"})),
        Row(3, measure("puck_flow", "mean", {"phase": "decline"})),
        Row(4, measure("scale_flow", "max", {"phase": "decline"})),
        Row(5, measure("scale_flow", "max", {"phase": "ramp"})),
        Row(6, measure("pressure", "max", {"phase": "decline"})),
    ]
    faster = patch(lambda d: d["phases"][3]["pump"].update(flow=3.0))
    flags = affected_by(base_document(), faster, rows)
    assert [f.expectation_id for f in flags] == [1, 2, 3, 4]
    assert flags[0].cause == "the decline flow target changed from 2 ml/s to 3 ml/s"
    assert flagged(base_document(), *rows) == []


# ── a phase's volumetric and pumped targets ──────────────────────────


def test_a_weight_target_flags_the_absolute_cup_weight_measures_only() -> None:
    absolute = Row(1, measure("cup_weight", "at_end", {"phase": "decline"}))
    share = Row(2, measure("cup_weight", "at_end", {"phase": "decline"}, relative_to="dose"))
    elsewhere = Row(3, measure("cup_weight", "at_end", {"phase": "ramp"}))
    longer = patch(
        lambda d: d["phases"][3].update(
            targets=[{"type": "volumetric", "operator": "gte", "value": 40}]
        )
    )
    assert flagged(longer, absolute, share, elsewhere) == [1]
    assert flagged(base_document(), absolute, share, elsewhere) == []


def test_a_pumped_target_flags_the_water_pumped_measures_of_that_phase() -> None:
    water = Row(1, measure("water_pumped", "at_end", {"phase": "ramp"}))
    other = Row(2, measure("water_pumped", "at_end", {"phase": "soak"}))
    cup = Row(3, measure("cup_weight", "at_end", {"phase": "ramp"}))
    more = patch(
        lambda d: d["phases"][2].update(
            targets=[{"type": "pumped", "operator": "gte", "value": 30}]
        )
    )
    assert flagged(more, water, other, cup) == [1]
    assert flagged(base_document(), water, other, cup) == []


# ── a phase's duration or exit ───────────────────────────────────────


def test_a_duration_or_exit_change_flags_the_time_measures_of_that_phase() -> None:
    duration = Row(1, measure("pressure", "duration", {"phase": "soak"}))
    time_to = Row(2, measure("pressure", "time_to", {"phase": "soak"}, threshold=2))
    elsewhere = Row(3, measure("pressure", "duration", {"phase": "ramp"}))
    level = Row(4, measure("temperature", "min", {"phase": "soak"}))
    longer = patch(lambda d: d["phases"][1].update(duration=14))
    flags = affected_by(base_document(), longer, [duration, time_to, elsewhere, level])
    assert [f.expectation_id for f in flags] == [1, 2]
    assert flags[0].cause == "the soak duration changed from 10 s to 14 s"
    assert flagged(base_document(), duration, time_to, elsewhere, level) == []
    exit_moved = patch(
        lambda d: d["phases"][1].update(
            targets=[{"type": "pressure", "operator": "gte", "value": 3}]
        )
    )
    assert flagged(exit_moved, duration, elsewhere) == [1]


# ── a window can be a span, and a phase can be renamed or removed ────


def test_a_span_is_the_phases_between_its_phase_anchors() -> None:
    span = {"from": {"phase_start": "soak"}, "to": {"phase_end": "ramp"}}
    inside = Row(1, measure("pressure", "mean", span))
    ramp_only = Row(2, measure("pressure", "mean", {"phase": "ramp"}))
    lower = patch(lambda d: d["phases"][2]["pump"].update(pressure=8.0))
    assert flagged(lower, inside) == [1]
    before = {"from": {"phase_start": "preinfusion"}, "to": {"phase_end": "soak"}}
    outside = Row(3, measure("pressure", "mean", before))
    assert flagged(lower, outside, ramp_only) == [2]
    # A span with a time anchor is not bound to phases: it may cover the changed one.
    timed = Row(4, measure("pressure", "mean", {"from": {"at_s": 1}, "to": "shot_end"}))
    assert flagged(lower, timed) == [4]


def test_a_renamed_or_removed_phase_is_not_flagged_it_needs_a_new_phase() -> None:
    row = Row(1, measure("pressure", "mean", {"phase": "ramp"}))
    lower = patch(lambda d: d["phases"][2]["pump"].update(pressure=8.0))
    assert flagged(lower, row) == [1]
    renamed = patch(
        lambda d: d["phases"][2].update(
            name="build", pump={"target": "pressure", "pressure": 8.0, "flow": 0.0}
        )
    )
    assert flagged(renamed, row) == []
    removed = patch(lambda d: d["phases"].pop(2))
    assert flagged(removed, row) == []


def test_one_flag_per_expectation_with_every_cause() -> None:
    row = Row(1, measure("pressure", "time_above", {"phase": "ramp"}, threshold=5))
    both = patch(
        lambda d: (
            d["phases"][2]["pump"].update(pressure=8.0),
            d["phases"][2].update(duration=14),
        )
    )
    (flag,) = affected_by(base_document(), both, [row])
    assert "pressure target changed" in flag.cause and "duration changed" in flag.cause


@pytest.mark.parametrize("kind", ["reached", "expects_warning", "free_text"])
def test_only_measures_are_flagged(kind: str) -> None:
    row = Row(1, measure("temperature", "min"))
    row.kind = kind
    assert flagged(patch(lambda d: d.update(temperature=93.0)), row) == []


def test_a_change_of_a_pressure_target_flags_levels_and_times_not_changes_and_slopes() -> None:
    lower = patch(lambda d: d["phases"][2]["pump"].update(pressure=8.0))
    level = Row(1, measure("pressure", "mean", {"phase": "ramp"}))
    time = Row(2, measure("pressure", "time_above", {"phase": "ramp"}, threshold=5))
    rise = Row(3, measure("pressure", "change", {"phase": "ramp"}))
    slope = Row(4, measure("pressure", "slope", {"phase": "ramp"}))
    assert flagged(lower, level, time) == [1, 2]
    # The other half of the second claim: the same channel, window and patch, a difference of it.
    assert flagged(lower, rise, slope) == []


def test_a_phase_that_is_gone_keeps_its_rows_out_even_when_the_temperature_moves() -> None:
    """The temperature cause belongs to no phase, so only the missing-phase rule keeps a row
    that needs a new phase (and says so as it is carried) from also being flagged."""
    on_soak = Row(1, measure("temperature", "min", {"phase": "soak"}))
    on_ramp = Row(2, measure("temperature", "min", {"phase": "ramp"}))
    span = Row(
        3,
        measure(
            "temperature",
            "mean",
            {"start": {"phase_start": "soak"}, "end": {"phase_end": "ramp"}},
        ),
    )
    shot = Row(4, measure("temperature", "min"))
    cooler = patch(lambda d: d.update(temperature=93.0))
    # First half: with every phase there, the temperature flags all four.
    assert flagged(cooler, on_soak, on_ramp, span, shot) == [1, 2, 3, 4]
    renamed = patch(lambda d: (d.update(temperature=93.0), d["phases"][1].update(name="settle")))
    assert [p["name"] for p in renamed["phases"]].count("soak") == 0
    # The soak is gone: what names it needs a new phase; the others are flagged as before.
    assert flagged(renamed, on_soak, on_ramp, span, shot) == [2, 4]
