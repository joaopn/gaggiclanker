#!/usr/bin/env python
"""Reproduce: a pressure profile's shot is graded on a flow the machine was not steering by.

    uv run python scripts/repro_flow_adherence_uncommanded.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The GaggiMate firmware (v1.9.0) logs both a pressure target (`tp`) and a flow
target (`tf`) on every sample of an advanced pump phase, but only one of them is
the target: the other is a soft limit (`Controller.cpp`, `BrewProcess.h`,
`profile.h`). A simple power phase, an inactive machine and the recording tail
after the brew log 0/0. Only the profile says which is which: `phase.pump` is
an integer, or `{target: "pressure" | "flow", ...}`.

The diagnostics paired every sample's flow with its logged `tf` in every phase,
so a shot brewed with a profile that steers by pressure everywhere (every brew
phase of the real shots) was graded on the puck flow against a flow limit and
against zeros, and came out `flow_adherence: POOR` with a flow penalty in its
score. That verdict is an artefact: the machine never tried to hold a flow.

The check derives each of the three real fixture shots the way the archive does
(`derive_shot`), with a profile constructed to match the shot's phases whose
first phase steers by pressure (`tests/fixtures/constructed_profiles`; the real
profiles no longer exist), and requires that the stored diagnostics carry no
flow adherence, overshoot or undershoot, and that no phase carries a flow error.
On a checkout where `derive_shot` cannot take a profile the shot is derived
without one, which is what that code did.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import gaggiclanker
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.sync.derive import derive_shot

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
FLOW_KEYS = ("flow_adherence", "flow_overshoot", "flow_undershoot")


def derive(raw: bytes, profile: dict[str, Any], device_id: str) -> Any:
    slog = parse_slog(raw, device_id)
    try:
        return derive_shot(slog, raw, device_id=device_id, profile=profile)
    except TypeError:
        return derive_shot(slog, raw, device_id=device_id)


def main() -> int:
    failures = 0
    print("gaggiclanker imported from", gaggiclanker.__file__)
    for path in sorted((FIXTURES / "slog").glob("*.slog")):
        prefix = "_".join(path.stem.split("_")[:2])
        profile = json.loads(
            (FIXTURES / "constructed_profiles" / f"{prefix}_pressure-first.json").read_text()
        )
        shot = derive(path.read_bytes(), profile, device_id=path.stem).shot
        assert shot.diagnostics_json is not None and shot.phases_json is not None
        stored = json.loads(shot.diagnostics_json)
        compliance = stored["diagnostics"]["profile_compliance"] or {}
        found = {k: v for k, v in compliance.get("annotations", {}).items() if k in FLOW_KEYS}
        phases = [
            (p["name"], p["diagnostics"]["flow_rmse_ml_s"])
            for p in json.loads(shot.phases_json)
            if "flow_rmse_ml_s" in p.get("diagnostics", {})
        ]
        # Graded, not merely absent: a profile ignored altogether would pass the rest.
        ungraded = compliance.get("pressure_grading") != "graded"
        print(
            f"{path.stem}: pressure graded {not ungraded}; flow verdicts {found or 'none'}; "
            f"phases with a flow error {phases}"
        )
        failures += bool(found) + bool(phases) + ungraded
    print("FAIL" if failures else "ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
