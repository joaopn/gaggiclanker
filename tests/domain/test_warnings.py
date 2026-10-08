"""The warnings: each threshold at, just under and just over its boundary.

`shot_warnings` is pure: what is stored about the shot, the recipe's target
yield and the profile's phase names in, a list out. Boundaries are tested on its
arguments; the gates (no scale, no profile, which exit reasons) are tested twice,
once on the arguments and once on the constructed lever shot derived through the
real path, with the sensor's columns zeroed and its flag cleared as the firmware
writes them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.slog import Slog
from gaggiclanker.domain.warnings import (
    FAULTS,
    ShotWarning,
    badge_text,
    fault_token,
    percent_of_target,
    shot_warnings,
    sort_warnings,
)
from gaggiclanker.sync.derive import derive_shot
from tests.lever_shot import (
    LEVER_PROFILE,
    RAMP_END_G,
    TARGET_YIELD_G,
    lever_shot,
    without_pressure,
    without_scale,
)

PHASES = [
    {"phase_number": 0, "name": "preinfusion"},
    {"phase_number": 1, "name": "soak"},
    {"phase_number": 2, "name": "ramp"},
]
SKIPPED = {
    "phases_not_reached": [{"phase_number": 3, "name": "decline"}],
    "fast_flow": None,
}


def call(**overrides: Any) -> list[ShotWarning]:
    arguments: dict[str, Any] = {
        "final_weight_g": 32.0,
        "scale_connected": True,
        "final_exit_reason": 5,
        "duration_s": 30.0,
        "target_yield_g": 32.0,
        "phases": PHASES,
        "metrics": {"phases_not_reached": [], "fast_flow": None},
    }
    arguments.update(overrides)
    return shot_warnings(**arguments)


def faults(found: list[ShotWarning]) -> list[str]:
    return [w.badge for w in found]


# ── over and under target ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("weight", "over"),
    [(35.19, False), (35.2, False), (35.21, True), (36.0, True), (32.0, False)],
)
def test_over_target_is_strictly_above_110_percent(weight: float, over: bool) -> None:
    found = call(final_weight_g=weight)
    assert (faults(found) == ["Shot: over target"]) is over
    assert over or not found


@pytest.mark.parametrize(
    ("weight", "under"),
    [(28.81, False), (28.8, False), (28.79, True), (20.0, True), (32.0, False)],
)
def test_under_target_is_strictly_below_90_percent(weight: float, under: bool) -> None:
    found = call(final_weight_g=weight)
    assert (faults(found) == ["Shot: under target"]) is under


def test_the_yield_warnings_need_a_scale() -> None:
    assert call(final_weight_g=50.0, scale_connected=False) == []
    assert call(final_weight_g=None) == []
    assert call(final_weight_g=0.0) == []


def test_the_yield_warnings_need_a_filed_version_with_a_target() -> None:
    assert call(final_weight_g=50.0, target_yield_g=None) == []
    assert call(final_weight_g=50.0, target_yield_g=0.0) == []


def test_over_target_says_the_share_and_the_limit() -> None:
    (found,) = call(final_weight_g=40.0, target_yield_g=32.0)
    assert found.severity == "amber"
    assert found.phase == "Shot"
    assert found.phase_number is None
    assert "40.0 g" in found.detail
    assert "125.0 %" in found.detail
    assert "32 g target yield" in found.detail


def test_percent_of_target() -> None:
    assert percent_of_target(42.2, 36.0) == 117.2
    assert percent_of_target(None, 36.0) is None
    assert percent_of_target(30.0, None) is None
    assert percent_of_target(30.0, 0.0) is None


# ── skipped ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("reason", [1, 4])
def test_a_phase_never_begun_is_skipped_when_the_shot_stopped_on_a_target(reason: int) -> None:
    found = call(final_exit_reason=reason, metrics=SKIPPED)
    assert faults(found) == ["decline: skipped"]
    assert found[0].at_s == 30.0
    assert found[0].phase_number == 3


@pytest.mark.parametrize("reason", [0, 2, 3, 5, 6, 7, 8])
def test_no_phase_is_skipped_for_any_other_reason(reason: int) -> None:
    assert call(final_exit_reason=reason, metrics=SKIPPED) == []


def test_no_phase_is_skipped_without_a_profile_or_with_every_phase_begun() -> None:
    assert call(final_exit_reason=1, metrics={"phases_not_reached": [], "fast_flow": None}) == []
    assert call(final_exit_reason=1, metrics=None) == []
    assert call(final_exit_reason=1, metrics={}) == []


def test_the_badge_names_the_first_skipped_phase_and_the_detail_lists_them_all() -> None:
    metrics = {
        "phases_not_reached": [
            {"phase_number": 3, "name": "hold"},
            {"phase_number": 4, "name": "decline"},
        ],
        "fast_flow": None,
    }
    (found,) = call(final_exit_reason=1, metrics=metrics)
    assert found.badge == "hold: skipped"
    assert "hold, decline" in found.detail
    assert "volumetric target" in found.detail


# ── fast flow ───────────────────────────────────────────────────────

WINDOW = {
    "phase_number": 2,
    "start_s": 24.75,
    "end_s": 25.75,
    "mean_g_s": 4.0,
    "pressure_min_bar": 7.2,
    "peak_pressure_bar": 8.2,
}


def test_fast_flow_names_the_phase_the_window_and_its_numbers() -> None:
    (found,) = call(metrics={"phases_not_reached": [], "fast_flow": WINDOW})
    assert found.badge == "ramp: fast flow"
    assert found.at_s == 24.75
    for text in ("4.00 g/s", "24.75 s", "25.75 s", "7.2 bar", "8.2 bar"):
        assert text in found.detail


# ── order ───────────────────────────────────────────────────────────


def test_the_order_is_the_order_of_the_shot_with_the_shot_wide_ones_last() -> None:
    metrics = {**SKIPPED, "fast_flow": WINDOW}
    found = call(final_weight_g=50.0, final_exit_reason=1, metrics=metrics)
    assert faults(found) == ["ramp: fast flow", "decline: skipped", "Shot: over target"]
    assert [w.at_s for w in found] == [24.75, 30.0, 30.0]


def test_a_more_severe_warning_comes_first_whatever_the_time() -> None:
    # Only amber is produced yet; a signature will raise one to red.
    early = ShotWarning("ramp", "fast flow", "amber", "", 2, 5.0)
    red = ShotWarning("soak", "skipped", "red", "", 1, 20.0)
    shot_wide_red = ShotWarning("Shot", "over target", "red", "", None, 30.0)
    assert sort_warnings([early, shot_wide_red, red]) == [red, shot_wide_red, early]


def test_a_shot_wide_warning_comes_after_a_phase_warning_even_when_it_is_earlier() -> None:
    phase = ShotWarning("ramp", "fast flow", "amber", "", 2, 25.0)
    shot_wide = ShotWarning("Shot", "over target", "amber", "", None, 5.0)
    assert sort_warnings([shot_wide, phase]) == [phase, shot_wide]


def test_the_badge_is_the_first_warning_and_a_count_of_the_rest() -> None:
    metrics = {**SKIPPED, "fast_flow": WINDOW}
    found = call(final_weight_g=50.0, final_exit_reason=1, metrics=metrics)
    assert badge_text(found) == "ramp: fast flow +2"
    assert badge_text(found[:1]) == "ramp: fast flow"
    assert badge_text([]) is None


def test_the_fault_list_is_the_fixed_one() -> None:
    assert FAULTS == (
        "early yield",
        "little yield",
        "fast flow",
        "slow flow",
        "skipped",
        "cut short",
        "low pressure",
        "high pressure",
        "unstable",
        "temperature",
        "over target",
        "under target",
    )
    assert fault_token("fast flow") == "fast_flow"


# ── the constructed lever shot, through the real derivation ─────────


def derived_warnings(slog: Slog, *, target: float | None = TARGET_YIELD_G, **kwargs: Any) -> Any:
    derived = derive_shot(
        slog,
        slog_to_raw(slog),
        device_id="000001",
        profile=kwargs.pop("profile", LEVER_PROFILE),
        **kwargs,
    )
    shot = derived.shot
    return shot_warnings(
        final_weight_g=shot.final_weight_g,
        scale_connected=shot.scale_connected,
        final_exit_reason=shot.final_exit_reason or 0,
        duration_s=shot.duration_ms / 1000,
        target_yield_g=target,
        phases=json.loads(shot.phases_json or "[]"),
        metrics=json.loads(shot.diagnostics_json or "{}").get("metrics"),
    )


def test_the_lever_shot_is_exactly_fast_flow_then_skipped_then_over_target() -> None:
    found = derived_warnings(lever_shot())
    assert faults(found) == ["ramp: fast flow", "decline: skipped", "Shot: over target"]
    assert all(w.severity == "amber" for w in found)
    assert f"{RAMP_END_G:.1f} g" in found[2].detail
    assert "117.2 %" in found[2].detail


def test_the_lever_shot_on_no_scale_has_only_the_skipped_phase() -> None:
    found = derived_warnings(without_scale(lever_shot()))
    assert faults(found) == ["decline: skipped"]


def test_the_lever_shot_on_no_pressure_sensor_has_no_fast_flow() -> None:
    found = derived_warnings(without_pressure(lever_shot()), has_pressure=False)
    assert "ramp: fast flow" not in faults(found)
    assert faults(found) == ["decline: skipped", "Shot: over target"]


def test_the_lever_shot_with_no_profile_cannot_be_skipped() -> None:
    found = derived_warnings(lever_shot(), profile=None)
    assert faults(found) == ["ramp: fast flow", "Shot: over target"]


def test_the_lever_shot_that_stopped_for_another_reason_skips_nothing() -> None:
    slog = lever_shot()
    slog.header = slog.header.model_copy(update={"final_exit_reason": 5})
    assert faults(derived_warnings(slog)) == ["ramp: fast flow", "Shot: over target"]


def test_refiling_changes_the_yield_warnings_and_nothing_stored() -> None:
    slog = lever_shot()
    derived = derive_shot(slog, slog_to_raw(slog), device_id="000001", profile=LEVER_PROFILE)
    stored = (derived.shot.phases_json, derived.shot.diagnostics_json)
    assert "Shot: over target" in faults(derived_warnings(slog, target=36.0))
    assert "Shot: over target" not in faults(derived_warnings(slog, target=40.0))
    assert "Shot: under target" in faults(derived_warnings(slog, target=60.0))
    assert faults(derived_warnings(slog, target=None)) == ["ramp: fast flow", "decline: skipped"]
    assert (derived.shot.phases_json, derived.shot.diagnostics_json) == stored


def test_a_fast_window_in_a_log_with_no_phase_table_is_about_the_shot() -> None:
    metrics = {
        "phases_not_reached": [],
        "fast_flow": {**WINDOW, "phase_number": 0},
        "per_phase": False,
    }
    (found,) = call(metrics=metrics, phases=[{"phase_number": 0, "name": "extraction"}])
    assert found.phase == "Shot"
    assert found.phase_number is None
    assert found.at_s == WINDOW["start_s"]


# ── a phase that ended before the machine logged a sample of it ──────────────

UNSAMPLED_FILL = {
    "phase_number": 0,
    "name": "Fill",
    "ended_by": 2,
    "at_s": 0.0,
    "pressure_end_bar": 4.8,
}


def test_a_phase_that_ended_before_its_first_sample_is_a_skipped_warning_at_its_time() -> None:
    [warning] = call(metrics={"phases_unsampled": [UNSAMPLED_FILL]})

    assert (warning.phase, warning.fault, warning.severity, warning.at_s) == (
        "Fill",
        "skipped",
        "amber",
        0.0,
    )
    assert warning.phase_number == 0 and warning.ended_at_start
    assert warning.badge == "Fill: skipped"
    assert warning.detail == (
        "The Fill ended on its pressure target before the first sample: pressure was already "
        "4.8 bar."
    )


def test_the_pressure_is_said_only_for_a_pressure_target() -> None:
    [duration] = call(metrics={"phases_unsampled": [{**UNSAMPLED_FILL, "ended_by": 5}]})
    [unknown] = call(metrics={"phases_unsampled": [{**UNSAMPLED_FILL, "ended_by": 0}]})
    [no_sensor] = call(
        metrics={
            "phases_unsampled": [
                {k: v for k, v in UNSAMPLED_FILL.items() if k != "pressure_end_bar"}
            ]
        }
    )

    assert duration.detail == "The Fill ended on its duration before the first sample."
    assert unknown.detail == (
        "The Fill ended before the first sample; the machine did not log why."
    )
    assert no_sensor.detail == "The Fill ended on its pressure target before the first sample."


def test_it_is_independent_of_how_the_shot_ended_and_of_the_stop_early_warning() -> None:
    # The same shot stopped on its weight before a later phase began: both are told, apart.
    both = call(
        final_exit_reason=1,
        metrics={
            "phases_unsampled": [UNSAMPLED_FILL],
            "phases_not_reached": [{"phase_number": 3, "name": "decline"}],
        },
    )

    assert [(w.phase, w.ended_at_start) for w in both] == [("Fill", True), ("decline", False)]
    # And without the stop, only the first is there.
    assert [w.phase for w in call(metrics={"phases_unsampled": [UNSAMPLED_FILL]})] == ["Fill"]
