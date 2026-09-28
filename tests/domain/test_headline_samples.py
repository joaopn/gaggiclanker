"""The sample behind each headline number: first drip, peak pressure, the largest drop.

A reader that shows the curve keeps these samples so the moment a diagnostic
is about is never stepped over. They must be the samples the engine read its
own numbers from, so each is checked against the number the engine reports on
every real shot the tests carry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaggiclanker.domain.diagnostics import (
    SampleDict,
    _round1,
    _round2,
    as_sample_dicts,
    compute_shot_diagnostics,
    first_drip_index,
    largest_pressure_drop,
    peak_pressure_index,
    transform_shot,
)
from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog
from gaggiclanker.domain.models import PhaseTransition
from gaggiclanker.domain.slog import Slog, parse_slog

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _shots() -> dict[str, Slog]:
    shots = {path.name: parse_slog(path.read_bytes()) for path in FIXTURES.glob("slog/*.slog")}
    for name in ("shot-129.json", "shot-v7-synthetic.json"):
        document = json.loads((FIXTURES / "exports" / name).read_text())
        shots[name] = shot_export_to_slog(ShotExport.model_validate(document))
    return dict(sorted(shots.items()))


SHOTS = _shots()


#: The time of peak pressure and the time to first drip, in seconds, as the
#: engine reported them before it read either through the functions under
#: test. Literals, so the vendored numbers have a guard that does not go
#: through those functions: every one of these shots but the synthetic one
#: holds its peak for several samples, and the first of them is the answer.
VENDORED_TIMES: dict[str, tuple[float, float | None]] = {
    "shot-129.json": (20.2, 6.8),
    "shot-v7-synthetic.json": (3.0, 0.0),
    "shot_196_baseline_high.slog": (14.2, 16.2),
    "shot_204_ramping_flow.slog": (25.5, 28.0),
    "shot_222_hold_false_positive.slog": (22.0, 23.2),
}


def test_every_fixture_shot_has_its_vendored_times() -> None:
    assert set(VENDORED_TIMES) == set(SHOTS)


@pytest.mark.parametrize("name", list(SHOTS))
def test_the_summary_s_times_are_the_vendored_ones(name: str) -> None:
    summary = transform_shot(SHOTS[name], "per_phase")["summary"]
    pressure = summary["pressure"]

    assert pressure is not None
    assert (pressure["peak_time_s"], summary["flow"]["time_to_first_drip_s"]) == (
        VENDORED_TIMES[name]
    )


@pytest.mark.parametrize("name", list(SHOTS))
def test_first_drip_and_peak_are_the_first_samples_of_their_rule(name: str) -> None:
    samples = as_sample_dicts(SHOTS[name])
    peak_time, drip_time = VENDORED_TIMES[name]

    drip = first_drip_index(samples)
    peak = peak_pressure_index(samples)

    assert peak is not None
    top = max(s["cp"] for s in samples)
    assert samples[peak]["cp"] == top
    assert all(s["cp"] < top for s in samples[:peak]), "the first of equal peaks"
    assert _round1(samples[peak]["t"] / 1000) == peak_time
    assert drip is not None
    assert samples[drip]["pf"] > 0.0
    assert all(s.get("pf", 0.0) <= 0.0 for s in samples[:drip]), "the first flow above zero"
    assert _round1(samples[drip]["t"] / 1000) == drip_time


@pytest.mark.parametrize("name", list(SHOTS))
def test_the_drop_runs_between_the_samples_the_indicator_was_computed_on(name: str) -> None:
    slog = SHOTS[name]
    samples = as_sample_dicts(slog)
    dt = slog.sample_interval / 1000
    diagnostics = compute_shot_diagnostics(slog)
    assert diagnostics is not None
    channeling = diagnostics["channeling"]
    assert channeling is not None

    window = largest_pressure_drop(samples, slog.transitions, dt)

    assert window is not None
    start, end = window
    assert start < end
    rate = (samples[end]["cp"] - samples[start]["cp"]) / dt
    assert _round2(rate) == channeling["pressure_max_drop_rate_bar_s"]


def _flat(count: int, **values: float) -> list[SampleDict]:
    return [{"t": float(i * 250), **values} for i in range(count)]


def test_nothing_recorded_is_no_sample() -> None:
    no_flow = _flat(20, cp=9.0)
    no_pressure = _flat(20, pf=2.0)

    assert first_drip_index(no_flow) is None, "puck flow not recorded"
    assert first_drip_index(_flat(20, pf=0.0)) is None, "puck flow never rose"
    assert peak_pressure_index(no_pressure) is None
    assert peak_pressure_index(_flat(20, cp=0.0)) is None, "a zero trace has no peak"


def test_no_drop_where_the_engine_assesses_none() -> None:
    brew = [PhaseTransition(sample_index=0, phase_number=0, phase_name="Brew")]
    steady = _flat(20, cp=9.0, pf=2.0)

    assert largest_pressure_drop(steady[:4], brew, 0.25) is None, "fewer than five samples"
    only_soak = [PhaseTransition(sample_index=0, phase_number=0, phase_name="Soak")]
    # Every phase a pre-infusion: the engine falls back to the whole shot.
    assert largest_pressure_drop(steady, only_soak, 0.25) is not None
    dry = _flat(20, cp=9.0, pf=0.0)
    assert largest_pressure_drop(dry, brew, 0.25) is None, "no flowing steady state"
    assert largest_pressure_drop(steady, brew, 0.0) is None


def test_the_first_of_equal_drops_wins_and_the_ramp_is_not_one() -> None:
    brew = [PhaseTransition(sample_index=0, phase_number=0, phase_name="Brew")]
    pressures = [2.0, 5.0, 9.0, 9.0, 8.0, 9.0, 8.0, 9.0, 9.0, 3.0]
    samples = [
        {"t": float(i * 250), "cp": p, "pf": 2.0 if i < 9 else 0.0} for i, p in enumerate(pressures)
    ]

    # The last step (9 → 3) is dry and trimmed; the ramp is before 90 % of peak.
    assert largest_pressure_drop(samples, brew, 0.25) == (3, 4)
