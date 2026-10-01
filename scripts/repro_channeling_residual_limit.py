#!/usr/bin/env python
"""Reproduce: the channeling check measures flow against a pressure phase's flow limit.

    uv run python scripts/repro_channeling_residual_limit.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The channeling block's flow-versus-target residual pairs the puck flow with the
logged flow target `tf` over the steady-state window. In a phase that steers by
pressure `tf` is the phase's flow *limit* (a soft one: the pump follows the lower
of the flow ask and the pressure ask) or 0, never a target, so the residual measures the puck
against a number the machine was not steering by, and the channeling score it
feeds moves on it.

Real shot 129 logs `tf = 3` in every phase. With the profile constructed for it
(`tests/fixtures/constructed_profiles`: every brew phase steers by pressure) the
steady state lies wholly in pressure-steered phases, so the residual must be
absent (not applicable), neither a number nor 0, in the full block, in the
brew phases' own channeling annotation, and the summary's risk must be the
one its other indicators give (LOW).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gaggiclanker  # noqa: E402
from gaggiclanker.domain.diagnostics import (  # noqa: E402
    compute_shot_diagnostics,
    compute_summary_diagnostics,
    transform_shot,
)
from gaggiclanker.domain.phase_control import phase_controls  # noqa: E402
from tests.domain.helpers import constructed_profile, slog_from_export  # noqa: E402


def main() -> int:
    print("gaggiclanker imported from", gaggiclanker.__file__)
    failures = 0
    slog = slog_from_export("shot-129.json")
    controls = phase_controls(constructed_profile("shot_129", "pressure-first"))
    assert controls is not None
    print("phase controls:", controls)

    diagnostics = compute_shot_diagnostics(slog, phase_controls=controls)
    assert diagnostics is not None and diagnostics["channeling"] is not None
    channeling = diagnostics["channeling"]
    residual = channeling["flow_vs_target_residual_ml_s"]
    annotation = channeling["annotations"]["flow_vs_target"]
    print(f"full block: residual {residual}, annotation {annotation}")
    failures += residual is not None or annotation != "N/A"

    transformed = transform_shot(slog, "per_phase", phase_controls=controls)
    for phase in transformed["phases"]:
        found = phase.get("diagnostics", {}).get("annotations", {}).get("channeling_flow_vs_target")
        if found is not None:
            print(f"phase {phase['name']!r}: channeling_flow_vs_target {found}")
            failures += found != "N/A"
    summary = compute_summary_diagnostics(slog, phase_controls=controls)
    assert summary is not None
    risk = summary["annotations"]["channeling_risk"]
    print(f"summary: channeling risk {risk}")
    failures += risk != "LOW"
    print("FAIL" if failures else "ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
