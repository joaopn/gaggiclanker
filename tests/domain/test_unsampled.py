"""A phase that ended before the machine logged a sample of it.

The definition: a phase whose number is below the last phase any sample carries and that no
sample carries. Its reason is the transition reason of the first row whose phase is its
successor; its time is the start of the next phase the log holds; its pressure is the first
sample after it. The real case is a spring-lever shot whose Fill (exit: 2.8 bar) met a group
still pressurised at 4.8 bar.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from gaggiclanker.domain.diagnostics import as_sample_dicts
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.metric_language import Expression, ShotData, evaluate
from gaggiclanker.domain.phase_metrics import last_phase_reached, phase_began, phases_not_reached
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.domain.unsampled import (
    ended_before_sampled,
    phases_unsampled,
    reason_words,
    stored_unsampled,
)
from gaggiclanker.sync.derive import derive_shot
from tests.domain.helpers import (
    SLOG_FIXTURES,
    constructed_profile_for,
    fill_ended_shot,
    slog_from_export,
    with_phase_table,
)
from tests.lever_shot import LEVER_PROFILE, lever_shot

NAMES = ["Fill", "Ramp", "Decline"]


def _metrics(profile: dict[str, Any] | None, slog: Any = None) -> dict[str, Any]:
    slog = slog or fill_ended_shot()[0]
    derived = derive_shot(slog, slog_to_raw(slog), device_id="000225", profile=profile)
    blob: dict[str, Any] = json.loads(derived.shot.diagnostics_json or "{}")
    metrics: dict[str, Any] = blob["metrics"]
    return metrics


def test_the_real_shot_has_exactly_one_phase_that_ended_before_it_was_sampled() -> None:
    _, _, profile = fill_ended_shot()

    assert _metrics(profile)["phases_unsampled"] == [
        {
            "phase_number": 0,
            "name": "Fill",
            "ended_by": 2,  # pressure target: the reason on the Ramp's row at sample 0
            "at_s": 0.0,
            "pressure_end_bar": 4.8,
        }
    ]
    # Nothing was never reached: the Fill is before the last phase, not after it.
    assert _metrics(profile)["phases_not_reached"] == []


def test_without_a_profile_the_phase_is_still_known_by_its_number() -> None:
    [only] = _metrics(None)["phases_unsampled"]

    assert (only["phase_number"], only["name"], only["ended_by"]) == (0, "phase 0", 2)
    assert (
        ended_before_sampled(only) == "phase 0 ended on its pressure target before the first sample"
    )


def test_no_pressure_sensor_means_no_pressure_at_the_first_sample() -> None:
    slog, _, profile = fill_ended_shot()
    derived = derive_shot(
        slog, slog_to_raw(slog), device_id="000225", has_pressure=False, profile=profile
    )
    [only] = json.loads(derived.shot.diagnostics_json or "{}")["metrics"]["phases_unsampled"]

    assert "pressure_end_bar" not in only


def test_a_log_from_before_exit_reasons_says_unknown() -> None:
    slog, _, _ = fill_ended_shot()
    samples = as_sample_dicts(slog)

    [only] = phases_unsampled(NAMES, samples, slog.transitions, version=5, has_pressure=True)

    assert only["ended_by"] == 0
    assert ended_before_sampled(only) == (
        "the Fill ended before the first sample; the machine did not log why"
    )


def test_two_unsampled_phases_in_a_row_only_the_last_has_the_row_s_reason() -> None:
    # The real shot, its table edited so the log opens in phase 2: the row says why phase 1
    # ended, and phase 0 has no row of its own.
    slog, _, _ = fill_ended_shot()
    edited = with_phase_table(slog, [(0, 2, 3, "Hold"), (17, 3, 5, "Decline")])

    found = phases_unsampled(
        ["Fill", "Soak", "Hold", "Decline"],
        as_sample_dicts(edited),
        edited.transitions,
        version=7,
        has_pressure=True,
    )

    assert [(p["phase_number"], p["name"], p["ended_by"], p["at_s"]) for p in found] == [
        (0, "Fill", 0, 0.0),
        (1, "Soak", 3, 0.0),
    ]


def test_a_phase_in_the_middle_with_no_sample_ends_where_the_next_one_starts() -> None:
    slog, _, _ = fill_ended_shot()
    edited = with_phase_table(slog, [(0, 0, 0, "Fill"), (4, 1, 2, "Ramp"), (17, 3, 5, "Decline")])

    [only] = phases_unsampled(
        ["Fill", "Ramp", "Hold", "Decline"],
        as_sample_dicts(edited),
        edited.transitions,
        version=7,
        has_pressure=True,
    )

    assert only["phase_number"] == 2 and only["name"] == "Hold"
    assert only["ended_by"] == 5  # the row for phase 3 says why phase 2 ended
    assert only["at_s"] == pytest.approx((edited.samples[17].t or 0) / 1000.0, abs=0.05)
    assert only["pressure_end_bar"] == pytest.approx(edited.samples[17].cp or 0.0, abs=0.05)


def test_a_logged_phase_reads_only_the_row_of_its_successor_and_else_unknown() -> None:
    """The firmware keeps one exit reason per row. When the machine jumps over the Hold, the
    Decline's row holds why the Hold ended, and the Ramp, logged before it, ended for a reason
    the log does not hold: reading the Decline's row for both gave two phases one reason."""
    slog, _, _ = fill_ended_shot()
    edited = with_phase_table(slog, [(0, 0, 0, "Fill"), (4, 1, 2, "Ramp"), (17, 3, 5, "Decline")])
    profile = {
        "label": "p",
        "type": "pro",
        "phases": [{"name": n} for n in ("Fill", "Ramp", "Hold", "Decline")],
    }

    derived = derive_shot(edited, slog_to_raw(edited), device_id="000225", profile=profile)
    reasons = {
        p["name"]: p["metrics"]["ended_by"] for p in json.loads(derived.shot.phases_json or "[]")
    }
    [hold] = json.loads(derived.shot.diagnostics_json or "{}")["metrics"]["phases_unsampled"]

    # Fill: the Ramp's row (reason 2). Ramp: no row for phase 2, Unknown. Decline: the header's.
    assert reasons == {"Fill": 2, "Ramp": 0, "Decline": edited.header.final_exit_reason}
    assert hold["name"] == "Hold" and hold["ended_by"] == 5


def test_a_window_by_phase_number_on_such_a_phase_is_absent_for_that_reason() -> None:
    slog, _, profile = fill_ended_shot()
    metrics = _metrics(profile)
    data = ShotData.build(
        as_sample_dicts(slog),
        slog.transitions,
        profile_phases=NAMES,
        unsampled=metrics["phases_unsampled"],
    )

    def by_number(number: int) -> Any:
        expression = Expression.model_validate(
            {"channel": "pressure", "op": "max", "window": {"phase_number": number}}
        )
        return evaluate(expression, data)

    fill = by_number(0)
    assert fill.value is None and fill.absent == "ended_before_sampled"
    assert fill.why == "the Fill ended on its pressure target before the first sample"
    assert by_number(1).value is not None
    # A profile phase after the last one logged is still "did not reach".
    assert by_number(3).absent == "no_such_phase"
    longer = dataclasses.replace(data, profile_phases=[*NAMES, "Tail"])
    tail = Expression.model_validate(
        {"channel": "pressure", "op": "max", "window": {"phase_number": 3}}
    )
    assert evaluate(tail, longer).absent == "phase_not_reached"


def test_no_other_real_shot_has_one() -> None:
    for path in sorted(SLOG_FIXTURES.glob("*.slog")):
        slog = parse_slog(path.read_bytes())
        for profile in (None, constructed_profile_for(path)):
            assert _metrics(profile, slog)["phases_unsampled"] == [], path.name
    for name in ("shot-129.json", "shot-v7-synthetic.json"):
        assert _metrics(None, slog_from_export(name))["phases_unsampled"] == [], name
    assert _metrics(LEVER_PROFILE, lever_shot())["phases_unsampled"] == []


def test_phase_began_means_a_sample_carries_the_number() -> None:
    samples = as_sample_dicts(fill_ended_shot()[0])

    assert last_phase_reached(samples) == 2
    assert [phase_began(n, samples) for n in (0, 1, 2, 3)] == [False, True, True, False]
    # The phase after the last is the other kind of absence.
    assert phases_not_reached([*NAMES, "Tail"], samples) == [{"phase_number": 3, "name": "Tail"}]


@pytest.mark.parametrize(
    ("code", "words"), [(0, None), (2, "pressure target"), (5, "duration"), (99, None)]
)
def test_reasons_in_words(code: int, words: str | None) -> None:
    assert reason_words(code) == words


def test_stored_facts_are_read_leniently() -> None:
    assert stored_unsampled(None) == []
    assert stored_unsampled({"phases_unsampled": "x"}) == []
    assert stored_unsampled({"phases_unsampled": [1, {"phase_number": 0}]}) == [{"phase_number": 0}]
