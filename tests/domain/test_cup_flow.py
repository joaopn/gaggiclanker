"""Cup flow: the flow the scale sees, one definition per test.

The expected values are worked out here from the parsed samples of the three real
`.slog` fixtures; a machine with no scale is the same shot with ``v`` and ``vf``
zeroed, as the firmware writes it, never a trace with nothing in it.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from gaggiclanker.domain.cup_flow import cup_first_drip_index, cup_flow, mean_cup_flow
from gaggiclanker.domain.diagnostics import (
    as_sample_dicts,
    calculate_summary,
    compute_shot_diagnostics,
    first_drip_index,
)
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.slog import Slog, parse_slog
from gaggiclanker.sync.derive import DERIVATION_VERSION, derive_shot
from tests.domain.helpers import SLOG_FIXTURES

#: Where coffee first reached the cup on each real shot, in seconds (a sample is 0.25 s).
FIRST_DRIP_S = {
    "shot_196_baseline_high": 12.0,
    "shot_204_ramping_flow": 24.0,
    "shot_222_hold_false_positive": 18.25,
}
SHOTS = [
    pytest.param(parse_slog((SLOG_FIXTURES / f"{name}.slog").read_bytes()), name, id=name)
    for name in FIRST_DRIP_S
]


def zeroed_scale(slog: Slog) -> Slog:
    """The shot as a machine with no scale logs it: ``v`` and ``vf`` zero, the rest untouched."""
    samples = [s.model_copy(update={"v": 0.0, "vf": 0.0}) for s in slog.samples]
    return dataclasses.replace(slog, samples=samples)


def derived_blob(slog: Slog) -> dict[str, Any]:
    derived = derive_shot(slog, slog_to_raw(slog), device_id="000001")
    assert derived.shot.diagnostics_json is not None
    blob: dict[str, Any] = json.loads(derived.shot.diagnostics_json)
    return blob


# 1. Cup flow is the stored vf, read at zero when it is below zero.


def test_cup_flow_reads_vf_and_floors_it_at_zero() -> None:
    assert cup_flow({"vf": 1.8}) == 1.8
    assert cup_flow({"vf": -20.0}) == 0.0
    assert cup_flow({"vf": 0.0}) == 0.0
    assert cup_flow({"pf": 3.0}) is None  # not recorded is not zero


def test_shot_204_starts_with_the_old_tare_glitch_and_reads_it_as_zero() -> None:
    slog = parse_slog((SLOG_FIXTURES / "shot_204_ramping_flow.slog").read_bytes())
    stored = [s.vf for s in slog.samples[:4]]
    assert stored[0] == pytest.approx(-20.0)
    assert cup_flow(as_sample_dicts(slog)[0]) == 0.0
    assert all((cup_flow(s) or 0.0) >= 0.0 for s in as_sample_dicts(slog))


# 2. A shot has a scale when a brew-phase weight is above zero; without one, no cup value.


@pytest.mark.parametrize(("slog", "name"), SHOTS)
def test_a_shot_without_a_scale_has_no_cup_numbers_anywhere(slog: Slog, name: str) -> None:
    blank = zeroed_scale(slog)
    assert all(s.v == 0.0 and s.vf == 0.0 for s in blank.samples)
    summary = calculate_summary(blank)
    assert summary["flow"]["cup_first_drip_s"] is None
    diagnostics = compute_shot_diagnostics(blank)
    assert diagnostics is not None
    assert diagnostics["extraction"]["cup_flow_avg_brew_g_s"] is None
    # The puck-flow numbers are still there: only the cup's are absent.
    assert summary["flow"]["time_to_first_drip_s"] is not None
    stored = derived_blob(blank)
    assert stored["summary"]["flow"]["cup_first_drip_s"] is None
    assert stored["diagnostics"]["extraction"]["cup_flow_avg_brew_g_s"] is None
    phases_json = derive_shot(blank, slog_to_raw(blank), device_id="000001").shot.phases_json
    assert phases_json is not None
    phases = json.loads(phases_json)
    for phase in phases:
        assert not {k for k in phase.get("metrics", {}) if k.startswith("scale_")}


# 3. First drip in the cup: the weight reaches the first reading plus half a gram.


@pytest.mark.parametrize(("slog", "name"), SHOTS)
def test_first_drip_in_the_cup_on_the_real_shots(slog: Slog, name: str) -> None:
    samples = as_sample_dicts(slog)
    index = cup_first_drip_index(samples)
    assert index is not None
    assert samples[index]["t"] / 1000 == FIRST_DRIP_S[name]
    first = samples[0]["v"]
    assert samples[index]["v"] >= first + 0.5
    assert all(s["v"] < first + 0.5 for s in samples[:index])
    assert calculate_summary(slog)["flow"]["cup_first_drip_s"] == FIRST_DRIP_S[name]
    stored = derived_blob(slog)
    assert stored["summary"]["flow"]["cup_first_drip_s"] == FIRST_DRIP_S[name]


def test_first_drip_in_the_cup_is_relative_to_the_first_reading() -> None:
    rows = [{"v": 10.0}, {"v": 10.4}, {"v": 10.5}, {"v": 12.0}]
    assert cup_first_drip_index(rows) == 2
    assert cup_first_drip_index([{"v": 0.0}, {"v": 0.2}]) is None
    assert cup_first_drip_index([{"pf": 1.0}]) is None


# 4. Brew cup flow: the mean over the brew samples flow_avg_brew_ml_s averages.


@pytest.mark.parametrize(("slog", "name"), SHOTS)
def test_brew_cup_flow_times_the_brew_duration_is_the_brew_weight_gain(
    slog: Slog, name: str
) -> None:
    diagnostics = compute_shot_diagnostics(slog)
    assert diagnostics is not None
    flow = diagnostics["extraction"]["cup_flow_avg_brew_g_s"]
    assert flow is not None
    brew = [
        s
        for s in as_sample_dicts(slog)
        if int(s.get("phase", -1)) >= 0 and s["t"] >= _brew_start_ms(slog)
    ]
    # The same samples flow_avg_brew_ml_s averages.
    expected = mean_cup_flow(brew)
    assert expected is not None
    assert flow == pytest.approx(expected, abs=0.006)
    duration = len(brew) * slog.sample_interval / 1000
    gain = brew[-1]["v"] - brew[0]["v"]
    assert flow * duration == pytest.approx(gain, rel=0.10)


def _brew_start_ms(slog: Slog) -> float:
    from gaggiclanker.domain.diagnostics import _brew_phase_positions

    samples = as_sample_dicts(slog)
    positions = _brew_phase_positions(samples, slog.transitions)
    return samples[positions[0]]["t"]


# 5. The puck-flow numbers are not changed: cup numbers sit beside them.


@pytest.mark.parametrize(("slog", "name"), SHOTS)
def test_the_puck_flow_numbers_keep_their_meaning(slog: Slog, name: str) -> None:
    samples = as_sample_dicts(slog)
    puck = first_drip_index(samples)
    assert puck is not None
    summary = calculate_summary(slog)
    assert summary["flow"]["time_to_first_drip_s"] == round(samples[puck]["t"] / 100) / 10
    # Four to five seconds after the cup began to fill: the figure people used to read.
    assert summary["flow"]["time_to_first_drip_s"] - FIRST_DRIP_S[name] > 3.0
    diagnostics = compute_shot_diagnostics(slog)
    assert diagnostics is not None
    brew = [s.get("pf", 0.0) for s in samples]
    assert diagnostics["extraction"]["flow_avg_brew_ml_s"] > 0
    assert set(diagnostics["extraction"]) == {"flow_avg_brew_ml_s", "cup_flow_avg_brew_g_s"}
    assert brew  # the puck column is the one read


# 7. The derivation version moved so the archive gets the new fields at the next boot.


def test_the_derivation_version_moved_for_the_cup_flow_fields() -> None:
    assert DERIVATION_VERSION == 11


# One rule for "has a scale": any brew-phase weight above zero, not the connection flag and
# not a weight anywhere in the shot.


def _flag_cleared(slog: Slog) -> Slog:
    """The scale shot as an older file writes it: the connection bit never set."""
    samples = [s.model_copy(update={"si": (s.si or 0) & ~0x0004}) for s in slog.samples]
    return dataclasses.replace(slog, samples=samples)


def _weight_only_before_the_brew(slog: Slog) -> Slog:
    """Weights and cup flow zeroed through the brew phase, a gram left before it."""
    from gaggiclanker.domain.diagnostics import _brew_phase_positions

    brew = set(_brew_phase_positions(as_sample_dicts(slog), slog.transitions))
    samples = [
        s.model_copy(update={"v": 0.0, "vf": 0.0}) if index in brew else s
        for index, s in enumerate(slog.samples)
    ]
    first_brew = min(brew)
    assert first_brew > 1
    # The cup reads a gram before the brew begins (a tare, a cup set down), then nothing.
    for index in range(1, first_brew):
        samples[index] = samples[index].model_copy(update={"v": 1.0})
    samples[0] = samples[0].model_copy(update={"v": 0.0})
    return dataclasses.replace(slog, samples=samples)


@pytest.mark.parametrize(("slog", "name"), SHOTS)
def test_a_scale_shot_with_the_connection_flag_cleared_still_has_every_cup_number(
    slog: Slog, name: str
) -> None:
    cleared = _flag_cleared(slog)
    derived = derive_shot(cleared, slog_to_raw(cleared), device_id="000001")
    assert derived.shot.scale_connected is False  # the row keeps the firmware's word
    stored = derived_blob(cleared)
    assert stored["summary"]["flow"]["cup_first_drip_s"] == FIRST_DRIP_S[name]
    assert stored["diagnostics"]["extraction"]["cup_flow_avg_brew_g_s"] is not None
    assert stored["diagnostics"]["weight"]["scale_connected"] is True
    assert derived.shot.phases_json is not None
    metrics = [p.get("metrics", {}) for p in json.loads(derived.shot.phases_json)]
    assert any("scale_flow_mean_g_s" in m for m in metrics)
    assert [m["cup_first_drip_s"] for m in metrics if "cup_first_drip_s" in m] == [
        FIRST_DRIP_S[name]
    ]


@pytest.mark.parametrize(("slog", "name"), SHOTS)
def test_a_weight_outside_the_brew_phase_is_not_a_scale(slog: Slog, name: str) -> None:
    odd = _weight_only_before_the_brew(slog)
    assert max(s.v or 0.0 for s in odd.samples) > 0  # a weight is in the shot, not in its brew
    stored = derived_blob(odd)
    assert stored["diagnostics"]["weight"]["scale_connected"] is False
    assert stored["summary"]["flow"]["cup_first_drip_s"] is None
    assert stored["diagnostics"]["extraction"]["cup_flow_avg_brew_g_s"] is None


def test_the_cup_first_drip_is_placed_in_the_phase_holding_its_sample() -> None:
    """Shot 196's drip moved to the first sample of Extraction (a rounded start of 11.8 s is
    after 11.75 s, which put it in the phase before)."""
    slog = parse_slog((SLOG_FIXTURES / "shot_196_baseline_high.slog").read_bytes())
    first_of_extraction = next(
        t.sample_index for t in slog.transitions if t.phase_name == "Extraction"
    )
    base = slog.samples[0].v or 0.0
    samples = [
        s.model_copy(
            update={"v": max(s.v or 0.0, base + 0.6) if index >= first_of_extraction else 0.0}
        )
        for index, s in enumerate(slog.samples)
    ]
    shifted = dataclasses.replace(slog, samples=samples)
    assert cup_first_drip_index(as_sample_dicts(shifted)) == first_of_extraction

    derived = derive_shot(shifted, slog_to_raw(shifted), device_id="000001")

    assert derived.shot.phases_json is not None
    holding = [
        p["name"]
        for p in json.loads(derived.shot.phases_json)
        if "cup_first_drip_s" in p.get("metrics", {})
    ]
    assert holding == ["Extraction"]
