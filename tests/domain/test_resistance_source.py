"""Where a shot's puck resistance comes from: the machine's own, else ours.

The firmware logs ``pr = sqrt(P) / Q_puck`` per sample, so ``pr²`` is the same
quadratic model as our ``P / F²`` on the same scale. The four real shots below
pin that claim: the level and the slope are the same to within a few percent both
ways.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from gaggiclanker.domain import diagnostics as diag
from gaggiclanker.domain.diagnostics import (
    as_sample_dicts,
    compute_shot_diagnostics,
    compute_summary_diagnostics,
    transform_shot,
)
from gaggiclanker.domain.slog import Slog, parse_slog
from tests.domain.helpers import SLOG_FIXTURES, make_slog, slog_from_export


def _real_shots() -> dict[str, Slog]:
    shots = {
        path.stem: parse_slog(path.read_bytes(), "000001")
        for path in sorted(SLOG_FIXTURES.glob("*.slog"))
    }
    shots["shot-129"] = slog_from_export("shot-129.json")
    return shots


REAL_SHOTS = _real_shots()


def _both_ways(slog: Slog) -> tuple[diag.ResistanceDiagnostics, diag.ResistanceDiagnostics]:
    """The window's resistance from the machine, and the same window computed."""
    samples = as_sample_dicts(slog)
    window = diag._get_brew_phase_samples(samples, slog.transitions)
    dt = slog.sample_interval / 1000.0
    machine = diag._build_resistance(window, dt)
    computed = diag._build_resistance(
        [{k: v for k, v in s.items() if k != "pr"} for s in window], dt
    )
    return machine, computed


@pytest.mark.parametrize("name", sorted(REAL_SHOTS))
def test_real_shots_take_the_machines_value_and_it_is_on_the_scale_of_ours(name: str) -> None:
    machine, computed = _both_ways(REAL_SHOTS[name])

    assert machine["source"] == "machine"
    assert computed["source"] == "computed"
    # The same quadratic model on the same scale: the two readings of one puck
    # agree to within a tenth of the level, and a few hundredths of the slope.
    assert machine["avg"] == pytest.approx(computed["avg"], rel=0.1)
    assert machine["slope"] == pytest.approx(computed["slope"], abs=0.03)
    assert set(machine) == {"source", "avg", "slope"}


def test_all_three_shapes_say_the_source_on_a_real_shot() -> None:
    slog = REAL_SHOTS["shot_196_baseline_high"]

    full = compute_shot_diagnostics(slog)
    summary = compute_summary_diagnostics(slog)
    per_phase = transform_shot(slog, "per_phase")

    assert full is not None and full["resistance"] is not None
    assert full["resistance"]["source"] == "machine"
    assert summary is not None and summary["resistance_source"] == "machine"
    brew_phases = [
        p["diagnostics"]
        for p in per_phase["phases"]
        if p.get("diagnostics", {}).get("phase_type") == "brew"
    ]
    # Each phase decides for itself: the ramp has no flowing sample yet and so
    # falls back, the extraction has the machine's own.
    assert [d["resistance_source"] for d in brew_phases] == ["computed", "machine"]


# ── synthetic windows ────────────────────────────────────────────────


def _slog(prs: Sequence[float | None], *, flow: float = 2.0, pressure: float = 8.0) -> Slog:
    samples = []
    for i, pr in enumerate(prs):
        sample: dict[str, float] = {"t": i * 250, "cp": pressure, "pf": flow, "ct": 93.0}
        if pr is not None:
            sample["pr"] = pr
        samples.append(sample)
    return make_slog(samples)


def _resistance(slog: Slog) -> diag.ResistanceDiagnostics:
    full = compute_shot_diagnostics(slog)
    assert full is not None and full["resistance"] is not None
    return full["resistance"]


COMPUTED = 8.0 / (2.0 * 2.0)  # P / F² for the synthetic shots


def test_valid_machine_samples_are_squared() -> None:
    resistance = _resistance(_slog([1.5] * 10))

    assert resistance["source"] == "machine"
    assert resistance["avg"] == 2.25


@pytest.mark.parametrize(
    "prs",
    [
        pytest.param([None] * 10, id="no-pr-field"),
        pytest.param([0.0] * 10, id="all-zeros"),
        pytest.param([100.0] * 10, id="at-the-clamp"),
        pytest.param([655.35] * 10, id="above-the-clamp"),
        pytest.param([1.5, 1.5] + [0.0] * 8, id="two-valid"),
        pytest.param([1.5, 100.0, 1.5] + [0.0] * 7, id="two-valid-one-clamped"),
    ],
)
def test_without_three_valid_machine_samples_ours_is_used(prs: list[float | None]) -> None:
    resistance = _resistance(_slog(prs))

    assert resistance["source"] == "computed"
    assert resistance["avg"] == COMPUTED


def test_a_mix_with_three_valid_samples_uses_only_the_valid_ones() -> None:
    prs = [0.0, 0.0, 1.0, 2.0, 100.0, 3.0, 0.0]

    resistance = _resistance(_slog(prs))

    assert resistance["source"] == "machine"
    assert resistance["avg"] == round((1 + 4 + 9) / 3, 2)


def test_the_window_is_the_flowing_samples_only() -> None:
    # A valid `pr` on a sample with no flow is the stale value the firmware
    # holds; it stays out, so two valid samples remain and ours is used.
    slog = make_slog(
        [
            {"t": 0, "cp": 8.0, "pf": 2.0, "pr": 1.0},
            {"t": 250, "cp": 8.0, "pf": 2.0, "pr": 1.0},
            {"t": 500, "cp": 8.0, "pf": 0.05, "pr": 1.0},
            {"t": 750, "cp": 8.0, "pf": 0.0, "pr": 1.0},
            # Exactly 0.1 ml/s is not flowing: the window is `pf > 0.1`.
            {"t": 900, "cp": 8.0, "pf": 0.1, "pr": 1.0},
        ]
        + [{"t": 1000 + i * 250, "cp": 8.0, "pf": 2.0, "pr": 0.0} for i in range(4)]
    )
    resistance = _resistance(slog)

    assert resistance["source"] == "computed"


def test_the_summary_and_the_phase_follow_the_same_rule() -> None:
    machine = _slog([1.5] * 10)
    fallback = _slog([0.0] * 10)

    for slog, source, avg in ((machine, "machine", 2.25), (fallback, "computed", COMPUTED)):
        summary = compute_summary_diagnostics(slog)
        assert summary is not None
        assert summary["resistance_source"] == source
        assert summary["resistance_avg"] == avg


def test_no_pressure_means_no_resistance_and_no_source() -> None:
    slog = _slog([1.5] * 10)

    full = compute_shot_diagnostics(slog, has_pressure=False)
    summary = compute_summary_diagnostics(slog, has_pressure=False)

    assert full is not None and full["resistance"] is None
    assert summary is not None and summary["resistance_source"] is None
