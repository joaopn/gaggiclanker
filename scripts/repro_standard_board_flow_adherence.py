#!/usr/bin/env python
"""Reproduce: a shot from a machine without a pressure sensor is graded on a flow it never had.

    uv run python scripts/repro_standard_board_flow_adherence.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A Standard board has no pressure sensor and no dimmed pump. In the GaggiMate
firmware (v1.9.0) the controller sends zero for the pressure, the pump flow and
the puck flow unless the board has the pressure capability
(`GaggiMateController.cpp`, `sendSensorData`), the display sets the target flow
only inside `if (active && systemInfo.capabilities.pressure)`
(`Controller.cpp`), and every Standard revision in `ControllerConfig.h` is
`dimming = false, pressure = false`. The log records every field, so a real
Standard shot has `cp`, `tp`, `pf`, `tf` and `fl` at zero on every sample.

The summary diagnostics compared that zero flow with that zero target, found
no deviation, and reported a flow adherence of 0.0 (and a flow overshoot): a
confident reading of a measurement that does not exist.

The check builds a realistic Standard shot out of each real fixture (the
pressure, flow and resistance columns zeroed, the temperature and the scale
kept; the pressure gate inferred from the trace, never forced) and requires
that no diagnostics shape carries any flow adherence, overshoot or undershoot.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import gaggiclanker
from gaggiclanker.domain.diagnostics import transform_shot
from gaggiclanker.domain.slog import Slog, parse_slog

ROOT = Path(__file__).resolve().parents[1]
ZEROED = ("cp", "tp", "pf", "tf", "fl", "pr", "wp")
FLOW_KEYS = ("flow_rmse_ml_s", "max_flow_overshoot_ml_s", "max_flow_undershoot_ml_s")


def standard_board(slog: Slog) -> Slog:
    samples = [
        s.model_copy(update={f: 0.0 for f in ZEROED if getattr(s, f) is not None})
        for s in slog.samples
    ]
    return dataclasses.replace(slog, samples=samples)


def main() -> int:
    failures = 0
    print("gaggiclanker imported from", gaggiclanker.__file__)
    for path in sorted((ROOT / "tests" / "fixtures" / "slog").glob("*.slog")):
        slog = standard_board(parse_slog(path.read_bytes()))
        assert not slog.has_pressure, "the shot must read as a Standard board unforced"
        for detail in ("summary", "per_phase"):
            diagnostics = transform_shot(slog, detail)["diagnostics"]
            assert diagnostics is not None
            if detail == "summary":
                found = {k: v for k, v in diagnostics.items() if k in FLOW_KEYS and v is not None}
            else:
                compliance = diagnostics["profile_compliance"] or {}
                found = {k: v for k, v in compliance.items() if k in FLOW_KEYS and v is not None}
            print(f"{path.stem} {detail}: {found or 'no flow adherence'}")
            failures += bool(found)
    print("FAIL" if failures else "ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
