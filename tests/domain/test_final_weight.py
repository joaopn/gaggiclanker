"""A shot's final weight when the scale drops to zero as the shot ends.

The header's `finalWeight` is the scale reading when the file is closed, so a
cup lifted (or a scale resetting) in the last second leaves the header and the
last samples at 0 with the yield visible right before the drop. These pin how
narrowly that pattern is matched: anything else that ends at zero keeps having
no final weight.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from gaggiclanker.domain.slog import (
    DROPOUT_WINDOW_MS,
    encode_slog,
    parse_slog,
    weight_before_dropout,
)
from tests.domain.helpers import make_slog

INTERVAL = 250


def _samples(weights: Sequence[float | None]) -> list[dict[str, Any]]:
    return [{"t": i * INTERVAL, "v": w} for i, w in enumerate(weights)]


def _ramp(peak: float, n: int = 120) -> list[float | None]:
    return [round(peak * i / (n - 1), 1) for i in range(n)]


def test_the_reading_before_a_drop_to_zero_is_the_yield() -> None:
    """The shot that showed it: 31.8 g, then three samples of 0, header 0."""
    slog = make_slog(_samples([*_ramp(31.8), 0.0, 0.0, 0.0]))
    assert slog.volume_g == pytest.approx(31.8)


def test_it_survives_the_binary_round_trip() -> None:
    """The header writes "no weight" as 0, which is how the machine stores it."""
    raw = encode_slog(make_slog(_samples([*_ramp(36.2), 0.0, 0.0])))
    parsed = parse_slog(raw)
    assert parsed.header.final_weight_g is None
    assert parsed.volume_g == pytest.approx(36.2)


def test_the_header_weight_wins_when_there_is_one() -> None:
    slog = make_slog(_samples([*_ramp(31.8), 0.0]), final_weight_g=32.4)
    assert slog.volume_g == pytest.approx(32.4)


def test_a_positive_last_reading_is_the_yield_as_before() -> None:
    assert make_slog(_samples(_ramp(31.8))).volume_g == pytest.approx(31.8)


def test_a_shot_with_no_scale_has_no_weight() -> None:
    assert make_slog(_samples([0.0] * 50)).volume_g is None
    assert make_slog(_samples([None] * 50)).volume_g is None
    assert make_slog([]).volume_g is None


def test_a_zero_run_as_long_as_the_window_still_counts() -> None:
    zeros = DROPOUT_WINDOW_MS // INTERVAL
    slog = make_slog(_samples([*_ramp(31.8), *[0.0] * zeros]))
    assert slog.volume_g == pytest.approx(31.8)


def test_a_zero_run_longer_than_the_window_is_not_a_dropout() -> None:
    """Zero for more than the firmware's settle window began during the brew."""
    zeros = DROPOUT_WINDOW_MS // INTERVAL + 1
    slog = make_slog(_samples([*_ramp(31.8), *[0.0] * zeros]))
    assert slog.volume_g is None


def test_the_window_is_measured_in_time_not_samples() -> None:
    """From v6 `t` is real milliseconds, which is what the window is about.

    A header interval of 250 ms with readings a second apart (a busy display
    loop): four zero samples span four seconds, not one.
    """
    weights = [*_ramp(31.8, 30), 0.0, 0.0, 0.0, 0.0]
    samples = [{"t": i * 1000, "v": w} for i, w in enumerate(weights)]
    assert make_slog(samples, sample_interval=INTERVAL).volume_g is None
    samples = [{"t": i * 1000, "v": w} for i, w in enumerate(weights[:-1])]
    assert make_slog(samples, sample_interval=INTERVAL).volume_g == pytest.approx(31.8)


def test_without_a_time_field_the_interval_measures_the_run() -> None:
    samples = [{"v": w} for w in [*_ramp(31.8), 0.0, 0.0]]
    assert weight_before_dropout(make_slog(samples).samples, INTERVAL) == pytest.approx(31.8)
    samples = [{"v": w} for w in [*_ramp(31.8), *[0.0] * 20]]
    assert weight_before_dropout(make_slog(samples).samples, INTERVAL) is None


def test_scale_drift_before_the_drop_is_no_yield() -> None:
    """A tenth of a gram on an empty scale, then zero: nothing was brewed onto it."""
    slog = make_slog(_samples([0.0] * 40 + [0.1, 0.1, 0.1, 0.0, 0.0]))
    assert slog.volume_g is None


def test_a_weight_that_fell_away_before_zero_has_no_single_reading_to_trust() -> None:
    """A cup lifted slowly: 36 g, then 20, then 5, then zero. None of those is the yield."""
    slog = make_slog(_samples([*_ramp(36.0), 20.0, 5.0, 0.0, 0.0]))
    assert slog.volume_g is None


def test_a_reading_within_the_peak_share_still_counts() -> None:
    """A scale settling a little below its peak (drips, a wobble) before the drop."""
    slog = make_slog(_samples([*_ramp(36.0), 33.0, 0.0, 0.0]))
    assert slog.volume_g == pytest.approx(33.0)
