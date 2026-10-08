#!/usr/bin/env python
"""Reproduce: a phase that ends before the machine logs its first sample vanishes.

    uv run python scripts/repro_phase_ended_before_first_sample.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A profile's first phase (a fill that exits on a pressure target) can end before the machine
has logged a single sample: the group was still pressurised from a flush or the previous
shot, so the exit was met the moment the pump would have started. The log then opens
with a transition table row ``(sample 0, phase 1, reason: pressure target)``; a row's reason
is why the *previous* phase ended, so it says the fill ended on its pressure target. The
app lost all of it: no phase row, no reason, no warning, a ``reached`` expectation on the
fill was held (phase 0 is below the last phase reached), a measure on the fill read "did
not reach" and sat at the end of the shot, and the review blamed the next phase.

The check derives the real shot (fixture 225) with its profile and requires: the shot's facts
name the unsampled fill with its reason and the pressure at the first sample; a ``skipped``
warning names the fill at 0 s and says it ended on its pressure target before the first sample;
a ``reached`` expectation on the fill fails; a measure windowed on the fill is not measured
for that reason and sits at 0 s, not at the end of the shot.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SLOG = ROOT / "tests" / "fixtures" / "slog_edge" / "shot_225_fill_ended_at_start.slog"
PROFILE = ROOT / "tests" / "fixtures" / "slog_edge" / "alma_lever_16_32.json"


@dataclass
class Expectation:
    """The parts of a stored expectation the evaluation reads."""

    id: int
    position: int
    tier: str
    phase: str | None
    kind: str
    expression: Any
    sentence: str
    warning_fault: str | None = None
    text: str = ""
    fault: str | None = "skipped"


def main() -> int:
    import gaggiclanker.db.repos  # noqa: F401  (the repositories import in one order)
    from gaggiclanker.domain.diagnostics import as_sample_dicts
    from gaggiclanker.domain.metric_language import Expression
    from gaggiclanker.domain.signature import build_checks
    from gaggiclanker.domain.slog import parse_slog
    from gaggiclanker.domain.warnings import shot_warnings
    from gaggiclanker.signatures.checks import CheckSubject, stored_shot_data
    from gaggiclanker.sync.derive import derive_shot

    raw = SLOG.read_bytes()
    profile = json.loads(PROFILE.read_text())
    slog = parse_slog(raw, "000225")
    derived = derive_shot(slog, raw, device_id="000225", source="import", profile=profile)
    shot = derived.shot
    phases = json.loads(shot.phases_json or "[]")
    diagnostics = json.loads(shot.diagnostics_json or "{}")
    metrics = diagnostics.get("metrics", {})
    failures: list[str] = []

    unsampled = metrics.get("phases_unsampled") or []
    if len(unsampled) != 1:
        failures.append(
            f"the shot's facts hold {len(unsampled)} unsampled phases, expected the Fill"
        )
    else:
        fill = unsampled[0]
        want = {"phase_number": 0, "name": "Fill", "ended_by": 2, "at_s": 0.0}
        for key, value in want.items():
            if fill.get(key) != value:
                failures.append(f"unsampled Fill {key} = {fill.get(key)!r}, expected {value!r}")
        if fill.get("pressure_end_bar") != 4.8:
            failures.append(f"unsampled Fill pressure_end_bar = {fill.get('pressure_end_bar')!r}")

    duration_s = shot.duration_ms / 1000.0
    warnings = shot_warnings(
        final_weight_g=shot.final_weight_g,
        scale_connected=True,
        final_exit_reason=shot.final_exit_reason,
        duration_s=duration_s,
        target_yield_g=None,
        phases=phases,
        metrics=metrics,
    )
    fill_warnings = [w for w in warnings if w.fault == "skipped" and w.phase == "Fill"]
    if len(fill_warnings) != 1:
        failures.append(f"{len(fill_warnings)} skipped warnings name the Fill, expected 1")
    else:
        warning = fill_warnings[0]
        if warning.at_s != 0.0:
            failures.append(f"the Fill warning sits at {warning.at_s} s, expected 0 s")
        for words in ("pressure target", "before the first sample", "4.8 bar"):
            if words not in warning.detail:
                failures.append(f"the Fill warning does not say {words!r}: {warning.detail!r}")

    subject = CheckSubject(
        shot_id=1,
        profile_version_id=None,
        set_version_id=None,
        warnings=warnings,
        phases=phases,
        duration_s=duration_s,
        scale_connected=True,
        final_weight_g=shot.final_weight_g,
        target_yield_g=None,
        dose_g=None,
        metrics=metrics,
    )
    samples = as_sample_dicts(slog)
    data = stored_shot_data(samples, phases, subject, metrics.get("profile_phases"))
    expectations = [
        Expectation(1, 0, "critical", "Fill", "reached", None, "the Fill begins"),
        Expectation(
            2,
            1,
            "important",
            "Fill",
            "measure",
            Expression.model_validate(
                {"channel": "pressure", "op": "max", "window": {"phase": "Fill"}}
            ),
            "the Fill's peak pressure",
        ),
    ]
    checks = build_checks(
        warnings=warnings,
        expectations=expectations,  # type: ignore[arg-type]
        override=None,
        data=data,
        phases=phases,
        duration_s=duration_s,
    ).checks
    reached = next((c for c in checks if c.kind == "reached"), None)
    if reached is None or reached.status != "failed":
        failures.append(f"a reached expectation on the Fill is {reached and reached.status!r}")
    elif "before the first sample" not in reached.detail:
        failures.append(f"the failed reached check does not say why: {reached.detail!r}")
    measure = next((c for c in checks if c.kind == "measure"), None)
    if measure is None or measure.status != "unmeasured":
        failures.append(f"a measure on the Fill is {measure and measure.status!r}")
    else:
        if "did not reach" in (measure.absent or ""):
            failures.append(f"the Fill measure says the shot did not reach it: {measure.absent!r}")
        if "before the first sample" not in (measure.absent or ""):
            failures.append(f"the Fill measure does not say why: {measure.absent!r}")
        if measure.at_s != 0.0:
            failures.append(f"the Fill measure sits at {measure.at_s} s, expected 0 s")

    for problem in failures:
        print("BUG:", problem)
    print(f"{len(failures)} problems with a fill that ended before the first sample")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
