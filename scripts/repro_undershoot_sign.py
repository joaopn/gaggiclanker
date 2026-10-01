#!/usr/bin/env python
"""Reproduce: the "largest undershoot" of a shot that never fell below target is not 0.

    uv run python scripts/repro_undershoot_sign.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The diagnostics report the largest undershoot (pressure, flow and temperature)
as `abs(min(deviation))`. When every graded sample sits above its target,
`min(deviation)` is the *smallest overshoot*, a positive number, so the shot
shows an undershoot that never happened (an overshoot of 0.8 with an
"undershoot" of 0.1). The overshoot side already clamps with `max(0, max(d))`;
the undershoot must be `max(0, -min(d))`.

Three constructed shots, each with every sample above its target by 0.1 to 0.8,
one steered by pressure, one by flow, and a temperature that stays above its
own target, must all report an undershoot of exactly 0.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gaggiclanker  # noqa: E402
from gaggiclanker.domain.diagnostics import compute_shot_diagnostics  # noqa: E402
from tests.domain.helpers import make_slog  # noqa: E402

OVER = [0.1, 0.3, 0.5, 0.8, 0.6, 0.4, 0.2, 0.7]


def shot(control: str) -> dict[str, Any]:
    samples = []
    for i, over in enumerate(OVER):
        sample: dict[str, Any] = {
            "t": i * 250,
            "ct": 93.0 + over,
            "tt": 93.0,
            "cp": 6.0,
            "pf": 1.5,
            "fl": 1.5,
            "tp": 0.0,
            "tf": 0.0,
        }
        if control == "pressure":
            sample["tp"], sample["cp"] = 6.0, 6.0 + over
        else:
            sample["tf"], sample["fl"] = 1.5, 1.5 + over
            sample["tp"] = 0.0
        samples.append(sample)
    slog = make_slog(samples, [(0, 0, "Brew")])
    diagnostics = compute_shot_diagnostics(slog, phase_controls=(control,))  # type: ignore[arg-type]
    assert diagnostics is not None
    return {
        "temperature": diagnostics["temperature"]["undershoot_c"],
        "compliance": diagnostics["profile_compliance"],
    }


def main() -> int:
    print("gaggiclanker imported from", gaggiclanker.__file__)
    failures = 0
    pressure = shot("pressure")
    flow = shot("flow")
    found = {
        "pressure undershoot (bar)": pressure["compliance"]["max_pressure_undershoot_bar"],
        "flow undershoot (ml/s)": flow["compliance"]["max_flow_undershoot_ml_s"],
        "temperature undershoot (C)": pressure["temperature"],
    }
    for name, value in found.items():
        bad = value != 0.0
        failures += bad
        print(f"{name}: {value} {'(should be 0.0)' if bad else 'ok'}")
    print("FAIL" if failures else "ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
