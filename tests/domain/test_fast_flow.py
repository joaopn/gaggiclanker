"""The fast-flow window: scale flow above 3.0 g/s over 1.0 s while at 80 % of peak pressure.

Each case is the constructed lever shot with its scale-flow or pressure column
rewritten at a few samples, so the boundaries are exact: the samples are 250
ms apart, so a 1.0 s window is five samples (the first and last 1.0 s apart),
and the shot's peak pressure is 8.2 bar, so the pressure floor is 6.56 bar.
"""

from __future__ import annotations

import pytest

from gaggiclanker.domain.diagnostics import as_sample_dicts
from gaggiclanker.domain.phase_metrics import FastFlow, find_fast_flow
from gaggiclanker.domain.slog import Slog, parse_slog
from tests.domain.helpers import SLOG_FIXTURES
from tests.lever_shot import lever_shot, without_pressure, without_scale

#: Samples 99..103 are the first five at or above the pressure floor (6.56 bar).
FIRST = 99


def flat(slog: Slog, flow: float) -> Slog:
    """The shot with a constant scale flow everywhere, so only what a test sets matters."""
    slog.samples = [s.model_copy(update={"vf": flow}) for s in slog.samples]
    return slog


def with_flow(slog: Slog, first: int, flows: list[float]) -> Slog:
    for offset, flow in enumerate(flows):
        index = first + offset
        slog.samples[index] = slog.samples[index].model_copy(update={"vf": flow})
    return slog


def fast(slog: Slog, *, has_pressure: bool = True, scale: bool = True) -> FastFlow | None:
    return find_fast_flow(as_sample_dicts(slog), has_pressure=has_pressure, scale_connected=scale)


def test_the_constructed_lever_shot_fires_in_the_ramp() -> None:
    window = fast(lever_shot())
    assert window == {
        "phase_number": 2,
        "start_s": 24.75,
        "end_s": 25.75,
        "mean_g_s": 4.0,
        "pressure_min_bar": 7.2,
        "peak_pressure_bar": 8.2,
    }


def test_a_window_averaging_exactly_the_threshold_does_not_fire() -> None:
    slog = with_flow(flat(lever_shot(), 0.5), FIRST, [3.0] * 5)
    assert fast(slog) is None


def test_a_window_just_above_the_threshold_fires() -> None:
    slog = with_flow(flat(lever_shot(), 0.5), FIRST, [3.01] * 5)
    window = fast(slog)
    assert window is not None
    assert (window["start_s"], window["end_s"], window["mean_g_s"]) == (24.75, 25.75, 3.01)


def test_the_window_is_a_mean_not_every_sample() -> None:
    # Three samples are not above 3.0 g/s, but the five average above it (3.2) ...
    slog = with_flow(flat(lever_shot(), 0.5), FIRST, [3.0, 3.0, 2.0, 3.0, 5.0])
    assert fast(slog) is not None
    # ... and a hair under it (2.98) they do not.
    slog = with_flow(flat(lever_shot(), 0.5), FIRST, [3.0, 3.0, 2.0, 3.0, 3.9])
    assert fast(slog) is None


def test_a_run_shorter_than_one_second_does_not_fire() -> None:
    # Four fast samples span 0.75 s from first to last; the fifth sample a window
    # needs to reach 1.0 s is slow, and the five average 2.98.
    slog = with_flow(flat(lever_shot(), 0.5), FIRST, [3.6] * 4)
    assert fast(slog) is None


def test_a_window_with_one_sample_under_80_percent_of_peak_does_not_fire() -> None:
    slog = flat(lever_shot(), 4.0)
    slog.samples[FIRST + 2] = slog.samples[FIRST + 2].model_copy(update={"cp": 6.5})
    # The pressure was lower than 6.56 bar at one sample of every window that
    # reaches it, and above it after; only windows clear of it can fire.
    window = fast(slog)
    assert window is not None
    assert window["start_s"] >= (FIRST + 3) * 0.25
    assert window["pressure_min_bar"] >= 6.56


def test_a_window_at_80_percent_of_peak_exactly_fires() -> None:
    slog = flat(lever_shot(), 0.5)
    slog = with_flow(slog, FIRST, [4.0] * 5)
    for index in range(FIRST, FIRST + 5):
        slog.samples[index] = slog.samples[index].model_copy(update={"cp": 6.56})
    window = fast(slog)
    assert window is not None
    assert window["start_s"] == 24.75


def test_a_shot_that_never_goes_fast_at_pressure_never_fires() -> None:
    assert fast(flat(lever_shot(), 2.99)) is None


def test_the_phase_is_the_one_holding_the_first_window() -> None:
    slog = flat(lever_shot(), 0.5)
    # A fast stretch in the soak at no pressure (does not count), then one in the
    # ramp at 110..114. The first window to average above 3 g/s starts at 107,
    # where two of its five samples are in the stretch (3.9 g/s).
    slog = with_flow(slog, 40, [9.0] * 5)
    slog = with_flow(slog, 110, [9.0] * 5)
    window = fast(slog)
    assert window is not None
    assert window["phase_number"] == 2
    assert window["start_s"] == 26.75


def test_no_scale_never_fires() -> None:
    slog = without_scale(lever_shot())
    assert all(s.vf == 0.0 for s in slog.samples)
    assert fast(slog, scale=False) is None
    # The gate is the flag: even a column that were not zeros would not count.
    assert fast(lever_shot(), scale=False) is None


def test_no_pressure_sensor_never_fires() -> None:
    slog = without_pressure(lever_shot())
    assert fast(slog, has_pressure=False) is None
    assert fast(lever_shot(), has_pressure=False) is None


@pytest.mark.parametrize("path", sorted(SLOG_FIXTURES.glob("*.slog")), ids=lambda p: p.stem)
def test_the_real_fixtures_never_fire(path: object) -> None:
    # Their scale flow tops out at 3.0 g/s.
    assert fast(parse_slog(path.read_bytes())) is None  # type: ignore[attr-defined]


def test_a_window_straddling_a_phase_boundary_names_the_phase_of_its_first_sample() -> None:
    slog = with_flow(flat(lever_shot(), 0.5), 110, [9.0] * 5)
    # The fast stretch crosses from phase 2 into a phase 3 that only these rows carry.
    slog.samples = [
        s.model_copy(update={"phase": 2 if i < 110 else 3}) if i >= 100 else s
        for i, s in enumerate(slog.samples)
    ]
    window = fast(slog)
    assert window is not None
    boundary_s = (slog.samples[110].t or 0) / 1000.0
    assert window["start_s"] < boundary_s < window["end_s"]
    assert window["phase_number"] == 2
